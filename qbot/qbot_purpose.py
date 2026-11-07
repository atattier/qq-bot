# -*- coding: utf-8 -*-
"""主动性层（2026-11-04 人味阶段 C）：purpose-engine 的 Python 重写。

来源：docs/人味阶段研究-2026-11-03.md §4（SurfCore purpose-engine）+
docs/人味阶段C设计草案-2026-11-04.md 与主人拍板的默认值。
**照算法重写，不复制 AGPL 源码。**

核心思想：主动开口不再「定时现编」，而是从 goal（目的）里长出来：
  · goal 必须有来源、need、置信度、价值、风险、状态、时间戳和**来源证据**；
  · 节奏与内容解耦：先按固定节奏（2.5~5h + 每日上限 + 安静时段）决定「允许开口」，
    再在 goal 池里竞争选出「说什么」；
  · 模型有明确 [SILENT] 出口：翻完素材没想说的就跳过，不算失败；
  · 素材归属写清楚：记忆卡的旧事是**它自己记得的事**，群聊观察是**群友的事**
    （observation 在 C1 只做 shadow，不进正式池）。

🔴 权威关系（不许颠倒）：
    mood 既有硬闸 → purpose 候选/调度闸 → timing 开场退避 → LLM → 现有发送闸。
    · 本模块不新增任何发送通道，只返回文本，由 bot 走 send_bubbles；
    · 不新增 sim_bias 硬阈值（它只作为软权重）；
    · `purpose.enabled` 默认 False：关 = 现有 idle_opener_tick 原样。

状态落盘：state/purpose.json（原子写 + .prev 回退；写失败进入只读模式）。
"""
import json
import logging
import math
import os
import random
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("qqbot")

BASE = Path(__file__).resolve().parent.parent
STATE_FILE = BASE / "state" / "purpose.json"

CFG = None
_lock = threading.RLock()
_READ_ONLY = False

# ---- 宿主钩子（未 bind 时安全降级）----
_memory_candidates = None      # (gid) -> [{"id","text","importance","clarity","createdAt"}]
_purpose_seen = None           # (text) -> bool
_purpose_mark = None           # (text) -> None
_touch_ids = None              # ([id]) -> None
_chat = None                   # (msgs) -> (raw, ans)
_system_prompt = None          # (gid) -> str
_is_placeholder = None         # (text) -> bool

# 默认值（与主人拍板一致；config 里 purpose 段可逐项覆盖）
DEFAULTS = {
    "enabled": False,
    "observe": True,           # 观察采集（shadow）：与「要不要开口」解耦，默认只记录不说话
    "daily_cap": 4,            # 每日「成功主动发言」上限
    "attempt_cap": 6,          # 每日尝试上限（SILENT/失败也算尝试）
    "gap_min_h": 2.5,          # 两次尝试之间的随机间隔下限（小时）
    "gap_max_h": 5.0,          # 上限
    "quiet": "23:30-09:00",    # 安静时段（本地时间，支持跨午夜）
    "owner_active_min": 30,    # 主人最近说过话的阈值（分钟）
    "owner_fallback_h": 24,    # 主人活跃闸连续拦截多久后自动放行
    "pool_max": 12,            # 每群目标池总上限
    "need_max": 3,             # 每个 need 的上限
    "active_max": 6,           # 可参与竞争（active）的上限
    "memory_ttl_days": 7,      # 记忆卡来源 TTL
    "shadow_ttl_days": 3,      # observation shadow TTL
    "per_goal_attempts": 3,    # 单个 goal 最多试几次
    "send_retry_min": 15,      # 发送失败后重试间隔（分钟）
    "mood_weight": True,       # sim_bias 作为软权重（不设硬阈值）
}

_STATE = {"gid": {}}
_last_save = [0.0]
_SILENT_RE = re.compile(r"^\s*\[?\s*SILENT\s*\]?\s*$", re.IGNORECASE)
_NEED_INVITE_RE = re.compile(r"(下次|一起|说好|约好|约个|改天|回头|记得|别忘)")
_NEED_ASK_RE = re.compile(r"[?？]|(?:吗|呢)\s*$|怎么|为什么|多少|哪个|能不能")
# 隐私/凭据类内容不进 goal（记忆卡入库时脱敏过，这里再兜一道；
# 手机号/身份证/token/密码/验证码一律不主动提）
_PRIV_RE = re.compile(r"1[3-9]\d{9}|\d{17}[\dXx]|"
                      r"token|password|passwd|secret|密码|验证码|身份证",
                      re.IGNORECASE)


