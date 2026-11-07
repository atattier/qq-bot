# -*- coding: utf-8 -*-
"""keeper 看门狗：keeper 自己死了、或者活着但卡住了，把它拉起来。

为什么需要它（2026-10-22 00:39 真事故）：
    keeper 进程和 bot 一起消失，keeper.pid 里还留着 33852，keeper.beat 停在 00:39，
    3000/8088 全都没监听 —— 一直到 01:40 整整一小时没有任何东西发现这件事。
    keeper 内部虽然有看门狗线程，但那是**进程内**的：进程没了、或者控制台窗口被关掉，
    进程内的看门狗跟着一起死。外部必须有一个独立的、更笨但更可靠的东西定期看它一眼。

设计（刻意做得很小，因为它要长期跑在计划任务里）：
    · 没有 state/keeper.want 就什么都不做 —— 主人想停就停，守卫不许自作聪明复活
    · keeper.pid 指向的进程不存在 ⇒ 拉起
    · keeper.beat 超过 15 分钟没更新（进程在、但主循环卡死）⇒ 杀掉重拉
    · 每次动作都写 state/guard.log，最多留 500 行

用法：
    python tools/keeper_guard.py            # 检查一次（计划任务每 5 分钟跑一次）
    python tools/keeper_guard.py --status   # 只报告，不动手
    python tools/keeper_guard.py --install  # 注册计划任务（默认 5 分钟一次）
    python tools/keeper_guard.py --remove   # 注销计划任务
"""
import os
import subprocess

# pythonw（无控制台）起的进程调 tasklist/wmic/schtasks 会闪黑框，统一压掉
_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE = os.path.join(ROOT, "state")
PIDF = os.path.join(STATE, "keeper.pid")
BEAT = os.path.join(STATE, "keeper.beat")
WANT = os.path.join(STATE, "keeper.want")
LOG = os.path.join(STATE, "guard.log")
PY = sys.executable
PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
TASK = "qqbot-keeper-guard"
STALE = 900          # 心跳超过 15 分钟没动 = 卡死
INTERVAL_MIN = 5

# ---------------- 本地 meme 生成服务守护（方案A / 最小改造） ----------------
# 只调 meme_ctl.py 的 health/start：PID/命令行/marker 三重防误杀、端口占用判定、
# 防重复启动全在那边，这里绝不重写那套生命周期逻辑，也绝不自己 kill 任何进程。
# 可用环境变量把守护指向「实验服务」（独立端口/资产），方便真实验收不碰生产 2233。
MEME_CTL = os.environ.get("QQBOT_MEME_CTL") or os.path.join(
    os.path.dirname(ROOT), "_meme_lab", "meme_ctl.py")
MEME_PY = os.environ.get("QQBOT_MEME_PY") or os.path.join(
    os.path.dirname(sys.executable), "python.exe")
MEME_HEALTH_TMO = float(os.environ.get("QQBOT_MEME_HEALTH_TIMEOUT", "10"))
MEME_START_TMO = float(os.environ.get("QQBOT_MEME_START_TIMEOUT", "75"))
_meme_last_event = None        # 状态变化日志：健康态不逐轮刷屏
_meme_start_inflight = False   # 本 guard 不并发拉起第二实例（循环单线程，防御性）


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(STATE, exist_ok=True)
        # 只留最近 500 行，免得日志无界增长
        lines = []
        if os.path.exists(LOG):
            with open(LOG, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-499:]
        lines.append(line + "\n")
        with open(LOG, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception:
        pass


def keeper_pid():
    try:
        with open(PIDF, encoding="utf-8") as f:
            return int((f.read() or "0").strip() or 0)
    except Exception:
        return 0


def pid_alive(pid):
    if pid <= 0:
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                           capture_output=True, text=True, errors="replace",
                           timeout=20, creationflags=_NO_WIN)
        return str(pid) in (r.stdout or "")
    except Exception:
        return False


