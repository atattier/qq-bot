# -*- coding: utf-8 -*-
"""qbot_petpet —— 把 @的人 的头像做成「揉一揉 / 摸摸头 / 咬一口」那套动图。

**这是什么**：`nonebot-plugin-petpet` 的本地移植。原插件是 NoneBot2 插件，本机 bot 不是
NoneBot（纯标准库 + Pillow，直接收 NapCat 的 OneBot11 POST），所以那个包装进来一行也跑不了。
这里把它的**模板素材 + 合成算法**搬过来，用 Pillow 重写，挂进本项目的技能包体系：

    skills/petpet/assets/<模板>/<帧号>.png     模板素材（tools/_fetch_petpet_assets.py 下载）
    skills/petpet/skill.json                   技能清单（提示词 + 开关 + 参数）
    qbot_skill_tools.py  → petpet.send/list    给两个大脑调的工具
    bot.py  handle_petpet()                    群口令直通（「摸摸头 @某人」不用过大脑）

素材版权归上游（MIT）与原作者，这里只做本地缓存。

⚠️ 和其余 qbot_* 模块一样：**不 import bot**。发图靠调用方（qbot_skill_tools / bot.py）接。
"""
import hashlib
import io
import json
import logging
import re
import threading
import time
import urllib.request
from pathlib import Path

from PIL import Image

log = logging.getLogger("qqbot.petpet")

BASE = Path(__file__).resolve().parent.parent
ASSETS = BASE / "skills" / "petpet" / "assets"
STATE = BASE / "state"          # bind() 可改；只放同步记账，不放素材
OUTDIR = BASE / "cache" / "petpet"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
AVATAR = "http://q1.qlogo.cn/g?b=qq&nk=%s&s=%d"
# 没设头像的号，腾讯会给这张默认图；它 s=640 也是同一张，得降到 s=100 才不糊
DEFAULT_AVATAR_MD5 = "acef72340ac0e914090bd35799f5594e"

MAX_AVATAR_BYTES = 6 * 1024 * 1024
TMP_MAX_AGE = 24 * 3600
MAX_TMP_FILES = 120


# ── 模板表 ──────────────────────────────────────────────────────────────
# words 是群里真会打的说法；命中即直接出图，不经过大脑（见 bot.handle_petpet）。
# 帧参数全部照抄上游 functions.py（0.3.x），改素材时要一起改。

TEMPLATES = {
    "petpet": {
        "name": "摸摸头", "frames": 5, "duration": 60, "prep": "circle",
        "kind": "blank", "blank_bg": (0, 0, 0, 0), "over": True,
        "locs": [(14, 20, 98, 98), (12, 33, 101, 85), (8, 40, 110, 76),
                 (10, 33, 102, 84), (12, 20, 98, 98)],
        "words": ["摸摸头", "摸头", "摸摸", "揉头", "petpet", "rua", "撸头"],
    },
    "pat": {
        "name": "拍拍", "frames": 10, "duration": 85, "prep": "square",
        "kind": "under",
        "locs": [(11, 73, 106, 100), (8, 79, 112, 96)],
        "loc_by_index": {2: 1},
        "seq": [0, 1, 2, 3, 1, 2, 3, 0, 1, 2, 3, 0, 0, 1, 2, 3, 0, 0, 0, 0,
                4, 5, 5, 5, 6, 7, 8, 9],
        "words": ["拍拍", "拍一拍", "拍头", "pat"],
    },
    "rub": {
        "name": "揉一揉", "frames": 6, "duration": 50, "prep": "circle",
        "kind": "over_frame", "need_sender": True,
        "locs": [(39, 91, 75, 75), (49, 101, 75, 75), (67, 98, 75, 75),
                 (55, 86, 75, 75), (61, 109, 75, 75), (65, 101, 75, 75)],
        "sender_locs": [(102, 95, 70, 80, 0), (108, 60, 50, 100, 0), (97, 18, 65, 95, 0),
                        (65, 5, 75, 75, -20), (95, 57, 100, 55, -70), (109, 107, 65, 75, 0)],
        "words": ["揉一揉", "揉揉", "揉", "搓一搓", "搓揉", "rub"],
    },
    "bite": {
        "name": "咬一口", "frames": 16, "duration": 70, "prep": "square",
        "kind": "under",
        "locs": [(90, 90, 105, 150), (90, 83, 96, 172), (90, 90, 106, 148),
                 (88, 88, 97, 167), (90, 85, 89, 179), (90, 90, 106, 151)],
        "words": ["咬一口", "咬咬", "咬", "啃", "bite"],
    },
    "knock": {
        "name": "敲一敲", "frames": 8, "duration": 40, "prep": "square",
        "kind": "under",
        "locs": [(60, 308, 210, 195), (60, 308, 210, 198), (45, 330, 250, 172),
                 (58, 320, 218, 180), (60, 310, 215, 193), (40, 320, 250, 285),
                 (48, 308, 226, 192), (51, 301, 223, 200)],
        "words": ["敲一敲", "敲门", "敲", "knock"],
    },
    "punch": {
        "name": "揍一拳", "frames": 13, "duration": 30, "prep": "square",
        "kind": "blank", "blank_bg": "white", "over": True, "resize": (260, 260),
        "locs": [(-50, 20), (-40, 10), (-30, 0), (-20, -10), (-10, -10), (0, 0),
                 (10, 10), (20, 20), (10, 10), (0, 0), (-10, -10), (10, 0), (-30, 10)],
        "y_shift": -15,
        "words": ["揍", "揍一拳", "打一拳", "punch"],
    },
    "pound": {
        "name": "捶一捶", "frames": 8, "duration": 50, "prep": "square",
        "kind": "under",
        "locs": [(135, 240, 138, 47), (135, 240, 138, 47), (150, 190, 105, 95),
                 (150, 190, 105, 95), (148, 188, 106, 98), (146, 196, 110, 88),
                 (145, 223, 112, 61), (145, 223, 112, 61)],
        "words": ["捶", "捶一捶", "锤", "pound"],
    },
    "thump": {
        "name": "撞一撞", "frames": 4, "duration": 40, "prep": "square",
        "kind": "under",
        "locs": [(65, 128, 77, 72), (67, 128, 73, 72), (54, 139, 94, 61), (57, 135, 86, 65)],
        "words": ["撞", "撞一撞", "thump"],
    },
    "roll": {
        "name": "滚一圈", "frames": 8, "duration": 100, "prep": "square",
        "kind": "under", "resize": (210, 210),
        "locs": [(87, 77, 0), (96, 85, -45), (92, 79, -90), (92, 78, -135),
                 (92, 75, -180), (92, 75, -225), (93, 76, -270), (90, 80, -315)],
        "words": ["滚一圈", "滚", "roll"],
    },
    "twist": {
        "name": "扭一扭", "frames": 5, "duration": 100, "prep": "square",
        "kind": "under", "resize": (78, 78),
        "locs": [(25, 66, 0), (25, 66, 60), (23, 68, 120), (20, 69, 180),
                 (22, 68, 240), (25, 66, 300)],
        "words": ["扭一扭", "扭", "twist"],
    },
    "throw_gif": {
        "name": "扔出去", "frames": 8, "duration": 100, "prep": "circle",
        "kind": "over_frame", "multi": True,
        "locs": [[(32, 32, 108, 36)], [(32, 32, 122, 36)], [],
                 [(123, 123, 19, 129)], [(185, 185, -50, 200), (33, 33, 289, 70)],
                 [(32, 32, 280, 73)], [(35, 35, 259, 31)], [(175, 175, -50, 220)]],
        "words": ["扔出去", "扔", "抛", "throw"],
    },
    "capoo_rub": {
        "name": "猫猫揉", "frames": 4, "duration": 100, "prep": "square",
        "kind": "under", "resize": (180, 180), "loc_order": "whxy",
        "locs": [(178, 184, 78, 260), (178, 174, 84, 269),
                 (178, 174, 84, 269), (178, 178, 84, 264)],
        "words": ["猫猫揉", "capoo", "揉猫"],
    },
    "support": {
        "name": "支持你", "frames": 1, "duration": 0, "prep": "square",
        "kind": "under", "resize": (815, 815), "rotate": 23, "jpg": True,
        "locs": [(-172, -17)],
        "words": ["支持你", "支持", "support"],
    },
}

# 硬编码的这 13 个是**手工搬**的（参数逐条对照上游核过）。自动搬的另存一份覆盖表，
# 见 `_load_overrides()`；`_PRISTINE` 留着，是为了「移除」之后还能一键恢复。
_PRISTINE = dict(TEMPLATES)


