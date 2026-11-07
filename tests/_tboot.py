"""启动链路回归：真跑一遍 start_all.bat，确认 keeper 能在几秒内把 bot 拉起来并监听 8088。

为什么要有这个测试（2026-09-13 事故）：`start_all.bat` 被写成 **纯 LF 换行**，cmd.exe 对 LF 的
.bat 会错行解析 —— 双击出来的窗口每 30 秒往 bot.err.log 写一句「系统找不到指定的路径。」，
**永远走不到启动 bot 那一行**（bot.out.log 一直是 0 字节），主人以为"点了没用"。
光看代码逻辑看不出来，必须真跑。

跑法：`PYTHONIOENCODING=utf-8 python _tboot.py`
通过标准：退出码 0（即 8088 在 15 秒内监听）。
副作用：结束后 keeper 会被停掉，但 bot 是它的子进程、**可能仍在跑**（这正是期望状态）。
输出落在 `state/tboot.out`（state/ 不进 git）。
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# ⚠️ 2026-09-18 修：本脚本搬到 tests/ 之后，start_all.bat / config.json / state/ 全都要按
# **项目根**（_ROOT）算，不能再用 HERE（= tests/）——否则 FileNotFoundError 直接崩，
# 等于这个回归件一直是废的（它自己就是那次搬家的漏网）。
OUT = os.path.join(_ROOT, "state", "tboot.out")
DEADLINE = 15.0
STEP = 1.5


def port_open(port):
    s = socket.socket()
    s.settimeout(2)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    bat = os.path.join(_ROOT, "start_all.bat")
    raw = open(bat, "rb").read()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    print("start_all.bat: %d 字节, CRLF=%d, 裸LF=%d" % (len(raw), crlf, lf))
    ok_ending = (lf == 0 and crlf > 0)
    if not ok_ending:
        print("!! .bat 不是纯 CRLF —— cmd 会错行解析，先修换行符（见 .gitattributes）")

    cfg = json.load(open(os.path.join(_ROOT, "config.json"), encoding="utf-8"))
    ag = cfg.get("agent", {})
    for key in ("node", "cli_js"):
        p = ag.get(key) or ""
        print("agent.%-6s 存在=%s  (%s)" % (key, os.path.exists(p), p))

    print("跑之前 8088 监听 =", port_open(8088))
    with open(OUT, "wb") as fh:
        proc = subprocess.Popen(["cmd", "/c", "start_all.bat"], cwd=_ROOT,
                                stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT)
        up = None
        waited = 0.0
        while waited < DEADLINE:
            time.sleep(STEP)
            waited += STEP
            if port_open(8088):
                up = waited
                break
        try:
            proc.kill()
        except Exception:
            pass
    time.sleep(1)
    print("8088 起来耗时 =", ("%.1f 秒" % up) if up else "%.1f 秒内没起来" % DEADLINE)
    print()
    print("=== start_all.bat 的输出 ===")
    print(open(OUT, encoding="utf-8", errors="replace").read())
    print("=== state/keeper.log 尾部 ===")
    kl = os.path.join(_ROOT, "state", "keeper.log")
    if os.path.exists(kl):
        print("".join(open(kl, encoding="utf-8", errors="replace").readlines()[-4:]))
    print()
    if ok_ending and up:
        print("[OK] 启动链路通：bat 是 CRLF，keeper 在 %.1f 秒内拉起 bot" % up)
        return 0
    print("[失败] 启动链路有问题（换行符 ok=%s, bot 起来=%s）" % (ok_ending, bool(up)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
