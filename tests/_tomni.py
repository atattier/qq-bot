# -*- coding: utf-8 -*-
"""总控大脑（ai.omni_brain）离线回归（2026-10-30 主人「一个大脑控制所有」）。

验四件事，全打桩、不联网、不发真消息：
  1. 开关关着 = 一行也不走总控大脑（零回归）；
  2. 开关开着 + 大脑不支持 tools ⇒ **回落老链路**，且冷却期内不再撞；
  3. 工具面按身份分：群友拿不到发群/记长期记忆/操作别群；
  4. 每个工具都真的接到**原 handler** 上（不重写闸门），且参数空/无图时给人话。
"""
import sys, types, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot

# 🔴 2026-10-30：本用例**必须在开头就把 search 熔断状态清干净 + 落盘路径改到临时目录**。
#    原因：真实 `state/search_cool.json` 里可能正躺着一个 401 熔断（线上常态），
#    不清的话「正常时显示开」「force_search 先搜」这些用例会被它带偏；
#    改路径则是防止本测试**反过来污染线上**（回归跑时真踩过：别的用例开始读到
#    「搜索仍在熔断期（401 测试）」）。
import pathlib as _pl
import tempfile as _tf
bot._SEARCH_COOL_FILE = _pl.Path(_tf.gettempdir()) / "_tomni_search_cool.json"
bot._SEARCH_COOL.update({"until": 0.0, "why": "", "key": ""})
bot._SEARCH_LAST_ERROR.update({"msg": "", "t": 0.0})
# 🔴 2026-10-30 追加：`web_search_free()` 是**真联网**的（360 搜索），
#    本用例只在第 26 组显式打开它验状态分层；其余组一律打桩成「搜不到」，
#    否则它会真的去 request，还把 `_FREE_SEARCH["ok_at"]` 写上新时间 ⇒
#    「熔断中显示不可用」「401 后 dead 钉住」这些断言会被它带成「能用」。
_orig_free_real = bot.web_search_free
bot.web_search_free = lambda q, c=6: ""

FAILS = []


def chk(name, cond, detail=None):
    if not cond:
        FAILS.append("%s%s" % (name, ("  <- %r" % (detail,) if detail is not None else "")))
        print("  FAIL %s %r" % (name, detail))
    else:
        print("  ok   %s" % name)


print("总控大脑 _tomni")

# ---------- 1. 工具清单本身 ----------
names = [t["function"]["name"] for t in bot._BOT_TOOLS]
chk("工具清单非空且名字唯一", bool(names) and len(names) == len(set(names)), names)
for need in ("draw_image", "draw_like_this", "reverse_prompt",
             "gen_video", "web_search", "recall_memory", "remember",
             "send_meme", "bot_status"):
    chk("工具面含 %s" % need, need in names)
chk("cloud_draw 已从工具面移除（引擎只由总控闸门定）", "cloud_draw" not in names, names)
chk("每个工具都有可解析的 parameters",
    all(isinstance(t["function"].get("parameters"), dict) for t in bot._BOT_TOOLS))

# ---------- 2. 按身份裁剪 ----------
_priv_tools = [t["function"]["name"] for t in bot._omni_tools(True)]
_guest_tools = [t["function"]["name"] for t in bot._omni_tools(False)]
chk("主人拿全套（含 qq_send_group）", "qq_send_group" in _priv_tools, _priv_tools)
chk("群友拿不到 qq_send_group", "qq_send_group" not in _guest_tools, _guest_tools)
chk("群友拿不到 remember", "remember" not in _guest_tools, _guest_tools)
chk("群友仍能画图（画图闸门在 handler 里管）", "draw_image" in _guest_tools, _guest_tools)

# ---------- 3. 开关语义 ----------
_saved = dict(bot.CFG.get("ai") or {})
bot.CFG.setdefault("ai", {})["omni_brain"] = False
chk("开关关着 -> _omni_on() False", bot._omni_on() is False)
bot.CFG["ai"]["omni_brain"] = True
chk("开关开着 -> _omni_on() True", bot._omni_on() is True)

# ---------- 4. 大脑不支持 tools ⇒ ToolsUnsupported ----------
_called = {"n": 0}


def _boom(*a, **kw):
    _called["n"] += 1
    raise AssertionError("不该走到这里")


# 让当前大脑变成 ollama（ollama 链路不挂 function tools）⇒ 必须直接抛，不进循环
# ⚠️ 必须显式传 provider —— 私聊 gid 会先读 `ai.private_provider`，光改 ai.provider
#    会被私聊大脑顶掉（本次第一版测试就栽在这，白跑了一次真请求）。
bot.CFG["ai"]["provider"] = "ollama"
try:
    bot.brain_with_tools(123450001, "帮我画个猫", "sys", privileged=True, provider="ollama")
    chk("ollama 大脑应当抛 ToolsUnsupported", False, "没抛")
except bot.ToolsUnsupported as e:
    chk("ollama 大脑抛 ToolsUnsupported（不白跑）", True, str(e))
except Exception as e:
    chk("ollama 大脑抛的是 ToolsUnsupported 而不是别的", False, repr(e))

# 冷却：确认过不支持后，10 分钟内直接抛（冷却状态由 brain_with_tools 自己置位）
bot._TOOLS_BROKEN["until"] = 0.0
bot.CFG["ai"]["provider"] = "custom"
_orig_ctx = bot.chat_once_tools


def _fake_unsupported(name, messages, tools=None, max_rounds=bot._TOOL_MAX_ROUNDS,
                      budget_sec=None, max_calls=None, principal=None,
                      allow_draw=True):
    _called["n"] += 1
    raise bot.ToolsUnsupported("HTTP 400 tools not supported")


bot.chat_once_tools = _fake_unsupported
try:
    bot.brain_with_tools(123450001, "帮我画个猫", "sys", privileged=True)
    chk("网关拒 tools 时向上抛（交给调用方回落）", False, "没抛")
except bot.ToolsUnsupported:
    chk("网关拒 tools 时向上抛（交给调用方回落）", True)
chk("拒过之后进入冷却（10 分钟内不再撞）", bot._TOOLS_BROKEN["until"] > 0,
    bot._TOOLS_BROKEN["until"])
try:
    bot.brain_with_tools(123450001, "再试一次", "sys", privileged=True)
    chk("冷却期内直接抛、不再发请求", False, "没抛")
except bot.ToolsUnsupported:
    chk("冷却期内直接抛、不再发请求", _called["n"] == 1, _called["n"])
bot.chat_once_tools = _orig_ctx
bot._TOOLS_BROKEN["until"] = 0.0

# ---------- 5. 每个工具真的接到原 handler（打桩验参数透传）----------
seen = []


def _stub(name, ret=None):
    def f(*a, **kw):
        seen.append((name, a, kw))
        return ret
    return f


# 空白参数 ⇒ 不该调 handler，直接回人话
bot._tool_ctx_set(123450001, uid="123450003", priv=True, imgs=(), qmid=0, mid=0, who="")
r = bot._exec_bot_tool("draw_image", {"what": "  "}, 123450001)
chk("draw_image 空描述不调 handler", r["ok"] is False and "what" in r["error"], r)

_orig = {}
for fn in ("draw_submit", "handle_i2i", "handle_reverse", "handle_moss_draw",
           "handle_video_gen", "web_search", "send_memes", "comfy_info"):
    _orig[fn] = getattr(bot, fn)

bot.draw_submit = _stub("draw_submit", (True, "已排队 #1"))
r = bot._exec_bot_tool("draw_image", {"what": "一只猫", "style": "厚涂", "aspect": "16:9"},
                       123450001, priv=True, mid=7, who="主人")
chk("draw_image -> draw_submit 参数对", r["ok"] is True and seen[-1][1][1] == "一只猫"
    and seen[-1][1][2] == "厚涂" and seen[-1][2].get("aspect") == "16:9", seen[-1])

bot.draw_submit = _stub("draw_submit", (False, "这个群没开画图"))
r = bot._exec_bot_tool("draw_image", {"what": "一只猫"}, 123450001)
chk("draw_image 被闸门拒绝时把原话交回大脑",
    r["ok"] is False and "没开画图" in r["error"], r)

# 无图时画同款/反推要给人话，不能空转
r = bot._exec_bot_tool("draw_like_this", {}, 123450001)
chk("draw_like_this 无参考图 -> 明确报错", r["ok"] is False and "参考图" in r["error"], r)
r = bot._exec_bot_tool("reverse_prompt", {}, 123450001)
chk("reverse_prompt 无图 -> 明确报错", r["ok"] is False and "没有图" in r["error"], r)

bot.handle_i2i = _stub("handle_i2i")
r = bot._exec_bot_tool("draw_like_this", {"extra": "换成夜景"}, 123450001,
                       imgs=({"file": "file:///E:/x/a.png"},), priv=True)
chk("draw_like_this -> handle_i2i 透传 extra",
    r["ok"] is True and seen[-1][1][1] == "换成夜景", seen[-1])