def _rebuild():
    """重建「动词 → 模板」直查表（长词优先，避免「揉」抢了「揉一揉」）。

    每次改动 TEMPLATES（自动搬进来 / 移除某个）都要调一次，否则新模板认不出来。
    """
    global _VERB, _VERB_ORDER, _ALIAS
    _VERB = {}
    for _k, _t in TEMPLATES.items():
        for _w in _t.get("words") or []:
            _VERB.setdefault(_w, _k)
    _VERB_ORDER = sorted(_VERB, key=len, reverse=True)
    _ALIAS = {}
    for _k, _t in TEMPLATES.items():
        _ALIAS[_k] = _k
        if _t.get("name"):
            _ALIAS[_t["name"]] = _k


_rebuild()


# ── 图像小工具 ──────────────────────────────────────────────────────────

def _square(im):
    """居中裁成正方形。"""
    w, h = im.size
    s = min(w, h)
    return im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))


def _circle(im):
    """正方形 + 圆形蒙版（4 倍超采样再缩回来，边缘不锯齿）。"""
    im = _square(im).convert("RGBA")
    n = im.width
    big = Image.new("L", (n * 4, n * 4), 0)
    from PIL import ImageDraw
    ImageDraw.Draw(big).ellipse((0, 0, n * 4 - 1, n * 4 - 1), fill=255)
    mask = big.resize((n, n), Image.LANCZOS)
    im.putalpha(mask)
    return im


def _under(frame, im, pos):
    """把 im 垫在帧**下面**（等价于上游 paste(..., below=True)）：
    帧自己的 alpha 盖在上面，手/爪子这些不透明处会挡住头像。"""
    canvas = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    canvas.paste(im, (int(pos[0]), int(pos[1])))
    canvas.paste(frame, (0, 0), mask=frame)
    return canvas


def _over(canvas, im, pos):
    """把 im 叠在画布上（按 im 自己的 alpha 抠图）。"""
    im = im.convert("RGBA")
    canvas.alpha_composite(im, (int(pos[0]), int(pos[1])))
    return canvas


def _frame(tpl, i):
    p = ASSETS / tpl / ("%d.png" % i)
    if not p.exists():
        raise FileNotFoundError("模板素材缺失：%s（跑 tools/_fetch_petpet_assets.py %s）" % (p, tpl))
    with Image.open(p) as im:
        return im.convert("RGBA")


def _prep(im, spec):
    im = im.convert("RGBA")
    if spec.get("prep") == "circle":
        im = _circle(im)
    else:
        im = _square(im)
    # 旋转和缩放的先后顺序会影响结果（旋转会把画布撑大）：
    #   上游 support  : square().resize((815,815)).rotate(23, expand=True) → 先缩放
    #   上游 paint    : resize((117,135), keep_ratio=True).rotate(4, expand=True) → 先缩放
    # 手工搬的 support 一直是「先转再缩放」且看过了没问题，所以默认保持老顺序，
    # 自动搬的由 qbot_petpet_port 记下 rotate_after_resize 才换顺序。
    if spec.get("rotate") and not spec.get("rotate_after_resize"):
        im = im.rotate(spec["rotate"], expand=True)
    if spec.get("resize_keep"):
        # 上游的 resize((w, h), keep_ratio=True)：等比塞进这个框，不拉伸
        im.thumbnail(tuple(spec["resize_keep"]), Image.LANCZOS)
    if spec.get("resize"):
        im = im.resize(tuple(spec["resize"]), Image.LANCZOS)
    if spec.get("rotate") and spec.get("rotate_after_resize"):
        im = im.rotate(spec["rotate"], expand=True)
    return im


# ── 合成 ────────────────────────────────────────────────────────────────

def _loc_of(spec, idx):
    """取这一帧头像该贴哪。返回 None / 元组 / 元组列表（multi）。

    三个特例都照上游：
      · bite 只有前 6 帧贴头像，7~16 帧是纯动画 ⇒ locs 比帧数短，越界就当不贴；
      · pat 的 10 张图共用 2 个位置（第 3 张用第二个），用 loc_by_index 指定；
      · throw_gif 一帧可能贴好几个（飞出去的残影），locs 是「列表的列表」。
    """
    locs = spec.get("locs") or []
    if spec.get("multi"):
        return locs[idx] if idx < len(locs) else []
    if "loc_by_index" in spec:
        j = spec["loc_by_index"].get(idx, 0)
        return locs[j] if j < len(locs) else None
    return locs[idx] if idx < len(locs) else None


def _place(canvas, av, loc, below, y_shift=0):
    """按 loc 的形状决定怎么贴：4 元 = (x,y,w,h) 缩放贴；3 元 = (x,y,角度) 旋转贴；
    2 元 = (x,y) 原样贴。"""
    if not loc:
        return canvas
    if len(loc) == 4:
        x, y, w, h = loc
        im = av.resize((w, h), Image.LANCZOS)
        y += y_shift
    elif len(loc) == 3:
        x, y, ang = loc
        im = av.rotate(ang)
        y += y_shift
    else:
        x, y = loc
        im = av
        y += y_shift
    return _under(canvas, im, (x, y)) if below else _over(canvas, im, (x, y))


def _render(tpl, av, sender_av=None, spec=None, frame_fn=None, seq=None):
    """按参数渲一组帧。

    `spec` / `frame_fn` / `seq` 是给「素材市场预览」用的：那些模板还没搬进来，
    参数是 ast 现抠的、底图在预览缓存里（还缩过），不能走 `TEMPLATES[tpl]` + `ASSETS`。
    """
    spec = spec or TEMPLATES[tpl]
    get = frame_fn or (lambda i: _frame(tpl, i))
    av = _prep(av, spec)
    seq = seq or spec.get("seq") or list(range(spec["frames"]))
    whxy = spec.get("loc_order") == "whxy"      # capoo_rub 的 loc 是 (w,h,x,y)
    frames = []
    for idx in seq:
        img = get(idx)
        loc = _loc_of(spec, idx)
        if whxy and loc:
            loc = (loc[2], loc[3], loc[0], loc[1])
        elif spec.get("multi") and loc:
            # throw_gif 的每个元组也是 (w, h, x, y)，统一换成 _place 认的 (x, y, w, h)
            loc = [(x, y, w, h) for (w, h, x, y) in loc]

        if spec["kind"] in ("blank", "over_frame"):
            if spec["kind"] == "blank":
                bg = spec.get("blank_bg", (0, 0, 0, 0))
                canvas = Image.new("RGBA", img.size, bg if bg == "white" else tuple(bg))
            else:
                # over_frame：帧当画布，头像**盖在上面**。
                # 这类帧是**不透明**的（rub 是白底线稿、throw_gif 是照片），
                # 走 under 会被帧整块糊掉 —— 上游 rub/throw_gif 也是 alpha=True 直接贴。
                canvas = img.copy()
            if spec.get("multi") and loc:
                for one in loc:
                    canvas = _place(canvas, av, one, False, spec.get("y_shift", 0))
            else:
                canvas = _place(canvas, av, loc, False, spec.get("y_shift", 0))
            if spec.get("over"):
                canvas.paste(img, (0, 0), mask=img)
        else:
            # kind == under：帧当画布，头像垫底下（帧的不透明处会挡住头像）
            canvas = img
            if spec.get("multi") and loc:
                for one in loc:
                    canvas = _place(canvas, av, one, True)
            else:
                canvas = _place(canvas, av, loc, True)

        # rub：还要把「发起人」的头像按角度贴上去（上游是贴完对方再贴自己）
        if spec.get("need_sender") and sender_av is not None:
            sl = spec["sender_locs"]
            if idx < len(sl):
                x, y, w, h, ang = sl[idx]
                head = _circle(sender_av).resize((w, h), Image.LANCZOS)
                if ang:
                    head = head.rotate(ang, expand=True)
                canvas = _over(canvas, head, (x, y))
        frames.append(canvas)
    return frames


def _clean_out():
    try:
        files = sorted(OUTDIR.glob("*.gif"), key=lambda p: p.stat().st_mtime, reverse=True)
        now = time.time()
        for p in files[MAX_TMP_FILES:]:
            p.unlink(missing_ok=True)
        for p in files[:MAX_TMP_FILES]:
            if now - p.stat().st_mtime > TMP_MAX_AGE:
                p.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _save(frames, duration, jpg=False):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    _clean_out()
    name = "petpet_%d_%s" % (int(time.time() * 1000), hashlib.md5(
        str(time.time()).encode()).hexdigest()[:6])
    if jpg:
        path = OUTDIR / (name + ".jpg")
        bg = Image.new("RGB", frames[0].size, "white")
        bg.paste(frames[0], (0, 0), frames[0])
        bg.save(path, "JPEG", quality=90)
        return path
    path = OUTDIR / (name + ".gif")
    # 群里的底是白的，先压白底再量化，避免 GIF 半透明处发黑
    flat = []
    for f in frames:
        bg = Image.new("RGB", f.size, "white")
        bg.paste(f, (0, 0), f)
        flat.append(bg.convert("P", palette=Image.ADAPTIVE, colors=128))
    flat[0].save(path, save_all=True, append_images=flat[1:],
                 duration=max(20, int(duration)), loop=0, optimize=True, disposal=2)
    return path


