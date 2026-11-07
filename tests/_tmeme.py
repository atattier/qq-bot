# -*- coding: utf-8 -*-
"""qbot_meme —— 离线单元测试（阶段 3-C，SAFE 集）。

覆盖：总闸默认关 / 口令识别（正例·反例·别名·不误触·关闸降级）/ 目标地址防篡改 /
用户错误码(541/542/552)与服务故障(502/连接失败)的分流 / 输入大小与数量校验 /
结果 Pillow 校验 / multipart 拼装 / 临时文件清理。

⚠️ 全程打桩、不联网、不碰真 config.json、不起服务：这里测的是「客户端逻辑」。
   真正的端到端（生产 Bot 消息链 + 本地 3.12 服务 + 真实 GIF）在阶段 4/5 用真实
   服务和 tests/_tmeme_e2e.py（MANUAL）跑，不用桩代替。

用法：python tests/_tmeme.py
"""
import io
import os as _os
import sys as _sys
import tempfile
from pathlib import Path
from unittest import mock

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
_sys.stdout.reconfigure(encoding="utf-8")

from qbot import qbot_meme as M          # noqa: E402
from PIL import Image          # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [!!] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


def fresh_cfg(enabled=True, **over):
    d = {"meme": {}}
    for k, v in M._DEFAULTS.items():
        import copy
        d["meme"][k] = copy.deepcopy(v)
    d["meme"]["enabled"] = enabled
    d["meme"].update(over)
    M.bind(cfg=d, state_dir=tempfile.mkdtemp(prefix="meme_test_"))
    return d


def make_gif_bytes(w=40, h=30, n=3):
    frames = []
    from PIL import ImageDraw
    for i in range(n):
        im = Image.new("RGB", (w, h), (200, 30 * i, 30))
        ImageDraw.Draw(im).text((3, 3), str(i), fill=(255, 255, 255))
        frames.append(im.convert("P"))
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                   duration=100, loop=0)
    return buf.getvalue()


# ── 1) 总闸默认关 / cfg 默认值 ────────────────────────────────────────────
print("── 配置与总闸 ──")
chk("总闸默认关(不改动现有行为)", M._DEFAULTS["enabled"] is False)
fresh_cfg(enabled=False)
chk("关闸时 match('射') = None", M.match("射") is None)
fresh_cfg(enabled=True)
chk("开闸后 cfg 段含 5 类模板", len(M._conf("templates")) == 5)

# ── 2) 口令识别 ───────────────────────────────────────────────────────────
print("── 口令识别 ──")
for txt, want_key in [("射", "shoot"), ("🐍", "shoot"), ("爬", "crawl"),
                      ("亲", "kiss"), ("亲亲", "kiss"), ("悲报", "bad_news"),
                      ("摸", "petpet"), ("摸摸", "petpet"), ("摸头", "petpet")]:
    hit = M.match(txt)
    chk("命中 %r -> %s" % (txt, want_key), bool(hit) and hit[0] == want_key, hit and hit[0])
# 文字模板：「悲报 内容」应带 rest
hit = M.match("悲报 今天摸鱼被抓")
chk("文字模板带 rest", bool(hit) and hit[0] == "bad_news" and hit[3] == "今天摸鱼被抓", hit and hit[3])
# 反例：不误触
for txt in ["射雕英雄传", "爬山", "亲切", "摸鱼", "射hand", "随便聊聊", "", "   "]:
    chk("不误触 %r" % txt, M.match(txt) is None)
# 「射 内容」——射不需要文字但仍算命中（tail 被忽略，引擎 texts=0 会挡）
chk("射+空白后缀仍命中", bool(M.match("射 ")) and M.match("射 ")[0] == "shoot")
# parse_message 的图片占位符：真实「带图+口令」消息会拼成这些形态
for txt, want_key, want_rest in [
    ("射[图片]", "shoot", ""),
    ("[图片]摸头", "petpet", ""),
    ("悲报 今天摸鱼被抓了[图片]", "bad_news", "今天摸鱼被抓了"),
    ("[动画表情]爬", "crawl", ""),
]:
    hit = M.match(txt)
    chk("占位符剥离 %r -> %s" % (txt, want_key),
        bool(hit) and hit[0] == want_key and hit[3] == want_rest,
        hit and (hit[0], hit[3]))