# ---------------------------------------------------------------- 绑定 / 配置
def bind(cfg=None, memory_candidates=None, purpose_seen=None, purpose_mark=None,
         touch_ids=None, chat=None, system_prompt=None, is_placeholder=None,
         state_dir=None):
    """bot.py 启动时调用；cfg 传活引用。state_dir 给了就读写 purpose.json。"""
    global CFG, _memory_candidates, _purpose_seen, _purpose_mark, _touch_ids
    global _chat, _system_prompt, _is_placeholder, STATE_FILE
    if cfg is not None:
        CFG = cfg
    if memory_candidates is not None:
        _memory_candidates = memory_candidates
    if purpose_seen is not None:
        _purpose_seen = purpose_seen
    if purpose_mark is not None:
        _purpose_mark = purpose_mark
    if touch_ids is not None:
        _touch_ids = touch_ids
    if chat is not None:
        _chat = chat
    if system_prompt is not None:
        _system_prompt = system_prompt
    if is_placeholder is not None:
        _is_placeholder = is_placeholder
    if state_dir:
        STATE_FILE = Path(state_dir) / "purpose.json"
        load()


def enabled():
    return bool(_cfgget("enabled", False))


def observation_enabled():
    """observation 采集开关：**与开口解耦**。

    · `purpose.enabled=False`（默认）+ `purpose.observe=True`（默认）
      → 只把群友的计划/偏好记进 shadow 池，绝不开口；
    · 关掉 observe → 连记录都不做（真正的零副作用）。
    """
    return bool(_cfgget("observe", True)) or enabled()


def read_only():
    return _READ_ONLY


def _cfgget(key, default=None):
    v = ((CFG or {}).get("purpose") or {}).get(key)
    if v is not None:
        return v
    if key in DEFAULTS:
        return DEFAULTS[key]
    return default


# ---------------------------------------------------------------- 持久化
def load():
    """读状态；坏文件退回 .prev，再失败空池启动（绝不因状态文件影响 bot）。"""
    global _READ_ONLY
    for p in (STATE_FILE, STATE_FILE.with_name(STATE_FILE.name + ".prev")):
        try:
            if not p.exists():
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("gid"), dict):
                with _lock:
                    _STATE["gid"] = d["gid"]
                if p != STATE_FILE:
                    log.warning("purpose.json 损坏，已从 .prev 恢复")
                _READ_ONLY = False
                return
        except Exception as e:  # noqa: BLE001
            log.warning("读 purpose 状态失败(%s): %s", p.name, e)
    with _lock:
        _STATE["gid"] = {}
    _READ_ONLY = False


def _save(force=False):
    """原子写 + .prev；写失败 → 只读模式（主流程继续，不再尝试落盘）。"""
    global _READ_ONLY
    if _READ_ONLY:
        return False
    now = time.time()
    if not force and now - _last_save[0] < 5.0:
        return True
    _last_save[0] = now
    try:
        with _lock:
            snap = {"gid": _STATE["gid"]}
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if STATE_FILE.exists():
            try:
                STATE_FILE.replace(STATE_FILE.with_name(STATE_FILE.name + ".prev"))
            except Exception:  # noqa: BLE001
                pass
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp.%s" % os.getpid())
        tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
        return True
    except Exception as e:  # noqa: BLE001
        _READ_ONLY = True
        log.warning("写 purpose.json 失败，进入只读模式（不影响 bot）: %s", e)
        return False


# ---------------------------------------------------------------- 时间 / 安静时段
def day_key(ts):
    return time.strftime("%Y%m%d", time.localtime(ts))


def _parse_hhmm(s):
    try:
        h, m = str(s).strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:  # noqa: BLE001
        return None


def quiet_range(spec):
    """'23:30-09:00' → (start_min, end_min)；非法返回 None。"""
    try:
        a, b = str(spec or "").split("-", 1)
        x, y = _parse_hhmm(a), _parse_hhmm(b)
        if x is None or y is None:
            return None
        return x, y
    except Exception:  # noqa: BLE001
        return None


def in_quiet(now, spec=None):
    r = quiet_range(spec if spec is not None else _cfgget("quiet", "23:30-09:00"))
    if not r:
        return False
    a, b = r
    lt = time.localtime(now)
    cur = lt.tm_hour * 60 + lt.tm_min
    if a == b:
        return False
    if a < b:
        return a <= cur < b
    return cur >= a or cur < b          # 跨午夜（23:30~09:00）


