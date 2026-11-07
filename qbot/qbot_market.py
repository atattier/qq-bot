# -*- coding: utf-8 -*-
"""qbot_market —— 插件市场（可选技能包仓库 + 一键装卸）

目录约定（跟 `skills/` 同一套技能包格式，装卸就是复制/移动目录，**不联网**）：

    market/
      ├── plugins.json        目录：id / name / summary / tags / version / author
      └── <id>/skill.json     技能包本体

    install(id)    market/<id> → skills/<id>；已存在就拒绝，**绝不覆盖主人的改动**
    uninstall(id)  skills/<id> → market/_removed/<id>-<时间戳>；**移动不删除**，随时能捞回来

两条铁律：
  1. **只卸市场装进来的**。内置包（petpet / whale-memes / trendy-memes，工具在本机有原生实现）
     不在 plugins.json 里，`removable=False`，网页上不出现卸载按钮。
  2. 装完/卸完都 `qbot_skills.reload()`，网页下一次 `/api/config` 就能看到 —— 不用重启 bot。

另有一块**只读**的外链目录：AstrBot（astra）官方市场（见文件末尾）。那些插件是 AstrBot
框架的 Python 包，本机跑不了，所以只能逛、不能装 —— 别把 install 接到那上面去。

⚠️ 和 qbot_skills 一样：本模块不 import bot，宿主能力靠 bind() 注入。
"""
import json
import logging
import shutil
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("qqbot.market")

CFG = {}
BASE = Path(__file__).resolve().parent.parent
STATE = None

_DEFAULTS = {"enabled": True, "dir": "market"}


def bind(cfg, base=None, logger=None, state_dir=None):
    global CFG, BASE, STATE
    CFG = cfg
    if base:
        BASE = Path(base)
    if state_dir:
        STATE = Path(state_dir)
    if logger is not None:
        globals()["log"] = logger


def cfg():
    d = CFG.setdefault("market", {})
    for k, v in _DEFAULTS.items():
        d.setdefault(k, v)
    return d


def market_dir():
    p = (cfg().get("dir") or "market").strip()
    pp = Path(p)
    return pp if pp.is_absolute() else BASE / pp


def _skills():
    try:
        from qbot import qbot_skills
        return qbot_skills
    except Exception:  # noqa: BLE001
        return None


# ── 目录 ────────────────────────────────────────────────────────────────

def catalog():
    """市场目录 → {id: 条目}。文件缺失/写坏都当空市场，别拖垮网页。"""
    p = market_dir() / "plugins.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001
        log.warning("plugins.json 读不了（当空市场）: %s", e)
        return {}
    items = data.get("plugins") if isinstance(data, dict) else data
    out = {}
    for e in items or []:
        if not isinstance(e, dict):
            continue
        pid = str(e.get("id") or "").strip()
        if not pid or not (market_dir() / pid / "skill.json").exists():
            continue          # 目录里写了但包不存在 —— 不上架，免得点了装不了
        out[pid] = {
            "id": pid,
            "name": str(e.get("name") or pid),
            "summary": str(e.get("summary") or ""),
            "tags": [str(t) for t in (e.get("tags") or [])][:4],
            "version": str(e.get("version") or ""),
            "author": str(e.get("author") or ""),
        }
    return out


def list_all():
    """市场 + 已装（含不在市场里的内置包），已装的排前面。"""
    sk = _skills()
    cat = catalog()
    inst = {}
    if sk:
        try:
            for s in sk.scan():
                inst[s["id"]] = s
        except Exception as e:  # noqa: BLE001
            log.warning("扫描技能包失败: %s", e)
    out = []
    for pid, e in cat.items():
        s = inst.get(pid)
        out.append({
            "id": pid, "name": e["name"], "summary": e["summary"],
            "tags": e["tags"], "version": e["version"], "author": e["author"],
            "installed": s is not None, "builtin": False,
            "on": bool(sk.is_on(pid)) if (s and sk) else False,
            "removable": s is not None,
            "error": (s or {}).get("err") or "",
        })
    for pid, s in inst.items():
        if pid in cat:
            continue
        out.append({
            "id": pid, "name": s["name"],
            "summary": (s.get("desc") or "").splitlines()[0][:200],
            "tags": ["内置"], "version": s.get("version") or "", "author": "随 bot 自带",
            "installed": True, "builtin": True,
            "on": bool(sk.is_on(pid)) if sk else False,
            "removable": False, "error": s.get("err") or "",
        })
    out.sort(key=lambda x: (not x["installed"], x["builtin"], x["name"]))
    return out


