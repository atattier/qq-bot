"""图片混淆 / 解混淆（2026-10-06 主人要的）。

算法来自 https://iris10086.github.io/pic-scramble/ （「小番茄图片混淆」）：
用 **Gilbert 2D 广义空间填充曲线** 给画布上每个像素排出一个访问顺序，然后按
**黄金比例固定偏移** 把像素整段搬走：

    curve = gilbert2d(W, H)                     # 长度 W*H，第 i 项是第 i 个访问的坐标
    off   = round((√5-1)/2 * W * H)             # 只由画布大小决定
    混淆:  dst[curve[(i+off)%N]] = src[curve[i]]
    解混淆: dst[curve[i]]         = src[curve[(i+off)%N]]

**没有密钥**：任何人都能还原，所以它是「防扫描/防 AI 识图 / 恶搞」级别的，
不是加密。曲线保持空间相关性 ⇒ JPEG 压缩后仍能认出来（网页端就输出 jpeg）。

⚠️ **尺寸必须一模一样才能还原**（curve 和 off 都由 W/H 算出）。
平台一旦缩放/裁剪就还原不出来 —— 所以 `descamble()` 允许手工指定原始宽高兜底。

本模块只依赖 **PIL + numpy**（可选，缺 numpy 走慢速纯 Python 兜底），
不碰网络、不碰 ComfyUI、纯本地算。
"""

import json
import math
import os
import time
import uuid
from pathlib import Path

from PIL import Image

try:
    import numpy as _np
except Exception:      # 没 numpy 也能跑，只是慢
    _np = None

GOLDEN = (math.sqrt(5) - 1.0) / 2.0     # 0.6180339887...

CACHE_DIR = Path(__file__).resolve().parent / "state" / "scramble" / "curve"


class ScrambleError(Exception):
    """给 bot 直接转成群消息的错（别把 traceback 抖给群友）。"""


# ---------------------------------------------------------------- 曲线
def _sgn(v):
    return (v > 0) - (v < 0)


_CURVE_CACHE = {}          # (w,h) -> 索引数组（flatten 位置 ys*w+xs）
_CURVE_CACHE_MAX = 4
_AR = _np.arange(8192, dtype=_np.int32) if _np is not None else None


def _make_gen(W):
    """给一个画布宽度 W 生成专用递归函数（闭包带 W，少传一个参数）。

    ⚠️ **性能关键（实测 1536x1024：12.5s -> 1.2s，快 10 倍）**：
    递归叶子段里 90% 长度只有 1~2 个像素，而 numpy 切片赋值每次固定开销约 5µs，
    比 Python 循环还贵。所以短段（<32）走纯 Python 逐像素写，长段才用 numpy 整段算。
    ⚠️ 还有一处别动：结果必须是**单一 flatten 索引数组**（ys*w+xs）而不是 xs/ys 两个 ——
    两份数组的写法多一倍写放大，且短段要多写一次乘法。
    """
    def _gen(x, y, ax, ay, bx, by, out, i):
        # 逐行对照网页 JS 的 generate2d（唯一差别：写预分配数组，不 push）
        w = abs(ax + ay)
        h = abs(bx + by)
        dax, day = _sgn(ax), _sgn(ay)
        dbx, dby = _sgn(bx), _sgn(by)

        if h == 1:                                   # 整行填充
            if w >= 32:
                out[i:i + w] = (y + _AR[:w] * day) * W + (x + _AR[:w] * dax)
            else:
                b = y * W + x
                s = day * W + dax
                for k in range(w):
                    out[i + k] = b
                    b += s
            return i + w
        if w == 1:                                   # 整列填充
            if h >= 32:
                out[i:i + h] = (y + _AR[:h] * dby) * W + (x + _AR[:h] * dbx)
            else:
                b = y * W + x
                s = dby * W + dbx
                for k in range(h):
                    out[i + k] = b
                    b += s
            return i + h

        ax2 = ax // 2
        ay2 = ay // 2
        bx2 = bx // 2
        by2 = by // 2
        w2 = abs(ax2 + ay2)
        h2 = abs(bx2 + by2)

        if 2 * w > 3 * h:                           # long case：只拆两段
            if (w2 % 2) and (w > 2):
                ax2 += dax
                ay2 += day
            i = _gen(x, y, ax2, ay2, bx, by, out, i)
            return _gen(x + ax2, y + ay2, ax - ax2, ay - ay2, bx, by, out, i)

        if (h2 % 2) and (h > 2):                     # standard case：一步上+长横+一步下
            bx2 += dbx
            by2 += dby
        i = _gen(x, y, bx2, by2, ax2, ay2, out, i)
        i = _gen(x + bx2, y + by2, ax, ay, bx - bx2, by - by2, out, i)
        return _gen(x + (ax - dax) + (bx2 - dbx), y + (ay - day) + (by2 - dby),
                    -bx2, -by2, -(ax - ax2), -(ay - ay2), out, i)

    return _gen


