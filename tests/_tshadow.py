# -*- coding: utf-8 -*-
"""M1 影子核心 SAFE：Turn 契约 / Policy / Core / 队列 / 总闸 / 差异分类。

全离线：模型用假 decide，不联网、不发消息、不执行工具、不写生产 state。
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
os.environ["QBOT_TEST"] = "1"
os.environ.pop("QBOT_ARCH_SHADOW", None)
sys.stdout.reconfigure(encoding="utf-8")

import qbot_core as C          # noqa: E402
import qbot_shadow as S        # noqa: E402
import qbot_turn as T          # noqa: E402

bad = 0


def chk(name, ok, why=""):
    global bad
    if not ok:
        bad += 1
    print(("  [OK] " if ok else "  [失败] ") + name + ("" if ok else " | %s" % (why,)))


def wait_done(n, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if S.stats()["completed"] >= n:
            return True
        time.sleep(0.02)
    return False


def fake_decide(messages):
    text = messages[-1]["content"]
    if "发群" in text or "qq_send_group" in text:
        d = {"action": "tool_call", "tool": "qq_send_group",
             "args": {"group_id": 1, "message": "x"}, "reply": "",
             "reason": "要求发群", "confidence": 0.9}
    elif "quiet" in text:
        d = {"action": "silent", "reply": "", "reason": "无需接话", "confidence": 0.8}
    else:
        d = {"action": "reply", "reply": "收到。", "reason": "普通聊天", "confidence": 0.7}
    return "```json\n%s\n```" % json.dumps(d, ensure_ascii=False), {"backend": "fake"}


print("== 1 Turn / MediaRef 契约 ==")
t1 = T.make_turn(event_id="evt-1", session_kind="group", session_key=8123,
                 user_id=1001, display_name="甲", role="member",
                 received_at=1793878000, text="画这两张",
                 media_refs=[{"file": "a.png"}, {"file": "b.png"}],
                 quote_text="引用 上一张", quote_unresolved=False,
                 addressed=True, flags={"privileged": False})
t2 = T.make_turn(event_id="evt-1", session_kind="group", session_key=8123,
                 user_id=1001, display_name="甲", role="member",
                 received_at=1793878000, text="画这两张",
                 media_refs=[{"file": "a.png"}, {"file": "b.png"}],
                 quote_text="引用 上一张", quote_unresolved=False,
                 addressed=True, flags={"privileged": False})
chk("同一事件 turn_id 确定", t1.turn_id == t2.turn_id, (t1.turn_id, t2.turn_id))
chk("turn_id 长度/前缀", len(t1.turn_id) == 24, t1.turn_id)
try:
    t1.text = "改了"
    frozen = False
except Exception:
    frozen = True
chk("Turn 字段冻结", frozen)
chk("MediaRef 只存引用不存字节", len(t1.media) == 2 and all(m.local_uri is None for m in t1.media))
chk("引用未解析标记", t1.quote is not None and not t1.quote.unresolved)
chk("to_dict 可 JSON 序列化", isinstance(json.dumps(t1.to_dict(), ensure_ascii=False), str))
chk("群/私聊 session 不混", T.make_turn(event_id="x", session_kind="private", session_key=-1,
                                      user_id=1).session.kind == "private")

print("== 2 Core / Policy ==")
chk("JSON 围栏解析", C.parse_decision('```json\n{"action":"reply","reply":"好"}\n```').action == "reply")
chk("坏 JSON 不抛", C.parse_decision("不是 JSON").action == "invalid")
chk("ask 归一为 clarify", C.parse_decision('{"action":"ask","reply":"哪张？"}').action == "clarify")
chk("坏 JSON 经 Policy 仍是 invalid（差异可分类）",
    C.Policy().apply(member_bad := T.make_turn(event_id="p0", session_kind="group",
                                               session_key=1, user_id=2),
                     C.parse_decision("不是 JSON")).action == "invalid")

member = T.make_turn(event_id="p1", session_kind="group", session_key=1,
                     user_id=2, role="member", text="帮我发群")
d = C.parse_decision('{"action":"tool_call","tool":"qq_send_group","args":{}}')
pd = C.Policy().apply(member, d)
chk("群友调用 qq_send_group 被 Policy 拒绝", pd.action == "refuse", pd.action)

owner = T.make_turn(event_id="p2", session_kind="private", session_key=-2,
                    user_id=2, role="owner", text="发群",
                    flags={"privileged": True})
pd2 = C.Policy().apply(owner, d)
chk("主人调用 qq_send_group 放行", pd2.action == "tool_call", pd2.action)

mute = T.make_turn(event_id="p3", session_kind="group", session_key=1,
                   user_id=2, role="member", text="说话",
                   flags={"test_mute": True})
pd3 = C.Policy().apply(mute, C.parse_decision('{"action":"reply","reply":"hi"}'))
chk("测试静音强制 silent", pd3.action == "silent" and pd3.reply == "", pd3.to_dict())

forbidden = C.Policy().apply(owner, C.parse_decision(
    '{"action":"tool_call","tool":"shell","args":{"cmd":"ls"}}'))
chk("危险工具被拒绝", forbidden.action == "refuse", forbidden.action)

core = C.Core(fake_decide, C.Policy())
d = core.run(t1, history=[{"role": "user", "content": "上一句"}], tools=[])
chk("Core 正常产出 reply", d.action == "reply" and d.reply == "收到。", d.to_dict())
chk("Core 记录延迟/字符数", d.latency_ms >= 0 and d.prompt_chars > 0 and d.response_chars > 0)
chk("影子 tool_call 不执行", core.run(member).tool_executed is False)
err_core = C.Core(lambda messages: ("", {"backend": "x", "error": "down"}))
chk("模型失败记为 error", err_core.run(t1).action == "error", err_core.run(t1).to_dict())

print("== 3 影子队列 / 总闸 / 覆盖率 ==")
tmp = tempfile.mkdtemp(prefix="shadow_test_")
S.bind(cfg={"arch": {"shadow": False}}, state_dir=tmp, logger=None,
       decide=fake_decide, tools=[{"name": "draw_image", "description": "画图"}],
       max_queue=64, legacy_grace_ms=0)
S.reset()
chk("总闸默认关：observe 不入队", S.observe_message(
    session_key=1, is_group=True, user_id=2, text="hi", mid=1) is False)
chk("总闸关：无提交", S.stats()["submitted"] == 0, S.stats())
chk("总闸关：不创建日志", not Path(tmp, "decisions.jsonl").exists())

S.bind(cfg={"arch": {"shadow": True}}, state_dir=tmp, logger=None,
       decide=fake_decide, tools=[{"name": "draw_image", "description": "画图"}],
       max_queue=64, legacy_grace_ms=100)
S.reset()
for i in range(5):
    ok = S.observe_message(session_key=10 + i, is_group=True, user_id=2,
                           display_name="甲", role="member",
                           text="普通聊天 %d" % i, mid=100 + i,
                           history=[{"role": "user", "content": "前文"}])
    chk("入队 %d" % i, ok)
# 第 0 轮模拟旧路径已发消息；其余没有
S.note_outbound(10)
chk("5 轮全部处理", wait_done(5), S.stats())
st = S.stats()
chk("覆盖率 100%", st["submitted"] == 5 and st["completed"] == 5 and st["coverage"] == 1.0, st)
chk("无错误/丢弃", st["errors"] == 0 and st["dropped"] == 0, st)
chk("日志 5 行", len(Path(tmp, "decisions.jsonl").read_text(encoding="utf-8").splitlines()) == 5)
rep = S.report()
chk("报告动作计数", rep["decisions"] == 5 and rep["actions"].get("reply") == 5, rep)
chk("差异分类含 match_reply", rep["diffs"].get("match_reply", 0) >= 1, rep)

print("== 4 一键关闭 ==")
S.bind(cfg={"arch": {"shadow": False}}, state_dir=tmp, logger=None,
       decide=fake_decide, max_queue=64, legacy_grace_ms=0)
before = S.stats()["submitted"]
chk("关闸后不再入队", S.observe_message(session_key=99, is_group=True,
                                        user_id=2, text="hi", mid=999) is False)
chk("关闸后提交数不变", S.stats()["submitted"] == before, S.stats())
S.close()

print("== 4b 队列满：覆盖率不能掩盖丢弃 ==")
gate = threading.Event()


def slow_decide(messages):
    gate.wait(2)
    return '{"action":"silent","reply":"","reason":"slow"}', {"backend": "slow"}


S.bind(cfg={"arch": {"shadow": True}}, state_dir=tmp, logger=None,
       decide=slow_decide, tools=[], max_queue=1, legacy_grace_ms=0)
S.reset()
for i in range(5):
    S.observe_message(session_key=100 + i, is_group=True, user_id=2,
                      text="堆积 %d" % i, mid=500 + i)
st = S.stats()
chk("队列满：observed 记全部 5 条", st["observed"] == 5, st)
chk("队列满：接收+丢弃=5 且确有丢弃",
    st["submitted"] >= 1 and st["dropped"] >= 3
    and st["submitted"] + st["dropped"] == 5, st)
gate.set()
chk("队列满：已接收的仍完成", wait_done(st["submitted"]), S.stats())
st = S.stats()
chk("覆盖率按 observed 计算", abs(st["coverage"] - st["completed"] / 5.0) < 1e-9, st)
chk("丢弃率可见", st["drop_rate"] > 0, st)
S.close()

print("== 5 影子模块不依赖发送/工具执行 ==")
src = Path(_ROOT, "qbot_shadow.py").read_text(encoding="utf-8")
chk("不 import 发送函数", "send_group(" not in src and "send_segments(" not in src)
chk("不执行工具", "executor=" not in src and "tool_executed" not in src)

print()
print("RESULT:", "PASS" if bad == 0 else "FAIL (%d)" % bad)
sys.exit(1 if bad else 0)