# ── 装卸 ────────────────────────────────────────────────────────────────

def install(pid):
    """把市场里的包复制进 skills/。已存在就拒绝（不覆盖主人改过的提示词）。"""
    pid = str(pid or "").strip()
    cat = catalog()
    if pid not in cat:
        return False, "市场里没有「%s」（刷新页面看看）" % (pid or "?")
    sk = _skills()
    if not sk:
        return False, "技能加载器没就绪（qbot_skills 导入失败）"
    src = market_dir() / pid
    dst = sk.skills_dir() / pid
    if dst.exists():
        return False, "「%s」已经装过了 —— 要重装先卸载（卸载是移到 market/_removed，不删）" % cat[pid]["name"]
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(src), str(dst))
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(str(dst), ignore_errors=True)
        return False, "复制失败（已清掉半截目录）：%s" % e
    sk.reload()
    log.info("插件市场：安装 %s → %s", pid, dst)
    return True, "「%s」装好了，现在就能用（开关默认开）" % cat[pid]["name"]


def uninstall(pid):
    """把装进来的包移出 skills/（移到 market/_removed，不删除）。"""
    pid = str(pid or "").strip()
    sk = _skills()
    if not sk:
        return False, "技能加载器没就绪（qbot_skills 导入失败）"
    if pid not in catalog():
        return False, "「%s」不是市场装的，不能在这里卸（内置包动了会连累群口令）" % (pid or "?")
    dst = sk.skills_dir() / pid
    if not dst.exists():
        return False, "「%s」本来就没装" % pid
    grave = market_dir() / "_removed"
    try:
        grave.mkdir(parents=True, exist_ok=True)
        target = grave / ("%s-%s" % (pid, time.strftime("%Y%m%d-%H%M%S")))
        shutil.move(str(dst), str(target))
    except Exception as e:  # noqa: BLE001
        return False, "移动失败：%s" % e
    # 顺手把它在 config 里的开关清掉，免得以后重装带着旧状态
    try:
        c = sk.cfg()
        c.get("en", {}).pop(pid, None)
        c.get("settings", {}).pop(pid, None)
    except Exception:  # noqa: BLE001
        pass
    sk.reload()
    log.info("插件市场：卸载 %s → %s", pid, target)
    return True, "「%s」已卸载（文件移到 market/_removed/%s，没删）" % (pid, target.name)


# ══════════════════════════════════════════════════════════ AstrBot 市场（只读）
#
# 「直连 astra 的插件市场」能做到哪一步，先说清楚（2026-10-26 实地探过）：
#   · plugins.astrbot.app 是个 Vue SPA，数据打进 JS bundle，**没有对外 API**；
#     任何路径都回同一份 HTML，别指望扒接口。
#   · 真正机读的只有官方仓库的 plugins.json（1332 条，约 480KB）：
#       AstrBotDevs/AstrBot_Plugins_Collection/plugins.json
#     国内直连 raw.githubusercontent.com 常超时，jsDelivr 的 gh 镜像稳定（1.7s）。
#   · 那些插件是 **AstrBot 框架的 Python 包**（metadata.yaml + main.py），跟本机
#     「skill.json + 原生 Python 工具」是两套东西 —— **装不进来**。
# 所以这里只做「逛」：拉目录 → 缓存 → 搜索 → 给 GitHub 链接。绝不提供安装。

