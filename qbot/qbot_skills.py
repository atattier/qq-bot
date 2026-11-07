# -*- coding: utf-8 -*-
"""qbot_skills —— 技能包加载器（兼容 QQ Agent 的 skill.json 清单格式）

从 `skills/<id>/skill.json` 读清单，把「提示词片段」拼进系统提示词，把开关和参数
存进 config，供 `/admin`、`/skill` 命令和两个大脑（聊天 / agent）共用。

为什么要这层东西：技能包的价值全在「什么时候该怎么做」这类**写不死的判断**上
（这梗能不能接、现在该不该配图），只能交给模型读；但清单、权限、参数、启停是
可以写死的。所以这里只做「可写死的那半」：**加载、校验、注入、记账**。

⚠️ 和 `qbot_slang` 一样：**本模块不 import bot**，宿主能力靠 bind() 注入。

技能包目录结构（照抄 QQ Agent 的约定）：
    skills/<id>/
      ├── skill.json   清单：id / name / version / category / description /
      │                enabledByDefault / permissions / settings / configSchema /
      │                prompt.sections[{id,title,priority,content}]
      ├── index.js     工具入口（**本机不执行**，只嗅一下注册了哪几个工具名用于展示）
      └── SKILL.md     人读的说明（不解析）

🔴 关键取舍：`index.js` 是 Node 模块，本机 bot 是纯 Python，**不跑它**。
   所以技能目前只贡献「提示词 + 参数」，工具要另外用 Python 原生实现（见 tools_note()）。
   清单里 prompt 说「用 xx 工具」而工具不存在时，模型会空着手答应 —— `tools_note()`
   会把「本技能的工具还没接入」写进提示词，避免它吹牛。

✏️ 2026-10-26 起支持网页端编辑（主人：「那些插件有点太僵硬了」）：
   参数 → `set_setting()` 存进 config.json（不动技能包本体）
   提示词 → `save_section()` 写回 skills/<id>/skill.json（备份 + 原子写 + 立即重扫）
   网页侧把两者都做成普通旋钮，见 bot.py 的 `_skill_knobs()`。
"""
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path

log = logging.getLogger("qqbot.skills")

CFG = {}
BASE = Path(__file__).resolve().parent.parent

_DEFAULTS = {
    "enabled": True,          # 总开关
    "dir": "skills",          # 技能根目录（相对项目根，也可写绝对路径）
    "en": {},                 # {skill_id: bool} 单项开关
    "settings": {},           # {skill_id: {key: value}} 覆盖清单里的 settings
    "prompt_max_chars": 4200,  # 拼进提示词的总预算（4 个技能约 3000 字，留点余量）
    "section_max_chars": 1200,  # 单个技能片段的预算
}

_CACHE = {"sig": None, "skills": [], "ts": 0.0}


def bind(cfg, base=None, logger=None):
    global CFG, BASE
    CFG = cfg
    if base:
        BASE = Path(base)
    if logger is not None:
        globals()["log"] = logger


def cfg():
    d = CFG.setdefault("skills", {})
    for k, v in _DEFAULTS.items():
        if isinstance(v, dict):
            d.setdefault(k, dict(v))
        else:
            d.setdefault(k, v)
    return d


def skills_dir():
    p = (cfg().get("dir") or "skills").strip()
    pp = Path(p)
    if not pp.is_absolute():
        pp = BASE / pp
    return pp


# ── 扫描 ────────────────────────────────────────────────────────────────

def _sig():
    """目录签名：skill.json 的 mtime + 大小。变了才重扫（存盘即生效）。"""
    root = skills_dir()
    if not root.is_dir():
        return ("missing", str(root))
    parts = []
    for f in sorted(root.glob("*/skill.json")):
        try:
            st = f.stat()
            parts.append((f.parent.name, int(st.st_mtime), st.st_size))
        except OSError:
            continue
    return (str(root), tuple(parts))