# ── 头像 ────────────────────────────────────────────────────────────────

def fetch_avatar(qq, timeout=12):
    """拉 QQ 头像（640 起，默认头像降到 100 才不糊）。失败抛异常。"""
    qq = str(qq).strip()
    if not re.fullmatch(r"\d{4,12}", qq or ""):
        raise ValueError("QQ 号不对：%r" % qq)

    # 2026-11-06 真机踩坑：系统代理（127.0.0.1:10808）对 q1.qlogo.cn 一律回 502，
    # 主人「@谁用谁头像」全挂。qlogo 是腾讯自家源、无墙，绕过代理直连（同 custom
    # 模型出站绕代理的先例）。ProxyHandler({}) = 空代理表，比拆环境变量干净。
    _direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _get(size):
        req = urllib.request.Request(AVATAR % (qq, size), headers=UA)
        with _direct.open(req, timeout=timeout) as r:
            return r.read(MAX_AVATAR_BYTES + 1)

    body = _get(640)
    if hashlib.md5(body).hexdigest() == DEFAULT_AVATAR_MD5:
        body = _get(100)
    if len(body) > MAX_AVATAR_BYTES:
        raise ValueError("头像太大（%d 字节）" % len(body))
    im = Image.open(io.BytesIO(body))
    im.load()
    return im.convert("RGBA")


# ── 对外 ────────────────────────────────────────────────────────────────

def has_assets(tpl):
    return (ASSETS / tpl / "0.png").exists()


def list_templates(only_ready=True):
    out = []
    for k, t in TEMPLATES.items():
        if only_ready and not has_assets(k):
            continue
        out.append({"id": k, "name": t["name"], "words": t["words"]})
    return out


def list_text(only_ready=True):
    items = list_templates(only_ready)
    if not items:
        return "petpet 模板素材还没下载（跑 tools/_fetch_petpet_assets.py）"
    return "petpet 模板 %d 个：\n" % len(items) + "\n".join(
        "· %s（%s）　口令：%s" % (x["name"], x["id"], "、".join(x["words"][:3]))
        for x in items)


def list_numbered():
    """带编号的清单（群口令「@bot 模板」用）。编号从 1 开始，和 template_by_index 一一对应。"""
    items = list_templates()
    if not items:
        return []
    return ["%2d. %s　%s" % (i + 1, x["name"], "、".join(x["words"][:3]))
            for i, x in enumerate(items)]


def template_by_index(n):
    """「模板 3」→ 第 3 个模板的 id。编号越界/不是数字返回 None。"""
    items = list_templates()
    try:
        k = int(str(n).strip())
    except (TypeError, ValueError):
        return None
    if 1 <= k <= len(items):
        return items[k - 1]["id"]
    return None


def resolve(text):
    """从一句话里认模板：先按模板 id/中文名，再按动词表（长词优先）。返回 (id, 命中的词)。"""
    t = (text or "").strip()
    if not t:
        return None, ""
    for alias, tid in _ALIAS.items():
        if alias and alias in t:
            return tid, alias
    for w in _VERB_ORDER:
        if w in t:
            return _VERB[w], w
    return None, ""


def make(template, avatar, sender_avatar=None):
    """合成并落盘，返回 (路径, 模板中文名)。"""
    tpl = _ALIAS.get(template, template)
    if tpl not in TEMPLATES:
        raise KeyError("没有这个模板：%s（有：%s）"
                       % (template, "、".join(sorted(TEMPLATES))))
    spec = TEMPLATES[tpl]
    frames = _render(tpl, avatar, sender_avatar)
    path = _save(frames, spec.get("duration", 60), jpg=bool(spec.get("jpg")))
    return path, spec["name"]


def make_for(template, qq, sender_qq=None, image_path=None):
    """给某个 QQ 号出图。image_path 有值时优先用那张图（群友发的图当脸）。"""
    if image_path:
        im = Image.open(image_path)
        im.load()
        av = im.convert("RGBA")
    else:
        av = fetch_avatar(qq)
    sender_av = None
    if TEMPLATES.get(_ALIAS.get(template, template), {}).get("need_sender"):
        try:
            sender_av = fetch_avatar(sender_qq or qq)
        except Exception:  # noqa: BLE001
            sender_av = av
    return make(template, av, sender_av)


# ── 素材市场（2026-10-27）───────────────────────────────────────────────
# 主人：「为啥模板搬不了，不能在网页控制台预览那些模板，然后我看到喜欢的就拿过来，
#        原有的，不喜欢了也可以移除吗」
#
# 结论修正：**大部分是能自动搬的**。之前我说「搬不了」只对了一半 —— 帧图能从镜像下，
# 参数（贴哪儿/几帧/盖上面还是垫下面）写在上游 `functions.py` 里，但那些写法相当规整，
# `qbot_petpet_port` 用 ast 能把其中 36 个抠出来（120 个函数里），剩下的才是真需手工。
#
# 数据分三层，互不覆盖：
#   TEMPLATES（本文件，13 个手工核过的）  ← 底板
#   state/petpet/templates.json            ← 自动搬进来的（可整份删掉，回到 13 个）
#   state/petpet/removed.json              ← 移除过的（同步/自动搬都会跳过）

_PORT_FILE = "templates.json"
_REMOVED_FILE = "removed.json"
_UP_SRC = "up_functions.py"       # 上游 functions.py 的本地缓存
_UP_LIST = "up_listing.json"      # 上游 resource_list.json 的本地缓存


def _pdir():
    d = STATE / "petpet"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pjson(name, default):
    try:
        p = _pdir() / name
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("读 %s 失败: %s", name, e)
    return default


def _pwrite(name, data):
    try:
        p = _pdir() / name
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("写 %s 失败: %s", name, e)
        return False


def _pjson_mtime():
    """templates.json 的修改时间（= 参数表最后一次变动的时刻）。

    示范动图（demo.gif）拿它当「要不要重画」的判据：参数表比动图新就重建。
    """
    try:
        return (_pdir() / _PORT_FILE).stat().st_mtime
    except OSError:
        return 0.0


def _load_overrides():
    """把「自动搬的」和「移除的」叠到 TEMPLATES 上（bind 时调，改完也调）。"""
    global TEMPLATES
    TEMPLATES = dict(_PRISTINE)
    extra = _pjson(_PORT_FILE, {}) or {}
    for k, spec in extra.items():
        try:
            TEMPLATES[k] = dict(spec)
        except Exception:  # noqa: BLE001
            continue
    for k in (_pjson(_REMOVED_FILE, []) or []):
        TEMPLATES.pop(k, None)
    _rebuild()
    return {"extra": len(extra), "removed": len(_pjson(_REMOVED_FILE, []) or [])}


def removed_names():
    return list(_pjson(_REMOVED_FILE, []) or [])


def ported_names():
    """自动搬进来的模板名（不含 13 个手工的）。"""
    return sorted((_pjson(_PORT_FILE, {}) or {}).keys())


# ── 上游清单 / 源码缓存 ─────────────────────────────────────────────────

def _up_listing(force=False):
    """上游 resource_list.json → {模板名: [文件名...]}。缓存 6 小时。"""
    d = _pjson(_UP_LIST, {}) or {}
    if not force and d.get("at") and time.time() - d["at"] < 6 * 3600 and d.get("tpl"):
        return d["tpl"]
    body = _sync_get("resource_list.json", timeout=60)
    items = json.loads(body.decode("utf-8"))
    tpl = {}
    for it in items or []:
        parts = str(it.get("path") or "").split("/")
        if len(parts) == 3 and parts[0] == "images":
            tpl.setdefault(parts[1], []).append(parts[2])
    for k in tpl:
        tpl[k].sort()
    _pwrite(_UP_LIST, {"at": time.time(), "tpl": tpl})
    return tpl


def _up_source(force=False):
    """上游 functions.py 的本地副本（解析参数用）。

    force=True（网页「刷新清单」）会重下一份；但**下载失败必须退回旧副本**，
    否则镜像一抽风，整个市场会从「21 个能搬」变成「99 个都得手工」，
    主人看到的就是"刷新一下全坏了"。
    """
    p = _pdir() / _UP_SRC
    if not force and p.exists() and time.time() - p.stat().st_mtime < 24 * 3600:
        return p.read_text(encoding="utf-8")
    try:
        body = _root_get("nonebot_plugin_petpet/functions.py", timeout=90)
    except Exception as e:  # noqa: BLE001
        if p.exists():
            log.warning("上游 functions.py 这次没下下来（%s），先用 %s 那份旧的",
                        e, time.strftime("%m-%d %H:%M", time.localtime(p.stat().st_mtime)))
            return p.read_text(encoding="utf-8")
        raise
    p.write_bytes(body)
    return body.decode("utf-8")


