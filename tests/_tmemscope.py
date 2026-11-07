# -*- coding: utf-8 -*-
"""R1：记忆跨会话隔离（SAFE，临时库，不碰生产 state）。

验收边界：
  group A / group B / private A / private B 四段各写独特哨兵；
  每个会话的**真实 Prompt 块**只能看见自己的哨兵；
  当前会话自身记忆正常可见；记忆卡沿用原有 group/private 过滤。

覆盖真实注入链：_memory_ctx_blocks（ai_reply 的公共记忆段）、
sim_join、profile_block、render_recent、render_person、
recall_block。全部打桩，不联网、不发 QQ。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QBOT_TEST"] = "1"

FAIL = []


def chk(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  | %s" % (detail,) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


import bot  # noqa: E402
from qbot import qbot_memory as M  # noqa: E402
from qbot import qbot_memory_card as MC  # noqa: E402

GA, GB = 812900001, 812900002
PA, PB = -19001, -19002

bot.CFG.setdefault("memory", {}).update({
    "enabled": True, "cross_context": True, "embed": False,
    "recall_n": 4, "recall_chars": 320, "recent_turns": 12,
    "inject_people": 8, "inject_facts": 3,
})
bot.CFG.setdefault("memory_card", {})["enabled"] = True
bot.CFG.setdefault("sim", {}).update({"enabled": True, "inject": 6})

# 四段数据：同一个人（10001/10002）跨场合各写一条，专门验证「全局人物事实」
for cid, qq, name, tag in [(GA, "10001", "甲", "GA"), (GB, "10002", "乙", "GB"),
                           (PA, "10001", "甲", "PA"), (PB, "10002", "乙", "PB")]:
    M.note_person(cid, qq, name)
    M.remember_fact(cid, qq, "FACT_%s_ALPHA" % tag, name=name, kind="note")
    M.log_turn("private:%d" % abs(cid) if cid < 0 else "group:%d" % cid,
               name, qq, "TURN_%s_ALPHA" % tag)

SCOPES = {
    "GA": (GA, "group:%d" % GA, "甲", "10001"),
    "GB": (GB, "group:%d" % GB, "乙", "10002"),
    "PA": (PA, "private:19001", "甲", "10001"),
    "PB": (PB, "private:19002", "乙", "10002"),
}
ALL = ["GA", "GB", "PA", "PB"]


def foreign(text, own):
    return [t for t in ALL if t != own and ("FACT_%s" % t) in text or t != own and ("TURN_%s" % t) in text]


print("== ① _memory_ctx_blocks（ai_reply/agent/sim 的共同记忆段） ==")
for own, (cid, src, who, uid) in SCOPES.items():
    blk = "\n".join(bot._memory_ctx_blocks(cid, uid, who, "FACT"))
    chk("%s 能看到自己的事实" % own, ("FACT_%s_ALPHA" % own) in blk, blk[:120])
    chk("%s 能看到自己的对话" % own, ("TURN_%s_ALPHA" % own) in blk, blk[:120])
    chk("%s 看不到其他会话" % own, not foreign(blk, own), foreign(blk, own))

print("== ② render_recent / render_person / recall_block / profile_block ==")
for own, (cid, src, who, uid) in SCOPES.items():
    rr = M.render_recent(12, only_src=src)
    chk("render_recent %s 只含自己" % own,
        ("TURN_%s_ALPHA" % own) in rr and not foreign(rr, own), rr[:100])
    rp = M.render_person(uid, who, scope=cid)
    chk("render_person %s 只含自己" % own,
        ("FACT_%s_ALPHA" % own) in rp and not foreign(rp, own), rp[:100])
    rb = M.recall_block("FACT", cid, 4, 320, scope=cid)
    chk("recall_block %s 只含自己" % own,
        ("FACT_%s_ALPHA" % own) in rb and not foreign(rb, own), rb[:100])
pb = M.profile_block(GA, scope=GA)
chk("profile_block 群 A 只含 A 的事实", "FACT_GA_ALPHA" in pb and not foreign(pb, "GA"), pb[:120])

print("== ④ sim_join Prompt ==")
CAP = []
bot.chat = lambda msgs, *a, **kw: (CAP.append(msgs[0]["content"]), ("", "好的"))[1]
bot.send_bubbles = lambda *a, **kw: True
bot.sim_allowed = lambda *a, **kw: True
for own in ("GA", "PA"):
    cid, src, who, uid = SCOPES[own]
    CAP.clear()
    bot.sim_join(cid, who, "FACT 问一句", uid=uid)
    txt = CAP[0] if CAP else ""
    chk("sim_join %s 只含自己" % own,
        ("FACT_%s_ALPHA" % own) in txt and not foreign(txt, own), txt[:120])

print("== ⑤ ai_reply Prompt ==")
bot._omni_on = lambda: False
bot.brain_sees_image_on = lambda: False
bot.CFG.setdefault("ai", {}).update({"provider": "custom", "omni_brain": False})
CAP.clear()
bot.ai_reply(GA, "FACT 问一句", want_image=False, who="甲", uid="10001")
txt = CAP[0] if CAP else ""
chk("ai_reply 群 A 只含自己", ("FACT_GA_ALPHA" in txt) and not foreign(txt, "GA"), txt[:120])

print("== ⑥ memory card 原有过滤不变 ==")
with MC._LOCK:
    MC._CARD = MC._empty_card()
    for ck, text, priv in [("group:%d" % GA, "CARD_GA_ALPHA", False),
                           ("private:19001", "CARD_PA_ALPHA", True)]:
        rec = MC._make_record(MC._CARD, source="group" if not priv else "private",
                              source_id=text, text=text, conversation_key=ck,
                              private=priv, manual_importance=30)
        MC.add_record(MC._CARD, rec, MC._cfg())
gblk = MC.render_block("group:%d" % GA)
pblk = MC.render_block("private:19001")
chk("memory card 群视图不含私聊", "CARD_GA_ALPHA" in gblk and "CARD_PA_ALPHA" not in gblk, gblk[:120])
chk("memory card 私聊视图不含群聊", "CARD_PA_ALPHA" in pblk and "CARD_GA_ALPHA" not in pblk, pblk[:120])

print("== ⑦ 源码守卫：Prompt 注入点必须传 scope ==")
_src = open("bot.py", encoding="utf-8").read()
chk("_memory_ctx_blocks 三个子块都带 scope",
    "render_person(uid, who, scope=cid)" in _src
    and "only_src=_src_of(cid)" in _src
    and "scope=cid" in _src)
chk("agent/recall 工具也按会话过滤",
    "scope=gid" in _src and "recall(gid" in _src)

print("\n== 汇总 ==")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