def _tool_names(entry_file):
    """从 index.js 里嗅 registerTool 的工具名（只用于展示，不执行）。"""
    try:
        src = Path(entry_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    names = []
    for m in re.finditer(r"registerTool\(\s*\{", src):
        chunk = src[m.end():m.end() + 400]
        nm = re.search(r"""name\s*:\s*['"]([\w.-]+)['"]""", chunk)
        if nm:
            names.append(nm.group(1))
    return names


def _norm_sections(data):
    out = []
    for i, s in enumerate(((data.get("prompt") or {}).get("sections") or [])):
        if not isinstance(s, dict):
            continue
        content = str(s.get("content") or "").strip()
        if not content:
            continue
        try:
            pr = int(s.get("priority") or 100)
        except (TypeError, ValueError):
            pr = 100
        # idx = 在**原始清单**里的下标。排序（按 priority）之后下标就变了，
        # 而回写 skill.json 必须落到原位置，所以这里留一份原始位置。
        out.append({"id": s.get("id") or "", "title": s.get("title") or "",
                    "priority": pr, "content": content, "idx": i})
    out.sort(key=lambda x: x["priority"])
    return out


def _norm_links(data):
    """清单里的「来源网址」→ [{label,url}]。

    2026-10-26 主人问「这些从网站选图的插件都不贴网址出来吗」—— 图库在哪个站、
    索引 JSON 在哪，写在清单里（`links` 数组，或 `homepage`/`repo`/`source`/`site`），
    网页抽屉直接把链接摆出来，省得翻代码找。
    """
    out = []

    def _put(label, url):
        url = str(url or "").strip()
        if not url.startswith("http") or len(url) > 300:
            return
        if any(x["url"] == url for x in out):
            return
        # 24 个字：够放「cnmeme.wiki（词条来源）」这种带括号说明的名字
        # （原来 16 会把尾巴的「）」切掉，看着像没写完）
        out.append({"label": str(label or "链接")[:24], "url": url})

    raw = data.get("links")
    if isinstance(raw, list):
        for x in raw:
            if isinstance(x, dict):
                _put(x.get("label") or x.get("name"), x.get("url"))
            elif isinstance(x, str):
                _put(x.split("//")[-1].split("/")[0], x)
    for key, label in (("homepage", "主页"), ("repo", "仓库"),
                       ("source", "来源"), ("site", "站点")):
        _put(label, data.get(key))
    return out[:5]


def _load_one(folder):
    mf = folder / "skill.json"
    sk = {"id": folder.name, "name": folder.name, "version": "", "category": "",
          "desc": "", "path": str(folder), "sections": [], "schema": {},
          "settings_raw": {}, "tools": [], "perms": [], "default_on": True,
          "err": None}
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        sk["err"] = "skill.json 读不了：%s" % e
        return sk
    if not isinstance(data, dict):
        sk["err"] = "skill.json 不是对象"
        return sk
    sid = str(data.get("id") or folder.name).strip()
    if not sid:
        sk["err"] = "清单里没有 id"
        return sk
    sk["id"] = sid
    sk["name"] = str(data.get("name") or sid)
    sk["version"] = str(data.get("version") or "")
    sk["category"] = str(data.get("category") or "")
    sk["desc"] = str(data.get("description") or "")
    sk["perms"] = list(data.get("permissions") or [])
    sk["settings_raw"] = dict(data.get("settings") or {})
    sk["schema"] = dict(data.get("configSchema") or {})
    sk["sections"] = _norm_sections(data)
    sk["links"] = _norm_links(data)
    sk["default_on"] = bool(data.get("enabledByDefault", True))
    if "enabled" in sk["settings_raw"]:
        sk["default_on"] = bool(sk["settings_raw"]["enabled"]) and sk["default_on"]
    entry = folder / str(data.get("entry") or "index.js")
    sk["tools"] = _tool_names(entry) if entry.exists() else []
    if data.get("apiVersion") and int(data.get("apiVersion") or 1) > 1:
        sk["err"] = "apiVersion %s 太新，本机只认 1" % data.get("apiVersion")
    return sk


def scan(force=False):
    """返回技能列表。2s 内复用缓存，目录没变不重扫。"""
    sig = _sig()
    if not force and sig == _CACHE["sig"] and time.time() - _CACHE["ts"] < 2:
        return _CACHE["skills"]
    skills = []
    root = skills_dir()
    if root.is_dir():
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            if folder.name.startswith("."):
                continue
            if not (folder / "skill.json").exists():
                continue
            skills.append(_load_one(folder))
    _CACHE.update(sig=sig, skills=skills, ts=time.time())
    return skills


def reload():
    return scan(force=True)


def get(sid):
    for sk in scan():
        if sk["id"] == sid or sk["name"] == sid:
            return sk
    return None


def ids():
    return [sk["id"] for sk in scan()]


# ── 开关 / 参数 ──────────────────────────────────────────────────────────

def total_enabled():
    return bool(cfg().get("enabled", True))


def is_on(sid):
    sk = get(sid)
    if not sk:
        return False
    if not total_enabled():
        return False
    d = cfg()["en"]
    if sid in d:
        return bool(d[sid])
    return bool(sk["default_on"])


def set_on(sid, on):
    sk = get(sid)
    if not sk:
        return False, "没有这个技能：" + str(sid)
    cfg()["en"][sk["id"]] = bool(on)
    return True, "「%s」已%s" % (sk["name"], "开启" if on else "关闭")


def settings_of(sid):
    """清单默认值 ← config 覆盖。"""
    sk = get(sid)
    if not sk:
        return {}
    eff = dict(sk["settings_raw"])
    eff.update((cfg()["settings"].get(sk["id"]) or {}))
    return eff


def set_setting(sid, key, value):
    sk = get(sid)
    if not sk:
        return False, "没有这个技能：" + str(sid)
    schema = sk["schema"].get(key)
    if not schema:
        own = ", ".join(sorted(sk["schema"])) or "（没有可调项）"
        return False, "「%s」没有参数 %s。可调的有：%s" % (sk["name"], key, own)
    typ = schema.get("type")
    if typ == "boolean":
        v = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "on", "开", "是", "yes")
    elif typ == "number":
        try:
            v = float(value)
            if v == int(v):
                v = int(v)
        except (TypeError, ValueError):
            return False, "「%s」要一个数字" % schema.get("label", key)
    elif typ == "enum":
        vals = list(schema.get("values") or [])
        if value not in vals:
            return False, "「%s」只能是：%s" % (schema.get("label", key), " / ".join(vals))
        v = value
    else:
        v = str(value)
    cfg()["settings"].setdefault(sk["id"], {})[key] = v
    return True, "「%s」的 %s = %s" % (sk["name"], schema.get("label", key), v)


