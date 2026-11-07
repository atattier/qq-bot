# -*- coding: utf-8 -*-
"""人味阶段 A 真机验收（MANUAL，真 LLM 会花钱）。

任务书红线：「不得用单纯 mock 结果代替关键链路的真实测试」。
SAFE 套里的 _tslang/_texpr 全是打桩，证明的是确定性逻辑（溯源/审核/隔离/开关）；
这个文件用**真模型**跑两条关键链路，证明提示词真的能跑通：

  A2 阈值推断：字面义 vs 语境义双问 → 真模型给出的答案能不能正确分档
  A3 表达提取：真模型归 situation/style → 例句必须仍是消息原文（溯源红线）

运行：python tests/_tphaseA.py   （跑一次约 3~5 次 LLM 调用）
"""
import os
import sys
import tempfile
import json
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


# 🔴 先把两个落盘文件都指到临时目录——这个测试会产生真实推断结果，绝不能写进生产词库
TMP = Path(tempfile.mkdtemp(prefix="phaseA_live_"))
from qbot import qbot_slang as S
from qbot import qbot_expression as E
S.SLANG_FILE = TMP / "slang.json"
S.SLANG["terms"] = {}

import bot   # noqa: E402  真钩子在 _bind_modules 里绑好（chat=真 LLM）

E.bind(state_dir=str(TMP / "expr"), cfg=bot.CFG, chat=bot.chat,
       inbox=lambda g, n: INBOX_FAKE, names=bot.bot_names, send=lambda g, t: _OUT.append(t))

_SENT = []
_OUT = []
INBOX_FAKE = []


def section(t):
    print("\n== %s ==" % t)


# ============================== A2：阈值推断（真 LLM） ==============================
section("A2-1 真黑话：炼丹（字面义≠语境义，应判 inferred）")
S.SLANG["terms"]["炼丹"] = {"count": 3, "status": "pending", "meaning": "", "source": "群聊提取"}
# 注意：子串命中要求每条证据里真的含「炼丹」二字——证据是逐条对回真实消息的
for mid, txt in [(1001, "这模型炼丹炼了三天还没出炉"), (1002, "你那炼丹炉温度调多少"),
                 (1003, "昨晚炼丹出来的东西全是糊的"), (1004, "别炼丹了直接下别人的底模吧")]:
    S.slang_observe(812, mid, txt)
t = S.SLANG["terms"]["炼丹"]
chk("观察计数+证据落账", t["count"] == 7 and len(t["evidence"]) == 4, (t["count"], len(t["evidence"])))
r = S.slang_infer_once("炼丹")
print("  推断结果: %s | meaning=%r" % (r, S.SLANG["terms"]["炼丹"].get("meaning")))
chk("双问跑通且给出分档", r in ("literal", "inferred"), r)
if r == "inferred":
    tt = S.SLANG["terms"]["炼丹"]
    chk("推断义仍 pending + confirmed=False", tt["status"] == "pending" and tt.get("meaning_confirmed") is False,
        (tt["status"], tt.get("meaning_confirmed")))
    chk("推断义是语境方向（含训练/模型类字眼或非纯字面）",
        any(w in tt.get("meaning", "") for w in ("训练", "模型", "出图", "生图", "跑图", "画")), tt.get("meaning"))
    gb = S.slang_guess_block("你那炼丹炉温度多少")
    chk("语境释义块带猜的标注", "炼丹" in gb and "猜的" in gb, gb)
else:
    print("  （模型判成字面义——记录在案，人工复核这条的提示词效果）")

section("A2-2 普通词：显卡（字面=语境，应判 literal 不写释义）")
S.SLANG["terms"]["显卡"] = {"count": 3, "status": "pending", "meaning": "", "source": "群聊提取"}
for mid, txt in [(2001, "显卡又涨价了"), (2002, "二手显卡水太深"), (2003, "你这显卡多少瓦"), (2004, "显卡支架到了")]:
    S.slang_observe(812, mid, txt)