def _ps_cmdline(pid=None, image=None):
    """用 PowerShell 拿进程命令行。这台机器**没有 wmic.exe**，别再用它
    （2026-10-22 实测：调用直接 FileNotFoundError，导致守卫一直"查不了"）。
    失败返回 None，调用方自己决定要不要"宁可不动"。"""
    flt = ("ProcessId=%d" % pid) if pid is not None else ("Name='%s'" % image)
    try:
        cmd = ("Get-CimInstance Win32_Process -Filter \"%s\" "
               "| Select-Object -ExpandProperty CommandLine" % flt)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", cmd],
                           capture_output=True, text=True, errors="replace",
                           timeout=25, creationflags=_NO_WIN)
        if r.returncode != 0:
            return None
        return r.stdout or ""
    except Exception:
        return None


def pid_is_keeper(pid):
    """PID 会被复用：确认这个 PID 真的是 _keeper.py，别把别人的进程算成 keeper。"""
    want = os.path.basename(os.environ.get("QQBOT_GUARD_TARGET") or "_keeper.py")
    out = _ps_cmdline(pid=pid)
    if out is None:
        return True          # 查不了就退一步信 tasklist（宁可不动）
    return want in out


def beat_age():
    try:
        return time.time() - os.path.getmtime(BEAT)
    except Exception:
        return 1e9


def start_keeper():
    """拉起 keeper。**必须用 python.exe，不能用 pythonw** ——
    2026-10-18 实测过两次：pythonw（无控制台）起的 keeper 在拉起 NapCat 之后会静默
    退出（beat 停在启动那秒、日志无异常、无 traceback），原因没查明。项目里
    已经把它记成红线，这里不重踩。代价是会弹一个控制台窗口，跟 start_all.bat 一样。

    QQBOT_GUARD_TARGET 可以换成别的脚本 —— 只给自测用（tools/_probe_guard.py
    要验证「死了能拉起来」但不想真把 NapCat 拉起来弹 UAC）。"""
    exe = PY
    target = os.environ.get("QQBOT_GUARD_TARGET") or os.path.join(ROOT, "_keeper.py")
    try:
        # 新控制台窗口：keeper 的日志本来就走 state/keeper.log，窗口只是给人看的
        subprocess.Popen([exe, "-u", target],
                         cwd=ROOT, close_fds=True,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
        return True, "已拉起 keeper（%s）" % os.path.basename(exe)
    except Exception as e:
        return False, "拉起失败：%r" % (e,)


def stop_keeper(pid):
    try:
        r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True, errors="replace",
                           timeout=25, creationflags=_NO_WIN)
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()[:120]
    except Exception as e:
        return False, repr(e)


def check(act=True):
    if not os.path.exists(WANT):
        return 0, "没有 keeper.want（主人没让 keeper 跑），什么都不做"
    pid = keeper_pid()
    alive = pid_alive(pid) and pid_is_keeper(pid)
    age = beat_age()
    if alive and age < STALE:
        return 0, "keeper 正常（PID %d，心跳 %.0fs 前）" % (pid, age)
    if alive and age >= STALE:
        msg = "keeper PID %d 还活着但心跳停了 %.0f 分钟 —— 判定卡死" % (pid, age / 60.0)
        log(msg)
        if not act:
            return 1, msg
        ok, info = stop_keeper(pid)
        log("  结束卡死的 keeper：%s %s" % (ok, info))
        time.sleep(2)
        ok2, info2 = start_keeper()
        log("  " + info2)
        return (0 if ok2 else 1), msg + "；" + info2
    msg = "keeper 不在了（pid=%s）" % (pid or "无")
    log(msg)
    if not act:
        return 1, msg
    ok, info = start_keeper()
    log("  " + info)
    return (0 if ok else 1), msg + "；" + info


def install():
    py = PYW if os.path.exists(PYW) else PY
    tr = '"%s" "%s"' % (py, os.path.join(HERE, "keeper_guard.py"))
    args = ["schtasks", "/Create", "/TN", TASK, "/TR", tr,
            "/SC", "MINUTE", "/MO", str(INTERVAL_MIN), "/F"]
    r = subprocess.run(args, capture_output=True, text=True, errors="replace",
                           timeout=60, creationflags=_NO_WIN)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        # 失败多半是权限：/RL HIGHEST 需要管理员，先试普通任务
        print("注册失败(rc=%s)：%s" % (r.returncode, out[:300]))
        print("提示：管理员窗口里再跑一次 --install；或用 taskschd.msc 手工建")
        return 1
    print("已注册计划任务 %s（每 %d 分钟检查一次 keeper）" % (TASK, INTERVAL_MIN))
    print(out[:300])
    return 0


