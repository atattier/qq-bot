# -*- coding: utf-8 -*-
"""时机层（2026-11-04 人味阶段 B）：开口时机的概率决策，替代死板的固定节拍。

四个机制来自 MaiBot/SurfCore 的研究（docs/人味阶段研究-2026-11-03.md §1/§4.2），
按文档数值与公式**重写**实现（不复制 AGPL 代码）：

1. reply_likelihood —— 纯函数：用消息批的廉价特征算「这批值不值得花一次 LLM 判定」
   的概率。权重是 MaiBot 拿真实行为离线拟合的，数值原样移植（§1.2 权重表）。
2. dynamic_gate —— 自校准额度：1 小时滑窗内「实际开口次数」逼近「Σ概率×talk_value」；
   **只收紧不放宽**（校准阈值永不低于静态阈值）；窗口样本 <20 条退回静态阈值（§1.3）。
3. 空闲指数退避 + 积压绕过 —— 连续 [SILENT] 越多，下次开口前要等越久
   （15s×2^(n-2) 封顶 backoff_cap）；任何回复/@ 重置；积压 ≥bypass_pending 条绕过
   （热闹来了别装死，§1.5）。
4. wait/stop 出口 —— 插话模型可回 [WAIT:秒]（≤wait_max）表示「先等一等再看」：
   合批 flush 原样塞回窗口滚到到点再判，@/点名/任何回复立即唤醒；wait 不消耗
   冷却/配额（沿用 sim_note_sent 语义）。[SILENT] 即 stop，沿用现有处理（§1.4）。

🔴 总闸 `timing.enabled` 默认 **关**：关 = 本模块在所有入口恒放行、恒不记账，
   行为与没有它完全一致。主人开闸才生效。
🔴 mood 仍是开口的唯一权威：mood/冷却/配额/密度闸全在本模块**之前**一票否决；
   本模块只决定「过闸的这批值不值得再花一次 LLM」，sim_bias 只作概率特征输入。
"""
import json
import logging
import math
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("qqbot")

BASE = Path(__file__).resolve().parent
STATE_FILE = BASE / "state" / "timing.json"

_lock = threading.RLock()
_STATE = {
    "gate_th": 0.35,     # 自校准后的判定门槛（永不低于静态阈值）
    "rounds": [],        # [{"t": ts, "p": 0~1}] 每个到点批一条（1h 滑窗）
    "sends": [],         # [ts] 实际开口时刻（1h 滑窗）
    "gid": {},           # str(gid) -> {"silent": n, "last_silent": ts, "wait_until": ts, "opener_silent": n}
}
_last_save = [0.0]

# ---- 宿主钩子（未 bind 降级：names 空 / self_id 0 / 无上下文 → 特征退化但不炸）----
CFG = None
_names = None
_self_id = None
_context_items = None
_is_placeholder = None


def bind(cfg=None, names=None, self_id=None, context_items=None, is_placeholder=None,
         state_dir=None):
    """bot.py 启动时调用；cfg 传活引用。state_dir 给了就读写 state/timing.json。"""
    global CFG, _names, _self_id, _context_items, _is_placeholder, STATE_FILE
    if cfg is not None:
        CFG = cfg
    if names is not None:
        _names = names
    if self_id is not None:
        _self_id = self_id
    if context_items is not None:
        _context_items = context_items
    if is_placeholder is not None:
        _is_placeholder = is_placeholder
    if state_dir is not None:
        STATE_FILE = Path(state_dir) / "timing.json"
        load()


def enabled():
    return bool(((CFG or {}).get("timing") or {}).get("enabled", False))


def _cfgget(key, default):
    v = ((CFG or {}).get("timing") or {}).get(key)
    return default if v is None else v


# ---------------- 持久化（校准阈值/退避/等待；原子写，写崩不毁文件） ----------------
def load():
    try:
        if STATE_FILE.exists():
            d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        else:
            d = None
    except Exception as e:  # noqa: BLE001
        log.warning("读 timing.json 失败: %s", e)
        d = None
    if not isinstance(d, dict):
        return
    with _lock:
        _STATE["gate_th"] = float(d.get("gate_th") or 0.35)
        _STATE["gid"] = d.get("gid") if isinstance(d.get("gid"), dict) else {}