_ANALYZE_CACHE = {"key": None, "val": None}


def _up_analyze(force=False):
    """解析结果缓存（网页每次开抽屉都会问一遍，别每次都重解析）。"""
    from qbot import qbot_petpet_port as PORT
    src = _up_source(force=force)
    key = (len(src), hash(src))
    if not force and _ANALYZE_CACHE["key"] == key:
        return _ANALYZE_CACHE["val"]
    val = PORT.available(src)
    _ANALYZE_CACHE.update(key=key, val=val)
    return val


# ── 预览 ────────────────────────────────────────────────────────────────

def frame_path(name, i, allow_fetch=False):
    """取某个模板的第 i 帧。本地有就用本地的；没有就看预览缓存。

    ⚠️ `allow_fetch=False`（网页默认）时**绝不联网**：镜像单张要 5~10 秒，
    而 BaseHTTP 是单线程的 —— 99 张缩略图挨个去拉，网页会卡成两分钟白屏。
    缓存由后台的 `thumb_prefetch()` 慢慢填，网页先显示占位、过几秒再重试。
    """
    name = re.sub(r"[^A-Za-z0-9_\-]", "", str(name or ""))
    try:
        i = int(i)
    except (TypeError, ValueError):
        return None
    if not name or i < 0 or i > 400:
        return None
    local = ASSETS / name / ("%d.png" % i)
    if local.exists():
        return local
    for ext in (".jpg", ".jpeg", ".webp", ".gif"):
        q = ASSETS / name / ("%d%s" % (i, ext))
        if q.exists():
            return q
    cache = _pdir() / "prev" / name
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        q = cache / ("%d%s" % (i, ext))
        if q.exists():
            return q
    if not allow_fetch:
        return None
    return _fetch_frame(name, i)


def _fetch_frame(name, i):
    """真的从镜像拉一帧，落到预览缓存里。返回路径或 None。"""
    cache = _pdir() / "prev" / name
    try:
        listing = _up_listing()
    except Exception as e:  # noqa: BLE001
        log.debug("拉不到上游清单: %s", e)
        return None
    files = listing.get(name) or []
    cand = [f for f in files if re.fullmatch(r"0*%d\.[A-Za-z0-9]+" % i, f)]
    if not cand:
        return None
    try:
        body = _sync_get("images/%s/%s" % (name, cand[0]), timeout=30)
    except Exception as e:  # noqa: BLE001
        log.debug("预览帧拉不下来 %s/%s: %s", name, cand[0], e)
        return None
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / (cand[0].rsplit(".", 1)[0] + ".png")
    # 缩略图压到 200px 再存：网页里只显示 60px，原图存着白占 18MB（实测 76 张就 18MB）
    try:
        im = Image.open(io.BytesIO(body))
        im.load()
        im = im.convert("RGBA")
        # 原图尺寸得记下来：预览缓存是缩过的，而参数表里的坐标是**原图坐标**，
        # 合成预览动图时按 orig 算缩放，不然头像会贴到画布外面去（2026-10-27）。
        _meta_set(name, im.size, first=(i == 0))
        im.thumbnail((200, 200), Image.LANCZOS)
        im.save(out, "PNG", optimize=True)
    except Exception:  # noqa: BLE001
        out = cache / cand[0]
        out.write_bytes(body)
    return out


def _meta_path(name):
    return _pdir() / "prev" / name / "meta.json"


def _meta(name):
    try:
        return json.loads(_meta_path(name).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _meta_set(name, size=None, first=True, **kw):
    d = _meta(name)
    if size:
        # 只认**第 0 帧**的尺寸：同一个模板各帧大小可能不一样（distracted 的 0.png 是
        # 220×88，1/2/3.png 是 500×500 的透明层），拿后面的帧当基准，坐标就全错了。
        if first or not d.get("orig"):
            d["orig"] = [int(size[0]), int(size[1])]
    d.update(kw)
    try:
        p = _meta_path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return d


# 缩略图预取：网页要一次显示 99 张，不能让请求线程去等网络（单线程会串成两分钟）。
_THUMB = {"running": False, "done": 0, "total": 0, "started": 0.0, "ended": 0.0}


def thumb_status():
    d = dict(_THUMB)
    try:
        have = sum(1 for p in (_pdir() / "prev").iterdir() if p.is_dir())
    except Exception:  # noqa: BLE001
        have = 0
    d["cached"] = have
    return d


def thumb_prefetch(names=None, workers=4):
    """后台把「还没缓存的第一帧」拉下来。已经缓存的跳过，所以重复调很便宜。"""
    if _THUMB["running"]:
        return {"ok": True, "already": True}
    try:
        listing = _up_listing()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}
    todo = []
    for name in (names or sorted(listing)):
        if frame_path(name, 0) is None:
            todo.append(name)
    if not todo:
        return {"ok": True, "nothing": True, "total": 0}
    _THUMB.update(running=True, done=0, total=len(todo), started=time.time(), ended=0.0)

    def _one(n):
        try:
            _fetch_frame(n, 0)
        except Exception:  # noqa: BLE001
            pass
        _THUMB["done"] = _THUMB["done"] + 1

    def _work():
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_one, todo))
        _THUMB["running"] = False
        _THUMB["ended"] = time.time()
        log.info("petpet 缩略图预取完成：%d 个（%.0fs）",
                 _THUMB["done"], _THUMB["ended"] - _THUMB["started"])

    threading.Thread(target=_work, name="petpet-thumb", daemon=True).start()
    return {"ok": True, "started": True, "total": len(todo)}


# ── 市场列表 ────────────────────────────────────────────────────────────

def catalog(refresh=False):
    """上游全部模板 + 本机状态，给网页「素材市场」用。

    status: ready（本机已有且能用）/ portable（能自动搬）/ manual（得人来）
    refresh=True 时强制重拉上游清单和源码（网页那个「刷新清单」按钮）。
    """
    try:
        listing = _up_listing(force=refresh)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "拉不到上游清单：%s" % e}
    try:
        if refresh:
            _ANALYZE_CACHE["key"] = None
        analyzed = _up_analyze(force=refresh)
    except Exception as e:  # noqa: BLE001
        log.warning("上游 functions.py 拿不到（只影响「能不能自动搬」的判断）: %s", e)
        analyzed = {}
    ported = set(ported_names())
    removed = set(removed_names())
    try:
        from qbot import qbot_petpet_port as PORT
        cn = PORT.CN_NAMES
    except Exception:  # noqa: BLE001
        cn = {}
    out = []
    busy = _PORT["name"] if _PORT.get("running") else ""
    for name in sorted(listing):
        files = listing[name]
        spec = analyzed.get(name) or {}
        why = spec.get("_why") or ""
        if has_assets(name):
            status, why = "ready", ""
        elif name in ported and name in TEMPLATES:
            status, why = "ready", ""
        elif spec and "_why" not in spec:
            # 参数抠出来了，但坐标得落在画面里才算「能搬」——
            # distracted 的 y=320 配 88 高的图，搬进来就是一张没头像的原图。
            if _loc_ok(name, spec):
                status = "portable"
            else:
                status, why = "manual", "坐标不在画面里（得手工重写位置）"
        else:
            status = "manual"
        if name == busy:
            # 正在搬的这个：素材是边下边落盘的，0.png 一到就会 has_assets 变 True，
            # 于是列表提前显示「已搬」—— 其实后面几十张还在下。这里压回「可搬」。
            status, why = "portable", "正在搬…"
        out.append({
            # 名字：本机模板的中文名 → 解析出来的 → 上游函数名对应的中文名 → 原 id
            # （「需手工」那批也得有个中文名，不然整屏 my_friend / name_generator 看不出是啥）
            "id": name, "name": (TEMPLATES.get(name) or {}).get("name")
            or spec.get("name") or cn.get(name) or name,
            "files": len(files), "status": status, "why": why,
            "removed": name in removed,
            # 预览翻几帧用：本机有的按参数，没有的按解析出来的（再不行按文件数）
            "frames": (TEMPLATES.get(name) or {}).get("frames")
            or spec.get("frames") or 0,
            "words": (TEMPLATES.get(name) or {}).get("words") or [],
            # 第一帧在不在本地/缓存里（不在就先显示占位，后台预取完再回来）
            "thumb": frame_path(name, 0) is not None,
            # 动图预览：`demo` = 已经生成好了（网页直接拿来当 src）；
            # `demo_possible` = 有编号帧、能生成（有参数就贴头像真渲，没参数就循环播原帧）。
            # 纯文字模板只有字体/背景图，没有编号帧 ⇒ False，网页就不会一直等它。
            "demo": _demo_path(name).exists(),
            "demo_possible": bool(_frame_ids(files)),
        })
    # 顺手把缺的缩略图 / 动图排上（已经在跑就秒返回，不会重复跑）
    try:
        thumb_prefetch()
    except Exception as e:  # noqa: BLE001
        log.debug("缩略图预取没起来: %s", e)
    try:
        demo_prefetch()
    except Exception as e:  # noqa: BLE001
        log.debug("预览动图预取没起来: %s", e)
    return {
        "ok": True,
        "items": out,
        "total": len(out),
        "ready": sum(1 for x in out if x["status"] == "ready"),
        "portable": sum(1 for x in out if x["status"] == "portable"),
        "manual": sum(1 for x in out if x["status"] == "manual"),
        "removed": sorted(removed),
        "thumbs": thumb_status(),
        "demos": demo_status(),
        # 示范动图的版本号：参数表一动就变，网页把它拼到 URL 后面，
        # 免得浏览器按 max-age=600 一直用重搬之前那张旧动图。
        "v": int(_pjson_mtime()),
        "source": "github.com/noneplugin/nonebot-plugin-petpet（v0.3.x）",
    }


