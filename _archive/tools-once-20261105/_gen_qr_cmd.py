# -*- coding: utf-8 -*-
"""生成「看二维码.cmd」——纯 ASCII + CRLF，别让中文和 LF 混进批处理。

2026-10-27 踩的坑：第一版直接用文本编辑器写，结果
  ① 中文按 UTF-8 存进去，cmd 用 GBK 解析 → 整行拆错，报
     `'/d' is not recognized as an internal or external command`
  ② 行尾是 LF，cmd 要 CRLF
所以改成用脚本生成：内容全 ASCII（中文提示全交给 Python 那边打印），行尾强制 CRLF。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 文件名可以是中文（NTFS 没问题），但**内容**必须全 ASCII —— 这是踩过坑的铁律。
TARGETS = [
    ("看二维码.cmd", []),
    # 2026-10-28 清理：旧版验证（QQ 3.2.21）已证实被腾讯版本硬门槛拒登（错误码 45），
    # 旧容器/旧 compose/旧入口一并归档，不再生成「看旧版二维码.cmd」。
]


def build(extra):
    # 用拼接而不是 % 格式化：cmd 的 %~dp0 里的 % 会跟 Python 的 % 格式化打架
    call = 'python "%~dp0tools\\show_qr.py"'
    if extra:
        call += " " + " ".join(extra)
    call += " %*"
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        'cd /d "%~dp0"',
        call,
        "echo.",
        "pause",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


def main() -> int:
    for name, extra in TARGETS:
        target = os.path.join(ROOT, name)
        data = build(extra)
        with open(target, "wb") as f:
            f.write(data)
        print("已写入 %s（%d 字节，%d 个 CRLF）"
              % (target, len(data), data.count(b"\r\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