ASTRBOT_REPO = "AstrBotDevs/AstrBot_Plugins_Collection"
ASTRBOT_SOURCES = (
    "https://fastly.jsdelivr.net/gh/%s@main/plugins.json",
    "https://gcore.jsdelivr.net/gh/%s@main/plugins.json",
    "https://cdn.jsdelivr.net/gh/%s@main/plugins.json",
    "https://raw.githubusercontent.com/%s/main/plugins.json",
)
ASTRBOT_TTL = 7 * 86400          # 超过 7 天在页面上提示「该更新了」，不自动拉
_AS = {"data": None}
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _read_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
    tmp.replace(p)


def _as_file():
    root = STATE or (BASE / "state")
    return Path(root) / "market" / "astrbot.json"


def _as_norm(key, e):
    """官方记录 → 本页要的字段。原记录没有 stars/download_count，别编。"""
    if not isinstance(e, dict):
        return None
    desc = e.get("short_desc") or e.get("desc") or ""
    desc = " ".join(str(desc).split())[:220]
    tags = e.get("tags") or e.get("tag") or []
    if isinstance(tags, str):
        tags = [tags]
    return {
        "id": str(key),
        "name": str(e.get("display_name") or e.get("name") or key)[:60],
        "desc": desc,
        "author": str(e.get("author") or "")[:40],
        "repo": str(e.get("repo") or e.get("download_url") or "")[:200],
        "category": str(e.get("category") or "")[:24],
        "tags": [str(t)[:16] for t in list(tags)[:4]],
        "version": str(e.get("version") or "")[:20],
    }


def astrbot_data():
    """内存 → 磁盘缓存。**永不联网**（联网只在 astrbot_sync 里，且由人点）。"""
    if _AS["data"] is not None:
        return _AS["data"]
    d = _read_json(_as_file(), None)
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        d = {"at": 0, "items": [], "source": ""}
    _AS["data"] = d
    return d


def astrbot_status():
    d = astrbot_data()
    at = float(d.get("at") or 0)
    age = (time.time() - at) if at else None
    return {
        "at": int(at), "count": len(d.get("items") or []),
        "age_days": round(age / 86400, 1) if age is not None else None,
        "stale": (age is None) or age > ASTRBOT_TTL,
        "source": d.get("source") or "",
        "repo": "https://github.com/" + ASTRBOT_REPO,
    }


