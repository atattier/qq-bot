#!/usr/bin/env python3
"""梗百科（2026-10-26 加）：内置 6668 条词条的本地梗库，毫秒级查梗；没收录才联网。

移植自 AstrBot 插件 `astrbot_plugin_memewiki`（作者 in1t1a1，MIT）。
AstrBot 的插件是 Python 包（metadata.yaml + main.py + AstrBot 运行时），本机不吃那一套，
所以按本机技能包的规矩重写了一份，只留真正有用的部分：

  · 词库：`state/memewiki/memes.db`（SQLite 只读快照，6668 条，来自插件发布包）
  · 兜底：cnmeme.wiki 公开只读接口（无需 Key；本地没收录才查，同一个词 10 分钟不重查）
  · 群内黑话：**不重复造**，直接用本机 qbot_slang 那套「黑话」的结果（群里自产的词优先）
  · 卡片：「梗 X」默认回图片卡片（PIL 画，版式照原插件），设置里能切纯文字

为什么不把词库塞进 skills/：16MB 的二进制，装/卸插件会跟着搬来搬去；
放 state 里，重装插件也能直接复用，卸载也不会丢。

⚠️ 词条版权归 cnmeme.wiki 及原作者，卡片/正文底部保留来源链接；抓取限速 0.35 秒/次。
"""

from __future__ import annotations

import html as htmllib
import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://cnmeme.wiki/api/v1/public/memes"
UA = "QQBot-memewiki/1.0 (+local port)"
CACHE_TTL = 6 * 3600          # 在线结果缓存
MISS_TTL = 600                # 同一个词没查到，10 分钟内不再联网
MAX_CHARS = 1200              # 注入正文上限（跟原插件一致）
SEARCH_LIMIT = 5
SYNC_SLEEP = 0.35             # 站点要求：0.35 秒/次
SYNC_PAGE = 30

# 词条标签里带这些的一律不查（擦边内容本机不接，见人设里的「擦边内容拦截」）
BLOCK_TAGS = ("擦边", "色情", "成人", "裸露", "福利", "性暗示")

BASE = Path(".").resolve()
STATE = BASE / "state"
log = None

_DB = {"path": None, "mtime": 0.0, "conn": None}
_HIT = {}      # term -> (expire, data or None)
_MISS = {}     # term -> expire
_SYNC = {"running": False, "total": 0, "done": 0, "added": 0, "msg": "", "at": 0.0}
_LOCK = threading.Lock()


def bind(base=None, state_dir=None, logger=None):
    global BASE, STATE, log
    if base:
        BASE = Path(base)
    if state_dir:
        STATE = Path(state_dir)
    if logger is not None:
        log = logger


def _warn(msg, *a):
    if log:
        try:
            log.warning(msg, *a)
        except Exception:  # noqa: BLE001
            pass


def _info(msg, *a):
    if log:
        try:
            log.info(msg, *a)
        except Exception:  # noqa: BLE001
            pass


# ── 设置 ────────────────────────────────────────────────────────────────

def _cfg():
    """技能参数：config.json 的 skills.settings.memewiki 覆盖 skill.json 的默认值。"""
    d = {"replyMode": "card", "online": True, "inject": True,
         "shortMax": 8, "maxChars": MAX_CHARS,
         # 2026-10-26 晚：主人问「会每天自动更新吗」——原来只有手动按钮，现在补了每天定时
         "autoSync": True, "autoSyncHour": 4}
    try:
        import qbot_skills
        d.update(qbot_skills.settings_of("memewiki") or {})
    except Exception:  # noqa: BLE001
        pass
    return d


def _opener():
    # 国内站点直连，绕开 Clash（没开代理时裸 urlopen 会 10061）
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with _opener().open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _read_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(p, obj):
    try:
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        return True
    except Exception as e:  # noqa: BLE001
        _warn("梗百科写 %s 失败: %s", p, e)
        return False


# ── 词库 ────────────────────────────────────────────────────────────────

def db_path() -> Path:
    return STATE / "memewiki" / "memes.db"


