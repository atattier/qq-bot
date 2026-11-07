# -*- coding: utf-8 -*-
"""config.json 单一可信基座（2026-11-05 U5，审计 P1-3）。

为什么抽出来：bot.py 里 `save_config()`（把内存 CFG 整体 dump 回盘）有约 15 个
调用点散布在 HTTP 线程、消息线程、scheduler 线程——无锁并发写既有丢更新风险，
也有写一半崩溃留半截文件的风险。U5 三件事：

  1. CFG 的加载/保存收进本模块，bot.py 与后续拆出的域模块都从这里 import；
  2. `save_config()` 加 `_cfg_lock`（RLock）+ **原子写**（同目录临时文件 → os.replace），
     写崩不毁配置；
  3. `_dot_get/_dot_set` 路径寻址一并收编（bot.py 旋钮注册表 150+ 处在用）。

约定：本模块**不许 import 任何项目模块**（它是被依赖的最底层）。
文件格式与历史完全一致（ensure_ascii=False, indent=2），字节级不引入 diff。
"""
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("qqbot")

BASE = Path(__file__).resolve().parent
_CONFIG_PATH = BASE / "config.json"

CFG = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))

_cfg_lock = threading.RLock()


def save_config():
    """把内存 CFG 整体写回 config.json。锁内 + 原子替换；失败只记日志返回 False。

    调用方语义不变（bot.py 原实现：写坏只 log 不抛）；区别是并发调用在此串行，
    且读者永远不会看到半截文件。
    """
    try:
        data = json.dumps(CFG, ensure_ascii=False, indent=2)
        with _cfg_lock:
            tmp = _CONFIG_PATH.with_name("config.json.tmp.%s" % os.getpid())
            tmp.write_text(data, encoding="utf-8")
            os.replace(str(tmp), str(_CONFIG_PATH))
        return True
    except Exception as e:  # noqa: BLE001
        log.error("写回 config.json 失败: %s", e)
        return False


def dot_get(path, default=None):
    """CFG 点路径读：dot_get("ai.quote_max", 3000)。"""
    cur = CFG
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def dot_set(path, value):
    """CFG 点路径写：中间缺的 dict 自动补。"""
    parts = path.split(".")
    cur = CFG
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value