def _save(force=False):
    """节流落盘（30s 一次）；force 用于阈值变更/等待设定这类关键状态。"""
    now = time.time()
    if not force and now - _last_save[0] < 30.0:
        return
    _last_save[0] = now
    try:
        with _lock:
            snap = {"gate_th": _STATE["gate_th"], "gid": _STATE["gid"]}
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp.%s" % os.getpid())
        tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except Exception as e:  # noqa: BLE001
        log.debug("写 timing.json 失败（不影响主流程）: %s", e)


# ---------------- 1. reply_likelihood（纯函数） ----------------
# MaiBot reply_likelihood 权重（研究文档 §1.2 权重表原值照录；离线拟合产物，可重拟合只改表）
W = {
    "intercept": -1.2005,
    "mention_bot": 1.5852,
    "at_other": -1.1157,
    "question": 0.4807,
    "placeholder_only": -1.0281,
    "recent_self_ratio": 2.6525,
    "recent_count": -0.0367,     # 每条
    "secs_since_self": 0.0571,   # ×log1p(距上次发言秒数)
    "pending_count": 0.1012,     # 每条
}
RECENT_WINDOW = 300.0        # 近 5 分钟窗口（MaiBot RECENT_PRESENCE_WINDOW_SECONDS）
MAX_PENDING = 20             # 批内条数封顶
MIN_WINDOW_SCORE_COUNT = 20  # 滑窗样本不足此数 → 退回静态阈值

_Q_RE = re.compile(r"[?？]|(?:吗|呢)[\s。!！~…、,，]*$")
_AT_RE = re.compile(r"@\S{1,20}")


def _sigmoid(x):
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def reply_likelihood(text, pending_count, recent):
    """纯函数：logit = 截距 + Σ权重×特征 → sigmoid 概率（0~1，越高越值得判）。

    text          批内消息拼文
    pending_count 批内条数（内部封顶 MAX_PENDING）
    recent        {"self_ratio": 近5分钟bot发言占比, "count": 近5分钟消息数,
                   "secs_since_self": 距上次bot发言秒数（没说过用 3600）}
    """
    text = str(text or "")
    names = []
    try:
        names = list(_names() or []) if _names else []
    except Exception:  # noqa: BLE001
        names = []
    mention = any(n and n in text for n in names if n)
    at_other = (not mention) and bool(_AT_RE.search(text))
    question = bool(_Q_RE.search(text))
    ph = False
    if _is_placeholder:
        try:
            ph = bool(text.strip()) and all(_is_placeholder(str(t or "")) for t in text.split())
        except Exception:  # noqa: BLE001
            ph = False
    pending = max(0, min(int(pending_count or 0), MAX_PENDING))
    r = recent or {}
    logit = (W["intercept"]
             + W["mention_bot"] * (1.0 if mention else 0.0)
             + W["at_other"] * (1.0 if at_other else 0.0)
             + W["question"] * (1.0 if question else 0.0)
             + W["placeholder_only"] * (1.0 if ph else 0.0)
             + W["recent_self_ratio"] * float(r.get("self_ratio") or 0.0)
             + W["recent_count"] * float(r.get("count") or 0.0)
             + W["secs_since_self"] * math.log1p(max(0.0, float(r.get("secs_since_self") or 0.0)))
             + W["pending_count"] * pending)
    return _sigmoid(logit)


def _recent(gid):
    """近 5 分钟的现场特征：消息数 / bot 发言占比 / 距上次 bot 发言秒数。"""
    now = time.time()
    self_qq = str(_self_id() or "") if _self_id else ""
    total = selfn = 0
    last_self = 0.0
    try:
        items = list(_context_items(gid) or []) if _context_items else []
    except Exception:  # noqa: BLE001
        items = []
    for it in items:
        t = float((it or {}).get("t") or 0)
        if now - t > RECENT_WINDOW:
            continue
        total += 1
        if self_qq and str((it or {}).get("qq") or "") == self_qq:
            selfn += 1
            if t > last_self:
                last_self = t
    return {"count": total,
            "self_ratio": (selfn / total) if total else 0.0,
            "secs_since_self": (now - last_self) if last_self else 3600.0}


