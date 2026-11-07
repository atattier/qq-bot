# -*- coding: utf-8 -*-
"""qbot_skill_tools —— 技能包工具的原生实现（Python）

技能包里的 `index.js` 是 Node 模块，本机 bot 是纯 Python，**不执行它**。所以每个技能
「能干什么」在这里用 Python 重写一遍，再由 bot.py 的 `/api/skill` 暴露给 MCP，
最终变成 agent 大脑能调的 `skill__*` 工具。

已实现：
  trendy-memes（网络流行梗）
    · lookup  查梗（本地底稿 kb/memes.json + 自学习 user-added.json）
    · catalog 梗库目录
    · teach   收录新梗（写 user-added.json）
  whale-memes（梗鲸表情包）
    · send    按语气挑一张、下载验真图、发到当前会话
    · list    只列候选（带编号，供「点一张」用）
  fibi-memes（菲比表情包，2026-10-26）
    · send    从本机 images/emoji 里挑一张菲比动图直接发（不联网，只管体积/动图）
    · list    只列候选（编号固定，pick 才对得上）

数据落在 `state/skills/<id>/`（技能目录保持只读，和原包的设计一致）：
    trendy-memes/user-added.json     自学习库
    whale-memes/catalog.json         图库索引缓存（约 1.2MB，离线可用）
    whale-memes/state.json           本会话发过什么（去重 + 额度）
    whale-memes/tmp/                 下载的临时图（48h 自动清）
    fibi-memes/state.json            本会话发过哪几张（去重 + 额度）

⚠️ 和其余 qbot_* 模块一样：**不 import bot**，发图/写盘靠 bind() 注入。
"""
import json
import hashlib
import logging
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger("qqbot.skilltools")

CFG = {}
STATE = None
BASE = Path(__file__).resolve().parent
_SEND_IMAGE = None          # (cid, path, caption) -> bool
SELF_QQ = ""                # bot 自己的 QQ 号（bot 登录成功后回填），捏脸的「另一方」用它

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SITE = "https://aigengtu.com"
INDEX_URL = SITE + "/gallery-index.json"

MAX_BYTES = 12 * 1024 * 1024
TMP_MAX_AGE = 48 * 3600
MAX_TMP_FILES = 80

# 情绪分组：中文没法靠分词命中（「生气」vs 图上的「气鼓鼓」），按组做同义扩展。
MOOD_GROUPS = {
    "开心": ["开心", "高兴", "笑", "嘿嘿", "哈哈", "乐", "眉开眼笑", "棒", "赞"],
    "得意": ["得意", "太会", "会了", "简单", "小意思", "轻松", "拿捏", "厉害", "牛"],
    "委屈": ["委屈", "哭", "泪", "呜呜", "呜哇", "可怜", "难受", "惨", "悲"],
    "生气": ["气", "怒", "恼", "炸", "暴躁", "不爽", "生气", "翻脸"],
    "无语": ["无语", "无奈", "摊手", "算了", "服了", "麻了", "裂开", "扶额", "离谱", "吐槽"],
    "疑惑": ["疑惑", "奇怪", "问号", "不懂", "懵", "迷惑", "啥啊", "不解"],
    "敷衍": ["敷衍", "不关我事", "随便", "行吧", "懒得"],
    "求饶": ["求饶", "放过", "饶", "救命", "救救", "别打", "对不起", "错了"],
    "催促": ["催", "快点", "搞快", "速度", "赶紧", "急"],
    "害羞": ["害羞", "脸红", "不好意思"],
    "夸奖": ["夸", "厉害", "好看", "可爱", "谢谢", "牛啊"],
    "摸鱼": ["摸鱼", "躺平", "摆烂", "累", "困", "睡", "下班", "白饭"],
    "比心": ["比心", "喜欢", "爱你", "抱", "亲", "贴贴"],
    "财迷": ["钱", "工资", "便宜", "穷", "氪", "充钱", "氪金"],
    "吃": ["吃", "饿", "饭", "干饭", "吃了"],
}

# 口语补词（2026-10-19）：原词表是给对方客服写的短词，中文聊天里多是「气死/笑死」这种两字组合；
# 补在末尾，不改动原表 —— 单向追加，vendor 更新时照样能对齐。
# 实测（拿 bot.log 里 334 条真人消息量）：只用原表命中 2%，补完 15% 上下，
# 这才是「自觉配图」能感觉到的量级（再高就变刷屏）。
_EXTRA_WORDS = {
    "生气": ["气死", "火大", "气人", "生气了", "有病", "烦死", "别惹", "惯的", "骂人",
             "骂我", "凶什么", "欠揍", "滚蛋"],
    "开心": ["笑死", "好笑", "笑疯", "乐死", "开心死", "哈哈哈", "哈哈", "嘿嘿", "好玩",
             "好玩儿", "有趣", "乐呵", "绷不住", "笑不活"],
    "无语": ["醉了", "无语了", "服了", "真的服", "我去", "疯了", "玩我呢", "搞什么",
             "什么鬼", "绝了", "离谱", "麻了", "崩了", "魔了", "离谱了", "白给",
             "算了吧", "拉倒", "没救了"],
    "委屈": ["想哭", "难受了", "委屈了", "呜呜", "呜哇", "冤枉", "凭什么", "欺负",
             "好惨", "自闭"],
    "摸鱼": ["累死", "困死", "摸鱼了", "好累", "好困", "划水", "躺一下", "不想动",
             "歇会儿", "打工人", "加班"],
    "疑惑": ["啥啊", "咋回事", "什么情况", "什么意思", "问我呢", "看不懂", "这是啥",
             "真假的", "没听懂", "什么意思啊"],
    "夸奖": ["好看", "好可爱", "真可爱", "厉害啊", "牛啊", "真香", "可以啊", "好棒",
             "太强了", "好帅", "好萌"],
    "催促": ["快点", "赶紧", "搞快", "速度点", "急死", "等到花儿", "还没好", "怎么还没"],
    "财迷": ["花钱", "好贵", "太贵", "吃土", "没钱", "氪金", "打折", "白嫖"],
    "吃": ["干饭", "好饿", "吃啥", "宵夜", "馋了", "外卖"],
}
for _k, _ws in _EXTRA_WORDS.items():
    MOOD_GROUPS.setdefault(_k, []).extend(_ws)

# 主题图（插画/壁纸那种）当表情包不合适，压分
_TOPIC_RE = re.compile(r"插画|壁纸|头像|立绘|换装|涂鸦|九宫格")
_CAPTION_RE = re.compile(r"『([^』]{1,40})』")
_MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"),
          (b"GIF8", "gif"), (b"RIFF", "webp"))


def bind(cfg, state_dir=None, base=None, send_image_file=None, logger=None):
    global CFG, STATE, BASE, _SEND_IMAGE
    CFG = cfg
    if state_dir:
        STATE = Path(state_dir)
    if base:
        BASE = Path(base)
    if send_image_file:
        _SEND_IMAGE = send_image_file
    if logger:
        globals()["log"] = logger


def guess_keyword(text):
    """从一句话里猜情绪关键词（拿 `MOOD_GROUPS` 的词表），猜不到返回 ""（= 让图库随机/按默认挑）。

    取**命中最长的那个词**所属分组，避免单字「气」把「生气」的语义盖掉。
    ⚠️ 单字词一律不认（「天气」里的「气」、「吃了」里的「吃」误伤太多）。
    """
    t = str(text or "")
    hit = None
    for name, words in MOOD_GROUPS.items():
        for w in words:
            if len(w) >= 2 and w in t and (hit is None or len(w) > hit[1]):
                hit = (name, len(w))
    return hit[0] if hit else ""


def _state_root():
    root = (STATE or (BASE / "state")) / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sdir(sid):
    d = _state_root() / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _settings(sid):
    try:
        import qbot_skills
        return qbot_skills.settings_of(sid)
    except Exception:  # noqa: BLE001
        return {}


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
        log.warning("写 %s 失败: %s", p, e)
        return False


# ── 通用 ────────────────────────────────────────────────────────────────

def _opener():
    # 本地/国内站点直连，绕开 Clash（没开代理时裸 urlopen 会 10061）
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url, timeout=20, accept="application/json,*/*"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with _opener().open(req, timeout=timeout) as r:
        return r.status, r.read()


def _skill_file(sid, *parts):
    try:
        import qbot_skills
        sk = qbot_skills.get(sid)
        if sk:
            return Path(sk["path"]).joinpath(*parts)
    except Exception:  # noqa: BLE001
        pass
    return BASE / "skills" / sid / Path(*parts)


# ══════════════════════════════════════════════════════════════ trendy-memes
_TM_CACHE = {"t": 0.0, "entries": []}