def _conn():
    """只读连接；文件换了（同步过）自动重开，别一直读旧快照。"""
    p = db_path()
    if not p.exists():
        return None
    try:
        mt = p.stat().st_mtime
    except OSError:
        return None
    if _DB["conn"] is not None and _DB["path"] == str(p) and _DB["mtime"] == mt:
        return _DB["conn"]
    try:
        c = sqlite3.connect("file:%s?mode=ro" % p.as_posix(), uri=True, check_same_thread=False)
        c.row_factory = sqlite3.Row
        _DB.update(path=str(p), mtime=mt, conn=c)
        return c
    except Exception as e:  # noqa: BLE001
        _warn("梗百科词库打不开: %s", e)
        return None


def stats() -> dict:
    """给网页抽屉看：多少条、什么时候生成的、多大。"""
    p = db_path()
    if not p.exists():
        return {"ok": True, "count": 0, "built_at": "", "mb": 0.0, "path": str(p)}
    c = _conn()
    count, built = 0, ""
    if c is not None:
        try:
            row = c.execute("SELECT value FROM meta WHERE key='count'").fetchone()
            count = int(row[0]) if row else 0
            row = c.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
            built = row[0] if row else ""
        except Exception:  # noqa: BLE001
            pass
    try:
        mb = round(p.stat().st_size / 1048576.0, 1)
    except OSError:
        mb = 0.0
    return {"ok": True, "count": count, "built_at": built, "mb": mb, "path": str(p)}


def _row_data(row) -> dict:
    tags = [t for t in str(row["tags"] or "").split("、") if t]
    usage = str(row["usage"] or "")
    origin = str(row["origin"] or "")
    other = str(row["other"] or "")
    if not usage and not origin:
        other = other or str(row["summary"] or "")
    return {"title": row["title"], "tags": tags, "usage": usage, "origin": origin,
            "other": other, "slug": str(row["slug"] or ""), "note": "", "extra": []}


def _blocked(data) -> bool:
    return any(t in BLOCK_TAGS for t in (data.get("tags") or []))


def find_local(term: str):
    """只认完整梗名：消息里必须出现完整标题，反过来（消息是标题前缀）不算。

    例：「你是什么」不会命中「你是什么冠军」；「你说你是什么冠军」会命中。
    """
    term = (term or "").strip()
    if not term:
        return None
    c = _conn()
    if c is None:
        return None
    try:
        row = c.execute(
            "SELECT title, slug, tags, usage, origin, other, summary FROM memes WHERE title = ?",
            (term,)).fetchone()
        if row is None:
            row = c.execute(
                "SELECT title, slug, tags, usage, origin, other, summary FROM memes "
                "WHERE length(title) >= 2 AND instr(?, title) > 0 "
                "ORDER BY length(title) DESC LIMIT 1", (term,)).fetchone()
        return _row_data(row) if row else None
    except Exception as e:  # noqa: BLE001
        _warn("梗百科查词失败: %s", e)
        return None


# ── 在线兜底（本地没收录才走） ──────────────────────────────────────────

def _html_to_text(raw: str) -> str:
    s = re.sub(r"<h2[^>]*>", "\n## ", raw or "", flags=re.I)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"</p>|<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _split_sections(text: str) -> dict:
    """「## 用法 / ## 来源」拆成三段（跟插件 pack_data.py 一个规矩）。"""
    parts = re.split(r"\n##\s*", "\n" + (text or ""))
    usage, origin, other = [], [], []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        head, _, rest = part.partition("\n")
        head = head.strip()
        if any(k in head for k in ("用法", "怎么用", "如何用", "例句", "接梗")):
            usage.append(rest.strip() or head)
        elif any(k in head for k in ("来源", "出处", "背景", "起源", "介绍")):
            origin.append(rest.strip() or head)
        else:
            other.append((head + "\n" + rest).strip() if rest else head)
    return {"usage": "\n".join(usage).strip(), "origin": "\n".join(origin).strip(),
            "other": "\n".join(other).strip()}