def next_quiet_end(now, spec=None):
    """安静时段结束的时间戳（不在安静中则返回 now）。"""
    if not in_quiet(now, spec):
        return now
    r = quiet_range(spec if spec is not None else _cfgget("quiet", "23:30-09:00"))
    if not r:
        return now
    end_min = r[1]
    lt = time.localtime(now)
    end = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                       end_min // 60, end_min % 60, 0, 0, 0, -1))
    if end <= now:
        end += 86400
    return end


# ---------------------------------------------------------------- 调度
def plan_next_fire(now, backoff=1.0, rng=None):
    """下一次允许尝试的时间：uniform(gap_min,gap_max) × 开场退避；避开安静时段。"""
    rng = rng or random
    lo = max(0.1, float(_cfgget("gap_min_h", 2.5)))
    hi = max(lo, float(_cfgget("gap_max_h", 5.0)))
    mult = max(1.0, float(backoff or 1.0))
    fire = now + rng.uniform(lo, hi) * 3600.0 * mult
    if in_quiet(fire):
        fire = next_quiet_end(fire) + rng.uniform(0, 20 * 60)
    return fire


def _roll_day(st, now):
    d = day_key(now)
    if st.get("day") != d:
        st["day"] = d
        st["success"] = 0
        st["attempts"] = 0


def _normalize_fire(st, now, backoff=1.0):
    """重启恢复：停机很久只补一枪（最早 now+2min），不集中补发。"""
    fire = float(st.get("next_fire_at") or 0)
    if not fire:
        st["next_fire_at"] = plan_next_fire(now, backoff)
        return "first"
    if fire < now - 120:
        st["next_fire_at"] = now + 120
        return "catchup"
    return "ok"


def classify_owner_state(last_owner_ts, now, threshold_sec=1800):
    """主人三态：active / idle / unknown（没数据）。"""
    try:
        ts = float(last_owner_ts or 0)
    except (TypeError, ValueError):
        return "unknown"
    if ts <= 0:
        return "unknown"
    return "active" if (now - ts) < float(threshold_sec) else "idle"


# ---------------------------------------------------------------- goal 模型
def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(v)))


def infer_need(text):
    t = str(text or "")
    if _NEED_INVITE_RE.search(t):
        return "invite"
    if _NEED_ASK_RE.search(t):
        return "ask"
    return "share"


def _base_goal(gid, source, need, text, source_id, now, confidence, value, risk, ttl_days):
    return {
        "id": "pg_%s_%s" % (gid, source_id or int(now * 1000)),
        "gid": int(gid),
        "source": source,
        "source_id": str(source_id or ""),
        "need": need,
        "text": str(text or "").strip(),
        "evidence": {"source": source, "source_id": str(source_id or ""),
                     "text": str(text or "")[:80], "created_at": float(now)},
        "confidence": _clamp(confidence),
        "value": _clamp(value),
        "risk": _clamp(risk),
        "status": "active" if source == "memory" else "shadow",
        "created_at": float(now),
        "last_asked_at": 0.0,
        "expires_at": float(now) + float(ttl_days) * 86400.0,
        "attempts": 0,
    }


def goal_from_memory(cand, now, gid=None):
    """记忆卡候选 → goal。不合格（模糊/太旧/太轻/没证据）返回 None。"""
    if not isinstance(cand, dict):
        return None
    text = str(cand.get("text") or "").strip()
    if not text:
        return None
    if _PRIV_RE.search(text):
        return None
    if str(cand.get("clarity") or "") not in ("vivid", "clear"):
        return None
    try:
        imp = float(cand.get("importance") or 0)
    except (TypeError, ValueError):
        return None
    if imp < 14:
        return None
    try:
        created = float(cand.get("createdAt") or 0)
    except (TypeError, ValueError):
        created = 0.0
    ttl = float(_cfgget("memory_ttl_days", 7))
    if created and (now - created) / 86400.0 > ttl:
        return None
    cid = cand.get("id")
    if cid is None:
        return None                   # 没有来源证据的 goal 不许进池
    # 置信度：importance 14 → ~0.58（提示词会加「试探/好奇」语气）；
    # 24 → ~0.81；30 → ~0.9（上限）。别让普通素材一上来就像「确定的事实」。
    conf = 0.28 + 0.02 * min(imp, 30.0) + (0.05 if cand.get("clarity") == "vivid" else 0.02)
    value = min(1.0, imp / 32.0)
    return _base_goal(int(gid or cand.get("gid") or 0), "memory", infer_need(text), text,
                      cid, now, conf, value, 0.15, ttl)