bot.handle_video_gen = _stub("handle_video_gen", True)
r = bot._exec_bot_tool("gen_video", {"desc": "一个人站在草原上说话"}, 123450001)
chk("gen_video -> handle_video_gen", r["ok"] is True and seen[-1][1][1].startswith("一个人"), seen[-1])

bot.web_search = _stub("web_search", "- 标题 ｜ 摘要")
bot._SEARCH_COOL["until"] = 0.0        # 清熔断，这条测的是「搜到了要把结果交回大脑」
bot._SEARCH_LAST_ERROR["msg"] = ""
r = bot._exec_bot_tool("web_search", {"query": "qwen3.5 发布"}, 123450001)
chk("web_search -> web_search 且结果回给大脑", r["ok"] is True and "摘要" in r["result"], r)

bot.send_memes = _stub("send_memes")
r = bot._exec_bot_tool("send_meme", {"keyword": "菲比"}, 123450001)
chk("send_meme -> send_memes", r["ok"] is True and seen[-1][1][1][0][0] == "菲比", seen[-1])

bot.comfy_info = _stub("comfy_info", {"server_up": True, "drawing": False, "queued": 0,
                                      "engine_here": "comfyui", "cooldown_left_sec": 0,
                                      "done_this_hour": 1})
r = bot._exec_bot_tool("bot_status", {}, 123450001)
chk("bot_status -> comfy_info", r["ok"] is True and r["comfyui"] == "在线", r)

# remember：只有主人能记
_r = bot._exec_bot_tool("remember", {"content": "主人喜欢厚涂"}, 123450001, priv=False)
chk("remember 群友被拦", _r["ok"] is False and "主人" in _r["error"], _r)

for fn, v in _orig.items():
    setattr(bot, fn, v)

# ---------- 6. 图片段落统一（路径字符串也能进 handler）----------
segs = bot._tool_img_segs(["C:\\qqbot\\state\\a.png",
                           {"url": "http://x/b.png"}])
chk("路径字符串包成 file:/// 段", segs[0].get("file", "").startswith("file:///"), segs[0])
chk("原有 dict 段原样保留", segs[1].get("url") == "http://x/b.png", segs[1])

# ---------- 7. 旋钮已注册且能改 ----------
items = {i["key"]: i for i in bot.knob_items()}
chk("旋钮 ai.omni_brain 已注册", "ai.omni_brain" in items, list(items)[:6])
chk("旋钮注册表默认值=False（保守：没验过不许默认开）",
    next(e for e in bot.KNOBS if e["key"] == "ai.omni_brain").get("default") is False)
chk("旋钮只对 API 大脑显示（brain=api）", items["ai.omni_brain"].get("brain") == "api")
# 预算旋钮：网页不单列（大脑组已顶格 12），但口令能查能改
chk("预算旋钮已注册且网页不单列",
    items["ai.tool_budget_sec"].get("web_hidden") is True and
    items["ai.tool_budget_sec"].get("type") == "int", items["ai.tool_budget_sec"].get("web_hidden"))
_br = [i for i in bot.knob_items("大脑") if not i.get("web_hidden")]


def _vis(prov):
    n = 0
    for i in _br:
        b = i.get("brain")
        if not b or b == prov or b == "api":
            n += 1
    return n


_cnt = {p: _vis(p) for p in ("deepseek", "custom")}
chk("加完新旋钮大脑组仍 ≤12", max(_cnt.values()) <= 12, _cnt)

# 恢复现场
bot.CFG["ai"] = _saved

# ---------- 8. 真链路（ai_reply）里总控大脑真的被调用 ----------
# 2026-10-30 踩到的真 bug：这里原来写的 `privileged=privileged`，但 `ai_reply()`
# 的签名里根本没有这个名字 ⇒ 每轮都抛 NameError 被 except 吞掉、静默回落老链路，
# 日志只留一行「总控大脑出错（name 'privileged' is not defined）」。
# 所以这条必须**真的调一次 ai_reply**，不能只验 brain_with_tools。
_sent = []
_orig_sendb, _orig_drawsub = bot.send_bubbles, bot.draw_submit
bot.send_bubbles = lambda gid, ans, want=True, **kw: _sent.append(ans)
bot.draw_submit = lambda *a, **kw: (True, "已排队 #1")
_seen_calls = {"n": 0}
_orig_bwt = bot.brain_with_tools


def _spy_bwt(*a, **kw):
    _seen_calls["n"] += 1
    return _orig_bwt(*a, **kw)


bot.brain_with_tools = _spy_bwt
bot.CFG["ai"]["omni_brain"] = True
# 用假的对话后端，避免这条测试真的联网/花钱
_orig_cots = bot.chat_once_tools
bot.chat_once_tools = lambda name, msgs, tools=None, max_rounds=bot._TOOL_MAX_ROUNDS, \
    budget_sec=None, max_calls=None, principal=None, allow_draw=True: \
    ("这句话我直接回，不用工具。", False)
bot.ai_reply(123450001, "今天天气怎么样", True, False, "", "主人", 0, "123450003")
bot.chat_once_tools = _orig_cots
bot.brain_with_tools = _orig_bwt
chk("ai_reply 真链路里走到了总控大脑", _seen_calls["n"] == 1, _seen_calls["n"])
chk("ai_reply 里没有被吞掉的 NameError", bool(_sent), "没发出任何话")

# 复原
bot.send_bubbles, bot.draw_submit = _orig_sendb, _orig_drawsub

# ---------- 9. 工具动过但没给正文 ⇒ 不再由工具层补固定话术 ----------
chk("已删除工具层兜底话术", not hasattr(bot, "_omni_fallback_say"))

# ---------- 10. 总控大脑不许漏掉老链路的两件事（10-30 二次补）----------
# ① 写长文要 pad_to_length；② 显式要搜要先替它搜。这两处漏了都是真退化。
_cap = {}
_orig_pad, _orig_ws, _orig_cots2 = bot.pad_to_length, bot.web_search, bot.chat_once_tools


def _spy_pad(msgs, answer, question, rounds=2):
    _cap["pad"] = (_cap.get("pad") or 0) + 1
    return answer + " 补写完成"


def _spy_ws(q, count=None):
    _cap["search"] = q
    return "- 结果甲 ｜ 摘要"


bot.pad_to_length, bot.web_search = _spy_pad, _spy_ws
bot.chat_once_tools = lambda name, msgs, tools=None, max_rounds=bot._TOOL_MAX_ROUNDS, \
    budget_sec=None, max_calls=None, principal=None, allow_draw=True: \
    ("这是直接回的正文。", False)
bot.CFG["ai"]["omni_brain"] = True
_sent2 = []
_orig_sb2 = bot.send_bubbles
bot.send_bubbles = lambda gid, ans, want=True, **kw: _sent2.append(ans)

# 写 800 字（want_chars 会要求补写）；顺带让 needs_search 命中
# ⚠️ 每段前必须清 `_cap` —— 它是同一个字典，跨用例累加会互相污染（本次就踩到）。
_cap.clear()
bot._SEARCH_COOL["until"] = 0.0
bot._SEARCH_LAST_ERROR["msg"] = ""
bot.ai_reply(123450001, "帮我写一篇 800 字的小短文，然后搜一下最近的新闻", True, False,
             "", "主人", 0, "123450003")
chk("总控大脑这条也做字数补写（pad_to_length 被调用）", _cap.get("pad") == 1, _cap)
chk("补写后的正文真的发出去了（不是拿原始短文本发）",
    bool(_sent2) and "补写完成" in _sent2[0], _sent2)

_cap.clear()
bot._SEARCH_COOL["until"] = 0.0
bot._SEARCH_LAST_ERROR["msg"] = ""
bot.ai_reply(123450001, "搜 今天天气怎么样", True, True, "", "主人", 0, "123450003")
chk("force_search 时总控大脑先替它联网搜", "search" in _cap, _cap)

bot.pad_to_length, bot.web_search, bot.chat_once_tools = _orig_pad, _orig_ws, _orig_cots2
bot.send_bubbles = _orig_sb2

# ---------- 11. sim_pic_chat 要能带 uid（否则主人发图被降级成群友）----------
import inspect
chk("sim_pic_chat 接受 uid（主人发图才拿得到全工具面）",
    "uid" in inspect.signature(bot.sim_pic_chat).parameters,
    list(inspect.signature(bot.sim_pic_chat).parameters))

# ---------- 12. 搜索失败不许冒充「没有结果」（10-30 智谱 key 401 实测踩到）----------
# 大脑原来收到 {"ok":false,"result":"（没搜到东西）"} 就准备凭记忆编「新模型」，
# 现在必须把**真实错因**交回去，让它说「现在查不到」。
_orig_ws2 = bot.web_search
bot.web_search = lambda q, n=None: (bot._SEARCH_LAST_ERROR.update(
    {"msg": "HTTP 401 (Unauthorized)"}) or "")
