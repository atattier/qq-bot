# -*- coding: utf-8 -*-
"""五个群聊新功能离线测试：段识别/戳一戳/引用/转发/看图闲聊/晒图。"""
import os, sys, json
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import bot

bot.save_config = lambda: True
bad = 0
def chk(name, cond, extra=""):
    global bad
    print(("OK  " if cond else "FAIL ") + name + (" | " + str(extra) if extra and not cond else ""))
    if not cond: bad += 1

# 1) 段识别
chk("mface 占位符", bot.SEG_PLACEHOLDER.get("mface") == "[动画表情]")
chk("forward 占位符", bot.SEG_PLACEHOLDER.get("forward") == "[转发消息]")
t, *_ = bot.parse_message([{"type": "mface", "data": {"emoji_id": "1"}},
                           {"type": "text", "data": {"text": "哈"}}])
chk("parse_message 认 mface", t == "[动画表情]哈", t)

# 2) 戳一戳
chk("POKE_RE 命中", all(bot.POKE_RE.match(x) for x in ("戳", "戳一戳", "戳戳", "戳 @张三", "戳一戳！")))
chk("POKE_RE 不误伤", not bot.POKE_RE.match("戳穿一切") and not bot.POKE_RE.match("随手戳"))

API_CALLS = []
bot.api = lambda action, **kw: (API_CALLS.append((action, kw)) or
                                ({"data": {"messages": [
                                    {"sender": {"nickname": "阿甲"}, "content": [{"type": "text", "data": {"text": "今天吃什么"}}]},
                                    {"sender": {"nickname": "阿乙"}, "content": [{"type": "text", "data": {"text": "火锅!"}}]}]}}
                                 if action == "get_forward_msg" else {"data": {}}))
SENT = []
bot.send_group = lambda cid, text, **kw: SENT.append(text) or True

# 被戳 -> 戳回去
bot.SELF_ID = 123450002
bot.CONTEXT.clear(); API_CALLS.clear(); SENT.clear()
bot._poke_state["last"] = 0
bot.handle_notice({"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
                   "group_id": 123450001, "user_id": 111, "target_id": 123450002})
chk("被戳 -> group_poke 戳回", any(a == "group_poke" and kw.get("user_id") == 111 for a, kw in API_CALLS))
chk("被戳 -> 记进上下文", "戳了戳我" in (bot.CONTEXT.get(123450001) or [{}])[-1].get("text", ""))
# 互戳 -> 只记上下文不说话
API_CALLS.clear()
bot.handle_notice({"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
                   "group_id": 123450001, "user_id": 111, "target_id": 222})
chk("别人互戳 -> 不 group_poke", not any(a == "group_poke" for a, _ in API_CALLS))
chk("别人互戳 -> 记上下文", "戳了戳" in (bot.CONTEXT.get(123450001) or [{}])[-1].get("text", ""))
# 口令戳人（走 handle_poke_cmd，ats 由 parse_message 来）
API_CALLS.clear()
bot.handle_poke_cmd(123450001, ["999"])
chk("口令戳人 -> group_poke", any(a == "group_poke" and kw.get("user_id") == 999 for a, kw in API_CALLS))
API_CALLS.clear()
bot.handle_poke_cmd(123450001, [123450002])   # 只@了自己 -> 提示
chk("口令戳自己 -> 提示不走 api", not any(a == "group_poke" for a, _ in API_CALLS) and SENT)
# 3) 转发聊天记录
s = bot._forward_summary([{"type": "forward", "data": {"id": "F123"}}])
chk("转发记录摊平", "阿甲: 今天吃什么" in s and "阿乙: 火锅!" in s and "共2条" in s, s)
chk("无转发段返回空", bot._forward_summary([{"type": "text", "data": {"text": "hi"}}]) == "")

# 4) 引用回复
CAP = {}
bot._api_send = lambda cid, message, *a, **kw: CAP.setdefault("seg", message) or {"status": "ok"}
bot.send_reply(123450001, "这是一条引用回复", reply_to=42457)
chk("引用段在最前", (CAP.get("seg") or [{}])[0].get("type") == "reply"
    and (CAP.get("seg") or [{}])[0].get("data", {}).get("id") == 42457, CAP.get("seg"))
bot.send_reply(123450001, "私聊不引用", reply_to=42457) if False else None
bot.cid_capture = None