_OBS_RE = re.compile(r"(想|打算|准备|计划|下次|改天|一起|约好|约个|希望|要不|考虑|推荐)")
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)


def looks_like_observation(text):
    """群友这句话像不像「可观察的话题」（计划/偏好/约定）。

    observation 在 C1 只做 shadow：这里只负责**筛掉噪音**（链接/口令/隐私/太短太长），
    命中的句子进 shadow 池，永不直接开口。
    """
    t = str(text or "").strip()
    if not (6 <= len(t) <= 60):
        return False
    if _URL_RE.search(t) or _PRIV_RE.search(t):
        return False
    if t.startswith("/") or t.startswith("画图") or t.startswith("反推"):
        return False
    if _is_placeholder:
        try:
            if _is_placeholder(t):
                return False
        except Exception:  # noqa: BLE001
            pass
    return bool(_OBS_RE.search(t))


def goal_from_observation(gid, text, source_id, now):
    """群聊观察 → **shadow** goal（C1 只记录，不参与竞争、不进正式池）。"""
    text = str(text or "").strip()
    if not text or source_id is None or _PRIV_RE.search(text):
        return None
    return _base_goal(gid, "observation", infer_need(text), text, source_id, now,
                      0.35, 0.4, 0.30, float(_cfgget("shadow_ttl_days", 3)))


# ---------------------------------------------------------------- 去重 / 权重 / 池
_TOK_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _tokens(text):
    return set(_TOK_RE.findall(str(text or "").lower()))


