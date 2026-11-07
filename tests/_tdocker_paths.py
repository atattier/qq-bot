# -*- coding: utf-8 -*-
"""Docker 环境残留假设 —— 离线自测（2026-10-25）。

背景：主人提醒「我现在用的 docker，可能有些老旧残余代码会出问题」。
NapCat 从「Windows 注入 QQ.exe」搬到 **Docker 容器** 之后，一批老代码仍然假设
「NapCat 给的文件路径在宿主机上读得到」——那在容器模式下**恒不成立**：
  · `get_image` 回的是**容器内路径**（`/app/napcat/...`），宿主机 `Path(lp).is_file()` 恒 False
  · `base64://` 段（bot 自己注入/转发时用的格式）以前**完全没处理**，被当成路径
  · `file:///` 段同理
  · `_vg_fetch_video` 原来**先**问容器 `get_file`，白等一次才落到 URL 分支

这里验证 `_fetch_rev_image` 的四条降级分支（全打桩，不联网）：
  A. 段里带 `base64://` → 本地解出来直接用（★新增）
  B. 段里带 `file:///` 且文件存在 → 直接复制（★新增）
  C. 段里是普通文件名 + `get_image` 回容器内路径 → **判为失败并写明原因**，不许假装成功
  D. 段里有 http 直链 → 下载成功（走的是最常见路径）
  E. `_vg_fetch_video`：URL 优先于 `get_file`（★顺序调换）
  F. 失败原因里要能看出「容器内路径」这件事，别再是笼统的一句话

**不联网、不碰真 config.json**：`_fetch_rev_image` 里的 urlopen / api 全打桩。

用法：python tests/_tdocker_paths.py
"""
import base64
import json
import os as _os
import sys as _sys
import tempfile
from pathlib import Path

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
_sys.stdout.reconfigure(encoding="utf-8")

import bot                     # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [!!] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


_tmp = Path(tempfile.mkdtemp(prefix="dkpaths_test_"))
# 弄一张真 PNG（4x4 纯色，PIL 不一定装了就用最小合法 PNG 头）
_png = (_tmp / "src.png")
try:
    from PIL import Image
    Image.new("RGB", (8, 8), (20, 40, 80)).save(_png)
except Exception:  # noqa: BLE001
    _png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
_blob = _png.read_bytes()

# 把输出目录挪到临时区，别污染真 state/reverse
_orig_rev = bot.REV_DIR
bot.REV_DIR = _tmp / "reverse"
bot.REV_DIR.mkdir(parents=True, exist_ok=True)

# 🔴 2026-10-25 血案：`_vg_fetch_video()` 的下落目录是**写死的** `BASE/state/videos`，
#    测试桩把同一个 8x8 PNG 当成视频流回放，`while True: read(64K)` 就**无限循环**写，
#    一次测试造出一个 **2.4 GB** 的假 mp4；连跑几次把 C 盘写满（只剩 2.4G），
#    连带 `No space left on device` 让后面的用例全挂。
#    必须把 BASE 也指向临时目录，并在 finally 里清干净。
_orig_base = bot.BASE
bot.BASE = _tmp


def _clean():
    for d in (bot.REV_DIR, bot.BASE / "state" / "videos"):
        if not d.is_dir():
            continue
        for p in d.glob("*"):
            try:
                p.unlink()
            except OSError:
                pass


_REAL_API = bot.api
_REAL_URLOPEN = bot.urllib.request.urlopen


class _Resp:
    """模拟 http 响应。

    ⚠️ 必须支持「读到头就返回 b""」——`_vg_fetch_video` 是
    `while True: chunk = r.read(64K); if not chunk: break` 的写法。
    原来的桩无论读多少次都吐同一份 blob，循环永远不结束
    ⇒ 一次测试写出 2.4GB 假 mp4，把 C 盘写满（2026-10-25 血案）。
    """

    def __init__(self, blob):
        self._b = blob or b""
        self._done = False

    def read(self, n=-1):
        if self._done or not self._b:
            return b""
        self._done = True          # 一次性：下次再读就是 EOF
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_net(blob=None, fail=False):
    def _f(req, timeout=None, *a, **kw):
        if fail:
            raise OSError("net down")
        return _Resp(blob if blob is not None else _blob)
    bot.urllib.request.urlopen = _f


def _stub_api(ret):
    bot.api = lambda action, **kw: ret


# ---------------------------------------------------------------- A. base64:// 段
print("== A. 段里带 base64:// （以前完全没处理）==")
_clean()
_stub_net()
_stub_api({"status": "failed", "retcode": 200, "message": "file not found"})
p = bot._fetch_rev_image({"file": "base64://" + base64.b64encode(_blob).decode(), "url": ""})
chk("能取到本地文件", bool(p) and Path(p).is_file(), p)
chk("内容非空", bool(p) and Path(p).stat().st_size > 0,
    Path(p).stat().st_size if p else 0)

