# -*- coding: utf-8 -*-
"""冷场开场去重（SAFE，2026-11-04 修「同一件旧事连开三次场」）。

主人在真实群里看到：09:00 / 11:36 / 14:54 / 15:54 四次主动开场都在说
「手机亮了一晚上」。根因是 legacy `idle_opener_tick` 只把**模型生成的那句话**
记进去重账本，而 `opener_candidates` 拿**候选原文**去比 → 永远对不上；
复习效应（touch_by_ids）还把同一条越抬越高。

本测试锁死：
  ① 开场成功后，候选原文本体必须进 purpose_seen；
  ② 下一次开场不会再用同一条记忆；
  ③ 两条都用过之后不再拿记忆卡硬凑；
  ④ 源码级：mark 的是候选原文（不是只 mark 回答）。
全离线。
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QBOT_TEST"] = "1"

FAIL = []


def chk(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  | %s" % (detail,) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


import bot  # noqa: E402
import qbot_memory_card as M  # noqa: E402

GID = 123450001
M.bind(tempfile.mkdtemp(prefix="topener_card_"),
       {"memory_card": {}, "admin": {"owner_qq": ["10001"]}})
CFG_MC = M._cfg()
with M._LOCK:
    M._CARD = M._empty_card()
    for sid, text in [("s1", "手机亮了一晚上"), ("s2", "下次一起去看那个展")]:
        rec = M._make_record(M._CARD, source="group", source_id=sid, text=text,
                             conversation_key="group:%s" % GID, manual_importance=30)
        M.add_record(M._CARD, rec, CFG_MC)
    M._CARD["meta"]["purposeHistory"] = []

bot.CFG.setdefault("sim", {}).update({"enabled": True})
bot.CFG["sim"]["idle"] = {"enabled": True, "minutes": 0, "max_per_day": 4,
                          "gap_sec": 0, "chance": 1.0, "hours": "0-24"}
bot.CFG["groups"] = [GID]
bot._last_human[GID] = time.time() - 9999
bot.context_block = lambda gid, skip=6: "CTX"
bot.system_prompt = lambda gid=None: "SYS"

MSGS = []


def fake_chat(msgs, *a, **kw):
    MSGS.append(msgs[0]["content"] if msgs and isinstance(msgs[0], dict) else "")
    return "", "开场回复"


bot.chat = fake_chat
SENT = []
bot.send_bubbles = lambda gid, text, **kw: SENT.append(text) or True


def reset_idle():
    with bot._idle_lock:
        bot._idle_state.update({"day": time.strftime("%Y%m%d"), "count": 0, "last": 0.0})


TEXTS = ("手机亮了一晚上", "下次一起去看那个展")


def _hit(prompt):
    return [t for t in TEXTS if prompt and t in prompt]


print("== ① 第一次开场 ==")
reset_idle()
MSGS[:] = []
bot.idle_opener_tick()
_first = _hit(MSGS[0] if MSGS else "")
chk("第一次用了记忆卡候选", len(_first) == 1, MSGS[:1])
chk("候选原文进了去重账本", bool(_first) and M.purpose_seen(_first[0]))

print("== ② 第二次开场不得复用同一条 ==")
reset_idle()
MSGS[:] = []
bot.idle_opener_tick()
_second = _hit(MSGS[0] if MSGS else "")
chk("第二次换了下一条记忆", bool(_second) and _second[0] not in _first, MSGS[:1])
chk("第二次没有再提同一条", bool(_first) and _first[0] not in (MSGS[0] if MSGS else ""), MSGS[:1])
chk("两条候选都已记入账本", all(M.purpose_seen(t) for t in TEXTS))

print("== ③ 两条都用过 → 不再拿记忆卡硬凑 ==")
reset_idle()
MSGS[:] = []
bot.idle_opener_tick()
chk("第三次不重复任何一条", MSGS and all(
    x not in MSGS[0] for x in ("手机亮了一晚上", "下次一起去看那个展")), MSGS[:1])

print("== ④ 源码级守卫 ==")
_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "bot.py"), encoding="utf-8").read()
chk("mark 的是候选原文（不只 mark 回答）",
    'purpose_mark(_cand["cand"].get("text")' in _src)

print("\n== 汇总 ==")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
