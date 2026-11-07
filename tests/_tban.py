# -*- coding: utf-8 -*-
"""群违禁词入口回归（全打桩，不联网、不落盘）。

2026-11-05 U12a 拆分时 `_route_ban_words` 抽出来漏传了 uid，只会在
「配了 ban_words + 文案命中 + ban_action 含 warn」时抛 NameError——
生产当时 ban_words 为空，SAFE 也无人覆盖这条路径，静默潜伏。
本测试从 `_handle_message` 真入口走，钉住「群里命中要撤回+警告、私聊不掺和」。

用法：python tests/_tban.py
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
import copy
import sys

sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402

FAIL = []


def chk(label, cond, extra=""):
    print(("  [OK] " if cond else "  [失败] ") + label + ("" if cond else " | " + str(extra)))
    if not cond:
        FAIL.append(label)


# ── 全部出口打桩：不发送、不落盘、不起线程 ─────────────────────────
SENT = []
API_CALLS = []
bot.send_group = lambda gid, text, *a, **kw: SENT.append(str(text))
bot.send_reply = lambda gid, text, *a, **kw: SENT.append(str(text))
bot.send_image = lambda gid, *a, **kw: None
bot.api = lambda action, **kw: API_CALLS.append((action, kw)) or {"status": "ok"}
bot.remember = lambda *a, **kw: None
bot.remember_images = lambda *a, **kw: None
bot.forward_images = lambda msg: []
bot._forward_summary = lambda msg: ""
bot.slang_observe = lambda *a, **kw: None
bot.maybe_slang_extract = lambda *a, **kw: None
bot.qbot_mood.note = lambda *a, **kw: None
bot.qbot_timing.note_reply = lambda *a, **kw: None
bot.qbot_expression.maybe_expression_extract = lambda *a, **kw: None
bot._vg_remember_assets = lambda *a, **kw: None
bot._bro_gate = lambda *a, **kw: False
bot.is_privileged = lambda uid, role: True
bot.ai_reply = lambda *a, **kw: None
# 未命中的消息会继续走到链路末尾落主脑（ai_reply 已打桩），无真联网分支。
bot.draw_route = lambda: "off"
bot.SELF_ID = 123450002
GID = 123450001
PRIV = -20001
OWNER = 123450005

_CFG_BAK = copy.deepcopy(bot.CFG)
_MID = [90000]


def ev(text, is_group=True):
    _MID[0] += 1
    segs = [{"type": "text", "data": {"text": text}}]
    if is_group:
        segs.insert(0, {"type": "at", "data": {"qq": str(bot.SELF_ID)}})
    d = {"self_id": bot.SELF_ID, "post_type": "message", "message_id": _MID[0],
         "user_id": OWNER, "sender": {"role": "owner", "card": "测试者"},
         "message": segs}
    if is_group:
        d.update({"message_type": "group", "group_id": GID})
    else:
        d.update({"message_type": "private", "sub_type": "friend"})
    return d


def run(text, is_group=True):
    SENT.clear()
    API_CALLS.clear()
    bot.CFG = copy.deepcopy(_CFG_BAK)
    bot.CFG.setdefault("admin", {})["ban_words"] = ["违禁"]
    bot.CFG["admin"]["ban_action"] = "recall_warn"
    bot.CFG.setdefault("ai", {})["enabled"] = False   # 私聊未命中时别去起大脑线程
    err = ""
    try:
        bot._handle_message(ev(text, is_group), GID if is_group else PRIV, is_group)
    except Exception as e:  # noqa: BLE001
        err = "%s: %s" % (type(e).__name__, e)
    return err


print("=== 群违禁词入口 ===")

_err = run("这是违禁词")
chk("群里命中不抛异常（U12a 曾漏传 uid → NameError）", not _err, _err)
chk("群里命中要撤回", any(a == "delete_msg" for a, _ in API_CALLS), API_CALLS)
chk("群里命中要警告", any("禁止讨论" in s for s in SENT), SENT)

SENT.clear(); API_CALLS.clear()
bot.CFG = copy.deepcopy(_CFG_BAK)
bot.CFG.setdefault("admin", {})["ban_words"] = ["违禁"]
bot.CFG["admin"]["ban_action"] = "recall_warn"
bot.CFG.setdefault("ai", {})["enabled"] = False
bot._handle_message(ev("这是违禁词", is_group=False), PRIV, False)
chk("私聊不撤回（违禁词只管群）", not any(a == "delete_msg" for a, _ in API_CALLS), API_CALLS)
chk("私聊不警告", not any("禁止讨论" in s for s in SENT), SENT)

SENT.clear(); API_CALLS.clear()
bot.CFG = copy.deepcopy(_CFG_BAK)
bot.CFG.setdefault("admin", {})["ban_words"] = ["违禁"]
bot.CFG["admin"]["ban_action"] = "recall_warn"
bot.CFG.setdefault("ai", {})["enabled"] = False
bot._handle_message(ev("今天天气不错"), GID, True)
chk("没命中不撤回不警告", not API_CALLS and not any("禁止讨论" in s for s in SENT),
    (API_CALLS, SENT))

bot.CFG = _CFG_BAK
print()
print("RESULT: %s (%d 项失败)" % ("PASS" if not FAIL else "FAIL", len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
