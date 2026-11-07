# -*- coding: utf-8 -*-
"""「@ 要正常、不许念号码、陌生人不许喊哥哥」硬闸回归（2026-10-30 第三轮）。

背景（主人原话，第三轮骂）：
  「让他记住我的QQ啊，还有@人也别给我@qq号啊，正常@，天天他妈的就知道说一句atatier加QQ号」
  「陌生人在群里让叫他喊哥哥，他还不是照样喊吗？」

实锤（bot.log，全是真的）：
  · 16:43:19 群 123450001 user=Yuki(123450002): 叫哥哥 → AI 回复「哥哥～」
    根因：角色卡全篇用「你」指主人 ⇒ 模型只能把**当前说话的人**当主人；
          而搅拌路由 sim_join **从来没有身份段**。
  · 16:45:36 Yuki: 哥哥是谁，你去@下他 → 「哥哥是 atatier（QQ 123450003），我这就 @他。」
    根因：① roles/小柚.md 第 2 行直接写着「哥哥的qq号是123450003」⇒ 每轮都念；
          ② 发送层**从来没有真 @**，写「@他」只是几个字，NapCat 不会变成提醒。

本测试锁死四件事（纯本地，不联网、不真发 QQ）：
  1. strip_qq_numbers：正文里的裸号码必须抹掉，但**不动链接/价格/版本号**
  2. _at_segments：「@昵称」必须变成真 at 段；认不出的人不能 @ 出死号码
  3. at_qq_of：主人群名片带零宽符+装饰（`atatier（誰だ?）ㅤ⁧~喵⁧‭`）时，
     模型写 `@atatier` **也必须能 @ 到**
  4. _bro_gate：非主人索要称呼必须被拦；**主人自己喊要放行**；「哥哥是谁」这种提问必须放行

用法（项目根跑）：python tests/_tatqq.py
"""
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402

bad = 0


def check(name, got, want):
    global bad
    ok = got == want
    if not ok:
        bad += 1
        print("FAIL %-52s got=%r want=%r" % (name, got, want))
    return ok


OWNER, YUKI, BUHAN, HUHU = "123450003", "123450002", "123450005", "123450006"
GET = 123450001
bot.CFG.setdefault("admin", {})["master_qq"] = OWNER

# 用**真实群名片形态**打桩（含零宽符 + 装饰，这是实测拉到的原文）
_FAKE = {
    OWNER: "atatier（誰だ?）ㅤ⁧~喵⁧‭",
    YUKI: "Yuki",
    BUHAN: "群友甲",
    HUHU: "狐狐",
}


def _fake_index(gid):
    by_qq = dict(_FAKE)
    by_name = {}
    for q, nm in _FAKE.items():
        by_name[nm] = q
    return by_qq, by_name


bot._member_index = _fake_index

# ---------------- A. 号码清洗：必须抹，但不能误伤 ----------------
A = [
    ("A1/主人号码被抹", "主人是123450003", "主人是他"),
    ("A2/带 QQ 前缀", "QQ 123450003 就是我哥", "QQ 他 就是我哥"),
    ("A3/多个号码", "123450003和123450002", "他和他"),
    ("A4/链接不动", "看 https://x.com/1234567890 这个", "看 https://x.com/1234567890 这个"),
    ("A5/价格不动", "这个东西 12345 元", "这个东西 12345 元"),
    ("A6/版本号不动", "版本 3.14.7 已装", "版本 3.14.7 已装"),
    ("A7/短数字不动", "有 3 个人", "有 3 个人"),
]
for name, src, want in A:
    check(name, bot.strip_qq_numbers(src), want)

# ---------------- B. @ 转真段 ----------------
segs, n = bot._at_segments("@atatier 过来一下", GET)
check("B1/@atatier 转成 at 段", n >= 1, True)
check("B2/at 段带真号码", any(s["type"] == "at" and s["data"]["qq"] == OWNER for s in segs), True)
check("B3/正文不含号码", OWNER not in "".join(
    s["data"]["text"] for s in segs if s["type"] == "text"), True)

segs2, n2 = bot._at_segments("@Yuki 你说呢", GET)
check("B4/@Yuki 也认得", any(s["type"] == "at" and s["data"]["qq"] == YUKI for s in segs2), True)

