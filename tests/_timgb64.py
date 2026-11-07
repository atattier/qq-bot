# -*- coding: utf-8 -*-
"""图片发送：本机路径 → base64 兜底 —— 离线自测（2026-10-23）。

背景：NapCat 跑在 Docker 容器里，容器看不见 `E:\\` 上的文件，直接给路径会被拒
（`文件处理失败: 识别URL失败`），文字照发、图全哑火。`bot._api_send` 现在会**自动重试一次
base64** 并记住结果。

这里把 `bot.api` 打桩成「第一次拒绝、第二次成功」，验证：
  · 第一次用路径、第二次用 base64://；
  · 成功后 `_NAPCAT_FS["b64"]` 记住，后续直接走 base64（不再白跑）；
  · 两次都失败 → 返回 False，且把 NapCat 的原始拒绝理由记下来（以前完全看不到）；
  · 纯文字消息不受影响；超大文件不转 base64（JSON 体会炸）。
**不联网、不发群**。

用法：python tests/_timgb64.py
"""
import os as _os
import sys as _sys
import tempfile
from pathlib import Path

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
_sys.stdout.reconfigure(encoding="utf-8")

import bot                      # noqa: E402
from PIL import Image           # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [!!] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


_tmp = Path(tempfile.mkdtemp(prefix="imgb64_test_"))
_img = _tmp / "shot.png"
Image.new("RGB", (240, 160), (30, 60, 120)).save(_img)

GID = 123450001
_real_api = bot.api
CALLS = []

# 🔴 2026-10-30：本用例专测发送层本身，必须明说「这次别再静音」。
#    `_api_send` 现在对 `QBOT_TEST` 进程一律静音（主人要求「测试的发言不要发群里」），
#    不打开这个逃生舱就永远走不到 base64 重试，所有断言都会假失败。
#    安全前提：本文件把 `bot.api` 全程打成桩，放行也发不到真群。
bot._MUTE_BYPASS["on"] = True


def _stub(seq):
    """seq 里放每次调用的返回值。"""
    CALLS.clear()

    def _f(action, **kw):
        CALLS.append({"action": action, "kw": kw})
        return seq[min(len(CALLS) - 1, len(seq) - 1)]
    bot.api = _f


def _segs():
    return [{"type": "text", "data": {"text": "看图"}},
            {"type": "image", "data": {"file": str(_img)}}]


print("== 第一次拒绝 → 自动 base64 重试 ==")
bot._NAPCAT_FS["b64"], bot._NAPCAT_FS["why"] = False, ""
_stub([{"status": "failed", "retcode": 200, "message": "文件处理失败: 识别URL失败"},
       {"status": "ok", "retcode": 0, "data": {"message_id": 1}}])
r = bot.send_segments(GID, _segs())
chk("最终返回成功", r is True, r)
chk("一共调了 2 次", len(CALLS) == 2, len(CALLS))
_f1 = (CALLS[0]["kw"]["message"][1]["data"]["file"])
_f2 = (CALLS[1]["kw"]["message"][1]["data"]["file"])
chk("第一次给的是本机路径", _f1 == str(_img), _f1[:40])
chk("第二次换成了 base64://", _f2.startswith("base64://"), _f2[:20])
chk("文字段没被动过", CALLS[1]["kw"]["message"][0]["data"]["text"] == "看图")
chk("记住了「这个 NapCat 要 base64」", bot._NAPCAT_FS["b64"] is True)

print("== 记住之后不再白跑一趟 ==")
_stub([{"status": "ok", "retcode": 0, "data": {"message_id": 2}}])
r = bot.send_segments(GID, _segs())
chk("只调 1 次就成功", r is True and len(CALLS) == 1, len(CALLS))
chk("直接就是 base64", CALLS[0]["kw"]["message"][1]["data"]["file"].startswith("base64://"))

print("== 两次都失败：报 False 并留下原始理由 ==")
bot._NAPCAT_FS["b64"], bot._NAPCAT_FS["why"] = False, ""
_stub([{"status": "failed", "retcode": 200, "message": "文件处理失败: 识别URL失败"}])
r = bot.send_segments(GID, _segs())
chk("返回失败", r is False, r)
chk("原始拒绝理由被记下来", "识别URL失败" in (bot._NAPCAT_FS["why"] or ""), bot._NAPCAT_FS["why"])

print("== 纯文字 / 大文件 ==")
bot._NAPCAT_FS["b64"], bot._NAPCAT_FS["why"] = False, ""
_stub([{"status": "failed", "retcode": 200, "message": "x"},
       {"status": "failed", "retcode": 200, "message": "x"}])
r = bot.send_segments(GID, [{"type": "text", "data": {"text": "只有文字"}}])
chk("纯文字失败不触发 base64 重试（只调 1 次）", r is False and len(CALLS) == 1, len(CALLS))

_big = _tmp / "big.png"
with open(_big, "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n" + b"\0" * (9 * 1024 * 1024))
bot._NAPCAT_FS["b64"], bot._NAPCAT_FS["why"] = False, ""
_stub([{"status": "failed", "retcode": 200, "message": "x"}])
r = bot.send_segments(GID, [{"type": "image", "data": {"file": str(_big)}}])
chk("超 8MB 的**图**不转 base64（只调 1 次）", r is False and len(CALLS) == 1, len(CALLS))

print("== 视频段（2026-10-24：video 以前压根不在转换列表里，扒视频整体是哑的）==")
# ⚠️ 先把熔断/限速记账清干净：上面那组「两次都失败」故意连失败好几次，
#    会把熔断器打开 180 秒 ⇒ 后面视频段的发送**根本没发出去**（不是代码坏）。
#    这属于测试自己的脏状态，谁弄脏谁收拾（2026-10-30 加）。
bot._send_times.clear()
bot._circuit.update({"fails": 0, "open_until": 0.0})
_vid = _tmp / "clip.mp4"
with open(_vid, "wb") as f:
    f.write(b"\0" * (8 * 1024 * 1024 + 400 * 1024))     # 8.4MB：超过图片上限，但视频该放行
chk("视频上限比图片宽", bot._B64_CAP["video"] > bot._B64_CAP["image"],
    "%s vs %s" % (bot._B64_CAP["video"], bot._B64_CAP["image"]))
bot._NAPCAT_FS["b64"], bot._NAPCAT_FS["why"] = True, ""
_stub([{"status": "ok", "retcode": 0, "data": {"message_id": 4}}])
r = bot.send_segments(GID, [{"type": "video", "data": {"file": str(_vid)}}])
_f = CALLS[0]["kw"]["message"][0]["data"]["file"] if CALLS else ""
chk("8.4MB 视频照样转 base64", r is True and _f.startswith("base64://"), _f[:20])

bot._NAPCAT_FS["b64"], bot._NAPCAT_FS["why"] = False, ""
_stub([{"status": "failed", "retcode": 200, "message": "识别URL失败"},
       {"status": "ok", "retcode": 0, "data": {"message_id": 5}}])
r = bot.send_segments(GID, [{"type": "video", "data": {"file": str(_vid)}}])
chk("视频也会「路径失败→自动 base64 重试」", r is True and len(CALLS) == 2, len(CALLS))
chk("重试那次是 base64", CALLS[1]["kw"]["message"][0]["data"]["file"].startswith("base64://"))

bot.api = _real_api
bot._NAPCAT_FS["b64"], bot._NAPCAT_FS["why"] = False, ""

print()
print("==== ALL PASS ====" if ok else "==== 有失败 ====")
_sys.exit(0 if ok else 1)
