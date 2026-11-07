# -*- coding: utf-8 -*-
"""自测：keeper 的 _webui_token / _export_qr 能不能拿到真码（不弹窗）。

用法：python tests/_tqr.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _keeper as K

ok = True


def chk(name, cond, extra=""):
    global ok
    print(("  OK   " if cond else "  FAIL ") + name + (("  <- " + str(extra)) if extra else ""))
    if not cond:
        ok = False


print("=== 1. WebUI 口令 ===")
tok = K._webui_token()
chk("从 .env 读到 token", bool(tok), tok or "空")
chk("token 长度合理", len(tok) >= 8, len(tok))

print("=== 2. 导出二维码 ===")
path, why = K._export_qr()
chk("导出成功", bool(path), why)
if path:
    chk("文件存在", os.path.exists(path), path)
    chk("文件非空", os.path.getsize(path) > 200, os.path.getsize(path))
    try:
        from PIL import Image
        im = Image.open(path)
        chk("能解码成图片", im.width > 0, "%dx%d" % (im.width, im.height))
        chk("尺寸够扫（>=300px）", im.width >= 300, im.width)
    except ImportError:
        print("  SKIP  PIL 不可用，跳过图片校验")

print("=== 3. 容器不在时的降级 ===")
_old = K._container_name
K._container_name = lambda: "no-such-container-xyz"
p2, why2 = K._export_qr()
chk("容器不存在时返回 None", p2 is None, why2)
chk("降级有说明文字", bool(why2), why2)
K._container_name = _old

print()
print("结果：" + ("全部通过" if ok else "有失败项"))
sys.exit(0 if ok else 1)