# ── 网页编辑：提示词正文回写（2026-10-26）────────────────────────────────
# 背景：主人说「那些插件有点太僵硬了，能不能在网页端编辑」。
# 原来网页上每个技能只有 on/off，参数得发群口令「技能 set」，提示词只能手改 skill.json。
# 现在参数走 config（set_setting，不动包本体），提示词**直接写回 skill.json**。
#
# 🔴 三条纪律（写文件的地方最容易出事）：
#   1. 先备份再写，备份名 skill.json.bak-YYYYmmdd-HHMMSS，只留最近 3 份
#   2. 原子替换（写 .tmp → os.replace），断电/被杀不会留下半个 JSON
#   3. 只改目标片段那一个字段，其余字段原样保留（清单里可能有人手写的注释字段）
_BACKUP_KEEP = 3


def _manifest_path(sid):
    sk = get(sid)
    if not sk:
        return None, None
    p = Path(sk["path"]) / "skill.json"
    if not p.exists():
        return None, sk
    return p, sk


def _backup(p):
    """留一份带时间戳的备份，顺手清掉太老的（只留最近 _BACKUP_KEEP 份）。"""
    try:
        bak = p.with_name("%s.bak-%s" % (p.name, time.strftime("%Y%m%d-%H%M%S")))
        shutil.copy2(str(p), str(bak))
        olds = sorted(p.parent.glob(p.name + ".bak-*"))
        for old in olds[:-_BACKUP_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
        return bak.name
    except OSError as e:  # noqa: BLE001
        log.warning("技能包备份失败（继续写，但没退路）: %s", e)
        return ""


def _atomic_write_json(p, data):
    txt = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp = p.with_name(p.name + ".tmp")
    with open(str(tmp), "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(p))


def section_key(sec, i=0):
    """片段的稳定键：有 id 用 id，没有 id 用原始下标 `#i`（网页/口令都用这个点）。"""
    return str(sec.get("id") or ("#%d" % sec.get("idx", i)))


def section_text(sid, sec_id):
    """读某个片段现在的正文（网页 textarea 的初值）。"""
    sk = get(sid)
    if not sk:
        return ""
    for i, s in enumerate(sk["sections"]):
        if section_key(s, i) == str(sec_id):
            return s["content"]
    return ""


def save_section(sid, sec_id, content):
    """把提示词片段写回 skills/<id>/skill.json。返回 (ok, msg)。"""
    p, sk = _manifest_path(sid)
    if not sk:
        return False, "没有这个技能：%s" % sid
    if not p:
        return False, "「%s」的 skill.json 不见了：%s" % (sk["name"], sk["path"])
    text = str(content or "").strip()
    if not text:
        return False, "提示词不能存空 —— 空片段等于把这个技能的做法删掉（要停用请关开关）。"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return False, "skill.json 读不出来，先修好再改：%s" % e
    if not isinstance(data, dict):
        return False, "skill.json 不是对象，不敢写"
    secs = ((data.get("prompt") or {}).get("sections")) or []
    hit = None
    for i, s in enumerate(secs):
        if isinstance(s, dict) and section_key(s, i) == str(sec_id):
            hit = s
            break
    if hit is None:
        return False, "「%s」里没有片段 %s（清单被改过？刷新页面再看）" % (sk["name"], sec_id)
    old = str(hit.get("content") or "")
    if old == text:
        return True, "「%s」的提示词没变，没动文件" % sk["name"]
    hit["content"] = text
    bak = _backup(p)
    try:
        _atomic_write_json(p, data)
    except Exception as e:  # noqa: BLE001
        return False, "写 skill.json 失败（旧文件还在）：%s" % e
    reload()   # 存盘即生效：下一句话拼提示词就用新的
    log.info("技能提示词已改：%s/%s %d→%d 字%s", sid, sec_id, len(old), len(text),
             "（备份 %s）" % bak if bak else "")
    return True, "「%s」的提示词已保存（%d 字），下一句话就按新的来%s" % (
        sk["name"], len(text), "，旧版备份在 %s" % bak if bak else "")


# ── 提示词 ──────────────────────────────────────────────────────────────
def _native_tools():
    """本机有没有这些技能工具的原生实现（qbot_skill_tools）。没装就当没有。"""
    try:
        from qbot import qbot_skill_tools as st
        return dict(getattr(st, "NATIVE", {}) or {}), dict(getattr(st, "USAGE", {}) or {})
    except Exception:  # noqa: BLE001
        return {}, {}


def prompt_block(max_chars=None):
    """把已启用技能的提示词片段拼成一段，给两个大脑共用。

    没接入工具的技能会显式标注「工具未接入」，免得模型答应帮人发图却发不出来。
    本机已用 Python 重写了工具的（见 qbot_skill_tools.NATIVE），改成告诉它**怎么用**。
    """
    if not total_enabled():
        return ""
    native, usage = _native_tools()
    c = cfg()
    total = 0
    budget = int(max_chars or c.get("prompt_max_chars") or 4200)
    per = int(c.get("section_max_chars") or 1200)
    lines = []
    for sk in scan():
        if sk.get("err") or not is_on(sk["id"]):
            continue
        if not sk["sections"]:
            continue
        head = "■ %s（%s）" % (sk["name"], sk["id"])
        if sk["id"] in native:
            # 本机重写过工具的直接报原生名 —— 别依赖 index.js 里那个（自建包根本没有
            # index.js，而模型看到的名字是本机这套，不是原作者的）
            head += "　工具：本机已接 %s" % "、".join(native[sk["id"]])
        elif sk["tools"]:
            head += "　工具：%s（**尚未接入本机，暂时不要承诺能用**）" % "、".join(sk["tools"])
        body = []
        if usage.get(sk["id"]):
            u = usage[sk["id"]]
            body.append(u if u.startswith("【") else "【本机用法】" + u)
        for s in sk["sections"]:
            t = s["content"]
            if len(t) > per:
                t = t[:per].rstrip() + "…"
            body.append(t)
        chunk = head + "\n" + "\n".join(body)
        if total + len(chunk) > budget:
            rest = budget - total
            if rest < 80:
                break
            chunk = chunk[:rest].rstrip() + "…"
        lines.append(chunk)
        total += len(chunk)
    if not lines:
        return ""
    return "【能用的小技能】下面这些是可选做法，用得上就照着做，用不上别硬套。\n" + "\n\n".join(lines)


def tools_note():
    """给维护者/主人看的一句话：哪些技能只有提示词、工具还没接。"""
    native, _ = _native_tools()
    pend = [sk["id"] for sk in scan()
            if is_on(sk["id"]) and sk["tools"] and sk["id"] not in native]
    if not pend:
        return ""
    return "工具未接入：" + "、".join(pend)


# ── 文本（命令 / 后台） ─────────────────────────────────────────────────

def list_text(verbose=False):
    skills = scan()
    if not skills_dir().is_dir():
        return "技能目录不存在：%s（建好再把技能包放进去）" % skills_dir()
    if not skills:
        return "技能目录里还没有技能包：%s" % skills_dir()
    on = [sk for sk in skills if is_on(sk["id"])]
    out = ["技能 %d 个，开 %d 个%s" % (len(skills), len(on),
                                   "" if total_enabled() else "（总开关关着，全部不生效）")]
    for sk in skills:
        flag = "✅" if is_on(sk["id"]) else "⛔"
        bad = " ⚠️" + sk["err"] if sk.get("err") else ""
        out.append("%s %s　%s　v%s　%d 段%s" % (flag, sk["name"], sk["id"],
                                             sk["version"] or "?", len(sk["sections"]), bad))
        if verbose and sk["desc"]:
            out.append("    " + sk["desc"].splitlines()[0][:120])
        if verbose and sk["tools"]:
            out.append("    工具（未接入）：" + "、".join(sk["tools"]))
    return "\n".join(out)


def detail_text(sid):
    sk = get(sid)
    if not sk:
        return "没有这个技能：%s（发「技能」看全部）" % sid
    out = ["%s（%s）v%s　%s" % (sk["name"], sk["id"], sk["version"] or "?", "开" if is_on(sk["id"]) else "关")]
    if sk["err"]:
        out.append("⚠️ " + sk["err"])
    if sk["desc"]:
        out.append(sk["desc"])
    if sk["sections"]:
        out.append("提示词片段：")
        for s in sk["sections"]:
            out.append("  · %s（priority %d，%d 字）" % (s["title"] or s["id"], s["priority"], len(s["content"])))
    if sk["tools"]:
        out.append("工具（index.js 里注册，本机未接入）：" + "、".join(sk["tools"]))
    eff = settings_of(sid)
    if sk["schema"]:
        out.append("可调参数：")
        for k, sc in sk["schema"].items():
            out.append("  · %s = %s　%s" % (k, eff.get(k), sc.get("label") or ""))
    return "\n".join(out)
