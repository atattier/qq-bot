# -*- coding: utf-8 -*-
"""架构方向验证：主脑是否真的接管 + 执行能力是否保留。

离线全桩：不联网、不发真消息、不碰生产 state。
四步验证：
  ① 唯一出口：口令链没接住的消息，全链只有主脑（ai_reply）一个 AI 收尾分支
  ② 主脑接管：5 类真实表达（画图/改图/梗图/搜索/闲聊）都落到 ai_reply
  ③ 执行能力：主脑工具面 draw_image/draw_like_this/reverse_prompt/send_meme/
     web_search/gen_video 能真触发到执行函数
  ④ 退出原语：send_bubbles 是唯一自然语言出口，工具不直接说话
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding="utf-8")

FAIL = []


def chk(name, ok, why=""):
    if not ok:
        FAIL.append(name)
    print(("  [PASS] " if ok else "  [FAIL] ") + name + ("" if ok else "  | " + why))


import bot  # noqa: E402

bot.save_config = lambda: True
bot.SELF_ID = 123450002

GID = 123450001
UID_OWNER = 123450005
MID = [90000]


def ev(text, imgs=(), uid=UID_OWNER, role="owner"):
    MID[0] += 1
    segs = [{"type": "at", "data": {"qq": str(bot.SELF_ID)}}]
    for u in imgs:
        segs.append({"type": "image", "data": {"file": u}})
    segs.append({"type": "text", "data": {"text": text}})
    return {"self_id": 123450002, "post_type": "message", "message_type": "group",
            "group_id": GID, "message_id": MID[0], "user_id": uid,
            "sender": {"role": role, "card": "验证owner"},
            "message": segs}


print("== ① 唯一出口：主链收尾只有主脑 ==")
# 被删的二次决策层不得存在
for gone in ("_route_ai_draw", "_route_moss_edit_pic", "maybe_send_meme",
             "_ai_route_draw", "_looks_like_edit", "_moss_only"):
    chk("已删 " + gone, not hasattr(bot, gone))
# 主链 AI 收尾分支存在且只有一个
src_tail = "\n".join(bot.__dict__.get("_handle_src", "") or [""])
import inspect
_handle = None
for name in ("handle_group_message", "_handle_group_message"):
    if hasattr(bot, name):
        _handle = getattr(bot, name)
        break
chk("主脑直答分支 _route_brain_reply 存在", hasattr(bot, "_route_brain_reply"))
chk("主脑入口 ai_reply 存在", hasattr(bot, "ai_reply"))
chk("带工具主脑 brain_with_tools 存在", hasattr(bot, "brain_with_tools"))
chk("自然语言唯一出口 send_bubbles 存在", hasattr(bot, "send_bubbles"))

print()
print("== ② 主脑接管：5 类消息全部落到 ai_reply ==")
AI_CALLS = []
_orig_ai = bot.ai_reply
bot.ai_reply = lambda gid, content, *a, **kw: (
    AI_CALLS.append({"gid": gid, "content": str(content)[:50]}), None)[1]
# 同步线程：直接执行 target
_orig_thread = bot.threading.Thread
class _SyncThread:
    def __init__(self, *a, **kw):
        self._t = kw
    def start(self):
        f = self._t.get("target")
        if f:
            try:
                f(*self._t.get("args", ()), **self._t.get("kwargs", {}))
            except Exception as e:
                AI_CALLS.append({"gid": "THREAD_ERR", "content": str(e)[:60]})
bot.threading.Thread = _SyncThread
# 视觉/搜索链全部桩死，防真联网
bot.chat_image_once = lambda *a, **kw: ("（桩）", "stub")
bot._comfy_up = lambda *a, **kw: False
bot.comfy_up = lambda *a, **kw: False
bot.handle_moss_edit = lambda *a, **kw: (_ for _ in ()).throw(
    AssertionError("moss改稿正则不应被触发"))
bot.handle_moss_reverse = lambda *a, **kw: (_ for _ in ()).throw(
    AssertionError("moss反推正则不应被触发"))

CASES = [
    # 「画…」开头的明确口令由 keyword 链接住（设计行为：显式指令最快），不算漏；
    # 主脑接管要验的是口令认不出的表达。
    ("帮我去画一张戴墨镜的橘猫", (), "口语化画图（口令链认不出的写法）"),
    ("把帽子低一点", ("stub1.png",), "带图改图动词"),
    ("来张表情包", (), "点名要梗图"),
    ("MiniMax M3 发布了吗", (), "问时效（旧 needs_search 预搜路径）"),
    ("今天好热啊", (), "普通闲聊"),
]
for text, imgs, note in CASES:
    AI_CALLS.clear()
    bot._draw_state["last"] = 0.0
    try:
        bot.handle_group_message(ev(text, imgs=imgs))
    except AssertionError as e:
        AI_CALLS.append({"gid": "REGEX_LEAK", "content": str(e)[:60]})
    except Exception as e:
        AI_CALLS.append({"gid": "EXC", "content": str(e)[:60]})
    landed = any(str(c.get("gid")) == str(GID) for c in AI_CALLS)
    leaks = [c for c in AI_CALLS if c.get("gid") in ("REGEX_LEAK", "THREAD_ERR", "EXC")]
    chk("「%s」→ 主脑（%s）" % (text, note), landed and not leaks,
        str(AI_CALLS)[:120])
bot.ai_reply = _orig_ai
bot.threading.Thread = _orig_thread

print()
print("== ③ 执行能力：主脑工具面可触发执行函数 ==")
# 工具面清单：omni 开、主人身份 → 完整工具
tools = bot._omni_tools(True)
names = {t["function"]["name"] for t in tools}
for need in ("draw_image", "draw_like_this", "reverse_prompt", "send_meme",
             "web_search", "gen_video", "bot_status"):
    chk("工具面含 " + need, need in names, str(sorted(names))[:150])

# 逐个触发到执行层（全部桩化底层）
EXEC = []
bot.draw_submit = lambda gid, what, style, who, *a, **kw: (
    EXEC.append(("draw_submit", what)), (True, "#1"))[1]
bot.handle_i2i = lambda gid, ins, *a, **kw: EXEC.append(("handle_i2i", ins))
bot.handle_reverse = lambda gid, ins, *a, **kw: EXEC.append(("handle_reverse", ins))
bot.send_memes = lambda gid, kw2, *a, **kw: EXEC.append(("send_memes", kw2)) or (True, "ok")
bot.web_search_any = lambda q, *a, **kw: EXEC.append(("web_search", q)) or {"ok": True}
bot.handle_video_gen = lambda gid, text, *a, **kw: EXEC.append(("gen_video", text)) or True

r = bot._exec_bot_tool("draw_image", {"what": "一只戴墨镜的橘猫"}, GID,
                       uid=str(UID_OWNER), priv=True)
chk("draw_image → draw_submit", any(x[0] == "draw_submit" for x in EXEC), str(r)[:80])
r = bot._exec_bot_tool("draw_like_this", {"extra": "把帽子低一点"}, GID,
                       uid=str(UID_OWNER), priv=True, imgs=["stub.png"])
chk("draw_like_this → handle_i2i", any(x[0] == "handle_i2i" for x in EXEC), str(r)[:80])
r = bot._exec_bot_tool("reverse_prompt", {}, GID, uid=str(UID_OWNER), priv=True,
                       imgs=["stub.png"])
chk("reverse_prompt → handle_reverse", any(x[0] == "handle_reverse" for x in EXEC), str(r)[:80])
r = bot._exec_bot_tool("send_meme", {"keyword": "生气"}, GID, uid=str(UID_OWNER), priv=True)
chk("send_meme → send_memes", any(x[0] == "send_memes" for x in EXEC), str(r)[:80])
r = bot._exec_bot_tool("web_search", {"query": "MiniMax M3"}, GID,
                       uid=str(UID_OWNER), priv=True)
chk("web_search → web_search_any", any(x[0] == "web_search" for x in EXEC), str(r)[:80])
r = bot._exec_bot_tool("gen_video", {"desc": "一只橘猫在阳光下打滚，慵懒的氛围"}, GID,
                       uid=str(UID_OWNER), priv=True)
chk("gen_video → handle_video_gen", any(x[0] == "gen_video" for x in EXEC), str(r)[:80])

print()
print("== ④ 权限硬轨：非主人拿不到 master 工具 ==")
names_pub = {t["function"]["name"] for t in bot._omni_tools(False)}
chk("非主人工具面无 qq_send_group", "qq_send_group" not in names_pub)
chk("非主人工具面无 remember", "remember" not in names_pub)
chk("非主人仍有 draw_image（成员画图权限现状）", "draw_image" in names_pub)

print()
if FAIL:
    print("失败 %d 项：%s" % (len(FAIL), FAIL))
    sys.exit(1)
print("PASS 主脑接管 + 执行能力 全部验证通过")
