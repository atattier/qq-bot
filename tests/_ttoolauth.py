# -*- coding: utf-8 -*-
"""R2：工具执行层只执行本轮真正提供给模型的工具面。

背景（2026-11-05 安全整改 R2）：
    非主人轮次的工具面由 `_omni_tools(False)` 裁剪，不包含 qq_send_group/remember；
    但模型可能被引用文本、搜索结果或幻觉诱导，硬写一个未提供的工具名。
    如果执行层只信模型，就会绕过上层裁剪。本用例锁住执行层这道闸。

全离线：`_post_json` 返回剧本，`_exec_qq_tool` 只记录，不联网、不发群。
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402

FAIL = []


def chk(label, cond, extra=""):
    print(("  [OK] " if cond else "  [失败] ") + label + ("" if cond else " | " + str(extra)))
    if not cond:
        FAIL.append(label)


EXEC = []


def _fake_exec(name, args):
    EXEC.append((name, dict(args or {})))
    return {"ok": True, "note": "桩：未真执行"}


_orig_exec = bot._exec_qq_tool
_orig_post = bot._post_json
bot._exec_qq_tool = _fake_exec


def _run(tools, calls, max_calls=2):
    """跑一轮 chat_once_tools：第一轮返回 calls，第二轮返回正文。"""
    EXEC.clear()
    seen = []
    rounds = {"i": 0}

    def _fake_post(url, payload, headers, timeout=None):
        seen.append(payload)
        i = rounds["i"]
        rounds["i"] = i + 1
        if i == 0:
            tcs = [{"id": "c%d" % j,
                    "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                   for j, (n, a) in enumerate(calls)]
            return {"choices": [{"message": {"content": "", "tool_calls": tcs}}]}
        return {"choices": [{"message": {"content": "收工，这是正文"}}]}

    bot._post_json = _fake_post
    ans, used = bot.chat_once_tools("custom", [{"role": "user", "content": "x"}],
                                    tools=tools, max_calls=max_calls, max_rounds=2)
    return ans, used, seen


def _tool_results(seen):
    if not seen:
        return []
    return [m for m in (seen[-1].get("messages") or []) if m.get("role") == "tool"]


print("=== R2：工具执行层授权复核 ===")

guest_tools = bot._omni_tools(False)
guest_names = [t["function"]["name"] for t in guest_tools]
chk("非主人工具面不含 qq_send_group", "qq_send_group" not in guest_names, guest_names)
chk("非主人工具面不含 remember", "remember" not in guest_names, guest_names)

# 1) 非主人工具面 + 模型硬写 qq_send_group ⇒ 执行层拒绝
ans, used, seen = _run(guest_tools,
                       [("qq_send_group", {"group_id": 987654321, "message": "R2 桩"})])
chk("未提供的 qq_send_group 不被执行", EXEC == [], EXEC)
_errs = " ".join(str(m.get("content") or "") for m in _tool_results(seen))
chk("拒绝原因如实回填给模型", "不在本轮工具面" in _errs, _errs[:160])
chk("被拒后模型仍能拿到正文", bool(ans), ans)

# 2) 非主人工具面 + 模型硬写 remember ⇒ 同样拒绝
_run(guest_tools, [("remember", {"content": "R2 桩"})])
chk("未提供的 remember 不被执行", EXEC == [], EXEC)

# 3) 主人工具面（_QQ_TOOLS）⇒ qq_send_group 正常执行
_run(list(bot._QQ_TOOLS),
     [("qq_send_group", {"group_id": 987654321, "message": "R2 桩"})])
chk("主人工具面下 qq_send_group 正常执行", EXEC == [
    ("qq_send_group", {"group_id": 987654321, "message": "R2 桩"})], EXEC)

# 4) tools=None 是主人派活老路径的默认值 ⇒ 仍允许 _QQ_TOOLS
_run(None, [("qq_send_group", {"group_id": 987654321, "message": "R2 桩"})])
chk("tools=None 保持主人派活路径兼容", len(EXEC) == 1 and EXEC[0][0] == "qq_send_group", EXEC)

# 5) 显式空工具面 ⇒ 任何工具都不许执行（_tbudget 旧夹具的语义边界）
_run([], [("web_search", {"query": "x"})])
chk("空工具面下任何工具都不执行", EXEC == [], EXEC)

# 6) 本轮提供了 bot 工具 ⇒ 正常执行（不是只对 QQ 工具放行）
_one_tool = [{"type": "function",
              "function": {"name": "web_search",
                           "parameters": {"type": "object", "properties": {}}}}]
_run(_one_tool, [("web_search", {"query": "x"})])
chk("本轮提供的 bot 工具正常执行", len(EXEC) == 1 and EXEC[0][0] == "web_search", EXEC)

# 7) 未提供的未知工具名 ⇒ 拒绝
_run(guest_tools, [("not_a_tool", {})])
chk("未知工具名被拒绝", EXEC == [], EXEC)

bot._exec_qq_tool = _orig_exec
bot._post_json = _orig_post

print()
print("=== 汇总 ===")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