# ---------------------------------------------------------------- B. file:/// 段
print("== B. 段里带 file:/// （路径真实存在）==")
_clean()
p2 = bot._fetch_rev_image({"file": "file:///" + str(_png).replace("\\", "/"), "url": ""})
chk("能取到本地文件", bool(p2) and Path(p2).is_file(), p2)
_clean()
p2b = bot._fetch_rev_image({"file": "file:///" + str(_tmp / "nope.png").replace("\\", "/"), "url": ""})
chk("路径不存在时不假装成功", p2b is None, p2b)

# ---------------------------------------------------------------- D. http 直链
print("== D. http 直链 ==")
_clean()
_stub_net(blob=_blob)
p4 = bot._fetch_rev_image({"file": "abc.jpg", "url": "https://example.com/x.jpg"})
chk("直链下载成功", bool(p4) and Path(p4).is_file(), p4)

# ---------------------------------------------------------------- C. 容器内路径
print("== C. get_image 回容器内路径（Docker 的典型情形）==")
_clean()
_stub_api({"status": "ok", "retcode": 0,
           "data": {"file": "/app/napcat/cache/abc.image"}})
captured = []
_orig_warn = bot.log.warning
bot.log.warning = lambda *a, **kw: captured.append(a[0] % a[1:] if len(a) > 1 else str(a[0]))
try:
    p3 = bot._fetch_rev_image({"file": "abc.image", "url": ""})
finally:
    bot.log.warning = _orig_warn
chk("容器内路径判为失败（不许假装成功）", p3 is None, p3)
_why = " ".join(captured)
chk("失败原因写明了「宿主机上不存在/容器内路径」",
    "宿主机" in _why or "容器" in _why, _why[:120])
chk("失败原因里带着那个容器路径（便于排查）",
    "/app/napcat" in _why, _why[:120])

print("== C2. get_image 同时回了 base64（NapCat 某些版本会这样）==")
_clean()
_stub_api({"status": "ok", "retcode": 0,
           "data": {"file": "/app/napcat/cache/abc.image",
                    "base64": "base64://" + base64.b64encode(_blob).decode()}})
p5 = bot._fetch_rev_image({"file": "abc.image", "url": ""})
chk("优先用 base64 落地", bool(p5) and Path(p5).is_file(), p5)

print("== C3. get_image 回 url（可选字段）==")
_clean()
_stub_net(blob=_blob)
_stub_api({"status": "ok", "retcode": 0,
           "data": {"file": "/app/napcat/cache/abc.image",
                    "url": "https://example.com/real.jpg"}})
p6 = bot._fetch_rev_image({"file": "abc.image", "url": ""})
chk("能用 get_image 回的 url 下载", bool(p6) and Path(p6).is_file(), p6)

# ---------------------------------------------------------------- E. 视频顺序
print("== E. _vg_fetch_video：URL 优先于 get_file ==")
_clean()
CALLS = []


def _stub_api_track(ret):
    def _f(action, **kw):
        CALLS.append(action)
        return ret
    bot.api = _f


def _stub_net_track(blob=None):
    def _f(req, timeout=None, *a, **kw):
        CALLS.append("urlopen")
        return _Resp(blob if blob is not None else _blob)
    bot.urllib.request.urlopen = _f


_stub_api_track({"status": "ok", "retcode": 0,
                 "data": {"file": "/app/napcat/cache/v.mp4"}})
_stub_net_track()
v = bot._vg_fetch_video({"file_id": "fid1", "url": "https://example.com/v.mp4"})
chk("有 URL 时直接下，不先问 get_file", CALLS and CALLS[0] == "urlopen", CALLS[:3])
chk("拿到文件", bool(v) and Path(v).is_file(), v)

print("== E2. 没有 URL 才问 get_file ==")
CALLS.clear()
_stub_api_track({"status": "ok", "retcode": 0,
                 "data": {"file": "/app/napcat/cache/v.mp4"}})
v2 = bot._vg_fetch_video({"file_id": "fid1", "url": ""})
chk("无 URL 时调了 get_file", "get_file" in CALLS, CALLS[:3])
chk("容器路径读不到就返回 None（别返回假路径）", v2 is None, v2)

print("== E3. URL 挂了才回落 get_file ==")
CALLS.clear()
_stub_api_track({"status": "ok", "retcode": 0, "data": {"file": str(_png)}})


def _net_down(req, timeout=None, *a, **kw):
    raise OSError("down")


bot.urllib.request.urlopen = _net_down
v3 = bot._vg_fetch_video({"file_id": "fid1", "url": "https://example.com/v.mp4"})
chk("URL 失败后回落到 get_file", "get_file" in CALLS, CALLS[:3])
chk("get_file 给的宿主机真实路径可用", v3 == str(_png), v3)

# ---------------------------------------------------------------- 收尾
bot.api = _REAL_API
bot.urllib.request.urlopen = _REAL_URLOPEN
_clean()
bot.REV_DIR = _orig_rev
bot.BASE = _orig_base
# 把整个临时目录删掉（里面有假视频，不让它留到下次）
try:
    import shutil as _sh
    _sh.rmtree(_tmp, ignore_errors=True)
except Exception:  # noqa: BLE001
    pass

print()
print("==== ALL PASS ====" if ok else "==== 有失败 ====")
_sys.exit(0 if ok else 1)