def text_sim(a, b):
    """token Jaccard（中文按字切；短句够用，不引第三方库）。"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def dedup_ok(goal, pool, recent_texts=(), use_seen=True):
    """多层去重：同来源 / 与池内高相似 / 与最近发过的高相似 / 去重账本。"""
    if not goal:
        return False
    for g in pool or []:
        if g.get("source_id") and g.get("source_id") == goal.get("source_id"):
            return False
        if text_sim(g.get("text"), goal.get("text")) >= 0.80:
            return False
        if g.get("need") == goal.get("need") and \
                text_sim(g.get("text"), goal.get("text")) >= 0.55:
            return False
    for t in recent_texts or []:
        if text_sim(t, goal.get("text")) >= 0.55:
            return False
    if use_seen and _purpose_seen:
        try:
            if _purpose_seen(goal.get("text")):
                return False
        except Exception:  # noqa: BLE001
            pass
    return True


def mood_multiplier(bias=None):
    """sim_bias 只做软权重（0.75~1.25），不是硬闸。"""
    if not bool(_cfgget("mood_weight", True)):
        return 1.0
    try:
        b = 0.5 if bias is None else _clamp(bias)
    except (TypeError, ValueError):
        b = 0.5
    return 0.75 + 0.5 * b


def goal_weight(goal, recent_needs=(), mood_bias=None):
    w = 0.55 + float(goal.get("confidence") or 0) * 0.7 \
        + float(goal.get("value") or 0) * 0.35 - float(goal.get("risk") or 0) * 0.25
    w *= 0.65 + 0.55 * 0.5          # drives 默认 0.5（C1 不接驱动源权重）
    rn = list(recent_needs or [])
    # 轮换只惩罚「又要说同一类」：最近 3 次都同 need，且当前 goal 也是这个 need
    if goal.get("need") == (rn[-1] if rn else None):
        if len(rn) >= 3 and len(set(rn[-3:])) == 1:
            w *= 0.72
        if len(rn) >= 2 and rn[-1] == rn[-2]:
            w *= 0.65
    if len(rn) >= 2 and len(set(rn[-2:])) == 1:
        if rn[-1] == "share":
            w *= 1.0 if goal.get("need") == "share" else 1.35
        elif rn[-1] in ("ask", "invite"):
            w *= 1.2 if goal.get("need") == "share" else 1.0
    w *= mood_multiplier(mood_bias)
    return max(0.0, w)


def prune_pool(goals, now, shadow=False):
    """TTL + 容量：总 ≤pool_max、每 need ≤need_max、active ≤active_max。

    先按 (confidence×value) 从高到低保留；shadow 单独一池、不参与 active 计数。
    """
    pool_max = int(_cfgget("pool_max", 12))
    need_max = int(_cfgget("need_max", 3))
    active_max = int(_cfgget("active_max", 6))
    alive, dead = [], []
    for g in goals or []:
        if not isinstance(g, dict):
            continue
        if float(g.get("expires_at") or 0) and now > float(g.get("expires_at") or 0):
            g["status"] = "expired"
            dead.append(g)
            continue
        alive.append(g)
    alive.sort(key=lambda g: float(g.get("confidence") or 0) * float(g.get("value") or 0),
               reverse=True)
    out, need_n, active_n = [], {}, 0
    for g in alive:
        if shadow:
            if len(out) < pool_max:
                out.append(g)
            continue
        if g.get("status") != "active":
            continue
        need = str(g.get("need") or "share")
        if need_n.get(need, 0) >= need_max:
            continue
        if active_n >= active_max:
            break
        out.append(g)
        need_n[need] = need_n.get(need, 0) + 1
        active_n += 1
    return out, dead


def pick_goal(goals, recent_needs=(), mood_bias=None, rng=None):
    """按权重轮盘赌（要随机，不要永远最高分）；没有可用 goal 返回 None。"""
    cand = [g for g in goals or [] if g.get("status") == "active" and g.get("source") == "memory"]
    if not cand:
        return None
    rng = rng or random
    weights = [goal_weight(g, recent_needs, mood_bias) for g in cand]
    total = sum(weights)
    if total <= 0:
        return rng.choice(cand)
    r = rng.random() * total
    acc = 0.0
    for g, w in zip(cand, weights):
        acc += w
        if r <= acc:
            return g
    return cand[-1]


# ---------------------------------------------------------------- 状态与决策
def _gstate(gid):
    key = str(int(gid))
    st = _STATE["gid"].setdefault(key, {
        "next_fire_at": 0.0, "day": "", "success": 0, "attempts": 0,
        "last_success_at": 0.0, "last_result": "",
        "blocked_since": 0.0, "owner_unknown": 0, "owner_fallback_until": 0.0,
        "goals": [], "shadow": [], "recent_needs": [], "recent_texts": [],
    })
    for k, v in (("goals", []), ("shadow", []), ("recent_needs", []), ("recent_texts", [])):
        if not isinstance(st.get(k), list):
            st[k] = v
    return st


def _refresh_goals(gid, st, now):
    """从记忆卡拉候选进池（每 tick 增量补；去重后裁剪容量）。"""
    if not _memory_candidates:
        return
    try:
        cands = _memory_candidates(gid) or []
    except Exception as e:  # noqa: BLE001
        log.debug("取记忆卡候选失败（不影响开场）: %s", e)
        return
    pool = st["goals"]
    for c in cands:
        try:
            g = goal_from_memory(c, now, gid)
            if g is None:
                continue
            g["gid"] = int(gid)
            if not dedup_ok(g, pool, st.get("recent_texts"), use_seen=True):
                continue
            pool.append(g)
        except Exception:  # noqa: BLE001
            continue
    st["goals"], _ = prune_pool(pool, now)
    st["shadow"], _ = prune_pool(st.get("shadow"), now, shadow=True)


def add_observation(gid, text, source_id, now=None):
    """observation shadow 入口（只记录，不参与开口；与总闸解耦）。"""
    if not observation_enabled():
        return None
    now = float(now or time.time())
    st = _gstate(gid)
    g = goal_from_observation(gid, text, source_id, now)
    if g is None or not dedup_ok(g, st["shadow"], st.get("recent_texts"), use_seen=False):
        return None
    st["shadow"].append(g)
    st["shadow"], _ = prune_pool(st["shadow"], now, shadow=True)
    _save()
    return g


def _result(st, reason, **extra):
    st["last_result"] = reason
    d = {"action": "skip", "reason": reason}
    d.update(extra)
    return d


def decide(gid, now=None, cold_minutes=0.0, owner_last_ts=0.0, backoff=1.0,
           mood_bias=None, ctx=""):
    """到点的判断：该不该开口、说哪个 goal。

    返回 {"action":"speak", "goal":…, "messages":…} 或 {"action":"skip","reason":…}。
    本函数**不发送**、不调 LLM；调用方负责 chat → parse_answer → send → finish。
    """
    if not enabled():
        return {"action": "skip", "reason": "目的层未启用"}
    now = float(now or time.time())
    st = _gstate(gid)
    _roll_day(st, now)
    _refresh_goals(gid, st, now)

    daily_cap = int(_cfgget("daily_cap", 4))
    attempt_cap = int(_cfgget("attempt_cap", 6))
    if int(st.get("success") or 0) >= daily_cap:
        _normalize_fire(st, now)
        return _result(st, "今日成功额度已满(%d/%d)" % (st["success"], daily_cap))
    if int(st.get("attempts") or 0) >= attempt_cap:
        _normalize_fire(st, now)
        return _result(st, "今日尝试次数已满(%d/%d)" % (st["attempts"], attempt_cap))
    if in_quiet(now):
        return _result(st, "安静时段")

    # 主人活跃闸（只拦非主人来源；unknown 不拦，但必须记账）
    ost = classify_owner_state(owner_last_ts, now,
                               float(_cfgget("owner_active_min", 30)) * 60.0)
    prev_ost = st.get("last_owner_state")
    st["last_owner_state"] = ost
    if ost == "unknown" and prev_ost != "unknown":
        st["owner_unknown"] = int(st.get("owner_unknown") or 0) + 1
    if ost == "active":
        fb_until = float(st.get("owner_fallback_until") or 0)
        if fb_until > now:
            pass                                  # 24h 自愈窗口内：放行
        else:
            blocked = float(st.get("blocked_since") or 0) or now
            st["blocked_since"] = blocked
            limit = float(_cfgget("owner_fallback_h", 24)) * 3600.0
            if now - blocked >= limit:
                st["owner_fallback_until"] = now + 3600.0
                st["blocked_since"] = 0.0
                log.warning("主人活跃闸连续 %.0f 小时导致零开口 → 临时放行 1 小时（仅本闸）",
                            (now - blocked) / 3600.0)
            else:
                _save()
                return _result(st, "主人在场（%s，%.0f 分钟前说过话）"
                               % (ost, (now - float(owner_last_ts)) / 60.0),
                               owner_state=ost)
    else:
        st["blocked_since"] = 0.0

    kind = _normalize_fire(st, now, backoff)
    if kind == "catchup":
        _save()
        return _result(st, "停机恢复：最早 2 分钟后再看")
    if kind == "first":
        _save()
        return _result(st, "首次调度：已排下一次")
    if now < float(st.get("next_fire_at") or 0):
        return _result(st, "未到下一次时间（还剩 %.0f 分钟）"
                       % ((float(st["next_fire_at"]) - now) / 60.0))

    goal = pick_goal(st["goals"], st.get("recent_needs"), mood_bias)
    if not goal:
        st["next_fire_at"] = now + 15 * 60
        _save()
        return _result(st, "没有可用素材（不硬编）")

    return {
        "action": "speak", "reason": "选中 %s/%s" % (goal.get("need"), goal.get("id")),
        "goal": goal, "goal_id": goal["id"], "gid": int(gid),
        "messages": _build_messages(gid, goal, st, cold_minutes, ctx),
    }


def _build_messages(gid, goal, st, cold_minutes, ctx):
    sys_txt = ""
    try:
        if _system_prompt:
            sys_txt = _system_prompt(gid) or ""
    except Exception:  # noqa: BLE001
        sys_txt = ""
    src = goal.get("source")
    if src == "memory":
        attrib = "这件事是你自己记得的旧事（不是你刚编的，也不是群友让你说的），像忽然想起来一样提一句。"
    else:
        attrib = ("这是群友聊过的事，不是你的经历——只可以顺着它接话，"
                  "不要写成「我也去过/我也经历过」。")
    tips = []
    if float(goal.get("confidence") or 0) < 0.55:
        tips.append("你记得不深，说得像试探/好奇，不要像宣布结论。")
    if float(goal.get("risk") or 0) >= 0.5:
        tips.append("这件事有点敏感，只能先问主人能不能，不要自己拍板。")
    recent = [t for t in (st.get("recent_texts") or []) if t]
    if recent:
        tips.append("最近你说过：「%s」——别重复这些内容。"
                    % "」「".join(str(t)[:30] for t in recent[-3:]))
    task = (
        "\n\n【现在的任务】群里已经冷场 %.0f 分钟没人说话了。"
        "你想起一件事：\n「%s」\n%s\n"
        "用你的口吻主动接一句（30字内、口语）。%s"
        "只输出要发的那一句话；翻完真的没想说的就输出 [SILENT]（不算失败，安静也是选择）。"
        "别问「有人在吗」「怎么没人说话」，别提自己是机器人或AI，不许编素材里没有的细节。"
        % (cold_minutes, goal.get("text") or "", attrib,
           ("\n" + "\n".join(tips)) if tips else "")
    )
    user = ctx or "（群里最近没聊过什么，自由找话头）"
    return [{"role": "system", "content": (sys_txt + task).strip()},
            {"role": "user", "content": user}]


def parse_answer(ans):
    """(kind, text)：silent / text。模型说 [SILENT] 或空回都算主动跳过。"""
    a = str(ans or "").strip()
    if not a:
        return "silent", ""
    if _SILENT_RE.match(a) or "SILENT" in a.upper():
        return "silent", ""
    if _is_placeholder:
        try:
            if _is_placeholder(a):
                return "silent", ""
        except Exception:  # noqa: BLE001
            pass
    return "text", a


def finish(gid, goal_id, sent, silent=False, now=None, backoff=1.0, mood_bias=None):
    """一次开口尝试的结果记账。sent=False 且 silent=True = 模型主动跳过。"""
    now = float(now or time.time())
    st = _gstate(gid)
    goal = None
    for g in st["goals"]:
        if g.get("id") == goal_id:
            goal = g
            break
    st["attempts"] = int(st.get("attempts") or 0) + 1
    per_goal = int(_cfgget("per_goal_attempts", 3))
    if goal is not None:
        goal["attempts"] = int(goal.get("attempts") or 0) + 1
    if sent:
        st["success"] = int(st.get("success") or 0) + 1
        st["last_success_at"] = now
        st["blocked_since"] = 0.0
        if goal is not None:
            goal["status"] = "done"
            goal["confidence"] = _clamp(float(goal.get("confidence") or 0) + 0.03, 0, 0.95)
            goal["last_asked_at"] = now
            st["recent_needs"] = (st.get("recent_needs") or [])[-2:] + [goal.get("need")]
            st["recent_texts"] = (st.get("recent_texts") or [])[-2:] + [goal.get("text")]
            _mark_used(goal)
        st["next_fire_at"] = plan_next_fire(now, backoff)
        st["last_result"] = "sent"
    elif silent:
        if goal is not None:
            goal["confidence"] = _clamp(float(goal.get("confidence") or 0) - 0.03, 0.05, 1.0)
            if int(goal.get("attempts") or 0) >= per_goal:
                goal["status"] = "expired"
        st["next_fire_at"] = plan_next_fire(now, backoff)
        st["last_result"] = "silent"
    else:
        # 发送失败：不占成功额度；短间隔重试，单 goal 超过次数就放弃
        st["last_result"] = "send_failed"
        if goal is not None and int(goal.get("attempts") or 0) >= per_goal:
            goal["status"] = "expired"
            st["next_fire_at"] = plan_next_fire(now, backoff)
        else:
            st["next_fire_at"] = now + float(_cfgget("send_retry_min", 15)) * 60.0
    _save(force=True)
    return {"success": st["success"], "attempts": st["attempts"],
            "next_fire_at": st["next_fire_at"], "result": st["last_result"]}


def _mark_used(goal):
    """记忆卡话头真的发出去了 → 去重账本 + 复习效应（best effort）。"""
    try:
        if _purpose_mark:
            _purpose_mark(goal.get("text") or "")
        cid = (goal.get("evidence") or {}).get("source_id")
        if cid is not None and str(cid).strip() and _touch_ids:
            try:
                _touch_ids([int(cid)])
            except (TypeError, ValueError):
                _touch_ids([cid])
    except Exception as e:  # noqa: BLE001
        log.debug("标记记忆卡话头已用失败：%s", e)


# ---------------------------------------------------------------- 观测（只读）
def snapshot(gid):
    """给控制台/日志看的只读快照（不含发送动作）。"""
    st = _gstate(gid)
    return {
        "next_fire_at": st.get("next_fire_at"),
        "success": st.get("success"), "attempts": st.get("attempts"),
        "last_result": st.get("last_result"),
        "goals": len([g for g in st["goals"] if g.get("status") == "active"]),
        "shadow": len(st.get("shadow") or []),
        "recent_needs": list(st.get("recent_needs") or []),
        "owner_unknown": st.get("owner_unknown"),
    }