# ── 3) 目标地址防篡改 ─────────────────────────────────────────────────────
print("── 目标地址安全 ──")
fresh_cfg(enabled=True)
chk("默认地址 = 127.0.0.1:2233", M._api_base() == "http://127.0.0.1:2233", M._api_base())
fresh_cfg(enabled=True, api_url="http://datukuai.top:2233")
try:
    M._api_base()
    chk("公网地址被拒", False, "没抛异常")
except M.MemeError:
    chk("公网地址被拒", True)
fresh_cfg(enabled=True, api_url="http://127.0.0.1:2233@evil.com/")
try:
    M._api_base()
    chk("带 userinfo 被拒", False)
except M.MemeError:
    chk("带 userinfo 被拒", True)
fresh_cfg(enabled=True, api_url="http://127.0.0.1:9999/foo")
try:
    M._api_base()
    chk("带路径被拒", False)
except M.MemeError:
    chk("带路径被拒", True)

# ── 4) 错误码分流 ─────────────────────────────────────────────────────────
print("── 错误码分流 ──")
fresh_cfg(enabled=True)

def fake_post(code, detail):
    body = ('{"detail":"%s"}' % detail).encode()
    return (code, body, "application/json")

for code, detail in [(541, "图片数量不符，图片数量应为 2"),
                     (542, "文本数量不符，文本数量应为 1"),
                     (552, "参数格式不对")]:
    with mock.patch.object(M, "_post_generate", return_value=fake_post(code, detail)):
        try:
            M.generate("kiss", [], [])
            chk("HTTP %d -> 用户错误" % code, False, "没抛")
        except M.MemeError as e:
            chk("HTTP %d -> 用户错误且带中文" % code, e.user_fault and detail in e.user_msg,
                (e.user_fault, e.user_msg))

# 502 = 服务故障（不是用户错误）
with mock.patch.object(M, "_post_generate", return_value=fake_post(502, "Bad Gateway")):
    try:
        M.generate("shoot", [], [])
        chk("502 -> 服务故障", False)
    except M.MemeError as e:
        chk("502 -> 服务故障(不甩锅用户)", (not e.user_fault) and "开小差" in e.user_msg,
            (e.user_fault, e.user_msg))

# 连接层异常 -> 服务故障
def raise_urlerror(*a, **k):
    import urllib.error
    raise urllib.error.URLError("refused")
with mock.patch.object(M, "_post_generate", side_effect=raise_urlerror):
    try:
        M.generate("shoot", [], [])
        chk("连接失败 -> 服务故障", False)
    except M.MemeError as e:
        chk("连接失败 -> 服务故障", (not e.user_fault) and "连不上" in e.user_msg, e.user_msg)

# ── 5) 结果校验 + 临时文件 ────────────────────────────────────────────────
print("── 结果校验 / 临时文件 ──")
gif = make_gif_bytes(40, 30, 3)
with mock.patch.object(M, "_post_generate", return_value=(200, gif, "image/gif")):
    p, meta = M.generate("shoot", [], [])
    chk("返回 GIF meta 正确", meta["format"] == "GIF" and meta["n_frames"] == 3
        and meta["animated"] and meta["size"] == (40, 30), meta)
    chk("临时文件真实存在且可 Pillow 打开", Path(p).is_file()
        and Image.open(p).n_frames == 3, p)
    _os.unlink(p)
# 空结果
with mock.patch.object(M, "_post_generate", return_value=(200, b"", "image/gif")):
    try:
        M.generate("shoot", [], [])
        chk("空结果被拒", False)
    except M.MemeError as e:
        chk("空结果被拒", "空图" in e.user_msg, e.user_msg)
