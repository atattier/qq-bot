# -*- coding: utf-8 -*-
"""重启 keeper（连带 bot）。

为什么不用「杀进程 + 双击 start_all.bat」：keeper 的 pid 锁在 state/keeper.pid，
直接再起一个会被 already_running 挡掉；而 start_all.bat 会弹 UAC，脚本里没法点。
所以这里按顺序做三件事：停旧 → 等锁释放 → 起新（新窗口，脱离本脚本存活）。

    python tools/restart_keeper.py            # 重启
    python tools/restart_keeper.py --status   # 只看
    python tools/restart_keeper.py --stop     # 只停（同时撤掉 keeper.want，守卫不会复活它）
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import keeper_guard as G


def main():
    a = sys.argv[1:]
    if "--status" in a:
        return G.status()

    # 只有「明确要它跑」时才写 want；--stop 是明确不要
    if "--stop" in a:
        try:
            os.remove(G.WANT)
        except Exception:
            pass
    else:
        os.makedirs(G.STATE, exist_ok=True)
        with open(G.WANT, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S 由 restart_keeper.py 写入\n"))

    pid = G.keeper_pid()
    if G.pid_alive(pid):
        print("停掉旧 keeper（PID %d）…" % pid)
        ok, info = G.stop_keeper(pid)
        print("  %s %s" % (ok, info))
        for _ in range(10):
            if not G.pid_alive(pid):
                break
            time.sleep(1)
    else:
        print("旧 keeper 已经不在（pid=%s）" % (pid or "无"))

    # 清掉残留的 pid 锁，否则新 keeper 会以为还有一个在跑
    try:
        if os.path.exists(G.PIDF) and not G.pid_alive(G.keeper_pid()):
            os.remove(G.PIDF)
            print("已清理残留 pid 锁")
    except Exception:
        pass

    if "--stop" in a:
        print("已停。要恢复：双击 start_all.bat")
        return 0

    ok, info = G.start_keeper()
    print("  " + info)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
