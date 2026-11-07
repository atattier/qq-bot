# -*- coding: utf-8 -*-
"""NapCat 容器管理（方案 B）。

    up      启动容器（没有 .env 会自动从 .env.example 生成并提示你填 QQ 号）
    status  容器状态 + 3000 端口 + 账号是否 online（= 一键看「现在能不能用」）
    restart 重启容器（登录态在 .config 卷里，不会丢，一般不用重扫）
    logs    打印容器最近 80 行日志
    down    停掉容器（登录态不丢，下次 up 接着用）

所有 docker 调用都走 docker-napcat/dutil.py，和 wire/bootstrap/doctor 用同一套
路径探测逻辑（2026-10-22 之前各写一份，wire 认 <工具安装目录> 而 keeper 认 PATH，
结果「脚本说装好了、keeper 说没有」这种鬼故事）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "docker-napcat"))

import dutil as U


def cmd_up():
    if not os.path.exists(U.ENV_FILE):
        if not os.path.exists(U.ENV_EXAMPLE):
            print("没有 .env.example，无法生成 .env")
            return 1
        with open(U.ENV_EXAMPLE, "r", encoding="utf-8") as a, \
                open(U.ENV_FILE, "w", encoding="utf-8", newline="\n") as b:
            b.write(a.read())
        print("\n[!] 已生成 .env —— 先填 QQ 号：")
        print("    %s\n" % U.ENV_FILE)
        print("    把 NAPCAT_ACCOUNT 改成机器人 QQ 号、WEBUI_TOKEN 改成口令，再跑一次")
        return 2
    rc, out = U.docker(["compose", "up", "-d"], timeout=900, cwd=U.HERE)
    print("\n%s" % (out or "")[-1200:])
    if rc != 0:
        print("\n[!] 起不来。先跑 docker_doctor.bat 看断在哪；"
              "最常见是 3000 端口被 Windows 注入版 NapCat 占着。")
        return 1
    print("\n容器已启动。")
    print("  扫码登录: http://127.0.0.1:%d/webui （口令见 docker-napcat/.env）" % U.WEBUI_PORT)
    print("  验收    : docker_doctor.bat")
    return 0


def cmd_down():
    rc, out = U.docker(["compose", "down"], timeout=300, cwd=U.HERE)
    print("\n%s" % (out or "")[-600:])
    print("登录态在 docker-napcat/.config 卷里，没删；下次 up 不用重扫。")
    return 0 if rc == 0 else 1


def cmd_restart():
    rc, out = U.docker(["restart", U.CONTAINER], timeout=180)
    print("\n%s" % (out or "")[-400:])
    if rc != 0:
        return 1
    print("已重启。等 20~30 秒让 NapCat 起来，再看 docker_status.bat。")
    return 0


def cmd_logs():
    rc, out = U.docker(["logs", "--tail", "80", U.CONTAINER], timeout=120)
    print((out or "（没有日志）")[-6000:])
    return 0 if rc == 0 else 1


def cmd_status():
    if not U.docker_bin():
        print("\n[!] 没找到 docker —— 双击 install_docker.bat 装环境（要重启一次）")
        return 1
    ok, ver = U.docker_ok()
    if not ok:
        print("\n[!] docker 守护进程没起来（%s）" % str(ver)[:120])
        print("    启动 Docker Desktop，等托盘变绿再来")
        return 1
    rc, out = U.docker(["ps", "-a", "--filter", "name=" + U.CONTAINER,
                        "--format", "{{.Status}}"], timeout=60)
    state = (out or "").strip() or "（没有 napcat 容器）"
    print("\n容器状态: %s" % state)
    print("端口 3000: %s    WebUI 6099: %s" % (
        "通" if U.port_open(U.NAPCAT_PORT) else "不通",
        "通" if U.port_open(U.WEBUI_PORT) else "不通"))
    st, detail = U.napcat_status()
    if st is True:
        print("账号: 在线（%s）" % detail)
        print("\n✅ 容器版 NapCat 在线，bot 可以直接连。")
        return 0
    if st is False:
        print("账号: 离线 —— 去 http://127.0.0.1:%d/webui 扫码" % U.WEBUI_PORT)
        return 1
    print("账号: 探不到（%s）" % detail)
    print("\n⚠️ 还没通。跑 docker_doctor.bat 逐环体检。")
    return 1


CMDS = {"up": cmd_up, "down": cmd_down, "status": cmd_status,
        "restart": cmd_restart, "logs": cmd_logs}


def main():
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "status").strip().lower()
    print("=" * 56)
    print(" NapCat 容器管理 [%s]" % cmd)
    print("=" * 56)
    fn = CMDS.get(cmd)
    if not fn:
        print("用法: docker_mgr.py [%s]" % "|".join(CMDS))
        return 2
    return fn()


if __name__ == "__main__":
    sys.exit(main())