def _api_get(url, params=None, timeout=12):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(2):
        try:
            return _http_json(url, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(3)
                continue
            if e.code == 404:
                return None
            _warn("梗百科在线查询 HTTP %s", e.code)
            return None
        except Exception as e:  # noqa: BLE001
            if attempt == 0:
                time.sleep(1)
                continue
            _warn("梗百科在线查询失败(%s): %s", type(e).__name__, e)
            return None
    return None


def find_online(term: str):
    """在线搜一次。只认「完整标题出现在查询里」，不做前缀式模糊匹配。"""
    data = _api_get(API, {"q": term, "limit": SEARCH_LIMIT})
    if not data:
        return None
    items = data.get("items") or []
    if not items:
        return None
    q = term.strip()
    picked = None
    for it in items:
        if str(it.get("title") or "").strip() == q:
            picked = it
            break
    if picked is None:
        for it in items:
            title = str(it.get("title") or "").strip()
            if len(title) >= 2 and title in q:
                picked = it
                break
    if picked is None:
        return None
    detail = _api_get(API + "/" + urllib.parse.quote(str(picked.get("slug") or ""), safe=""))
    item = (detail or {}).get("item") or picked
    body = _html_to_text(item.get("content_html") or "") or str(item.get("summary") or "")
    sec = _split_sections(body)
    usage, origin, other = sec["usage"], sec["origin"], sec["other"]
    if not usage and not origin:
        other = body
    return {"title": item.get("title") or q,
            "tags": [str(t) for t in (item.get("tags") or [])],
            "usage": usage.strip(), "origin": origin.strip(), "other": other.strip(),
            "slug": str(item.get("slug") or picked.get("slug") or ""),
            "note": "", "extra": [], "online": True}


# ── 群内黑话（复用 qbot_slang，不另存一份） ─────────────────────────────

def _slang_hit(term: str):
    try:
        import qbot_slang
        terms = (qbot_slang.SLANG or {}).get("terms") or {}
    except Exception:  # noqa: BLE001
        return None
    t = (term or "").strip()
    if not t:
        return None
    for k, v in terms.items():
        if v.get("status") != "confirmed":
            continue
        if k == t or (len(k) >= 2 and k in t):
            return {"title": k, "tags": ["群内黑话"],
                    "usage": str(v.get("meaning") or ""), "origin": "", "other": "",
                    "slug": "", "note": "（群内黑话，不是网上的梗）", "extra": [],
                    "custom": "群内黑话 · 本群自己记的"}
    return None


# ── 查询主入口 ──────────────────────────────────────────────────────────

def lookup_data(term: str, allow_online=True):
    """返回 (data, src)。src: slang / local / online / blocked / none。"""
    term = (term or "").strip()
    if not term:
        return None, "none"
    hit = _slang_hit(term)
    if hit:
        return hit, "slang"
    data = find_local(term)
    if data:
        return (None, "blocked") if _blocked(data) else (data, "local")
    if not allow_online:
        return None, "none"
    now = time.time()
    c = _HIT.get(term)
    if c and c[0] > now:
        d = c[1]
        return (d, "online") if d else (None, "none")
    if float(_MISS.get(term) or 0) > now:
        return None, "none"
    d = find_online(term)
    if d and _blocked(d):
        return None, "blocked"
    if d:
        _HIT[term] = (now + CACHE_TTL, d)
        if len(_HIT) > 300:
            _HIT.pop(min(_HIT.items(), key=lambda kv: kv[1][0])[0], None)
    else:
        _MISS[term] = now + MISS_TTL
        if len(_MISS) > 600:        # 2026-11-05 U9：负缓存也做逐出（原来只有全清，
            _MISS.pop(min(_MISS.items(), key=lambda kv: kv[1])[0], None)
            # 长期运行轻微无界——审计 P1-6）
    return (d, "online") if d else (None, "none")


def _clip(s, n):
    s = str(s or "").strip()
    return (s[:n] + "…") if len(s) > n else s


def render_text(data: dict, for_llm=False) -> str:
    """给模型看：用法在前（先接梗）；给群友看：来源在前。"""
    maxc = int(_cfg().get("maxChars") or MAX_CHARS)
    lines = ["【梗百科】" + str(data.get("title") or "")]
    if data.get("note"):
        lines.append(data["note"])
    if data.get("tags"):
        lines.append("标签：" + "、".join(data["tags"][:10]))
    if for_llm:
        if data.get("usage"):
            head = "【含义】" if data.get("custom") else "【怎么用 / 怎么接】"
            lines.append(head + "\n" + _clip(data["usage"], 600))
        if data.get("origin"):
            lines.append("【出处（仅用于理解，别照搬）】\n" + _clip(data["origin"], 450))
    else:
        if data.get("origin"):
            lines.append("【来源】\n" + _clip(data["origin"], 450))
        if data.get("usage"):
            head = "【含义】" if data.get("custom") else "【用法】"
            lines.append(head + "\n" + _clip(data["usage"], 600))
    if data.get("other"):
        lines.append(_clip(data["other"], 400))
    if data.get("extra"):
        lines.append("同批检索到的其它相关词条：" + "、".join(data["extra"]))
    if data.get("custom"):
        lines.append(str(data["custom"]))
    else:
        lines.append("cnmeme.wiki/词条/" + str(data.get("slug") or ""))
    out = "\n".join(lines)
    return out if len(out) <= maxc else out[:maxc] + "…"


# ── 图片卡片 ────────────────────────────────────────────────────────────

_FONTS_REG = [
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhl.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]
_FONTS_BOLD = [
    "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def _font(size, bold=False):
    from PIL import ImageFont
    for p in (_FONTS_BOLD if bold else _FONTS_REG):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def render_card(data: dict):
    """画一张 900px 宽的卡片，返回路径；画不出来返回 None（调用方退回文字）。"""
    try:
        from PIL import Image, ImageDraw
    except Exception as e:  # noqa: BLE001
        _warn("梗百科画卡片跳过（没有 PIL）: %s", e)
        return None
    try:
        W, PAD = 900, 44
        f_title, f_sec, f_body = _font(42, True), _font(26, True), _font(24)
        f_meta, f_small = _font(20), _font(18)
        inner = W - PAD * 2

        def wrap(text, font, width):
            out = []
            for para in str(text or "").split("\n"):
                para = para.strip()
                if not para:
                    continue
                cur = ""
                for ch in para:
                    if font.getlength(cur + ch) <= width:
                        cur += ch
                    else:
                        if cur:
                            out.append(cur)
                        cur = ch
                if cur:
                    out.append(cur)
            return out

        blocks = [("title", wrap(data.get("title"), f_title, inner), f_title, (17, 24, 39), 0)]
        if data.get("tags"):
            blocks.append(("meta", wrap("、".join(data["tags"][:8]), f_meta, inner),
                           f_meta, (107, 114, 128), 12))
        if data.get("origin"):
            blocks.append(("sec", ["来源"], f_sec, (37, 99, 235), 26))
            blocks.append(("body", wrap(_clip(data["origin"], 520), f_body, inner),
                           f_body, (55, 65, 81), 10))
        if data.get("usage"):
            blocks.append(("sec", ["含义" if data.get("custom") else "用法"], f_sec,
                           (37, 99, 235), 22))
            blocks.append(("body", wrap(_clip(data["usage"], 700), f_body, inner),
                           f_body, (31, 41, 55), 10))
        if data.get("note"):
            blocks.append(("meta", wrap(data["note"], f_meta, inner), f_meta, (146, 64, 14), 14))

        footer = data.get("custom") or ("cnmeme.wiki/词条/" + str(data.get("slug") or ""))
        heights, total = [], PAD
        for _k, lines, font, _c, gap in blocks:
            h = gap + int(font.size * 1.5) * len(lines)
            heights.append(h)
            total += h
        total += 22 + int(f_small.size * 1.5) + PAD

        img = Image.new("RGB", (W, total), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, W, 8], fill=(37, 99, 235))
        y = PAD
        for (kind, lines, font, color, gap), h in zip(blocks, heights):
            y += gap
            lh = int(font.size * 1.5)
            for line in lines:
                draw.text((PAD, y), line, font=font, fill=color)
                y += lh
            if kind == "title":
                draw.line([(PAD, y + 10), (W - PAD, y + 10)], fill=(229, 231, 235), width=2)
                y += 12
        draw.line([(PAD, total - PAD - 26), (W - PAD, total - PAD - 26)],
                  fill=(229, 231, 235), width=2)
        draw.text((PAD, total - PAD - 20), footer, font=f_small, fill=(156, 163, 175))

        out_dir = STATE / "memewiki" / "cards"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / ("meme_%d.png" % int(time.time() * 1000))
        img.save(path)
        try:  # 只留最近 30 张
            olds = sorted(out_dir.glob("meme_*.png"))
            for p in olds[:-30]:
                p.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return path
    except Exception as e:  # noqa: BLE001
        _warn("梗百科画卡片失败: %s", e)
        return None


def answer(term: str):
    """给群里「梗 X」用：返回 (kind, payload)，kind = card / text / none。"""
    if int(stats().get("count") or 0) <= 0:
        return "text", ("梗百科的词库还没装（state/memewiki/memes.db 不见了）。"
                        "把插件发布包里的 data/memes.db 拷到那儿就行，网页抽屉里能看到状态。")
    data, src = lookup_data(term, allow_online=bool(_cfg().get("online", True)))
    if src == "blocked":
        return "text", "这个不查。"
    if not data:
        return "text", "词库里没有收录「%s」。可能是刚出来的新梗、群里的自造词，或者打错字了。" % term
    mode = str(_cfg().get("replyMode") or "card").strip().lower()
    if mode.startswith(("card", "image", "图")):
        p = render_card(data)
        if p:
            return "card", str(p)
    return "text", render_text(data, for_llm=False)


# ── 自动预查（不依赖模型自觉） ──────────────────────────────────────────

_ASK = re.compile(
    r"(?:是)?(?:什么梗|啥梗|什么来头|什么来历|什么出处|什么意思|啥意思|怎么用|怎么接|怎么玩|如何用)"
    r"(?:啊|呀|呢|的|啊这是)?[？?。！!~～]*$")
_BARE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9·]{2,10}$")
_SENTENCE = re.compile(r"怎么样|怎么|多少|为什么|能不能|是不是|什么|了吗|了没|一下|你说|要不要|有没有")
_STOP = {"在吗", "在不在", "哈哈", "哈哈哈", "笑死", "好的", "好吧", "晚安", "早安",
         "睡觉", "吃饭", "干嘛", "没事", "收到", "谢谢", "好耶", "草", "典", "乐",
         "牛", "绝了", "行", "嗯嗯", "好滴", "早", "晚安啦"}