# 超大结果
fresh_cfg(enabled=True, max_result_bytes=100)
with mock.patch.object(M, "_post_generate", return_value=(200, gif, "image/gif")):
    try:
        M.generate("shoot", [], [])
        chk("超大结果被拒", False)
    except M.MemeError as e:
        chk("超大结果被拒", "太大" in e.user_msg, e.user_msg)
# 损坏图片
fresh_cfg(enabled=True)
with mock.patch.object(M, "_post_generate", return_value=(200, b"NOT_AN_IMAGE_XXXX" * 20, "image/gif")):
    try:
        M.generate("shoot", [], [])
        chk("损坏图片被拒", False)
    except M.MemeError as e:
        chk("损坏图片被拒", "损坏" in e.user_msg or "空图" in e.user_msg, e.user_msg)

# ── 6) multipart 拼装 ────────────────────────────────────────────────────
print("── multipart ──")
td = Path(tempfile.mkdtemp(prefix="meme_mp_"))
imgp = td / "a.png"
Image.new("RGB", (10, 10), (1, 2, 3)).save(imgp)
body, ctype = M._multipart([str(imgp)], ["中文", "two"])
chk("boundary 出现在 ctype", "boundary=" in ctype)
chk("图片/文字/args 三段齐", b"image_0.png" in body and "中文".encode() in body
    and b'name="args"' in body and body.endswith(b"--\r\n"))

# ── 7) 数量校验 ──────────────────────────────────────────────────────────
print("── 输入数量校验 ──")
info = {"params_type": {"min_images": 2, "max_images": 2, "min_texts": 0,
        "max_texts": 0, "default_texts": []}}
try:
    M._validate_inputs(info, [str(imgp)], [])
    chk("图片不足被拒", False)
except M.MemeError as e:
    chk("图片不足被拒(用户错误)", e.user_fault and "还差" in e.user_msg, e.user_msg)
info2 = {"params_type": {"min_images": 0, "max_images": 0, "min_texts": 1,
         "max_texts": 1, "default_texts": []}}
try:
    M._validate_inputs(info2, [], [])
    chk("文字不足被拒", False)
except M.MemeError as e:
    chk("文字不足被拒(用户错误)", e.user_fault, e.user_msg)
# 有 default_texts 时缺文字不报错
info3 = {"params_type": {"min_images": 0, "max_images": 0, "min_texts": 1,
         "max_texts": 1, "default_texts": ["喜报"]}}
try:
    M._validate_inputs(info3, [], [])
    chk("有默认文案时缺文字放行", True)
except M.MemeError as e:
    chk("有默认文案时缺文字放行", False, e.user_msg)

# ── 10) 头像模式（2026-11-05）：@人 / 双人头像 / 自己头像兜底 ─────────────
print("── 头像模式 ──")
_AV = tempfile.mkdtemp(prefix="meme_avatar_test_")
_orig_info, _orig_gen = M.meme_info, M.generate


def _fake_info(key, force=False):
    pt = {"kiss": (2, 2, 0, 0), "petpet": (1, 1, 0, 0), "bad_news": (0, 0, 1, 1)}[key]
    return {"key": key, "params_type": {"min_images": pt[0], "max_images": pt[1],
                                        "min_texts": pt[2], "max_texts": pt[3],
                                        "default_texts": []}}


def _fake_avatar(qq):
    p = Path(_AV) / ("%s.png" % qq)
    p.write_bytes(make_gif_bytes(20, 20, 1))     # 内容无所谓，落盘即可
    return str(p)


def _fake_gen(key, image_paths, texts):
    out = Path(M.STATE) / "result.gif"
    out.write_bytes(make_gif_bytes())
    return str(out), {"format": "GIF", "size": (40, 30), "n_frames": 3,
                      "animated": True, "bytes": out.stat().st_size, "ext": ".gif"}


fresh_cfg(enabled=True)
M.meme_info = _fake_info
M.generate = _fake_gen
M.bind(cfg=M.CFG, avatar_file=_fake_avatar,
       fetch_image=lambda seg: (str(seg.get("file") or "").replace("file:///", "") or None))
sent, gen_calls = [], []

