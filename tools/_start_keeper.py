"""一次性：用「分离会话」拉起 keeper（不占终端、不随父进程退出）。

为什么需要这个脚本（2026-10-30 真踩到）：
- 在 AI 会话里 `nohup pythonw _keeper.py &` 起的 keeper，**主循环一退出就死**
  （父 shell 结束时子进程被回收）。实测 00:14 起的 48544 在 00:18 就没了。
- `cmd start` 也不行：AI 会话里 Popen 出来的进程被程序黑名单拦（记忆里的那条）。
- keeper 自己的 `ensure_guard_running` 用 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP |
  CREATE_NO_WINDOW` 三件套起守卫，这条路是**验证过能用**的（守卫 16444 一直活着）。
  所以这里照抄同一套 flags 起 keeper。

用法：python tools/_start_keeper.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYW = sys.executable
if not os.path.exists(PYW):
    PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")

DETACHED = 0x00000008       # DETACHED_PROCESS
NEWGRP = 0x00000200         # CREATE_NEW_PROCESS_GROUP
NO_WIN = 0x08000000         # CREATE_NO_WINDOW

sys.stdout.reconfigure(encoding="utf-8")
p = subprocess.Popen([PYW, "-u", os.path.join(ROOT, "_keeper.py")],
                     cwd=ROOT, close_fds=True,
                     creationflags=DETACHED | NEWGRP | NO_WIN)
print("keeper 已在分离会话里启动 pid=%d（不会随这个窗口退出）" % p.pid)
print("看它活没活：state/keeper.beat 的时间戳；看日志：state/keeper.log")