# ── 搬过来 / 移除 ───────────────────────────────────────────────────────

_DUMMY = None


_DEMO_FILE = "demo.gif"
_DEMO_LOCK = threading.Lock()
# 后台「给整页模板生成动图预览」的进度（网页上显示「动图 12/99」）
_DEMO = {"running": False, "done": 0, "total": 0, "name": "", "built": 0,
         "failed": 0, "started": 0.0, "ended": 0.0}


def _demo_path(name):
    return _pdir() / "prev" / name / _DEMO_FILE


def _frame_ids(files):
    """上游文件清单 → 帧号（升序去重）。`1.png` / `01.png` / `001.png` 都算 1。"""
    out = set()
    for f in files or []:
        m = re.fullmatch(r"0*(\d+)\.[A-Za-z0-9]+", str(f))
        if m:
            out.add(int(m.group(1)))
    return sorted(out)


def _sample(ids, cap):
    """帧太多就均匀抽 cap 张（printing 有 115 个文件，全下要等到天荒地老）。"""
    ids = list(ids)
    if cap <= 0 or len(ids) <= cap:
        return ids
    step = len(ids) / float(cap)
    return [ids[min(len(ids) - 1, int(i * step))] for i in range(cap)]


def _demo_plan(name, analyzed=None):
    """这个模板怎么出预览动图 → (mode, spec, idxs)。

    · render：参数有（本机的 / 从上游抠出来的）→ 贴个假头像真渲一遍，看到的就是搬进来以后的样子
    · raw   ：参数抠不出来（需手工那批）→ 把上游那几帧本身循环播，至少能看出这是个啥
    · none  ：连帧都凑不齐
    """
    try:
        listing = _up_listing()
    except Exception:  # noqa: BLE001
        listing = {}
    ids = _frame_ids(listing.get(name))
    spec = TEMPLATES.get(name)
    if spec:
        n = int(spec.get("frames") or 0)
        seq = spec.get("seq") or list(range(n))
        seq = [i for i in seq if not n or i < n]
        return "render", spec, _sample(seq, 24)
    if analyzed is None:
        try:
            analyzed = _up_analyze()
        except Exception:  # noqa: BLE001
            analyzed = {}
    s = analyzed.get(name) or {}
    if s and "_why" not in s and _loc_ok(name, s):
        n = int(s.get("frames") or 0)
        if n:
            ids = [i for i in ids if i < n]
        return "render", s, _sample(ids, 24)
    return ("raw", None, _sample(ids, 6)) if ids else ("none", None, [])


def _scale_spec(spec, s):
    """把「原图坐标」的参数整体缩到预览缓存的尺寸（s = 缓存宽 / 原图宽）。

    预览缓存里的帧是缩到 200px 存的，而参数表里的坐标是原图坐标 ——
    不缩就直接贴，头像会跑到画布外面（2026-10-27）。
    """
    if not s or abs(s - 1.0) < 1e-6:
        return spec

    def num(v):
        try:
            return max(1, int(round(float(v) * s)))
        except Exception:  # noqa: BLE001
            return v

    def one(loc):
        try:
            return tuple(num(v) for v in loc)
        except Exception:  # noqa: BLE001
            return loc

    out = dict(spec)
    locs = spec.get("locs") or []
    if spec.get("multi"):
        out["locs"] = [[one(l) for l in frame] if isinstance(frame, (list, tuple))
                       else one(frame) for frame in locs]
    else:
        out["locs"] = [one(l) if l else l for l in locs]
    for k in ("resize", "resize_keep"):
        if spec.get(k):
            out[k] = one(spec[k])
    if spec.get("y_shift"):
        out["y_shift"] = int(round(spec["y_shift"] * s))
    if spec.get("sender_locs"):
        out["sender_locs"] = [tuple(list(one(l[:4])) + list(l[4:]))
                              for l in spec["sender_locs"]]
    return out


def _demo_stale(name, mode):
    out = _demo_path(name)
    try:
        t = out.stat().st_mtime
    except Exception:  # noqa: BLE001
        return True
    if mode == "render":
        # 参数表比动图新 ⇒ 重建。判据用 **templates.json 的 mtime**（每次搬/移除都会重写它），
        # 以前比的是 _PORT_FILE，可它只在**网页异步搬**时才更新 —— 直接调 import_template()
        # 重搬的模板会留着上一版的老动图（2026-10-27 实测：改了旋转顺序，网页上还是旧的）。
        try:
            if t < _pjson_mtime():
                return True
        except Exception:  # noqa: BLE001
            pass
    # 缓存里的帧比动图新（重新下过 / 刚补了几帧）也要重做
    try:
        d = _pdir() / "prev" / name
        newest = max((p.stat().st_mtime for p in d.iterdir()
                      if p.suffix.lower() != ".gif" and p.name != "meta.json"), default=0)
    except Exception:  # noqa: BLE001
        newest = 0
    return newest > t


def _build_demo(name, mode, spec, idxs, max_side=200):
    """真渲 / 真拼一张 GIF 存到 `prev/<name>/demo.gif`。**不联网**（帧不在就放弃）。"""
    out = _demo_path(name)
    have = [i for i in idxs if frame_path(name, i) is not None]
    if not have:
        return None

    def get(i):
        with Image.open(frame_path(name, i)) as im:
            return im.convert("RGBA")

    if mode == "render":
        s = 1.0
        if not has_assets(name):
            orig = (_meta(name).get("orig") or [0, 0])
            try:
                with Image.open(frame_path(name, have[0])) as im:
                    w0 = im.size[0]
            except Exception:  # noqa: BLE001
                w0 = 0
            if not (orig and orig[0] and w0):
                # 不知道原图多大就没法把坐标缩到缓存尺寸 —— 宁可不出图，也不贴个歪头像
                log.debug("预览动图跳过 %s：缺原图尺寸（等后台补 meta）", name)
                return None
            s = float(w0) / float(orig[0])
        try:
            frames = _render(name, _dummy_avatar(), spec=_scale_spec(spec, s),
                             frame_fn=get, seq=have)
        except Exception as e:  # noqa: BLE001
            log.debug("合成示范动图失败 %s: %s", name, e)
            return None
        duration = int((spec or {}).get("duration") or 60)
    else:
        frames = []
        for i in have:
            try:
                frames.append(get(i))
            except Exception:  # noqa: BLE001
                continue
        duration = 120
    if not frames:
        return None
    # 缩到 200px 以内再存：上游有大到 1080×1080 的，原尺寸 gif 一张能上 MB
    w, h = frames[0].size
    scale = min(1.0, float(max_side) / max(w, h))
    if scale < 1.0:
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        frames = [f.resize((nw, nh), Image.LANCZOS) for f in frames]
    flat = []
    for f in frames:
        bg = Image.new("RGB", f.size, "white")
        bg.paste(f, (0, 0), f)
        flat.append(bg.convert("P", palette=Image.ADAPTIVE, colors=128))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    try:
        flat[0].save(tmp, format="GIF", save_all=True, append_images=flat[1:],
                     duration=max(30, int(duration)), loop=0, optimize=True, disposal=2)
        tmp.replace(out)
    except Exception as e:  # noqa: BLE001
        log.debug("存示范动图失败 %s: %s", name, e)
        return None
    return out


def demo_gif(name, max_side=200):
    """网页上那张会自己动的预览图。本机已有的按真参数渲；还没搬的按抠出来的参数渲；
    抠不出参数的（需手工那批）就把上游几帧循环播。

    为什么要这个：网页上点缩略图翻帧，只能翻到缓存里那一帧，翻到没有的就 404 ⇒
    浏览器显示破图，看起来就是「黑一下、闪一下、又黑」（2026-10-27 主人反馈）。
    干脆让服务端出 GIF，`<img>` 拿到自己就会动，不用点也不会闪。
    """
    name = re.sub(r"[^A-Za-z0-9_\-]", "", str(name or ""))
    if not name:
        return None
    mode, spec, idxs = _demo_plan(name)
    if mode == "none":
        return None
    out = _demo_path(name)
    if out.exists() and not _demo_stale(name, mode):
        return out
    return _build_demo(name, mode, spec, idxs, max_side)


