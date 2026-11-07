# -*- coding: utf-8 -*-
"""「群里没被点名就不跑 ComfyUI 生图/生视频」闸门测试（2026-10-08 主人要求）。

主人原话：「群里面 bot 不被@就不要进行 ComfyUI 的生图或者生视频功能」

做法：驱动**真实的 `_handle_message`**（不是只测单个函数），把三条出口
（`draw_submit` / `handle_i2i` / `handle_random_draw` / `handle_video_gen`）
换成记录桩，断言：
  ① 群里没@  → 四个出口一个都不能进，也不许回话（只写日志）
  ② 群里 @我 / 叫名字 → 照跑
  ③ 私聊    → 照跑（私聊天然算点名）
  ④ 关掉 /admin「群里要@」→ 回到老行为，群里没@ 也跑
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import bot   # noqa: E402

bad = 0


def chk(name, ok, why=""):
    global bad
    if not ok:
        bad += 1
    print(("   [OK] " if ok else "   [失败] ") + name + ("" if ok else " | %s" % (why,)))


# ---------------- 打桩：把「外面世界」全挡掉 ----------------
bot.save_config = lambda: True
bot.SELF_ID = 123450002
SENT = []
bot.send_group = lambda cid, text, **kw: SENT.append(text) or True
bot.send_reply = lambda *a, **kw: True
bot.api = lambda action, **kw: {"data": {}, "status": "ok", "retcode": 0}
bot.maybe_slang_extract = lambda gid: None
bot.remember = lambda *a, **kw: None
bot.sim_allowed = lambda *a, **kw: False   # 2026-10-19: 多了 uid 参数，桩收 *a/**kw
# 关键词规则会抢在画图口令之前把消息吃掉（配置里真有「画」类规则的话，
# 这里就测不到闸门了）—— 本条链只关心出口，先把关键词清空。
_KEYWORDS_BACKUP = bot.CFG.get("keywords")
bot.CFG["keywords"] = []
bot.CFG.setdefault("debug", {})["dump_events"] = False
bot.CFG.setdefault("admin", {})["silent"] = False      # 静默模式会吞掉回话，测不准

_IMGS = []
bot._scan_media = lambda msg: (list(_IMGS), 0)
# ⚠️ 视频那条会「回捞这人前几分钟发的图」→ 真去下载，测试会卡几十秒还依赖外网。
#    闸门只关心"进不进 handle_video_gen"，这两步直接短路。
bot._vg_recall_assets = lambda gid, uid: ([], None)
bot._vg_localize_images = lambda imgs: list(imgs or [])

HITS = []
bot.draw_submit = lambda *a, **kw: (HITS.append(("现画", a[1] if len(a) > 1 else "")), (True, ""))[1]
bot.handle_i2i = lambda gid, extra="", privileged=False, imgs=None, qmid=0, style=None: \
    HITS.append(("画同款", extra))
# ⚠️ 真实调用会给 arg= 等关键字参数，桩一律收 `*a, **kw`（2026-10-16 修：bot 侧加了
#    `arg=parse_random_draw_arg(text)`，老桩按位置参数写 ⇒ TypeError，整套测试跑不下去）
bot.handle_random_draw = lambda *a, **kw: HITS.append(("随机生图", ""))
bot.handle_video_gen = lambda gid, desc, privileged=False, who="", mid=0, images=None, \
    ref_video=None: HITS.append(("视频生成", desc))

_MID = [800000]


def run(text, group=True, at=False, uid=111, imgs=None, style=None):
    """走一遍真实的 _handle_message，返回本次命中的出口列表。"""
    HITS.clear()
    SENT.clear()
    _IMGS[:] = list(imgs or [])
    _MID[0] += 1
    segs = []
    if at:
        segs.append({"type": "at", "data": {"qq": str(bot.SELF_ID), "name": "小鲸"}})
    segs.append({"type": "text", "data": {"text": text}})
    data = {"post_type": "message",
            "message_type": "group" if group else "private",
            "group_id": 123450001, "user_id": uid,
            "sender": {"role": "member", "nickname": "阿甲"},
            "message_id": _MID[0], "message": segs}
    gid = 123450001 if group else bot.priv_key(uid)
    bot._handle_message(data, gid, is_group=group)
    return list(HITS)


_img = [{"url": "http://example.com/a.jpg", "file": "a.jpg"}]
_vg_desc = "一只白猫在雨中撑伞走过小巷整条街"


# ---------------- 0) 闸门本体 ----------------
chk("旋钮「群里要@」已注册", bool(bot.knob_find("群里要@")), bot.knob_find("群里要@"))
chk("默认是开（要@）", bot._comfy_at_required() is True)
chk("_comfy_at_ok：私聊放行", bot._comfy_at_ok(-123, False, False, "t") is True)
chk("_comfy_at_ok：群里@了放行", bot._comfy_at_ok(123, True, True, "t") is True)
chk("_comfy_at_ok：群里没@拦住", bot._comfy_at_ok(123, True, False, "t") is False)


# ---------------- 1) 群里没@：四条出口全不进 ----------------
for _name, _text, _imgs in (
        ("现画", "画一只猫", None),
        ("画同款", "画同款 举着伞站在雨里", _img),
        ("照着重画（画 单字）", "画", _img),
        ("随机生图", "随机画一张", None),
        ("视频生成", "生成视频 " + _vg_desc, None)):
    h = run(_text, group=True, at=False, imgs=_imgs)
    chk("群里没@ → %s 不跑（%r）" % (_name, _text[:14]), not h, h)
    chk("群里没@ → %s 不回话" % _name, not SENT, SENT)

# 私聊是一对一会话，_handle_message 里本来就有一段"私聊不算没@我"的逻辑，别被它带偏
chk("群里没@ 的纯聊天照旧不受影响（不命中任何出口）", not run("今天天气不错", group=True, at=False))


# ---------------- 2) 群里 @我：照跑 ----------------
h = run("画一只猫", group=True, at=True)
chk("群里 @我 → 现画跑", h and h[0][0] == "现画" and h[0][1] == "猫", h)

h = run("画同款 举着伞站在雨里", group=True, at=True, imgs=_img)
chk("群里 @我 → 画同款跑", h and h[0][0] == "画同款", h)

h = run("随机画一张", group=True, at=True)
chk("群里 @我 → 随机生图跑", h and h[0][0] == "随机生图", h)

h = run("生成视频 " + _vg_desc, group=True, at=True)
chk("群里 @我 → 视频生成跑", h and h[0][0] == "视频生成", h)


# ---------------- 3) 群里叫名字：也算点名（口径与全项目一致） ----------------
_names = bot.bot_names()
if not _names:
    _names = ["小鲸"]
    bot.CFG.setdefault("ai", {})["names"] = _names
h = run("%s 画一只猫" % _names[0], group=True, at=False)
chk("群里 叫名字+画（没@）→ 现画跑（名字算点名）", h and h[0][0] == "现画", h)


# ---------------- 4) 私聊：照跑 ----------------
h = run("画一只猫", group=False, at=False)
chk("私聊 → 现画跑", h and h[0][0] == "现画", h)

h = run("生成视频 " + _vg_desc, group=False, at=False)
chk("私聊 → 视频生成跑", h and h[0][0] == "视频生成", h)


# ---------------- 5) 关掉开关：回到老行为 ----------------
bot.CFG["comfyui"]["require_addressing"] = False
chk("关掉后 _comfy_at_required 为假", bot._comfy_at_required() is False)
h = run("画一只猫", group=True, at=False)
chk("关掉后 群里没@ 也跑现画", h and h[0][0] == "现画", h)
h = run("生成视频 " + _vg_desc, group=True, at=False)
chk("关掉后 群里没@ 也跑视频生成", h and h[0][0] == "视频生成", h)
bot.CFG["comfyui"]["require_addressing"] = True
chk("开回来后 群里没@ 又被拦住", not run("画一只猫", group=True, at=False))


# ---------------- 收尾 ----------------
if _KEYWORDS_BACKUP is not None:
    bot.CFG["keywords"] = _KEYWORDS_BACKUP

print("RESULT:", "PASS" if bad == 0 else "FAIL")
sys.exit(1 if bad else 0)
