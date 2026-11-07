# -*- coding: utf-8 -*-
"""「真 @、认清每个人的 QQ、不串人」硬闸回归（2026-10-30 第四轮）。

背景（主人原话，第四次骂）：
  「我说让 bot 认清每个人的 QQ，你理解得了吗？他艾特我就是艾特加上 atatier，
   根本就不是艾特人，你这就是艾特加上一串字符，真@要长按头像后出现的那个，
   你现在这种@根本就不会出现有人@我的标识，而且你给我分清每一个人啊，
   每个人说的话你都给我单独分了呀，你不要这个人说的话安给另一个人。」

真机实锤（bot.log，全是真的）：
  · 17:24:24 `AI 回复: @atatier 哥哥♡` —— 群里看到的就是「@ + 一串字符」，
    **主人根本没收到 @ 提醒**。根因：`send_reply` 带 reply_to 时直接 send_segments，
    完全绕过了挂在 `send_group` 上的清洗层。
  · 17:28:46 `真 @ 已转段 … 原文='@满穗⁧   ~喵⁧‭ 在呢'` —— 老正则撞到名片里的空格就断，
    剩下的 `   ~喵⁧‭` 当正文发了出去。
  · context_block 从来不带 QQ，只给群名片；满穗的 card 实测是空，靠 nickname 顶，
    两个人名字一像就串人；合批段里甲乙丙三个人说话，身份段只报最后那个人。

本文件锁死五件事（纯本地，不联网、不真发 QQ）：
  1. _at_segments：@昵称必须变真 at 段，且**名片带空格/零宽符/装饰尾巴也不能留垃圾字符**
  2. _sanitize_out：**出口唯一闸**——带 reply 段的消息、裸字符串、段列表都要被洗
  3. context_block / render_recent：每行必须带 QQ，才谈得上「认清每个人」
  4. build_messages：HISTORY 里每条 user 必须带说话人，否则多轮必串人
  5. _sim_batch_flush：合批必须逐人标注，不能只报最后一个人

用法（项目根跑）：python tests/_tatident.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402

bad = 0
total = [0]


def check(name, got, want):
    global bad
    total[0] += 1
    if got != want:
        bad += 1
        print("FAIL %-54s got=%r want=%r" % (name, got, want))
    return got == want


OWNER, XIAOJING, MANSUI, BUHAN = "123450003", "123450002", "123450008", "123450005"
GET = 123450001
bot.CFG.setdefault("admin", {})["master_qq"] = OWNER

# 用**真实群名片原文**打桩（零宽符、空格、装饰尾巴都是实测拉到的）
_FAKE = {
    OWNER: "atatier（誰だ?）ㅤ\u2067~喵\u2067\u202d",
    XIAOJING: "Yuki",
    MANSUI: "满穗\u2067   ~喵\u2067\u202d",
    BUHAN: "群友甲",
}


def _fake_index(gid):
    by_qq = dict(_FAKE)
    by_name = {}
    for q, nm in _FAKE.items():
        by_name[nm] = q
    return by_qq, by_name


bot._member_index = _fake_index
_orig_index = _fake_index


def _txt(segs):
    return "".join(s["data"].get("text", "") for s in segs if s.get("type") == "text")


def _ats(segs):
    return [s["data"]["qq"] for s in segs if s.get("type") == "at"]


print("== A. @ 必须变成真 at 段（含名片带空格/零宽符） ==")
A = [
    # (输入, 期望 at 的 QQ, 期望正文)
    ("@atatier 哥哥♡", OWNER, "哥哥♡"),
    ("@atatier 过来", OWNER, "过来"),
    ("@满穗 在呢", MANSUI, "在呢"),
    ("@满穗\u2067   ~喵\u2067\u202d 在呢", MANSUI, "在呢"),          # 名片原样抄
    ("@atatier（誰だ?）ㅤ\u2067~喵\u2067\u202d 在呢", OWNER, "在呢"),  # 主人名片原样抄
    ("@Yuki 你说呢", XIAOJING, "你说呢"),
    ("@不提了 在吗", OWNER, "在吗"),
    ("@123450003 过来", OWNER, "过来"),
    ("@atatier，哥哥", OWNER, "，哥哥"),
]
for src, want_qq, want_txt in A:
    segs, n = bot._at_segments(src, GET)
    check("A/%r 转出的 QQ" % src[:16], _ats(segs), [want_qq])
    check("A/%r 正文不许留垃圾字符" % src[:16], _txt(segs).strip(), want_txt)
    check("A/%r 计数" % src[:16], n, 1)

# 一句里 @ 两个人
segs, n = bot._at_segments("@atatier 和 @Yuki 都在", GET)
check("A/两个人都要 @ 到", _ats(segs), [OWNER, XIAOJING])
check("A/两个人计数", n, 2)

# 认不出的人：不许 @ 出死号码；也不许把 @ 留着（@ 留着等于假 @）
segs, n = bot._at_segments("@张三丰 你是谁", GET)
check("A/认不出不转段", n, 0)
check("A/认不出正文保留名字、去掉 @", _txt(segs), "张三丰 你是谁")

# 代词：去掉 @ 符号，字留着，别吞正文
segs, n = bot._at_segments("@他 过来", GET)
check("A/@他 不转段", n, 0)
check("A/@他 留字", _txt(segs), "他 过来")

# 私聊不转 at（私聊没有 @ 语义）
segs, n = bot._at_segments("@atatier 过来", -int(XIAOJING))
check("A/私聊不转 at", n, 0)
check("A/私聊正文原样", _txt(segs), "@atatier 过来")

print("== B. 出口唯一闸 _sanitize_out（最关键：防绕路） ==")
# B1 裸字符串
out = bot._sanitize_out(GET, "@atatier 哥哥♡")
check("B1/裸字符串转 at", _ats(out), [OWNER])
check("B1/裸字符串正文", _txt(out).strip(), "哥哥♡")

# B2 带 reply 段（17:24 那条真实绕路形态）
msg = [{"type": "reply", "data": {"id": 999}},
       {"type": "text", "data": {"text": "@atatier 哥哥♡"}}]
out = bot._sanitize_out(GET, msg)
check("B2/reply 段必须保留", out[0], {"type": "reply", "data": {"id": 999}})
check("B2/reply 后面转出 at", _ats(out), [OWNER])
check("B2/reply 后正文", _txt(out).strip(), "哥哥♡")

# B3 号码必须抹
out = bot._sanitize_out(GET, "哥哥的QQ是123450003")
check("B3/裸号码被抹", out, "哥哥的QQ是他")
out = bot._sanitize_out(GET, [{"type": "text", "data": {"text": "加 123450003"}}])
check("B3/段里号码被抹", _txt(out), "加 他")

# B4 不能误伤链接/版本/价格
for s in ("看 https://x.com/1234567890 这个", "版本 3.14.7 已装", "这个东西 12345 元"):
    check("B4/不误伤 %r" % s[:12], bot._sanitize_out(GET, s), s)

# B5 私聊：不转 at，但号码照抹
out = bot._sanitize_out(-int(XIAOJING), "我主人是123450003，@atatier")
out_txt = out if isinstance(out, str) else _txt(out)
check("B5/私聊抹号码", "123450003" not in out_txt, True)
check("B5/私聊抹成「他」", "我主人是他" in out_txt, True)
check("B5/私聊不转 at（@ 原样留着，不做段）",
      any(s.get("type") == "at" for s in out) if isinstance(out, list) else False, False)

# B6 _api_send 真的调了清洗（源码级断言：这条是本次最关键的结构保证）
import inspect
src_api = inspect.getsource(bot._api_send)
check("B6/_api_send 调用了 _sanitize_out", "_sanitize_out(" in src_api, True)

# B7 顺序必须是「先转 at 再洗号码」：反过来的话 [at:123450003] 会被洗成 [at:他] 而丢掉
out = bot._sanitize_out(GET, "[at:123450003] 兜底格式")
check("B7/[at:号码] 必须转成真段", _ats(out), [OWNER])
out = bot._sanitize_out(GET, "我哥哥的QQ是123450003，@atatier 来")
check("B7/转段后正文号码还要洗", OWNER not in _txt(out), True)
check("B7/同时保留真 at", _ats(out), [OWNER])

# B8 名字后面紧跟字母 = 是另一个名字，不许误吃（@atatierx ≠ atatier）
segs, n = bot._at_segments("@atatierx 不该吃", GET)
check("B8/@atatierx 不许误认为 atatier", n, 0)
check("B8/@atatierx 保留完整名字（只去 @ 符号）", _txt(segs), "atatierx 不该吃")

# B9 长句不许被「包含匹配」整体误认成某人（只 @ 紧跟 @ 的那段，正文一字不丢）
A9 = [
    ("@atatier 哥哥 你今天真好看", [OWNER], "哥哥 你今天真好看"),
    ("@满穗 你昨天说的那个东西", [MANSUI], "你昨天说的那个东西"),
    ("@所有人 注意一下", [], "所有人 注意一下"),
    ("@大家 都来看看", [], "大家 都来看看"),
]
for src, want_at, want_txt in A9:
    segs, n = bot._at_segments(src, GET)
    check("B9/%r 只 @ 紧跟的那段" % src[:14], _ats(segs), want_at)
    check("B9/%r 正文一字不丢" % src[:14], _txt(segs), want_txt)

print("== C. 上下文每行必须带 QQ（才谈得上认清每个人） ==")
bot.CONTEXT.clear()
bot.remember(GET, "满穗", MANSUI, "我呢我呢")
bot.remember(GET, "Yuki", XIAOJING, "你再@下你哥哥")
blk = bot.context_block(GET, skip=8)
check("C1/带满穗 QQ", MANSUI in blk, True)
check("C2/带 Yuki QQ", XIAOJING in blk, True)
check("C3/两个人分开列", blk.count("\n"), 1)

print("== D. HISTORY 每条 user 必须带说话人 ==")
bot.HISTORY.pop(GET, None)
m1 = bot.build_messages(GET, "我叫满穗", who="满穗", uid=MANSUI)
u1 = [x for x in m1 if x["role"] == "user"][-1]["content"]
check("D1/user 带名字", u1.startswith("满穗（QQ %s）：" % MANSUI), True)
m2 = bot.build_messages(GET, "我叫Yuki", who="Yuki", uid=XIAOJING)
users = [x["content"] for x in m2 if x["role"] == "user"]
check("D2/两条 user 各带各的号", users[0].startswith("满穗（QQ"), True)
check("D3/第二条带 Yuki 号", users[1].startswith("Yuki（QQ %s）：" % XIAOJING), True)
# 不传 who 时不包装（老调用点零回归）
bot.HISTORY.pop(GET, None)
m3 = bot.build_messages(GET, "你好")
check("D4/不传 who 时原样", [x for x in m3 if x["role"] == "user"][-1]["content"], "你好")

print("== E. 合批必须逐人标注 ==")
src_flush = inspect.getsource(bot._sim_batch_flush)
check("E1/合批带 QQ 标注", '（QQ %s）' in src_flush, True)
check("E2/合批明说不同人", "不同人" in src_flush, True)

print("== F. 身份段必须有「分人硬规矩」 ==")
for label, gid, uid, who in (
        ("F1/群聊·主人", GET, OWNER, "atatier"),
        ("F2/群聊·群友", GET, XIAOJING, "Yuki"),
        ("F3/私聊·主人", -int(OWNER), OWNER, "不提了"),
        ("F4/私聊·群友", -int(XIAOJING), XIAOJING, "小鲸")):
    blk = bot._identity_block(gid, uid, who)
    check(label + "/有分人规矩", "QQ 号才是「谁说的」的唯一凭据" in blk, True)
    check(label + "/禁串人", "绝不许把甲说的话算到乙头上" in blk, True)
    check(label + "/有号码禁令", "绝不许出现任何 QQ 号码" in blk, True)
    check(label + "/有 @ 规则", "@昵称" in blk, True)

print()
print("共 %d 项断言，失败 %d" % (total[0], bad))
print("RESULT:", "PASS" if bad == 0 else "FAIL (%d 项)" % bad)
sys.exit(1 if bad else 0)