def tm_entries(force=False):
    """底稿 + 自学习库合成一份。底稿 38 条，自学习库随用随长。"""
    now = time.time()
    if not force and _TM_CACHE["entries"] and now - _TM_CACHE["t"] < 60:
        return _TM_CACHE["entries"]
    out = []
    kb = _read_json(_skill_file("trendy-memes", "kb", "memes.json"), {}) or {}
    for e in (kb.get("entries") or []):
        if isinstance(e, dict) and e.get("title"):
            out.append(dict(e, _src="kb"))
    user = _read_json(_sdir("trendy-memes") / "user-added.json", []) or []
    if isinstance(user, dict):
        user = user.get("entries") or []
    for e in user:
        if isinstance(e, dict) and e.get("title"):
            out.append(dict(e, _src="user"))
    _TM_CACHE.update(t=now, entries=out)
    return out


def tm_lookup(term, limit=None):
    """查一个梗。返回 (ok, text)。先精确/别名，再模糊。"""
    term = str(term or "").strip()
    if not term:
        return False, "要查哪个梗？给我个词。"
    lim = int(limit or _settings("trendy-memes").get("maxResults") or 3)
    lim = max(1, min(lim, 5))
    low = term.lower()
    hits, weak = [], []
    for e in tm_entries():
        title = str(e.get("title") or "")
        aliases = [str(a) for a in (e.get("aliases") or [])]
        if low == title.lower() or any(low == a.lower() for a in aliases):
            hits.append(e)
            continue
        bag = " ".join([title] + aliases + [str(e.get("meaning") or ""),
                                           str(e.get("usage") or ""),
                                           " ".join(e.get("tags") or [])]).lower()
        if low in bag:
            weak.append(e)
    picked = (hits or weak)[:lim]
    if not picked:
        return True, ("梗库里没有「%s」（现有 %d 条）。可以联网查一下再决定怎么接，"
                      "查明白后记得收录。" % (term, len(tm_entries())))
    lines = []
    for e in picked:
        line = "「%s」= %s" % (e.get("title"), e.get("meaning") or "")
        if e.get("usage"):
            line += "\n  怎么用：" + str(e["usage"])
        meta = "　".join(x for x in [str(e.get("tone") or ""), str(e.get("era") or ""),
                                     str(e.get("freshness") or "")] if x)
        if meta:
            line += "\n  " + meta
        if e.get("_src") == "user":
            line += "\n  （自学习库，之前查明白记下的）"
        lines.append(line)
    head = "" if hits else "没找到完全对应的，下面是最接近的：\n"
    return True, head + "\n".join(lines)


def tm_catalog(limit=None):
    lim = int(limit or _settings("trendy-memes").get("indexLimit") or 30)
    lim = max(1, min(lim, 200))
    es = tm_entries()
    titles = [str(e.get("title")) for e in es]
    n_user = len([e for e in es if e.get("_src") == "user"])
    out = "梗库 %d 条（自学习 %d 条）：%s" % (len(es), n_user, "、".join(titles[:lim]))
    if len(titles) > lim:
        out += "……（还有 %d 条，要查具体的直接问）" % (len(titles) - lim)
    return True, out


def tm_teach(term, meaning, usage="", aliases=None):
    """把查明白的新梗记进自学习库（不是替代底稿，底稿随技能包更新）。"""
    if not _settings("trendy-memes").get("allowTeach", True):
        return False, "这个技能关掉了「允许收录」，梗库只能手改底稿。"
    term = str(term or "").strip()
    meaning = str(meaning or "").strip()
    if not term or not meaning:
        return False, "收录至少要给「梗名」和「什么意思」。"
    f = _sdir("trendy-memes") / "user-added.json"
    data = _read_json(f, [])
    if isinstance(data, dict):
        data = data.get("entries") or []
    for e in data:
        if str(e.get("title") or "") == term:
            e.update(meaning=meaning, usage=usage or e.get("usage") or "",
                     ts=int(time.time()))
            _write_json(f, data)
            _TM_CACHE["entries"] = []
            return True, "「%s」之前记过，这次把内容更新了。" % term
    if len(data) >= 300:
        return False, "自学习库满了（300 条），先删几条旧的再加。"
    data.append({"title": term, "aliases": list(aliases or []), "meaning": meaning,
                 "usage": usage or "", "tags": [], "freshness": "自学习",
                 "ts": int(time.time())})
    _write_json(f, data)
    _TM_CACHE["entries"] = []
    log.info("梗库收录: %s", term)
    return True, "记下了：「%s」= %s" % (term, meaning)


# ══════════════════════════════════════════════════════════════ whale-memes
_WCACHE = {"at": 0.0, "items": [], "cats": [], "src": ""}


def _whale_ttl():
    h = _settings("whale-memes").get("refreshHours", 12)
    try:
        h = float(h)
    except (TypeError, ValueError):
        h = 12
    return h * 3600 if h > 0 else float("inf")


def whale_catalog(force=False):
    """内存 → 网络 → 磁盘缓存，逐级降级（离线也能发）。"""
    f = _sdir("whale-memes") / "catalog.json"
    if not force and _WCACHE["items"] and time.time() - _WCACHE["at"] < _whale_ttl():
        return _WCACHE
    try:
        st, body = _get(INDEX_URL, timeout=20)
        data = json.loads(body.decode("utf-8"))
        items, cats = [], []
        for key, node in ((data or {}).get("gallery") or {}).items():
            images = (node or {}).get("images") or []
            if not images:
                continue
            cname = str((node or {}).get("name") or key)[:24]
            cats.append((cname, len(images)))
            for im in images:
                url = str((im or {}).get("original") or "").strip()
                if not url.startswith("http"):
                    continue
                stem = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if not stem:
                    continue
                name = str((im or {}).get("name") or "")[:80]
                m = _CAPTION_RE.search(name)
                items.append({"id": stem, "name": name,
                              "caption": (m.group(1) if m else ""),
                              "character": cname, "url": url,
                              "preview": str((im or {}).get("preview") or ""),
                              "story": str(((im or {}).get("story") or {}).get("zh") or "")[:220]})
        if not items:
            raise ValueError("索引里没有可用条目")
        _WCACHE.update(at=time.time(), items=items, cats=cats, src="net")
        _write_json(f, {"at": _WCACHE["at"], "items": items,
                        "categories": [{"name": n, "count": c} for n, c in cats]})
        log.info("梗鲸图库已刷新：%d 张 / %d 个角色", len(items), len(cats))
        return _WCACHE
    except Exception as e:  # noqa: BLE001
        disk = _read_json(f, None)
        if disk and disk.get("items"):
            _WCACHE.update(at=time.time(), items=disk["items"], src="disk",
                           cats=[(c["name"], c["count"]) for c in disk.get("categories") or []])
            log.warning("梗鲸图库刷新失败（%s），用本地缓存 %d 张", e, len(disk["items"]))
            return _WCACHE
        log.warning("梗鲸图库拿不到，也没缓存: %s", e)
        return {"at": 0, "items": [], "cats": [], "src": "none"}


def _expand(term):
    kw = str(term or "").strip().lower()
    if not kw:
        return []
    out = {kw}
    for group in MOOD_GROUPS.values():
        if any(kw in t.lower() or t.lower() in kw for t in group):
            out.update(t.lower() for t in group)
    return list(out)


def _score(item, terms):
    if not terms:
        return 0
    cap = (item.get("caption") or "").lower()
    name = (item.get("name") or "").lower()
    story = (item.get("story") or "").lower()
    s = 0
    for t in terms:
        if not t:
            continue
        if cap == t:
            s += 120
        elif cap and t in cap:
            s += 60
        if t in name:
            s += 18
        if t in story:
            s += 6
    if cap:
        s += 3
    if _TOPIC_RE.search(item.get("name") or ""):
        s -= 8
    return s


def _wstate():
    return _read_json(_sdir("whale-memes") / "state.json", {"sent": {}}) or {"sent": {}}


def _wsave(st):
    # 只留最近 500 条
    for k, v in list((st.get("sent") or {}).items()):
        st["sent"][k] = v[-60:]
    _write_json(_sdir("whale-memes") / "state.json", st)


def _sent_ids(cid):
    return {str(x.get("id")) for x in ((_wstate().get("sent") or {}).get(str(cid)) or [])}


def _num(conf, key, dflt):
    """取数字参数。⚠️ 不能用 `conf.get(k) or dflt` —— 用户把额度设成 0 是「一张都不许发」，
    用 or 会被当成没填、又放行（2026-10-19 自测踩到）。"""
    v = conf.get(key, None)
    if v is None or v == "":
        return dflt
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return dflt


def _quota_left(cid):
    conf = _settings("whale-memes")
    hist = ((_wstate().get("sent") or {}).get(str(cid)) or [])
    now = time.time()
    per_h = _num(conf, "maxPerHour", 6)
    per_d = _num(conf, "maxPerDay", 30)
    h = len([x for x in hist if now - float(x.get("t") or 0) < 3600])
    d = len([x for x in hist if now - float(x.get("t") or 0) < 86400])
    return max(0, per_h - h), max(0, per_d - d)


