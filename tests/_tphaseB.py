# -*- coding: utf-8 -*-
"""人味阶段 B 真机验收（MANUAL，真 LLM 会花钱，约 2 次调用）。

任务书红线：「不得用单纯 mock 结果代替关键链路的真实测试」。
SAFE 套里的 _ttiming 证明的是确定性逻辑（概率权重/自校准/退避/WAIT 状态机/总闸关）；
这个文件用**真模型**跑插话提示词，确认 WAIT 出口在真实链路里能跑通：

  B1 总闸关：提示词里**没有** [WAIT] 选项（关闸 = 旧行为逐字节不变）
  B2 总闸开：提示词确实给出 [WAIT:秒] 选项；真模型的输出要么正常接话/[SILENT]，
     要么是**合法** [WAIT:秒]（parse_wait 吃得下、且不进发送）
  B3 被叫名字：提示词要求「必须立刻答，不许 WAIT」；模型输出不许是 WAIT

运行：python tests/_tphaseB.py   （约 2 次真调用）
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.argv = ["bot-test"]
os.environ.setdefault("QBOT_TEST", "1")

FAIL = []


def chk(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  | %s" % (detail,) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


import bot  # noqa: E402

# ⚠️ import bot 的 _bind_modules() 会把 qbot_timing 绑到真实 state 目录 —— 立刻盖回临时目录
from qbot import qbot_timing as T  # noqa: E402
TMP = Path(tempfile.mkdtemp(prefix="phaseB_live_"))
T.bind(cfg=bot.CFG, names=bot.bot_names, self_id=lambda: bot.SELF_ID,
       context_items=lambda g: list(bot.CONTEXT.get(g) or []),
       is_placeholder=bot.placeholder_only, state_dir=str(TMP))
T.CFG["timing"] = {"enabled": False, "likelihood_min": 0.35, "talk_value": 1.0,
                   "backoff_cap": 300, "bypass_pending": 6, "wait_max": 180,
                   "opener_backoff": True}

GID = 123450001
SENT = []
PROMPTS = []

# 不真发、不写记忆库：只验提示词与判定链路
bot.send_bubbles = lambda gid, text, ctx_text="": SENT.append(text)
bot.send_image = lambda gid, kw="": SENT.append("[图]" + str(kw))
bot._memory_log_turn = lambda *a, **k: None
bot.sim_allowed = lambda *a, **k: True
bot._gallery_roll = lambda sim: False
_CTX_TEXT = "【群聊记录】\n阿伟：等下我翻翻上次那张图\n小鱼：翻吧翻吧\n"


def _run(tag, text, stub=None, uid="10002", who="阿伟"):
    """跑一次 sim_join，返回 (prompt文本, 模型答案)。stub 给了就桩掉这次调用。"""
    PROMPTS[:] = []
    SENT[:] = []
    ANSWER = [""]
    real_chat = bot.chat

    def cap(msgs, *a, **kw):
        PROMPTS.append("\n".join(str(m.get("content") if isinstance(m, dict) else m) for m in msgs))
        if stub is not None:
            ANSWER[0] = stub
            return "", stub
        out = real_chat(msgs, *a, **kw)
        try:
            ANSWER[0] = out[1] if isinstance(out, (tuple, list)) and len(out) > 1 else str(out)
        except Exception:  # noqa: BLE001
            ANSWER[0] = str(out)
        return out

    bot.chat = cap
    try:
        bot.sim_join(GID, who, text, uid=uid)
    finally:
        bot.chat = real_chat
    prompt = PROMPTS[-1] if PROMPTS else ""
    print("  [%s] prompt=%d字 答案=%r 发送=%r" % (tag, len(prompt), ANSWER[0][:40], SENT))
    return prompt, ANSWER[0]


def section(t):
    print("\n== %s ==" % t)


bot.CFG.setdefault("sim", {}).update({
    "enabled": True, "cooldown_sec": 0, "max_per_hour": 999, "min_gap_msgs": 0,
    "min_activity": 0, "single_quiet": True, "sticker_quiet": True, "inject": 8,
})
bot._sim_state.update({"last": 0.0, "count": 0})
bot.CONTEXT.clear()
bot.CONTEXT[GID] = [{"t": 0, "qq": "10002", "who": "阿伟", "text": _CTX_TEXT}]

# ============================== B1：总闸关（桩，不花钱） ==============================
section("B1 总闸关 → 提示词没有 [WAIT] 选项")
T.CFG["timing"]["enabled"] = False
p1, _ = _run("关闸", "等下我找找图", stub="[SILENT]")
chk("关闸提示词不含 [WAIT:", "[WAIT:" not in p1)
chk("关闸也不进发送", SENT == [], SENT)

# ============================== B2：总闸开（真模型 1 次） ==============================
section("B2 总闸开 → 真模型看到 [WAIT] 选项")
T.CFG["timing"]["enabled"] = True
T.CFG["timing"]["wait_max"] = 180
p2, a2 = _run("开闸", "等下我找找图，找到了发你们")
chk("开闸提示词给出 [WAIT:秒] 选项", "可以只输出 [WAIT:" in p2, p2[-200:])
print("  模型答案: %r" % a2[:80])
_ok_shape = (not a2.strip()) or ("SILENT" in a2.upper()) or a2.upper().startswith("[WAIT") \
    or ("[WAIT" not in a2.upper())
chk("模型输出是合法形状（接话/[SILENT]/[WAIT:秒]，不夹半截标签）", _ok_shape, a2[:80])
if a2.upper().startswith("[WAIT"):
    _sec, _ = T.parse_wait(a2, 180)
    chk("真模型给的 [WAIT] 能被 parse_wait 解析", _sec > 0, (a2, _sec))
    chk("[WAIT] 不消耗发送（SENT 为空）", SENT == [], SENT)
else:
    print("  （模型这次没选 WAIT —— 正常，WAIT 是选项不是义务）")

# ============================== B3：被叫名字（真模型 1 次） ==============================
section("B3 被叫名字 → 不许 WAIT")
_nm = (list(bot.bot_names()) or ["小鲸"])[0]
p3, a3 = _run("点名", "%s，在吗？这个怎么弄" % _nm, uid="123450002", who="主人")
chk("点名场景提示词写明「不许 WAIT」", "不许 WAIT" in p3, p3[-300:])
chk("点名时模型没有输出 WAIT", not a3.upper().startswith("[WAIT"), a3[:60])

print("\n== 汇总 ==")
print("失败 %d 条（真机验收，失败=提示词或链路有问题，不是预算问题）" % len(FAIL))
sys.exit(1 if FAIL else 0)