_PRONOUN = ("自己", "你们", "我们", "他们", "她们", "咱们", "大家", "别人")
_WAKE = ("小鲸鱼", "大鲸鱼", "大肥鲸", "小肥鲸", "大肥鱼", "奶鱼", "鲸鱼")


def _clean(text: str) -> str:
    t = str(text or "").strip()
    t = re.sub(r"^(@\S+\s*)+", "", t)
    for w in _WAKE:
        if t.startswith(w):
            t = t[len(w):].strip(" ，,。.！!？?:：")
            break
    return t.strip()


def extract_term(text: str):
    """返回 (term, explicit)。explicit=True 表示「明确在问梗」。"""
    t = _clean(text)
    if not t:
        return "", False
    m = _ASK.search(t)
    if m:
        head = t[:m.start()].strip(" ，,。.！!？?")
        head = head.strip("是").strip(" ，,。.！!？?")
        head = re.sub(r"(这个梗|那个梗|这梗|那梗|这个|那个)$", "", head).strip()
        head = re.sub(r"(到底|究竟)$", "", head).strip()
        if head in {"这", "那", "它", "他", "她", "这个", "那个", "你",
                    "这句话", "那句话", "上面", "刚才", "下文", "前面"}:
            return "", False
        if len(head) <= 2 and head[:1] in {"这", "那", "它", "他", "她"}:
            return "", False
        if 1 < len(head) <= 12:
            return head, True
        return "", False
    if _BARE.match(t) and t not in _STOP:
        short_max = int(_cfg().get("shortMax") or 8)
        if len(t) > short_max or _SENTENCE.search(t):
            return "", False
        if any(w in t for w in _PRONOUN):
            return "", False
        return t, False
    return "", False