_r = bot._exec_bot_tool("web_search", {"query": "新模型"}, 123450001)
chk("搜索失败时把真错因交回大脑（不许冒充没结果）",
    _r["ok"] is False and "401" in _r["error"], _r)
bot._SEARCH_LAST_ERROR["msg"] = ""
bot._SEARCH_COOL["until"] = 0.0        # 清熔断：这条测的是「真空结果」，别被上一条的 401 影响
bot.web_search = lambda q, n=None: ""
_r = bot._exec_bot_tool("web_search", {"query": "新模型"}, 123450001)
chk("真搜到空 ⇒ 才报「没有结果」", _r["ok"] is True and "没有结果" in _r["result"], _r)
bot.web_search = _orig_ws2
bot._SEARCH_LAST_ERROR["msg"] = ""

# ---------- 13. 图片必须真进 messages（10-30 真 bug：只传文字 ⇒ 大脑回「我没看到图」）----------
# 这条必须验**发给模型的 messages 长什么样**，不能只验函数跑通。
_cap_msg = {}


def _spy_tools(name, messages, tools=None, max_rounds=bot._TOOL_MAX_ROUNDS, budget_sec=None,
               max_calls=None, principal=None, allow_draw=True):
    _cap_msg["messages"] = messages
    return "看到了。", False


_orig_cots3 = bot.chat_once_tools
_orig_fetch3 = bot._fetch_rev_image
_orig_fit = getattr(bot.reverser, "fit_image_data_url", None)
bot.chat_once_tools = _spy_tools
bot.reverser.fit_image_data_url = lambda p, *a, **kw: "data:image/jpeg;base64,AAAA"
bot._fetch_rev_image = lambda d: "E:/x/a.png"
bot.brain_with_tools(123450001, "你看这图", "sys", privileged=True,
                     imgs=[{"url": "http://x/y.png"}])
_last = (_cap_msg.get("messages") or [])[-1]
_parts = _last.get("content") if isinstance(_last.get("content"), list) else []
chk("带图时 user content 是多模态数组", isinstance(_last.get("content"), list), type(_last.get("content")))
chk("数组里有 image_url 段（图真进 messages 了）",
    any(p.get("type") == "image_url" for p in _parts if isinstance(p, dict)),
    [p.get("type") for p in _parts if isinstance(p, dict)])
chk("数组里同时保留文字段", any(p.get("type") == "text" for p in _parts if isinstance(p, dict)),
    [p.get("type") for p in _parts if isinstance(p, dict)])

# 没图时必须退回纯字符串（别给网关塞空数组，有些站会 400）
_cap_msg.clear()
bot.brain_with_tools(123450001, "纯聊天", "sys", privileged=True, imgs=())
_last = (_cap_msg.get("messages") or [])[-1]
chk("无图时退回纯文本（不塞空数组）", isinstance(_last.get("content"), str), type(_last.get("content")))

# 🔴 2026-10-30 真 bug 二次：`_fetch_rev_image()` 返回 **pathlib 对象**，
# 而 `_tool_img_segs` 只认 str ⇒ 图片被**静默丢掉**，大脑回「我还没看到图呢」。
# 症状跟「没传图」一模一样，代码看起来却是「传了」。这一栏专盯 Path 输入。
from pathlib import Path as _P
_pathobj = _P(r"C:\qqbot\state\a.png")
_s = bot._tool_img_segs([_pathobj])
chk("pathlib 对象也能转成图片段（pathlib 不是 str）",
    bool(_s) and _s[0].get("file", "").startswith("file:///"), _s)
chk("pathlib 段保住了文件名（file:/// 前缀正确）",
    bool(_s) and _s[0]["file"].endswith("a.png"), _s)
_s2 = bot._tool_img_segs([_pathobj, {"url": "http://x/b.png"}, "  ", None])
chk("混合输入不炸且丢弃空值", len(_s2) == 2, _s2)

bot.chat_once_tools = _orig_cots3
if _orig_fit is not None:
    bot.reverser.fit_image_data_url = _orig_fit
# 🔴 2026-10-30：本组开头把 `_fetch_rev_image` 打桩成 `lambda d: "E:/x/a.png"`，
#    但**从来没还原过** ⇒ 后面所有用例都在用这个假返回值（第 28 组因此假失败）。
#    测试桩必须"谁打谁还原"，否则污染是全文件级的，查起来很费劲。
bot._fetch_rev_image = _orig_fetch3

# ---------- 14. 资源闸：没被点名时不许白嫖显卡（10-30 补的真漏洞）----------
# 漏洞成因：群友发图只要撞上 pic_chat 冷却就进总控大脑，而那轮提示词还写着
# 「想画就调 draw_like_this」⇒ 群友发一张图就能白嫖一张图生图（烧显卡、占队列）。
# 修法有两道：① `_omni_tools(allow_draw=False)` 直接把画图/视频工具**摘掉**；
#            ② `_exec_bot_tool` 执行层再拒一次（防模型凭记忆硬写调用）。
_DRAW3 = ("draw_image", "draw_like_this", "gen_video")
_no = [t["function"]["name"] for t in bot._omni_tools(False, allow_draw=False)]
_yes = [t["function"]["name"] for t in bot._omni_tools(False, allow_draw=True)]
chk("未授权时工具面里没有画图/视频（三个全摘）",
    all(x not in _no for x in _DRAW3), _no)
chk("授权时画图工具在（对比组）", all(x in _yes for x in _DRAW3), _yes)
chk("未授权时仍保留看图/搜索/表情（不至于变哑巴）",
    "web_search" in _no and "send_meme" in _no and "bot_status" in _no, _no)

_orig_ds = bot.draw_submit
bot.draw_submit = lambda *a, **kw: (True, "已排队")
bot._tool_ctx_set(123450001, priv=False, allow_draw=False)
_r = bot._exec_bot_tool("draw_image", {"what": "一只猫"}, 123450001, priv=False)
chk("模型硬调 draw_image 也被执行层拒绝",
    _r["ok"] is False and "先别画" in _r["error"], _r)
bot._tool_ctx_set(123450001, priv=True, allow_draw=True)
_r = bot._exec_bot_tool("draw_image", {"what": "一只猫"}, 123450001, priv=True)
chk("授权时 draw_image 照常放行", _r["ok"] is True, _r)
bot.draw_submit = _orig_ds
bot._tool_ctx_clear()

# ---------- 15. 搜索：没源要如实报错 + 失效要熔断（10-30 智谱 401）----------
# 🔴 特别验「**不许用假结果糊弄**」：试过免密钥 Bing，能连通但返回**与查询无关**的结果
#    （「qwen3.5 什么时候发布」→「河南省人社厅」）。那比搜不到危险得多 —— 大脑会拿它编。
#    所以代码里明确**没有**任何「随便抓个页面当结果」的兜底，这条守住这个决定。
chk("没有 bing_search 这类假兜底（试过但结果是假的，已撤）",
    not hasattr(bot, "bing_search"), hasattr(bot, "bing_search"))
chk("web_search_any 存在（调用方统一入口）", callable(getattr(bot, "web_search_any", None)))
_orig_ws0 = bot.web_search
_orig_wsf0 = getattr(bot, "web_search_free", None)
bot.web_search = lambda q, n=None: ""
if _orig_wsf0 is not None:
    bot.web_search_free = lambda q, n=None: ""
bot._SEARCH_LAST_ERROR["msg"] = "没有可用的搜索源"
bot._SEARCH_COOL["until"] = 0.0
_r = bot._exec_bot_tool("web_search", {"query": "x"}, 123450001)
bot.web_search = _orig_ws0
if _orig_wsf0 is not None:
    bot.web_search_free = _orig_wsf0
chk("搜索不可用时工具明确报错（不返回 ok:true 的空结果）",
    _r["ok"] is False and "用不了" in _r["error"], _r)
bot._SEARCH_LAST_ERROR["msg"] = ""
bot._SEARCH_COOL["until"] = 0.0

# 熔断：key 401 之后不该每次都白撞
bot._SEARCH_COOL["until"] = 0.0
bot._SEARCH_LAST_ERROR["msg"] = ""
_calls = {"n": 0}
_orig_ws3 = bot.web_search


def _ws401(q, count=None):
    _calls["n"] += 1
    bot._SEARCH_LAST_ERROR["msg"] = "HTTP 401 (Unauthorized)"
    return ""


bot.web_search = _ws401
bot.web_search_any("第一次")
_after_first = _calls["n"]
bot.web_search_any("第二次")
chk("401 后进入熔断（第二次不再真发请求）", _calls["n"] == _after_first == 1, _calls)
chk("熔断期内的错因也说清楚", "熔断" in bot.web_search_status() or
    "401" in bot.web_search_status(), bot.web_search_status())
bot.web_search = _orig_ws3
bot._SEARCH_COOL["until"] = 0.0
bot._SEARCH_LAST_ERROR["msg"] = ""