def remove():
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], creationflags=_NO_WIN,
                       capture_output=True, text=True, errors="replace", timeout=60)
    print(((r.stdout or "") + (r.stderr or "")).strip()[:300])
    return 0 if r.returncode == 0 else 1


def status():
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK, "/FO", "LIST"], creationflags=_NO_WIN,
                       capture_output=True, text=True, errors="replace", timeout=60)
    if r.returncode != 0:
        print("计划任务 %s：未注册" % TASK)
    else:
        print((r.stdout or "").strip()[:600])
    print("keeper.want: %s" % ("有（允许自动拉起）" if os.path.exists(WANT) else "无（不会自动拉起）"))
    print("keeper.pid : %s" % (keeper_pid() or "无"))
    print("心跳       : %.0f 秒前" % beat_age())
    return 0


PIDFILE = os.path.join(STATE, "guard.pid")


def _write_pid():
    try:
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _other_loop_pid():
    """已经有别的守卫常驻进程了吗？（自启可能被两个入口同时拉起：Startup 的 vbs 和 lnk）

    2026-10-22 修：原来用 wmic 扫全表（这台机器根本没 wmic，一直静默失败）；
    第一版改成扫 powershell 全表又**把自己也算进去**，导致新守卫起来就退。
    现在改成「PID 文件 + 按 PID 查命令行」：既准又不会误判自己。
    """
    try:
        pid = int((open(PIDFILE).read() or "0").strip() or 0)
    except Exception:
        return 0
    if pid <= 0 or pid == os.getpid():
        return 0
    out = _ps_cmdline(pid=pid)
    if out is None or "keeper_guard.py" not in out:
        return 0          # 文件里的 pid 已经死了 / 被别的进程复用
    return pid


def _meme_ctl(sub, timeout):
    """调 meme_ctl.py 子命令，返回 (returncode, 合并输出)。异常不外泄。
    cwd 设为 meme_ctl.py 所在目录，让实验副本读到它自己的 meme_service.json。"""
    try:
        r = subprocess.run([MEME_PY, "-u", MEME_CTL, sub],
                           cwd=os.path.dirname(MEME_CTL) or None,
                           capture_output=True, text=True, errors="replace",
                           timeout=timeout, creationflags=_NO_WIN)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def _meme_brief(s, n=240):
    s = " ".join((s or "").split())
    return s[-n:] if len(s) > n else s


def _meme_log(event, detail="", quiet=False):
    """写 guard.log。quiet=True 时同事件连续重复不刷屏（健康态逐轮静默）。"""
    global _meme_last_event
    if quiet and event == _meme_last_event:
        return
    try:
        log("  meme守护 [%s] %s" % (event, detail))
    except Exception:
        pass
    _meme_last_event = event


