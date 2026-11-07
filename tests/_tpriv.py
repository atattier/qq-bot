# -*- coding: utf-8 -*-
"""私聊（群临时会话）链路回归 —— 2026-10-27 主人「为啥不听话」那次。

背景：管理员在群里让 bot「去私聊把@狐狐 拿下」，bot 回「工具压根没给这条路」。
查出来不是权限问题，是 **`action_send` 没把 group_hint 传给 send_segments**，
而 `_api_send` 早就支持群临时会话了（源码注释里写着 NapCat 的 TEMPC2CFROMGROUP）。

这里把 send_segments 换成假的，只验参数有没有传对，**不发真消息**。

跑法：python tests/_tpriv.py
"""
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)

import importlib.util  # noqa: E402

import bot  # noqa: E402

_sys.stdout.reconfigure(encoding="utf-8")

FAIL = []


def chk(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  <- " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(name)


# ── 把真实发送换成记录器 ───────────────────────────────────────────────
CALLS = []


def fake_send_segments(cid, segments, group_hint=None):
    CALLS.append({"cid": cid, "hint": group_hint, "segs": segments})
    return True


bot.send_segments = fake_send_segments
bot._last_agent_send = {}
bot._allowed_conv = lambda cid: True       # 白名单不是本测试要验的东西
bot.audit_ok = lambda text: True

GROUP = 123450001
TARGET = 123450006                        # 狐狐，非好友
PRIV_KEY = -TARGET

print("=== 1. 私聊 + group_hint（群临时会话）===")
CALLS.clear()
r = bot.action_send(PRIV_KEY, text="测试", group_hint=GROUP)
chk("调用成功", r.get("ok") is True, r)
chk("只调了一次 send_segments", len(CALLS) == 1, len(CALLS))
chk("group_hint 传下去了", CALLS and CALLS[0]["hint"] == GROUP,
    CALLS[0]["hint"] if CALLS else "没调用")
chk("cid 是负的私聊 key", CALLS and CALLS[0]["cid"] == PRIV_KEY)
chk("返回值里带 group_hint", r.get("group_hint") == GROUP, r.get("group_hint"))

print("=== 2. 私聊但不带 group_hint（非好友发不出去，必须如实反馈）===")
CALLS.clear()
r = bot.action_send(PRIV_KEY, text="测试")
chk("hint 为 None", CALLS and CALLS[0]["hint"] is None,
    CALLS[0]["hint"] if CALLS else "没调用")
chk("返回值 group_hint 是 None", r.get("group_hint") is None, r.get("group_hint"))

print("=== 3. 发群消息时 group_hint 必须被丢掉（不能走错分支）===")
CALLS.clear()
r = bot.action_send(GROUP, text="测试", group_hint=GROUP)
chk("hint 被清成 None", CALLS and CALLS[0]["hint"] is None,
    CALLS[0]["hint"] if CALLS else "没调用")
chk("cid 是群号", CALLS and CALLS[0]["cid"] == GROUP)

print("=== 4. group_hint 传字符串也要能转成 int ===")
CALLS.clear()
r = bot.action_send(PRIV_KEY, text="测试", group_hint=str(GROUP))
chk("字符串 hint 被转成 int", CALLS and CALLS[0]["hint"] == GROUP,
    CALLS[0]["hint"] if CALLS else "没调用")

print("=== 5. 发不出去要如实报错，不能谎报成功 ===")
bot.send_segments = lambda cid, segments, group_hint=None: False
CALLS.clear()
r = bot.action_send(PRIV_KEY, text="测试", group_hint=GROUP)
chk("ok 是 False", r.get("ok") is False, r)
chk("有 error 文字", bool(r.get("error")), r.get("error"))
bot.send_segments = fake_send_segments

print("=== 6. MCP 的 qq_send 工具必须暴露 group_hint ===")
spec = importlib.util.spec_from_file_location("mcp", _os.path.join(_ROOT, "qq-mcp-server.py"))
mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp)
tool = [t for t in mcp.TOOLS if t["name"] == "qq_send"][0]
props = tool["inputSchema"]["properties"]
chk("qq_send 有 group_hint 参数", "group_hint" in props, list(props))
chk("group_hint 是 integer", props.get("group_hint", {}).get("type") == "integer",
    props.get("group_hint", {}).get("type"))
chk("描述里提到群临时会话", "群临时会话" in props.get("group_hint", {}).get("description", ""))

print()
print("结果：" + ("全部通过" if not FAIL else "失败 %d 项：%s" % (len(FAIL), "、".join(FAIL))))
_sys.exit(0 if not FAIL else 1)
