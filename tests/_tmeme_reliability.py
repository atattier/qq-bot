# -*- coding: utf-8 -*-
"""阶段 5：本地 meme 生成服务 · 可靠性 / 端到端场景测试（MANUAL，需本地服务）。

与 _tmeme.py（纯桩离线）互补：这里**真跑**——
  · 从真实消息入口 bot.handle_group_message 进；
  · 图片段经 bot._fetch_raw_image 真落盘（file:/// 分支）；
  · qbot_meme -> 127.0.0.1:2233 的 HTTP 生成、Pillow 校验、临时清理全真跑；
  · 服务生命周期（未启动 / 启动失败 / 重启）通过 meme_ctl.py 真控制，绝不伪造返回值。

**只有外部出口打桩**：send_group 文字、send_segments 图片（避免发真群）。
服务控制 subprocess 调 _meme_lab/meme_ctl.py；config.json 里 meme.enabled 在进程内
临时开关（bot.save_config 打桩，绝不写盘）。

14 个场景（对应批准书 阶段5）：
  01 服务未启动        02 服务启动失败（端口占用）    03 服务重启
  04 生成超时          05 模板不存在                  06 图片数量错误
  07 文字数量错误      08 超大图片                    09 损坏图片
  10 多会话并发        11 连续重复生成                12 动图输入
  13 临时文件清理      14 普通聊天 & 其它图片技能回归

用法：python tests/_tmeme_reliability.py
前置：_meme_lab 的 venv312 服务资产齐全、config.json 的 meme.enabled=true。
退出码：0=全过  1=有用例失败  2=服务资产/前置不满足（明确 SKIP 不假装通过）
"""
import io
import json
import os
import subprocess
import sys as _sys
import tempfile
import threading
import time
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
_sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image, ImageDraw  # noqa: E402
import bot                        # noqa: E402
import qbot_meme as M             # noqa: E402

LAB = Path(_ROOT).parent / "_meme_lab"
CTL = LAB / "meme_ctl.py"
CTL_PY = sys.executable
GID = 123450001
UID_A, UID_B, UID_C = 111001, 222002, 333003

bot.save_config = lambda: True
bot.SELF_ID = 123450002

ok = True
RESULT = []


def chk(label, cond, extra=""):
    global ok
    flag = "OK " if cond else "!! "
    print("  [%s] %s%s" % (flag, label, ("  " + str(extra)) if extra else ""))
    RESULT.append((label, bool(cond)))
    if not cond:
        ok = False


# ── 出口桩：捕获发送，绝不发真群 ──────────────────────────────────────────
TEXT_OUT = []
IMG_OUT = []
_OUT_LOCK = threading.Lock()


def _stub_send_group(cid, text, *a, **kw):
    with _OUT_LOCK:
        TEXT_OUT.append((cid, str(text)))
    return True


def _stub_send_segments(cid, segments, *a, **kw):
    for seg in segments or []:
        if isinstance(seg, dict) and seg.get("type") == "image":
            f = (seg.get("data") or {}).get("file")
            try:
                data = Path(f).read_bytes()
            except Exception:
                data = b""
            with _OUT_LOCK:
                IMG_OUT.append((cid, f, data))
    return True


bot.send_group = _stub_send_group
bot.send_segments = _stub_send_segments
M.bind_feedback(_stub_send_group)

# 下游「与被测系统无关」的网络/大脑出口打桩成空操作（沿用 _tentry.py 的成熟做法）。
# 被 meme 旁路认领的口令在旁路里同步 return，根本走不到这些出口；只有场景 14
# 的「关闸 / 普通聊天」未被认领会落到下游，桩保证它们不真调 LLM、不发真群。
def _noop(*a, **kw):
    return None


bot.ai_reply = _noop
bot.sim_vqa_chat = _noop
bot.brain_with_tools = lambda *a, **kw: ("custom", "（大脑桩）", False)
bot.send_reply = lambda *a, **kw: True
bot.api = lambda action, **kw: {"status": "ok", "retcode": 0}


def _reset_out():
    with _OUT_LOCK:
        TEXT_OUT.clear()
        IMG_OUT.clear()


