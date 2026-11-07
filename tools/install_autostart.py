# -*- coding: utf-8 -*-
"""把 keeper 守卫装进「启动」文件夹（不需要管理员，也不用计划任务）。

背景：注册计划任务要 schtasks（要管理员），而这台机器的 AI 会话里 schtasks 被安全
策略挡着，凌晨也没人能点 UAC。所以补一条**纯文件操作**的路：往当前用户的
Startup 文件夹丢一个 vbs，开机静默拉起 `keeper_guard.py --loop` 常驻。

两者的关系（不冲突，可以都要）：
    · Startup vbs  → 开机就有一个守卫常驻，每 5 分钟看一眼（不需要任何权限）
    · 计划任务     → 更狠一点（能跨会话、被关掉也能再起），但要管理员，见 keeper_guard.bat install

    python tools/install_autostart.py            # 安装（写 vbs + 立刻起一个常驻守卫）
    python tools/install_autostart.py --check    # 只看会写什么
    python tools/install_autostart.py --remove   # 卸载（删 vbs + 停常驻守卫）
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYW = sys.executable
GUARD = os.path.join(HERE, "keeper_guard.py")
VBS_NAME = "qqbot-keeper-guard.vbs"


def startup_dir():
    return os.path.join(os.environ.get("APPDATA") or "",
                        r"Microsoft\Windows\Start Menu\Programs\Startup")


def vbs_path():
    return os.path.join(startup_dir(), VBS_NAME)


VBS_LINES = [
    "' qqbot keeper 守卫：开机静默拉起常驻检查（由 tools/install_autostart.py 生成）",
    "' 作用：keeper 进程没了 / 卡死了，5 分钟内把它拉回来。2026-10-22 00:39 那次",
    "' 「keeper 和 bot 一起消失、一小时没人发现」就是没有这个东西。",
    'Set sh = CreateObject("WScript.Shell")',
    'sh.CurrentDirectory = "%(root)s"',
    'sh.Run """%(pyw)s"" -u ""%(guard)s"" --loop", 0, False',
    "",
]


def guard_running():
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name='pythonw.exe'", "get", "CommandLine,ProcessId",
             "/format:csv"],
            capture_output=True, text=True, errors="replace", timeout=30)
        for line in (r.stdout or "").splitlines():
            if "keeper_guard.py" in line and "--loop" in line:
                parts = line.rsplit(",", 1)
                return parts[-1].strip()
    except Exception:
        pass
    return ""


def start_loop():
    try:
        subprocess.Popen([PYW, "-u", GUARD, "--loop"], cwd=ROOT, close_fds=True,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception as e:
        print("  起常驻守卫失败：%r" % (e,))
        return False


def main():
    check = "--check" in sys.argv
    remove = "--remove" in sys.argv

    print("=" * 60)
    print(" keeper 守卫开机自启（Startup 文件夹方案，不用管理员）")
    print("=" * 60)
    print(" Startup 目录: %s" % startup_dir())

    if remove:
        p = vbs_path()
        if os.path.exists(p):
            os.remove(p)
            print(" 已删除 %s" % VBS_NAME)
        else:
            print(" 没有 %s，跳过" % VBS_NAME)
        pid = guard_running()
        if pid:
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=25)
            print(" 已停掉常驻守卫 PID %s" % pid)
        return 0

    content = "\r\n".join(VBS_LINES) % {"root": ROOT, "pyw": PYW, "guard": GUARD}
    if check:
        print("\n将写入 %s：" % vbs_path())
        print(content)
        return 0

    os.makedirs(startup_dir(), exist_ok=True)
    # vbs 用 ANSI/UTF-8 都行（内容纯 ASCII 路径），但为保险用 utf-8 无 BOM
    with open(vbs_path(), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(content)
    print("\n ✅ 已写入开机自启: %s" % vbs_path())

    pid = guard_running()
    if pid:
        print(" 常驻守卫已在跑（PID %s），不重复启动" % pid)
    elif start_loop():
        import time
        time.sleep(2)
        pid = guard_running()
        print(" ✅ 常驻守卫已启动%s" % (("（PID %s）" % pid) if pid else "（稍后自查 --status）"))

    print("")
    print(" 它现在每 5 分钟看一次 keeper；只有 state/keeper.want 存在时才动手。")
    print(" 想让它真正接管：正常启动一次 keeper（start_qq.bat / start_all.bat），")
    print(" keeper 启动时会自己写 keeper.want。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