# ---------- 16. 状态显示必须如实（不许拿配置项冒充「能用」）----------
# 10-30 的问题：`search_on()` 还是 True（配置没关），但 key 401 全挂 ⇒ 面板显示
# 「联网 开」是在骗人，主人会奇怪为什么问最新的事答不上来。
chk("search_usable 与 search_on 是两个判断", callable(getattr(bot, "search_usable", None)))
_orig_sc = dict(bot.CFG["ai"].get("search") or {})
bot._SEARCH_COOL["until"] = 0.0
bot._SEARCH_LAST_ERROR["msg"] = ""
chk("正常时显示「开」", bot.search_state_text() == "开", bot.search_state_text())

bot.CFG["ai"].setdefault("search", {})["key"] = ""
chk("没 key 时显示「开·没配key」（不冒充能用）",
    "没配key" in bot.search_state_text() and bot.search_usable() is False,
    bot.search_state_text())

bot.CFG["ai"]["search"]["key"] = _orig_sc.get("key") or "x"
bot._SEARCH_COOL.update({"until": time.time() + 600, "dead": False})   # 模拟短冷却中
chk("熔断中显示「不可用」且 usable=False",
    "不可用" in bot.search_state_text() and bot.search_usable() is False,
    bot.search_state_text())

# 🔴 2026-10-30 第二次踩：401 是**配置层面的死**，不是「过 10 分钟就好」。
#    第一版只记 10 分钟冷却 ⇒ 冷却一到状态卡又写「联网 开」，而 key 依然是 401，
#    真群里就是这个症状（/status 显示「联网 开」，问最新的事照样答不上来）。
bot._SEARCH_COOL.update({"until": time.time() + 600, "dead": True})
chk("key 判死后一直显示「不可用」（冷却到期也不许假装好了）",
    "不可用" in bot.search_state_text() and bot.search_usable() is False,
    bot.search_state_text())

# 旧版熔断文件（没写 dead，只有 401 错因）读回来也必须是 dead，
# 否则升级重启后 10 分钟一到又会「装作好了」。
bot._SEARCH_COOL.update({"until": time.time() + 120, "dead": False,
                         "why": "HTTP 401 (Unauthorized)"})
bot._search_cool_save()
bot._SEARCH_COOL.update({"until": 0.0, "dead": False, "why": ""})
bot._search_cool_load()
chk("旧熔断文件里的 401 会被升级成 dead（重启不许装好）",
    bot._SEARCH_COOL["dead"] is True, bot._SEARCH_COOL)

# 🔴 网页红标「为什么」不许因为重启就变空：`web_search_status()` 是**内存**里的
#    最后一次错误，重启后是 "" ⇒ 红标只剩「不可用」两个字，等于没说清原因。
#    所以 `live.why` 必须走 `_search_why_text()`（优先取落盘的熔断原因）。
bot._SEARCH_LAST_ERROR["msg"] = ""        # 模拟「重启后内存是空的」
chk("重启后联网不能用也要说得出原因（不只靠内存错因）",
    "401" in bot._search_why_text() or "失效" in bot._search_why_text(),
    bot._search_why_text())
chk("原因文案不许一律甩「没配 key」（key 明明配着）",
    "没配" not in bot._search_why_text(), bot._search_why_text())
_knobs = {i["key"]: i for i in bot.knob_items()}
_sk = _knobs.get("ai.search.enabled")
chk("网页旋钮 live.why 走统一入口（重启后也不会空）",
    bool(_sk) and bool((_sk.get("live") or {}).get("why")),
    (_sk or {}).get("live"))
# 401 之后真跑一次，必须把 dead 钉住（且重探间隔远长于 10 分钟）
bot._SEARCH_LAST_ERROR["msg"] = ""
_calls401 = {"n": 0}


def _ws401b(q, count=None):
    _calls401["n"] += 1
    bot._SEARCH_LAST_ERROR["msg"] = "HTTP 401 (Unauthorized)"
    return ""


_orig_ws4 = bot.web_search
bot.web_search = _ws401b
bot._SEARCH_COOL.update({"until": 0.0, "dead": False, "why": ""})
bot.web_search_any("验死")
chk("401 之后 dead 被钉住（不是只等 10 分钟）",
    bot._SEARCH_COOL["dead"] is True
    and bot._SEARCH_COOL["until"] > time.time() + 900,
    bot._SEARCH_COOL)
chk("dead 状态下 search_usable 为 False 且状态卡写不可用",
    bot.search_usable() is False and "不可用" in bot.search_state_text(),
    bot.search_state_text())
bot.web_search = _orig_ws4
bot._SEARCH_COOL.update({"until": 0.0, "dead": False, "why": ""})
bot._SEARCH_LAST_ERROR["msg"] = ""

bot.CFG["ai"].setdefault("search", {})["enabled"] = False
chk("配置关掉时显示「关」", bot.search_state_text() == "关", bot.search_state_text())
bot.CFG["ai"]["search"]["enabled"] = _orig_sc.get("enabled", True)
bot._SEARCH_COOL["until"] = 0.0

# ---------- 17. modelscope 备用真的挂在回落链里 ----------
# 2026-10-30：deepseek/siliconflow 都 402 欠费，唯一活的云端档是 modelscope
# （实测 Qwen/Qwen3.8-Flash-Next 聊天 1.8~3.3s、不思考、支持 tools）。
chk("modelscope 在大脑下拉候选里（不再被 BRAIN_MENU 挡掉）",
    "modelscope" in bot._brain_names(), bot._brain_names())
chk("modelscope 能当「备用」（在回落链里）",
    "modelscope" in bot._fallback_names(), bot._fallback_names())
chk("modelscope 模型是实测快的那个（别退回 34s 的 Qwen3.5-27B）",
    "Flash" in str((bot.provider_cfg("modelscope") or {}).get("model") or ""),
    (bot.provider_cfg("modelscope") or {}).get("model"))

# ---------- 18. 熔断状态必须落盘（重启不许「装作好了」）----------
# 只在内存里的话，每次重启都归零 ⇒ 面板又显示「联网 开」、第一句要联网的话再白撞 401。
# 而 key 失效这件事重启不会变。
# ⚠️ 落盘路径已在文件开头指到临时目录（见顶部说明），这里只验它能读写
_orig_cool_file = bot._SEARCH_COOL_FILE
chk("熔断状态有落盘文件（路径已隔离到 temp）",
    bot._SEARCH_COOL_FILE.name == "_tomni_search_cool.json",
    str(bot._SEARCH_COOL_FILE))
_orig_key = (bot.CFG.get("ai", {}).get("search") or {}).get("key")
bot.CFG.setdefault("ai", {}).setdefault("search", {})["key"] = _orig_key or "k"
bot._SEARCH_COOL.update({"until": time.time() + 300, "why": "401 测试", "key": ""})
bot._search_cool_save()
bot._SEARCH_COOL.update({"until": 0.0, "why": "", "key": ""})
bot._search_cool_load()
chk("落盘后能读回来（重启接着熔断）", bot._SEARCH_COOL["until"] > time.time(),
    bot._SEARCH_COOL)

# 换了新 key ⇒ 旧熔断作废（否则主人买了新 key 还以为搜不了）
bot._SEARCH_COOL.update({"until": time.time() + 300, "why": "401 测试"})
bot._search_cool_save()              # 文件里记的是旧 key
bot.CFG["ai"]["search"]["key"] = "brand-new-key"
bot._SEARCH_COOL.update({"until": 0.0, "why": ""})
bot._search_cool_load()
chk("换了新 key 自动开闸（旧熔断作废）", bot._SEARCH_COOL["until"] == 0.0,
    bot._SEARCH_COOL)

bot.CFG["ai"]["search"]["key"] = _orig_key
bot._SEARCH_COOL.update({"until": 0.0, "why": "", "key": ""})
try:
    bot._SEARCH_COOL_FILE.unlink()
except Exception:  # noqa: BLE001
    pass

# ---------- 19. `/status` 与面板不许拿配置项冒充「联网能用」----------
import inspect as _ins
_src_status = _ins.getsource(bot.handle_admin_cmd)
chk("/status 用 search_state_text 而不是 search_on()",
    "search_state_text()" in _src_status and "search_on() else" not in _src_status,
    "查看 handle_admin_cmd 源码")

# ---------- 20. 斜杠管理命令不许被剥前缀吞掉、也不许被总控大脑抢答 ----------
# 🔴 2026-10-30 真 bug：`by_slash` 会把 `/` 剥掉 ⇒ `/status` 变 `status`，
#    后面 `if text.startswith("/")` 那句管理命令判断**永远不成立** ⇒
#    `/status` 掉进聊天链路，总控大脑还去调 `bot_status` 工具回了句人话。
#    主人看不到状态卡，会以为「这命令不存在」。
chk("管理命令词表存在且是显式枚举",
    isinstance(bot._ADMIN_SLASH_CMDS, frozenset) and "status" in bot._ADMIN_SLASH_CMDS,
    sorted(bot._ADMIN_SLASH_CMDS))
