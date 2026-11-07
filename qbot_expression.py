# -*- coding: utf-8 -*-
"""表达学习闭环 v1（2026-11-03 人味阶段 A3）。

学什么：群友在**什么场面**（situation）习惯**怎么说话**（style），配上真实原话当例子。
目的：让 bot 接话时带点这个群自己的表达习惯 —— 参考，不是模板。

任务书四条硬边界（实现都钉死了，别松）：
1. 溯源：例句永远取**真实群消息原文**（verbatim），AI 只负责归 situation/style，
   一个字都不许它编；来源记 INBOX 的真实 mid。取不到原文的候选整条丢弃。
2. 审核：默认 checked_only=True，AI 学来的只进 pending；管理员「表达 确认」才进生产。
   管理员动过的条目（modified_by=admin）AI 后续学习**只加计数，不改内容**。
3. 隔离：表达按来源群记账，只注入回**来源群**；私聊永不注入、永不提取。
4. 开关与回滚：expression.enabled 总闸关掉=全部 no-op（回到旧行为）；
   每次落盘前把上一版存 .prev，「表达 回滚」一键恢复。

和 qbot_slang.py 同样的约定：**本模块不 import bot**，宿主能力靠 bind() 注入。
"""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("qqbot")

BASE = Path(__file__).resolve().parent
EXP_FILE = BASE / "state" / "expressions.json"

_lock = threading.RLock()
EXPRESSIONS = {"expressions": {}, "next_id": 1, "since": 0}
_state = {"running": False, "last": 0.0}

# ---- 宿主钩子（未 bind 降级为空操作）----
_chat = None
_inbox = None      # inbox(gid, n) -> [{id, who, qq, text, out}]
_names = None      # names() -> [bot 自己的昵称们]
_send = None       # send_group(gid, text)
CFG = {}

MAX_SIT_CHARS = 15     # 场面描述上限（MaiBot 口径 20，我们更紧，防 AI 写小作文）
MAX_STYLE_CHARS = 15
BATCH_MAX = 8          # 单批最多收几条
BATCH_HARD_MAX = 20    # AI 一口气报超过这个数 = 跑飞，整批丢弃（MaiBot 同规）
EV_CAP = 8             # 每条表达最多留几个来源 mid
CONT_CAP = 6           # 每条表达最多存几个例句
MIN_MSGS = 6           # 候选消息不足这个数不开工
INJECT_MAX = 3         # 每轮注入条数
INJECT_CHARS = 400     # 注入段字符预算
SCORE_MIN = 2          # 语境相关性的二元组重叠下限（不到就不注=返回空）


def bind(state_dir=None, cfg=None, chat=None, inbox=None, names=None, send=None):
    """bot.py 启动时调用。cfg 传活引用（热改生效），别传快照。"""
    global _chat, _inbox, _names, _send, CFG, EXP_FILE
    if chat is not None:
        _chat = chat
    if inbox is not None:
        _inbox = inbox
    if names is not None:
        _names = names
    if send is not None:
        _send = send
    if cfg is not None:
        CFG = cfg
    if state_dir:
        EXP_FILE = Path(state_dir) / "expressions.json"
    return CFG


def _cfg():
    return CFG


def _cfgget(key, default):
    v = (CFG.get("expression") or {}).get(key)
    return default if v is None else v


def enabled():
    return bool(_cfgget("enabled", True))


def confirmed_list():
    """给 /api/status 和磁贴用的已生效条目（浅拷贝，防外部改坏）。"""
    with _lock:
        return [dict(e) for e in EXPRESSIONS["expressions"].values()
                if e.get("status") == "confirmed"]


def pending_list():
    with _lock:
        return [dict(e) for e in EXPRESSIONS["expressions"].values()
                if e.get("status") == "pending"]