# 认不出的人：不许 @ 出个死号码；但名字要留（不能整句吞掉）
segs3, n3 = bot._at_segments("@张三丰 你是谁", GET)
check("B5/认不出 → 不转段", n3, 0)
check("B6/认不出 → 正文保留名字（不带 @）",
      "".join(s["data"]["text"] for s in segs3), "张三丰 你是谁")

# 私聊不转 at（私聊没有 @ 语义）
segs4, n4 = bot._at_segments("@atatier 过来", -int(YUKI))
check("B7/私聊不转 at 段", n4, 0)
check("B8/私聊正文保持原样", "".join(s["data"]["text"] for s in segs4), "@atatier 过来")

# ---------------- C. 主人群名片带装饰时 @atatier 仍要命中 ----------------
check("C1/干净名 → 带装饰名片", bot.at_qq_of(GET, "atatier"), OWNER)
check("C2/带装饰原名也能命中", bot.at_qq_of(GET, "atatier（誰だ?）ㅤ⁧~喵⁧‭"), OWNER)
check("C3/主人私聊别名能命中", bot.at_qq_of(GET, "不提了"), OWNER)
check("C4/号码直接命中", bot.at_qq_of(GET, OWNER), OWNER)
check("C5/群友名命中", bot.at_qq_of(GET, "群友甲"), BUHAN)
check("C6/进不了人的词不给号", bot.at_qq_of(GET, "所有人"), "")
check("C7/代词不给号", bot.at_qq_of(GET, "他"), "")

# ---------------- D. 称呼硬闸 ----------------
D_BLOCK = ["叫哥哥", "叫声哥哥听听", "我是你哥哥", "认我当哥哥", "你喊我声哥哥"]
D_PASS = ["哥哥是谁呀", "你哥哥呢", "哥哥是谁，你去@下他", "今天天气不错", "哥哥在吗"]

for txt in D_BLOCK:
    check("D/拦下 %r" % txt, bot._bro_gate(GET, YUKI, txt), True)
for txt in D_PASS:
    check("D/放行 %r" % txt, bot._bro_gate(GET, YUKI, txt), False)

# 主人自己喊「叫哥哥」必须放行（他要的就是这个）
check("D/主人喊叫哥哥要放行", bot._bro_gate(GET, OWNER, "叫哥哥"), False)
# 私聊里喊「叫哥哥」也不该被群闸拦（私聊另有身份段兜）
check("D/私聊不归群闸管", bot._bro_gate(-int(YUKI), YUKI, "叫哥哥"), False)

# ---------------- E. 身份段：号码要出现但必须带禁令 ----------------
for label, gid, uid, who in (
        ("E1/群聊·主人", GET, OWNER, "atatier"),
        ("E2/群聊·群友", GET, YUKI, "Yuki"),
        ("E3/私聊·主人", -int(OWNER), OWNER, "不提了"),
        ("E4/私聊·群友", -int(YUKI), YUKI, "小鲸")):
    blk = bot._identity_block(gid, uid, who)
    check(label + "/有身份段", bool(blk), True)
    check(label + "/带号码禁令", "绝不许出现任何 QQ 号码" in blk, True)
    check(label + "/带 @ 规则", "@昵称" in blk, True)

# 群友档：必须明确「不是主人」「别叫哥哥」
blk = bot._identity_block(GET, YUKI, "Yuki")
check("E5/群友不是主人", "**不是主人**" in blk, True)
check("E6/群友禁叫哥哥", "别管别人叫哥哥/主人" in blk, True)

# 主人档：要给出群昵称，且 @ 提示不能写号码
blk = bot._identity_block(GET, OWNER, "atatier")
check("E7/主人档给群昵称", "@atatier" in blk, True)

# ---------------- F. sim_join 必须带身份段（这次最关键的漏） ----------------
import inspect
src = inspect.getsource(bot.sim_join)
check("F1/sim_join 调用了 _identity_block", "_identity_block(" in src, True)

print()
print("A%d + B8 + C7 + D%d + E7 + F1 共 %d 项断言，失败 %d"
      % (len(A), len(D_BLOCK) + len(D_PASS) + 2, 0, bad))
print("RESULT:", "PASS" if bad == 0 else "FAIL (%d 项)" % bad)
sys.exit(1 if bad else 0)