def demo_status():
    d = dict(_DEMO)
    try:
        d["cached"] = sum(1 for p in (_pdir() / "prev").glob("*/" + _DEMO_FILE))
    except Exception:  # noqa: BLE001
        d["cached"] = 0
    return d


def _ensure_orig(name, idxs):
    """预览缓存是缩过的，得知道**原图**多大才能把参数表的坐标缩回去。

    老缓存（2026-10-27 之前下的）没有 meta.json，这里补一张把尺寸记下来。
    """
    if has_assets(name) or (_meta(name).get("orig") or [0, 0])[0]:
        return True
    for i in (idxs[:1] or [0]):
        if _fetch_frame(name, i) and (_meta(name).get("orig") or [0, 0])[0]:
            return True
    return False


def demo_prefetch(workers=4):
    """后台把整页 99 个模板的预览动图都做出来（网页上就不用一格一格等）。

    只下**预览用**的帧（缩到 200px 存，一张几十 KB），不是搬素材：
    全量 99 个约 600~700 个文件，4 线程跑几分钟，占几十 MB 磁盘。
    """
    if _DEMO["running"]:
        return {"ok": True, "already": True}
    try:
        listing = _up_listing()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}
    try:
        analyzed = _up_analyze()
    except Exception:  # noqa: BLE001
        analyzed = {}
    plan = []
    for name in sorted(listing):
        mode, spec, idxs = _demo_plan(name, analyzed)
        if mode == "none" or not idxs:
            continue
        # 没记原图尺寸的（老缓存）也要过一遍：不补 meta 就没法判断「坐标是不是出界」，
        # 会一直把它当「能搬」，点下去才失败（distracted 就是这样）。
        need_meta = (not has_assets(name)
                     and not ((_meta(name).get("orig") or [0, 0])[0]))
        if _demo_path(name).exists() and not _demo_stale(name, mode) and not need_meta:
            continue
        plan.append((name, mode, spec, idxs))
    if not plan:
        return {"ok": True, "nothing": True, "total": 0}
    _DEMO.update(running=True, done=0, total=len(plan), name="",
                 built=0, failed=0, started=time.time(), ended=0.0)

    def _one(item):
        name = item[0]
        _DEMO["name"] = name
        ok = False
        try:
            # 先把「原图多大」补齐（老缓存没记），再重新判断怎么渲 ——
            # 尺寸没到手前 `_loc_ok` 是放行的，可能判成 render，补完才知道该走 raw。
            _ensure_orig(name, item[3])
            mode, spec, idxs = _demo_plan(name, analyzed)
            if mode == "none" or not idxs:
                with _DEMO_LOCK:
                    _DEMO["done"] = _DEMO["done"] + 1
                    _DEMO["failed"] = _DEMO["failed"] + 1
                return
            for i in idxs:
                if frame_path(name, i) is None:
                    _fetch_frame(name, i)
            ok = _build_demo(name, mode, spec, idxs) is not None
        except Exception as e:  # noqa: BLE001
            log.debug("预览动图失败 %s: %s", name, e)
        with _DEMO_LOCK:
            _DEMO["done"] = _DEMO["done"] + 1
            _DEMO["built" if ok else "failed"] = _DEMO["built" if ok else "failed"] + 1

    def _work():
        from concurrent.futures import ThreadPoolExecutor
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(_one, plan))
        finally:
            _DEMO.update(running=False, name="", ended=time.time())
            log.info("petpet 预览动图：成 %d / 败 %d / 共 %d（%.0fs）",
                     _DEMO["built"], _DEMO["failed"], _DEMO["total"],
                     _DEMO["ended"] - _DEMO["started"])

    threading.Thread(target=_work, name="petpet-demo", daemon=True).start()
    return {"ok": True, "started": True, "total": len(plan)}


def _dummy_avatar():
    """自检用的假头像：一个带十字的圆，用来验证「搬进来能不能真出图」。"""
    global _DUMMY
    if _DUMMY is not None:
        return _DUMMY
    from PIL import ImageDraw
    im = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, 319, 319), fill=(240, 120, 60, 255))
    d.line((160, 40, 160, 280), fill=(255, 255, 255, 255), width=10)
    d.line((40, 160, 280, 160), fill=(255, 255, 255, 255), width=10)
    _DUMMY = im
    return im


def _avatar_seen(frames):
    """头像到底有没有出现在画面里（数一下洋红像素）。

    `distracted` 那种坐标 (140,320) 配 88 高的画布，渲出来就是「一张没有头像的原图」——
    不检查的话搬进来是个废模板，群友还会以为没生效（2026-10-27 实测）。
    """
    for f in frames:
        if f.convert("RGB").tobytes().count(b"\xff\x00\xff") >= 30:
            return True
    return False


def _loc_ok(name, spec):
    """坐标是不是落在画面里（便宜版检查，网页列清单时用，不用真渲）。

    ⚠️ 只拿**原图**尺寸比：预览缓存是缩到 200px 的，拿它比会把正常模板误判成「坐标出界」。
    原图尺寸还不知道就先放行（后台会补 meta，补完下一轮就准了）。
    """
    orig = _meta(name).get("orig") or [0, 0]
    if not orig[0] or not orig[1]:
        return True
    w0, h0 = orig[0], orig[1]
    locs = spec.get("locs") or []
    if spec.get("multi"):
        flat = []
        for fr in locs:
            flat.extend(fr if isinstance(fr, (list, tuple)) else [fr])
        locs = flat
    whxy = spec.get("loc_order") == "whxy"
    for l in locs:
        if not l or len(l) < 2:
            continue
        x, y = (l[2], l[3]) if (whxy and len(l) >= 4) else (l[0], l[1])
        if x >= w0 or y >= h0:
            return False
    return True


def _verify(name, spec):
    """真渲一帧出来看：能不能开图、尺寸对不对、是不是一片空白。

    自动抠出来的参数**必须过这一关**才允许落盘 —— 猜错的后果是群友被贴一脸歪头像。
    """
    try:
        frames = _render(name, _dummy_avatar())
    except Exception as e:  # noqa: BLE001
        return False, "试渲失败：%s: %s" % (type(e).__name__, e)
    if not frames:
        return False, "试渲没有出帧"
    f0 = frames[0]
    if f0.width < 40 or f0.height < 40:
        return False, "渲出来太小（%dx%d），参数八成不对" % (f0.size)
    ex = f0.convert("RGB").getextrema()
    if all(a == b for a, b in ex):
        return False, "渲出来是纯色，参数八成不对"
    # 再用洋红头像渲一遍：一个像素都看不见 ⇒ 坐标压根不在画面里
    try:
        mk = Image.new("RGBA", (320, 320), (255, 0, 255, 255))
        if not _avatar_seen(_render(name, mk)):
            return False, "头像压根没落在画面里（位置表不对）"
    except Exception as e:  # noqa: BLE001
        log.debug("洋红自检没跑成 %s: %s", name, e)
    return True, "%d 帧，%dx%d" % (len(frames), f0.width, f0.height)


def import_template(name):
    """把一个上游模板搬进来：下帧图 → 解析参数 → 试渲 → 记账。

    ⚠️ 同步版（一次几十秒，网页别直接等）：网页走 `import_start()` + `market_status()` 轮询。
    """
    name = re.sub(r"[^A-Za-z0-9_\-]", "", str(name or ""))
    if not name:
        return {"ok": False, "error": "模板名不对"}
    if name in TEMPLATES and has_assets(name):
        return {"ok": False, "error": "「%s」本机已经有了" % name}
    listing = _up_listing()
    files = listing.get(name) or []
    if not files:
        return {"ok": False, "error": "上游没有这个模板：%s" % name}
    # 1) 参数：能自动抠就自动，抠不出来就不搬（不猜）
    spec = None
    try:
        spec = (_up_analyze().get(name) or {})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "拿不到上游源码：%s" % e}
    if not spec or spec.get("_why"):
        return {"ok": False, "error": "这个得人来搬：%s"
                % ((spec or {}).get("_why") or "上游没有这个模板")}
    # 2) 帧图（并发拉，串行太慢：镜像单文件要 5~10 秒，20 帧就是两分钟）
    dst = ASSETS / name
    todo = [f for f in files if not (dst / f).exists()]
    got = len(files) - len(todo)
    fail = 0
    _PORT["msg"] = "拉素材 %d/%d…" % (got, len(files))
    _PORT["total"] = len(files)
    _PORT["done"] = got
    if todo:
        from concurrent.futures import ThreadPoolExecutor

        def _one(f):
            try:
                body = _sync_get("images/%s/%s" % (name, f), timeout=60)
            except Exception as e:  # noqa: BLE001
                log.warning("搬 %s/%s 失败: %s", name, f, e)
                return False
            p = dst / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)
            return True

        with ThreadPoolExecutor(max_workers=4) as ex:
            for ok in ex.map(_one, todo):
                _PORT["done"] = _PORT["done"] + 1
                _PORT["msg"] = "拉素材 %d/%d…" % (_PORT["done"], len(files))
                if ok:
                    got += 1
                else:
                    fail += 1
    if fail or not got:
        return {"ok": False, "error": "素材没下全（%d 个失败 / 共 %d 个），先不记账"
                % (fail, len(files))}
    # 3) 落盘 + 试渲
    _PORT["msg"] = "试渲一帧…"
    TEMPLATES[name] = dict(spec)
    ok, why = _verify(name, spec)
    if not ok:
        TEMPLATES.pop(name, None)
        _rebuild()
        return {"ok": False, "error": "搬过来了但%s，已退回（素材留在本地）" % why}
    extra = _pjson(_PORT_FILE, {}) or {}
    extra[name] = dict(spec)
    _pwrite(_PORT_FILE, extra)
    rem = [x for x in removed_names() if x != name]
    _pwrite(_REMOVED_FILE, rem)
    _load_overrides()
    log.info("petpet 搬入模板 %s（%d 个文件，%s）", name, got, why)
    return {"ok": True, "id": name, "name": spec.get("name") or name,
            "files": got, "detail": why}


