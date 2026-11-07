# -*- coding: utf-8 -*-
"""「别逐句接话 / 别点评表情包」守卫（2026-10-30 第五修）。

主人原话（这一轮就是为这两句改的）：
  「每张图都在那给我评头论足，每句话都搁那接，一个正常人是这样的吗？」
  「别人讨论的激烈的时候，你要做的并不是忽然插一句对别人表情的评价，
    应该是分析这几段对话，这几段来自不同人的对话，
    很多图片都只是一个表情而已，来表达情绪的」

所以本文件盯三件事，全部**离线打桩**，不联网不花钱：
  A. `img_is_sticker` 判据：只有 sub_type=1（或 summary 带「表情」）才算表情包
  B. `all_stickers` / 入口闸：群里刷表情包**不进看图链路**（不吃冷却、不调视觉）
  C. `_sim_should_judge`：单条不接、别人正聊着不打断、连段才判；主人/叫名字必判
  D. 源码级断言：`_sim_batch_flush` 真调了 `_sim_should_judge`（防被后人拆掉）
  E. `context_block` 把表情包标成「纯语气」
  F. 旋钮都在册（不然主人没法在网页上关掉）
"""
import os
import sys
import re
import time
import inspect

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import bot

FAIL = []


def chk(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
        print("FAIL %-46s got=%r want=%r" % (name, got, want))
    else:
        print("OK  " + name)


# ---------------- A. 表情包判据 ----------------
# 真机取证（get_group_msg_history 300 条统计）：
#   ("sub_type":1, "summary":"[动画表情]") × 23  ← 表情包
#   ("sub_type":0, "summary":"")             ×  9  ← 普通图
#   ("sub_type":1, "summary":"<$ǿĀD...")      ×  6  ← 乱码 summary 也是表情包
STICKER = {"sub_type": 1, "summary": "[动画表情]", "url": "http://x/a.gif"}
STICKER_GARBLED = {"sub_type": 1, "summary": "<$ǿĀD\x0e><$ÿĀ...", "url": "http://x/b.gif"}
PHOTO = {"sub_type": 0, "summary": "", "url": "http://x/c.png"}
TEXTY = {"sub_type": 0, "summary": "文字表情", "url": "http://x/d.png"}

chk("A1/sub_type=1 判表情包", bot.img_is_sticker(STICKER), True)
chk("A2/乱码 summary 靠 sub_type 也认得出", bot.img_is_sticker(STICKER_GARBLED), True)
chk("A3/普通照片不是表情包", bot.img_is_sticker(PHOTO), False)
chk("A4/summary 带「表情」文字也认（别的客户端）", bot.img_is_sticker(TEXTY), True)
chk("A5/空 dict 不炸", bot.img_is_sticker({}), False)
chk("A6/None 不炸", bot.img_is_sticker(None), False)
chk("A7/全表情 -> True", bot.all_stickers([STICKER, STICKER_GARBLED]), True)
chk("A8/混着真图 -> False（要按有内容处理）", bot.all_stickers([STICKER, PHOTO]), False)
chk("A9/空列表 -> False（没图不算表情包）", bot.all_stickers([]), False)


# ---------------- B. 入口闸：群里刷表情包不进看图 ----------------
_before_fetch = bot._fetch_rev_image
FETCHED = []
bot._fetch_rev_image = lambda d: FETCHED.append(d) or r"state\_vqa_eggs.png"
CHAT_CALLED = []
bot.chat = lambda msgs, *a, **k: CHAT_CALLED.append(1) or ("x", "点评一句")
BUB = []
bot.send_bubbles = lambda gid, t, **kw: BUB.append(t)
bot.reverser = type("R", (), {
    "describe_scene": staticmethod(lambda p, c: ("一只鸡蛋", "stub", False)),
    "describe": staticmethod(lambda p, c: "一只鸡蛋"),
    "identify": staticmethod(lambda p, c, *a, **k: {"known": False}),
})()
# 🔴 必须显式关掉两条「一跳」路径（总控大脑 / 一图直达）：不关就**真联网**，
#    本文件第一次跑就因此耗了 2 分钟、还叫坏了 B4~B7 四项（模型答的是
#    「图没加载出来呀」，跟我们测的闸门无关）。这两条各有专门守卫：
#    `_tomni.py`（总控大脑）、`_tsee_follow.py`（一图直达）。
bot.CFG.setdefault("ai", {})["brain_sees_image"] = False
bot.CFG["ai"]["omni_brain"] = False
CFG = bot.CFG.setdefault("sim", {})
CFG["pic_chat"] = True
CFG["pic_chat_scene"] = True
CFG["sticker_quiet"] = True

# B1 群里（没点它）发纯表情包 -> sim_pic_chat 应该立刻 return，连图都不取
FETCHED.clear(); CHAT_CALLED.clear(); BUB.clear()
bot._pic_chat_state["last"] = {}
bot.sim_pic_chat(123450001, "群友A", [STICKER], addressed=False, mid=1, uid="111")
chk("B1/群里刷表情包：不取图", FETCHED, [])
chk("B2/群里刷表情包：不调模型", CHAT_CALLED, [])
chk("B3/群里刷表情包：不发话", BUB, [])

# B4 @它 发表情包 -> 那是跟它闹，照旧要看
FETCHED.clear(); CHAT_CALLED.clear(); BUB.clear()
bot.sim_pic_chat(123450001, "群友A", [STICKER], addressed=True, mid=2, uid="111")
chk("B4/被@时表情包照常处理", len(CHAT_CALLED) >= 1, True)

# B5 私聊发表情包 -> 照常（对面只有主人一个人）
FETCHED.clear(); CHAT_CALLED.clear(); BUB.clear()
bot.sim_pic_chat(-123450003, "主人", [STICKER], addressed=True, mid=3, uid="123450003")
chk("B5/私聊发表情包照常处理", len(CHAT_CALLED) >= 1, True)

# B6 群里发**真图**（照片）-> 照常要处理（别把闸开太大）
FETCHED.clear(); CHAT_CALLED.clear(); BUB.clear()
bot.sim_pic_chat(123450001, "群友A", [PHOTO], addressed=False, mid=4, uid="111")
chk("B6/真图不受表情闸影响", len(CHAT_CALLED) >= 1, True)

# B7 关掉旋钮 -> 退回旧行为（表情包也会被点评）
CFG["sticker_quiet"] = False
FETCHED.clear(); CHAT_CALLED.clear(); BUB.clear()
bot.sim_pic_chat(123450001, "群友A", [STICKER], addressed=False, mid=5, uid="111")
chk("B7/旋钮关掉就退回逐张点评", len(CHAT_CALLED) >= 1, True)
CFG["sticker_quiet"] = True
bot._fetch_rev_image = _before_fetch


# ---------------- C. 该不该跑一次「要不要插话」判定 ----------------
def _mk(gid, pairs):
    """把 CONTEXT 铺成给定 [(qq, text)]，全部算「刚刚说的」。"""
    import time as _t
    with bot._ctx_lock:
        bot.CONTEXT[gid] = bot.deque(maxlen=30)
        for qq, txt in pairs:
            bot.CONTEXT[gid].append({"t": _t.time(), "who": "u%s" % qq,
                                     "qq": qq, "text": txt, "sticker": False})


GO = {"force": False}
CFG["single_quiet"] = True
G = 123450001
SELF = str(bot.SELF_ID or 0)

# C1 单条 + 只有一个人说过话 + 纯感叹 -> 不判
_mk(G, [("111", "靠了，不知道自己给键盘背回来干什么")])
_go, _why = bot._sim_should_judge(G, [{"text": "靠了，不知道自己给键盘背回来干什么",
                                       "who": "甲", "uid": "111", "force": False}])
chk("C1/单人自言自语一句不接", _go, False)

# C2 单条 + 两个人正在聊 -> 不打断（这是主人最气的场景）
_mk(G, [("111", "键盘能远程控制电脑吗"), ("222", "还是得uu远程啊"),
        ("111", "就是为了串流电脑用的")])
_go, _why = bot._sim_should_judge(G, [{"text": "手机这个破屏幕太小了",
                                       "who": "乙", "uid": "111", "force": False}])
chk("C2/两人正聊着，单条不插", _go, False)
# 理由随场景而变：短窗口高频时是「激烈讨论」，否则是「别人正聊着」。
# 两种都算对 —— 关键是要把「为什么不插」写进日志，方便以后追溯。
chk("C2b/理由写明了为什么不插",
    ("激烈讨论" in _why) or ("别人正聊着" in _why), True)

# C3 单条 + 只有一个人 + 是个问题 -> 可以判（值得回）
_mk(G, [("111", "键盘能远程控制电脑吗")])
_go, _why = bot._sim_should_judge(G, [{"text": "键盘能远程控制电脑吗",
                                       "who": "甲", "uid": "111", "force": False}])
chk("C3/一个人问了个问题可以接", _go, True)

# C4 连着两条 -> 判一次（这才谈得上读懂整段）
_mk(G, [("111", "这板子4K屏"), ("111", "我笔记本才2.5K")])
_go, _why = bot._sim_should_judge(G, [{"text": "这板子4K屏", "who": "甲", "uid": "111", "force": False},
                                      {"text": "我笔记本才2.5K", "who": "甲", "uid": "111", "force": False}])
chk("C4/连段两条要判", _go, True)
chk("C4b/理由写明条数", "连着" in _why, True)

# C5 主人说的 -> 永远判
_mk(G, [("123450003", "在吗")])
_go, _why = bot._sim_should_judge(G, [{"text": "在吗", "who": "主人", "uid": "123450003", "force": True}])
chk("C5/主人说话必判", _go, True)

# C6 叫到它名字 -> 永远判（明着点它）
_mk(G, [("111", "小鲸你在干嘛")])
_go, _why = bot._sim_should_judge(G, [{"text": "小鲸你在干嘛", "who": "甲", "uid": "111", "force": False}])
chk("C6/叫到名字必判", _go, True)

# C7 关掉旋钮 -> 退回旧行为（单条也判）
CFG["single_quiet"] = False
_mk(G, [("111", "靠了")])
_go, _why = bot._sim_should_judge(G, [{"text": "靠了", "who": "甲", "uid": "111", "force": False}])
chk("C7/旋钮关掉就退回逐句判定", _go, True)
CFG["single_quiet"] = True

# C8 空 items 不炸
_go, _why = bot._sim_should_judge(G, [])
chk("C8/空列表安全", _go, False)

# C9 **激烈讨论**（主人原话的重点场景）：2 个人 60 秒内来回 4 条，单条感叹 -> 绝不插
_mk(G, [("111", "键盘能远程控制电脑吗"), ("222", "还是得uu远程啊"),
        ("111", "就是为了串流电脑用的"), ("222", "画质有点低")])
_go, _why = bot._sim_should_judge(G, [{"text": "这板子4K屏", "who": "甲", "uid": "111",
                                       "force": False}])
chk("C9/激烈讨论时单条感叹不插", _go, False)
chk("C9b/理由写「激烈讨论」", "激烈讨论" in _why, True)

# C10 激烈讨论中冒出**派活/提问** -> 照旧判（别人吵着时来一句请求是该接的）
_go, _why = bot._sim_should_judge(G, [{"text": "介绍这家公司", "who": "乙", "uid": "222",
                                       "force": False}])
chk("C10/吵着时的派活不掐掉", _go, True)

# C11 激烈讨论攒够 3 条 -> 要判（这才是「分析这几段对话」的时机，不是一味装死）
_go, _why = bot._sim_should_judge(G, [{"text": "a", "who": "甲", "uid": "111", "force": False},
                                      {"text": "b", "who": "乙", "uid": "222", "force": False},
                                      {"text": "c", "who": "甲", "uid": "111", "force": False}])
chk("C11/攒够一整段要读一遍再决定", _go, True)
chk("C11b/理由写「攒够一整段」", "攒够一整段" in _why, True)

# C12 派活词覆盖（这是 _tomni 那条 FAIL 教会我的：把「派活」当闲聊会静默掉真请求）
_REQ_SAMPLES = ["介绍这家公司", "帮我翻译一下", "讲讲这个原理", "总结一下刚才的",
                "查一下今天的天气", "写个脚本", "做个表格", "算算这个多少钱"]
_all_req = all(bot._REQ_RE.search(x) for x in _REQ_SAMPLES)
chk("C12/常见派活说法都认得出", _all_req, True)
_quiet_samples = ["靠了，不知道自己给键盘背回来干什么", "好舒服啊", "④了", "沙杯",
                  "我光明正大的看", "4k144hz", "慈善二人组", "这单还白嫖了个京东plus"]
_none_req = not any(bot._REQ_RE.search(x) or bot._ASK_RE.search(x) for x in _quiet_samples)
chk("C12b/自言自语不会被误判成派活/提问", _none_req, True)


# ---------------- D. 源码级：闸真的挂在合批上（防后人拆掉）----------------
_src = inspect.getsource(bot._sim_batch_flush)
chk("D1/合批到点必须先过 _sim_should_judge", "_sim_should_judge(" in _src, True)
chk("D2/不接的时候要打日志（别静默到没法排查）", "不接这句" in _src, True)
_src2 = inspect.getsource(bot.sim_pic_chat)
chk("D3/sim_pic_chat 里有表情包闸", "all_stickers(" in _src2, True)
chk("D4/表情闸受旋钮控制（不是写死）", "_sticker_quiet()" in _src2, True)

# 入口那处（_handle_message）也要真拦
_src3 = inspect.getsource(bot._handle_message)
chk("D5/消息入口对表情包不跑看图", "all_stickers(" in _src3, True)


# ---------------- E. 上下文里表情包要标成「纯语气」----------------
with bot._ctx_lock:
    bot.CONTEXT[G] = bot.deque(maxlen=30)
    import time as _t
    bot.CONTEXT[G].append({"t": _t.time(), "who": "甲", "qq": "111",
                           "text": "[图片]", "sticker": True})
    bot.CONTEXT[G].append({"t": _t.time(), "who": "乙", "qq": "222",
                           "text": "[图片]", "sticker": False})
_cb = bot.context_block(G, skip=10)
chk("E1/表情包标成纯语气", "纯语气" in _cb, True)
chk("E2/普通图不被标成语气", _cb.count("纯语气"), 1)
chk("E3/每行仍带 QQ（分人没丢）", "QQ 111" in _cb and "QQ 222" in _cb, True)

# remember 要把 sticker 落进 CONTEXT
with bot._ctx_lock:
    bot.CONTEXT[G] = bot.deque(maxlen=30)
bot.remember(G, "甲", "111", "[图片]", mid=9, sticker=True)
with bot._ctx_lock:
    _last = list(bot.CONTEXT[G])[-1]
chk("E4/remember 存下 sticker 标记", _last.get("sticker"), True)


# ---------------- F. 旋钮在册 ----------------
chk("F1/「表情包不点评」旋钮在册", bool(bot.knob_find("表情包不点评")), True)
chk("F2/「孤立一句不接话」旋钮在册", bool(bot.knob_find("孤立一句不接话")), True)
chk("F3/旋钮默认开（不写 config 也生效）",
    bool((bot.CFG.get("sim") or {}).get("sticker_quiet", True)), True)
chk("F4/旋钮默认开（不写 config 也生效）2",
    bool((bot.CFG.get("sim") or {}).get("single_quiet", True)), True)


# ---------------- G. 沉默必须说得出原因（别让它哑了还没法查）----------------
# 真事故（21:38）：一群人聊得热闹，bot 整段一句话不说，而 bot.log 里**只有「群消息」**，
# 查不出是冷却/配额/心情/活跃度哪一道拦的。`sim_allowed` 返回的是裸 bool，
# 现在有了 `sim_allowed_reason` —— 它把「为什么不能」也交出来。
from collections import Counter as _C
chk("G1/sim_allowed_reason 存在", callable(getattr(bot, "sim_allowed_reason", None)), True)
chk("G2/sim_allowed 仍返回裸 bool（老调用点不用改）",
    isinstance(bot.sim_allowed(G, "111"), bool), True)

# 冷却中 -> 原因带「冷却」和剩余秒数
_orig_cd = bot.CFG["sim"].get("cooldown_sec")
bot.CFG["sim"]["cooldown_sec"] = 600
bot._sim_state["last"] = __import__("time").time()
bot._sim_state["count"] = 0
bot._sim_state["hour"] = int(__import__("time").time() // 3600)
_ok, _why = bot.sim_allowed_reason(G, "111")
chk("G3/冷却中被拦", _ok, False)
chk("G3b/说出剩余秒数", "冷却" in _why, True)
bot.CFG["sim"]["cooldown_sec"] = _orig_cd

# 静默模式 -> 明说「静默模式开着」
_orig_silent = bot.CFG["admin"].get("silent")
bot.CFG["admin"]["silent"] = True
_ok, _why = bot.sim_allowed_reason(G, "111")
chk("G4/静默模式下被拦", _ok, False)
chk("G4b/说明确原因是静默", "静默" in _why, True)
bot.CFG["admin"]["silent"] = _orig_silent
bot._sim_state["last"] = 0.0

# 入口真的会打「不接话：原因」这行日志（源码级，防后人删掉）
# 2026-11-05 U12a：这段搬进了 _route_group_ambient，守卫跟着搬（防删语义不变）。
_src4 = inspect.getsource(bot._route_group_ambient)
chk("G5/入口打「不接话：原因」日志", "不接话" in _src4, True)
chk("G6/该日志有 300 秒限流（不刷屏）", "_sim_quiet_log" in _src4, True)


# ---------------- H. 纯表情包：读情绪，但不许「整条跳过」----------------
# 🔴 2026-10-30 第六修（主人原话：「你直接跳过表情包啊，你可真会找办法」）。
#    第五修这里写的是「全是表情包 ⇒ 直接 return」，主人一眼看穿那是躲不是懂。
#    正确行为：**读一次情绪**写进上下文，读不到才退回「纯语气」；
#    无论哪种都**不许描述画面**，也不许单独发一句点评。
_src5 = inspect.getsource(bot._sim_batch_flush)
chk("H1/合批里有「读情绪」这条路", "read_sticker_vibe" in _src5, True)
chk("H2/「跳过表情包」那句已不在（不许靠躲）", "跳过看图" not in _src5, True)
chk("H3/读不到情绪也有明确日志（不静默吞掉）", "没读出情绪" in _src5, True)

# ---- H4~H7：情绪真的会被写进上下文，而且**只读一次**（不是一张一次）----
_orig_read_vibe = bot.read_sticker_vibe
_orig_patch = bot._patch_sticker_vibe
_VIBE_CALLS = []
_VIBE_ARGS = []
bot.read_sticker_vibe = lambda gid, imgs, who="": (
    _VIBE_CALLS.append(1) or _VIBE_ARGS.append((gid, list(imgs or ()), who))
    or ("笑死", ""))          # 10-31 起返回 (情绪, 认出的角色/梗)
_PATCHED = []
bot._patch_sticker_vibe = lambda gid, uid, vibe, known="": (
    _PATCHED.append((gid, uid, vibe, known)) or True)
_orig_judge2 = bot._sim_should_judge
bot._sim_should_judge = lambda g, it: (False, "测试拦")
_JOIN2 = []
_orig_join2 = bot.sim_join
bot.sim_join = lambda *a, **kw: _JOIN2.append(1)

# 一段里**三条**表情包（同一段对话）⇒ 只读一次，且读到的情绪要贴到文本里
_VIBE_CALLS.clear(); _VIBE_ARGS.clear()
_items = [
    {"who": "甲", "uid": "111", "text": "[动画表情]", "force": False,
     "imgs": ({"sub_type": 1, "url": "http://x/1.gif"},)},
    {"who": "乙", "uid": "222", "text": "[动画表情]", "force": False,
     "imgs": ({"sub_type": 1, "url": "http://x/2.gif"},)},
    {"who": "丙", "uid": "333", "text": "[QQ表情]", "force": False,
     "imgs": ({"sub_type": 1, "url": "http://x/3.gif"},)},
]
bot._sim_batch_flush(G, _items)
chk("H4/一段三条表情包只读一次情绪", len(_VIBE_CALLS), 1)
chk("H5/读情绪带着图去了（不是空手）", bool(_VIBE_ARGS) and bool(_VIBE_ARGS[0][1]), True)
chk("H6/读到的情绪写进了这一段文本（大脑看得见）",
    any("情绪 笑死" in str(i.get("text") or "") for i in _items), True)
chk("H7/情绪也挂在条目上（context_block 才能渲染）",
    any(i.get("vibe") == "笑死" for i in _items), True)

# 读不到情绪 ⇒ 不许硬凑一句（也不许去 sim_join）
_VIBE_CALLS.clear(); _JOIN2.clear()
bot.read_sticker_vibe = lambda gid, imgs, who="": ("", "")
bot._sim_batch_flush(G, [{"who": "甲", "uid": "111", "text": "[图片]",
                          "force": False, "imgs": ({"sub_type": 1},)}])
chk("H8/读不到情绪就不硬凑（不进判定、不发话）", _JOIN2, [])

# 有真文字的那条**不触发**读情绪（别为一条纯文字白花一次视觉调用）
_VIBE_CALLS.clear(); _JOIN2.clear()
bot.read_sticker_vibe = _orig_read_vibe
bot._patch_sticker_vibe = _orig_patch
bot._sim_should_judge = _orig_judge2
bot.sim_join = _orig_join2

# ---- H9~H11：context_block 要把情绪渲染成人话（不然大脑还是看不见）----
bot.CONTEXT.pop(G, None)
bot.remember(G, "甲", "111", "[表情包]", sticker=True)
with bot._ctx_lock:
    _c = bot.CONTEXT[G][-1]
    _c["text"] = "[表情包]"
    _c["sticker"] = True
    _c["vibe"] = "无语"
_blk = bot.context_block(G)
chk("H9/上下文渲染出情绪", "情绪：无语" in _blk, True)
chk("H10/上下文同时警示「别描述画面」", "别描述画面" in _blk, True)
# 没有情绪时退回纯语气（不许凭空编一个）
with bot._ctx_lock:
    bot.CONTEXT[G][-1]["vibe"] = ""
_blk2 = bot.context_block(G)
chk("H11/没情绪时退回纯语气", ("纯语气" in _blk2) and ("情绪：" not in _blk2), True)
bot.CONTEXT.pop(G, None)

# ---- H12~H15：读情绪的额度闸（一段一次还不够，得防连刷十段）----
bot._VIBE_STATE.clear()
_ok, _tag = bot._vibe_budget(G)
chk("H12/一开始额度是满的", _ok and ("/3" in _tag or "/" in _tag), True)
for _ in range(bot._vibe_cfg()["max"]):
    bot._vibe_note(G)
_ok2, _tag2 = bot._vibe_budget(G)
chk("H13/用满后不再放行", _ok2, False)
chk("H14/额度说明写清了已用/上限（日志能自证）", "/" in _tag2, True)
bot.CFG.setdefault("sim", {})
_oldmax = bot.CFG["sim"].get("vibe_max")
bot.CFG["sim"]["vibe_max"] = 0
bot._VIBE_STATE.clear()
bot._ok0, _ = bot._vibe_budget(G)
chk("H15/额度设 0 = 一次都不读", bot._vibe_budget(G)[0], False)
if _oldmax is None:
    bot.CFG["sim"].pop("vibe_max", None)
else:
    bot.CFG["sim"]["vibe_max"] = _oldmax

# ---- H16~H21：读情绪的输出清洗（**最容易被改坏的一处**）----
# 🔴 这里是「读情绪」和「描述画面」的分界。模型很容易答「一个笑脸」「画了个人在哭」——
#    那就是描述画面，正是主人骂的那件事。这种必须在**清洗层**就拒收，不许洗一洗混进来。
_RV = __import__("reverser")
_rv_src = inspect.getsource(_RV.read_vibe)
chk("H16/读情绪走独立提示词（不复用描述画面那条）", "VIBE_SYS" in _rv_src, True)
chk("H17/提示词明令禁止描述画面", "严禁" in _RV.VIBE_SYS and "描述画面" in _RV.VIBE_SYS, True)
for _good in ("笑死", "无语", "震惊", "破防了", "摆烂", "委屈巴巴", "「笑死」"):
    chk("H18/情绪词收下：%s" % _good, _RV._vibe_clean(_good), _good.strip("「」"))
for _bad in ("这张图画了一个人在哭", "一个笑脸", "画面里是个卡通人物", "看不出情绪", "不确定"):
    chk("H19/描述画面/占位回答一律拒收：%s" % _bad, _RV._vibe_clean(_bad), "")

# ---- H22：入口那处也不许「跳过」（源码级，防后人改回去）----
# ⚠️ 判据要看**日志字符串字面量**，不能只看关键词 —— 注释里保留历史说明
#    （「第五修这里写的是跳过看图，主人一眼看穿那是躲」）是**应该的**，
#    一刀切关键词会把正确注释也判成错（本用例第一版就是这么假失败的）。
_src_hm = inspect.getsource(bot._handle_message)
chk("H22/入口不再打「跳过看图」这行日志", 'log.info("(群里刷表情包，跳过看图' not in _src_hm, True)
# 2026-11-05 U12a：读情绪块搬进了 _route_pic_only_chat，守卫跟着搬。
_src_hm = inspect.getsource(bot._route_pic_only_chat)
chk("H23/入口改成读情绪", "read_sticker_vibe" in _src_hm, True)
chk("H24/读到的情绪真的贴回上下文（不是白读）", "_patch_sticker_vibe" in _src_hm, True)

# ---- H25~H34：第八修「认出角色/梗才值得开口」（2026-10-31 主人原话）--------------
# 主人：「如果能认出角色是谁。或者梗的含义，也能说一句，整体说话的频率不要太频繁」
#   ⇒ 认得出 ⇒ 允许顺着接一句（真人一眼认出老朋友是会搭话的）；
#     只读出情绪 ⇒ **仍然只当语气**（否则每张表情都换来一句点评 = 主人骂的评头论足）。
_orig_read_vibe3 = bot.read_sticker_vibe
_orig_join3 = bot.sim_join
_JOIN3 = []
bot.sim_join = lambda *a, **kw: _JOIN3.append(1)
bot._sim_should_judge = lambda g, it: (True, "测试放行")

# ① 只读出情绪、没认出角色 ⇒ 不进判定
_JOIN3.clear()
bot.read_sticker_vibe = lambda gid, imgs, who="": ("笑死", "")
bot._sim_batch_flush(G, [{"who": "甲", "uid": "111", "text": "[动画表情]",
                          "force": False, "imgs": ({"sub_type": 1},)}])
chk("H25/只读出情绪、没认出角色 ⇒ 不进判定（不逐张开口）", _JOIN3, [])

# ② 认出角色 ⇒ 允许判定（这才是主人说的「也能说一句」）
_JOIN3.clear()
bot.read_sticker_vibe = lambda gid, imgs, who="": ("笑死", "蛋黄哥")
_items2 = [{"who": "甲", "uid": "111", "text": "[动画表情]",
            "force": False, "imgs": ({"sub_type": 1},)}]
bot._sim_batch_flush(G, _items2)
chk("H26/认出角色 ⇒ 进判定（可以顺着接一句）", len(_JOIN3), 1)
chk("H27/认出的角色写进这一段文本（大脑看得见）",
    any("蛋黄哥" in str(i.get("text") or "") for i in _items2), True)
chk("H28/认出的角色挂在条目上（context_block 才能渲染）",
    any(i.get("known") == "蛋黄哥" for i in _items2), True)

# ③ 上下文渲染：认出来了要写清楚，但仍然不许描述画面
bot.CONTEXT.pop(G, None)
bot.remember(G, "甲", "111", "[表情包]", sticker=True)
with bot._ctx_lock:
    bot.CONTEXT[G][-1].update({"text": "[表情包]", "sticker": True,
                               "vibe": "笑死", "known": "蛋黄哥"})
_blk3 = bot.context_block(G)
chk("H29/上下文渲染出「认出来了：蛋黄哥」", "蛋黄哥" in _blk3, True)
chk("H30/渲染时仍带着「别描述画面」的警示", "别描述画面" in _blk3, True)

# ④ 拆分与清洗：情绪段和角色段**互不影响**，角色段更像描述就更要拒收
_RV2 = __import__("reverser")
chk("H31/情绪|角色 拆得开", _RV2._vibe_split("笑死|蛋黄哥"), ("笑死", "蛋黄哥"))
chk("H32/只写情绪时角色为空", _RV2._vibe_split("无语"), ("无语", ""))
chk("H33/角色那段是描述 ⇒ 拒收（不许变成评头论足）",
    _RV2._vibe_split("笑死|一个黄色的卡通角色"), ("笑死", ""))
chk("H34/认不出时角色为空（绝不猜）", _RV2._vibe_split("笑死|看不出"), ("笑死", ""))
bot.CONTEXT.pop(G, None)
bot.read_sticker_vibe = _orig_read_vibe3
bot.sim_join = _orig_join3

# ---------------- I. 说话的频率（2026-10-31 主人：「整体说话的频率不要太频繁」）--------
# 🔴 为什么不用冷却代替：冷却是「每 N 分钟定时蹦一句」—— 冷场时也定时插话最出戏，
#    正聊得热火朝天时它反而在冷却。真人不是按表说话，是**听够了才开口**，
#    所以数的是「我上次开口之后群里又攒了几条真人消息」。
bot.CONTEXT.pop(G, None)
_SELF_BAK = bot.SELF_ID
bot.SELF_ID = 123450004
bot.CFG.setdefault("sim", {})
_OLD_GAP = bot.CFG["sim"].get("min_gap_msgs")
bot.CFG["sim"]["min_gap_msgs"] = 5
with bot._sim_lock:
    bot._sim_state["last"] = 0.0
chk("I1/还没开口过 ⇒ 不限", bot._sim_density_ok(G)[0], True)

# 它刚说过一句，之后群里才 2 条真人消息 ⇒ 别再开口
with bot._sim_lock:
    bot._sim_state["last"] = time.time() - 10
for _i in range(2):
    bot.remember(G, "甲", "111", "第%d句" % _i)
_ok_i, _why_i = bot._sim_density_ok(G)
chk("I2/上次开口后群里才 2 条 ⇒ 拦下", _ok_i, False)
chk("I3/拦下的原因说得出还差几条", "条新消息" in _why_i, True)

# 攒够 5 条 ⇒ 放行
for _i in range(4):
    bot.remember(G, "乙", "222", "再来%d句" % _i)
chk("I4/攒够 5 条真人消息 ⇒ 放行", bot._sim_density_ok(G)[0], True)

# 它**自己**说的话不算「群里有人聊」（跟 _thread_people 同口径）
bot.CONTEXT.pop(G, None)
with bot._sim_lock:
    bot._sim_state["last"] = time.time() - 10
for _i in range(6):
    bot.remember(G, "我自己", str(bot.SELF_ID), "这是我说的%d" % _i)
chk("I5/它自己说的话不算「群里在聊」", bot._sim_density_ok(G)[0], False)

# 兜底：等得够久且至少有 2 条新消息 ⇒ 放行（防群里慢慢聊时它彻底变哑）
bot.CONTEXT.pop(G, None)
with bot._sim_lock:
    bot._sim_state["last"] = time.time() - 5000
for _i in range(2):
    bot.remember(G, "丙", "333", "慢慢聊%d" % _i)
chk("I6/等得够久且有新消息 ⇒ 放行（不至于变哑巴）", bot._sim_density_ok(G)[0], True)

bot.CFG["sim"]["min_gap_msgs"] = 0
chk("I7/设 0 = 关掉这道闸（退回只看冷却）", bot._sim_density_ok(G)[0], True)
chk("I8/密度闸真的接进了 sim_allowed_reason（不然上面全是空转）",
    "_sim_density_ok" in inspect.getsource(bot.sim_allowed_reason), True)
if _OLD_GAP is None:
    bot.CFG["sim"].pop("min_gap_msgs", None)
else:
    bot.CFG["sim"]["min_gap_msgs"] = _OLD_GAP
bot.CONTEXT.pop(G, None)
bot.SELF_ID = _SELF_BAK
with bot._sim_lock:
    bot._sim_state["last"] = 0.0

# ---------------- J. 识图链整条挂了要熔断（2026-10-31 实测的浪费）----------------
# 🔴 真事故：群里每张表情包都要把 8 档视觉模型**全试一遍**（minimax 崩 / deepseek 402 /
#    zhipu 401 / ollama 没起），每张白等 8~10 秒，结果必然是「没读出情绪」。
#    这种明知会失败的调用不该一遍遍做 ⇒ 判死 15 分钟，期间表情包直接当语气。
bot._VIBE_DEAD.update({"until": 0.0, "why": "", "logged": 0.0})
_CALLS_J = []


class _FakeRev:
    @staticmethod
    def read_vibe(p, cfg, *a, **k):
        _CALLS_J.append(1)
        raise RuntimeError("视觉模型全用不了（minimax/MiniMax-M3: 模型输出崩了）")


_orig_rev = bot.reverser
bot.reverser = _FakeRev
bot._VIBE_STATE.clear()
bot.CFG["sim"]["vibe_max"] = 5

# 第一次：真去试（失败）⇒ 判死
_CALLS_J.clear()
bot._fetch_rev_image = lambda d: "E:/x/a.gif"
bot.read_sticker_vibe(123450001, ({"sub_type": 1},), "甲")
chk("J1/第一次真去试（失败后才判死）", len(_CALLS_J), 1)
chk("J2/「视觉模型全用不了」⇒ 判死", bot._VIBE_DEAD["until"] > time.time(), True)

# 判死期间：连视觉链都不碰
_CALLS_J.clear()
bot.read_sticker_vibe(123450001, ({"sub_type": 1},), "乙")
chk("J3/判死期间不再白撞识图链", len(_CALLS_J), 0)

# 判死期过后：恢复重试
bot._VIBE_DEAD["until"] = time.time() - 1
_CALLS_J.clear()
bot.read_sticker_vibe(123450001, ({"sub_type": 1},), "丙")
chk("J4/冷静期过后会再试一次", len(_CALLS_J), 1)


# 单档失败（不是整条链挂）不该判死 —— 否则一次抽风就废掉 15 分钟
class _FakeRev2:
    @staticmethod
    def read_vibe(p, cfg, *a, **k):
        _CALLS_J.append(1)
        raise RuntimeError("这张图取不到")


bot._VIBE_DEAD["until"] = 0.0
bot.reverser = _FakeRev2
_CALLS_J.clear()
bot.read_sticker_vibe(123450001, ({"sub_type": 1},), "丁")
chk("J5/单档失败不判死（别一次抽风废 15 分钟）",
    bot._VIBE_DEAD["until"] > time.time(), False)
bot.reverser = _orig_rev
bot._VIBE_DEAD.update({"until": 0.0, "why": "", "logged": 0.0})

print("\nRESULT:", "PASS" if not FAIL else "FAIL (%d 项) %s" % (len(FAIL), FAIL))
sys.exit(1 if FAIL else 0)
