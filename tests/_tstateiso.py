# -*- coding: utf-8 -*-
"""测试态生产状态隔离（SAFE，2026-11-04 审计小修）。

背景：`import bot` 会执行 `_bind_modules()`，把 mood / slang / expression / timing /
scramble / REV_DIR / wire 备份目录都绑到**生产 state**。以前只有记忆库和记忆卡在
`_IN_TEST` 下改到临时目录 —— 任何测试走到消息处理/记账路径就会写真实状态
（2026-11-04 逐套件审计实测：mood.json、slang.json、state/reverse、state/scramble、
config.json.bak-wired-* 都被测试写过）。

本测试锁死修复：
  ① 所有会落盘的模块路径必须在临时目录；
  ② 真的调用一次写盘函数，前后比对**生产 state 文件 hash 不变**；
  ③ wire 备份目录也必须在临时目录。
"""
import hashlib
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
os.environ["QBOT_TEST"] = "1"

FAIL = []


def chk(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  | %s" % (detail,) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


import bot  # noqa: E402
import qbot_mood as M  # noqa: E402
import qbot_expression as E  # noqa: E402
import qbot_timing as T  # noqa: E402
import qbot_scramble as SC  # noqa: E402
import qbot_slang as S  # noqa: E402
import qbot_purpose as PU  # noqa: E402
import qbot_draw as D  # noqa: E402
import qbot_shadow as SH  # noqa: E402

# 生产 state 里绝不能被测试写的文件（取 hash 时只读）
_PROD = ["config.json", "state/mood.json", "state/slang.json", "state/expressions.json",
         "state/timing.json", "state/memory-card.json", "state/purpose.json",
         "state/draw_time.json"]


def _hash(p):
    try:
        return hashlib.sha1(open(p, "rb").read()).hexdigest()[:16]
    except FileNotFoundError:
        return "<缺失>"


def _in_tmp(p):
    s = str(p or "")
    return bool(s) and ("qbot_state_test_" in s or "qbot_rev_test_" in s
                        or tempfile.gettempdir().lower() in s.lower())


print("== ① 落盘路径必须在临时目录 ==")
chk("mood 路径", _in_tmp(M._PATH), M._PATH)
chk("slang 路径", _in_tmp(S.SLANG_FILE), S.SLANG_FILE)
chk("expression 路径", _in_tmp(E.EXP_FILE), E.EXP_FILE)
chk("timing 路径", _in_tmp(T.STATE_FILE), T.STATE_FILE)
chk("purpose 路径", _in_tmp(PU.STATE_FILE), PU.STATE_FILE)
chk("draw 耗时样本路径", _in_tmp(D.DRAW_TIME_FILE), D.DRAW_TIME_FILE)
chk("影子日志路径", _in_tmp(SH.DECISIONS_FILE), SH.DECISIONS_FILE)
chk("REV_DIR", _in_tmp(bot.REV_DIR), bot.REV_DIR)
chk("scramble 目录", _in_tmp(SC.SCR_DIR), SC.SCR_DIR)

# dutil 在带连字符的目录里，用 spec 加载
_spec = importlib.util.spec_from_file_location(
    "qbot_dutil_iso", os.path.join(_ROOT, "docker-napcat", "dutil.py"))
_dutil = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dutil)
chk("wire 备份目录", _in_tmp(_dutil.BACKUP_DIR), _dutil.BACKUP_DIR)

print("== ② 真写一次，生产文件 hash 必须不变 ==")
_before = {p: _hash(p) for p in _PROD}

# mood：喂一句会被判成「被怼」的话，触发 _save()
M.note(812, "傻逼")
# slang：直接落一次盘
S.save_slang()
# expression：塞一条测试条目再落盘
with E._lock:
    E.EXPRESSIONS.setdefault("expressions", {})["_iso_test"] = {
        "id": 999999, "situation": "隔离测试", "style": "测试", "contents": ["测试"],
        "count": 1, "status": "pending", "sources": ["812"], "created_at": 0,
    }
E.save()
# timing：强制落一次（总闸关也允许落盘函数本身）
with T._lock:
    T._STATE["gate_th"] = 0.35
T._save(force=True)
# purpose：塞一个测试 goal 再落盘
with PU._lock:
    PU._STATE["gid"]["812"] = {"goals": [], "shadow": [], "next_fire_at": 0,
                               "success": 0, "attempts": 0}
PU._save(force=True)
# draw：真写一次耗时样本（走 qbot_draw 的桥接字典，验 STATE 已重绑）
D._est_learn("U12b隔离探针", 42)
# wire 备份：拿一个临时文件做源
_tmp_src = Path(tempfile.mkdtemp(prefix="iso_src_")) / "x.json"
_tmp_src.write_text("{}", encoding="utf-8")
_bak = _dutil.backup(str(_tmp_src), "iso")
chk("wire 备份真的落在临时目录", _in_tmp(_bak) and os.path.exists(_bak), _bak)

_after = {p: _hash(p) for p in _PROD}
for p in _PROD:
    chk("生产文件未被写：%s" % p, _before[p] == _after[p], (_before[p], _after[p]))

print("== ③ 临时副本确实产生（证明确实走到写盘路径）==")
chk("mood 临时文件已写", Path(M._PATH).exists(), M._PATH)
chk("slang 临时文件已写", Path(S.SLANG_FILE).exists(), S.SLANG_FILE)
chk("expression 临时文件已写", Path(E.EXP_FILE).exists(), E.EXP_FILE)
chk("timing 临时文件已写", Path(T.STATE_FILE).exists(), T.STATE_FILE)
chk("purpose 临时文件已写", Path(PU.STATE_FILE).exists(), PU.STATE_FILE)
chk("draw 耗时样本临时文件已写", Path(D.DRAW_TIME_FILE).exists(), D.DRAW_TIME_FILE)

print("\n== 汇总 ==")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
