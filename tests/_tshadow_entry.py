# -*- coding: utf-8 -*-
"""M1 入口接线：真实 _handle_message 在影子开/关时旧路径发送完全一致。

全离线：legacy 大脑换成固定桩；影子模型也是假 decide。
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
os.environ["QBOT_TEST"] = "1"
os.environ.pop("QBOT_ARCH_SHADOW", None)
sys.stdout.reconfigure(encoding="utf-8")

import bot  # noqa: E402

bad = 0


def chk(name, ok, why=""):
    global bad
    if not ok:
        bad += 1
    print(("  [OK] " if ok else "  [失败] ") + name + ("" if ok else " | %s" % (why,)))


def fake_decide(messages):
    return json.dumps({"action": "reply", "reply": "影子回复", "reason": "测试",
                       "confidence": 1.0}), {"backend": "fake"}


SENT = []
bot.SELF_ID = 9990001
bot.CFG.setdefault("keywords", [])
bot.CFG["keywords"] = []
bot.CFG.setdefault("admin", {})["silent"] = False


def _fake_api_send(cid, payload, group_hint=None):
    SENT.append((cid, payload))
    return {"status": "ok", "retcode": 0}


# 保留真实 send_group：只有它内部的出口钩子能记录 legacy 已发送。
bot._api_send = _fake_api_send
bot.api = lambda action, **kw: {"data": {}, "status": "ok", "retcode": 0}
bot._route_group_ambient = lambda *a, **kw: None


def _legacy_brain(*a, **kw):
    bot.send_group(a[0], "旧路径回复")
    return True


bot._route_brain_reply = _legacy_brain

tmp = tempfile.mkdtemp(prefix="shadow_entry_")
bot.qbot_shadow.bind(cfg=bot.CFG, state_dir=tmp, logger=None, decide=fake_decide,
                     tools=[], max_queue=32, legacy_grace_ms=200)
bot.qbot_shadow.reset()

_MID = [700001]


def run(text="你好", shadow=True, group=False, at_self=False):
    _MID[0] += 1
    bot.CFG["arch"] = {"shadow": bool(shadow)}
    segs = []
    if at_self:
        segs.append({"type": "at", "data": {"qq": str(bot.SELF_ID)}})
    segs.append({"type": "text", "data": {"text": text}})
    data = {"post_type": "message",
            "message_type": "group" if group else "private",
            "group_id": 8123, "user_id": 123456,
            "sender": {"role": "member", "nickname": "甲"},
            "message_id": _MID[0], "message": segs}
    gid = 8123 if group else bot.priv_key(123456)
    before = len(SENT)
    bot._handle_message(data, gid, is_group=group)
    return SENT[before:]


print("== 0 一键关闭的真实配置路径 ==")
chk("影子只允许本机模型（云端档不碰）",
    bot._shadow_local_provider("ollama_fast")
    and not bot._shadow_local_provider("modelscope"),
    (bot._shadow_local_provider("ollama_fast"),
     bot._shadow_local_provider("modelscope")))
_saved_arch = bot.CFG.get("arch", "__absent__")
bot.CFG.pop("arch", None)
os.environ.pop("QBOT_ARCH_SHADOW", None)
chk("生产配置无 arch.shadow 时默认关", bot.qbot_shadow.enabled() is False)
os.environ["QBOT_ARCH_SHADOW"] = "1"
chk("环境变量可临时开", bot.qbot_shadow.enabled() is True)
bot.CFG["arch"] = {"shadow": True}
os.environ["QBOT_ARCH_SHADOW"] = "0"
chk("环境变量 0 可一键关", bot.qbot_shadow.enabled() is False)
os.environ.pop("QBOT_ARCH_SHADOW", None)
if _saved_arch == "__absent__":
    bot.CFG.pop("arch", None)
else:
    bot.CFG["arch"] = _saved_arch

print("== 1 影子关闭：旧路径不变 ==")
bot.CFG["arch"] = {"shadow": False}
off = run("你好", shadow=False)
chk("旧路径照常发送", len(off) == 1 and off[0][1] == "旧路径回复", off)
chk("关闭时影子不提交", bot.qbot_shadow.stats()["submitted"] == 0, bot.qbot_shadow.stats())

print("== 2 影子开启：旧路径发送逐字节一致 ==")
on = run("你好", shadow=True)
chk("旧路径发送相同", on == off, (off, on))
chk("入口产生 Turn", bot.qbot_shadow.stats()["submitted"] == 1, bot.qbot_shadow.stats())
end = time.time() + 3
while time.time() < end and bot.qbot_shadow.stats()["completed"] < 1:
    time.sleep(0.02)
st = bot.qbot_shadow.stats()
chk("影子完成 1 轮", st["completed"] == 1 and st["coverage"] == 1.0, st)
lines = Path(tmp, "decisions.jsonl").read_text(encoding="utf-8").splitlines()
rec = json.loads(lines[0])
chk("Turn 带 test_mute 标记", (rec["turn"]["flags"] or {}).get("test_mute") is True, rec["turn"]["flags"])
chk("test_mute 被 Policy 判 silent", rec["decision"]["action"] == "silent",
    rec["decision"])
chk("发送观测记到旧路径", rec["legacy"]["outbound"] is True, rec["legacy"])
chk("差异分类可复核", rec["diff"] == "shadow_silent_legacy_speaks", rec["diff"])

print("== 2a 影子即使判 reply 也绝不发送 ==")
bot.qbot_shadow.reset()
_sent_before = len(SENT)
bot.qbot_shadow.bind(cfg=bot.CFG, state_dir=tmp, logger=None, decide=fake_decide,
                     tools=[], max_queue=32, legacy_grace_ms=0)
bot.qbot_shadow.observe_message(session_key=-999, is_group=False, user_id=999,
                                display_name="甲", text="来点反应",
                                mid=900001, test_mute=False, silent=False)
_end = time.time() + 3
while time.time() < _end and bot.qbot_shadow.stats()["completed"] < 1:
    time.sleep(0.02)
chk("影子判 reply 但发送计数不变", len(SENT) == _sent_before, SENT[_sent_before:])
_rec = json.loads(Path(tmp, "decisions.jsonl").read_text(encoding="utf-8").splitlines()[-1])
chk("该轮被分类为影子多话", _rec["diff"] == "shadow_speaks_legacy_silent",
    (_rec["diff"], _rec["turn"]["flags"], _rec["decision"], bot.qbot_shadow.stats()))

print("== 2b 群聊/点名/潜水：开关两态旧路径发送仍逐字节一致 ==")
for label, kw in (("群聊被@", {"group": True, "at_self": True}),
                  ("群聊潜水", {"group": True, "at_self": False})):
    bot.qbot_shadow.reset()
    off2 = run("你好", shadow=False, **kw)
    on2 = run("你好", shadow=True, **kw)
    chk("%s：发送相同" % label, on2 == off2, (off2, on2))
    chk("%s：入口产生 Turn" % label, bot.qbot_shadow.stats()["submitted"] == 1,
        bot.qbot_shadow.stats())

print("== 3 再次关闭：不再产生影子记录 ==")
before = bot.qbot_shadow.stats()["submitted"]
run("你好", shadow=False)
chk("关闭后提交数不变", bot.qbot_shadow.stats()["submitted"] == before, bot.qbot_shadow.stats())
bot.qbot_shadow.close()

print()
print("RESULT:", "PASS" if bad == 0 else "FAIL (%d)" % bad)
sys.exit(1 if bad else 0)