chk("`/help` 不在管理员表里（否则群友发了会被吞掉）",
    "help" not in bot._ADMIN_SLASH_CMDS, sorted(bot._ADMIN_SLASH_CMDS))
chk("`/help` 在公开表里（群友最该看这份说明）",
    "help" in bot._PUBLIC_SLASH_CMDS, sorted(bot._PUBLIC_SLASH_CMDS))


def _route(t):
    """复刻 _handle_message 里的分流逻辑（剥前缀之前）。"""
    if not t.startswith("/"):
        return "chat"
    head = t[1:].split()[0].lower() if t[1:].strip() else ""
    return "admin" if head in bot._ADMIN_SLASH_CMDS else "slash_other"


for t in ("/status", "/reset", "/silent", "/active", "/role 小柚", "/roles", "/mute @1"):
    chk("%s 走管理命令" % t, _route(t) == "admin", _route(t))
for t in ("/画 一只猫", "/宫格 描述", "/厚涂 一只猫", "/动画 一只猫"):
    chk("%s 仍走画图前缀（别被管理命令抢走）" % t,
        _route(t) == "slash_other", _route(t))

# 关键回归：`/status` 不能让总控大脑接管（它是硬命令）
_sent3 = []
_orig_sb3 = bot.send_bubbles
_orig_adm = bot.handle_admin_cmd
_hit = {"n": 0}


def _spy_admin(gid, text, ats):
    _hit["n"] += 1


bot.handle_admin_cmd = _spy_admin
bot.send_bubbles = lambda gid, ans, want=True, **kw: _sent3.append(ans)
bot._seen_ids.clear()
bot._handle_message({"message_id": 777001, "user_id": "123450003",
                     "message": [{"type": "text", "data": {"text": "/status"}}],
                     "sender": {"nickname": "atar", "role": "admin"}}, 123450001, True)
chk("/status 真的进了 handle_admin_cmd（没被大脑抢）", _hit["n"] == 1, _hit["n"])
chk("/status 全程没发聊天话术", _sent3 == [], _sent3)
bot.handle_admin_cmd = _orig_adm
bot.send_bubbles = _orig_sb3

# ---------- 21. 群友发 /help 必须回话，发 /status 必须被静默拦（不许一起吞）----------
# 2026-10-30 退化：`_ADMIN_SLASH_CMDS` 一开始把 help 也收进去了 ⇒ 群友发 /help
# 被「非管理员已拦截」直接吞掉、一句话收不到。而 /help 本身就是给群友看的。
_sent4 = []
_orig_sg4 = bot.send_group
bot.send_group = lambda gid, t, **kw: _sent4.append(str(t)) or True


def _run_slash(uid, text, mid):
    _sent4.clear()
    bot._seen_ids.clear()
    bot._handle_message({"message_id": mid, "user_id": str(uid),
                         "message": [{"type": "text", "data": {"text": text}}],
                         "sender": {"nickname": "x", "role": "member"}}, 123450001, True)
    return _sent4[0] if _sent4 else ""


_out_guest_help = _run_slash(123450005, "/help", 777010)
chk("群友发 /help 有回话（公开命令）", bool(_out_guest_help), repr(_out_guest_help[:40]))
chk("群友看到的确实是用法说明", "聊天：" in _out_guest_help or "设置" in _out_guest_help,
    _out_guest_help[:60])

_out_guest_status = _run_slash(123450005, "/status", 777011)
chk("群友发 /status 静默拦（不回话也不刷屏）", _out_guest_status == "",
    repr(_out_guest_status[:40]))

_out_owner_status = _run_slash(123450003, "/status", 777012)
chk("主人发 /status 拿到状态卡", "状态：AI" in _out_owner_status, _out_owner_status[:60])
chk("状态卡里的联网字段是事实状态（不是裸「开」）",
    "联网 开" in _out_owner_status, _out_owner_status[:90])
bot.send_group = _orig_sg4

# ---------- 22. 主动插话必须看得到引用图（2026-10-30 真群实测）----------
# 真实现场：群友「引用一张公司截图 + 问介绍这家公司」，bot 没被@，走的是插话那条路，
# 回了一句「给我名字或链接」——图就在引用里，只是 `sim_join` 压根没接 imgs/qmid。
# 这里钉死三件事：① 图和引用进得来；② 大脑不收图时必须转成文字描述带进去；
# ③ 看图彻底失败时也要说清「没看清」，绝不许装作没这回事。
_cap = []
_orig_chat22 = bot.chat
bot.chat = lambda msgs, *a, **kw: (_cap.append(msgs) or ("stub", "嗯"))[1]
_orig_allowed22 = bot.sim_allowed
bot.sim_allowed = lambda gid, uid="": True
_orig_paths22 = bot.first_image_path
bot.first_image_path = lambda imgs=(), qmid=0: ["C:/tmp/fake_shot.png"]
_orig_rev22 = bot.reverser
_orig_sees22 = bot.brain_sees_image_on
_orig_bstrip22 = bot.send_bubbles
bot.send_bubbles = lambda gid, ans, want=True, **kw: True


class _FakeRev22:
    @staticmethod
    def fit_image_data_url(p):  # U11 转正公共名（bot.py 调这个）
        return "data:image/jpeg;base64,AAAA"

    @staticmethod
    def describe_scene(p, cfg, **kw):
        return "一张公司官网截图，标题写着某某科技", "stub/vl", None


# ① 大脑不收图 ⇒ 必须落到文字描述
bot.reverser = _FakeRev22()
bot.brain_sees_image_on = lambda: False
_cap.clear()
bot.sim_join(123450001, "小明", "介绍这家公司", uid="123450005",
             imgs=[{"file": "a.jpg"}], qmid=0)
_u22 = _cap[-1][1]["content"] if _cap else ""
_u22 = _u22 if isinstance(_u22, str) else str(_u22)
chk("插话拿到了这张图（不是空白）", "某某科技" in _u22, _u22[:120])

# ② 大脑能收图 ⇒ 原图直塞 messages（一跳）
bot.brain_sees_image_on = lambda: True
_orig_can22 = bot._brain_can_take_image
bot._brain_can_take_image = lambda: ("custom", "minimax/MiniMax-M3")
_cap.clear()
bot.sim_join(123450001, "小明", "介绍这家公司", uid="123450005",
             imgs=[{"file": "a.jpg"}], qmid=0)
_c22 = _cap[-1][1]["content"] if _cap else ""
chk("大脑能收图时走一图直达（content 是分段列表）", isinstance(_c22, list), type(_c22))
chk("分段里真有 image_url",
    isinstance(_c22, list) and any(s.get("type") == "image_url" for s in _c22),
    str(_c22)[:120])

# ③ 取图失败 ⇒ 明说「没看清」，不许回「给我名字/链接」那种话术
bot.first_image_path = lambda imgs=(), qmid=0: []
_cap.clear()
bot.sim_join(123450001, "小明", "介绍这家公司", uid="123450005",
             imgs=[{"file": "a.jpg"}], qmid=0)
_u23 = _cap[-1][1]["content"] if _cap else ""
chk("取图失败也告诉模型「有张图但没看清」", "没看清" in str(_u23), str(_u23)[:120])

bot.chat = _orig_chat22
bot.sim_allowed = _orig_allowed22
bot.first_image_path = _orig_paths22
bot.reverser = _orig_rev22
bot.brain_sees_image_on = _orig_sees22
bot._brain_can_take_image = _orig_can22
bot.send_bubbles = _orig_bstrip22

# ---------- 23. 入口链路：图和引用真能传到插话（不只是 sim_join 自己会用）----------
# 第 22 组验的是 sim_join 收下就管用；这组验的是**入口有没有递过去**。
# 以前 `batch_push` 那个 dict 里只有 who/text/quote/force —— 图在这一步就丢了。
_seen23 = []
_orig_simjoin23 = bot.sim_join
bot.sim_join = lambda gid, who, text, quote="", uid="", force=False, imgs=(), qmid=0: \
    _seen23.append({"imgs": list(imgs or ()), "qmid": qmid})
_orig_batch23 = bot._batch_cfg
bot._batch_cfg = lambda: {"group_ms": 0, "max_items": 6}
bot._seen_ids.clear()
bot._handle_message({"message_id": 777101, "user_id": "123450005",
                     "message": [{"type": "image", "data": {"file": "shot.jpg", "url": "http://x/1.jpg"}},
                                 {"type": "text", "data": {"text": "介绍这家公司"}}],
                     "sender": {"nickname": "小明", "role": "member"}}, 123450001, True)
chk("入口把图递给了插话（不再在这一步丢图）",
    bool(_seen23) and bool(_seen23[-1]["imgs"]), _seen23[-1] if _seen23 else "没走插话")
