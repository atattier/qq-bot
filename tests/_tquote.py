# -*- coding: utf-8 -*-
"""全局引用读取：事件形状 / 正文上限 / 引用图进大脑（全离线，不联网）。

2026-11-03 主人实测：引用一条 3.7k 字的提示词发「画个」，
  · bot 收到的事件里 reply 段被 NapCat 丢了（qmid=0）→ 已由 NapCat 补丁修；
  · 就算读到了，正文也被截在 3000 字 → 本测试钉住 ai.quote_max（默认 12000）；
  · 普通聊天里大脑看不到「他引用的图」→ 本测试钉住 quoted_images(qmid) 进视觉 messages。

用法：python tests/_tquote.py
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
import sys

sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402

FAIL = []


def chk(label, cond, extra=""):
    print(("  [OK] " if cond else "  [失败] ") + label + ("" if cond else " | " + str(extra)))
    if not cond:
        FAIL.append(label)


print("=== 全局引用读取 ===")

# ---------- 1. 事件形状：三种都要认 ----------
ev_reply = {"message": [{"type": "reply", "data": {"id": "123"}},
                        {"type": "text", "data": {"text": "画个"}}]}
ev_raw = {"message": [{"type": "text", "data": {"text": "画个"}}],
          "raw_message": "[CQ:reply,id=456]画个"}
ev_top = {"message": [{"type": "text", "data": {"text": "画个"}}], "reply": {"id": 789}}
ev_src = {"message": [{"type": "text", "data": {"text": "画个"}}], "source": {"message_id": 790}}
ev_img = {"message": [{"type": "reply", "data": {"id": "999"}},
                      {"type": "image", "data": {"url": "http://x/1.png"}}]}
chk("reply 段在 message 数组里", bot._scan_event(ev_reply)[1] == 123, bot._scan_event(ev_reply))
chk("raw_message 的 CQ:reply 也认", bot._scan_event(ev_raw)[1] == 456, bot._scan_event(ev_raw))
chk("顶层 reply 字段也认", bot._scan_event(ev_top)[1] == 789, bot._scan_event(ev_top))
chk("顶层 source 字段也认", bot._scan_event(ev_src)[1] == 790, bot._scan_event(ev_src))
_o, _q = bot._scan_event(ev_img)
chk("引用段 + 图并存：qmid/图都不丢", _q == 999 and len(_o) == 1, (_o, _q))
chk("qmid 统一转 int（字符串 id 也稳）", isinstance(bot._scan_event(ev_reply)[1], int))

# ---------- 2. 正文上限：默认 12000，不再截 3000 ----------
BODY = "长提示词" * 2500          # 10000 字
bot._msg_cache.clear()
_orig_api = bot.api
bot.api = lambda action, **kw: {"data": {
    "sender": {"card": "主人", "user_id": 1},
    "message": [{"type": "text", "data": {"text": BODY}}]}}
try:
    got = bot.quoted_text(111)
finally:
    bot.api = _orig_api
chk("引用正文读满 10000 字（默认上限 12000）", len(got) >= 10000 - 10, len(got))

bot._msg_cache.clear()
_ai = bot.CFG.setdefault("ai", {})
_old_qm = _ai.get("quote_max")
_ai["quote_max"] = 800
bot.api = lambda action, **kw: {"data": {
    "sender": {"card": "主人", "user_id": 1},
    "message": [{"type": "text", "data": {"text": BODY}}]}}
try:
    got2 = bot.quoted_text(112)
finally:
    bot.api = _orig_api
    if _old_qm is None:
        _ai.pop("quote_max", None)
    else:
        _ai["quote_max"] = _old_qm
chk("ai.quote_max 可调小（800）", len(got2) <= 820, len(got2))

# ---------- 3. parse_message 的引用分支走同一份长文 ----------
bot._msg_cache.clear()
bot.api = lambda action, **kw: {"data": {
    "sender": {"card": "主人", "user_id": 1},
    "message": [{"type": "text", "data": {"text": BODY}}]}}
try:
    _txt, _at, _ats, _quote = bot.parse_message(
        [{"type": "reply", "data": {"id": "113"}},
         {"type": "text", "data": {"text": "画个"}}])
finally:
    bot.api = _orig_api
    bot._msg_cache.clear()
chk("parse_message 的 quote 带全文", len(_quote) >= 10000 - 10, len(_quote))
chk("parse_message 正文没被引用污染", _txt == "画个", _txt)

# ---------- 4. 引用图进大脑（普通聊天，不是画图专用） ----------
CAP = {"msgs": None}


def _fake_chat_once(name, messages, tools=None, **kw):
    CAP["msgs"] = messages
    return "看到了，是张猫图。", False


_orig = (bot.chat_once_tools, bot.provider_cfg, bot.quoted_images,
         bot._fetch_rev_image, bot._tool_ctx_set)
bot.chat_once_tools = _fake_chat_once
bot.provider_cfg = lambda name: {"url": "http://stub"}
bot.quoted_images = lambda mid: [{"type": "image", "data": {"url": "http://stub/q.png"}}]
bot._fetch_rev_image = lambda d: "state/reverse/stub.png"
bot._tool_ctx_set = lambda *a, **kw: None
_rev_fit = None
if bot.reverser is not None:
    _rev_fit = bot.reverser.fit_image_data_url
    bot.reverser.fit_image_data_url = lambda p: "data:image/jpeg;base64,QUJD"
try:
    bot.brain_with_tools(123450001, "这是什么", "系统提示", who="主人", uid="1",
                         imgs=(), qmid=4321, mid=1)
finally:
    (bot.chat_once_tools, bot.provider_cfg, bot.quoted_images,
     bot._fetch_rev_image, bot._tool_ctx_set) = _orig
    if _rev_fit is not None:
        bot.reverser.fit_image_data_url = _rev_fit
    bot._msg_cache.clear()

_user = [m for m in (CAP["msgs"] or []) if m.get("role") == "user"]
chk("大脑 messages 有 user 轮", bool(_user))
_img_parts = []
if _user and isinstance(_user[-1].get("content"), list):
    _img_parts = [p for p in _user[-1]["content"] if p.get("type") == "image_url"]
chk("引用里的图被塞进视觉 messages", len(_img_parts) == 1, _user[-1] if _user else None)
_sys = (CAP["msgs"] or [{}])[0].get("content", "")
chk("图片状态告诉大脑「引用里有 1 张图」", "引用里有 1 张图" in _sys, _sys[-300:])

# ---------- 5. 裸画图口令：引用/最近提示词优先 ----------
import json
import time
import types

for said in ("画个", "画这个", "画这张", "画它", "画个这个", "画个这张",
             "画一下", "画一下这个", "照这个画", "按这个画", "用这个画", "重画"):
    chk("裸指令识别 %s" % said, bool(bot._BARE_DRAW_RE.match(said)), said)
chk("「画一只猫」不是裸指令", not bot._BARE_DRAW_RE.match("画一只猫"))

chk("裸指令优先取引用正文",
    bot._bare_draw_source(999, "引用 主人：引用正文ABC", 0) == "引用正文ABC")
_orig_qt = bot.quoted_text
bot.quoted_text = lambda mid: "引用 主人：引用正文DEF"
try:
    chk("quote 为空时回落到 qmid", bot._bare_draw_source(999, "", 123) == "引用正文DEF")
finally:
    bot.quoted_text = _orig_qt
bot._last_prompt[778] = ("最近提示词XYZ", time.time())
try:
    chk("无引用时回落最近提示词", bot._bare_draw_source(778, "", 0) == "最近提示词XYZ")
    chk("带图时不接上一轮文字提示词",
        bot._bare_draw_source(778, "", 0, allow_last=False) == "")
finally:
    bot._last_prompt.pop(778, None)

# 裸指令 + 只有图：按引擎走画同款 / 云端反推重画
_orig_path = (bot.draw_engine, bot.handle_i2i, bot.handle_moss_reverse)
_calls = []
bot.draw_engine = lambda gid: "moss"
bot.handle_moss_reverse = lambda *a, **kw: _calls.append(("moss", a))
bot.handle_i2i = lambda *a, **kw: _calls.append(("i2i", a))
try:
    _eng = bot._bare_draw_by_image(123450001, True, ["x.png"], 123, 7)
finally:
    bot.draw_engine, bot.handle_i2i, bot.handle_moss_reverse = _orig_path
chk("裸指令+图：moss 会话走反推重画",
    _eng == "moss" and _calls and _calls[-1][0] == "moss"
    and _calls[-1][1][4] == 123 and _calls[-1][1][5] == 7, _calls)

_calls.clear()
bot.draw_engine, bot.handle_i2i, bot.handle_moss_reverse = (
    lambda gid: "comfyui",
    lambda *a, **kw: _calls.append(("i2i", a)),
    lambda *a, **kw: _calls.append(("moss", a)))
try:
    _eng = bot._bare_draw_by_image(123450001, True, ["x.png"], 123, 7)
finally:
    bot.draw_engine, bot.handle_i2i, bot.handle_moss_reverse = _orig_path
chk("裸指令+图：comfyui 会话走画同款",
    _eng == "comfyui" and _calls and _calls[-1][0] == "i2i"
    and _calls[-1][1][4] == 123, _calls)

# 2026-11-03 真机回归：早段曾把「画这个」直接送 handle_i2i（引用文字也当图生图）
_orig_msg_env = (bot.quoted_text, bot.draw_submit, bot.send_group,
                 bot.draw_engine, bot._comfy_at_ok, bot.handle_i2i)
_cap_msg = {}
bot.quoted_text = lambda mid: "引用 主人：一只猫在窗台上"
bot.draw_submit = lambda *a, **kw: (
    _cap_msg.update({"args": a, "kw": kw}), (True, "ok"))[1]
bot.send_group = lambda *a, **kw: None
bot.draw_engine = lambda gid=None: "moss"
bot._comfy_at_ok = lambda *a, **kw: True
bot.handle_i2i = lambda *a, **kw: _cap_msg.update({"i2i": True})
try:
    bot._handle_message({"message_id": 987654321, "message": [
        {"type": "reply", "data": {"id": "123"}},
        {"type": "text", "data": {"text": "画这个"}}]}, -123450003, False)
finally:
    (bot.quoted_text, bot.draw_submit, bot.send_group,
     bot.draw_engine, bot._comfy_at_ok, bot.handle_i2i) = _orig_msg_env
chk("「画这个」+引用文字走统一画图（不再被早段送进画同款）",
    not _cap_msg.get("i2i") and _cap_msg.get("args", (None, None))[1] == "一只猫在窗台上",
    _cap_msg)

# ---------- 6. 已编译 Moss JSON 直通 ----------
_moss_json = json.dumps({"summary": {"caption": "一只猫"}, "items": []}, ensure_ascii=False)
chk("成品 Moss JSON 能识别", bot._is_moss_json_prompt(_moss_json), _moss_json[:60])
chk("JSON 后带说明文字也能识别",
    bot._is_moss_json_prompt(_moss_json + "\n—— Moss 布局提示词"), "trailing note")
chk("普通描述不误判成 JSON", not bot._is_moss_json_prompt("一只猫在窗台上"))

class _FakeQ:
    def __init__(self):
        self.jobs = []
    def put(self, job):
        self.jobs.append(job)
    def qsize(self):
        return 0

_orig_gate, _orig_q = bot.draw_gate, bot._draw_q
_qcap = _FakeQ()
bot.draw_gate = lambda *a, **kw: None
bot._draw_q = _qcap
try:
    _ok, _note = bot.draw_submit(123450001, _moss_json, None, "t",
                                 raw_prompt=True)
finally:
    bot.draw_gate, bot._draw_q = _orig_gate, _orig_q
_job = _qcap.jobs[-1] if _qcap.jobs else {}
chk("draw_submit 把 raw_prompt 带进作业并强制 moss",
    _ok and _job.get("raw_prompt") is True and _job.get("what") == _moss_json
    and _job.get("engine") == "moss", _job)

_called = {"layout": 0, "prompt": None}


def _boom_layout(*a, **kw):
    _called["layout"] += 1
    raise AssertionError("raw_prompt 不该再走 _moss_prompt")


def _fake_moss_generate(cfg, prompt, **kw):
    _called["prompt"] = prompt
    return {"file": "state/stub.png", "name": "stub.png", "seconds": 1,
            "size": [1024, 1024], "engine": "moss", "style": None}


_orig_pack = (bot._moss_prompt, bot.moss_gen, bot._moss_safety_rewrite,
              bot.image_folder, bot.send_drawn, bot._delete_drawn_file, bot._moss_cfg)
bot._moss_prompt = _boom_layout
bot.moss_gen = types.SimpleNamespace(generate=_fake_moss_generate)
bot._moss_safety_rewrite = lambda: None
bot.image_folder = lambda: "state"
bot.send_drawn = lambda *a, **kw: True
bot._delete_drawn_file = lambda *a, **kw: None
bot._moss_cfg = lambda: {}
try:
    _res = bot.do_draw({"gid": 123450001, "what": _moss_json, "engine": "moss",
                        "raw_prompt": True, "aspect": None, "i2i": None,
                        "lookup": False})
finally:
    (bot._moss_prompt, bot.moss_gen, bot._moss_safety_rewrite,
     bot.image_folder, bot.send_drawn, bot._delete_drawn_file, bot._moss_cfg) = _orig_pack
chk("raw_prompt 跳过布局、原样交给 Moss",
    _called["layout"] == 0 and _called["prompt"] == _moss_json and _res is not None,
    _called)

# ---------- 7. 旧 AI 路由描述校验已删（决策归主脑） ----------
chk("旧路由描述校验 _route_prompt_grounded 已删",
    not hasattr(bot, "_route_prompt_grounded"))

print()
if FAIL:
    print("失败 %d 项：" % len(FAIL))
    for x in FAIL:
        print(" -", x)
    sys.exit(1)
print("全部通过")
