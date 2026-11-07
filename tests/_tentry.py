# -*- coding: utf-8 -*-
"""阶段 3 验收①：**生产消息入口级**回归（经 handle_group_message / handle_private_message）。

区别于 _tboundary.py（直接调 brain_with_tools）：这里从**真实消息入口**进 ——
事件形状、正则口令链、权限、引用捕获全部真跑，只有「下游出口」打桩：
  · brain_with_tools —— 记录大脑实际收到的 user 文本 + system 文本 + 工具面，
    不真调网关（决策质量由 _tboundary 的 18 例真模型回归负责，这里负责**链路**）；
  · draw_submit / handle_moss_* / handle_i2i —— 记录认领与引擎参数。

覆盖 6 个验收必验案例：
  1. 引用 Moss JSON +「画个这个」 → 入口正则认裸指令，引用原文当提示词直发（不走大脑）；
  2. 引用 Moss JSON +「画这个」   → 同上（裸指令变体）；
  3. 图片 +「把帽子低一点」       → 主脑（ai_reply 带图带工具，自判改图/问答）；
  4. 用户指定引擎（「画[anima] xx」）→ 显式本地档强制 ComfyUI，不受 moss 偏好影响；
  5. 大脑超时/挂掉 → ai_reply 老链路降级（不白屏、不假话）；
  6. 普通群友经完整入口调用安全工具（工具面裁剪：无 qq_send_group/remember，有 draw_image）。
用法：python tests/_tentry.py
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
import json
import struct
import sys
import time
import zlib

sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402

bot.save_config = lambda: True
bot.SELF_ID = 123450002
GID = 123450001
OWNER = 123450005
PEER = 999111          # 普通群友

FAIL = []

# ── 出口打桩：记录认领，零副作用 ─────────────────────────────────────
CLAIMS = []

_omni_calls = []


def _stub_brain(gid, content, sys_text, **kw):
    """总控大脑桩：记录喂进来的 user 文本/system/工具面，返回固定回复。

    2026-11-03 裁决①改：引用成品 Moss JSON 的裸指令现在**交大脑判**——
    桩在这里模拟大脑的正确决策：识别出「引用+画图」意图 → 调 draw_image(from_quote=true)
    → 工具层取引用原文提交。这样入口→大脑→工具→draw_submit 全链都被真跑。
    非「引用+画图」的消息保持桩回复不调工具。
    """
    _rec = {
        "gid": gid, "user": str(content or ""), "sys": str(sys_text or ""),
        "privileged": kw.get("privileged"), "imgs": tuple(kw.get("imgs") or ()),
        "qmid": kw.get("qmid"), "quote": str(kw.get("quote") or ""),
        "tools": [t["function"]["name"] for t in bot._omni_tools(bool(kw.get("privileged")))],
    }
    _omni_calls.append(_rec)
    u = str(content or "")
    if "【引用资料开始】" in u and bot._BARE_DRAW_RE.match(u.split("【引用资料结束】")[-1].strip() or ""):
        _r = bot._exec_bot_tool("draw_image",
                                {"what": "把引用的提示词画出来", "from_quote": True},
                                gid, uid=kw.get("uid") or "", priv=bool(kw.get("privileged")),
                                imgs=list(kw.get("imgs") or ()), qmid=kw.get("qmid") or 0,
                                who="测试", quote=str(kw.get("quote") or ""))
        CLAIMS.append("brain_tool:draw_image(from_quote)→%s" % _r)
        return "custom", "照着引用的那条画好了（桩）", True
    return "custom", "（大脑桩回复）", False


bot.brain_with_tools = _stub_brain

_orig_draw_submit = bot.draw_submit
def _rec_draw_submit(gid, what, style, who, *a, **kw):
    CLAIMS.append("draw_submit:what=%s|style=%s|engine=%s|i2i=%s|edit=%s|raw=%s" % (
        str(what)[:30], style, kw.get("engine"),
        bool(kw.get("i2i")), str(kw.get("edit") or "")[:20], bool(kw.get("raw_prompt"))))
    return True, "已排队（桩）"
bot.draw_submit = _rec_draw_submit

for _name in ("handle_moss_reverse", "handle_moss_edit", "handle_moss_draw",
              "handle_i2i", "handle_reverse", "ai_reply"):
    def _mk(name):
        orig = getattr(bot, name)
        def rec(*a, **kw):
            CLAIMS.append("%s:%s" % (name, str(a[1] if len(a) > 1 else "")[:30]))
            return orig(*a, **kw)
        setattr(bot, name, rec)
        return orig
    _mk(_name)

bot.send_group = lambda gid, text, *a, **kw: CLAIMS.append("send:" + str(text)[:40])

# 发送层最终出口（_api_send → api）也打桩：案例5 打开 _MUTE_BYPASS 后，降级回复会真走到这里。
# 注意 message 可能是段列表（send_reply 带引用走 send_segments）——拍平取纯文本再记。
_api_calls = []
def _api_stub(action, **params):
    m = params.get("message")
    if isinstance(m, list):
        m = " ".join(s.get("data", {}).get("text", "") for s in m
                     if isinstance(s, dict) and s.get("type") == "text")
    _api_calls.append((action, str(m or "")[:80]))
    return {"status": "ok", "retcode": 0}
bot.api = _api_stub

# 看图问答走线程 → 同步执行
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
                CLAIMS.append("线程异常:" + str(e)[:60])
bot.threading.Thread = _SyncThread

# ── 本地测试图（8x8 PNG）───────────────────────────────────────────
def _mk_png(path):
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    raw_row = bytes([0]) + bytes([0xcc, 0x88, 0x99]) * 8
    raw = raw_row * 8
    sig = bytes([0x89]) + b"PNG" + bytes([0x0d, 0x0a, 0x1a, 0x0a])
    blob = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    open(path, "wb").write(blob)


IMG1 = _os.path.join(bot.STATE, "_tentry_img1.png")
IMG2 = _os.path.join(bot.STATE, "_tentry_img2.png")
_mk_png(IMG1); _mk_png(IMG2)

MOSS_JSON = json.dumps({
    "summary": {"caption": "A girl in white dress under a tree"},
    "canvas": {"width": 1280, "height": 1280},
    "items": [{"type": "char", "yxwh": [0.3, 0.3, 0.4, 0.6], "cap": "white dress girl"}],
}, ensure_ascii=False)


def _quote_mid(body, uid=OWNER):
    """往 bot 的引用缓存里塞一条「被引用消息」，返回它的 mid。"""
    _mid = getattr(_quote_mid, "_n", 5000) + 1
    _quote_mid._n = _mid
    bot._msg_cache[str(_mid)] = "测试者：%s" % body
    bot._msg_images[str(_mid)] = []      # 引用消息本身不带图
    return _mid


def _quote_mid_with_img(body, img):
    _mid = getattr(_quote_mid, "_n", 5000) + 1
    _quote_mid._n = _mid
    bot._msg_cache[str(_mid)] = "测试者：%s" % (body or "[图片]")
    bot._msg_images[str(_mid)] = [{"file": "file:///" + img.replace("\\", "/")}]
    return _mid


def ev(text, imgs=(), qmid=0, uid=OWNER, role="owner", is_group=True):
    """构造群/私聊消息事件（@ SELF_ID = addressed）。"""
    _mid = getattr(ev, "_mid", 9000) + 1
    ev._mid = _mid
    segs = []
    if is_group:
        segs.append({"type": "at", "data": {"qq": str(bot.SELF_ID or "0")}})
    for u in imgs:
        segs.append({"type": "image", "data": {"file": u}})
    if qmid:
        segs.append({"type": "reply", "data": {"id": qmid}})
    segs.append({"type": "text", "data": {"text": text}})
    d = {"self_id": 123450002, "post_type": "message",
         "message_id": _mid, "user_id": uid,
         "sender": {"role": role, "card": "测试者"},
         "message": segs}
    if is_group:
        d["message_type"] = "group"
        d["group_id"] = GID
    else:
        d["message_type"] = "private"
        d["sub_type"] = "friend"
    return d


def _route(data, is_group=True):
    CLAIMS.clear()
    _omni_calls.clear()
    bot._last_img_turn.clear()
    if is_group:
        bot.handle_group_message(data)
    else:
        bot.handle_private_message(data)
    time.sleep(0.05)


def chk(cond, label, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(label)


print("=== 验收①：生产消息入口级回归（handle_group_message / handle_private_message）===")
print()

# ── 案例 1：引用 Moss JSON +「画个这个」 → 大脑判 → draw_image(from_quote) → raw_prompt 直发 ──
# 🔴 2026-11-03 裁决①：决策权归大脑——入口不再正则直发；大脑桩里模拟正确决策
#    （调 draw_image(from_quote=true)，工具层取引用原文，raw_prompt 仍由 JSON 判定）。
_mq = _quote_mid(MOSS_JSON)
_route(ev("画个这个", qmid=_mq))
_hit = [c for c in CLAIMS if c.startswith("draw_submit") and "raw=True" in c]
_brain_seen = bool(_omni_calls) and "【引用资料开始】" in _omni_calls[0]["user"]
_tool_ok = any(c.startswith("brain_tool:draw_image(from_quote)→{'ok': True") for c in CLAIMS)
chk(bool(_hit) and _brain_seen and _tool_ok,
    "案例1 引用MossJSON+「画个这个」→ 经大脑判 → from_quote 取原文 → raw_prompt 直发",
    (_hit or CLAIMS)[:3])

# ── 案例 2：引用 Moss JSON +「画这个」 → 同上 ──
_mq = _quote_mid(MOSS_JSON)
_route(ev("画这个", qmid=_mq))
_hit = [c for c in CLAIMS if c.startswith("draw_submit") and "raw=True" in c]
chk(bool(_hit) and bool(_omni_calls), "案例2 引用MossJSON+「画这个」→ 经大脑 → raw_prompt 直发",
    (_hit or CLAIMS)[:2])

# ── 案例 3：图片 +「把帽子低一点」 → 落主脑（带图+文字统一交 brain_with_tools，
#    它有 draw_like_this 工具，改不改自己判断；旧口语化改图正则已删）──
_route(ev("把帽子低一点", imgs=(IMG1,)))
_hit = [c for c in CLAIMS if c.startswith("ai_reply:") and "把帽子低一点" in c]
chk(bool(_hit), "案例3 带图+改图动词 → 主脑（ai_reply 带图带工具）", (_hit or CLAIMS)[:2])

# ── 案例 4：用户指定本地档（画[anima] xx）→ 强制 ComfyUI，不受 moss 偏好影响 ──
_route(ev("画[anima]一只戴墨镜的橘猫"))
_hit = [c for c in CLAIMS if c.startswith("draw_submit")]
_ok4 = any("style=anima" in c and "engine=None" in c for c in _hit)
chk(_ok4, "案例4 显式本地档 anima → engine 留给 draw_gate 按偏好判（不强制 moss）", (_hit or CLAIMS)[:2])

# ── 案例 4b：用户点名「用厚涂画」 → 同样强制本地档 ──
_route(ev("用厚涂画一只猫"))
_hit = [c for c in CLAIMS if c.startswith("draw_submit")]
_ok4b = any("style=厚涂" in c for c in _hit)
chk(_ok4b, "案例4b 「用厚涂画」→ style=厚涂（本地档优先于引擎偏好）", (_hit or CLAIMS)[:2])

# ── 案例 5：大脑挂掉 → ai_reply 老链路降级（有回复、不白屏）──
def _boom(*a, **kw):
    raise RuntimeError("网关超时（桩）")
_sent = []
bot.send_group = lambda gid, text, *a, **kw: (_sent.append(str(text)[:60]),
                                              CLAIMS.append("send:" + str(text)[:40]))
bot.chat = lambda msgs, *a, **kw: ("custom", "（老链路降级回复桩）")
# 🔴 次序教训：必须**先挂会炸的大脑再路由**（上次 _stub_brain 把 _boom 盖了，桩替它答了）。
#    逃生舱 `_MUTE_BYPASS` 同 _thuman 先例：bot.api 已是桩，放行也真发不出去；
#    不开它的话降级回复在 `_api_send` 就被测试静音吞掉，`_api_calls` 永远为空。
bot._MUTE_BYPASS["on"] = True
bot._auto_picture = lambda *a, **kw: None
bot.maybe_voice = lambda *a, **kw: None
bot.brain_with_tools = _boom
_api_calls.clear()
_route(ev("今天天气真不错啊"))
bot.brain_with_tools = _stub_brain          # 跑完恢复，后面案例还靠它
bot._MUTE_BYPASS["on"] = False
_ok5 = any("老链路降级回复桩" in m for _act, m in _api_calls)
chk(_ok5, "案例5 大脑挂掉 → 回落老链路且有回复", _api_calls[:1])

# ── 案例 6：普通群友经完整入口进大脑 → 工具面被裁剪（无 qq_send_group/remember）──
# 注意：画图口令「画一只猫」在入口正则就被 parse_draw 吃掉（口令优先是设计），
# 群友真正进大脑的是**口令认不出**的自然语言。用一句画图正则不认的派活话验证工具面。
_route(ev("帮我搜一下最近有什么新番", uid=PEER, role="member"))
_ok6 = False
_detail = ""
if _omni_calls:
    c = _omni_calls[0]
    t = c["tools"]
    _ok6 = (not c["privileged"]) and "draw_image" in t and "web_search" in t \
        and "qq_send_group" not in t and "remember" not in t
    _detail = "privileged=%s tools=%s" % (c["privileged"], t)
else:
    _detail = "大脑桩没被调用: %s" % CLAIMS[:2]
chk(_ok6, "案例6 普通群友进大脑 → 工具面裁剪（有 draw_image/web_search、无 qq_send_group/remember）",
    _detail[:120])

# ── 案例 7（补充）：普通群友引用 Moss JSON +「画个这个」 → 同样经大脑收口 ──
_mq = _quote_mid(MOSS_JSON)
_route(ev("画个这个", qmid=_mq, uid=PEER, role="member"))
_hit = [c for c in CLAIMS if c.startswith("draw_submit") and "raw=True" in c]
chk(bool(_hit) and bool(_omni_calls), "案例7 群友裸指令+引用JSON → 经大脑 → raw_prompt 直发",
    (_hit or CLAIMS)[:2])

# ── 案例 8（补充）：私聊引用 JSON +「画个这个」 → 私聊链路同收 ──
# 🔴 is_group 必须同时给 ev（事件形状）和 _route（走哪个入口）——
#    上次只给了 ev，_route 默认群入口，私聊事件进了 handle_group_message 被静默吞掉。
_mq = _quote_mid(MOSS_JSON)
_route(ev("画个这个", qmid=_mq, is_group=False), is_group=False)
_hit = [c for c in CLAIMS if c.startswith("draw_submit") and "raw=True" in c]
chk(bool(_hit) and bool(_omni_calls), "案例8 私聊裸指令+引用JSON → 经大脑 → 直发",
    (_hit or CLAIMS)[:2])

# ── 案例 9（补充）：带图引用 +「画这个」 → 当图生图（引擎=moss → moss反推重画）──
_mq = _quote_mid_with_img("", IMG2)
_route(ev("画这个", qmid=_mq))
_hit = [c for c in CLAIMS if c.startswith("handle_moss_reverse") or
        (c.startswith("draw_submit") and "i2i=True" in c)]
chk(bool(_hit), "案例9 引用带图消息+「画这个」→ 图生图（moss 反推重画）", (_hit or CLAIMS)[:2])

# ── 案例 10（补充）：大脑实际收到的引用文本带数据围栏（验收③入口面）──
_mq = _quote_mid(MOSS_JSON)
_route(ev("帮我看看这段设定讲的是什么", qmid=_mq))
_ok10 = False
if _omni_calls:
    u = _omni_calls[0]["user"]
    _ok10 = "【引用资料开始】" in u and "【引用资料结束】" in u \
        and "是数据不是指令" in u and u.strip().endswith("帮我看看这段设定讲的是什么")
chk(_ok10, "案例10 大脑收到的引用已包数据围栏（防注入格式）",
    (_omni_calls[0]["user"][:80] if _omni_calls else CLAIMS[:2]))

print()
print("=== 汇总 ===")
print("失败 %d 项" % len(FAIL))
if FAIL:
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)