def _curve_build(width, height):
    """现算曲线。返回长度 W*H 的 int32 索引数组（第 i 项 = 第 i 个访问到的像素位置）。"""
    n = int(width) * int(height)
    if n <= 0:
        raise ScrambleError("画布大小非法（%sx%s）" % (width, height))
    out = _np.empty(n, dtype=_np.int32)
    gen = _make_gen(int(width))
    if width >= height:
        gen(0, 0, int(width), 0, 0, int(height), out, 0)
    else:
        gen(0, 0, 0, int(height), int(width), 0, out, 0)
    return out


def _curve_disk(width, height):
    return CACHE_DIR / ("c_%dx%d.npy" % (int(width), int(height)))


def curve(width, height):
    """Gilbert 2D 曲线的像素索引序列（内存 LRU -> 磁盘 npy -> 现算）。"""
    if _np is None:
        return None
    key = (int(width), int(height))
    hit = _CURVE_CACHE.get(key)
    if hit is not None:
        return hit
    n = int(width) * int(height)
    if n <= 0:
        raise ScrambleError("画布大小非法（%sx%s）" % (width, height))
    p = _curve_disk(*key)
    arr = None
    try:
        if p.is_file():
            arr = _np.load(str(p))
            if arr.size != n:            # 尺寸对不上/损坏，当没有
                arr = None
    except Exception:
        arr = None
    if arr is None:
        arr = _curve_build(width, height)
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            # ⚠️ np.save 会自动补 ".npy" 后缀 ⇒ 必须传**不带后缀**的路径，
            #    写成 c_1536x1024.npy 的话实际落成 c_1536x1024.npy.npy，下次永远读不到缓存。
            tmp = CACHE_DIR / ("tmp_%d_%dx%d" % (int(time.time() * 1000),
                                                int(width), int(height)))
            _np.save(str(tmp), arr)
            tmp.with_suffix(".npy").replace(p)   # 原子落盘，别留半个文件给下次读
        except Exception:
            pass
    if len(_CURVE_CACHE) >= _CURVE_CACHE_MAX:
        _CURVE_CACHE.pop(next(iter(_CURVE_CACHE)))
    _CURVE_CACHE[key] = arr
    return arr


def offset_for(width, height):
    """黄金比例偏移（JS 的 Math.round 是四舍五入，Python 的 round 是银行家舍入，
    这里必须显式写 floor(x+0.5)，否则大图会差 1 个像素）。"""
    n = int(width) * int(height)
    off = int(math.floor(GOLDEN * n + 0.5)) % n
    return off or 1 if n > 1 else 0


