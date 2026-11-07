"""情绪状态：给 bot 一个**持续的底色**，而不是每轮现编心情。

为什么单独做一个模块（2026-10-26）：
    主人要「有强烈情感倾向、会吐槽、像人一样」的 bot。查了一圈开源项目
    （Pancakes-Labs/astrbot_plugin_proactive_chat 的情绪状态轴、AICO 的 PAD 思路），
    共同结论只有一句：**情绪必须是状态，不能是提示词**。
    写「你是一个有感情的人」没用；写「你现在有点烦，刚被催了三次」才有用。

三条硬规矩：
  1. **绝不向用户报数值**（不许出现「愉悦度 0.8」这种）。情绪只体现在
     语气、句子长短、分几条、要不要开口上。
  2. 情绪**会自己消退**，回归基线。不然一次被怼就永久记仇。
  3. 状态按会话（群/私聊）分开存，但跨轮持续 —— 昨天聊得开心，今天还带着。

坐标轴（都是 0~1，valence 是 -1~1）：
    valence   愉悦    基线 +0.15
    arousal   兴奋度  基线 0.45
    irritation 烦躁   基线 0.10
    curiosity 好奇    基线 0.60
    energy    社交电量 基线 0.60
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("qqbot")

BASE = {
    "v": 0.15, "a": 0.45, "irr": 0.10, "cur": 0.60, "en": 0.60,
}
# 半衰期（小时）：多久回归基线一半。烦躁散得最快，电量回得最慢。
HALF_LIFE = {"v": 0.75, "a": 0.50, "irr": 0.50, "cur": 2.0, "en": 3.0}

_PRAISE = re.compile(r"(好棒|真棒|棒|厉害|可爱|喜欢你|谢谢|谢了|牛啊|太强|爱你|靠谱|聪明|真好|不错|绝了|哈哈哈|笑死)")
# ⚠️ 2026-10-26 实测踩到：原来的词太宽，群聊里「在吗」「怎么回事」「滚啊（开玩笑）」
#    全被当成催/怼，烦躁值 10 分钟就冲到 0.98 卡死，之后半小时不插话。
#    现在只收**明确带攻击性/催促**的说法，日常口语一律不算。
_INSULT = re.compile(r"(滚开|滚蛋|滚吧|傻逼|蠢货|废物|闭嘴|垃圾|弱智|你不行|别说话|烦死你|有病吧|智障|笨死|你懂个)")
_URGE = re.compile(r"(快点回|快点说|怎么还不|你倒是|催你|催一下|卡死了|死机了|没反应啊)")

_PATH: Path | None = None
_CFG = {}
_STATE = {}
_LOCK = threading.Lock()
_MAX_CONV = 60


def bind(path, cfg=None):
    """由 bot.py 启动时调用。path = state/mood.json。"""
    global _PATH, _CFG
    if path:
        _PATH = Path(path)
    if cfg is not None:
        _CFG = cfg
    _load()


def _load():
    global _STATE
    if not _PATH:
        return
    try:
        if _PATH.is_file():
            _STATE = json.loads(_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log.warning("心情状态读不出来（重置为空）：%s", e)
        _STATE = {}


def _save():
    if not _PATH:
        return
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        # 只留最近活跃的若干会话，别无限长
        items = sorted(_STATE.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
        data = dict(items[:_MAX_CONV])
        _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log.debug("心情状态存不下来：%s", e)


def _clamp(k, v):
    lo, hi = (-1.0, 1.0) if k == "v" else (0.0, 1.0)
    return max(lo, min(hi, v))


def _key(gid):
    try:
        return str(int(gid))
    except (TypeError, ValueError):
        return str(gid or "0")


def _decay(st, now):
    """按流逝时间往基线回落。**永远补齐所有轴**（缺轴会让调用方 KeyError）。"""
    dt = max(0.0, now - float(st.get("ts") or now)) / 3600.0
    for k, hl in HALF_LIFE.items():
        cur = float(st.get(k, BASE[k]))
        base = BASE[k]
        keep = 0.5 ** (dt / hl) if dt > 0 else 1.0   # 半衰期：偏离量每 hl 小时减半
        st[k] = _clamp(k, base + (cur - base) * keep)
    return st


def snapshot(gid):
    """当前状态（已按时间衰减）。只给内部/网页看，**不许发给群友**。"""
    k = _key(gid)
    now = time.time()
    with _LOCK:
        st = dict(_STATE.get(k) or {"ts": now})
        st = _decay(st, now)
        _STATE[k] = st
    return st


def note(gid, text):
    """记一次互动，更新心情。返回新状态。

    识别三类信号：被夸 / 被怼 / 被催。另外「说了很长一段」算深聊 ——
    好奇上升、电量下降（聊天是消耗精力的）。
    """
    t = str(text or "")
    k = _key(gid)
    now = time.time()
    with _LOCK:
        st = dict(_STATE.get(k) or {"ts": now})
        st = _decay(st, now)
        neg, praise = [], False
        # 负向信号有**冷却**：60 秒内连着来只算一次。
        # 不然群里刷屏十几句，烦躁值会瞬间冲到 1.0，之后半小时不开口（实测踩到）。
        can_neg = (now - float(st.get("neg_ts") or 0)) > 60
        if _PRAISE.search(t):
            st["v"] += 0.15
            st["irr"] -= 0.12
            st["en"] += 0.04
            praise = True
        if can_neg and _INSULT.search(t):
            st["v"] -= 0.22
            st["irr"] += 0.30
            st["a"] += 0.12
            neg.append("刚被怼")
            st["neg_ts"] = now
        if can_neg and _URGE.search(t):
            st["irr"] += 0.14
            st["a"] += 0.06
            neg.append("被催")
            st["neg_ts"] = now
        if len(t) > 60:
            st["cur"] += 0.06
            st["en"] -= 0.05
        for kk in ("v", "a", "irr", "cur", "en"):
            st[kk] = _clamp(kk, float(st.get(kk, BASE[kk])))
        # 烦躁封顶 0.85：别让它一直贴顶，不然永远不开口、语气永远冲
        st["irr"] = min(st["irr"], 0.85)
        st["ts"] = now
        # 缘由只记「不快」的原因：被夸了就把旧的缘由清掉，
        # 否则会出现「心情：烦躁，刚被夸」这种自相矛盾的句子（实测踩到）
        if neg:
            st["why"] = neg[-1]
        elif praise:
            st.pop("why", None)
        _STATE[k] = st
        _save()
    return st


def _word(st):
    """把状态说成一个词（给人看的那种，不是数值）。"""
    v, irr, a, en = st["v"], st["irr"], st["a"], st["en"]
    if irr >= 0.35:
        return "烦躁"
    if v <= -0.12:
        return "不太高兴"
    if v >= 0.45 and a >= 0.5:
        return "来劲"
    if v >= 0.35:
        return "挺好"
    if en <= 0.35:
        return "有点乏"
    if a <= 0.25:
        return "懒得动"
    return "平静"


def block(gid):
    """给 system prompt 的心情段。**偏离不大就返回空串**，别每轮都塞噪音。"""
    if not _CFG or not (_CFG.get("mood") or {}).get("enabled", True):
        return ""
    st = snapshot(gid)
    dev = {k: st[k] - BASE[k] for k in BASE}
    if max(abs(x) for x in dev.values()) < 0.12:
        return ""
    lines = ["【你现在的心情】（这是你此刻的状态，不是任务）"]
    w = _word(st)
    lines.append("心情：%s" % w)
    if st.get("why") and st["irr"] >= 0.3:
        lines.append("缘由：%s" % st["why"])
    hint = []
    if st["irr"] >= 0.35:
        hint.append("说话短、别哄人、别道歉、别加表情包；有人找茬就顶回去")
    if st["v"] >= 0.42:
        hint.append("愿意多说两句，可以主动接话茬")
    if st["v"] <= -0.12:
        hint.append("兴致不高，别强行热情")
    if st["en"] <= 0.38:
        hint.append("精力不济，一句话说完就行，别分条")
    if st["a"] <= 0.28:
        hint.append("提不起劲，回得懒一点")
    if st["cur"] >= 0.75:
        hint.append("有点手痒，想接着聊刚才那个话题")
    if hint:
        lines.append("表现：" + "；".join(hint))
    lines.append("（别把心情说出来，也别报数值 —— 让它体现在语气和长短上。）")
    return "\n".join(lines)


def burst_cap(gid, default=3):
    """心情影响「分几条发」：没精力就少分条。"""
    st = snapshot(gid)
    if st["en"] <= 0.35 or st["a"] <= 0.25:
        return min(int(default), 1)
    if st["v"] >= 0.5 and st["a"] >= 0.5:
        return int(default)
    return max(1, int(default) - 1)


def sim_bias(gid):
    """主动插话的意愿系数（0~1）。烦/累的时候不想主动开口。"""
    st = snapshot(gid)
    if st["irr"] >= 0.5 or st["en"] <= 0.3:
        return 0.0          # 心情差 / 没电了 → 不主动插话
    if st["a"] <= 0.25:
        return 0.35
    score = 0.35 + 0.4 * st["a"] + 0.25 * max(0.0, st["v"])
    return max(0.0, min(1.0, score))
