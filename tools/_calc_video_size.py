# -*- coding: utf-8 -*-
"""本地算 ResolutionSelector 会给出多少像素 —— **纯计算，不连 ComfyUI、不占队列**。

算法照抄 ComfyUI 源码 `comfy_extras/nodes_resolution.py`：
    w_ratio, h_ratio = ASPECT_RATIOS[aspect]
    total_pixels = megapixels * 1024 * 1024     # ⚠️ 是 1024²，不是 1000000
    scale  = sqrt(total_pixels / (w_ratio * h_ratio))
    width  = round(w_ratio * scale / multiple) * multiple
    height = round(h_ratio * scale / multiple) * multiple

⚠️ 因为 `multiple` 会取整，**结果比例不会精确等于所选比例**（小尺寸尤其明显）。
   ratio 列就是"选了这个比例，实际拿到多少"。

用法：python tools/_calc_video_size.py [比例] [百万像素] [multiple]
      不带参数 = 把常用档位全列一遍
"""
import math
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
_sys.stdout.reconfigure(encoding="utf-8")

# 与 ComfyUI ASPECT_RATIOS 一致（名字必须逐字相同，COMBO 不认别名）
RATIOS = {
    "1:1 (Square)": (1, 1),
    "2:3 (Portrait Photo)": (2, 3),
    "3:2 (Photo)": (3, 2),
    "3:4 (Portrait Standard)": (3, 4),
    "4:3 (Standard)": (4, 3),
    "9:16 (Portrait Widescreen)": (9, 16),
    "16:9 (Widescreen)": (16, 9),
    "21:9 (Ultrawide)": (21, 9),
}


def calc(aspect, megapixels=0.6, multiple=32):
    if aspect not in RATIOS:
        raise KeyError("不认识的档位：%s（可选：%s）" % (aspect, "、".join(RATIOS)))
    wr, hr = RATIOS[aspect]
    total = float(megapixels) * 1024 * 1024
    scale = math.sqrt(total / (wr * hr))
    w = round(wr * scale / multiple) * multiple
    h = round(hr * scale / multiple) * multiple
    return w, h


def _line(aspect, mp, mult):
    w, h = calc(aspect, mp, mult)
    want = RATIOS[aspect][0] / RATIOS[aspect][1]
    return ("  %-26s %4sMP x%-3d -> %5d x %5d | %6.3f MP | 实际 %5.3f / 目标 %5.3f | "
            "%s | %s"
            % (aspect, mp, mult, w, h, w * h / 1048576.0, w / h, want,
               "16 的倍数 OK" if (w % 16 == 0 and h % 16 == 0) else "⚠️ 不是 16 的倍数",
               "OK" if abs(w / h - want) < 0.02 else "⚠️ 取整偏 %.1f%%" % (abs(w / h - want) / want * 100)))


if __name__ == "__main__":
    av = _sys.argv[1:]
    if len(av) >= 1:
        _a = av[0]
        if _a not in RATIOS:                       # 允许传短名
            for k in RATIOS:
                if k.startswith(_a + " "):
                    _a = k
                    break
        mp = float(av[1]) if len(av) > 1 else 0.6
        mult = int(av[2]) if len(av) > 2 else 32
        w, h = calc(_a, mp, mult)
        print("%s @ %sMP, multiple=%d  =>  %d x %d  (%.3f MP, ratio %.4f)"
              % (_a, mp, mult, w, h, w * h / 1048576.0, w / h))
        _sys.exit(0)

    print("=== 视频画布像素表（ResolutionSelector，multiple=32）===")
    print("【当前默认：3:4 @ 0.6MP】")
    print(_line("3:4 (Portrait Standard)", 0.6, 32))
    print("\n【比例 3:4，不同像素】")
    for mp in (0.3, 0.5, 0.6, 0.8, 1.0, 1.5):
        print(_line("3:4 (Portrait Standard)", mp, 32))
    print("\n【0.6 MP，各比例】")
    for a in RATIOS:
        print(_line(a, 0.6, 32))
    print("\n【multiple 对比（3:4 @ 0.6MP）】")
    for m in (8, 16, 32, 64):
        print(_line("3:4 (Portrait Standard)", 0.6, m))