# 网页「搬过来」是异步的：一次要几十秒，同步等会把浏览器/代理等超时。
_PORT = {"running": False, "name": "", "msg": "", "done": 0, "total": 0,
         "result": None, "started": 0.0, "ended": 0.0}


def import_start(name):
    name = re.sub(r"[^A-Za-z0-9_\-]", "", str(name or ""))
    if _PORT["running"]:
        return {"ok": False, "error": "上一个还在搬（%s），等它完" % (_PORT["name"] or "?")}
    if not name:
        return {"ok": False, "error": "模板名不对"}
    if name in TEMPLATES and has_assets(name):
        return {"ok": False, "error": "「%s」本机已经有了" % name}
    _PORT.update(running=True, name=name, msg="排队中…", done=0, total=0,
                 result=None, started=time.time(), ended=0.0)

    def _work():
        try:
            r = import_template(name)
        except Exception as e:  # noqa: BLE001
            log.warning("搬模板 %s 炸了: %s", name, e)
            r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        _PORT["result"] = r
        _PORT["msg"] = ("搬好了：" + (r.get("detail") or "")) if r.get("ok") \
            else (r.get("error") or "没搬成")
        _PORT["running"] = False
        _PORT["ended"] = time.time()

    threading.Thread(target=_work, name="petpet-port", daemon=True).start()
    return {"ok": True, "started": True, "name": name}


def market_status():
    d = dict(_PORT)
    d["ok"] = True
    d["ported"] = ported_names()
    d["removed"] = removed_names()
    return d


def remove_template(name, hard=False):
    """移除一个模板：素材挪到 assets/_removed/，并记账（同步/自动搬都不会再加回来）。

    内置那 13 个也允许移除（主人：「原有的，不喜欢了也可以移除吗」）—— 素材只是挪走不是删，
    `restore_template()` 能原样放回来。hard=True 才连 `_removed/` 那份一起删。
    """
    name = re.sub(r"[^A-Za-z0-9_\-]", "", str(name or ""))
    if not name or (name not in TEMPLATES and not has_assets(name)):
        return {"ok": False, "error": "没有这个模板：%s" % name}
    src = ASSETS / name
    if src.exists():
        if hard:
            import shutil
            shutil.rmtree(src, ignore_errors=True)
        else:
            import shutil
            grave = ASSETS / "_removed" / name
            if grave.exists():
                shutil.rmtree(grave, ignore_errors=True)
            grave.parent.mkdir(parents=True, exist_ok=True)
            src.replace(grave)
    rem = removed_names()
    if name not in rem:
        rem.append(name)
    _pwrite(_REMOVED_FILE, sorted(rem))
    # ⚠️ templates.json 里那条**不删**：它记的是「这模板的参数长什么样」，
    # 删了以后 `restore_template()` 就没东西可放回来了。可见与否由 removed.json 说了算。
    TEMPLATES.pop(name, None)
    _rebuild()
    log.info("petpet 移除模板 %s（%s）", name, "连素材删掉" if hard else "素材挪到 _removed/")
    return {"ok": True, "id": name, "hard": bool(hard)}


def restore_template(name):
    """把移除过的模板放回来（素材在 _removed/ 里的话一起搬回）。"""
    name = re.sub(r"[^A-Za-z0-9_\-]", "", str(name or ""))
    rem = removed_names()
    if name not in rem:
        return {"ok": False, "error": "「%s」没被移除过" % name}
    grave = ASSETS / "_removed" / name
    if grave.exists() and not (ASSETS / name).exists():
        grave.replace(ASSETS / name)
    rem = [x for x in rem if x != name]
    _pwrite(_REMOVED_FILE, rem)
    extra = _pjson(_PORT_FILE, {}) or {}
    spec = extra.get(name)
    if spec:
        TEMPLATES[name] = dict(spec)
    elif name in _PRISTINE:
        TEMPLATES[name] = dict(_PRISTINE[name])
    _rebuild()
    return {"ok": True, "id": name}


# ── 跟站更新（2026-10-26 晚）─────────────────────────────────────────────
# 主人问「是本机素材，没有网站吗？不能跟随网站更新嘛」——**有站**：
# 上游 noneplugin/nonebot-plugin-petpet（MIT），99 个模板 / 1091 个文件，
# 本机当初只缓存了 13 个（`tools/_fetch_petpet_assets.py` 是手跑的，跑完就再没对过）。
#
# 能自动对的：**素材本身**。上游 `resources/resource_list.json` 每个文件带 md5，
# 按 md5 比对就能做到「缺的补、改的换、没变的跳过」。
# 不能自动对的：**帧参数**（几帧、头像贴哪、叠前叠后）。那些在上游 Python 的
# functions.py 里是手写的 paste 坐标，图里没有 —— 所以上游新增的 86 个模板
# 只能「发现 + 报给你」，参数得人补进 TEMPLATES。这条同步就只做三件事：
#   ① 本地这 13 个模板的图跟上游 md5 对齐（上游修图会自动换上）
#   ② 统计上游有、本地没搬的模板（抽屉里显示数字，别让人以为只有 13 个）
#   ③ 记账（上次什么时候对的、这次换了几个文件）
UPSTREAM_REF = "v0.3.x"
_MIRRORS = (
    # 顺序有讲究：jsdelivr 快但偶尔 403/超时；ghproxy 稳但要 15~20 秒；
    # gh-proxy 兜底。_sync_get 会记住这次哪个通了，后面优先用它。
    "https://testingcf.jsdelivr.net/gh/noneplugin/nonebot-plugin-petpet@%s/resources/",
    "https://ghproxy.net/https://raw.githubusercontent.com/noneplugin/"
    "nonebot-plugin-petpet/%s/resources/",
    "https://gh-proxy.com/https://raw.githubusercontent.com/noneplugin/"
    "nonebot-plugin-petpet/%s/resources/",
    "https://fastly.jsdelivr.net/gh/noneplugin/nonebot-plugin-petpet@%s/resources/",
)
UA_SYNC = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
# 仓库根（不带 resources/）：素材市场要拉上游 `functions.py` 来解析参数。
# 和 _MIRRORS 同一套镜像，只是根不一样 —— 别混用，混了会去 resources/ 下面找 .py。
_MIRRORS_ROOT = (
    "https://testingcf.jsdelivr.net/gh/noneplugin/nonebot-plugin-petpet@%s/",
    "https://ghproxy.net/https://raw.githubusercontent.com/noneplugin/"
    "nonebot-plugin-petpet/%s/",
    "https://gh-proxy.com/https://raw.githubusercontent.com/noneplugin/"
    "nonebot-plugin-petpet/%s/",
    "https://fastly.jsdelivr.net/gh/noneplugin/nonebot-plugin-petpet@%s/",
)
_MIRROR_ROOT_IX = [0]
SYNC_SLEEP = 0.15               # 每次请求之间喘一口，别把镜像打急
SYNC_TIMEOUT = 45

_SYNC = {"running": False, "msg": "", "total": 0, "checked": 0, "added": 0,
         "started": 0.0, "ended": 0.0, "upstream_only": [], "upstream_total": 0}
_MIRROR_IX = [0]                # 这次哪个镜像通了（下标），下次先试它
# 每个镜像的实测表现：ms=平均耗时，bad=连续失败次数，until=冷却到什么时候。
# 以前只记「哪个通了」，ghproxy 一旦通了就永远排第一 —— 它单张要 5~10 秒，
# 缩略图、预览动图、搬素材全被它拖死（2026-10-27 实测 jsdelivr 只要 0.3~1.2 秒）。
_MIRROR_STAT = {}
_MIRROR_ROOT_STAT = {}