# @ 占位剥离：「@张三 亲」必须命中 kiss（此前 @ 开头永远匹配不上）
hit = M.match("@张三 亲")
chk("@ 开头的口令能命中", bool(hit) and hit[0] == "kiss", hit and hit[0])
chk("纯口令仍命中", bool(M.match("亲")) and M.match("亲")[0] == "kiss")

# 双人模板：亲 @某人 = [自己头像, 对方头像]（发话人是主动方排第 1）
sent.clear(); gen_calls.clear()
M.generate = lambda key, image_paths, texts: (gen_calls.append((key, list(image_paths))),
                                              _fake_gen(key, image_paths, texts))[1]
rc = M.handle(123450001, 111001, "@张三 亲", [], 0, ats=["222002"], send=lambda gid, p: sent.append(p) or True)
chk("双人模板已处理", rc is True and len(sent) == 1)
chk("双人模板图序 = 自己在前", bool(gen_calls) and gen_calls[0][1] == [str(Path(_AV) / "111001.png"),
                                                                    str(Path(_AV) / "222002.png")],
    gen_calls and gen_calls[0][1])

# 单人模板：「摸头 @某人」= 对方的头像
sent.clear(); gen_calls.clear()
rc = M.handle(123450001, 111001, "摸头 @李四", [], 0, ats=["333003"], send=lambda gid, p: sent.append(p) or True)
chk("单人模板用被@的人头像", bool(gen_calls) and gen_calls[0][1] == [str(Path(_AV) / "333003.png")],
    gen_calls and gen_calls[0][1])

# 不带图不带@：单人模板回落发话人自己的头像
sent.clear(); gen_calls.clear()
rc = M.handle(123450001, 111001, "摸头", [], 0, send=lambda gid, p: sent.append(p) or True)
chk("无@时单人模板用自己的头像", bool(gen_calls) and gen_calls[0][1] == [str(Path(_AV) / "111001.png")],
    gen_calls and gen_calls[0][1])

# 双人模板无@：明确报「要 @ 一个人」，不硬生成
sent.clear(); gen_calls.clear()
rc = M.handle(123450001, 111001, "亲", [], 0, send=lambda gid, p: sent.append(p) or True)
chk("双人模板无@给出可读提示", rc is True and not gen_calls and len(sent) == 0)
fb = M._FEEDBACK
_fb_msgs = []
M.bind_feedback(lambda gid, text: _fb_msgs.append(text))
M.handle(123450001, 111001, "亲", [], 0, send=lambda gid, p: sent.append(p) or True)
chk("提示文案带「@ 一个人」", bool(_fb_msgs) and "@ 一个人" in _fb_msgs[-1], _fb_msgs)

# 带图优先：本条有图时以本条图为主；图不够数用被@人的头像补齐
# （「发自己的照片 + 亲 @王五」= 这张图亲王五的头像）
sent.clear(); gen_calls.clear()
img_seg = {"file": "file:///" + (Path(_AV) / "img.png").as_posix()}
(Path(_AV) / "img.png").write_bytes(make_gif_bytes())
rc = M.handle(123450001, 111001, "亲 @王五", [img_seg], 0, ats=["444004"],
              send=lambda gid, p: sent.append(p) or True)
chk("本条图优先 + 头像补齐", bool(gen_calls) and
    [_os.path.normpath(p) for p in gen_calls[0][1]] ==
    [_os.path.normpath(str(Path(_AV) / "img.png")),
     _os.path.normpath(str(Path(_AV) / "444004.png"))],
    gen_calls and gen_calls[0][1])

# 临时文件清理：头像 + 结果图用完即删
leftover = [p.name for p in Path(_AV).glob("*.png")]
res_left = [p.name for p in Path(M.STATE).glob("result.gif")]
chk("头像临时文件全部清理", leftover == [], leftover)
chk("结果临时文件全部清理", res_left == [], res_left)

M.meme_info, M.generate = _orig_info, _orig_gen
M.bind_feedback(fb)

print()
print("RESULT", "PASS" if ok else "FAIL")
_sys.exit(0 if ok else 1)
