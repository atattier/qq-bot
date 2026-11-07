# -*- coding: utf-8 -*-
"""人味阶段 C 真机验收（MANUAL，真 LLM 会花钱，约 2 次调用）。

⚠️ 与 `_tpurpose.py`（全打桩）**分开报告**：
  · 打桩测试证明确定性逻辑（池/去重/闸门/调度/记账）；
  · 这里只用真模型确认「素材 → 提示词 → 模型输出」这条链能跑通，
    并**如实记录模型是选择开口还是 [SILENT]** —— 选 SILENT 不算主动发言成功。

运行：python tests/_tphaseC.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.argv = ["bot-test"]
os.environ["QBOT_TEST"] = "1"

FAIL = []


def chk(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  | %s" % (detail,) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


import bot  # noqa: E402
from qbot import qbot_purpose as P  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="phaseC_live_"))
# 真机验收：发送打桩（绝不真发到群里），其余走真实链路
SENT = []
bot.send_bubbles = lambda gid, text, **kw: SENT.append(text) or True
bot._memory_log_turn = lambda *a, **k: None

NOW = time.time()
CANDS = [
    {"id": 9001, "text": "上周说好一起去看那个展，结果加班没去成",
     "importance": 26, "clarity": "vivid", "createdAt": NOW - 2 * 86400},
    {"id": 9002, "text": "上次聊天提到想养一只橘猫，名字还没想好",
     "importance": 20, "clarity": "clear", "createdAt": NOW - 3 * 86400},
]
P.bind(cfg={"purpose": {"enabled": True}}, memory_candidates=lambda g: list(CANDS),
       purpose_seen=lambda t: False, purpose_mark=lambda t: None,
       touch_ids=lambda ids: None, chat=bot.chat, system_prompt=bot.system_prompt,
       is_placeholder=bot.placeholder_only, state_dir=str(TMP))


def one_call(tag):
    """走一次 decide → 真 LLM → parse → finish。返回 (决策, 模型答案, 是否真开口)。"""
    with P._lock:
        P._STATE["gid"].pop("123450001", None)
    st = P._gstate(123450001)
    st["next_fire_at"] = NOW - 1
    dec = P.decide(123450001, now=NOW, cold_minutes=95, owner_last_ts=0, mood_bias=0.6,
                   ctx="（群里最近聊过：周末去哪玩）")
    if dec.get("action") != "speak":
        print("  [%s] 未到开口：%s" % (tag, dec.get("reason")))
        return dec, "", False
    print("  [%s] 选中 goal: %r（conf=%.2f risk=%.2f）"
          % (tag, dec["goal"].get("text"), dec["goal"].get("confidence"),
             dec["goal"].get("risk")))
    prompt = dec["messages"][0]["content"]
    chk("%s：提示词含归属说明" % tag, "你自己记得" in prompt)
    chk("%s：提示词含 [SILENT] 出口" % tag, "[SILENT]" in prompt)
    _, ans = bot.chat(dec["messages"])
    kind, text = P.parse_answer(ans)
    print("  [%s] 模型答案: %r → %s" % (tag, str(ans)[:80],
                                       "开口" if kind == "text" else "[SILENT]（未发言）"))
    if kind != "text":
        P.finish(123450001, dec["goal_id"], sent=False, silent=True, now=NOW)
        return dec, str(ans), False
    sent = bool(bot.send_bubbles(123450001, text))     # 打桩发送；不是群里的真实消息
    P.finish(123450001, dec["goal_id"], sent=sent, now=NOW)
    return dec, str(ans), sent


print("== 阶段 C 真机验收（2 次真调用，发送打桩）==")
d1, a1, s1 = one_call("素材1")
chk("素材1：模型输出形状合法（正文或 [SILENT]）", bool(str(a1).strip()))
d2, a2, s2 = one_call("素材2")
chk("素材2：模型输出形状合法（正文或 [SILENT]）", bool(str(a2).strip()))

print("\n== 真机结论（如实） ==")
print("素材1：%s" % ("模型开口：" + str(a1)[:60] if s1 else "模型选择 [SILENT] / 未发言"))
print("素材2：%s" % ("模型开口：" + str(a2)[:60] if s2 else "模型选择 [SILENT] / 未发言"))
print("注意：发送是打桩的，本测试不产生真实群消息；[SILENT] 不计为主动发言成功。")
print("\n失败 %d 条（结构性问题才算失败；模型选 SILENT 不算失败，也不算成功）" % len(FAIL))
sys.exit(1 if FAIL else 0)
