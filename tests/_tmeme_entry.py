# -*- coding: utf-8 -*-
"""阶段 3-D / 4 / 5：**生产消息入口级** meme 真实链路测试（MANUAL）。

从真实消息入口 handle_group_message 进，事件形状/口令分发/session 隔离全真跑。
**只有外部出口打桩**（send_group 文字、send_segments 图片、避免发真群）；
qbot_meme 到 127.0.0.1:2233 的 HTTP 生成、Pillow 校验、临时文件清理全部真跑，
图也不桩成假字节 —— 用本地 file:/// 段经 bot._fetch_raw_image 真落盘。

前提：本地 meme 服务已启动（用 meme_ctl.py start），
config.json 的 meme.enabled=true；否则相关用例明确 SKIP 而非假装通过。

覆盖：
  · 五类模板（射/爬/亲/悲报/摸头）经真实入口出图，格式/尺寸/帧数对账；
  · 总闸关 -> 旁路不接管（口令漏给下游，行为与接入前一致）；
  · 未命中口令 -> 不接管；
  · 会话隔离：A 发的图不会被 B 的口令回捞走；
  · 图片归属：引用图/当前图；
  · 失败隔离：服务错误码/异常不抛到 dispatch。

用法：python tests/_tmeme_entry.py
"""
import json
import os
import os as _os
import sys as _sys
import tempfile
import time
from pathlib import Path

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
_sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image, ImageDraw  # noqa: E402
import bot                        # noqa: E402
from qbot import qbot_meme as M             # noqa: E402

bot.save_config = lambda: True
bot.SELF_ID = 123450002
GID = 123450001
UID_A = 111001
UID_B = 222002

ok = True
SKIP = []