# 5) 看图认角色（2026-09-29 改版：identify 认人、不点评画面、不引用原图）
bot.reverser = type("R", (), {
    "describe": staticmethod(lambda p, c: "a cute cat in a box"),
    "identify": staticmethod(lambda p, c, *a, **k:
        {"known": True, "name": "Hakurei Reimu", "series": "Touhou Project",
         "confidence": 0.9, "brief": "", "tags": "", "via": "test"}),
})()
bot.chat = lambda msgs, *a, **k: ("x", "哈哈这猫好圆润")
bot._fetch_rev_image = lambda d: "fake.jpg"
# 隔离网络（2026-11-04）：看图链路会先走 webai_identify_chat；以前它网络失败返回 {}，
# 现在环境能连上/偶尔抛错，会让这几条「认不出就闭嘴」的用例变成真联网的碰运气。
bot.webai_identify_chat = lambda src: {}
# 本机 config 开了 ai.omni_brain ⇒ 看图会走真「总控大脑」（真 LLM，12s+），
# 这条 SAFE 用例必须打桩在旧链路，否则结果取决于网络/模型当天发挥。
bot._omni_on = lambda: False
bot.brain_sees_image_on = lambda: False
BUB = []
bot.send_bubbles = lambda gid, text, want_image=True, reply_to=0, **kw: BUB.append((text, reply_to)) or True
SEEN = {}
bot.chat = lambda msgs, *a, **k: SEEN.update(sys=msgs[0]["content"]) or ("x", "哈哈这猫好圆润")
bot.sim_pic_chat(123450001, "小明", [{"url": "http://x/y.jpg"}], True, 555)
chk("看图认角色出话", BUB and BUB[0][0] == "哈哈这猫好圆润")
chk("认角色任务带名字", "Hakurei Reimu" in SEEN.get("sys", ""), SEEN.get("sys", "")[:120])
chk("看图闲聊不引用原图", BUB and BUB[0][1] == 0, BUB)
# 认不出 → 闭嘴（不点评画面）
bot.reverser = type("R", (), {
    "describe": staticmethod(lambda p, c: "a cute cat in a box"),
    "identify": staticmethod(lambda p, c, *a, **k:
        {"known": False, "name": "", "series": "", "confidence": 0.0,
         "brief": "", "tags": "", "via": "test"}),
})()
bot.chat = lambda msgs, *a, **k: ("x", "哈哈这猫好圆润")
BUB.clear()
bot.sim_pic_chat(123450001, "小明", [{"url": "http://x/y.jpg"}], True, 555)
chk("认不出角色就闭嘴", not BUB)
# SILENT 就闭嘴
bot.reverser = type("R", (), {
    "describe": staticmethod(lambda p, c: "a cute cat in a box"),
    "identify": staticmethod(lambda p, c, *a, **k:
        {"known": True, "name": "Hakurei Reimu", "series": "", "confidence": 0.9,
         "brief": "", "tags": "", "via": "test"}),
})()
bot.chat = lambda msgs, *a, **k: ("x", "[SILENT]")
BUB.clear()
bot.sim_pic_chat(123450001, "小明", [{"url": "http://x/y.jpg"}], False, 0)
chk("看图 [SILENT] 闭嘴", not BUB)

# 6) 随机晒图（模型输出 [图] 标记）
IMGS = []
bot.send_image = lambda gid, name, caption="": IMGS.append(name) or True
BUB.clear()
bot.CFG.setdefault("sim", {})["gallery_chance"] = 0
bot.sim_allowed = lambda *a, **kw: True   # 2026-10-19: sim_allowed 多了 uid 参数，桩必须收 **kw
bot.chat = lambda msgs, *a, **k: ("x", "[图] 发张存货")
bot.context_block = lambda gid, skip=8: "群友: 哈"
bot.sim_join(123450001, "小明", "有没有好图")
chk("[图] 插话带图", IMGS == ["随机"], IMGS)
chk("[图] 文案照发", BUB and BUB[0][0] == "发张存货")
# 概率晒图：chance=100 必中（冷却刚被上一步刷新？没有——[图]路径不走 _gallery_roll）
bot.chat = lambda msgs, *a, **k: ("x", "普通插话")
bot.CFG["sim"]["gallery_chance"] = 100
bot._gal_state["last"] = 0
IMGS.clear(); BUB.clear()
bot.sim_join(123450001, "小明", "继续聊")
chk("概率晒图", IMGS == ["随机"] and BUB[0][0] == "普通插话", (IMGS, BUB))

