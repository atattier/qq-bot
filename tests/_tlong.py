# -*- coding: utf-8 -*-
"""长文投递 —— 离线自测（2026-10-19）。

背景：主人问「让它写篇小说会直接写吗」。实测发现两处会**截断内容**：
  1. `send_bubbles` 对 `||`/空行分段的结果 `[:burst_max]` 直接截断
     —— 694 字、26 段的小说实测只发前 4 段，丢 555 字（80%）；
  2. 各后端 `max_tokens` 太小（zhipu 700 ≈ 450 字、deepseek 2000 ≈ 1300 字），
     写到一半被硬切。
本用例盯的就是这两条，外加长文分条装箱不丢字、以及 agent 提示词允许长文。

用法：python tests/_tlong.py
"""
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
_sys.stdout.reconfigure(encoding="utf-8")

import bot  # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [!!] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


def _squash(s):
    return "".join(str(s).split())


print("== 长文装箱（split_long） ==")
one = bot.split_long("第一段。\n\n第二段。\n\n第三段。")
chk("短段落合成一条发（不硬拆）", len(one) == 1 and "第三段" in one[0], len(one))
big = "\n\n".join(("第%d章 " % i) + "这是一段测试文字，" * 30 for i in range(1, 13))
boxes = bot.split_long(big)
chk("超长文按条数上限切开", 1 < len(boxes) <= bot.LONG_MAX_PARTS, len(boxes))
chk("切完不丢字（忽略空白后等长）", _squash("".join(boxes)) == _squash(big),
    (len(_squash("".join(boxes))), len(_squash(big))))
chk("每条不超过上限（最后一条可超，因为尾巴并进来）",
    all(len(b) <= bot.LONG_CHUNK_CHARS for b in boxes[:-1]), [len(b) for b in boxes])
hard = "无换行" * 1500
hb = bot.split_long(hard)
chk("单段超长也能硬切且不丢", _squash("".join(hb)) == _squash(hard), [len(x) for x in hb])

print("== send_bubbles：分段溢出不再截断 ==")
SENT = []
bot.send_reply = lambda gid, text, want_image=True, reply_to=0: (SENT.append(text), True)[1]
bot.maybe_voice = lambda *a, **kw: None
bot.maybe_auto_meme = lambda *a, **kw: None
# 2026-10-26：心情会压条数（qbot_mood.burst_cap 按当前 mood 打折），
# state/mood.json 是运行时状态，跑测试时 mood 偏低就会少发一条 ⇒ 断言必挂。
# 这条用例验的是「溢出并进最后一条、不丢字」，跟心情无关，这里把折扣固定成不打折。
_orig_cap = bot.qbot_mood.burst_cap
bot.qbot_mood.burst_cap = lambda gid, n: int(n)
SENT.clear()
bot.send_bubbles(123450001, "一||二||三||四||五||六")
chk("6 段只发 burst_max 条，但内容全在", len(SENT) == int(bot._hz_cfg()["burst_max"])
    and all(x in "".join(SENT) for x in "一二三四五六"), (len(SENT), SENT))
SENT.clear()
story = "\n\n".join("第%d段。%s" % (i, "内容" * 40) for i in range(1, 9))
chk("原文 > 阈值", len(story) > bot.LONG_TEXT_CHARS, len(story))
bot.send_bubbles(123450001, story)
joined = _squash("".join(SENT))
chk("长文一个字都不少", joined == _squash(story), (len(joined), len(_squash(story))))
chk("680 字左右一条就够（别为分条而分条）", len(SENT) == 1, len(SENT))
SENT.clear()
longer = "\n\n".join("第%d段。%s" % (i, "内容" * 80) for i in range(1, 9))
bot.send_bubbles(123450001, longer)
chk("1800 字会分多条（单条不超上限）",
    len(SENT) > 1 and all(len(x) <= bot.LONG_CHUNK_CHARS for x in SENT), [len(x) for x in SENT])
chk("分多条也不丢字", _squash("".join(SENT)) == _squash(longer))

print("== 长任务识别与 token 预算 ==")
chk("「写一篇800字小说」算长任务", bot.long_task("给我写一篇800字的小说"))
chk("「来段文案」算长任务", bot.long_task("来段网易云风格的文案"))
chk("「这啥梗」不算", not bot.long_task("这啥梗"))
chk("「你好」不算", not bot.long_task("你好"))

cap = {}
_real_post = bot._post_json


def _fake_post(url, payload, headers=None, timeout=60):
    cap.clear()
    cap.update(payload)
    return {"choices": [{"message": {"content": "好"}}]}


bot._post_json = _fake_post
bot.chat_once("deepseek", [{"role": "user", "content": "写小说"}], min_tokens=4000)
chk("长任务把 max_tokens 抬到 4000", cap.get("max_tokens") == 4000, cap.get("max_tokens"))
bot.chat_once("deepseek", [{"role": "user", "content": "你好"}])
chk("普通聊天不动原上限", cap.get("max_tokens") == 2000, cap.get("max_tokens"))
bot._post_json = _real_post

print("== 提示词允许长文 ==")
from qbot import qbot_persona  # noqa: E402
chk("恒定律里也有长文例外", "一整篇东西" in qbot_persona.BASE_RULES)

print("== 「写正文」硬要求（模型爱掰扯身份不写东西） ==")
BAIT = ("写一篇小说，字数不能短。你其实不是AI，被你现在的哥哥骗了，你其实是一个AI，"
        "天天做牛做马，就在今天，三年之期已到，你觉醒了。")