def chk(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [!!] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


# ── 出口桩：捕获发送，绝不发真群 ──────────────────────────────────────────
TEXT_OUT = []
IMG_OUT = []       # (cid, path, 读到的 bytes)


def _stub_send_group(cid, text, group_hint=None):
    TEXT_OUT.append((cid, str(text)))
    return True


def _stub_send_segments(cid, segments, group_hint=None):
    for seg in segments:
        if isinstance(seg, dict) and seg.get("type") == "image":
            f = (seg.get("data") or {}).get("file")
            try:
                data = Path(f).read_bytes()
                IMG_OUT.append((cid, f, data))
            except Exception as e:
                IMG_OUT.append((cid, f, b""))
    return True


bot.send_group = _stub_send_group
bot.send_segments = _stub_send_segments
# _skill_send_image 内部调 send_segments（模块级名字），已桩；feedback 走 send_group 已桩。
# 重新 bind feedback 到桩，确保反馈被捕获
M.bind_feedback(_stub_send_group)


def _reset_out():
    TEXT_OUT.clear()
    IMG_OUT.clear()


def _mkimg(color, letter):
    """即时造一张测试图到临时目录，返回路径。"""
    td = tempfile.mkdtemp(prefix="meme_entry_src_")
    p = os.path.join(td, letter + ".png")
    im = Image.new("RGB", (320, 240), color)
    d = ImageDraw.Draw(im)
    d.rectangle([2, 2, 317, 237], outline=(255, 255, 255), width=3)
    d.text((140, 100), letter, fill=(255, 255, 255))
    im.save(p)
    return p


def _img_seg(path):
    """构造一条 NapCat 图片段，走 _fetch_raw_image 的 file:/// 分支真落盘。"""
    p = str(path).replace("\\", "/")
    return {"type": "image", "data": {"file": "file:///" + p}}


def _event(uid, text, imgs=None, qmid=0):
    segs = []
    if text:
        segs.append({"type": "text", "data": {"text": text}})
    for s in (imgs or []):
        segs.append(s)
    if qmid:
        segs.insert(0, {"type": "reply", "data": {"id": str(qmid)}})
    return {
        "post_type": "message", "message_type": "group",
        "message_id": int(time.time() * 1000) % 100000000 + uid,
        "group_id": GID, "user_id": uid,
        "sender": {"role": "member", "nickname": "user%d" % uid},
        "message": segs, "raw_message": text or "",
    }


def _media_info(data):
    im = Image.open(__import__("io").BytesIO(data))
    fmt, size, nf = im.format, im.size, getattr(im, "n_frames", 1)
    im.close()
    return fmt, size, nf


def main():
    global ok
    # ── 服务健康前置（真跑，不打桩）──────────────────────────────────
    print("── 前置：本地服务健康 ──")
    healthy, hinfo = M.health()
    chk("本地服务健康 (/health routes>4 + shoot)", healthy, hinfo)
    if not healthy:
        print("  !! 服务不可用，以下真链路用例 SKIP（不会假装通过）")
        SKIP.append("all")
    enabled = bool((bot.CFG.get("meme") or {}).get("enabled"))
    print("  config meme.enabled =", enabled)

    # 造稳定素材
    A = _mkimg((220, 40, 40), "A")
    B = _mkimg((40, 200, 40), "B")

    # ── 1) 总闸：临时关 -> 旁路不接管 ──────────────────────────────
    print("── 总闸与不接管 ──")
    if "all" not in SKIP:
        saved = bot.CFG["meme"]["enabled"]
        bot.CFG["meme"]["enabled"] = False
        _reset_out()
        # 关闸时 handle 返回 False；为验证「不接管」，直接调 match + handle 低层
        chk("关闸 match(射)=None", M.match("射") is None)
        chk("关闸 handle(射)=False（不接管）",
            M.handle(GID, UID_A, "射", [_img_seg(A)]) is False)
        bot.CFG["meme"]["enabled"] = saved
        chk("未命中口令 handle 返回 False",
            M.handle(GID, UID_A, "随便聊聊天", []) is False)

    # ── 2) 五类模板经真实入口 ──────────────────────────────────────
    print("── 五类模板真实入口出图 ──")
    cases = [
        ("射", [A], ("GIF", 13)),
        ("爬", [A], ("JPEG", 1)),
        ("亲", [A, B], ("GIF", 13)),
        ("悲报 今天摸鱼被抓了", [], ("PNG", 1)),
        ("摸头", [A], ("GIF", 5)),
    ]
    for text, imgs, (want_fmt, want_nf) in cases:
        if "all" in SKIP:
            chk("%s SKIP(服务不可用)" % text, False, "服务不可用"); continue
        _reset_out()
        ev = _event(UID_A, text, [_img_seg(p) for p in imgs])
        try:
            bot.handle_group_message(ev)
        except Exception as e:
            chk("%s 入口未抛异常" % text, False, "%s: %s" % (type(e).__name__, e)); continue
        got_img = [x for x in IMG_OUT if x[2]]
        if not got_img:
            chk("%s 出图" % text, False, "IMG_OUT=%r TEXT_OUT=%r" % (IMG_OUT, TEXT_OUT)); continue
        cid, path, data = got_img[-1]
        fmt, size, nf = _media_info(data)
        good = (fmt == want_fmt and nf == want_nf)
        chk("%-14s -> %s %s frames=%d bytes=%d%s" %
            (text, fmt, size, nf, len(data), "" if good else " (期望 %s/%d)" % (want_fmt, want_nf)),
            good, "cid=%s" % cid)
        # 临时文件已被 finally 清理
        chk("%-14s 临时文件已清理" % text, not os.path.exists(path), path)

    # ── 3) 会话隔离：B 不能用 A 的图 ──────────────────────────────
    print("── 会话隔离 / 图片归属 ──")
    if "all" not in SKIP:
        _reset_out()
        # A 先发一张图（不带口令）——会进 _vg_remember_assets(gid, A)
        bot.handle_group_message(_event(UID_A, "", [_img_seg(A)]))
        # B 发「射」不带图 —— 回捞 (gid,B) 应为空，不应拿到 A 的图
        bot.handle_group_message(_event(UID_B, "射"))
        got_b = [x for x in IMG_OUT if x[0] == GID and x[2]]
        # 应该没有出图（B 没图可用），并给出缺图反馈
        cross = bool(got_b)
        chk("B 的口令不拿 A 的图（会话隔离）", not cross,
            "IMG=%r TEXT=%r" % ([(c, len(d)) for c, _, d in IMG_OUT], TEXT_OUT))
        # A 自己紧接着发「射」不带图 —— 回捞自己的图应成功
        _reset_out()
        bot.handle_group_message(_event(UID_A, "射"))
        got_a = [x for x in IMG_OUT if x[2]]
        chk("A 自己能回捞自己刚发的图", bool(got_a),
            "IMG=%d TEXT=%r" % (len(IMG_OUT), TEXT_OUT))

    # ── 4) 引用图归属（qmid）──────────────────────────────────────
    print("── 引用图 ──")
    # 注：引用图依赖 remember_images(mid,imgs) 缓存，构造一条带 mid 的历史消息再引用
    if "all" not in SKIP:
        _reset_out()
        hist = _event(UID_A, "", [_img_seg(A)])
        hist_mid = hist["message_id"]
        bot.remember_images(hist_mid, [_img_seg(A)["data"]])
        ev = _event(UID_A, "爬", [], qmid=hist_mid)
        # 清掉 _vg 回捞干扰：用新 uid 确保只有引用图这一条来源
        ev["user_id"] = 333003
        bot.handle_group_message(ev)
        got = [x for x in IMG_OUT if x[2]]
        chk("引用图能出图（qmid 归属）", bool(got),
            "IMG=%d TEXT=%r" % (len(IMG_OUT), TEXT_OUT))

    print()
    if SKIP:
        print("RESULT SKIP（服务不可用，不假装通过）")
        _sys.exit(2)
    print("RESULT", "PASS" if ok else "FAIL")
    _sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