def whale_candidates(keyword="", character="", want=1, cid=""):
    """返回 (候选列表, 说明)。按语气打分，同分随机，本会话发过的排后面。"""
    cat = whale_catalog()
    if not cat["items"]:
        return [], "图库这会儿连不上，本地也没有缓存，过一会儿再试。"
    conf = _settings("whale-memes")
    terms = _expand(keyword)
    pool = cat["items"]
    note = ""
    who = str(character or "").strip()
    if who and who not in ("auto", "任意", "全部"):
        sub = [it for it in pool if it["character"] == who]
        if sub:
            pool = sub
        else:
            note = "图库里没有「%s」这个角色，从全部图里挑。" % who
    elif not character:
        dflt = str(conf.get("character") or "auto")
        if dflt not in ("auto", "任意", "全部") and any(c[0] == dflt for c in cat["cats"]):
            sub = [it for it in pool if it["character"] == dflt]
            if sub:
                pool = sub
    scored = [(it, _score(it, terms)) for it in pool]
    scored = [x for x in scored if x[1] > 0]
    if scored:
        scored.sort(key=lambda x: -x[1])
        top = [x for x in scored if x[1] >= scored[0][1] * 0.75][:60]
        random.shuffle(top)
        ordered = [x[0] for x in top]
    else:
        ordered = list(pool)
        random.shuffle(ordered)
        if terms:
            note = note or "图库里没有直接贴「%s」的梗，从随机图里挑。" % str(keyword)[:20]
    if conf.get("dedupe", True) and cid:
        mine = _sent_ids(cid)
        ordered = [x for x in ordered if str(x["id"]) not in mine] or ordered
    pin_ids = [str(x) for x in (pins_of("whale-memes").get(str(keyword or "").strip()) or [])]
    if pin_ids:
        want_set = set(pin_ids)
        by_id = {str(it["id"]): it for it in cat["items"]}
        pinned = [by_id[i] for i in pin_ids if i in by_id]
        if pinned:
            ordered = pinned + [x for x in ordered if str(x["id"]) not in want_set]
    n = max(1, min(int(want or 1), 24))
    return ordered[:max(n, 20)], note


def whale_list(keyword="", character="", limit=10, cid=""):
    cand, note = whale_candidates(keyword, character, want=limit, cid=cid)
    if not cand:
        return False, note or "没挑到图。"
    lines = []
    for i, it in enumerate(cand[:max(1, min(int(limit or 10), 20))], 1):
        cap = it.get("caption") or it.get("name") or it["id"]
        lines.append("%d. [%s] %s" % (i, it["character"], cap))
    head = "从梗鲸挑了 %d 张候选（发的时候把编号给我就行）：" % len(lines)
    return True, head + "\n" + "\n".join(lines) + (("\n" + note) if note else "")


def _verify_image(path):
    """认魔数 + 量大小：这个图床图没了会返回 404+HTML，不验就会发裂图。"""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    for magic, _ext in _MAGIC:
        if head.startswith(magic):
            return True
    return False


def _to_jpg(path):
    """转 jpg（quality 90）。失败返回 None，调用方原样发。"""
    try:
        from PIL import Image
        out = Path(path).with_suffix(".jpg")
        with Image.open(path) as im:
            im.convert("RGB").save(out, "JPEG", quality=90)
        return out if _verify_image(out) else None
    except Exception as e:  # noqa: BLE001
        log.debug("转 jpg 失败（原图照发）: %s", e)
        return None


