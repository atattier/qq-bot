# -*- coding: utf-8 -*-
"""守卫：送视觉模型的图必须按**字节**压到云端预算内（2026-10-29 线上识图全线失效的根因）。

为什么必须有这条用例（事发经过，别再犯）：
  · QQ 收到的图经常 6~8MB，原来 `_call_vl` 只给**本地 ollama**压图，云端档一律发原图；
  · 而且压图走 PNG **无损重编码** —— 2304px 的 8MB PNG 压到 1280 还是 PNG，体积没降；
  · 于是 nex-agi 400（512KB 硬上限）、自定义站 413、本地 ollama 502，三个档同时挂，
    用户看到的就是「视觉模型全用不了」，画同款/看图接话全部失效。
断言（任何一条挂了都说明压图又退化）：
  ① 大图（现造 2400×2400 噪点 PNG，必有几 MB）压到云端预算内（base64 后 ≤ 512KB）；
  ② 输出 MIME 必须是 **image/jpeg**（PNG 无损编码压不下来，这是原来那个坑）；
  ③ 长边不超过 VL_CLOUD_IMG_SIDE；
  ④ 小图不被白白重编码（原样返回原文件 data URL，画质无损）；
  ⑤ 压缩后仍能被 PIL 打开（别压出坏图）。
全部在临时目录里造图，不碰项目文件、不删任何东西。
"""
import base64
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [NG] ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails.append(name)


def main():
    from qbot import reverser

    budget = reverser.VL_CLOUD_IMG_BYTES
    side = reverser.VL_CLOUD_IMG_SIDE
    tmp = tempfile.mkdtemp(prefix="tvl_")

    # 造一张「像照片一样压不下去」的图：噪点 + 渐变（纯色图 JPEG 会极小，测不出问题）
    big = os.path.join(tmp, "big.png")
    try:
        from PIL import Image, ImageDraw
        import random
        random.seed(7)
        im = Image.new("RGB", (2400, 2400))
        px = im.load()
        for y in range(0, 2400, 4):
            for x in range(0, 2400, 4):
                r = (x * 255 // 2400) + random.randint(-40, 40)
                g = (y * 255 // 2400) + random.randint(-40, 40)
                b = random.randint(0, 255)
                c = (max(0, min(255, r)), max(0, min(255, g)), b)
                for dy in range(4):
                    for dx in range(4):
                        px[x + dx, y + dy] = c
        im.save(big, "PNG")
    except ImportError:
        print("  [SKIP] 没装 PIL，这条用例测不了（装 Pillow 后再跑）")
        return 0

    raw = os.path.getsize(big)
    print("  造的测试大图：%d×%d，%.1f MB" % (2400, 2400, raw / 1048576.0))

    # ① 云端预算
    d = reverser._fit_image_data_url(big, max_side=side, max_bytes=budget)
    head, b64 = d.split(",", 1)
    got = len(b64) * 3 // 4
    check("① 大图压到云端预算内（base64 ≤512KB）", len(b64) <= 512 * 1024,
          "实得 base64=%.0fKB / 原始≈%.0fKB（预算 %dKB）" % (
              len(b64) / 1024.0, got / 1024.0, budget // 1024))

    # ② MIME 必须是 jpeg（PNG 无损压不下来 = 原来那个坑）
    check("② 输出 MIME = image/jpeg（别再用无损 PNG）", head.startswith("data:image/jpeg"),
          head)

    # ③ 长边
    try:
        from PIL import Image
        import io
        im2 = Image.open(io.BytesIO(base64.b64decode(b64)))
        check("③ 长边 ≤ VL_CLOUD_IMG_SIDE", max(im2.size) <= side,
              "实得 %s" % (im2.size,))
        check("⑤ 压缩后图可正常打开", im2.size[0] > 0 and im2.size[1] > 0, str(im2.size))
    except Exception as e:  # noqa: BLE001
        check("③ 长边 ≤ VL_CLOUD_IMG_SIDE", False, "打不开：%s" % e)
        check("⑤ 压缩后图可正常打开", False, str(e))

    # ④ 小图原样发（不白压，保画质）
    small = os.path.join(tmp, "small.png")
    try:
        from PIL import Image
        Image.new("RGB", (320, 240), (200, 60, 60)).save(small, "PNG")
        d2 = reverser._fit_image_data_url(small, max_side=side, max_bytes=budget)
        check("④ 小图原样返回（不重编码，画质无损）",
              d2.split(",", 1)[0].startswith("data:image/png"),
              d2.split(",", 1)[0])
    except Exception as e:  # noqa: BLE001
        check("④ 小图原样返回（不重编码，画质无损）", False, str(e))

    # ⑥ 本地档预算也仍然生效（怕 token 爆上下文）
    dl = reverser._fit_image_data_url(big, max_side=1280)
    try:
        from PIL import Image
        import io
        im3 = Image.open(io.BytesIO(base64.b64decode(dl.split(",", 1)[1]))
                         if dl.split(",", 1)[0].startswith("data:image/jpeg")
                         else open(big, "rb"))
        check("⑥ 本地档仍是长边 ≤1280", max(im3.size) <= 1280, str(im3.size))
    except Exception as e:  # noqa: BLE001
        check("⑥ 本地档仍是长边 ≤1280", False, str(e))

    for f in (big, small):
        try:
            os.remove(f)
        except Exception:
            pass
    try:
        os.rmdir(tmp)
    except Exception:
        pass

    if fails:
        print("\n压图守卫：失败 %d 条 —— %s" % (len(fails), "、".join(fails)))
        return 1
    print("\n压图守卫：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