# ---------------------------------------------------------------- 图像 IO
def _fit(w, h, max_side, max_pixels):
    """按需缩小到限制内；返回 (w, h)。只缩不放（放大会改像素数 = 破坏还原）。"""
    w, h = int(w), int(h)
    sc = 1.0
    if max_side and max(w, h) > max_side:
        sc = max_side / float(max(w, h))
    if max_pixels and w * h * sc * sc > max_pixels:
        sc = math.sqrt(max_pixels / float(w * h))
    if sc < 1.0:
        w = max(1, int(w * sc))
        h = max(1, int(h * sc))
    return w, h


def load_rgb(path):
    """读图 -> RGB numpy 数组 (h, w, 3)。动图取首帧；透明像素铺白底。"""
    try:
        im = Image.open(path)
    except Exception as e:
        raise ScrambleError("打不开这张图（%s）：%s" % (Path(path).name, e))
    try:
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)                        # 动图只取第一帧
        im = im.convert("RGBA")
        flat = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(flat, im).convert("RGB")
    except ScrambleError:
        raise
    except Exception as e:
        raise ScrambleError("这张图解不开（%s）：%s" % (Path(path).name, e))
    if _np is not None:
        return _np.asarray(im, dtype=_np.uint8)
    return im


def save_jpeg(arr, out_path, quality=95):
    """存 JPEG。4:4:4 不做色度抽样 —— 混淆图被平台二次压缩时掉得最少。

    `arr` 可以是 numpy (h,w,3) 也可以是纯 Python 的二维 list（没 numpy 的兜底路径）。
    """
    if _np is not None and hasattr(arr, "shape"):
        im = Image.fromarray(_np.ascontiguousarray(arr.astype("uint8")), "RGB")
    elif isinstance(arr, Image.Image):
        im = arr
    else:
        h = len(arr)
        w = len(arr[0]) if h else 0
        flat = []
        for row in arr:
            for px in row:
                flat.append(tuple(int(c) for c in px)[:3])
        im = Image.new("RGB", (w, h))
        im.putdata(flat)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(str(out_path), "JPEG", quality=int(quality), subsampling=0, optimize=False)
    return str(out_path)


# ---------------------------------------------------------------- 核心
def transform(arr, decrypt=False):
    """像素搬移。arr = (h, w, 3) uint8。decrypt=True 即解混淆。

    只依赖 `curve()` 给的索引数组：
        混淆  dst[curve[(i+off)%N]] = src[curve[i]]
        解混淆 dst[curve[i]]         = src[curve[(i+off)%N]]
    """
    h, w = _shape(arr)
    n = w * h
    off = offset_for(w, h)
    if _np is not None:
        idx = curve(w, h).astype(_np.int64)                     # curve[i]
        jdx = _np.empty(n, dtype=_np.int64)
        jdx[:n - off] = idx[off:]                               # curve[(i+off)%n]
        jdx[n - off:] = idx[:off]
        src = arr.reshape(-1, 3)
        out = _np.empty_like(src)
        if decrypt:
            out[idx] = src[jdx]
        else:
            out[jdx] = src[idx]
        return out.reshape(h, w, 3)
    # ---- 纯 Python 兜底（没 numpy 时走这条：正确但很慢，128x128 以内才实用）----
    xs = [0] * n
    ys = [0] * n
    if width_ge_h(w, h):
        _gen_py(0, 0, w, 0, 0, h, xs, ys, 0)
    else:
        _gen_py(0, 0, 0, h, w, 0, xs, ys, 0)
    src = _load_pixels(arr)
    dst = [[None] * w for _ in range(h)]
    for i in range(n):
        ox, oy = xs[i], ys[i]
        nx, ny = xs[(i + off) % n], ys[(i + off) % n]
        if decrypt:
            dst[oy][ox] = src[ny][nx]
        else:
            dst[ny][nx] = src[oy][ox]
    return dst


def width_ge_h(w, h):
    return int(w) >= int(h)