def meme_guard_check():
    """每轮一次：确保本地 meme 服务健康。

    完全隔离：自带 try/except + 每个子进程调用的超时，任何异常都不上抛；
    绝不触碰 keeper/Bot/NapCat 的任何状态，绝不让 guard 主循环退出。

    只调用 meme_ctl.py 的 health / start 两个命令，把「防重复启动 / PID+命令行+
    marker 三重核验 / 端口占用判定 / 进程终止」全部交给现成的生命周期管理器，
    本函数不自己 kill 任何进程、不按端口杀、不重写管理器逻辑。
    """
    global _meme_start_inflight
    try:
        if not os.path.isfile(MEME_CTL):
            _meme_log("meme_service_health_failed",
                      "meme_ctl.py 不存在，跳过本轮：%s" % MEME_CTL, quiet=True)
            return

        # 1) 健康 → 直接复用，不重复启动（状态变化日志，健康态不逐轮刷屏）
        rc, out = _meme_ctl("health", MEME_HEALTH_TMO)
        if rc == 0:
            _meme_log("meme_service_healthy",
                      "服务健康，复用现有实例（不重复启动）", quiet=True)
            return

        # health 非 0 / 超时 / 取不到返回码：短暂无响应或服务不在
        _meme_log("meme_service_health_failed", "health rc=%s %s" % (rc, _meme_brief(out)))

        # 2) 服务正在启动 / 本 guard 已有启动流程 → 不重复拉起第二实例
        if _meme_start_inflight:
            _meme_log("meme_service_starting",
                      "检测到并发启动流程在进行，本轮不重复拉起")
            return

        # 3) 交给 meme_ctl.py start 判定（它内部先查 PID 存活、端口是否本服务，
        #    已跑=复用 rc0 / 端口他占=rc4 / 进程存活未就绪=rc3 / 真没跑才新拉起）。
        _meme_log("meme_service_starting", "调用 meme_ctl.py start（并发/占用判定交管理器）")
        _meme_start_inflight = True
        try:
            rc2, out2 = _meme_ctl("start", MEME_START_TMO)
        finally:
            _meme_start_inflight = False

        if rc2 == 0:
            # start 成功（可能是「复用已有」或「新拉起就绪」）：再次 health 确认
            rc3, out3 = _meme_ctl("health", MEME_HEALTH_TMO)
            if rc3 == 0:
                _meme_log("meme_service_started",
                          "start ok + health 复检通过；%s" % _meme_brief(out2))
            else:
                _meme_log("meme_service_health_failed",
                          "start 返回 ok 但 health 复检未过（rc=%s）%s"
                          % (rc3, _meme_brief(out3)))
            return
        if rc2 == 4:
            # 端口被其它程序占用：明确记录，绝不误杀
            _meme_log("meme_service_port_conflict",
                      "端口被其它进程占用，拒绝启动/不误杀；%s" % _meme_brief(out2))
            return
        if rc2 == 3:
            # 进程存活但尚未健康（正在启动/短暂无响应）：管理器已判不重复拉起
            _meme_log("meme_service_starting",
                      "进程存活但未就绪（管理器 rc=3，不拉起第二实例）；%s" % _meme_brief(out2))
            return
        # rc2 == 2 / None / 其它：启动失败或调用异常 —— 只记录，继续 keeper 原有流程
        _meme_log("meme_service_start_failed",
                  "start rc=%s %s" % (rc2, _meme_brief(out2)))
    except Exception as e:
        # 兜底：Meme 守护的任何异常都不允许冒泡到 guard 主循环
        try:
            _meme_log("meme_service_health_failed", "守护异常（已隔离）：%r" % (e,))
        except Exception:
            pass


def _safe_meme_check():
    """loop() 调用点外层再包一层 try/except（纵深防御）：Meme 守护失败绝不冒泡。"""
    try:
        meme_guard_check()
    except Exception as e:
        try:
            log("  meme守护 外层兜底异常（已隔离，不影响 keeper）：%r" % (e,))
        except Exception:
            pass


def loop():
    """常驻模式：每 INTERVAL_MIN 分钟查一次。

    这是**不依赖计划任务**的那条路（注册计划任务要管理员，而往「启动」文件夹里
    丢一个 vbs/lnk 不用）。开机由 Startup 里的入口拉起来，之后自己一直转。
    guard 自己死了没人管 —— 所以它被写得极简：不 import 项目代码、不碰网络、
    只读两个文件 + 偶尔起一个子进程。"""
    other = _other_loop_pid()
    if other:
        log("已有守卫常驻进程在跑（PID %d），本实例退出（防双开互相抢着拉 keeper）" % other)
        return 0
    _write_pid()
    log("守卫进入常驻模式（每 %d 分钟检查一次；pid=%d）" % (INTERVAL_MIN, os.getpid()))
    _safe_meme_check()        # 启动时执行一次 Meme 服务检查（方案A）
    while True:
        _write_pid()   # 顺手刷新，防止 pid 文件被清掉后出现双守卫
        try:
            code, msg = check(act=True)
            log("  巡检：%s" % msg)
        except Exception as e:
            log("  巡检异常：%r" % (e,))
        _safe_meme_check()    # 沿用现有循环周期的轻量检查（与 keeper 巡检完全隔离）
        time.sleep(INTERVAL_MIN * 60)


def main():
    a = sys.argv[1:]
    if "--install" in a:
        return install()
    if "--remove" in a:
        return remove()
    if "--status" in a:
        return status()
    if "--loop" in a:
        try:
            return loop()
        except KeyboardInterrupt:
            log("守卫常驻模式被停掉")
            return 0
    code, msg = check(act="--dry" not in a)
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main())