# 7) 旋钮都注册了
for lab in ("看图闲聊", "看图冷却", "晒图概率", "晒图冷却", "戳回"):
    if not bot.knob_find(lab):
        chk("旋钮 " + lab, False); bad += 1
else:
    chk("5 个新旋钮都在", True)

# 8) NapCat 换号后 @判定要跟着换（2026-10-20「服从性没落实」）
AT_ME = [{"type": "at", "data": {"qq": "123450004", "name": ""}},
         {"type": "text", "data": {"text": " 做题"}}]
bot.SELF_ID, bot.SELF_NAME = 123450002, "小鲸"
chk("旧号时 @新号 不算@我", bot.parse_message(AT_ME)[1] is False)
bot.api = lambda action, **kw: {"data": {"user_id": 123450004, "nickname": "低空段云"}}
CAP2 = []
_real_hgm, bot.handle_group_message = bot.handle_group_message, (lambda d: CAP2.append(d))
try:
    bot.dispatch({"self_id": 123450004, "post_type": "message", "message_type": "group",
                  "group_id": 123450001, "user_id": 111, "message": AT_ME,
                  "sender": {"card": "甲"}})
finally:
    bot.handle_group_message = _real_hgm
chk("dispatch 跟着换号", bot.SELF_ID == 123450004 and bot.SELF_NAME == "低空段云",
    (bot.SELF_ID, bot.SELF_NAME))
chk("换号后 @我 判对", bot.parse_message(AT_ME)[1] is True)
# 10) @ 占位带尾随空格（2026-11-05）：「@某人亲」不能被拼成一个词，
#     否则 meme 口令剥离 @ 占位时会把「亲」一起吃掉（真机踩坑）
_AT_NOSPACE = [{"type": "at", "data": {"qq": "222002", "name": "张三"}},
               {"type": "text", "data": {"text": "亲"}}]
_t, _a, _ats, _q = bot.parse_message(_AT_NOSPACE)
chk("@ 占位与后文有空格隔开", _t == "@张三 亲", repr(_t))
chk("ats 照常收集", _ats == ["222002"], _ats)
chk("换号后名字表更新", "低空段云" in bot.bot_names(), bot.bot_names())
chk("事件照常投递", len(CAP2) == 1)
bot.SELF_ID, bot.SELF_NAME = 123450002, "小鲸"

# 9) agent 干活时：插话让路 + 太闷要应一声（2026-10-20「看着像放弃」）
import threading as _th, time as _t
G = 123450001
bot.CFG.setdefault("sim", {})["enabled"] = True
bot.CFG.setdefault("agent", {})["ack_after_sec"] = 0.35
bot._agent_inflight.clear()
bot._last_agent_send.pop(G, None)
_real_locked = bot._run_agent_locked
CHAT_SEEN = []
def _spy_chat(msgs, *a, **k):
    CHAT_SEEN.append(msgs[0]["content"])
    return "x", "这题我在算，等我两分钟喵"
bot.chat = _spy_chat
bot._run_agent_locked = lambda gid, content, quote="", who="", uid="", **kw: (
    _t.sleep(0.45) or (True, "答案正文", False))
BUB.clear()
_r1 = _th.Thread(target=bot.run_agent, args=(G, "做题")); _r1.start()
_t.sleep(0.25)
chk("干活中算 in-flight", bot.agent_working(G))
BUB.clear(); CHAT_SEEN.clear()
bot.sim_join(G, "群友甲", "不是为什么啊")
chk("干活时插话让路", not CHAT_SEEN and not BUB, (CHAT_SEEN, BUB))
_r1.join(timeout=5)
chk("跑完清 in-flight", not bot.agent_working(G))
chk("闷太久应一声", any("在做了" in s for s in CHAT_SEEN) and BUB, (CHAT_SEEN[:1], BUB))
# agent 这一轮里已经自己开过口的，不再补应声
bot._run_agent_locked = lambda gid, content, quote="", who="", uid="", **kw: (
    bot._last_agent_send.__setitem__(int(gid), _t.time()), _t.sleep(0.45),
    (True, "答案正文", False))[-1]
CHAT_SEEN.clear(); BUB.clear()
_th2 = _th.Thread(target=bot.run_agent, args=(G, "再做题")); _th2.start(); _th2.join(timeout=5)
chk("本轮已发言就不应声", not BUB, BUB)
bot._run_agent_locked = _real_locked
bot._last_agent_send.pop(G, None)

print("RESULT:", "PASS" if bad == 0 else "FAIL")