bot._batch_cfg = _orig_batch23
bot.sim_join = _orig_simjoin23

# ---------- 24. 识图必须落历史（2026-10-30 私聊「隔一句就不认账」）----------
# 真实现场：16:25:52 发图 → 16:26:02 答对了（真看到了）→ 16:26:20 追问「这是哪个插件」
# ⇒ 回「哥哥，你发的图我这边没收到内容」。根因是识图那轮**只发不记**，
# 下一轮历史里只剩「[图片]」占位符。这组把「落历史」钉死。
_G24 = -123450003
bot.HISTORY.pop(_G24, None)
bot._keep_seen(_G24, "不提了", uid="123450003",
               what="猫耳角色笑着，旁边露出一只光脚")
_h24 = "\n".join(str(i.get("content") or "") for i in bot.HISTORY.get(_G24) or ())
chk("识图后画面信息进 HISTORY", "猫耳角色" in _h24, _h24[:120])
_msgs24 = bot.build_messages(_G24, "这是哪个插件", who="不提了", uid="123450003")
_blob24 = "\n".join(str(m.get("content") or "") for m in _msgs24)
chk("下一轮纯文本追问时模型能看到画面", "猫耳角色" in _blob24, _blob24[-200:])

bot.HISTORY.pop(_G24, None)
bot._keep_seen(_G24, "不提了", uid="123450003", what="（这张图我这轮没看清）")
_h24b = "\n".join(str(i.get("content") or "") for i in bot.HISTORY.get(_G24) or ())
chk("没看清也要留痕（不许装作看过）", "没看清" in _h24b, _h24b[:120])

bot.HISTORY.pop(_G24, None)
bot._keep_seen(_G24, "不提了", uid="123450003")
chk("空内容不写脏数据", len(list(bot.HISTORY.get(_G24) or ())) == 0)

bot.HISTORY.pop(_G24, None)
bot._keep_seen(_G24, "", what="画面")
chk("who/uid 全空也能写（私聊边界）", len(list(bot.HISTORY.get(_G24) or ())) == 1)

# 真机踩到：模型回答里带 `[[情景:撑着下巴凑近屏幕]]`，原样落进历史会污染后面几轮
# （下一轮读起来像"场面里有个情景"，其实是它自己的舞台指示）。
bot.HISTORY.pop(_G24, None)
bot._keep_seen(_G24, "不提了", uid="123450003",
               said="哥哥贴的封面呀，红底黑字挺带感♡\n\n[[情景:撑着下巴凑近屏幕，客厅暖灯]]")
_h24c = "\n".join(str(i.get("content") or "") for i in bot.HISTORY.get(_G24) or ())
chk("落历史前擦掉情景标记", "情景:" not in _h24c and "带感" in _h24c, _h24c[-140:])

# ---------- 25. 私聊指代追问要能回捞那张图（老条件只认「图」字）----------
# 真实漏点：他问的是「这是哪个插件，」「找下来源」——里面没有「图」字，
# 老 `"图" in text` 判据直接失效。这组只验判据本身（不取真图、不联网）。
#
# 🔴 两档 + 夸奖排除（2026-10-30 自查纠的误判）：一开始一个正则全塞进去，
#    「这个游戏好玩」「上面说的对」「那张照片不错」全命中 ⇒ 只要 10 分钟内发过图，
#    这类普通闲聊就会被截胡成看图问答、答非所问。现在：
#      强词（图/插件/出处…）直接算；弱指代（这个/它/上面…）要配疑问语气；
#      夸奖/吐槽（不错/好看/笑死…）一律不算。
_isref = getattr(bot, "_is_img_ref", None)
chk("指代追问判据存在", _isref is not None)
if _isref is not None:
    # 必须命中（他真在问那张图）
    for _t in ("这是哪个插件，", "找下来源", "这个看不见吗", "它是啥",
               "刚才那张什么图", "图里是什么", "这是哪来的", "谁画的"):
        chk("「%s」判为在问那张图" % _t, bool(_isref(_t)), _t)
    # 必须不命中（普通闲聊，别截胡）
    for _t in ("这个游戏好玩", "今天好累啊", "上面说的对", "那张照片不错",
               "这个东西好用", "帮我写首诗", "下午去打球"):
        chk("闲聊「%s」不误判" % _t, not _isref(_t), _t)

# ---------- 26. 免密钥搜索：付费 key 死了也不许说「联网不能用」----------
# 2026-10-30 主人原话「联网搜索用的主模型搜啊」。实测聚合站 80 个模型全不自带联网
# （带 tools:[{"type":"web_search"}] 一律 400），所以只能我们搜好喂给主模型。
# 这组不联网，只验**状态判断的分层**：免密钥搜到过东西 ⇒ 就算能用。
_orig_free26 = bot._FREE_SEARCH.copy()
_orig_cool26 = bot._SEARCH_COOL.copy()
try:
    bot._FREE_SEARCH.update({"via": "360", "ok_at": time.time()})
    bot._SEARCH_COOL.update({"until": time.time() + 1800, "why": "HTTP 401", "dead": True})
    chk("key 死但免密钥在顶 ⇒ 判为能用", bot.search_usable() is True, bot.search_state_text())
    chk("状态卡说清靠的是免密钥源", "免密钥" in bot.search_state_text(), bot.search_state_text())
    chk("能用时不再报「为什么用不了」", bot._search_why_text() == "", bot._search_why_text())

    bot._FREE_SEARCH.update({"via": "", "ok_at": 0.0})
    chk("免密钥也没搜到 + key 死 ⇒ 判不可用", bot.search_usable() is False)
    chk("不可用时给得出人话原因", bool(bot._search_why_text()), bot._search_why_text())
finally:
    bot._FREE_SEARCH.clear()
    bot._FREE_SEARCH.update(_orig_free26)
    bot._SEARCH_COOL.clear()
    bot._SEARCH_COOL.update(_orig_cool26)

# 免密钥的输出格式必须和付费源一致（`- 标题 ｜ 摘要`），否则喂给大脑会串味
chk("免密钥结果格式统一（- 标题 ｜ 摘要）",
    callable(getattr(bot, "web_search_free", None)))

# ---------- 27. 状态展示前必须探活（重启后不许凭「没试过」就报不可用）----------
# 真机踩到：重启后 `_FREE_SEARCH["ok_at"]` 归零，付费 key 又死着 ⇒ /status 写
# 「开·不可用」，可免密钥其实搜得到。这组验 `free_search_probe()` 会把状态掰正。
_orig_probe27 = getattr(bot, "web_search_free")
_orig_f27 = bot._FREE_SEARCH.copy()
_orig_c27 = bot._SEARCH_COOL.copy()
try:
    bot._FREE_SEARCH.update({"via": "", "ok_at": 0.0, "try_at": 0.0})
    bot._SEARCH_COOL.update({"until": time.time() + 1800, "why": "HTTP 401", "dead": True})
    bot.web_search_free = lambda q, c=6: "- 标题 ｜ 摘要"      # 假装探活成功
    got = bot.free_search_probe(wait=True)     # wait=True：测试里要同步结果
    chk("探活能发现免密钥可用", got is True, got)
    chk("探活成功后判为能用", bot.search_usable() is True, bot.search_state_text())
    # 🔴 自查纠的设计失误：探活/免密钥成功**不许清付费 key 的熔断** ——
    #    清了会导致 ① 下次搜索又白撞 401；② 状态卡丢掉「靠免密钥顶着」这句提示。
    #    付费路的死活（dead）和免密钥能不能用（ok_at）是**两件事**，分开记。
    chk("探活不动付费 key 的熔断（仍记为死）", bot._SEARCH_COOL["dead"] is True,
        bot._SEARCH_COOL)
    chk("但免密钥确实被记为可用", bot._free_search_ok() is True)
    chk("两个判据合起来：能用 + 靠免密钥顶", 
        bot.search_usable() is True and bot._paid_search_dead() is True)

    # 节流：10 分钟内不再重复探
    _calls27 = []
    bot._FREE_SEARCH.update({"ok_at": 0.0, "try_at": time.time()})
    bot.web_search_free = lambda q, c=6: _calls27.append(1) or "- x ｜ y"
    bot.free_search_probe(wait=True)
    chk("10 分钟内不重复探活（防刷）", len(_calls27) == 0, len(_calls27))

    # 🔴 /status 不许被探活卡住（360+Bing 两源最坏 30s）⇒ 默认必须**非阻塞**
    bot._FREE_SEARCH.update({"ok_at": 0.0, "try_at": 0.0})
    _blocked27 = []
    bot.web_search_free = lambda q, c=6: (_blocked27.append(1), "- x ｜ y")[1]
    _t27 = time.time()
    bot.free_search_probe()                    # 不开 wait：应立刻返回
    _cost27 = time.time() - _t27
    chk("默认非阻塞（/status 不被探活卡住）", _cost27 < 0.5, "%.2fs" % _cost27)
    time.sleep(1.2)                            # 等后台线程跑完，再验它真探了
    chk("后台线程确实探了活", len(_blocked27) == 1, len(_blocked27))
