# -*- coding: utf-8 -*-
"""阶段 2：工具预算 / 去重 / 画图单发 / 失败停止 的离线单测（不联网）。

把 bot._post_json 打桩成"剧本"，模拟模型连续要求调工具，断言硬闸行为。
用法：python tests/_tbudget.py
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
import sys

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
    return {"ok": True, "note": "桩"}


bot._exec_qq_tool = _fake_exec


def script(calls_per_round):
    """把 _post_json 打桩：每轮返回一组 tool_calls，最后一轮返回正文。"""
    rounds = {"i": 0}

    def _fake(url, payload, headers, timeout=None):
        i = rounds["i"]
        rounds["i"] = i + 1
        if i < len(calls_per_round):
            calls = [{"id": "c%d_%d" % (i, j),
                      "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                     for j, (n, a) in enumerate(calls_per_round[i])]
            return {"choices": [{"message": {"content": "", "tool_calls": calls}}]}
        return {"choices": [{"message": {"content": "收工，这是正文"}}]}
    return _fake, rounds


import json  # noqa: E402


def _specs(*names):
    """构造本轮工具面；R2 执行层会按它复核，测试不能再传空列表。"""
    return [{"type": "function",
             "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}
            for n in names]


print("=== 工具预算 / 去重 / 画图单发 / 失败停止 ===")

# 1) 预算：一轮内要求 5 个不同工具，max_calls=2 → 只执行 2 个
EXEC.clear()
bot._post_json, _ = script([[
    ("web_search", {"query": "a"}), ("recall_memory", {"query": "b"}),
    ("bot_status", {}), ("qq_groups", {}), ("qq_history", {"group_id": 1})]])
ans, used = bot.chat_once_tools(
    "custom", [{"role": "user", "content": "x"}],
    tools=_specs("web_search", "recall_memory", "bot_status", "qq_groups", "qq_history"),
    max_calls=2, max_rounds=2)
chk("一轮 5 个调用只执行 2 个（预算硬闸）", len(EXEC) == 2, EXEC)
chk("预算用尽后模型仍拿到正文", bool(ans), ans)

# 2) 去重：同一工具同一参数连调 3 次 → 只执行 1 次
EXEC.clear()
bot._post_json, _ = script([[
    ("web_search", {"query": "同一句"}),
    ("web_search", {"query": "同一句"}),
    ("web_search", {"query": "同一句"})]])
bot.chat_once_tools("custom", [{"role": "user", "content": "x"}],
                    tools=_specs("web_search"), max_calls=6, max_rounds=2)
chk("同一调用重复 3 次只执行 1 次（去重）", len(EXEC) == 1, EXEC)

# 3) 画图单发：draw_like_this 成功后不许再 draw_image
EXEC.clear()
bot._post_json, _ = script([[
    ("draw_like_this", {"extra": "改帽子"}),
    ("draw_image", {"what": "同样画面"})]])
bot.chat_once_tools("custom", [{"role": "user", "content": "x"}],
                    tools=_specs("draw_like_this", "draw_image"),
                    max_calls=6, max_rounds=2)
chk("画图单发：第二个画图工具被拒", len(EXEC) == 1 and EXEC[0][0] == "draw_like_this", EXEC)

# 4) 失败停止：同一工具连续失败 2 次后第 3 次不再执行
EXEC.clear()
def _fail_exec(name, args):
    EXEC.append((name, dict(args or {})))
    return {"ok": False, "error": "这轮没有图"}
bot._exec_qq_tool = _fail_exec
bot._post_json, _ = script([[
    ("reverse_prompt", {"arg": "1"}), ("reverse_prompt", {"arg": "2"}),
    ("reverse_prompt", {"arg": "3"})]])
bot.chat_once_tools("custom", [{"role": "user", "content": "x"}],
                    tools=_specs("reverse_prompt"), max_calls=6, max_rounds=2)
chk("连续失败 2 次后第 3 次拒绝重试", len(EXEC) == 2, EXEC)
bot._exec_qq_tool = _fake_exec

# 5) 多步骤预算：显式多步骤话术把上限放到 6
chk("多步骤意图识别", bot._multi_step_intent("去群里问问今晚谁在线，然后告诉我"))
chk("普通聊天不误判多步骤", not bot._multi_step_intent("那再聊聊别的吧"))
chk("长文本不误判", not bot._multi_step_intent("然" * 100))

print()
print("=== 汇总 ===")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