# ---------------- 读写（带 .prev 轮换 = 数据回滚） ----------------
def load():
    try:
        if EXP_FILE.exists():
            data = json.loads(EXP_FILE.read_text(encoding="utf-8"))
        else:
            data = None
    except Exception as e:
        log.warning("读 expressions.json 失败: %s", e)
        data = None
    if not isinstance(data, dict) or "expressions" not in data:
        data = {"expressions": {}, "next_id": 1}
    with _lock:
        EXPRESSIONS.clear()
        EXPRESSIONS.update(data)
        EXPRESSIONS.setdefault("expressions", {})
        EXPRESSIONS.setdefault("next_id", 1)


def save():
    with _lock:
        try:
            EXP_FILE.parent.mkdir(parents=True, exist_ok=True)
            if EXP_FILE.exists():
                # 落盘前留上一版，给「表达 回滚」用
                import shutil
                shutil.copyfile(EXP_FILE, EXP_FILE.with_name(EXP_FILE.name + ".prev"))
            EXP_FILE.write_text(json.dumps(EXPRESSIONS, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        except Exception as e:
            log.error("写 expressions.json 失败: %s", e)


def rollback():
    """回滚到上一版。返回是否成功。"""
    prev = EXP_FILE.with_name(EXP_FILE.name + ".prev")
    if not prev.exists():
        return False
    try:
        import shutil
        shutil.copyfile(prev, EXP_FILE)
        load()
        return True
    except Exception as e:
        log.error("表达回滚失败: %s", e)
        return False


# ---------------- 确定性清洗 ----------------
_RE_SECRET_KV = re.compile(r"(密码|口令|密钥|令牌|验证码)\s*(?:是|为|[:=：])\s*\S+"
                           r"|\b(?:token|password|secret|api[_-]?key)\b\s*[:=：]\s*\S+", re.IGNORECASE)
_RE_TOKEN = re.compile(r"\b(?:sk|ghp|gho|xoxb)[-_][A-Za-z0-9_-]{8,}\b")
_RE_PHONE = re.compile(r"(?<!\w)1[3-9]\d{9}(?!\w)")
_RE_AT = re.compile(r"@\S{1,20}")


def redact(text):
    """例句脱敏：密钥/手机号/@人名。QQ 号保留 —— 活上下文靠号码认人是有意设计，
    且注入时出站层 strip_qq_numbers 还会再抹一道（双保险）。"""
    s = str(text or "")
    s = _RE_SECRET_KV.sub("[已隐藏]", s)
    s = _RE_TOKEN.sub("[已隐藏]", s)
    s = _RE_PHONE.sub("[号码已隐藏]", s)
    s = _RE_AT.sub("@某人", s)
    return re.sub(r"\s+", " ", s).strip()


def _is_noise(text, who="", out=False):
    """不能当学习材料的：bot 自己的话 / 占位符 / 纯符号 / 太长太短 / 带 bot 名字。"""
    t = str(text or "").strip()
    if out or not t:
        return True
    if t.startswith("引用 ") or "[图片]" in t or "[动画表情]" in t or "[转发" in t:
        return True
    body = re.sub(r"\[[^\[\]]{1,8}\]", "", t)
    body = re.sub(r"[。，！？~～\.!?,;；:：\s]+", "", body)
    if len(body) < 4 or len(t) > 60:
        return True
    names = []
    try:
        names = list(_names() or []) if _names else []
    except Exception:
        names = []
    if any(n and n in t for n in names if n):
        return True
    return False


# ---------------- 学习（AI 只分类，例句永远取原文） ----------------
def _key(situation, style):
    return "%s||%s" % (situation.strip()[:MAX_SIT_CHARS], style.strip()[:MAX_STYLE_CHARS])


def learn_batch(gid, msgs, items):
    """msgs = 候选消息 [{id, text}]（已经滤过噪）；items = AI 的分类结果。

    确定性校验：
      · idx 必须能解析回 msgs 里的一条真实消息 —— 例句直接取那条的**原文**，
        AI 的任何「content」字段一律忽略（杜绝编造）；
      · situation/style 空/超长 → 截断或丢弃；
      · 同批去重；单批超过 BATCH_HARD_MAX → 整批丢弃；
      · 已有条目按 situation+style **完全一致**才合并（计数+1、来源 id 入账）。
    返回 (merged_new, merged_count)。
    """
    if not isinstance(items, list) or len(items) > BATCH_HARD_MAX:
        return 0, 0
    by_idx = {}
    for m in msgs:
        by_idx[m["idx"]] = m
    seen_keys, new, bump = set(), 0, 0
    for it in items if isinstance(items, list) else []:
        try:
            idx = int((it or {}).get("idx"))
        except (TypeError, ValueError):
            continue
        src = by_idx.get(idx)
        if not src:
            continue   # 溯源失败：AI 指了一条不存在的消息 → 整条丢弃
        sit = str((it or {}).get("situation") or "").strip()[:MAX_SIT_CHARS]
        sty = str((it or {}).get("style") or "").strip()[:MAX_STYLE_CHARS]
        if not sit or not sty:
            continue
        k = _key(sit, sty)
        if k in seen_keys:
            continue
        seen_keys.add(k)
        content = redact(src["text"])[:80]
        with _lock:
            e = EXPRESSIONS["expressions"].get(k)
            if e is None:
                e = {"id": int(EXPRESSIONS.get("next_id", 1)),
                     "situation": sit, "style": sty, "contents": [], "count": 0,
                     "sources": {}, "status": "pending", "modified_by": "",
                     "t": time.time()}
                EXPRESSIONS["next_id"] = int(EXPRESSIONS.get("next_id", 1)) + 1
                EXPRESSIONS["expressions"][k] = e
                new += 1
            e["count"] += 1
            sl = e["sources"].setdefault(str(gid), [])
            if src["id"] not in sl:
                sl.append(src["id"])
                del sl[:-EV_CAP]
            # 管理员动过的条目：AI 只加计数，例句/字段一律不碰（任务书：不能覆盖）
            if e["modified_by"] != "admin" and content and content not in e["contents"]:
                e["contents"].append(content)
                del e["contents"][:-(CONT_CAP + 1)]
        bump += 1
    if new or bump:
        save()
    return new, bump


EXPR_CMD_RE = re.compile(r"^表达(?:\s*(列表|学习|确认|拒绝|删除|改|回滚))?\s*(.*)$")


def handle_expression(gid, raw, privileged):
    """群口令「表达 …」：列表/学习/确认/拒绝/删除/改/回滚。审核一律管理员。"""
    m = EXPR_CMD_RE.match(raw)
    act = m.group(1) or "列表"
    arg = (m.group(2) or "").strip()
    if not enabled():
        return
    with _lock:
        entries = list(EXPRESSIONS["expressions"].values())
    if act == "列表":
        pend = [e for e in entries if e.get("status") == "pending"]
        conf = [e for e in entries if e.get("status") == "confirmed"]
        def _row(e):
            c = (e.get("contents") or ["（无例句）"])[0]
            return "#%d %s/%s ×%d「%s」" % (e["id"], e["situation"], e["style"],
                                           e.get("count", 0), c[:18])
        _emit(gid, "待审核 %d 条：%s\n已生效 %d 条：%s" % (
            len(pend), "；".join(_row(e) for e in pend[:5]) or "无",
            len(conf), "；".join(_row(e) for e in conf[:5]) or "无"))
        return
    if act == "学习":
        if not privileged:
            _emit(gid, "这个得管理员来。")
            return
        _emit(gid, "我去翻翻最近的群聊，有候选报上来。")
        threading.Thread(target=expression_extract, args=(gid,), daemon=True).start()
        return
    if act == "回滚":
        if not privileged:
            _emit(gid, "这个得管理员来。")
            return
        _emit(gid, "已回滚到上一版" if rollback() else "没有可回滚的备份")
        return
    if not arg:
        _emit(gid, "用法：表达 学习 / 表达 确认 编号 / 表达 拒绝 编号 / 表达 删除 编号 / 表达 改 编号 新例句")
        return
    mm = re.match(r"#?(\d+)(?:\s+(.*))?$", arg)
    if not mm:
        _emit(gid, "编号看「表达 列表」，例如：表达 确认 3")
        return
    eid, rest = int(mm.group(1)), (mm.group(2) or "").strip()
    with _lock:
        hit = next((e for e in EXPRESSIONS["expressions"].values()
                    if int(e.get("id", 0)) == eid), None)
    if not hit:
        _emit(gid, "没找到 #%d，看「表达 列表」" % eid)
        return
    if not privileged:
        _emit(gid, "这个得管理员来。")
        return
    if act == "确认":
        if rest:
            hit["contents"] = [redact(rest)[:80]]
        hit["status"] = "confirmed"
        hit["modified_by"] = "admin"
        save()
        _emit(gid, "已生效：当%s时可以%s" % (hit["situation"], hit["style"]))
    elif act == "拒绝":
        hit["status"] = "rejected"
        hit["modified_by"] = "admin"
        save()
        _emit(gid, "不采纳 #%d" % eid)
    elif act == "删除":
        with _lock:
            EXPRESSIONS["expressions"].pop(_key(hit["situation"], hit["style"]), None)
        save()
        _emit(gid, "删掉了 #%d" % eid)
    elif act == "改":
        if not rest:
            _emit(gid, "写成：表达 改 编号 新例句")
            return
        hit["contents"] = [redact(rest)[:80]]
        hit["modified_by"] = "admin"
        save()
        _emit(gid, "改好了 #%d" % eid)


def _emit(gid, text):
    """出口钩子：bot bind 时传 send_group；没绑就只记日志（不把 bot 拖崩）。"""
    if _send:
        _send(gid, text)
    else:
        log.info("[表达] %s: %s", gid, text[:80])


# ---------------- 提取（攒够消息就跑一轮） ----------------
def _candidates(gid, n=40, blacklist=()):
    """从 INBOX 挑可学习的真实群消息。带 idx（AI 引用这个，溯源就靠它）。"""
    try:
        items = list(_inbox(gid, n) or []) if _inbox else []
    except Exception:
        items = []
    bl = set(str(x) for x in (blacklist or ()))
    out = []
    for it in items:
        if _is_noise(it.get("text"), it.get("who"), bool(it.get("out"))):
            continue
        if str(it.get("qq") or "") in bl:
            continue
        out.append({"idx": len(out), "id": it.get("id"),
                    "text": redact(str(it.get("text") or ""))})
    return out


def expression_extract(gid, report=True):
    """跑一轮提取。失败/开关关/候选不足都安静返回。"""
    if not enabled() or not _chat or not _inbox:
        return
    bl = _cfgget("blacklist", [])
    msgs = _candidates(gid, 40, bl)
    if len(msgs) < MIN_MSGS:
        return
    listing = "\n".join("%d. %s" % (m["idx"], m["text"]) for m in msgs)
    sys_p = ("你在帮 QQ 群机器人总结「这个群里大家什么场面习惯怎么说话」。"
             "从下面的群消息里挑最多 %d 组，每组给：idx（消息行号）、situation（什么场面，≤%d字）、"
             "style（这时候大家怎么说话，≤%d字）。例句不用你给，机器人会自己取原文。"
             "只输出 JSON 数组 [{\"idx\":0,\"situation\":\"…\",\"style\":\"…\"}]，挑不出来就 []。"
             % (BATCH_MAX, MAX_SIT_CHARS, MAX_STYLE_CHARS))
    try:
        _, ans = _chat([{"role": "system", "content": sys_p},
                        {"role": "user", "content": listing}])
    except Exception as e:
        log.warning("表达提取失败: %s", e)
        return
    mm = re.search(r"\[.*\]", ans or "", re.S)
    if not mm:
        return
    try:
        items = json.loads(mm.group(0))
    except Exception:
        return
    new, bump = learn_batch(gid, msgs, items)
    if report and gid:
        if new:
            _emit(gid, "学到 %d 条表达（待审核，管理员发「表达 确认 编号」才生效）" % new)
        elif bump:
            log.info("表达合并 %d 处（无新条目）", bump)


def maybe_expression_extract(gid):
    """每条群消息都路过：攒够条数、隔够时间才真跑（独立线程，不占主链路）。"""
    if not enabled() or not _cfgget("auto_extract", True):
        return
    need = int(_cfgget("extract_min_messages", 40))
    with _lock:
        EXPRESSIONS["since"] = int(EXPRESSIONS.get("since", 0)) + 1
        if EXPRESSIONS["since"] < need:
            return
        EXPRESSIONS["since"] = 0
        if _state["running"] or time.time() - _state["last"] < float(
                _cfgget("extract_every_sec", 3600)):
            return
        _state["running"] = True
        _state["last"] = time.time()

    def run():
        try:
            expression_extract(gid, report=False)
        finally:
            with _lock:
                _state["running"] = False

    threading.Thread(target=run, daemon=True).start()


# ---------------- 注入（快召回：无 LLM，语境相关才注） ----------------
def _bigrams(s):
    s = re.sub(r"[\s，。,.!！?？…~～、（）()「」]", "", str(s or ""))
    return set(s[i:i + 2] for i in range(len(s) - 1)) if len(s) >= 2 else ({s} if s else set())


def expression_block(gid, current_text, limit=None):
    """给 system prompt 的参考段。**没合适的就返回空串**，绝不硬塞。

    · 只含 status=confirmed（checked_only=False 时 pending 也放行，仍是参考）；
    · 只含**本群来源**的表达（跨群/私聊不泄漏）；
    · 相关性 = (situation+style) 与当前消息的二元组重叠 ≥ SCORE_MIN；
    · 条数/字数双上限。
    """
    if not enabled() or not current_text:
        return ""
    try:
        if int(gid) <= 0:   # 私聊（bot 把私聊编成负数）永不注入
            return ""
    except (TypeError, ValueError):
        pass
    if not _cfgget("checked_only", True):
        statuses = ("pending", "confirmed")
    else:
        statuses = ("confirmed",)
    want = _bigrams(current_text)
    if not want:
        return ""
    with _lock:
        pool = []
        for e in EXPRESSIONS["expressions"].values():
            if e.get("status") not in statuses or str(gid) not in (e.get("sources") or {}):
                continue
            # 相关性特征 = 场面/风格/**例句**三者。例句是真实群消息，跟当前消息的
            # 词汇重叠最有信息量（只看 situation 这种 4 字概括几乎碰不上）。
            feats = _bigrams(e["situation"]) | _bigrams(e["style"])
            for c in (e.get("contents") or [])[:2]:
                feats |= _bigrams(c)
            score = len(want & feats)
            if score >= SCORE_MIN:
                pool.append((score, e))
    pool.sort(key=lambda x: (-x[0], -x[1].get("count", 0)))
    lines, budget = [], INJECT_CHARS
    for score, e in pool[:int(limit or _cfgget("inject_max", INJECT_MAX))]:
        samples = " / ".join(e["contents"][:2]) or "（没例句）"
        line = "当%s时，可以%s，比如：%s" % (e["situation"], e["style"], samples)
        if len(line) > budget:
            break
        lines.append(line)
        budget -= len(line)
    if not lines:
        return ""
    log.info("表达习惯注入[%s]: %s", gid, "；".join(l.split("，比如")[0] for l in lines))
    return ("【这个群的表达习惯（参考语气用，不必照抄，不合适就当没看见）】\n" + "\n".join(lines))