def prelook(text: str) -> str:
    """给大脑「垫一句」：命中本地词库就注入词条，明确问梗但没收录就注入「别硬编」。

    返回 "" 表示这轮不插话（大部分闲聊都会走这里，别让它变成噪声源）。
    """
    conf = _cfg()
    if not bool(conf.get("inject", True)):
        return ""
    term, explicit = extract_term(text)
    if not term:
        return ""
    data, src = lookup_data(term, allow_online=bool(conf.get("online", True)) and explicit)
    if src == "blocked":
        return ""
    if data:
        return render_text(data, for_llm=True)
    if explicit:
        return ("[梗百科] 本地词库（%d 条）里没有收录「%s」。\n"
                "这个词看起来像在问梗：**别硬编**。不确定就直说「这个我不太确定」，"
                "或者反问一句「这什么梗」；不要把两个不相干的梗拼在一起，也不要把旧梗当新梗解释。"
                % (int(stats().get("count") or 0), term))
    return ""


# ── 工具入口（给 MCP / 技能工具调用） ───────────────────────────────────

def tool_lookup(term="", **kw):
    term = str(term or "").strip()
    if not term:
        return False, "要查哪个梗？给我个词。"
    data, src = lookup_data(term, allow_online=bool(_cfg().get("online", True)))
    if src == "blocked":
        return True, "这个不查。"
    if not data:
        return True, ("词库里没有收录「%s」（现有 %d 条）。真拿不准就说不确定，别硬编。"
                      % (term, int(stats().get("count") or 0)))
    tag = {"slang": "（群内黑话）", "online": "（在线查到的，本地词库没收录）"}.get(src, "")
    return True, tag + render_text(data, for_llm=True)