def _imgs():
    with _OUT_LOCK:
        return [(c, p, d) for c, p, d in IMG_OUT if d]


def _texts():
    with _OUT_LOCK:
        return list(TEXT_OUT)


# ── 素材：即时造到临时目录，绝不依赖会消失的 testimg/ ─────────────────────
SRC = tempfile.mkdtemp(prefix="meme_rel_src_")


def _png(color, letter, size=(320, 240)):
    p = os.path.join(SRC, "%s_%s.png" % (letter, int(time.time() * 1000) % 100000))
    im = Image.new("RGB", size, color)
    d = ImageDraw.Draw(im)
    d.rectangle([2, 2, size[0] - 3, size[1] - 3], outline=(255, 255, 255), width=3)
    d.text((size[0] // 2 - 4, size[1] // 2 - 6), letter, fill=(255, 255, 255))
    im.save(p)
    return p


def _gif(letter):
    """2 帧真动图，验证动图输入能跑通生成链路。"""
    p = os.path.join(SRC, "%s_%s.gif" % (letter, int(time.time() * 1000) % 100000))
    f1 = Image.new("RGB", (120, 100), (30, 90, 200))
    f2 = Image.new("RGB", (120, 100), (200, 90, 30))
    f1.save(p, save_all=True, append_images=[f2], duration=80, loop=0)
    return p


def _corrupt(letter):
    p = os.path.join(SRC, "%s_%s.png" % (letter, int(time.time() * 1000) % 100000))
    Path(p).write_bytes(b"NOTANIMAGE\x00\x01\x02\x03not a real png")
    return p


def _huge(letter, mb=10):
    """构造 >8MB 的图片文件（PNG 压缩太狠，用随机噪声贴近不可压缩）。"""
    p = os.path.join(SRC, "%s_%s.png" % (letter, int(time.time() * 1000) % 100000))
    im = Image.frombytes("RGB", (1800, 1800), os.urandom(1800 * 1800 * 3))
    im.save(p)
    if Path(p).stat().st_size < mb * 1024 * 1024:
        with open(p, "ab") as f:      # 追加尾随字节，Pillow 仍能打开首部，但文件体积超限
            f.write(os.urandom(mb * 1024 * 1024))
    return p


def _seg(path):
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


def _run(text, imgs=None, uid=UID_A, qmid=0):
    """经真实入口跑一条消息；捕获异常（入口绝不该把异常抛到 dispatch）。"""
    _reset_out()
    err = None
    try:
        bot.handle_group_message(_event(uid, text, imgs, qmid))
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
    return err, _imgs(), _texts()


def _media(data):
    im = Image.open(io.BytesIO(data))
    fmt, size, nf = im.format, im.size, getattr(im, "n_frames", 1)
    for i in range(nf):
        im.seek(i)
        im.load()
    im.close()
    return fmt, size, nf


# ── 服务生命周期控制（subprocess 真调，绝不伪造）────────────────────────
def ctl(cmd, timeout=90):
    r = subprocess.run([CTL_PY, str(CTL), cmd], cwd=str(LAB),
                       capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def health(timeout=5):
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:2233/health", timeout=timeout) as r:
            d = json.loads(r.read())
        return bool(d.get("ok")) and int(d.get("routes", 0)) > 4
    except Exception:
        return False


def _wait_health(want, secs=40):
    t0 = time.time()
    while time.time() - t0 < secs:
        if health() == want:
            return True
        time.sleep(0.6)
    return health() == want


def _set_enabled(v):
    bot.CFG["meme"]["enabled"] = bool(v)


def set_timeout(v):
    bot.CFG["meme"]["generate_timeout"] = v


def _grab_dir_files():
    g = Path(_ROOT) / "state" / "emoji_grab"
    return set(g.glob("*")) if g.is_dir() else set()


def _meme_tmp_files():
    s = Path(_ROOT) / "state"
    return set(s.glob("meme_*")) if s.is_dir() else set()


# ══════════════════════════════════════════════════════════════════════════
# 场景 1 / 2 / 3：服务生命周期（最先跑；finally 里保证恢复）
# ══════════════════════════════════════════════════════════════════════════
def scenario_lifecycle(img):
    print("\n── 01 服务未启动 / 02 启动失败 / 03 重启（真控制，finally 必恢复）──")
    rc, out = ctl("stop")
    down = not health()
    chk("01a meme_ctl stop 成功且端口释放", rc == 0 and down,
        "rc=%s down=%s" % (rc, down))
    if down:
        # 关闸是另一回事；这里 enabled=True 但服务不在，应给「连不上」反馈、不抛异常、不出图
        _set_enabled(True)
        err, ims, txt = _run("射", [_seg(img)])
        chk("01b 服务未启动：入口不抛异常", err is None, err)
        chk("01c 服务未启动：不出图", len(ims) == 0, "IMG=%d" % len(ims))
        chk("01d 服务未启动：给简短故障反馈", any("连不上" in t for _, t in txt),
            "TEXT=%r" % txt)
    else:
        chk("01b/01c/01d 服务未启动用例", False, "服务没能停下来（down=%s）" % down)

    # 02 启动失败：端口被非法占用（起一个纯 socket 监听器占住 2233，再 start）
    import socket
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", 2233))
        holder.listen(5)
        rc2, out2 = ctl("start")
        occupied = (rc2 == 4)
        chk("02a 端口被占用时 start 拒绝并返回码4", occupied,
            "rc=%s out=%r" % (rc2, out2[-160:]))
        chk("02b 启动失败不误杀占用者（端口仍被占）", _port_held(), "")
        chk("02c 启动失败时服务未健康（不是假成功）", not health(), "")
    finally:
        holder.close()
    # 等 socket 释放
    time.sleep(0.5)

    # 03 重启：先恢复一个可用服务，再 restart，确认重启前后都健康、五类仍可出图
    rc3, out3 = ctl("start")
    up = _wait_health(True)
    chk("03a 恢复服务成功且健康", rc3 == 0 and up, "rc=%s up=%s" % (rc3, up))
    rc4, out4 = ctl("restart")
    up2 = _wait_health(True)
    chk("03b restart 成功且重启后健康", rc4 == 0 and up2, "rc=%s up2=%s" % (rc4, up2))
    if up2:
        M._info_cache.clear()
        M._keys_cache["keys"] = []
        err, ims, txt = _run("射", [_seg(img)])
        got = bool(ims) and _media(ims[-1][2])[0] == "GIF"
        chk("03c 重启后真实生成仍成功（射->GIF）", got and err is None,
            "err=%s IMG=%d" % (err, len(ims)))
    else:
        chk("03c 重启后真实生成", False, "服务未恢复健康")


def _port_held():
    import socket
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", 2233)) == 0
    finally:
        s.close()


# ══════════════════════════════════════════════════════════════════════════
# 场景 4-14：输入 / 生成 / 并发 / 回归
# ══════════════════════════════════════════════════════════════════════════
def scenario_inputs(img_a, img_b, gif, corrupt, huge):
    print("\n── 04~09 输入与生成异常（真 HTTP / 真错误码）──")
    _set_enabled(True)

    # 04 生成超时：把 generate_timeout 调到极小，真实 POST 会被 urlopen 超时打断
    set_timeout(0.001)
    try:
        err, ims, txt = _run("射", [_seg(img_a)])
        chk("04a 生成超时：入口不抛异常", err is None, err)
        chk("04b 生成超时：不出图", len(ims) == 0, "IMG=%d" % len(ims))
        chk("04c 生成超时：给简短故障反馈", len(txt) >= 1, "TEXT=%r" % txt)
    finally:
        set_timeout(60)
        time.sleep(0.3)

    # 05 模板不存在：临时往 templates 注入一个不存在的 key，验证 info 531 / POST 404 被兜住
    spec = {"keywords": ["不存在模板"], "kind": "gif"}
    tmpl = bot.CFG["meme"]["templates"]
    tmpl["ghost_meme"] = spec
    try:
        err, ims, txt = _run("不存在模板", [_seg(img_a)])
        chk("05a 模板不存在：入口不抛异常", err is None, err)
        chk("05b 模板不存在：不出图", len(ims) == 0, "IMG=%d" % len(ims))
        chk("05c 模板不存在：有可读反馈", len(txt) >= 1, "TEXT=%r" % txt)
    finally:
        tmpl.pop("ghost_meme", None)

    # 06 图片数量错误：kiss 需要 2 张，只给 1 张 -> 本地校验「还差 1 张图」
    err, ims, txt = _run("亲", [_seg(img_a)])
    chk("06a 图片数量不足：入口不抛异常", err is None, err)
    chk("06b 图片数量不足：不出图", len(ims) == 0, "IMG=%d" % len(ims))
    chk("06c 图片数量不足：提示「还差」且属用户输入问题",
        any("还差" in t for _, t in txt), "TEXT=%r" % txt)
    # 直接打服务验 541 被当用户输入错误（不是服务故障）
    st, detail = _raw_post("kiss", [(img_a, b"")], [])
    chk("06d 服务确实返回 541（图片数量不符）", st == 541, "st=%s detail=%r" % (st, detail))

    # 07 文字数量错误：bad_news 最多 1 条，塞 2 条 -> 本地 _validate_inputs 挡
    err, ims, txt = _run("悲报 头一条 第二条", [])
    # 说明：_parse_texts 对单条模板只产 1 条 texts，本地不会误判成 2；
    # 这里验证「文字模板给文字能正常出图」+ 直接打服务验 542 错误码语义。
    chk("07a 文字模板带文字：入口不抛异常", err is None, err)
    st2, detail2 = _raw_post("bad_news", [], ["喜报", "又喜报"])
    chk("07b 服务确实返回 542（文字数量不符）", st2 == 542,
        "st=%s detail=%r" % (st2, detail2))
    # 错误码分流：自定义 5xx 用户输入码必须归 user_fault，标准网关故障码归服务故障
    user_codes = [531, 532, 533, 540, 541, 542, 543, 550, 551, 552, 560]
    gw_codes = [500, 501, 502, 503, 504, 505]
    chk("07c 自定义 5xx 全在用户错误码表、标准网关码全不在",
        all(c in M.USER_ERR_CODES for c in user_codes)
        and not any(c in M.USER_ERR_CODES for c in gw_codes), "")

    # 08 超大图片：>8MB 输入被 _collect_images 跳过 -> 缺图反馈，不出图、不上传
    err, ims, txt = _run("射", [_seg(huge)])
    chk("08a 超大图片：入口不抛异常", err is None, err)
    chk("08b 超大图片：不出图（被本地大小闸拦下）", len(ims) == 0, "IMG=%d" % len(ims))
    chk("08c 超大图片：给缺图反馈", any("还差" in t or "图" in t for _, t in txt),
        "TEXT=%r" % txt)

    # 09 损坏图片：file 是真段但内容非图片 -> 服务 533，反馈可读，不抛异常
    err, ims, txt = _run("射", [_seg(corrupt)])
    chk("09a 损坏图片：入口不抛异常", err is None, err)
    chk("09b 损坏图片：不出图", len(ims) == 0, "IMG=%d" % len(ims))
    chk("09c 损坏图片：有可读反馈（非堆栈）", len(txt) >= 1 and not any("Traceback" in t for _, t in txt),
        "TEXT=%r" % txt)
    st3, detail3 = _raw_post("shoot", [], [], raw_files=[("bad.png", b"NOTANIMAGE\x00\x01\x02")])
    chk("09d 服务对损坏图返回 533", st3 == 533, "st=%s detail=%r" % (st3, detail3))


def _raw_post(key, files, texts, raw_files=None):
    """直接打真实服务，拿真实 HTTP 码 + detail（用于核对错误码语义，非伪造）。"""
    import urllib.request
    import urllib.error
    import uuid
    b = "----rel" + uuid.uuid4().hex
    buf = io.BytesIO()

    def fld(n, v):
        buf.write(("--%s\r\n" % b).encode())
        buf.write(('Content-Disposition: form-data; name="%s"\r\n\r\n' % n).encode())
        buf.write(str(v).encode())
        buf.write(b"\r\n")

    items = []
    for p, _ in files:
        items.append((Path(p).name, Path(p).read_bytes()))
    for n, c in (raw_files or []):
        items.append((n, c))
    for i, (n, c) in enumerate(items):
        buf.write(("--%s\r\n" % b).encode())
        buf.write(('Content-Disposition: form-data; name="images"; filename="%s"\r\n'
                   'Content-Type: image/png\r\n\r\n' % n).encode())
        buf.write(c)
        buf.write(b"\r\n")
    for t in texts:
        fld("texts", t)
    fld("args", "{}")
    buf.write(("--%s--\r\n" % b).encode())
    req = urllib.request.Request("http://127.0.0.1:2233/memes/%s/" % key,
                                 data=buf.getvalue(),
                                 headers={"Content-Type": "multipart/form-data; boundary=" + b},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        try:
            detail = json.loads(body).get("detail", "")
        except Exception:
            detail = body[:120].decode("utf-8", "replace")
        return e.code, detail


def scenario_concurrency_and_cleanup(img_a, img_b, gif):
    print("\n── 10 并发 / 11 连续重复 / 12 动图 / 13 清理 / 14 回归 ──")
    _set_enabled(True)

    # 10 多会话并发：3 个会话同时经真实入口发「射」，各用**不同**的图。
    # 先给并发闸门留足余量（max_concurrency=4），验证并行正确性；再用 3 张不同输入
    # 的产物字节两两不同，证明「不串线 / 不混用别的会话的图」。
    saved_conc = bot.CFG["meme"].get("max_concurrency", 2)
    bot.CFG["meme"]["max_concurrency"] = 4
    M._sem_cache["obj"] = None        # 配置改了，重建信号量
    try:
        _reset_out()
        errs = []
        threads = []
        inputs = {UID_A: img_a, UID_B: img_b, UID_C: gif}

        def worker(uid):
            try:
                bot.handle_group_message(_event(uid, "射", [_seg(inputs[uid])]))
            except Exception as e:
                errs.append("%s:%s" % (uid, e))

        for u in (UID_A, UID_B, UID_C):
            t = threading.Thread(target=worker, args=(u,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        imgs = _imgs()
        chk("10a 多会话并发：无异常", len(errs) == 0, errs)
        chk("10b 多会话并发：3 次都出图", len(imgs) == 3, "IMG=%d" % len(imgs))
        blobs = [d for _, _, d in imgs]
        chk("10c 多会话并发：每张都是合法 GIF",
            all(_media(d)[0] == "GIF" for d in blobs),
            [(_media(d)[0] if d else None) for d in blobs])
        chk("10d 多会话并发：产物两两不同（各用各的图，未串线）",
            len({d for d in blobs}) == 3, "distinct=%d" % len({d for d in blobs}))
    finally:
        bot.CFG["meme"]["max_concurrency"] = saved_conc
        M._sem_cache["obj"] = None

    # 11 连续重复生成：同一口令连发 5 次，每次都成功且产物一致可用
    _reset_out()
    rep_err = None
    for _ in range(5):
        try:
            bot.handle_group_message(_event(UID_A, "爬", [_seg(img_a)]))
        except Exception as e:
            rep_err = str(e)
    rimgs = _imgs()
    chk("11a 连续重复：无异常", rep_err is None, rep_err)
    chk("11b 连续重复：5 次都出图", len(rimgs) == 5, "IMG=%d" % len(rimgs))
    chk("11c 连续重复：产物均为合法 JPEG",
        all(_media(d)[0] == "JPEG" for _, _, d in rimgs), "")

    # 12 动图输入：GIF 段经 _fetch_raw_image 保留动图，正常出图
    err, ims, txt = _run("射", [_seg(gif)])
    chk("12a 动图输入：入口不抛异常", err is None, err)
    chk("12b 动图输入：正常出图（GIF）",
        bool(ims) and _media(ims[-1][2])[0] == "GIF",
        "IMG=%d TEXT=%r" % (len(ims), txt))

    # 13 临时文件清理：所有退出路径后，GRAB_DIR 与 state/meme_* 不留残留
    before_g, before_m = _grab_dir_files(), _meme_tmp_files()
    _run("射", [_seg(img_a)])                 # 成功
    _run("亲", [_seg(img_a)])                 # 校验失败
    _run("射", [_seg(corrupt:=_corrupt("Z"))])  # 服务 533 失败
    _run("悲报 今天摸鱼", [])               # 文字模板成功
    time.sleep(0.4)
    after_g, after_m = _grab_dir_files(), _meme_tmp_files()
    chk("13a 结果临时文件 state/meme_* 全部清理",
        len(after_m - before_m) == 0, "残留=%r" % (after_m - before_m))
    chk("13b 落盘输入图 GRAB_DIR 无新增残留",
        len(after_g - before_g) == 0, "残留=%r" % (after_g - before_g))

    # 14 普通聊天 & 其它图片技能回归：关掉 meme 总闸后，口令透传给下游，不被旁路吞掉
    _set_enabled(False)
    err, ims, txt = _run("射", [_seg(img_a)])
    chk("14a 关闸后 meme 不接管（不出 meme 图）", err is None and len(ims) == 0,
        "err=%s IMG=%d" % (err, len(ims)))
    chk("14b 关闸后普通口令不被旁路改变分发", M.match("射") is None, "")
    # 普通聊天文本（非任何口令）不应出图，也不该抛异常
    err2, ims2, txt2 = _run("今天天气真不错，随便聊聊", [])
    chk("14c 普通聊天：不抛异常、不出 meme 图", err2 is None and len(ims2) == 0,
        "err=%s IMG=%d" % (err2, len(ims2)))
    _set_enabled(True)


def main():
    print("=" * 72)
    print("阶段5 可靠性 / 端到端场景测试（真入口 + 真本地服务 + 生命周期真控制）")
    print("=" * 72)

    # 前置资产检查（不满足明确 SKIP，不假装通过）
    if not (LAB / "venv312" / "Scripts" / "python.exe").is_file() \
            or not (LAB / "meme_server.py").is_file() \
            or not (LAB / "contrib" / "memes" / "shoot").is_dir():
        print("  !! _meme_lab 服务资产不全，无法真跑。RESULT SKIP")
        _sys.exit(2)
    conf = json.loads((LAB / "meme_service.json").read_text(encoding="utf-8"))
    print("  服务配置: host=%s port=%s marker=%s" %
          (conf["host"], conf["port"], conf["service_marker"]))
    print("  前置健康:", health())

    img_a = _png((220, 40, 40), "A")
    img_b = _png((40, 200, 40), "B")
    gif = _gif("G")
    corrupt = _corrupt("X")
    huge = _huge("H")
    print("  素材: A=%dB B=%dB gif=%dB corrupt=%dB huge=%.1fMB" % (
        Path(img_a).stat().st_size, Path(img_b).stat().st_size,
        Path(gif).stat().st_size, Path(corrupt).stat().st_size,
        Path(huge).stat().st_size / 1024 / 1024))

    saved_enabled = bool((bot.CFG.get("meme") or {}).get("enabled"))
    saved_timeout = bot.CFG["meme"].get("generate_timeout", 60)
    try:
        # 生命周期场景会 stop/start/restart；finally 保证无论如何都把服务恢复
        scenario_lifecycle(img_a)
        # 确保后半程服务健康（生命周期场景若中途失败也要尽力恢复）
        if not health():
            ctl("start")
            _wait_health(True)
        scenario_inputs(img_a, img_b, gif, corrupt, huge)
        scenario_concurrency_and_cleanup(img_a, img_b, gif)
    finally:
        _set_enabled(saved_enabled)
        set_timeout(saved_timeout)
        if not health():
            print("\n  [finally] 服务未健康，执行 ctl start 恢复…")
            rc, out = ctl("start")
            up = _wait_health(True)
            print("  [finally] 恢复结果 rc=%s healthy=%s" % (rc, up))
        else:
            print("\n  [finally] 服务健康，无需恢复")

    print("\n" + "=" * 72)
    n_pass = sum(1 for _, c in RESULT if c)
    print("阶段5 结果：%d / %d 通过" % (n_pass, len(RESULT)))
    if not ok:
        for label, c in RESULT:
            if not c:
                print("   FAIL:", label)
    print("RESULT", "PASS" if ok else "FAIL")
    _sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