def astrbot_sync(timeout=20):
    """拉一次官方目录。**失败不动旧缓存**（宁可看旧的，也别把页面弄空）。"""
    body, src = None, ""
    errs = []
    for tpl in ASTRBOT_SOURCES:
        url = (tpl % ASTRBOT_REPO) if "%s" in tpl else tpl
        # 先直连（国内 jsDelivr 直连就通），再试系统代理 —— 反过来会让没开代理的机器白等
        for opener in (urllib.request.build_opener(urllib.request.ProxyHandler({})),
                       urllib.request.build_opener()):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": _UA})
                with opener.open(req, timeout=timeout) as r:
                    body = r.read()
                src = url.split("/")[2]
                break
            except Exception as e:  # noqa: BLE001
                errs.append("%s: %s" % (url.split("/")[2], type(e).__name__))
        if body:
            break
    if not body:
        return False, "拉不到 AstrBot 目录（%s）。旧缓存还在，不影响本地市场。" % (
            "；".join(errs[:3]) or "网络不通")
    try:
        raw = json.loads(body.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return False, "目录格式不对（%s）：%s" % (src, e)
    if not isinstance(raw, dict):
        return False, "目录格式不对（%s）：顶层不是对象" % src
    items = []
    for k, v in raw.items():
        if str(k).startswith("$"):
            continue
        it = _as_norm(k, v)
        if it and it["repo"]:
            items.append(it)
    if not items:
        return False, "目录是空的（%s），不动旧缓存" % src
    data = {"at": time.time(), "items": items, "source": src}
    try:
        _write_json(_as_file(), data)
    except Exception as e:  # noqa: BLE001
        return False, "缓存写不进去：%s" % e
    _AS["data"] = data
    log.info("AstrBot 目录已同步：%d 条（来源 %s）", len(items), src)
    return True, "同步好了，共 %d 个 AstrBot 插件（来源 %s）" % (len(items), src)


ASTRBOT_NOCAT = "未分类"
# 官方目录的 category 是英文小写（只有 6 个值），下拉里直接摆英文太生硬，给个中文名。
# 没登记的按原值显示 —— 以后官方加新分类也不会变成空白选项。
ASTRBOT_CAT_CN = {
    "entertainment": "娱乐",
    "utilities": "实用工具",
    "ai_tools": "AI 工具",
    "integrations": "平台接入",
    "productivity": "效率",
    "media": "媒体",
    "game": "游戏",
    "search": "搜索",
    "memory": "记忆",
    "developer": "开发",
    "admin": "管理",
    ASTRBOT_NOCAT: "未分类",
}


def astrbot_cats(items=None):
    """分类统计（**全量**，跟当前搜索词无关），给网页那个筛选下拉用。

    为什么不跟着搜索结果变：主人在「工具」里搜「语音」，下拉还应该能看见别的分类还剩多少，
    不然筛完一次就不知道还能往哪跳了。
    """
    if items is None:
        items = astrbot_data().get("items") or []
    cnt = {}
    for it in items:
        k = str(it.get("category") or "").strip() or ASTRBOT_NOCAT
        cnt[k] = cnt.get(k, 0) + 1
    out = [{"name": k, "label": ASTRBOT_CAT_CN.get(k, k), "count": v} for k, v in cnt.items()]
    out.sort(key=lambda x: (-x["count"], x["name"]))
    return out


def astrbot_search(q="", limit=24, offset=0, category=""):
    """搜索 AstrBot 目录。空格分词、**命中任一即可**，命中词多的排前面；空关键词就是前 N 条。

    为什么不是 AND：中文里「语音 签到」这种组合本来就是"想找这两类"，AND 直接零结果
    （自测踩到：1332 条里 AND 命中 0）。OR + 按命中数排序，才符合人的预期。

    2026-10-26 第二轮：主人问「为啥只显示 24 个」—— 以前 limit 写死 24、上限 60，
    页面上连个「还有多少」都没有。现在支持 `offset` 翻页 + `category` 筛选，
    上限放到 300，返回 `hasMore` / `matched` / `cats` 让页面能自己算。
    """
    d = astrbot_data()
    items = d.get("items") or []
    toks = [t for t in str(q or "").lower().split() if t]
    try:
        lim = int(limit or 24)
    except (TypeError, ValueError):
        lim = 24
    lim = max(1, min(lim, 300))
    try:
        off = max(0, int(offset or 0))
    except (TypeError, ValueError):
        off = 0
    cat = str(category or "").strip()
    pool = items
    if cat and cat not in ("全部", "all", "*"):
        if cat == ASTRBOT_NOCAT:
            pool = [it for it in items if not str(it.get("category") or "").strip()]
        else:
            pool = [it for it in items if str(it.get("category") or "").strip() == cat]
    if toks:
        scored = []
        for it in pool:
            bag = " ".join([it.get("name", ""), it.get("desc", ""), it.get("author", ""),
                            it.get("category", ""), it.get("id", ""),
                            " ".join(it.get("tags") or [])]).lower()
            n = sum(1 for t in toks if t in bag)
            if n:
                scored.append((n, it))
        scored.sort(key=lambda x: -x[0])
        hit = [it for _n, it in scored]
    else:
        hit = list(pool)
    page = hit[off:off + lim]
    st = astrbot_status()
    return {
        "ok": True,
        "items": page,
        "matched": len(hit),
        "total": len(items),
        "offset": off,
        "shown": off + len(page),
        "has_more": off + len(page) < len(hit),
        "q": str(q or ""),
        "category": cat,
        "cats": astrbot_cats(items),
        "at": st["at"], "age_days": st["age_days"], "stale": st["stale"],
        "source": st["source"], "repo": st["repo"],
    }