def _emit_path(path):
    """按**真实内容**定后缀，顺手把「不适合直接上 QQ 的」转小一点。

    · 实测图床会把 PNG 内容挂在 `.webp` 名字上 —— NapCat 上传认内容不认后缀，
      但后缀错了排查时极容易误判「发了张坏图」；
    · webp：QQ 旧客户端支持不稳（会显示成文件），转 jpg；
    · 大于 1.2MB 的 PNG：梗图不值这个流量，转 jpg（实测 1.9MB → 90KB）。
    PIL 不在就原样发，不因为压缩失败把图丢了。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(16)
        if head[:4] == b"RIFF":
            return _to_jpg(path) or path
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            if Path(path).stat().st_size > 1_200_000:
                jpg = _to_jpg(path)
                if jpg:
                    return jpg
            want = ".png"
        elif head.startswith(b"\xff\xd8\xff"):
            want = ".jpg"
        elif head.startswith(b"GIF8"):
            want = ".gif"
        else:
            return path
        if Path(path).suffix.lower() == want:
            return path
        new = Path(path).with_suffix(want)
        Path(path).replace(new)
        return new
    except Exception as e:  # noqa: BLE001
        log.debug("定后缀失败（原图照发）: %s", e)
        return path


def _clean_tmp(tmp):
    now = time.time()
    files = sorted(tmp.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for i, p in enumerate(files):
        try:
            if i >= MAX_TMP_FILES or now - p.stat().st_mtime > TMP_MAX_AGE:
                p.unlink()
        except OSError:
            pass


def whale_send(cid, keyword="", character="", pick=0, count=1, caption=""):
    """挑图 → 下载验真 → 发出去。pick 是 whale_list 里的编号（从 1 开始）。"""
    if _SEND_IMAGE is None:
        return False, "发送通道没接上（bind 时没给 send_image_file）。"
    if not cid:
        # 没有会话就没有额度可算、也没处发 —— 别静默挑图下图的（自测时踩到会白跑一趟网络）
        return False, "要发到哪个会话？没给 cid（群号或私聊 key）。"
    conf = _settings("whale-memes")
    if not conf.get("proactive", True) and not keyword and not pick:
        return False, "「允许主动配图」是关的，只有明确要图时才发。"
    left_h, left_d = _quota_left(cid)
    if left_h <= 0 or left_d <= 0:
        return False, "这个会话的配图额度用完了（每小时 %s / 每天 %s），待会儿再发。" % (
            conf.get("maxPerHour", 6), conf.get("maxPerDay", 30))
    cand, note = whale_candidates(keyword, character,
                                 want=max(1, min(int(count or 1), 3)), cid=cid)
    if not cand:
        return False, note or "没挑到图。"
    if pick:
        try:
            k = int(pick)
        except (TypeError, ValueError):
            k = 0
        if 1 <= k <= len(cand):
            cand = [cand[k - 1]] + [c for c in cand if c is not cand[k - 1]]
    tmp = _sdir("whale-memes") / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    _clean_tmp(tmp)
    want_n = max(1, min(int(count or 1), 3, left_h, left_d))
    sent, tried = [], 0
    for it in cand:
        if len(sent) >= want_n or tried >= 6:
            break
        tried += 1
        url = it["url"] if conf.get("preferOriginal", True) else (it.get("preview") or it["url"])
        path = tmp / ("%s.webp" % it["id"])
        # ⚠️ 先落 .part 验真、成了再改名：直接写最终文件名的话，这张下载失败会把
        #    上一次已经验过的好图覆盖掉、再被 unlink，tmp 里的好图凭空消失（自测踩到）。
        part = tmp / ("%s.part" % it["id"])
        try:
            st, body = _get(url, timeout=15, accept="image/*,*/*")
            if len(body) > MAX_BYTES:
                continue
            part.write_bytes(body)
            if not _verify_image(part):
                part.unlink(missing_ok=True)
                if conf.get("preferOriginal", True) and it.get("preview"):
                    st, body = _get(it["preview"], timeout=15, accept="image/*,*/*")
                    part.write_bytes(body)
                    if not _verify_image(part):
                        part.unlink(missing_ok=True)
                        continue
                else:
                    continue
            part.replace(path)
            path = _emit_path(path)
        except Exception as e:  # noqa: BLE001
            log.warning("梗鲸下载失败 %s: %s", it["id"], e)
            part.unlink(missing_ok=True)
            continue
        ok = bool(_SEND_IMAGE(cid, str(path), caption if not sent else ""))
        if ok:
            sent.append(it)
            st0 = _wstate()
            st0.setdefault("sent", {}).setdefault(str(cid), []).append(
                {"id": it["id"], "t": time.time()})
            _wsave(st0)
    if not sent:
        return False, "图都下载不下来（图床这会儿可能不稳），换一张或者等会儿再试。"
    desc = "、".join((x.get("caption") or x.get("name") or x["id"])[:24] for x in sent)
    return True, "发出去 %d 张：%s%s" % (len(sent), desc, ("　" + note) if note else "")


# ══════════════════════════════════════════════════════════════ petpet
# 捏脸表情包（2026-10-23）：把某个人的头像做成「摸摸头 / 揉一揉 / 咬一口」动图。
# 渲染在 qbot_petpet.py（移植自 nonebot-plugin-petpet），这里只管额度、模板解析、发图。

def _pp_state():
    st = _read_json(_sdir("petpet") / "state.json", {})
    if not isinstance(st, dict):
        st = {}
    st.setdefault("sent", {})
    return st


def _pp_save(st):
    """⚠️ 不能复用 _wsave —— 那个把路径写死成 whale-memes/state.json（我第一版踩过：
    额度永远记到梗鲸的账上，冷却形同虚设）。"""
    for k, v in list((st.get("sent") or {}).items()):
        st["sent"][k] = v[-60:]
    _write_json(_sdir("petpet") / "state.json", st)


def _pp_quota(cid, conf):
    """返回 (能不能发, 说明)。按会话算每小时张数 + 冷却秒数。"""
    now = time.time()
    st = _pp_state()
    hist = [t for t in (st["sent"].get(str(cid)) or []) if now - t < 3600]
    per_hour = _num(conf, "maxPerHour", 20)
    cool = _num(conf, "cooldown", 6)
    if per_hour > 0 and len(hist) >= per_hour:
        return False, "这个会话一小时的捏脸额度（%d 张）用完了，过会儿再来。" % per_hour
    if cool > 0 and hist and now - max(hist) < cool:
        return False, "刚做过一张，%d 秒后再来。" % int(cool - (now - max(hist)) + 1)
    return True, ""


def _pp_mark(cid):
    now = time.time()
    st = _pp_state()
    hist = [t for t in (st["sent"].get(str(cid)) or []) if now - t < 3600]
    hist.append(now)
    st["sent"][str(cid)] = hist[-60:]
    _pp_save(st)


def _pp_template(name, conf):
    """把用户/模型给的说法解析成模板 id。空值 = 用默认模板。"""
    try:
        import qbot_petpet as pp
    except Exception as e:  # noqa: BLE001
        return None, "捏脸模块没起来：%s" % e
    raw = str(name or "").strip()
    if not raw:
        raw = str(conf.get("defaultTemplate") or "petpet")
    if raw in pp.TEMPLATES:
        return raw, ""
    tid, _ = pp.resolve(raw)
    if tid:
        return tid, ""
    return None, "没有「%s」这个动作。能做：%s" % (raw, "、".join(
        "%s=%s" % (x["id"], x["name"]) for x in pp.list_templates()))


def petpet_send(cid, template="", qq="", caption="", sender_qq="", image_path=""):
    """把 qq 这个人的头像做成动图发到 cid 会话。"""
    if _SEND_IMAGE is None:
        return False, "发图通道没接上（bot 没 bind send_image_file）。"
    conf = _settings("petpet")
    if not conf.get("enabled", True):
        return False, "捏脸表情包现在是关的。"
    try:
        import qbot_skills
        if not qbot_skills.is_on("petpet"):
            return False, "捏脸表情包现在是关的。"
    except Exception:  # noqa: BLE001
        pass
    qq = str(qq or "").strip()
    if not qq:
        return False, "得给个 QQ 号（做谁的头像）。"
    tid, why = _pp_template(template, conf)
    if not tid:
        return False, why
    ok, why = _pp_quota(cid, conf)
    if not ok:
        return False, why
    try:
        import qbot_petpet as pp
        path, name = pp.make_for(tid, qq, sender_qq=sender_qq or SELF_QQ or qq,
                                 image_path=image_path or None)
    except FileNotFoundError as e:
        log.warning("petpet 素材缺失: %s", e)
        return False, "这个模板的素材还没下载（%s），换个动作或者让主人补素材。" % tid
    except Exception as e:  # noqa: BLE001
        log.warning("petpet 出图失败 template=%s qq=%s: %s", tid, qq, e, exc_info=True)
        return False, "出图失败：%s" % e
    if not _SEND_IMAGE(cid, str(path), caption or ""):
        return False, "图做好了但没发出去（发送闸门拦了或协议端异常）。"
    _pp_mark(cid)
    return True, "已经把 %s 的头像做成「%s」发出去了。" % (qq, name)


def petpet_list():
    try:
        import qbot_petpet as pp
        return True, pp.list_text()
    except Exception as e:  # noqa: BLE001
        return False, "捏脸模块没起来：%s" % e


# ══════════════════════════════════════════════════════════════ fibi-memes
# 本地表情包（2026-10-26）：主人 images/emoji 里那套「菲比啾比」动图（383 张，204 gif）。
# 和 whale-memes 的差别：**不联网、不下图**，直接发本机文件 —— 所以没有下载/验真那套，
# 但要多管两件本地素材才有的麻烦：
#   1. 体积：库里有 16MB 的 gif，QQ 传上去大概率失败，还会把整条消息卡住；
#   2. 动图：webp 一律转 jpg 会把动画转没（`_emit_path` 就是那样），所以这里自己判断帧数。
_FIBI_HINT = {
    "开心": ["开心", "傻笑", "大笑", "嘿嘿", "哈哈", "雀跃", "蹦跶", "激动", "满足", "糖笑",
             "樱笑", "比YA", "点赞", "可爱"],
    "得意": ["得意", "装比", "正宫", "赏", "队长", "主教", "宗", "超级", "豪"],
    "生气": ["生气", "愤怒", "暴怒", "红温", "气鼓鼓", "气晕", "炸", "恶霸", "敲打", "捶打",
             "打菲比头", "乱拳", "疯拳", "中指", "镇压", "枪指", "刀"],
    "委屈": ["可怜", "哭", "失望", "战败", "没用", "失去了一切", "急哭", "恳求", "道歉",
             "投降", "磕头", "补药欺负"],
    "无语": ["无奈", "摊手", "无语", "嫌弃", "看扁", "白眼", "黑脸", "生无可恋", "辣眼睛",
             "邪惡", "嫌弃"],
    "疑惑": ["疑问", "问号", "思考", "挠头", "呆", "懵", "装傻", "啥子", "伪人", "吃惊", "震惊"],
    "摸鱼": ["摆烂", "躺躺", "懒床", "睡觉", "昏睡", "失眠", "熬夜", "看手机", "坐牢", "洗澡",
             "趴趴", "被窝"],
    "吃": ["干饭", "吃", "炫饭", "炫面", "贪吃", "嗦面", "流口水", "大胃袋", "煎饼", "炒粉",
           "汉堡", "面包", "火锅", "披萨", "麻辣烫", "泔水", "投喂", "大福", "水果", "瓜",
           "喝水", "调酒", "干杯", "kfc", "M记", "麦当劳", "瑞幸", "瑞"],
    "比心": ["比心", "爱你", "抱抱", "抱", "舔", "亲", "送花", "贴", "揉脸", "捏脸", "摸摸",
             "嗅嗅", "咬耳朵"],
    "催促": ["急", "快", "催", "转圈", "转铃", "加速"],
    "夸奖": ["点赞", "送花", "敬礼", "比YA", "花手"],
    "财迷": ["要钱", "财神", "纳斯达克", "vivo50", "支付", "token", "赌狗", "钱"],
    "跳舞": ["跳舞", "扭", "摇", "舞", "节拍", "摇滚", "回旋", "新宝岛", "蜘蛛", "鳄鱼",
             "点头", "花手", "转圈"],
    "打招呼": ["来啦", "探头", "看", "推荐", "放只", "邮寄", "证件照", "拍照", "自拍"],
}
_FIBI_CACHE = {"key": None, "items": []}


def _fibi_conf():
    return _settings("fibi-memes")


def _fibi_dir(conf):
    raw = str(conf.get("folder") or "images/emoji").strip()
    p = Path(raw)
    return p if p.is_absolute() else (BASE / p)


def _fibi_index(force=False):
    """扫素材目录 → 可用文件列表。按 (目录 mtime, 匹配字, 体积上限) 缓存，改参数立刻生效。"""
    conf = _fibi_conf()
    root = _fibi_dir(conf)
    match = str(conf.get("match") or "").strip()
    try:
        max_bytes = int(float(conf.get("maxMB") or 4) * 1024 * 1024)
    except (TypeError, ValueError):
        max_bytes = 4 * 1024 * 1024
    if max_bytes <= 0:
        max_bytes = 4 * 1024 * 1024
    try:
        cp = root / "_collected.json"
        mp = root / "_manifest.json"
        stamp = (str(root), root.stat().st_mtime, match, max_bytes,
                 cp.stat().st_mtime if cp.exists() else 0,
                 mp.stat().st_mtime if mp.exists() else 0)
    except OSError:
        stamp = (str(root), 0, match, max_bytes, 0, 0)
    if not force and _FIBI_CACHE["key"] == stamp:
        return _FIBI_CACHE["items"]
    collected = _fibi_collected(root)
    items = []
    try:
        for p in root.iterdir():
            if not p.is_file() or p.suffix.lower() not in (".gif", ".webp", ".jpg", ".jpeg", ".png"):
                continue
            # 群里收进来的图名字多半没有「菲比」，按 match 过滤等于白收 —— 单独记账放行
            if match and match not in p.stem and p.name not in collected:
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > max_bytes or size < 2048:
                continue          # 太大 QQ 传不动；太小的多半是坏图/占位图
            ext = p.suffix.lower()
            # id 用**完整文件名**：图库里有同名的 `可爱菲比.gif` / `可爱菲比.webp`，
            # 只取 stem 会让两张图共用一个 id（挑图面板点一个选中俩、去重也当成一张）。
            items.append({"id": p.name, "path": str(p), "stem": p.stem, "size": size,
                          "ext": ext, "animated": ext in (".gif", ".webp")})
    except OSError as e:
        log.warning("菲比图库读不了（%s）: %s", root, e)
        items = []
    items.sort(key=lambda x: x["stem"])
    _FIBI_CACHE.update(key=stamp, items=items)
    if items:
        log.info("菲比图库：%d 张可用（%s）", len(items), root)
    return items


def _fibi_terms(keyword):
    kw = str(keyword or "").strip().lower()
    if not kw:
        return []
    out = {kw}
    for name, words in _FIBI_HINT.items():
        if kw == name.lower() or kw in words or any(w in kw for w in words if len(w) >= 2):
            out.update(w.lower() for w in words)
    return list(out)


def _fibi_state():
    st = _read_json(_sdir("fibi-memes") / "state.json", {})
    if not isinstance(st, dict):
        st = {}
    st.setdefault("sent", {})
    return st


def _fibi_save(st):
    for k, v in list((st.get("sent") or {}).items()):
        st["sent"][k] = v[-80:]
    _write_json(_sdir("fibi-memes") / "state.json", st)


def _fibi_quota(cid):
    conf = _fibi_conf()
    now = time.time()
    hist = ((_fibi_state().get("sent") or {}).get(str(cid)) or [])
    per_h = _num(conf, "maxPerHour", 8)
    per_d = _num(conf, "maxPerDay", 40)
    h = len([x for x in hist if now - float(x.get("t") or 0) < 3600])
    d = len([x for x in hist if now - float(x.get("t") or 0) < 86400])
    left_h = 999 if per_h <= 0 else max(0, per_h - h)
    left_d = 999 if per_d <= 0 else max(0, per_d - d)
    return left_h, left_d


def fibi_candidates(keyword="", want=1, cid="", shuffle=True):
    """返回 (候选列表, 说明)。先按名字命中打分，再按「动图优先」排；本会话发过的靠后。

    2026-10-26 第二轮：主人要「选定几张作为几个情绪的标准返图」——`pins` 里钉过的图
    **无条件排最前**（连「同会话不重发」也让路：主人点了名就是每次都想要这张）。
    """
    items = _fibi_index()
    conf = _fibi_conf()
    if not items:
        return [], ("图库里没找到可用素材（目录 %s，匹配「%s」）。检查一下文件夹，"
                    "或者把「文件名必须包含」放宽。" % (_fibi_dir(conf), conf.get("match") or ""))
    terms = _fibi_terms(keyword)
    note = ""
    scored = []
    for it in items:
        stem = it["stem"].lower()
        s = 0
        for t in terms:
            if t and t in stem:
                s += 40 if len(t) >= 2 else 10
        scored.append((it, s))
    top = max((s for _i, s in scored), default=0)
    if top > 0:
        keep = [x for x in scored if x[1] >= top * 0.6]
    else:
        keep = scored
        if terms:
            note = "库里没有名字贴「%s」的图，从别的里面挑。" % str(keyword)[:12]
    # 固定顺序（list / 网页挑图用）时按「贴切度」排，同分再按名字 —— 纯按名字排会把
    # 勉强沾边的排在真正贴切的前面，主人挑图时第一屏全是废图。
    if not shuffle:
        keep.sort(key=lambda x: (-x[1], x[0]["stem"]))
    pool = [x[0] for x in keep]
    if shuffle:
        random.shuffle(pool)
    if conf.get("animatedFirst", True):
        anim = [x for x in pool if x["animated"]]
        still = [x for x in pool if not x["animated"]]
        pool = anim + still
    if conf.get("dedupe", True) and cid:
        mine = {str(x.get("id")) for x in
                ((_fibi_state().get("sent") or {}).get(str(cid)) or [])}
        pool = [x for x in pool if x["id"] not in mine] or pool
    pin_ids = [str(x) for x in (pins_of("fibi-memes").get(str(keyword or "").strip()) or [])]
    if pin_ids:
        want_set = set(pin_ids)
        # 从**全库**找，不是从筛过的 pool 找：主人钉的可能就是一张名字不贴这个情绪的图
        # （「无语」用一张干饭图），那种图在 keep 里早被分数过滤掉了。
        by_id = {x["id"]: x for x in items}
        pinned = [by_id[i] for i in pin_ids if i in by_id]
        if pinned:
            pool = pinned + [x for x in pool if x["id"] not in want_set]
    n = max(1, min(int(want or 1), 24))
    return pool[:max(n, 20)], note


def fibi_list(keyword="", limit=10, cid=""):
    """列候选（编号固定：按名字排序，所以「pick 第 N 张」下一次调也还是同一张）。"""
    cand, note = fibi_candidates(keyword, want=limit, cid=cid, shuffle=False)
    if not cand:
        return False, note or "没挑到图。"
    lim = max(1, min(int(limit or 10), 20))
    lines = []
    for i, it in enumerate(cand[:lim], 1):
        tag = "动图" if it["animated"] else "静图"
        lines.append("%d. [%s %dKB] %s" % (i, tag, max(1, it["size"] // 1024), it["stem"]))
    head = "从菲比图库挑了 %d 张候选（发的时候把编号给我就行）：" % len(lines)
    return True, head + "\n" + "\n".join(lines) + (("\n" + note) if note else "")


def _fibi_animated(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            return int(getattr(im, "n_frames", 1) or 1) > 1
    except Exception:  # noqa: BLE001
        return True          # 判断不了就当动图，宁可原样发也别把动画转没


def _fibi_jpg(src, tmp):
    """静态图转 jpg，**落在缓存目录**。

    ⚠️ 不能像 `_emit_path` 那样 `with_suffix(".jpg")` 就地写 —— 那是主人的图库
    （575MB、只读素材），转换一次就多出一张派生图，还会把目录 mtime 改掉、
    让索引缓存失效（第一版就这么写的，自测时图库从 366 张一路涨到 369）。
    """
    try:
        from PIL import Image
        tmp.mkdir(parents=True, exist_ok=True)
        # 带一段 hash：同名的 .gif / .webp 转出来不能互相覆盖（不然发的图和挑的不是一张）
        tag = hashlib.md5(Path(src).name.encode("utf-8")).hexdigest()[:6]
        out = tmp / (Path(src).stem[:50] + "-" + tag + ".jpg")
        with Image.open(src) as im:
            im.convert("RGB").save(out, "JPEG", quality=90)
        return out if _verify_image(out) else None
    except Exception as e:  # noqa: BLE001
        log.debug("菲比图转 jpg 失败（原图照发）: %s", e)
        return None


def _fibi_prepare(path, conf):
    """定「到底发哪个文件」。动图原样发；静态 webp 可选转 jpg；不是图就返回 None。"""
    p = Path(path)
    try:
        with open(p, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    tmp = _sdir("fibi-memes") / "tmp"
    if head[:4] == b"RIFF":                       # webp
        if not conf.get("webpToJpg", True):
            return p
        if _fibi_animated(p):
            return p                              # 动图 webp：转了就没动画
        _clean_tmp(tmp)
        return _fibi_jpg(p, tmp) or p
    if head.startswith(b"GIF8"):
        return p
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        if p.stat().st_size > 1_200_000:
            _clean_tmp(tmp)
            return _fibi_jpg(p, tmp) or p
        return p
    if head.startswith(b"\xff\xd8\xff"):
        return p
    return None


def fibi_send(cid, keyword="", pick=0, count=1, caption=""):
    """从本机菲比图库挑一张发出去。pick 是 fibi_list 里的编号（从 1 开始）。"""
    if _SEND_IMAGE is None:
        return False, "发送通道没接上（bind 时没给 send_image_file）。"
    if not cid:
        return False, "要发到哪个会话？没给 cid（群号或私聊 key）。"
    conf = _fibi_conf()
    if not conf.get("enabled", True):
        return False, "菲比表情包现在是关的。"
    try:
        import qbot_skills
        if not qbot_skills.is_on("fibi-memes"):
            return False, "菲比表情包现在是关的。"
    except Exception:  # noqa: BLE001
        pass
    left_h, left_d = _fibi_quota(cid)
    if left_h <= 0 or left_d <= 0:
        return False, "这个会话的表情额度用完了（每小时 %s / 每天 %s），待会儿再发。" % (
            conf.get("maxPerHour", 8), conf.get("maxPerDay", 40))
    try:
        k = int(pick or 0)
    except (TypeError, ValueError):
        k = 0
    cand, note = fibi_candidates(keyword, want=max(1, min(int(count or 1), 3)),
                                 cid=cid, shuffle=not k)
    if not cand:
        return False, note or "没挑到图。"
    if k:
        if not (1 <= k <= len(cand)):
            return False, "没有第 %d 张（候选 %d 张）。" % (k, len(cand))
        cand = [cand[k - 1]] + [c for c in cand if c is not cand[k - 1]]
    want_n = max(1, min(int(count or 1), 3, left_h, left_d))
    sent, tried = [], 0
    for it in cand:
        if len(sent) >= want_n or tried >= 5:
            break
        tried += 1
        path = _fibi_prepare(it["path"], conf)
        if not path:
            log.warning("菲比表情发不了（不是图或读不了）: %s", it["path"])
            continue
        if not _SEND_IMAGE(cid, str(path), caption if not sent else ""):
            log.warning("菲比表情没发出去: %s", path)
            continue
        sent.append(it)
        st = _fibi_state()
        st.setdefault("sent", {}).setdefault(str(cid), []).append(
            {"id": it["id"], "t": time.time()})
        _fibi_save(st)
    if not sent:
        return False, "图找到了但没发出去（发送闸门拦了或协议端异常）。"
    desc = "、".join(x["stem"][:20] for x in sent)
    return True, "发出去 %d 张：%s%s" % (len(sent), desc, ("　" + note) if note else "")


# ══════════════════════════════════════════════════════════════ 情绪标准返图（pins）
# 主人 2026-10-26：「也让我能选定几张作为几个情绪的标准返图嘛」。
#
# 就是一张「情绪 → 图 id 列表」的表，落在 config.json 的
# `skills.settings.<id>.pins`（跟别的参数同一处，群口令 / 网页 / MCP 看到的都一样）。
# 为什么不塞进 skill.json：那是只读素材（改它要备份+原子写），而且 configSchema 只认
# 标量旋钮，装不下 dict。为什么不做成新旋钮：旋钮表是给「一行一个值」设计的，
# 情绪×图片是个二维选择，硬塞进去只会更丑 —— 所以单独给一块挑图面板。
PIN_SKILLS = ("fibi-memes", "whale-memes")
_PIN_MAX = 8


def pin_moods(sid):
    """这个技能能钉哪几种情绪（= 它自己那套情绪词表的键）。"""
    if sid == "fibi-memes":
        return list(_FIBI_HINT)
    if sid == "whale-memes":
        return list(MOOD_GROUPS)
    return []


def pins_of(sid):
    """读 pins。永远返回 {情绪: [图 id, ...]}，脏数据就地丢掉，不让页面炸。"""
    raw = _settings(sid).get("pins")
    if not isinstance(raw, dict):
        return {}
    moods = set(pin_moods(sid))
    out = {}
    for k, v in raw.items():
        k = str(k or "").strip()
        if not k or (moods and k not in moods):
            continue
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            continue
        ids = [str(x).strip() for x in v if str(x or "").strip()]
        if ids:
            out[k] = ids[:_PIN_MAX]
    return out


def pin_ids(sid, mood):
    return list(pins_of(sid).get(str(mood or "").strip()) or [])


def _pin_universe(sid):
    """这个技能里「能被钉」的图 id 集合（校验用）。"""
    if sid == "fibi-memes":
        return {x["id"] for x in _fibi_index()}
    if sid == "whale-memes":
        return {str(x["id"]) for x in (whale_catalog().get("items") or [])}
    return set()


def set_pins(sid, mood, ids):
    """校验一遍再交给宿主写盘。**不自己写 config**（qbot_skill_tools 不 import bot）。

    返回 (ok, msg, 清洗后的 ids)。
    """
    sid = str(sid or "").strip()
    mood = str(mood or "").strip()
    if sid not in PIN_SKILLS:
        return False, "「%s」不支持固定返图（只支持 %s）。" % (sid, "、".join(PIN_SKILLS)), []
    if mood not in pin_moods(sid):
        return False, "「%s」没有「%s」这个情绪。可选：%s" % (
            sid, mood, " / ".join(pin_moods(sid))), []
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list):
        return False, "ids 要一个数组。", []
    clean = []
    for x in ids:
        x = str(x or "").strip()
        if x and x not in clean:
            clean.append(x)
    if len(clean) > _PIN_MAX:
        return False, "一个情绪最多钉 %d 张（给了 %d 张）。" % (_PIN_MAX, len(clean)), []
    universe = _pin_universe(sid)
    if universe:
        bad = [x for x in clean if x not in universe]
        if bad:
            return False, "图库里没有：%s（可能被体积上限/匹配字过滤掉了）" % "、".join(bad[:3]), []
    return True, "%s「%s」%s" % (sid, mood, ("钉了 %d 张" % len(clean)) if clean else "已清空"), clean


def thumb_path(sid, name, px=200):
    """本地图的缩略图（只 fibi）。生成在 state/skills/_thumbs/，**绝不往素材目录写**。"""
    if sid != "fibi-memes":
        return None
    it = None
    for x in _fibi_index():
        if x["id"] == str(name or ""):
            it = x
            break
    if not it:
        return None
    src = Path(it["path"])
    try:
        st = src.stat()
    except OSError:
        return None
    key = hashlib.md5(("%s|%d|%d|%d" % (src, st.st_mtime, st.st_size, px)).encode("utf-8")).hexdigest()
    d = _state_root() / "_thumbs"
    out = d / (key + ".jpg")
    if out.exists() and out.stat().st_size > 0:
        return out
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return src                      # 没 PIL 就原图，页面慢点但别空着
    try:
        d.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            try:
                im.seek(0)              # 动图取第一帧
            except Exception:  # noqa: BLE001
                pass
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            bg.thumbnail((px, px))
            bg.save(out, "JPEG", quality=82)
        return out
    except Exception as e:  # noqa: BLE001
        log.debug("缩略图生成失败（%s）: %s", src.name, e)
        return src


def mood_board(sid, mood="", limit=24):
    """网页「挑图」面板要的一屏：情绪列表 + 该情绪的候选（带缩略图 URL）+ 已钉的图。"""
    sid = str(sid or "").strip()
    moods = pin_moods(sid)
    if not moods:
        return {"ok": False, "error": "「%s」没有情绪图库。" % sid}
    mood = str(mood or "").strip() or moods[0]
    if mood not in moods:
        mood = moods[0]
    try:
        lim = max(6, min(int(limit or 24), 48))
    except (TypeError, ValueError):
        lim = 24
    pins = pins_of(sid)
    note = ""
    if sid == "fibi-memes":
        cand, note = fibi_candidates(keyword=mood, want=lim, cid="", shuffle=False)
        pool = list(cand)
        if len(pool) < lim:                      # 贴切的太少就从全库补，别让面板空着
            have = {x["id"] for x in pool}
            extra = [x for x in _fibi_index() if x["id"] not in have]
            random.shuffle(extra)
            pool += extra[:lim - len(pool)]
            note = note or "贴「%s」的图只有 %d 张，后面补了别的。" % (mood, len(cand))
        items = []
        for it in pool[:lim]:
            items.append({"id": it["id"], "name": it["stem"], "size": it["size"],
                          "animated": bool(it["animated"]),
                          "thumb": "/api/thumb?skill=fibi-memes&name=" + quote(it["id"])})
    else:
        cand, note = whale_candidates(keyword=mood, want=lim, cid="")
        pool = list(cand)
        if len(pool) < lim:
            have = {str(x["id"]) for x in pool}
            extra = [x for x in (whale_catalog().get("items") or []) if str(x["id"]) not in have]
            random.shuffle(extra)
            pool += extra[:lim - len(pool)]
            note = note or "贴「%s」的图只有 %d 张，后面补了别的。" % (mood, len(cand))
        items = []
        for it in pool[:lim]:
            items.append({"id": it["id"], "name": it.get("caption") or it.get("name") or it["id"],
                          "size": 0, "animated": False,
                          "thumb": it.get("preview") or it.get("url") or ""})
    have = set(pins.get(mood) or [])
    for it in items:
        it["pinned"] = it["id"] in have
    # 钉过但没进候选的图（比如它本来就不贴这个情绪）也得露出来，不然主人看不到自己钉了啥
    if have:
        seen = {it["id"] for it in items}
        front = []
        for pid in pins.get(mood) or []:
            if pid in seen:
                continue
            it = _pin_item(sid, pid)
            if it:
                it["pinned"] = True
                front.append(it)
        if front:
            items = front + items
    return {"ok": True, "skill": sid, "mood": mood, "moods": moods,
            "items": items[:lim], "pinned": list(pins.get(mood) or []),
            "counts": {k: len(v) for k, v in pins.items()}, "note": note}


def _pin_item(sid, pid):
    """按 id 造一条面板条目（给「钉了但不在候选里」的图用）。"""
    if sid == "fibi-memes":
        for x in _fibi_index():
            if x["id"] == pid:
                return {"id": x["id"], "name": x["stem"], "size": x["size"],
                        "animated": bool(x["animated"]),
                        "thumb": "/api/thumb?skill=fibi-memes&name=" + quote(x["id"]),
                        "pinned": True}
        return None
    for x in (whale_catalog().get("items") or []):
        if str(x["id"]) == pid:
            return {"id": x["id"], "name": x.get("caption") or x.get("name") or x["id"],
                    "size": 0, "animated": False,
                    "thumb": x.get("preview") or x.get("url") or "", "pinned": True}
    return None


# ══════════════════════════════════════════════════════════════ 菲比图库：同步 / 收图
# 主人 2026-10-26 晚：「菲比是本机素材，没有网站吗？不能跟随网站更新嘛，能指定聊天时
# 的表情加入图库嘛？」
#   有网站：**Phoebe Hub（https://phoebehub.top/）**，仓库 Kato-Shoko705/Phoebe-Hub，
#   索引 `data/memes.json` + `images/`。`images/emoji` 那 1133 张就是
#   `tools/import_emoji.py` 从这儿下的 —— 那脚本要手动跑，这里把两件事做进运行时：
#     ① `fibi_sync()` 跟网站对一遍，只补新的（不重复下、不覆盖本地改过的名字）；
#     ② `fibi_add()` 把群里看到的图收进图库（`收图` 口令 / 网页按钮都走它）。
FIBI_SITE = "https://phoebehub.top/"
FIBI_REPO = "Kato-Shoko705/Phoebe-Hub"
# ⚠️ CDN 会轮着抽风，别只留一个。2026-10-26 实测：gcore 被 RST、fastly 通（348KB / 1178 条），
#    跟 `tools/import_emoji.py` 注释里写的正好相反 —— 所以两个都留，按顺序试。
FIBI_CDNS = (
    "https://fastly.jsdelivr.net/gh/%s@main/" % FIBI_REPO,
    "https://gcore.jsdelivr.net/gh/%s@main/" % FIBI_REPO,
    "https://raw.githubusercontent.com/%s/main/" % FIBI_REPO,
)
_FIBI_SYNC = {"running": False, "at": 0.0, "total": 0, "done": 0,
              "added": 0, "fail": 0, "msg": "", "err": ""}
# 能「跟网站对一遍」的技能（网页抽屉里出「同步」按钮），值是同步通道名：
#   fibi     → 菲比图库跟 Phoebe Hub 对一遍
#   memewiki → 梗百科词库跟 cnmeme.wiki 对一遍（2026-10-26）
# whale-memes 自己有 refreshHours 自动刷新，不用手动同步。
SYNC_SKILLS = ("fibi-memes",)
SYNC_KINDS = {"fibi-memes": "fibi", "memewiki": "memewiki", "petpet": "petpet"}
_FIBI_EXT = {b"GIF8": ".gif"}


def _fibi_http(rel, timeout=30):
    """按 CDN 顺序取一个文件。中文名要 quote，但保留路径分隔符。"""
    last = None
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for base in FIBI_CDNS:
        try:
            req = urllib.request.Request(
                base + quote(rel, safe="/()[]'!~*"),
                headers={"User-Agent": UA, "Referer": FIBI_SITE,
                         "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
            with op.open(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(str(last)[:140])


def _fibi_safe_name(meme):
    """网站条目 → 本地文件名。跟 `tools/import_emoji.py` 的规则一致，不然会重复下。"""
    base = os.path.basename(urllib.parse.unquote(str(meme.get("url") or "")))
    stem, ext = os.path.splitext(base)
    for ch in '\\/:*?"<>|':
        stem = stem.replace(ch, "_")
    stem = stem.strip().strip(".") or ("meme_%s" % meme.get("id"))
    return stem[:80] + (ext or ".webp").lower()


def _fibi_collected(root):
    """「不受 match 过滤」的文件名集合。

    两个来源：
      · `_collected.json` —— 群里收的图（名字随主人起，多半没「菲比」）；
      · `_manifest.json`  —— 网站清单（`import_emoji.py` / 同步写的），站上的图本来就
        是菲比表情包，只是文件名常是「我倒要看看.jpg」这种。不放行的话，
        图库里 1100 多张只有 400 张能发，「跟随网站更新」等于白做。
    """
    files = set()
    d = _read_json(Path(root) / "_collected.json", {})
    if isinstance(d, dict) and isinstance(d.get("files"), list):
        files.update(str(x) for x in d["files"])
    m = _read_json(Path(root) / "_manifest.json", {})
    if isinstance(m, dict) and isinstance(m.get("files"), dict):
        files.update(str(x) for x in m["files"].values())
    return files


def _fibi_sync_manifest(root, pairs):
    """把「标题 → 文件名」并进 `_manifest.json`（网页/口令查图用）。"""
    p = Path(root) / "_manifest.json"
    old = _read_json(p, {})
    files = (old.get("files") if isinstance(old, dict) else None) or {}
    if not isinstance(files, dict):
        files = {}
    for title, name in pairs:
        files[str(title)] = name
    try:
        _write_json(p, {"source": FIBI_SITE, "repo": FIBI_REPO,
                        "count": len(files), "files": files})
    except Exception as e:  # noqa: BLE001
        log.debug("图库清单写不进去: %s", e)


def fibi_sync(limit=0, workers=6):
    """跟 Phoebe Hub 对一遍：只下本地没有的。**不删本地、不覆盖同名文件。**

    为什么不做「镜像删除」：主人可能自己往 images/emoji 里丢过图、也可能改过文件名，
    照网站全量同步会把它们抹掉 —— 图库是素材不是缓存。
    """
    conf = _fibi_conf()
    root = _fibi_dir(conf)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, "图库目录建不了（%s）：%s" % (root, e)
    try:
        raw = _fibi_http("data/memes.json", timeout=30)
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return False, "拉不到网站索引：%s" % str(e)[:120]
    memes = [m for m in (data.get("memes") or [])
             if isinstance(m, dict) and str(m.get("url") or "").startswith("images/")]
    if limit:
        memes = memes[:int(limit)]
    if not memes:
        return False, "网站索引里没有条目（格式可能变了）"
    _FIBI_SYNC.update(total=len(memes), done=0, added=0, fail=0)
    todo = []
    for m in memes:
        name = _fibi_safe_name(m)
        dst = root / name
        if dst.is_file() and dst.stat().st_size > 512:
            continue
        todo.append((m, name))
    added, fails = [], 0
    _FIBI_SYNC["total"] = len(todo)
    if todo:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _one(job):
            m, name = job
            try:
                blob = _fibi_http(m["url"], timeout=40)
            except Exception as e:  # noqa: BLE001
                return ("fail", name, str(e)[:80])
            if len(blob) < 512:
                return ("fail", name, "太小")
            # CDN 对不存在的图有时回 200 + HTML 错误页 —— 不验文件头就会往图库里写一张假图
            if not (blob.startswith(b"GIF8") or blob[:4] == b"RIFF"
                    or blob.startswith(b"\x89PNG\r\n\x1a\n")
                    or blob.startswith(b"\xff\xd8\xff")):
                return ("fail", name, "不是图片（CDN 回了错误页）")
            dst = root / name
            if dst.is_file():                 # 同名不同图：加 id 前缀，别互相覆盖
                dst = root / ("%s_%s" % (m.get("id"), name))
            try:
                dst.write_bytes(blob)
            except OSError as e:
                return ("fail", name, str(e)[:60])
            return ("ok", dst.name, len(blob))

        with ThreadPoolExecutor(max_workers=max(1, min(int(workers or 6), 12))) as ex:
            futs = [ex.submit(_one, j) for j in todo]
            for i, f in enumerate(as_completed(futs), 1):
                kind, name, info = f.result()
                if kind == "ok":
                    added.append(name)
                    _FIBI_SYNC["added"] = len(added)
                else:
                    fails += 1
                    _FIBI_SYNC["fail"] = fails
                    log.debug("图库同步失败 %s: %s", name, info)
                _FIBI_SYNC["done"] = i
    # 网站来的图**记账放行 match**：站上的文件名多是「我倒要看看.jpg」这种，
    # 不带「菲比」三个字，不放行的话同步 1000 张也只有几十张能发 —— 那同步就没意义了。
    if added:
        cp = root / "_collected.json"
        cur = _read_json(cp, {})
        files = cur.get("files") if isinstance(cur, dict) else None
        files = list(files or []) if isinstance(files, list) else []
        for n in added:
            if n not in files:
                files.append(n)
        try:
            _write_json(cp, {"at": time.time(), "files": files})
        except Exception as e:  # noqa: BLE001
            log.debug("收图清单写不进去: %s", e)
    _fibi_sync_manifest(root, [(m.get("title") or n, n) for m, n in todo])
    _fibi_index(force=True)
    total = len(_fibi_index())
    msg = "同步完成：网站 %d 个 / 本地新增 %d 张 / 失败 %d，图库现在 %d 张可用" % (
        len(memes), len(added), fails, total)
    log.info("菲比图库同步：%s", msg)
    return True, msg


def fibi_sync_start():
    """后台跑同步（全量下 1000+ 张要几分钟，别卡住网页请求）。"""
    if _FIBI_SYNC.get("running"):
        return False, "上次同步还没跑完（%d/%d）" % (
            _FIBI_SYNC.get("done") or 0, _FIBI_SYNC.get("total") or 0)
    import threading
    _FIBI_SYNC.update(running=True, at=time.time(), total=0, done=0,
                      added=0, fail=0, msg="正在拉网站索引…", err="")

    def _job():
        try:
            ok, msg = fibi_sync()
            _FIBI_SYNC.update(msg=msg, err="" if ok else msg)
        except Exception as e:  # noqa: BLE001
            log.warning("图库同步挂了: %s", e)
            _FIBI_SYNC.update(err=str(e)[:200], msg="同步失败：%s" % str(e)[:120])
        finally:
            _FIBI_SYNC["running"] = False
            _FIBI_SYNC["at"] = time.time()

    threading.Thread(target=_job, daemon=True).start()
    return True, "开始同步（后台跑，图多的时候要几分钟）"


def fibi_sync_status():
    conf = _fibi_conf()
    root = _fibi_dir(conf)
    st = dict(_FIBI_SYNC)
    # 这里只数「主人从群里收的」—— 网站清单（_manifest）上千条，混进来数字会自相矛盾
    # （收图数比可用图还多）。索引放行用的是合并后的 `_fibi_collected()`，两回事。
    cd = _read_json(root / "_collected.json", {})
    mine = cd.get("files") if isinstance(cd, dict) else None
    st.update(site=FIBI_SITE, repo="https://github.com/" + FIBI_REPO,
              dir=str(root), count=len(_fibi_index()),
              collected=len(mine or []) if isinstance(mine, list) else 0)
    return st


def _clean_stem(name, limit=60):
    s = str(name or "").strip()
    for ch in '\\/:*?"<>|\r\n\t':
        s = s.replace(ch, "_")
    s = " ".join(s.split()).strip(" .")
    return s[:limit]


def fibi_add(src, name=""):
    """把一张本地图片收进图库。返回 (ok, msg, 存下来的文件名)。

    收进来的图**不受 `match` 限制**（`_collected.json` 记着）—— 主人从群里收的多半
    名字里没有「菲比」，按 match 过滤等于白收。
    """
    conf = _fibi_conf()
    root = _fibi_dir(conf)
    p = Path(str(src or ""))
    try:
        with open(p, "rb") as f:
            head = f.read(16)
        size = p.stat().st_size
    except OSError as e:
        return False, "读不到图片：%s" % str(e)[:80], ""
    if head.startswith(b"GIF8"):
        ext = ".gif"
    elif head[:4] == b"RIFF":
        ext = ".webp"
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        ext = ".png"
    elif head.startswith(b"\xff\xd8\xff"):
        ext = ".jpg"
    else:
        return False, "这不是图片（认不出文件头，可能发来的是文件/链接）", ""
    if size < 2048:
        return False, "图太小（%d 字节），多半是坏图或占位图" % size, ""
    if size > 12 * 1024 * 1024:
        return False, "图太大（%.1fMB），QQ 传不动，先压一下" % (size / 1048576.0), ""
    stem = _clean_stem(name) or ("收图_" + time.strftime("%Y%m%d_%H%M%S"))
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, "图库目录建不了：%s" % e, ""
    dst = root / (stem + ext)
    n = 1
    while dst.exists():
        n += 1
        dst = root / ("%s_%d%s" % (stem, n, ext))
        if n > 50:
            return False, "同名文件太多了，换个名字吧", ""
    try:
        dst.write_bytes(p.read_bytes())
    except OSError as e:
        return False, "写不进图库：%s" % e, ""
    # 记账：收进来的图不过 match 过滤
    cp = root / "_collected.json"
    cur = _read_json(cp, {})
    files = cur.get("files") if isinstance(cur, dict) else None
    files = list(files or []) if isinstance(files, list) else []
    if dst.name not in files:
        files.append(dst.name)
    try:
        _write_json(cp, {"at": time.time(), "files": files})
    except Exception as e:  # noqa: BLE001
        log.debug("收图清单写不进去: %s", e)
    _fibi_sync_manifest(root, [(stem, dst.name)])
    _fibi_index(force=True)
    total = len(_fibi_index())
    log.info("收图入库：%s（%.0fKB），图库现在 %d 张", dst.name, size / 1024.0, total)
    return True, "收好了：%s（%.0fKB，%s），图库现在 %d 张可用" % (
        dst.name, size / 1024.0, "动图" if ext in (".gif", ".webp") else "静图", total), dst.name


# ── 注册表 ──────────────────────────────────────────────────────────────


def _mw_tool(name):
    """梗百科的工具入口（2026-10-26）：模块按需导入。

    单独成模块是因为它自带 6668 条词库和卡片渲染，塞进这个文件会把它撑爆；
    真导入失败（比如漏拷文件）也只让这两个工具报错，别把整张技能表带崩。
    """
    def _call(**kw):
        try:
            import qbot_memewiki
        except Exception as e:  # noqa: BLE001
            return False, "梗百科模块没加载（%s）" % e
        return getattr(qbot_memewiki, "tool_" + name)(**kw)
    _call.__name__ = "mw_" + name
    return _call


TOOLS = {
    "trendy-memes": {
        "lookup": tm_lookup,
        "catalog": tm_catalog,
        "teach": tm_teach,
    },
    "memewiki": {
        "lookup": _mw_tool("lookup"),
        "catalog": _mw_tool("catalog"),
    },
    "petpet": {
        "send": petpet_send,
        "list": petpet_list,
    },
    "whale-memes": {
        "send": whale_send,
        "list": whale_list,
    },
    "fibi-memes": {
        "send": fibi_send,
        "list": fibi_list,
    },
}

# 给 qbot_skills.prompt_block 判断「本机到底接没接」用
NATIVE = {sid: sorted(v) for sid, v in TOOLS.items()}

# 拼进提示词的「本机怎么用」——只写模型真做得到的事（它只能写字，不能调函数）
USAGE = {
    "trendy-memes": "本机的梗库可以直接查（已经在你手边的梗就在下面），查不到就老实说不认识，别硬编。"
                    "（问「查网络梗」那个工具就是本机梗库，联网查另算。）",
    "memewiki": ("【本机怎么查】梗百科 6668 条词条全在本机（`梗 <词>` 直接回卡片），"
                 "你手上没有调工具的通道时，系统已经替你把「这一句里的梗」查好塞进上下文了 —— "
                 "**看到【梗百科】那一段就照着接梗**，别复述词条、别报出处（除非对方明确问哪来的）。"
                 "没有那一段 = 这词没收录，**不确定就说不确定，绝对别硬编**。"),
    "whale-memes": ("【本机怎么发】两条路，你手上是哪条就用哪条：\n"
                    "① 能调工具（MCP 里有 `qq_meme_send` / `qq_meme_pick`）→ 直接调；\n"
                    "② 不能调工具 → **在回复的最后单独一行**写 `[[梗图:关键词]]`（例如 `[[梗图:生气]]`，"
                    "指定角色写 `[[梗图:生气|DeepSeek娘]]`）。这行会被系统吃掉，图会跟在这句话后面发出去。\n"
                    "两条路都：**别在正文里解释标记、别预告「我找个图给你」**；一次最多 1 张，别每句都配；"
                    "正文里的 `whale-memes__send_meme` / `whale-memes__list_memes` 是原作者的工具名，"
                    "本机没有这两个名字（按上面的替代）。额度用完/图床连不上时系统会静默放弃，"
                    "你别反复试，也别假装发了。"),
    "petpet": ("【本机怎么用】能调工具就调 `qq_petpet`（group_id + qq + template）；"
               "群友直接说「摸摸头 @某人」这类口令时**系统会自己出图**，你不用管、也别复述图长什么样。"
               "做不出来就正常说话，别假装发了。"),
    "fibi-memes": ("【本机怎么发】菲比表情包全在本机（images/emoji），不用联网。两条路：\n"
                   "① 能调工具（MCP 里有 `qq_fibi_send` / `qq_fibi_pick`）→ 直接调；\n"
                   "② 不能调工具 → **在回复最后单独一行**写 `[[菲比:关键词]]`（如 `[[菲比:无语]]`）。"
                   "这行会被系统吃掉，图跟在这句话后面发出去。\n"
                   "两条路都：一次最多 1 张、别每条都发、别在正文里描述图里画了什么、"
                   "别预告「我找个图给你」。额度用完或没贴切的图时系统会静默放弃，你别反复试、别假装发了。"),
}


def has_native(sid):
    return sid in TOOLS


def call(skill, tool, **kwargs):
    """统一入口。返回 (ok, text)。"""
    sid = str(skill or "").strip()
    t = str(tool or "").strip().lower()
    fn = (TOOLS.get(sid) or {}).get(t)
    if not fn:
        return False, "没有这个技能工具：%s.%s（已有：%s）" % (
            sid, t, "、".join("%s.%s" % (s, x) for s, v in TOOLS.items() for x in v))
    try:
        return fn(**kwargs)
    except TypeError as e:
        return False, "参数不对：%s" % e
    except Exception as e:  # noqa: BLE001
        log.warning("技能工具 %s.%s 出错: %s", sid, t, e, exc_info=True)
        return False, "工具出错了：%s" % e