def bind(state_dir=None):
    """由 bot.py 启动时调一次；只影响记账文件放哪。"""
    global STATE
    if state_dir:
        STATE = Path(state_dir)
    _load_overrides()
    return str(STATE)


def _opener():
    # 国内镜像直连，绕开 Clash（没开代理时裸 urlopen 会 10061）
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _note_mirror(stat, i, sec, ok):
    st = stat.setdefault(i, {})
    if ok:
        ms = max(1.0, sec * 1000.0)
        prev = st.get("ms")
        st["ms"] = ms if prev is None else (prev * 0.6 + ms * 0.4)
        st["bad"] = 0
        st.pop("until", None)
    else:
        st["bad"] = int(st.get("bad", 0)) + 1
        st["until"] = time.time() + min(600, 60 * st["bad"])


def _mirror_order(n, stat):
    now = time.time()

    def key(i):
        st = stat.get(i) or {}
        cooling = 1 if st.get("until", 0) > now else 0
        return (cooling, 1 if st.get("bad") else 0, st.get("ms", 9999.0))

    return sorted(range(n), key=key)


def _get_mirror(table, stat, cur_ix, path, timeout):
    last = None
    for i in _mirror_order(len(table), stat):
        url = (table[i] % UPSTREAM_REF) + path
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=UA_SYNC)
            with _opener().open(req, timeout=timeout) as r:
                body = r.read()
            if body:
                cur_ix[0] = i
                _note_mirror(stat, i, time.time() - t0, True)
                return body
            last = RuntimeError("空响应")
            _note_mirror(stat, i, time.time() - t0, False)
        except Exception as e:  # noqa: BLE001
            last = e
            _note_mirror(stat, i, time.time() - t0, False)
    raise RuntimeError("镜像都拉不到 %s（%s）" % (path, last))


def _sync_get(path, timeout=SYNC_TIMEOUT):
    """按镜像链拉一个文件；按实测耗时排序，谁快先用谁。"""
    return _get_mirror(_MIRRORS, _MIRROR_STAT, _MIRROR_IX, path, timeout)


def _root_get(path, timeout=90):
    """拉仓库根下的文件（`functions.py` 这种）。和 `_sync_get` 同一套镜像，根不同。"""
    return _get_mirror(_MIRRORS_ROOT, _MIRROR_ROOT_STAT, _MIRROR_ROOT_IX, path, timeout)


def _md5_file(p):
    try:
        h = hashlib.md5()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return ""


def _read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_json(p, data):
    try:
        p = Path(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.debug("petpet 记账写失败: %s", e)


def _state_file():
    return Path(STATE) / "petpet" / "sync.json"


def local_ready():
    """本地素材齐了的模板（按 0.png 在不在算，跟 list_templates 一个口径）。"""
    return [k for k in TEMPLATES if has_assets(k)]


def sync_status():
    """给网页/自测看的：本地几个、上游几个、上次什么时候对的。"""
    st = _read_json(_state_file())
    return {"ok": True,
            "source": "github.com/noneplugin/nonebot-plugin-petpet（%s）" % UPSTREAM_REF,
            "templates": len(TEMPLATES),
            "local_ready": len(local_ready()),
            "upstream_total": int(_SYNC.get("upstream_total") or st.get("upstream_total") or 0),
            "upstream_only": list(_SYNC.get("upstream_only") or st.get("upstream_only") or []),
            "running": bool(_SYNC.get("running")),
            "msg": _SYNC.get("msg") or st.get("msg") or "",
            "checked": int(_SYNC.get("checked") or 0),
            "added": int(_SYNC.get("added") or 0),
            "last": st.get("at_str") or "",
            "last_ok": st.get("ok"),
            "auto": auto_status()}


def sync_start():
    if _SYNC.get("running"):
        return dict(sync_status(), ok=True, message="正在对，别急")
    _SYNC.update(running=True, msg="排队中…", total=0, checked=0, added=0,
                 started=time.time(), ended=0.0, upstream_only=[], upstream_total=0)
    threading.Thread(target=_sync_worker, name="petpet-sync", daemon=True).start()
    return dict(sync_status(), ok=True, message="已开始")


def _sync_worker():
    """后台对一遍。**只增不减、只换不改**：本地有且 md5 一致就跳过，绝不删文件。"""
    try:
        _SYNC["msg"] = "拉上游清单…"
        listing = json.loads(_sync_get("resource_list.json").decode("utf-8"))
        by_tpl = {}
        for it in listing or []:
            parts = str(it.get("path") or "").split("/")
            if len(parts) == 3 and parts[0] == "images":
                by_tpl.setdefault(parts[1], []).append(
                    (parts[2], str(it.get("hash") or "").lower()))
        _SYNC["upstream_total"] = len(by_tpl)
        want = [t for t in TEMPLATES if t in by_tpl]
        _SYNC["total"] = sum(len(by_tpl[t]) for t in want)
        added = checked = 0
        for tpl in want:
            for name, md5 in by_tpl[tpl]:
                dst = ASSETS / tpl / name
                checked += 1
                _SYNC.update(checked=checked, added=added)
                if dst.exists() and md5 and _md5_file(dst) == md5:
                    continue
                try:
                    body = _sync_get("images/%s/%s" % (tpl, name))
                except Exception as e:  # noqa: BLE001
                    _SYNC["msg"] = "%s/%s 拉不下来（%s）" % (tpl, name, type(e).__name__)
                    continue
                if md5 and hashlib.md5(body).hexdigest() != md5:
                    _SYNC["msg"] = "%s/%s md5 对不上，跳过" % (tpl, name)
                    continue
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(body)
                    added += 1
                except Exception as e:  # noqa: BLE001
                    _SYNC["msg"] = "写不进 %s/%s（%s）" % (tpl, name, e)
                _SYNC.update(checked=checked, added=added)
                time.sleep(SYNC_SLEEP)
        only = sorted(set(by_tpl) - set(TEMPLATES))
        _SYNC["upstream_only"] = only
        _SYNC["msg"] = ("对完了：%d 个模板 / %d 个文件，更新 %d 个；上游另有 %d 个模板没搬"
                        % (len(want), checked, added, len(only)))
        _SYNC["running"] = False
        _SYNC["ended"] = time.time()
        _write_json(_state_file(), {
            "ok": True, "at": time.time(),
            "at_str": time.strftime("%Y-%m-%d %H:%M", time.localtime()),
            "msg": _SYNC["msg"], "checked": checked, "added": added,
            "upstream_total": len(by_tpl), "upstream_only": only})
        log.info("petpet 素材同步: %s", _SYNC["msg"])
    except Exception as e:  # noqa: BLE001
        _SYNC.update(running=False, ended=time.time(),
                     msg="同步失败：%s" % str(e)[:120])
        log.warning("petpet 素材同步失败: %s", e)
        _write_json(_state_file(), {
            "ok": False, "at": time.time(),
            "at_str": time.strftime("%Y-%m-%d %H:%M", time.localtime()),
            "msg": _SYNC["msg"]})


# ── 每天自动对一遍 ──────────────────────────────────────────────────────
# 跟梗百科同款规矩：**先记账再跑**。上游/镜像挂掉时，不记账就会每 15 秒重试一次，
# 等于 DDoS 人家；记了账一天最多撞一次。
def _auto_state():
    return _read_json(Path(STATE) / "petpet" / "auto.json")


def auto_status():
    st = _auto_state()
    cfg = {}
    try:
        from qbot import qbot_skills
        cfg = qbot_skills.settings_of("petpet") or {}
    except Exception:  # noqa: BLE001
        pass
    return {"enabled": bool(cfg.get("autoSync", True)),
            "hour": int(cfg.get("autoSyncHour", 4) or 0),
            "date": st.get("date") or "",
            "msg": st.get("msg") or "",
            "added": st.get("added") or 0,
            "at": st.get("at") or 0}


def auto_tick(now=None):
    """由 bot.py 的 scheduler_loop 每 15 秒调一次；不到点/今天跑过就直接返回。"""
    st = auto_status()
    if not st["enabled"]:
        return False
    t = now or time.localtime()
    if t.tm_hour != st["hour"]:
        return False
    today = time.strftime("%Y-%m-%d", t)
    if st["date"] == today:
        return False
    if _SYNC.get("running"):
        return False
    if not (ASSETS / "petpet" / "0.png").exists():
        return False          # 一个模板都没有 = 从没下过素材，别让自动任务去灌 1000 个文件
    _write_json(Path(STATE) / "petpet" / "auto.json",
                {"date": today, "at": time.time(), "msg": "每天自动对一遍：已启动", "added": 0})
    sync_start()
    return True
