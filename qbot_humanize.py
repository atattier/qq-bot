# -*- coding: utf-8 -*-
"""拟人化发送（2026-10-19，参考 ai-companion-kit 的 Humanizer）。

一个真人发消息是这样的：
  1. 先「正在输入…」一会儿（时长跟字数成正比，但不完全成正比）
  2. 短句分几条发，不是一大段
  3. 条与条之间有停顿，偶尔停久一点（像在走神/被打断）
  4. 深夜和白天的节奏不一样

这个模块**只管怎么发**，不管说什么。和 qbot_slang/qbot_persona 一样的规矩：
**不 import bot**，宿主能力由调用方传进来（send / set_typing / sleep）。

参数沿用上游那套（有实测依据），我们只加了一条它没有的：**总时长上限**
（`max_total_ms`）——它 4 条 × 6 秒能拖 40 秒，QQ 群里没人等得起。

⚠️ 别把打字延迟调太猛：`typing` 上报本身也是风控眼里的动作，见 kit 文档第十三章
（一天 175 次 `set_input_status` 会被盯上，要配合 min_interval 与概率）。
"""
import random
import time

# 睡觉时段（本地时间），这段时间回复明显变慢
NIGHT_FROM, NIGHT_TO = 2, 7
DEFAULT_MS_PER_CHAR = 85

DEFAULTS = {
    "enabled": True,
    "ms_per_char": 85,
    "min_ms": 1200,
    "max_ms": 6000,
    "jitter": 0.2,
    "burst_max": 4,
    "gap_min_ms": 700,
    "gap_max_ms": 2600,
    "long_gap_chance": 0.25,
    "long_gap_min_ms": 3000,
    "long_gap_max_ms": 9000,
    "night_slow": True,
    "night_factor": 1.6,
    "max_total_ms": 12000,       # 我们加的：整条回复的等待上限
    "typing_enabled": True,
    "typing_min_chars": 20,      # 太短的消息不报「正在输入」
    "typing_min_interval_ms": 45000,
    "typing_chance": 0.35,
}


def cfg_of(cfg):
    """把宿主传进来的 dict 补全成完整参数（缺什么用什么默认）。"""
    out = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if k in out and v is not None:
            out[k] = v
    return out


def typing_ms(text, c=None):
    """按字数算「正在输入」该持续多久。"""
    c = c or DEFAULTS
    n = len(str(text or ""))
    ms = float(c["min_ms"]) + n * float(c["ms_per_char"])
    ms = max(float(c["min_ms"]), min(float(c["max_ms"]), ms))
    ms *= 1 + (random.random() * 2 - 1) * float(c["jitter"])
    if c.get("night_slow"):
        h = time.localtime().tm_hour
        if NIGHT_FROM <= h < NIGHT_TO:
            ms *= float(c["night_factor"])
    return max(300, int(ms))


def gap_ms(c=None):
    """条与条之间停多久（偶尔来一次长停顿，像走神）。"""
    c = c or DEFAULTS
    a, b = float(c["gap_min_ms"]), float(c["gap_max_ms"])
    ms = a + random.random() * max(0.0, b - a)
    if random.random() < float(c["long_gap_chance"]):
        lo, hi = float(c["long_gap_min_ms"]), float(c["long_gap_max_ms"])
        ms += lo + random.random() * max(0.0, hi - lo)
    return int(ms)


SENT_END = "。！？!?…~～"


def split(text, max_parts=None, c=None):
    """把一段话切成「像人打出来的」几条。

    规则（照抄上游，实测够用）：
      - 先按换行切
      - 单段超过 40 字，再按中文句末标点切
      - 相邻的碎句（<6 字）向后面合并，别一条一条蹦
      - 超过 max_parts 就把尾巴合并成最后一条
    """
    c = c or DEFAULTS
    max_parts = int(max_parts or c["burst_max"])
    raw = str(text or "").strip()
    if not raw:
        return []
    parts = [s.strip() for s in raw.split("\n") if s.strip()]
    out = []
    for p in parts:
        if len(p) <= 40:
            out.append(p)
            continue
        segs, buf = [], ""
        for ch in p:
            buf += ch
            if ch in SENT_END:
                if buf.strip():
                    segs.append(buf.strip())
                buf = ""
        if buf.strip():
            segs.append(buf.strip())
        out.extend(segs or [p])
    merged = []
    for s in out:
        if merged and len(merged[-1]) < 6 and len(merged[-1]) + len(s) <= 24:
            merged[-1] = merged[-1] + s
        else:
            merged.append(s)
    if len(merged) > max_parts:
        head = merged[:max_parts - 1]
        head.append(" ".join(merged[max_parts - 1:]))
        return head
    return merged


def say(text, send, set_typing=None, c=None, log=None):
    """执行一次拟人化发送。返回 (发出的条数, 分好的各条)。

    send(part) -> bool        真正发一条（返回 False 就停下，别继续刷）
    set_typing(True/False)    可选；不支持就传 None
    """
    c = cfg_of(c)
    parts = text if isinstance(text, list) else split(text, c=c)
    parts = [p for p in parts if str(p).strip()]
    if not parts:
        return 0, []
    if not c.get("enabled"):
        n = 0
        for p in parts:
            if not send(p):
                break
            n += 1
        return n, parts

    # 总时长预算：先按理论值算，超了就整体按比例缩（不然 4 条能拖 40 秒）
    want = sum(typing_ms(p, c) for p in parts) + sum(gap_ms(c) for _ in parts[1:])
    budget = float(c.get("max_total_ms") or 0)
    scale = 1.0
    if budget > 0 and want > budget:
        scale = budget / float(want)

    sent = 0
    typing_left = 3          # 连续失败就放弃上报，别每句都去撞一堵墙
    last_typing = 0.0
    for i, p in enumerate(parts):
        if set_typing and c.get("typing_enabled") and typing_left > 0:
            now = time.time() * 1000
            if (len(p) >= int(c["typing_min_chars"])
                    and now - last_typing >= float(c["typing_min_interval_ms"])
                    and random.random() < float(c["typing_chance"])):
                try:
                    set_typing(True)
                except Exception:
                    typing_left -= 1
            time.sleep(max(0.05, typing_ms(p, c) * scale / 1000.0))
            try:
                if typing_left > 0:
                    set_typing(False)
            except Exception:
                typing_left -= 1
            last_typing = time.time() * 1000
        else:
            time.sleep(max(0.05, typing_ms(p, c) * scale / 1000.0))
        if not send(p):
            break
        sent += 1
        if i < len(parts) - 1:
            time.sleep(max(0.05, gap_ms(c) * scale / 1000.0))
    if log and scale < 1.0:
        log.debug("拟人化发送按预算压缩：理论 %.0fms → %.0fms", want, want * scale)
    return sent, parts
