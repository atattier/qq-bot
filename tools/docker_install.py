# -*- coding: utf-8 -*-
"""方案 B 环境安装（旧入口，保留兼容）。

2026-10-22 起真正的实现搬到了 tools/docker_bootstrap.py —— 它把「开 WSL 功能 → 装
Docker Desktop」和后面的「起容器 → 接线 → 切 keeper 模式 → 验收」串成一条可重入的
流水线，装完重启后接着跑就行。这个文件只做转发，免得旧的快捷方式和文档里的路径失效。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import docker_bootstrap


def main():
    print("（docker_install.py 已并入 docker_bootstrap.py，这里只是转发）")
    for stage in ("features", "install"):
        rc = docker_bootstrap.main(["--stage", stage])
        if rc != 0:
            return rc
    print("")
    print("环境装完了。**请重启电脑**，然后双击 install_docker.bat 继续后面的步骤。")
    return docker_bootstrap.EXIT_NEED_REBOOT


if __name__ == "__main__":
    sys.exit(main())