# ---------------- 2. dynamic_gate（自校准，只收紧不放宽） ----------------
def _static_threshold():
    return max(0.0, min(1.0, float(_cfgget("likelihood_min", 0.35))))


def _evaluate():
    """1h 滑窗校准。返回 (threshold, calibrated?)。

    目标：实际开口次数 ≈ Σ概率 × talk_value。实际偏多 → 门槛 +0.05（封 0.9）；
    偏少 → 向静态阈值缓退 -0.02，**永不低于静态**（只收紧不放宽的正解：
    校准只能让 bot 比静态阈值更谨慎，不能比主人设的基线更话痨）。
    样本 < MIN_WINDOW_SCORE_COUNT 条 → 直接用静态阈值。
    """
    now = time.time()
    static = _static_threshold()
    talk_value = max(0.0, float(_cfgget("talk_value", 1.0)))
    with _lock:
        rounds = [r for r in _STATE["rounds"] if now - float(r.get("t") or 0) < 3600.0]
        sends = [t for t in _STATE["sends"] if now - float(t or 0) < 3600.0]
        _STATE["rounds"], _STATE["sends"] = rounds[-400:], sends[-400:]
        if len(rounds) < MIN_WINDOW_SCORE_COUNT:
            _STATE["gate_th"] = static
            return static, False
        expected = sum(float(r.get("p") or 0.0) for r in rounds) * talk_value
        actual = len(sends)
        th = float(_STATE.get("gate_th") or static)
        if actual > expected + 0.5:
            th = min(0.9, th + 0.05)      # 说多了 → 收紧
        elif actual < expected - 0.5:
            th = max(static, th - 0.02)   # 说少了 → 只能退回静态，不许更话痨
        th = max(static, min(0.9, th))
        changed = abs(th - float(_STATE.get("gate_th") or 0)) > 1e-9
        _STATE["gate_th"] = th
    if changed:
        _save(force=True)
        log.info("时机额度自校准：门槛 -> %.2f（1h 预计 %.1f / 实际 %d）", th, expected, actual)
    return th, True


def _backoff_window(streak):
    """连续 silent streak（≥2）→ 15s × 2^(n-2)，封 backoff_cap。"""
    cap = max(30, int(_cfgget("backoff_cap", 300)))
    n = max(2, int(streak))
    return float(min(cap, 15 * (2 ** (n - 2))))


# ---------------- 对外主入口 ----------------
def should_judge(gid, items, force=False):
    """合批到点、过了现有全部闸门之后问一句：**值得再花一次 LLM 判定吗？**

    返回 (要不要, 理由)。总闸关 → 恒 (True, "时机层未启用")。
    force=True（主人发言/被点名）→ 直接放行（但照常记账，供校准用）。
    """
    if not enabled():
        return True, "时机层未启用"
    if not items:
        return True, ""
    now = time.time()
    text = " ".join(str((i or {}).get("text") or "") for i in items)
    p = reply_likelihood(text, len(items), _recent(gid))
    with _lock:
        st = _STATE["gid"].setdefault(str(gid), {})
        _STATE["rounds"].append({"t": now, "p": round(float(p), 4)})
        del _STATE["rounds"][:-400]
    if force:
        return True, "点名/主人（时机门不拦）"
    # 积压绕过：热闹来了，退避让路（§1.5 no_action_backoff_bypass_pending_count=6 同值）
    bypass = max(1, int(_cfgget("bypass_pending", 6)))
    streak = int(st.get("silent") or 0)
    if streak >= 2:
        left = _backoff_window(streak) - (now - float(st.get("last_silent") or 0))
        if left > 0 and len(items) < bypass:
            return False, "时机退避中（连续 %d 次没话说，再等 %.0f 秒）" % (streak, left)
    th, cal = _evaluate()
    if p < th:
        return False, "时机概率 %.2f < 门槛 %.2f%s" % (
            p, th, "（自校准）" if cal else "")
    return True, "时机概率 %.2f" % p