def _gen_py(x, y, ax, ay, bx, by, xs, ys, i):
    """没 numpy 时的纯 Python 曲线生成（逐行对照网页 JS 的 generate2d）。"""
    w = abs(ax + ay)
    h = abs(bx + by)
    dax, day = _sgn(ax), _sgn(ay)
    dbx, dby = _sgn(bx), _sgn(by)
    if h == 1:
        for k in range(w):
            xs[i + k] = x
            ys[i + k] = y
            x += dax
            y += day
        return i + w
    if w == 1:
        for k in range(h):
            xs[i + k] = x
            ys[i + k] = y
            x += dbx
            y += dby
        return i + h
    ax2, ay2, bx2, by2 = ax // 2, ay // 2, bx // 2, by // 2
    w2, h2 = abs(ax2 + ay2), abs(bx2 + by2)
    if 2 * w > 3 * h:
        if (w2 % 2) and (w > 2):
            ax2 += dax
            ay2 += day
        i = _gen_py(x, y, ax2, ay2, bx, by, xs, ys, i)
        return _gen_py(x + ax2, y + ay2, ax - ax2, ay - ay2, bx, by, xs, ys, i)
    if (h2 % 2) and (h > 2):
        bx2 += dbx
        by2 += dby
    i = _gen_py(x, y, bx2, by2, ax2, ay2, xs, ys, i)
    i = _gen_py(x + bx2, y + by2, ax, ay, bx - bx2, by - by2, xs, ys, i)
    return _gen_py(x + (ax - dax) + (bx2 - dbx), y + (ay - day) + (by2 - dby),
                   -bx2, -by2, -(ax - ax2), -(ay - ay2), xs, ys, i)


def _gen_py(x, y, ax, ay, bx, by, xs, ys, i):
    """没 numpy 时的纯 Python 曲线生成（逐行对照网页 JS）。"""
    w = abs(ax + ay)
    h = abs(bx + by)
    dax, day = _sgn(ax), _sgn(ay)
    dbx, dby = _sgn(bx), _sgn(by)
    if h == 1:
        for k in range(w):
            xs[i + k] = x
            ys[i + k] = y
            x += dax
            y += day
        return i + w
    if w == 1:
        for k in range(h):
            xs[i + k] = x
            ys[i + k] = y
            x += dbx
            y += dby
        return i + h
    ax2, ay2, bx2, by2 = ax // 2, ay // 2, bx // 2, by // 2
    w2, h2 = abs(ax2 + ay2), abs(bx2 + by2)
    if 2 * w > 3 * h:
        if (w2 % 2) and (w > 2):
            ax2 += dax
            ay2 += day
        i = _gen_py(x, y, ax2, ay2, bx, by, xs, ys, i)
        return _gen_py(x + ax2, y + ay2, ax - ax2, ay - ay2, bx, by, xs, ys, i)
    if (h2 % 2) and (h > 2):
        bx2 += dbx
        by2 += dby
    i = _gen_py(x, y, bx2, by2, ax2, ay2, xs, ys, i)
    i = _gen_py(x + bx2, y + by2, ax, ay, bx - bx2, by - by2, xs, ys, i)
    return _gen_py(x + (ax - dax) + (bx2 - dbx), y + (ay - day) + (by2 - dby),
                   -bx2, -by2, -(ax - ax2), -(ay - ay2), xs, ys, i)


# ---------------------------------------------------------------- 对外
def _out_dir(out_dir):
    return Path(out_dir)