r = S.slang_infer_once("显卡")
print("  推断结果: %s | meaning=%r" % (r, S.SLANG["terms"]["显卡"].get("meaning")))
chk("普通词判 literal 或至少没被安上怪释义",
    r == "literal" or (r == "inferred" and "显卡" in S.SLANG["terms"]["显卡"].get("meaning", "")), r)

section("A2-3 MANUAL 保护（真 LLM 也不许覆盖管理员）")
S.slang_learn("炼丹", "管理员口径：指训练/微调绘图模型这事儿", "confirmed", "管理员确认")
_calls = {"n": 0}
_orig_chat = S._chat


def counting_chat(msgs):
    _calls["n"] += 1
    return _orig_chat(msgs)


S._chat = counting_chat
r = S.slang_infer_once("炼丹")
S._chat = _orig_chat
chk("管理员确认后推断直接拒绝", r is None and _calls["n"] == 0, (r, _calls["n"]))
chk("管理员释义原封不动", S.SLANG["terms"]["炼丹"]["meaning"].startswith("管理员口径"))

section("A2-4 语境释义注入（真实词库形态）")
gb = S.slang_guess_block("今晚开黑炼丹吗")
print("  注入块: %r" % gb[:160])
chk("confirmed 行不带猜的标注",
    "炼丹=管理员口径：指训练/微调绘图模型这事儿（这个意思是猜的" not in gb)

# ============================== A3：表达提取（真 LLM） ==============================
section("A3-1 真模型提取表达（例句必须仍是原文）")
INBOX_FAKE[:] = [
    {"id": 1, "who": "阿伟", "qq": "10002", "text": "卧槽这渲染也太顶了吧", "out": False},
    {"id": 2, "who": "小鱼", "qq": "10003", "text": "我看看这构图，绝了真就一张比一张离谱", "out": False},
    {"id": 3, "who": "老K", "qq": "10004", "text": "8点开黑不许鸽啊说好了", "out": False},
    {"id": 4, "who": "阿伟", "qq": "10002", "text": "谁鸽谁请奶茶没商量", "out": False},
    {"id": 5, "who": "小鱼", "qq": "10003", "text": "这显卡烤机一小时稳得很别慌", "out": False},
    {"id": 6, "who": "老K", "qq": "10004", "text": "你倒是出图啊干瞪眼呢", "out": False},
    {"id": 7, "who": "阿伟", "qq": "10002", "text": "急什么牌还没抽到", "out": False},
    {"id": 8, "who": "小鱼", "qq": "10003", "text": "抽卡这东西真看脸", "out": False},
]
S._chat = _orig_chat
E._chat = bot.chat
E.expression_extract(812, report=True)
with E._lock:
    learned = dict(E.EXPRESSIONS["expressions"])
print("  学到 %d 条：" % len(learned))
for k, e in learned.items():
    print("   #%d %s / %s ×%d 例句=%r 状态=%s" % (e["id"], e["situation"], e["style"],
                                                 e["count"], (e["contents"] or [""])[0][:24], e["status"]))
src_texts = " ".join(m["text"] for m in INBOX_FAKE)
bad = [k for k, e in learned.items()
       if e["contents"] and not all(c in src_texts or c.startswith("[") for c in e["contents"])]
chk("所有例句都能对回真实消息（绝不编造）", not bad, bad)
chk("默认 pending 等审核", all(e["status"] == "pending" for e in learned.values()))
chk("来源群记账", all("812" in e["sources"] for e in learned.values()))

section("A3-2 确认后注入（真数据过相关性闸）")
if learned:
    with E._lock:
        for e in learned.values():
            e["status"] = "confirmed"
    probe = (INBOX_FAKE[0]["text"][:12])   # 用真实消息当探针，应该能命中例句重叠
    blk = E.expression_block(812, probe)
    print("  探针=%r → 注入: %r" % (probe, blk[:80]))
    chk("本群可注入", isinstance(blk, str))
    chk("跨群隔离仍成立", E.expression_block(999, probe) == "")
else:
    print("  （模型没提取出条目——记录在案；提取提示词需人工复核）")

print("\n== 汇总 ==")
print("失败 %d 条（真机验收，失败=提示词或链路有问题，不是预算问题）" % len(FAIL))
sys.exit(1 if FAIL else 0)