def note_silent(gid):
    """插话模型回 [SILENT] → 退避 +1（关闸时是 no-op，绝不影响旧行为）。"""
    if not enabled():
        return
    with _lock:
        st = _STATE["gid"].setdefault(str(gid), {})
        st["silent"] = min(10, int(st.get("silent") or 0) + 1)
        st["last_silent"] = time.time()
    _save()


def note_reply(gid):
    """任何回复/@/主人发言 → 退避清零 + wait 唤醒（「闭嘴但被点名立刻醒」）。"""
    if not enabled():
        return
    with _lock:
        st = _STATE["gid"].setdefault(str(gid), {})
        changed = bool(st.get("silent")) or bool(st.get("wait_until"))
        st["silent"] = 0
        st["wait_until"] = 0
        _STATE["sends"].append(time.time())
        del _STATE["sends"][:-400]
    if changed:
        _save(force=True)


# ---------------- 4. wait / stop 出口 ----------------
# ⚠️ 秒数用 \d{1,9}（不是 1,4）：要能匹配 [WAIT:99999] 这类超限值，再封顶到 wait_max；
#    限死 4 位会让整个标签不匹配 → 模型输出原样进正文（2026-11-04 测试抓到）。
_WAIT_RE = re.compile(r"^\s*\[\s*WAIT\s*[:：]\s*(\d{1,9})\s*\]\s*", re.IGNORECASE)


def parse_wait(ans, wait_max=None):
    """解析模型输出的 [WAIT:秒]。返回 (秒数, 剩余文本)。

    · 剩余文本非空 → 视为它其实想说那句话：(0, rest)；
    · wait_max ≤ 0（功能关）或没匹配 → (0, 原文)；
    · 秒数被 wait_max 封顶。
    """
    ans = str(ans or "").strip()
    wm = int(wait_max if wait_max is not None else _cfgget("wait_max", 180))
    m = _WAIT_RE.match(ans)
    if not m or wm <= 0:
        return 0, ans
    rest = ans[m.end():].strip()
    if rest:
        return 0, rest
    return max(1, min(int(m.group(1)), wm)), ""


def set_wait(gid, seconds):
    if not enabled():
        return
    with _lock:
        st = _STATE["gid"].setdefault(str(gid), {})
        st["wait_until"] = time.time() + max(1, int(seconds))
    _save(force=True)
    log.info("时机等待[%s]: %ds 后再看一眼", gid, int(seconds))


def wait_remaining(gid):
    if not enabled():
        return 0.0
    with _lock:
        st = _STATE["gid"].get(str(gid)) or {}
        return max(0.0, float(st.get("wait_until") or 0.0) - time.time())


def note_wait_flushed(gid):
    """wait 到点重判后若仍决定不说话 → 按一次 silent 记账（别让它无限等下去）。"""
    note_silent(gid)


# ---------------- 冷场开场的 wait 递增（到期唤醒由 60s tick 天然实现） ----------------
def opener_gap_multiplier(gid):
    """[SILENT] 开场越连续，下次尝试间隔越久（×2 递增，封 4 倍）。总闸关=1.0。"""
    if not enabled() or not bool(_cfgget("opener_backoff", True)):
        return 1.0
    with _lock:
        st = _STATE["gid"].get(str(gid)) or {}
        n = int(st.get("opener_silent") or 0)
    return min(4.0, 2.0 ** n) if n > 0 else 1.0


def note_opener(gid, spoke):
    """开场成功 spoke=True 清零递增；被 [SILENT] 递增。"""
    if not enabled():
        return
    with _lock:
        st = _STATE["gid"].setdefault(str(gid), {})
        if spoke:
            if st.get("opener_silent"):
                st["opener_silent"] = 0
                _save(force=True)
        else:
            st["opener_silent"] = min(4, int(st.get("opener_silent") or 0) + 1)
            _save()