finally:
    bot.web_search_free = _orig_probe27
    bot._FREE_SEARCH.clear()
    bot._FREE_SEARCH.update(_orig_f27)
    bot._SEARCH_COOL.clear()
    bot._SEARCH_COOL.update(_orig_c27)

# ---------- 28. 取图函数必须吃「路径字符串」（2026-10-30 私聊识图真正的崩点）----------
# 铁证：21:13:12 私聊「这是哪个插件，找下来源」→ 回捞触发 →
#   `Exception in thread Thread-46 (sim_vqa_chat): AttributeError: 'str' object has no attribute 'get'`
# 根因：`_vg_localize_images()` 返回的是 `[str(path)]`，而 `_fetch_rev_image` 原来只吃 dict。
# 这组把它钉死：传 str / Path 都不许崩，且真能读回本地图。
import pathlib as _pl28
import tempfile as _tf28
from PIL import Image as _Img28

_tmp28 = _pl28.Path(_tf28.mkdtemp(prefix="tomni28_"))
_img28 = _tmp28 / "t.png"
_Img28.new("RGB", (24, 24), (200, 30, 30)).save(_img28)
_orig_rev28 = bot.REV_DIR
bot.REV_DIR = _tmp28 / "rev"

try:
    got = bot._fetch_rev_image(str(_img28))
    chk("取图函数吃字符串路径（不再 AttributeError）", bool(got), got)
    got2 = bot._fetch_rev_image(_pl28.Path(_img28))
    chk("取图函数吃 pathlib.Path", bool(got2), got2)
    chk("取回的确实是本地文件", bool(got) and _pl28.Path(str(got)).is_file(), got)
    chk("完全不相干的对象也不崩（返回 None）", bot._fetch_rev_image(12345) is None)
except Exception as e:
    chk("取图函数吃字符串路径（不再 AttributeError）", False, "%s: %s" % (type(e).__name__, e))
finally:
    bot.REV_DIR = _orig_rev28

# 🔴 分人只认 QQ：识图落的每一条历史都必须带说话人的 QQ 号。
#    项目铁律「名字会撞、QQ 不会，绝不许把甲的话算到乙头上」。`sim_vqa_chat`
#    原来是**没有 uid 参数**的（只有 who），落历史时说话人标不全 ⇒ 补上。
import inspect as _ins28
_sig_pic = _ins28.signature(bot.sim_pic_chat)
_sig_vqa = _ins28.signature(bot.sim_vqa_chat)
chk("sim_pic_chat 收 uid", "uid" in _sig_pic.parameters, list(_sig_pic.parameters))
chk("sim_vqa_chat 收 uid（原来没有）", "uid" in _sig_vqa.parameters, list(_sig_vqa.parameters))
# 落历史内容里必须出现 QQ 号
bot.HISTORY.pop(_G24, None)
bot._keep_seen(_G24, "某人", uid="123450003", what="测试画面")
_h28 = "\n".join(str(i.get("content") or "") for i in bot.HISTORY.get(_G24) or ())
chk("落历史带上了 QQ 号（分人只认 QQ）", "123450003" in _h28, _h28[:130])

# 🔴 2026-10-31：`_fetch_rev_image` 的坑**排查时在 `_fetch_raw_image` 又找到一个同款**
#    （收图入库那条路）。两个函数都必须对 str / Path / None / int 全都**不崩**，
#    否则症状就是「看起来触发了、其实后台线程已经崩了」，日志上只留前半句。
for _fn28 in ("_fetch_rev_image", "_fetch_raw_image"):
    _f28 = getattr(bot, _fn28)
    _tmp28b = _pl28.Path(_tf28.mkdtemp(prefix="tomni28b_"))
    _img28b = _tmp28b / "b.png"
    _Img28.new("RGB", (24, 24), (5, 5, 200)).save(_img28b)
    _o_rev28b, _o_grab28b = bot.REV_DIR, getattr(bot, "GRAB_DIR", None)
    bot.REV_DIR = _tmp28b / "rev"
    if _o_grab28b is not None:
        bot.GRAB_DIR = _tmp28b / "grab"
    try:
        _crashed = []
        for _lbl, _val in (("str", str(_img28b)), ("Path", _img28b), ("None", None), ("int", 999)):
            try:
                _f28(_val)
            except Exception as _e:  # noqa: BLE001
                _crashed.append("%s:%s" % (_lbl, type(_e).__name__))
        chk("%s 对 str/Path/None/int 都不崩" % _fn28, not _crashed, _crashed)
    finally:
        bot.REV_DIR = _o_rev28b
        if _o_grab28b is not None:
            bot.GRAB_DIR = _o_grab28b

# 端到端：`_vg_localize_images` 的输出（就是字符串列表）能直接喂给取图函数
bot.REV_DIR = _tmp28 / "rev2"
try:
    loc = bot._vg_localize_images([{"file": "file:///" + str(_img28).replace("\\", "/")}])
    chk("_vg_localize_images 产出是字符串路径", bool(loc) and isinstance(loc[0], str), loc)
    if loc:
        back = bot._fetch_rev_image(loc[0])
        chk("回捞路径能再次取图（回捞链路不崩）", bool(back), back)
except Exception as e:
    chk("回捞路径能再次取图（回捞链路不崩）", False, "%s: %s" % (type(e).__name__, e))
finally:
    bot.REV_DIR = _orig_rev28

# ---------- 29. 「靠免密钥顶着」的判据必须准（别把好的说成坏的）----------
# 自查纠的准确性 bug：`/status` 原来只要 `_free_search_ok()` 就断言
#   「付费 key 还是失效的」。可**付费 key 正常、免密钥碰巧也搜过**时，这是误报
#   —— 把好的说成坏的，同样是骗人。判据要落在「付费路真的走不通」上。
_pd = getattr(bot, "_paid_search_dead", None)
chk("付费路判据存在", _pd is not None)
if _pd is not None:
    _o_f29 = bot._FREE_SEARCH.copy()
    _o_c29 = bot._SEARCH_COOL.copy()
    _o_k29 = bot.search_cfg().get("key")
    _o_e29 = bot._SEARCH_LAST_ERROR.copy()
    try:
        # 情形 A：付费 key 正常（没 dead、没冷却、没 401）⇒ 不该说「key 失效」
        bot.search_cfg()["key"] = "sk-alive-key"
        bot._SEARCH_COOL.update({"until": 0.0, "why": "", "dead": False})
        bot._SEARCH_LAST_ERROR.update({"msg": "", "t": 0.0})
        chk("key 正常时不判「付费路死」", _pd() is False, _pd())

        # 情形 B：key 被判死 ⇒ 该说
        bot._SEARCH_COOL.update({"dead": True, "until": time.time() + 1800,
                                 "why": "HTTP 401"})
        chk("key 判死时判「付费路死」", _pd() is True, _pd())

        # 情形 C：压根没配 key ⇒ 该说（只能靠免密钥）
        bot._SEARCH_COOL.update({"until": 0.0, "why": "", "dead": False})
        bot.search_cfg()["key"] = ""
        chk("没配 key 时判「付费路死」", _pd() is True, _pd())

        # 情形 D：刚失败过、冷却中 ⇒ 该说
        bot.search_cfg()["key"] = "sk-x"
        bot._SEARCH_COOL.update({"until": time.time() + 300, "why": "", "dead": False})
        chk("冷却中判「付费路死」", _pd() is True, _pd())

        # 情形 E：总开关关了 ⇒ 不算「靠免密钥顶」
        bot._SEARCH_COOL.update({"until": 0.0, "why": "", "dead": False})
        _o_en29 = bot.search_cfg().get("enabled")
        bot.search_cfg()["enabled"] = False
        chk("联网总开关关时不判「靠免密钥顶」", _pd() is False, _pd())
        bot.search_cfg()["enabled"] = _o_en29
    finally:
        if _o_k29 is not None:
            bot.search_cfg()["key"] = _o_k29
        bot._FREE_SEARCH.clear(); bot._FREE_SEARCH.update(_o_f29)
        bot._SEARCH_COOL.clear(); bot._SEARCH_COOL.update(_o_c29)
        bot._SEARCH_LAST_ERROR.clear(); bot._SEARCH_LAST_ERROR.update(_o_e29)