def tool_catalog(limit=30, **kw):
    try:
        lim = max(1, min(int(limit or 30), 200))
    except (TypeError, ValueError):
        lim = 30
    c = _conn()
    if c is None:
        return True, "词库还没装（state/memewiki/memes.db 不存在）。"
    try:
        titles = [r[0] for r in c.execute("SELECT title FROM memes LIMIT ?", (lim,))]
        n = int(stats().get("count") or 0)
    except Exception as e:  # noqa: BLE001
        return False, "词库读不了：%s" % e
    out = "梗百科词库 %d 条，先看这些：%s" % (n, "、".join(titles))
    if n > lim:
        out += "……（要查具体的直接说「梗 词」）"
    return True, out


# ── 词库更新（只补新增，不重抓全量） ────────────────────────────────────

def sync_status():
    d = dict(_SYNC)
    d.update(stats())
    d["auto"] = auto_status()
    d["ok"] = True
    return d


def sync_start():
    with _LOCK:
        if _SYNC["running"]:
            return sync_status()
        _SYNC.update(running=True, total=0, done=0, added=0, msg="正在拉站点列表…", at=time.time())
    threading.Thread(target=_sync_worker, daemon=True).start()
    return sync_status()


def _sync_worker():
    added = checked = 0
    empty_pages = 0
    stop_msg = ""
    try:
        if not db_path().exists():
            _SYNC["msg"] = "还没有词库，先手动放一份 memes.db 进来"
            return
        conn = sqlite3.connect(str(db_path()))
        conn.execute("CREATE TABLE IF NOT EXISTS memes (title TEXT PRIMARY KEY, slug TEXT, "
                     "tags TEXT, usage TEXT, origin TEXT, other TEXT, summary TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        known = {r[0] for r in conn.execute("SELECT title FROM memes")}
        offset = 0
        while empty_pages < 2:
            data = _api_get(API, {"limit": SYNC_PAGE, "offset": offset}, timeout=20)
            if not data:
                stop_msg = "站点没响应（从第 %d 条起），先停下" % offset
                break
            batch = data.get("items") or []
            if not batch:
                break
            _SYNC["total"] = offset + len(batch)
            new_here = 0
            for it in batch:
                title = str(it.get("title") or "").strip()
                slug = str(it.get("slug") or "").strip()
                checked += 1
                _SYNC["done"] = checked
                if not title or not slug or title in known:
                    continue
                time.sleep(SYNC_SLEEP)
                detail = _api_get(API + "/" + urllib.parse.quote(slug, safe=""), timeout=20)
                item = (detail or {}).get("item") or it
                body = _html_to_text(item.get("content_html") or "") or str(item.get("summary") or "")
                sec = _split_sections(body)
                usage, origin, other = sec["usage"], sec["origin"], sec["other"]
                if not usage and not origin:
                    other = body
                conn.execute("INSERT OR REPLACE INTO memes VALUES (?,?,?,?,?,?,?)",
                             (title, slug, "、".join(str(t) for t in (item.get("tags") or [])),
                              usage, origin, other, str(item.get("summary") or "")))
                known.add(title)
                added += 1
                new_here += 1
                _SYNC.update(added=added, msg="新增 %d 条，正在补「%s」…" % (added, title))
            conn.commit()
            empty_pages = empty_pages + 1 if new_here == 0 else 0
            offset += SYNC_PAGE
            if not data.get("has_more"):
                break
        total = int(conn.execute("SELECT COUNT(*) FROM memes").fetchone()[0])
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('count', ?)", (str(total),))
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('built_at', ?)",
                     (time.strftime("%Y-%m-%d %H:%M:%S"),))
        conn.commit()
        conn.close()
        _DB.update(conn=None)
        _HIT.clear()
        _MISS.clear()
        _SYNC["msg"] = stop_msg or ("检查 %d 条，新增 %d 条，词库现有 %d 条"
                                    % (checked, added, total))
        _info("梗百科同步完成：%s", _SYNC["msg"])
    except Exception as e:  # noqa: BLE001
        _SYNC["msg"] = "同步出错：%s" % e
        _warn("梗百科同步出错: %s", e, exc_info=True)
    finally:
        _SYNC["running"] = False
        _SYNC["at"] = time.time()
        # 这一轮如果是「每天自动」触发的，把结果记回 auto.json，抽屉里能看到「上次自动跑的结果」
        try:
            p = STATE / "memewiki" / "auto.json"
            if p.exists():
                st = _read_json(p, {}) or {}
                st.update(msg=_SYNC.get("msg") or "", added=int(_SYNC.get("added") or 0),
                          done_at=time.time())
                _write_json(p, st)
        except Exception:  # noqa: BLE001
            pass


