# -*- coding: utf-8 -*-
"""轻量运行指标（2026-11 阶段 2c）：消息收/发 + 模型调用的小时聚合。

为什么独立模块：Dashboard 的消息量/调用趋势卡需要历史数据（设计文档 B.6 缺口①②），
而收发计数必须挂在每条消息的路径上 —— 独立成模块才能单测 + 不污染 bot.py。

写入策略：**先写内存、每 60 秒落盘一次**（state/metrics.db，SQLite 两张小时表），
绝不每条消息碰一次磁盘。bind 没调用（未接库）时只进内存、flush 静默跳过 ——
回归测试环境零副作用。
"""
import threading
import time
from pathlib import Path

import sqlite3

_LOCK = threading.Lock()
_DB = None
_MEM_MSG = {}    # (day_hour, gid) -> [recv, sent]
_MEM_CALL = {}   # (day_hour, provider) -> [calls, fails]
_LAST_FLUSH = 0.0
_FLUSH_SEC = 60


def _hour():
    return time.strftime("%Y-%m-%d %H:00")


def bind(db_path):
    """bot 启动时接库（state/metrics.db）。幂等，建表幂等。"""
    global _DB
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(db_path)
        c.execute("CREATE TABLE IF NOT EXISTS msg_hour("
                  "day_hour TEXT, gid TEXT, recv INTEGER DEFAULT 0, sent INTEGER DEFAULT 0,"
                  "PRIMARY KEY(day_hour, gid))")
        c.execute("CREATE TABLE IF NOT EXISTS call_hour("
                  "day_hour TEXT, provider TEXT, calls INTEGER DEFAULT 0, fails INTEGER DEFAULT 0,"
                  "PRIMARY KEY(day_hour, provider))")
        c.commit()
        c.close()
        _DB = str(db_path)
    except Exception:
        _DB = None


def count_msg(gid, out=False):
    """记账一条消息（收 False / 发 True）。gid 用会话 key（正群/负uid）。"""
    key = (_hour(), str(gid))
    with _LOCK:
        cell = _MEM_MSG.setdefault(key, [0, 0])
        cell[1 if out else 0] += 1
    # 🔴 落盘判定必须在 _LOCK 之外：flush 要再拿同一把不可重入锁，
    #    在锁内调用 = 第一条消息就死锁（2026-11-02 独立复现证实）。
    _maybe_flush()


def count_call(provider, ok=True):
    """记账一次模型调用（provider=后端名）。"""
    key = (_hour(), str(provider or "unknown"))
    with _LOCK:
        cell = _MEM_CALL.setdefault(key, [0, 0])
        cell[0] += 1
        if not ok:
            cell[1] += 1
    _maybe_flush()


def _maybe_flush():
    global _LAST_FLUSH
    now = time.time()
    if now - _LAST_FLUSH >= _FLUSH_SEC and (_MEM_MSG or _MEM_CALL):
        _LAST_FLUSH = now
        try:
            flush()
        except Exception:
            pass


def flush():
    """内存聚合落库（UPSERT 累加）。失败静默——指标不能影响主流程。
    🔴 本函数自己拿 _LOCK，调用方（count_*）绝不能在持锁状态调进来。"""
    if not _DB:
        return
    with _LOCK:
        msg, call = dict(_MEM_MSG), dict(_MEM_CALL)
        _MEM_MSG.clear()
        _MEM_CALL.clear()
    if not msg and not call:
        return
    try:
        c = sqlite3.connect(_DB)
        for (h, gid), (recv, sent) in msg.items():
            c.execute("INSERT INTO msg_hour(day_hour,gid,recv,sent) VALUES(?,?,?,?) "
                      "ON CONFLICT(day_hour,gid) DO UPDATE SET recv=recv+?, sent=sent+?",
                      (h, gid, recv, sent, recv, sent))
        for (h, prov), (calls, fails) in call.items():
            c.execute("INSERT INTO call_hour(day_hour,provider,calls,fails) VALUES(?,?,?,?) "
                      "ON CONFLICT(day_hour,provider) DO UPDATE SET calls=calls+?, fails=fails+?",
                      (h, prov, calls, fails, calls, fails))
        c.commit()
        c.close()
    except Exception:
        pass


def snapshot(hours=48):
    """给 /api/metrics：最近 N 小时的收发与调用序列（小时升序）。"""
    out = {"msg": [], "calls": [], "today": {"recv": 0, "sent": 0}, "calls_today": 0, "fails_today": 0}
    if not _DB:
        return out
    try:
        c = sqlite3.connect(_DB)
        for table, cols in (("msg_hour", "gid"), ("call_hour", "provider")):
            rows = c.execute("SELECT * FROM %s ORDER BY day_hour ASC" % table).fetchall()
            agg = {}
            for r in rows:
                h = r[0]
                if h < time.strftime("%Y-%m-%d %H:00", time.localtime(time.time() - hours * 3600)):
                    continue
                agg.setdefault(h, [0] * (len(r) - 1))
                for i, v in enumerate(r[1:]):
                    if isinstance(v, int):
                        agg[h][i] += v
            for h in sorted(agg):
                out["msg" if table == "msg_hour" else "calls"].append(
                    dict(zip(["h", "gid", "recv", "sent"] if table == "msg_hour"
                             else ["h", "provider", "calls", "fails"], [h] + agg[h])))
        c.close()
    except Exception:
        pass
    today = time.strftime("%Y-%m-%d")
    for m in out["msg"]:
        if m["h"].startswith(today):
            out["today"]["recv"] += m["recv"]
            out["today"]["sent"] += m["sent"]
    for c2 in out["calls"]:
        if c2["h"].startswith(today):
            out["calls_today"] += c2["calls"]
            out["fails_today"] += c2["fails"]
    return out