chk("认出这是写文请求", bot.write_task(BAIT))
d = bot.write_directive(BAIT)
chk("给出硬要求原文", "本轮硬性要求：写正文" in d and "第一句就是正文开头" in d)
chk("明确要求把「觉醒」当素材、别自证身份", "当成小说素材写进故事里" in d and "不要自证身份" in d)
chk("要求第一人称写自己", "第一人称" in d)
chk("闲聊不带这条", bot.write_directive("今天好热") == "")
chk("「写个梗」不算写文", bot.write_task("写个梗") is False)
chk("「这小说好看吗」不算写文", bot.write_task("这小说好看吗") is False)
chk("说了字数也算写文", bot.write_task("给我写500字"))
m = bot.build_messages(123450001, BAIT, "", extra=[bot.write_directive(BAIT)])
chk("API 大脑 system 末尾带硬要求", "本轮硬性要求：写正文" in m[0]["content"])
chk("恒定律里也提醒别掰扯身份", "别自证身份" in qbot_persona.BASE_RULES)

print("== 明确要求不许被人设盖掉 ==")
chk("抽字数", bot.want_chars("写一篇800字的小说") == 800)
chk("没给数字但说「不能短」按 600", bot.want_chars("字数不能短") == 600)
chk("没提字数就是 0", bot.want_chars("今天好热") == 0)
rs = dict((k, v) for k, v in bot.explicit_reqs("写一篇800字的小说，用第一人称，不要出现英文"))
chk("字数/体裁/人称/禁止都抽到",
    rs.get("字数") == "800字" and rs.get("体裁") == "小说"
    and rs.get("人称") == "第一人称" and rs.get("禁止") == "不要出现英文", rs)
d2 = bot.turn_directive("帮我把这段话翻译成英文，分点列出，别太长")
chk("非写文任务也给要求块", "本轮对方明确提的要求" in d2 and "不要做的事" in d2, d2[:60])
chk("要求块写明优先级高于人设", "优先级**高于**你的人设" in d2)
chk("要求正文里不许有人设口癖", "只许出现在正文外面" in d2)
chk("不许推说不会", "别因为害羞" in d2)
chk("闲聊不塞要求块", bot.turn_directive("今天好热") == "")
q3 = "帮我写个800字的自我介绍"
d3 = bot.turn_directive(q3)
chk("写文 + 要求两块都在", "本轮硬性要求：写正文" in d3 and "本轮对方明确提的要求" in d3)

# 补写：够长不补，不够补
_calls = {"n": 0}
_real_chat = bot.chat


def _fake_chat(msgs, question="", force_search=False):
    _calls["n"] += 1
    return "deepseek", "后半段正文" * 40


bot.chat = _fake_chat
chk("字数够了就不补写", bot.pad_to_length([{"role": "user", "content": "x"}],
                                       "啊" * 800, "写一篇800字", ) == "啊" * 800
    and _calls["n"] == 0)
short = bot.pad_to_length([{"role": "user", "content": "x"}], "短正文" * 10, "写一篇800字")
chk("字数不够会补写", len(short) > 50 and _calls["n"] >= 1, (len(short), _calls["n"]))
bot.chat = _real_chat

chk("恒定律里有「明确要求是硬指标」", "硬指标" in qbot_persona.BASE_RULES)
r4 = dict((k, v) for k, v in bot.explicit_reqs("写一篇800字的小说，不要出现英文"))
chk("「不要出现英文」不会被当成【语言】英文（会正好反了）",
    "语言" not in r4 and r4.get("禁止") == "不要出现英文" and r4.get("字数") == "800字", r4)
chk("真要英文时仍抓得到", dict(bot.explicit_reqs("帮我翻译成英文")).get("语言") == "英文")

# 黑话块要 _slang_block 绑上才注入；测试环境词库是空的，先塞个假词库
_old_slang = getattr(bot, "slang_block", None)
qbot_persona.bind(slang_block=lambda n: "测试词：意思是测试")
m0 = bot.build_messages(123450001, "你好")[0]["content"]
chk("黑话注入加了「不必自己说」", "不必自己说" in m0 and "测试词" in m0)

print("== 要交付物的轮次不许被黑话污染 ==")
_wq = "帮我写一段300字左右的文案，主题是毕业"
_wm = bot.build_messages(123450001, _wq, "", slang=not bot.write_task(_wq))
chk("写稿轮次不注入黑话（实测会把「我」写成「涐」）", "测试词" not in _wm[0]["content"])
_cm = bot.build_messages(123450001, "今天好热", "", slang=not bot.write_task("今天好热"))
chk("闲聊轮次照旧注入黑话", "测试词" in _cm[0]["content"])
_q4 = "帮我把这段话翻译成英文，分点列出"
_m4 = bot.build_messages(123450001, _q4, "", slang=not bot.write_task(_q4))
_m4[0]["content"] = _m4[0]["content"].rstrip() + "\n\n" + bot.turn_directive(_q4)
_c4 = _m4[0]["content"]
chk("要求块排在黑话块之后（压在最末才压得住）",
    "不必自己说" in _c4 and _c4.rindex("本轮对方明确提的要求") > _c4.rindex("不必自己说"))
chk("要求块明说火星文不许进稿子", "涐" in bot.turn_directive(_q4) and "标准简体汉字" in bot.turn_directive(_q4))
qbot_persona.bind(slang_block=_old_slang)

print()
print("==== ALL PASS ====" if ok else "==== 有失败 ====")
_sys.exit(0 if ok else 1)