# ── 每天自动更新（2026-10-26 晚） ────────────────────────────────────────
# 主人问「梗百科会每天自动更新吗」—— 原来只有手动按钮，这里补上定时。
# 不自建线程：挂在 bot.py 已有的 scheduler_loop 里（15 秒一轮），
# 判断「到点了 && 今天还没跑过」才启动，代价就是读一个小 json。

def _auto_state():
    return _read_json(STATE / "memewiki" / "auto.json", {}) or {}


def auto_status():
    c = _cfg()
    try:
        hour = int(c.get("autoSyncHour") if c.get("autoSyncHour") is not None else 4)
    except (TypeError, ValueError):
        hour = 4
    st = _auto_state()
    return {"enabled": bool(c.get("autoSync", True)), "hour": max(0, min(hour, 23)),
            "date": str(st.get("date") or ""), "msg": str(st.get("msg") or ""),
            "added": int(st.get("added") or 0), "at": float(st.get("done_at") or 0.0)}


def auto_tick(now=None):
    """到点就跑一次增量同步。返回 True 表示这一轮真的启动了。

    ⚠️ **先记账再跑**：同步中途挂了、或者站点挂了，当天也不再重试 ——
    否则站点一整天不可用，这里每 15 秒就试一次，等于 DDoS 人家。
    """
    c = _cfg()
    if not bool(c.get("autoSync", True)):
        return False
    try:
        hour = int(c.get("autoSyncHour") if c.get("autoSyncHour") is not None else 4)
    except (TypeError, ValueError):
        hour = 4
    lt = time.localtime(now if now is not None else time.time())
    if lt.tm_hour != max(0, min(hour, 23)):
        return False
    today = time.strftime("%Y-%m-%d", lt)
    if str(_auto_state().get("date") or "") == today:
        return False
    if _SYNC.get("running"):
        return False
    if not db_path().exists():
        return False
    _write_json(STATE / "memewiki" / "auto.json",
                {"date": today, "at": time.time(), "msg": "每天自动同步：已启动", "added": 0})
    sync_start()
    _info("梗百科每天自动同步：%s %02d:00 开始（只补新增）", today, hour)
    return True
