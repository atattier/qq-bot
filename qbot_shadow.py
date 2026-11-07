# -*- coding: utf-8 -*-
"""M1/M2 影子核心：Turn → 判断 → 落审计日志，绝不发送。

总闸：config.json `arch.shadow`（默认 false）；`QBOT_ARCH_SHADOW=1/0` 可临时覆盖。
M2 上下文：宿主可注入 Context Assembler（`arch.context=assembler`）；不注入 = M1
最小上下文。回滚：`arch.shadow` 设回 false，或 `arch.context=legacy`。
旧消息链路一行不改。

边界：
  - 不调用发送函数、不执行工具、不写记忆；tool_call 只记录“会调用什么”；
  - 工作线程里跑模型，消息线程只做一次入队（不改变线上延迟）；
  - 日志落 `state/shadow/decisions.jsonl`，测试态由 bot 绑到临时目录。
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from qbot_core import Core, Decision, Policy
from qbot_turn import Turn, make_turn

BASE = Path(__file__).resolve().parent
STATE_DIR = BASE / "state" / "shadow"
DECISIONS_FILE = STATE_DIR / "decisions.jsonl"

_LOCK = threading.Lock()
_CFG = {}
_LOG = None
_DECIDE = None
_TOOLS = []
_CORE = None
_ASSEMBLER = None
_RECENT_MAX = 8
_RECENT = defaultdict(lambda: deque(maxlen=_RECENT_MAX))
_QUEUE = queue.Queue(maxsize=500)
_THREAD = None
_LEGACY_GRACE_MS = 300
_STATS = {
    "observed": 0, "submitted": 0, "completed": 0, "dropped": 0, "errors": 0,
    "actions": {}, "diffs": {}, "outbound_seen": 0,
}
_LAST_OUTBOUND = {}


_UNSET = object()


def bind(cfg=None, state_dir=None, logger=None, decide=None, tools=None,
         max_queue=None, legacy_grace_ms=None, assembler=_UNSET):
    """宿主注入配置/模型调用/工具目录/上下文组装器。测试可反复 bind 到临时目录。

    传 assembler=None 显式回退到 M1 最小上下文（arch.context=legacy）。
    """
    global _CFG, STATE_DIR, DECISIONS_FILE, _LOG, _DECIDE, _TOOLS, _CORE
    global _QUEUE, _LEGACY_GRACE_MS, _ASSEMBLER
    if cfg is not None:
        _CFG = cfg
    if state_dir is not None:
        STATE_DIR = Path(state_dir)
        DECISIONS_FILE = STATE_DIR / "decisions.jsonl"
    if logger is not None:
        _LOG = logger
    if decide is not None:
        _DECIDE = decide
    if tools is not None:
        _TOOLS = list(tools)
    if max_queue is not None:
        # 工作线程一旦启动就绑定在旧队列上；这里换队列会让它永远等旧队列。
        # 生产只 bind 一次，测试反复 bind 时保留原队列，避免“入队了却没人处理”。
        if _THREAD is None or not _THREAD.is_alive():
            _QUEUE = queue.Queue(maxsize=max(1, int(max_queue)))
    if legacy_grace_ms is not None:
        _LEGACY_GRACE_MS = max(0, int(legacy_grace_ms))
    if assembler is not _UNSET:
        _ASSEMBLER = assembler
    _CORE = Core(_DECIDE, Policy(), assembler=_ASSEMBLER) if _DECIDE is not None else None


def _arch():
    return (_CFG or {}).get("arch") or {}


def reset():
    """测试/新会话用：清空计数、发送观测与跨轮媒体缓存，不碰已写日志。"""
    global _STATS
    with _LOCK:
        _STATS = {"observed": 0, "submitted": 0, "completed": 0, "dropped": 0,
                  "errors": 0, "actions": {}, "diffs": {}, "outbound_seen": 0}
        _LAST_OUTBOUND.clear()
        _RECENT.clear()


def enabled() -> bool:
    env = os.environ.get("QBOT_ARCH_SHADOW")
    if env is not None and str(env).strip() != "":
        return str(env).strip().lower() in ("1", "true", "yes", "on")
    return bool(_arch().get("shadow", False))


def note_outbound(session_key):
    """发送出口调用：只记时间，不改变发送。用于和旧路径做差异分类。"""
    if not enabled():
        return
    now = time.time()
    with _LOCK:
        _LAST_OUTBOUND[str(session_key)] = now
        _STATS["outbound_seen"] += 1


def _legacy_outbound(session_key, enqueued_at):
    with _LOCK:
        if _LAST_OUTBOUND.get(str(session_key), 0.0) >= enqueued_at:
            return True
    if _LEGACY_GRACE_MS:
        time.sleep(_LEGACY_GRACE_MS / 1000.0)
    with _LOCK:
        return _LAST_OUTBOUND.get(str(session_key), 0.0) >= enqueued_at


def _classify(d: Decision, legacy_outbound: bool) -> str:
    if d.action in ("error", "invalid"):
        return "error"
    if d.action == "tool_call":
        return "tool_proposal"
    if d.action in ("reply", "clarify", "refuse"):
        return "match_reply" if legacy_outbound else "shadow_speaks_legacy_silent"
    if d.action == "silent":
        return "match_silent" if not legacy_outbound else "shadow_silent_legacy_speaks"
    return "unknown"


def _append_record(rec):
    try:
        Path(DECISIONS_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(DECISIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as e:  # noqa: BLE001
        if _LOG:
            _LOG.warning("影子日志写入失败: %s", e)


def _remember_turn(turn):
    """M2：把本会话的 Turn 记进媒体索引与最近轮次（只在本进程内存里）。"""
    if _ASSEMBLER is None:
        return
    with _LOCK:
        d = _RECENT[str(turn.session.key)]
        if not d or d[-1].turn_id != turn.turn_id:
            d.append(turn)
    try:
        _ASSEMBLER.media_index.remember(turn)
    except Exception as e:  # noqa: BLE001
        if _LOG:
            _LOG.debug("影子媒体索引失败: %s", e)


def _recent_before(turn):
    """队列里排在本轮之前的同会话 Turn（不含本轮，也不含后到的）。"""
    with _LOCK:
        d = list(_RECENT.get(str(turn.session.key)) or ())
    out = []
    for t in d:
        if t.turn_id == turn.turn_id:
            break
        out.append(t)
    return out[-_RECENT_MAX:]


def _worker():
    while True:
        item = _QUEUE.get()
        try:
            if item is None:
                return
            turn, history, enqueued_at = item
            if _CORE is None:
                d = Decision(action="error", error="影子核心未绑定 decide")
            else:
                d = _CORE.run(turn, history=history, tools=_TOOLS,
                              recent_turns=_recent_before(turn))
            legacy = _legacy_outbound(turn.session.key, enqueued_at)
            diff = _classify(d, legacy)
            rec = {
                "schema_version": 1,
                "turn": turn.to_dict(),
                "decision": d.to_dict(),
                "legacy": {"outbound": bool(legacy),
                           "outbound_at": _LAST_OUTBOUND.get(turn.session.key, 0.0)},
                "diff": diff,
                "review": diff not in ("match_reply", "match_silent"),
                "written_at": time.time(),
            }
            _append_record(rec)
            with _LOCK:
                _STATS["completed"] += 1
                _STATS["actions"][d.action] = _STATS["actions"].get(d.action, 0) + 1
                _STATS["diffs"][diff] = _STATS["diffs"].get(diff, 0) + 1
                if d.action in ("error", "invalid"):
                    _STATS["errors"] += 1
        except Exception as e:  # noqa: BLE001
            with _LOCK:
                _STATS["errors"] += 1
            if _LOG:
                _LOG.warning("影子判断失败（不影响回复）: %s", e)
        finally:
            _QUEUE.task_done()


def _ensure_worker():
    global _THREAD
    with _LOCK:
        if _THREAD is None or not _THREAD.is_alive():
            _THREAD = threading.Thread(target=_worker, name="arch-shadow", daemon=True)
            _THREAD.start()
    return _THREAD


def observe_message(*, session_key, is_group, user_id, display_name="", role="member",
                    text="", imgs=(), quote="", qmid=0, at_self=False,
                    addressed=None, mid=None, event_time=None, history=(),
                    privileged=False, test_mute=False, silent=False):
    """Adapter 入口：只在总闸开时构造 Turn 并入队。永不抛到调用方。"""
    if not enabled():
        return False
    with _LOCK:
        _STATS["observed"] += 1
    if addressed is None:
        addressed = bool(at_self or not is_group)
    quote_text = str(quote or "")
    if quote_text.startswith("引用 "):
        quote_text = quote_text[3:]
    flags = {"privileged": bool(privileged), "test_mute": bool(test_mute),
             "silent": bool(silent)}
    turn = make_turn(
        adapter="napcat", event_id=str(mid) if mid not in (None, "") else None,
        session_kind="group" if is_group else "private", session_key=session_key,
        user_id=user_id, display_name=display_name, role=role,
        received_at=event_time, text=text, media_refs=imgs,
        quote_text=quote_text, quote_unresolved=bool(qmid and not quote_text),
        addressed=addressed, flags=flags,
    )
    return enqueue(turn, history=history)


def enqueue(turn: Turn, history=()):
    if not enabled():
        return False
    _remember_turn(turn)
    _ensure_worker()
    item = (turn, list(history or ()), time.time())
    try:
        _QUEUE.put_nowait(item)
    except queue.Full:
        with _LOCK:
            _STATS["dropped"] += 1
        if _LOG:
            _LOG.warning("影子队列已满，丢弃一轮（不影响回复） turn=%s", turn.turn_id)
        return False
    with _LOCK:
        _STATS["submitted"] += 1
    return True


def stats() -> dict:
    with _LOCK:
        d = json.loads(json.dumps(_STATS, ensure_ascii=False))
    d["queued"] = _QUEUE.qsize()
    # 覆盖率按“看到的 Turn”算，而不是按“成功入队”算：队列满/异常丢弃不能伪装成 100%。
    d["coverage"] = (d["completed"] / d["observed"]) if d["observed"] else 0.0
    d["drop_rate"] = (d["dropped"] / d["observed"]) if d["observed"] else 0.0
    d["enabled"] = enabled()
    d["decisions_file"] = str(DECISIONS_FILE)
    return d


def write_stats(path=None) -> str:
    p = Path(path) if path else Path(DECISIONS_FILE).with_name("stats.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(stats(), ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def close(timeout=5.0):
    """测试/退出用：排空队列并停 worker。生产 daemon 线程可不调用。"""
    global _THREAD
    if _THREAD is None or not _THREAD.is_alive():
        return
    _QUEUE.put(None)
    _THREAD.join(timeout)
    _THREAD = None


def report(path=None) -> dict:
    """读 JSONL 生成 M1 验收摘要（覆盖率、动作、差异分类）。"""
    p = Path(path) if path else DECISIONS_FILE
    total = 0
    actions, diffs = {}, {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            total += 1
            a = (rec.get("decision") or {}).get("action") or "?"
            actions[a] = actions.get(a, 0) + 1
            d = rec.get("diff") or "?"
            diffs[d] = diffs.get(d, 0) + 1
    return {"file": str(p), "decisions": total, "actions": actions,
            "diffs": diffs, "coverage": 1.0 if total else 0.0}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", nargs="?", const="", default=None)
    args = ap.parse_args()
    if args.report is not None:
        p = args.report or None
        print(json.dumps(report(p), ensure_ascii=False, indent=2))
