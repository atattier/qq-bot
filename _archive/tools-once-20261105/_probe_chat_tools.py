# -*- coding: utf-8 -*-
"""验证聊天链路的 function calling：离线循环逻辑 + 真网关只读冒烟（qq_groups，零副作用）。"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QBOT_TEST"] = "1"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = FAIL = 0


def chk(name, ok, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(("✅" if ok else "❌"), name, ("" if ok else "  << %s" % (extra,)))


import bot

# ---------- 1) _ob11_text ----------
chk("段数组→文本", bot._ob11_text(
    [{"type": "text", "data": {"text": "hi"}},
     {"type": "at", "data": {"qq": "123"}}, {"type": "image", "data": {}}]) == "hi@123[图片]")
chk("字符串直通", bot._ob11_text("原始文本") == "原始文本")

# ---------- 2) _exec_qq_tool（mock api / send_group）----------
_real_api, _real_send = bot.api, bot.send_group
try:
    _MSG = {"time": 1792700000, "user_id": 123450006, "sender": {"nickname": "狐狐"},
            "message": [{"type": "text", "data": {"text": "在吗"}}]}

    def fake_api(action, **kw):
        if action == "get_group_list":
            return {"status": "ok", "data": [{"group_id": 123450001, "group_name": "元镜主题"}]}
        if action == "get_group_member_list":
            return {"status": "ok", "data": [{"user_id": 123450006, "nickname": "狐狐",
                                              "card": "狐狐"}]}
        if action == "get_group_msg_history":
            return {"status": "ok", "data": {"messages": [_MSG]}}
        return {"status": "failed"}

    bot.api = fake_api
    g = bot._exec_qq_tool("qq_groups", {})
    chk("qq_groups", g["groups"][0]["group_id"] == 123450001, g)
    m = bot._exec_qq_tool("qq_members", {"group_id": 123450001})
    chk("qq_members 认出狐狐", m["members"][0]["nickname"] == "狐狐", m)
    h = bot._exec_qq_tool("qq_history", {"group_id": 123450001, "count": 5})
    chk("qq_history 文本抽取", h["messages"][0]["text"] == "在吗", h)
    unknown = bot._exec_qq_tool("hack_server", {})
    chk("未知工具拒绝", unknown.get("ok") is False)
finally:
    bot.api, bot.send_group = _real_api, _real_send

# 发送必须走 send_group（审计在里面）——mock 掉验证调用关系
_sent = {}
bot.send_group = lambda gid, text: _sent.update(gid=gid, text=text) or True
r = bot._exec_qq_tool("qq_send_group", {"group_id": 123450001, "message": "测试"})
bot.send_group = _real_send
chk("qq_send_group 走 send_group", r["ok"] and _sent == {"gid": 123450001, "text": "测试"}, (r, _sent))

# ---------- 3) chat_once_tools 循环（mock _post_json）----------
_real_post = bot._post_json
try:
    calls = []

    def fake_post(url, payload, headers=None, timeout=60):
        calls.append(payload)
        if len(calls) == 1:                      # 第一轮：模型要调工具
            tc = {"id": "c1", "type": "function",
                  "function": {"name": "qq_groups", "arguments": "{}"}}
            return {"choices": [{"message": {
                "role": "assistant", "content": "", "tool_calls": [tc]}}]}
        # 第二轮：回填后出文本
        assert payload["messages"][-1]["role"] == "tool", "工具结果没回填"
        return {"choices": [{"message": {
            "role": "assistant", "content": "群里就一个元镜主题♡ 探索完毕！"}}]}

    bot._post_json = fake_post
    bot.provider_cfg_orig = bot.provider_cfg
    ans, used = bot.chat_once_tools("custom", [{"role": "user", "content": "看看有哪些群"}])
    chk("工具循环: 请求带 tools", calls and calls[0].get("tool_choice") == "auto")
    chk("工具循环: 两轮后出文本", "探索完毕" in (ans or ""), ans)
    chk("工具循环: used_tool=True", used is True)
finally:
    bot._post_json, bot.provider_cfg = _real_post, getattr(bot, "provider_cfg_orig", bot.provider_cfg)

# 4xx ⇒ ToolsUnsupported
try:
    def post_400(url, payload, headers=None, timeout=60):
        raise RuntimeError("HTTP 400 (bad request)")
    bot._post_json = post_400
    try:
        bot.chat_once_tools("custom", [{"role": "user", "content": "x"}])
        chk("HTTP400 抛 ToolsUnsupported", False, "没抛")
    except bot.ToolsUnsupported:
        chk("HTTP400 抛 ToolsUnsupported", True)
    except Exception as e:
        chk("HTTP400 抛 ToolsUnsupported", False, repr(e))
finally:
    bot._post_json = _real_post

# ---------- 4) _task_chat_with_tools（mock 上层）----------
try:
    bot.provider_cfg_orig2 = bot.provider_cfg
    bot.provider_cfg = lambda n: {"url": "http://x", "model": "m", "key": "k"} if n == "custom" else None
    bot.HISTORY[(-123450003)] = bot.HISTORY.get(-123450003) or __import__("collections").deque(maxlen=20)
    bot._task_chat_with_tools_orig = bot._task_chat_with_tools
    # 成功路径
    bot.chat_once_tools = lambda n, msgs, **kw: ("干完了，群里就一个群♡", True)
    n, a = bot._task_chat_with_tools(-123450003, "去探索", "custom")
    chk("task 工具聊天成功路径", n == "custom" and "干完了" in a, (n, a))
    # 网关不支持 → 空回答（让 agent 兜底）
    def raise_unsup(n_, msgs_, **kw):
        raise bot.ToolsUnsupported("HTTP 400")
    bot.chat_once_tools = raise_unsup
    n, a = bot._task_chat_with_tools(-123450003, "去探索", "custom")
    chk("task 不支持tools → 回答空（落agent）", n == "custom" and a == "", (n, a))
finally:
    bot._post_json = _real_post
    bot.provider_cfg = getattr(bot, "provider_cfg_orig2", bot.provider_cfg)

print()
print("==== 离线 %d/%d PASS，下面真网关只读冒烟 ====" % (PASS, PASS + FAIL))

# ---------- 5) 真网关冒烟：qq_groups 只读（零副作用）----------
name = bot.CFG.get("ai", {}).get("provider", "")
p = bot.provider_cfg(name)
if not p or not str(p.get("url") or "").strip():
    print("（跳过：当前大脑 %r 不可用）" % name)
else:
    msgs = [{"role": "system", "content":
             "你是测试员。调用 qq_groups 工具拿到群列表，然后原样复述群号和群名，不要做别的。"},
            {"role": "user", "content": "列出你能看到的群"}]
    t0 = time.time()
    try:
        ans, used = bot.chat_once_tools(name, msgs)
        ok = bool((ans or "").strip())
        chk("真网关[%s] tools 冒烟（%.0fs, 用工具=%s）" % (name, time.time() - t0, used),
            ok and used, (ans or "")[:120])
    except bot.ToolsUnsupported as e:
        chk("真网关[%s] tools 冒烟" % name, False, "网关不支持 tools: %s" % e)
    except Exception as e:
        chk("真网关[%s] tools 冒烟" % name, False, bot._short_err(str(e)))

print()
print("==== 总计 %d/%d PASS ====" % (PASS, PASS + FAIL))
sys.exit(0 if FAIL == 0 else 1)