def _gc(out_dir, keep):
    """只留最新的 keep 个产物。keep=0 = 只留最新一张（别写成 max(1,keep)，那会把 0 吃掉）。"""
    out_dir = _out_dir(out_dir)
    keep = max(0, int(keep if keep is not None else 20))
    try:
        files = sorted((p for p in out_dir.iterdir()
                        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png")),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[keep:]:
            try:
                p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def scramble(src, out_dir, max_side=2048, max_pixels=4000000,
             quality=95, keep=20):
    """混淆一张图。返回 (输出路径, 原始宽高)。"""
    arr = load_rgb(src)
    h0, w0 = _shape(arr)
    w, h = _fit(w0, h0, max_side, max_pixels)
    if (w, h) != (w0, h0):
        arr = _resize(arr, w, h)
    t0 = time.time()
    out = transform(arr, decrypt=False)
    dst = _out_dir(out_dir) / ("scr_%d_%dx%d_%s.jpg" % (
        int(t0), w, h, uuid.uuid4().hex[:4]))
    save_jpeg(out, dst, quality)
    _gc(out_dir, keep)
    log_path = dst
    return str(log_path), (w, h), time.time() - t0


def descamble(src, out_dir, size=None, max_pixels=4000000,
              quality=95, keep=20):
    """解混淆一张图。`size=(w,h)` 可手工指定**原始**宽高（平台缩放过就用它兜底）。

    返回 (输出路径, 实际使用的宽高, 耗时秒)。
    """
    arr = load_rgb(src)
    h0, w0 = _shape(arr)
    if size:
        w, h = int(size[0]), int(size[1])
        if w <= 0 or h <= 0:
            raise ScrambleError("尺寸写错了：%sx%s" % (w, h))
        if (w, h) != (w0, h0):
            # 只有宽高按比例一致时才敢缩，否则形状已经不对了
            if abs(w / float(h) - w0 / float(h0)) > 0.02:
                raise ScrambleError(
                    "原图尺寸 %dx%d 和这张图 %dx%d 比例对不上（比例差太大），"
                    "硬转会花掉。确认尺寸再试，或者直接找发图的人要原图。"
                    % (w, h, w0, h0))
            arr = _resize(arr, w, h)
    else:
        if max_pixels and w0 * h0 > max_pixels:
            raise ScrambleError(
                "这张图 %dx%d 太大了（上限 %d 像素），先缩小或换小图。"
                % (w0, h0, int(max_pixels)))
        w, h = w0, h0
    t0 = time.time()
    out = transform(arr, decrypt=True)
    dst = _out_dir(out_dir) / ("dec_%d_%dx%d_%s.jpg" % (
        int(t0), w, h, uuid.uuid4().hex[:4]))
    save_jpeg(out, dst, quality)
    _gc(out_dir, keep)
    return str(dst), (w, h), time.time() - t0


def _load_pixels(arr):
    """转成二维 list of (r,g,b) —— 没 numpy 时 PIL Image 不能下标，只能先 tolist()。"""
    if _np is not None and hasattr(arr, "shape"):
        return [[tuple(int(c) for c in arr[y][x]) for x in range(arr.shape[1])]
                for y in range(arr.shape[0])]
    im = arr if getattr(arr, "mode", "") == "RGB" else arr.convert("RGB")
    w, h = im.size
    flat = list(im.getdata())          # ⚠️ PIL Image **没有** .tolist()，得走 getdata()
    return [list(flat[y * w:(y + 1) * w]) for y in range(h)]


def _shape(arr):
    """(h, w) —— 有 numpy 用 arr.shape，没 numpy 用 PIL 的 size（**注意 PIL 是 w,h**）。"""
    if _np is not None and hasattr(arr, "shape"):
        return int(arr.shape[0]), int(arr.shape[1])
    w, h = arr.size
    return int(h), int(w)


def _resize(arr, w, h):
    if _np is not None and hasattr(arr, "shape"):
        im = Image.fromarray(_np.ascontiguousarray(arr.astype("uint8")), "RGB")
        return _np.asarray(im.resize((w, h), Image.LANCZOS), dtype=_np.uint8)
    return arr.resize((w, h), Image.LANCZOS)


def parse_size(text):
    """从「1536x1536」「1536×1536」「1536,1536」「1536 1536」里抠出 (w,h)。"""
    import re as _re
    m = _re.search(r"(\d{2,5})\s*[xX×*,，\s]\s*(\d{2,5})", str(text or ""))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))