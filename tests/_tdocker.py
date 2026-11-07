# -*- coding: utf-8 -*-
"""方案 B（NapCat 容器化）自测的回归入口。

跑的是 tools/docker_selftest.py：把 docker / 端口 / 账号探活全换成假的，
真跑一遍 wire / doctor / docker_mgr / bootstrap / rollback，断言它们改出来的配置
和给出的结论都对。所以**没装 Docker 也能跑**，也不会碰真 config.json。

单独跑：python tools/docker_selftest.py -v
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TARGET = os.path.join(ROOT, "tools", "docker_selftest.py")


def main():
    if not os.path.exists(TARGET):
        print("找不到 %s" % TARGET)
        return 1
    p = subprocess.run([sys.executable, TARGET], cwd=ROOT,
                       capture_output=True, text=True, timeout=300)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    lines = [x for x in out.splitlines() if x.strip()]
    # 失败时把带 ❌ 的行打出来，否则回归报告里只有一行「失败」没法查
    if p.returncode != 0:
        for line in lines:
            if "❌" in line:
                print(line)
    summary = ""
    for line in reversed(lines):
        if "通过" in line:
            summary = line.strip()
            break
    print(summary or (lines[-1][:90] if lines else "无输出"))
    return 0 if p.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
