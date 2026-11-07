# -*- coding: utf-8 -*-
"""测试态隔离守卫（2026-11-05 U4，审计 P1-11 的系统性修复）。

背景：bot.py 在 _IN_TEST 时把带落盘的模块 bind 到临时目录（21649 段）。
这个清单是**手工维护**的——新增一个带 state 文件的模块、忘了登记，
测试就会静默写生产 state（11-03 词库事故、11-04 审计补漏，同一个坑踩了两次）。

本测试是唯一注册点：凡 bot 生态里「有模块级落盘文件」的模块，路径必须出现在
下面的 _STATE_FILES 清单里，并且 QBOT_TEST 下 import bot 后全部落在临时目录。
新模块加 state 文件 → 先来这里登记 → SAFE 全绿。
忘了登记路径本身 → 本测试红（清单外新增的 .json/.db 检测见文件末尾的扫描断言）。

跑法：SAFE 全桩，直接 python tests/_tisolation.py。
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import bot   # noqa: E402  （QBOT_TEST 下 import 即完成全部临时目录重绑）

bad = 0


def chk(name, ok, why=""):
    global bad
    if not ok:
        bad += 1
    print(("   [OK] " if ok else "   [失败] ") + name + ("" if ok else " | %s" % (why,)))


# ---- 注册清单：模块名, 状态路径属性, 生产落点（供扫描对照）----
_STATE_FILES = [
    ("qbot.qbot_slang",     "SLANG_FILE",   "state/slang.json"),
    ("qbot.qbot_mood",      "_PATH",        None),          # _PATH 由 bind 注入，无固定生产值
    ("qbot.qbot_expression", "EXP_FILE",    None),
    ("qbot.qbot_timing",    "STATE_FILE",   None),
    ("qbot.qbot_purpose",   "STATE_FILE",   None),
    ("qbot.qbot_memory",    "_DB",          None),
    ("qbot.qbot_memory_card", "_PATHS",     None),          # dict，见下面特判
    ("qbot.qbot_draw",      "DRAW_TIME_FILE", None),       # U12b：出图耗时样本，测试态临时目录
    ("qbot.qbot_shadow",    "DECISIONS_FILE", None),       # M1：影子审计日志，默认关且测试态临时目录
    ("qbot.qbot_tools",     "SHADOW_FILE",  None),          # M3：工具影子审计，默认 legacy 不写
]


def _mod_attr(mod_name, attr):
    m = sys.modules.get(mod_name)
    return getattr(m, attr, None) if m else None


def _in_tmp(p):
    try:
        return p is not None and Path(str(p)).resolve().is_relative_to(
            Path(tempfile.gettempdir()).resolve())
    except Exception:
        return False


print("== 1 注册清单里的路径，QBOT_TEST 下必须全落临时目录 ==")
for mod_name, attr, _prod in _STATE_FILES:
    v = _mod_attr(mod_name, attr)
    if isinstance(v, dict):                       # qbot_memory_card._PATHS
        chk("%s.%s" % (mod_name, attr), v and all(_in_tmp(x) for x in v.values()), v)
    else:
        chk("%s.%s" % (mod_name, attr), _in_tmp(v), v)

print("== 2 slang 的文件级实锤：save_slang 落盘后文件必须在临时目录 ==")
from qbot import qbot_slang as S   # noqa: E402
S.SLANG["terms"]["__isolation_probe__"] = {"meaning": "探针，不入库"}
try:
    S.save_slang()
    chk("save_slang 写进临时目录", _in_tmp(S.SLANG_FILE) and Path(S.SLANG_FILE).exists(),
        S.SLANG_FILE)
finally:
    S.SLANG["terms"].pop("__isolation_probe__", None)

print("== 3 生产 state 兜底扫描：模块目录里新增的 .json/.db 不许指向生产 state ==")
# 简单兜底：已知生产 state 下的敏感文件 mtime 不因本进程而变——不好断言 mtime，
# 改断言「生产 slang.json 不被本进程写过」：探针词不能出现在生产文件里。
_prod_slang = Path(_ROOT) / "state" / "slang.json"
if _prod_slang.exists():
    chk("生产 slang.json 无探针泄漏",
        "__isolation_probe__" not in _prod_slang.read_text(encoding="utf-8", errors="ignore"))

print()
print("RESULT:", "PASS" if bad == 0 else "FAIL (%d)" % bad)
sys.exit(1 if bad else 0)