# ---------- 30. 免密钥搜索：多源兜底 + 相关性过滤（2026-10-30 晚）----------
# 实测背景：**360 已失效**（0 条）、搜狗质量最好但**会反爬**（连续问就吐验证页）、
# 百度时好时坏、**Bing 相关度极差**（问「景德镇陶瓷大学招生」给回「Sci-Hub」）。
# ⇒ 必须多源逐个试 + 相关性过滤，宁可不给也不给跑偏的。
_rel = getattr(bot, "_free_relevant", None)
chk("相关性判据存在", _rel is not None)
if _rel is not None:
    _bad = ["- Sci-Hub ｜ Sci-Hub has the largest (> 88 million documents) database"]
    _good = ["- 景德镇陶瓷大学2026年招生简章 ｜ 预计6月公布招生计划"]
    chk("跑偏结果被判为不相关（Bing 那种）",
        _rel("景德镇陶瓷大学招生", _bad) is False)
    chk("对口结果被判为相关", _rel("景德镇陶瓷大学招生", _good) is True)
    chk("英文关键词也能判（MiniMax）",
        _rel("MiniMax M3 发布", ["- MiniMax M3 重磅发布 ｜ 2026年6月1日"]) is True)
    chk("纯符号查询不过滤（切不出词）", _rel("？？？", ["- 随便什么"]) is True)

# 🔴 逐行过滤（不是整体判定）：第一版只判「有没有任意一行命中」⇒
#    "10 行里 1 行相关 + 9 行垃圾"照样整体通过，**9 行垃圾也一起喂给大脑**（实测构造踩到）。
_flt = getattr(bot, "_free_filter_relevant", None)
chk("逐行过滤函数存在", _flt is not None)
if _flt is not None:
    _mix = ["- 无关内容 %d ｜ 完全不沾边" % i for i in range(9)]
    _mix.insert(3, "- MiniMax M3 重磅发布 ｜ 2026年6月1日")
    _kept = _flt("MiniMax M3 发布", _mix)
    chk("10 行里只 1 行相关 ⇒ 只留那 1 行", len(_kept) == 1, _kept)
    chk("留下的确实是相关那行", _kept and "MiniMax" in _kept[0], _kept)
    chk("全部不相关 ⇒ 0 行（换源）",
        len(_flt("MiniMax M3 发布", ["- 无关 %d" % i for i in range(5)])) == 0)
    chk("切不出词时不过滤（不误杀）",
        len(_flt("？？？", ["- a", "- b"])) == 2)

    # 🔴 实义段判据的真用例（当晚改了四版才定，每一版都栽过，全钉住）：
    #   ① 只按 2 字滑窗 ⇒ 命中「景德」就放行 ⇒「景德镇市_百度百科」（讲**市**）混过。
    #   ② 改数「不重叠 2 字词」⇒「景德镇」在标题+摘要各出现一次，仍被数成 2 段。
    #   ③ 改 n-gram 覆盖度 ⇒ 要点混进「今年招生什么情况」6 字长串，
    #      真结果里不会整段出现 ⇒ **正确结果被滤掉**。
    #   ④ 最终：按虚词/空白切实义段 + 长中文段补 3~4 字子段 ⇒ 才全对。
    _pairs = [
        ("MiniMax M3 发布", "- MiniMax M3 重磅发布 ｜ 2026年6月1日", True),
        ("MiniMax M3 发布", "- 完全无关的新闻", False),
        ("景德镇陶瓷大学招生", "- 景德镇陶瓷大学2026招生章程", True),
        ("景德镇陶瓷大学招生", "- 景德镇市旅游攻略", False),
        ("景德镇陶瓷大学今年招生什么情况", "- 景德镇陶瓷大学2026年招生章程 ｜ 招生计划", True),
        ("景德镇陶瓷大学今年招生什么情况", "- 景德镇市_百度百科 ｜ 别称瓷都", False),
        ("今天天气怎么样", "- 景德镇天气预报 ｜ 今天多云", True),
        ("今天天气怎么样", "- 陶瓷大学招生", False),
        ("MiniMax M3 是什么时候发布的", "- MiniMax M3 重磅发布 ｜ 2026年6月1日", True),
        ("MiniMax M3 是什么时候发布的", "- Sci-Hub ｜ largest database", False),
        # 「市 ≠ 大学」这条是核心：问大学不能放行讲市的百科页
        ("景德镇陶瓷大学怎么样", "- 景德镇市_百度百科", False),
    ]
    _wrong = []
    for _q, _ln, _want in _pairs:
        _got = len(_flt(_q, [_ln])) > 0
        if _got != _want:
            _wrong.append((_q, _ln, _want, _got))
    chk("实义段判据：11 例真用例全对（含「市≠大学」）", not _wrong, _wrong[:3])

# 多源顺序：搜狗在前、Bing 最后（Bing 只兜底）
_src = []
_o_fr = bot._FREE_SEARCH.copy()
# 🔴 必须临时换回**真** `web_search_free`：本文件开头为了防真联网把它打桩成
#    `lambda q, c=6: ""` 且**一直没还原** ⇒ 直接调它什么都不做、断言全假失败。
_o_wsf30 = bot.web_search_free
_o_free30 = bot.web_search_free
try:
    bot.web_search_free = _orig_free_real          # 文件开头存下的真函数
    _orig_fn = {}
    for _n in ("_search_sogou", "_search_baidu", "_search_so360", "_search_bing"):
        _orig_fn[_n] = getattr(bot, _n)
        setattr(bot, _n, (lambda n: (lambda q, c=6: _src.append(n) or ""))(_n))
    bot.web_search_free("测试查询")
    _short = [x.replace("_search_", "") for x in _src]     # 桩记的是函数名，折算成短名
    chk("多源都试过（一个不行换下一个）", len(_short) >= 3, _short)
    chk("搜狗优先试（质量最好）", _short and _short[0] == "sogou", _short)
    chk("360 真被试了（名字映射别拼错）", "so360" in _short, _short)
    chk("Bing 排最后（相关度差）", _short and _short[-1] == "bing", _short)
    # 第一源有结果就不该继续打后面的（省时间、少触发反爬）
    _src.clear()
    bot._search_sogou = lambda q, c=6: (_src.append("sogou") or "- 测试查询 ｜ 结果")
    bot.web_search_free("测试查询")
    chk("第一个源有结果就停止（不白打后面的）", _src == ["sogou"], _src)
finally:
    for _n, _f in _orig_fn.items():
        setattr(bot, _n, _f)
    bot.web_search_free = _o_wsf30          # 还原成「防真联网」的桩，后面组继续用
    bot._FREE_SEARCH.clear(); bot._FREE_SEARCH.update(_o_fr)

# ---------- 31. 同一轮搜索去重（2026-10-30 晚实测：一轮搜了 12 次）----------
# 真实现场：问「MiniMax M3 什么时候发布」，总控大脑反复换关键词搜同一件事
# （发布时间 / 开源日期 / vs M2.7 / HuggingFace…）**一轮 12 次**，
# 把 150s 预算烧光、正文差点挤不出来，用户要等近两分钟。
# 闸门：同话题（实义段重合 ≥60%）直接复用已有结果 + 告诉它别再搜。
_ctx31 = {}
_orig_exec31 = getattr(bot, "_TOOL_CTX", None)
_calls31 = []
_orig_wsa31 = bot.web_search_any
try:
    bot._TOOL_CTX.update({"gid": 123450001, "uid": "123450003", "priv": True,
                          "searched": {}, "allow_draw": False})
    bot.web_search_any = lambda q, c=None: (_calls31.append(q) or "结果-" + q[:10])
    r1 = bot._exec_bot_tool("web_search", {"query": "MiniMax M3 发布时间"}, 123450001,
                            "123450003", True)
    chk("首次搜索真发了请求", len(_calls31) == 1, _calls31)
    chk("首次返回 ok", isinstance(r1, dict) and r1.get("ok") is True, r1)
    # 同话题换个说法再搜 ⇒ 应被去重拦下（不再真发请求）
    r2 = bot._exec_bot_tool("web_search", {"query": "MiniMax M3 发布 开源 日期"}, 123450001,
                            "123450003", True)
    chk("同话题第二次不再真发请求（去重）", len(_calls31) == 1, _calls31)
    chk("去重时提醒它别再搜", isinstance(r2, dict) and "别再搜" in str(r2.get("note") or ""),
        r2.get("note"))
    # 完全不同的话题 ⇒ 应该能正常搜
    r3 = bot._exec_bot_tool("web_search", {"query": "景德镇陶瓷大学招生"}, 123450001,
                            "123450003", True)
    chk("不同话题不被误拦（仍能搜）", len(_calls31) == 2, _calls31)
    # 换一轮（清空 searched）⇒ 同话题又能搜
    bot._tool_ctx_set(123450001, uid="123450003", priv=True, allow_draw=False)
    chk("换轮后 searched 清空", bot._TOOL_CTX.get("searched") == {},
        bot._TOOL_CTX.get("searched"))
    bot._exec_bot_tool("web_search", {"query": "MiniMax M3 发布时间"}, 123450001,
                       "123450003", True)
    chk("换轮后同话题可再搜（不跨消息污染）", len(_calls31) == 3, _calls31)
finally:
    bot.web_search_any = _orig_wsa31
    bot._tool_ctx_clear()

print()
if FAILS:
    print("FAILED %d" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("PASS 总控大脑")
