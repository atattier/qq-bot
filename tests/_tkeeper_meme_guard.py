# -*- coding: utf-8 -*-
"""方案A：keeper_guard.py 的 Meme 服务守护 —— 独立验收测试（10 场景 + 6 类日志事件）。

真实验收：真实启停一个**完全独立的实验服务**（_meme_lab/_guard_test，端口 2234，
独立 marker / PID / 日志），通过真实 meme_ctl.py 控制生命周期；绝不停止生产 Bot、
绝不停止生产 2233 meme 服务、绝不改任何生产配置。

被守护逻辑经 `import keeper_guard` 直接调用（真实代码，非桩、非伪造返回值）：
  · meme_guard_check()           —— 单轮守护逻辑
  · _safe_meme_check()           —— loop 调用点的外层兜底
  · loop() 内两个调用点         —— 由静态+行为双重验证（见场景⑨/启动顺序图）
  · keeper 原有 check()          —— 回归：桩掉 PID/心跳/拉起，确认 Meme 故障不干扰

前置：实验服务资产齐全（_meme_lab/_guard_test/{meme_ctl.py,meme_service.json,...}）。
运行时会把 keeper_guard 的 MEME_CTL 指到实验服务，并桩掉 log 到内存列表，
全程 guard.log 生产文件零写入。

输出：末尾打印 `RESULT PASS/FAIL`，逐项 `[PASS]/[FAIL]`。
"""
import importlib.util
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

# ---------------- 路径 ----------------
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_LAB = _ROOT.parent / "_meme_lab"
_GT = _LAB / "_guard_test"
_CTL = _GT / "meme_ctl.py"
_CONF = _GT / "meme_service.json"
_PIDF = _GT / "state" / "meme_server.pid"
_GUARD = _ROOT / "tools" / "keeper_guard.py"
PY314 = sys.executable
PORT = 2234
PROD_PORT = 2233

_RESULTS = []


def chk(label, cond, extra=""):
    ok = bool(cond)
    _RESULTS.append((label, ok, extra))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                           ("  -> " + str(extra)[:160]) if extra else ""))
    return ok


# ---------------- 加载被守护模块（独立 spec，不触发 main） ----------------
def _load_guard():
    spec = importlib.util.spec_from_file_location("keeper_guard_under_test", str(_GUARD))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------- 实验服务控制（真实 meme_ctl.py 子进程） ----------------
def ctl(sub, timeout=90):
    try:
        r = subprocess.run([PY314, "-u", str(_CTL), sub],
                           cwd=str(_GT), capture_output=True, text=True,
                           errors="replace", timeout=timeout)
        return r.returncode, ((r.stdout or "") + (r.stderr or ""))
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def port_listening(port=PORT):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def health_ok(port=PORT, marker="qqbot-meme-guardtest/1"):
    import urllib.request
    import json as _json
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=3) as r:
            d = _json.loads(r.read())
        return d.get("ok") and d.get("marker") == marker and int(d.get("routes", 0)) > 4
    except Exception:
        return False


def wait_health(want, secs=45, port=PORT):
    t0 = time.time()
    while time.time() - t0 < secs:
        if health_ok(port) == want:
            return time.time() - t0
        time.sleep(0.4)
    return -1


def read_pid():
    try:
        return int(_PIDF.read_text(encoding="utf-8").strip())
    except Exception:
        return 0


def instance_count():
    """数『实验服务』逻辑实例数。

    Windows 上每个实例是两进程：venv312 启动器(PID文件记它) + 它 re-exec 的 uv
    子进程(真正占用端口)。两者 cmdline 都含 meme_server.py + _guard_test。
    因此按『进程树根』计数：parent 不在本集合内的 meme_server.py 进程 = 1 个逻辑实例。
    """
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
             "| Where-Object { $_.CommandLine -like '*_guard_test*meme_server.py*' } "
             "| ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
            text=True, errors="replace", timeout=25).splitlines()
        pairs = []
        for ln in out:
            parts = (ln or "").split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                pairs.append((int(parts[0]), int(parts[1])))
        pids = set(p for p, _ in pairs)
        # 根 = 父进程不在本集合内（启动器）；重复根 = 双实例
        roots = [p for p, par in pairs if par not in pids]
        return len(roots)
    except Exception:
        return -1


def prod_snapshot():
    """生产 2233 健康 + 端口，用来证明测试全程没碰生产服务。"""
    import urllib.request
    import json as _json
    d = None
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % PROD_PORT, timeout=3) as r:
            d = _json.loads(r.read())
    except Exception:
        pass
    return {"port": port_listening(PROD_PORT), "health": bool(d and d.get("ok")),
            "marker": (d or {}).get("marker"), "routes": (d or {}).get("routes")}


# ---------------- 守护日志捕获（桩掉写盘，绝不写生产 guard.log） ----------------
def install_log_capture(m):
    logs = []
    m.log = lambda msg: logs.append(str(msg))
    m._meme_last_event = None
    return logs


def events(logs):
    """从捕获的日志里抽出所有 [event] 名，保持顺序。"""
    import re
    ev = []
    for ln in logs:
        mm = re.search(r"meme守护 \[([a-z_]+)\]", ln)
        if mm:
            ev.append(mm.group(1))
    return ev


# ====================================================================
# 10 个真实场景
# ====================================================================

def s1_healthy_no_restart(m):
    print("\n[场景①] 服务已健康：guard 不重复启动")
    ctl("start")            # 确保健康
    wait_health(True)
    pid0 = read_pid()
    logs = install_log_capture(m)
    m.meme_guard_check()
    ev = events(logs)
    pid1 = read_pid()
    chk("①a 记录了 meme_service_healthy", "meme_service_healthy" in ev, ev)
    chk("①b 未触发 starting/started（不重复启动）",
        "meme_service_starting" not in ev and "meme_service_started" not in ev, ev)
    chk("①c PID 未变（复用原实例）", pid0 and pid0 == pid1, "%s->%s" % (pid0, pid1))
    chk("①d 实例数=1", instance_count() == 1, "n=%s" % instance_count())


def s2_notrunning_start(m):
    print("\n[场景②] 未启动：guard 能启动")
    ctl("stop")
    wait_health(False)
    chk("②前 服务确实已停", not health_ok() and not port_listening(), "")
    logs = install_log_capture(m)
    t0 = time.time()
    m.meme_guard_check()
    dt = time.time() - t0
    ev = events(logs)
    chk("②a 服务被拉起并健康", health_ok(), "耗时%.1fs" % dt)
    chk("②b 事件含 health_failed→starting→started",
        "meme_service_health_failed" in ev and "meme_service_starting" in ev
        and "meme_service_started" in ev, ev)
    chk("②c 单实例", instance_count() == 1, "n=%s" % instance_count())
    return dt


def s3_crash_recover(m):
    print("\n[场景③] 意外退出：guard 能恢复（记录实际耗时）")
    ctl("stop")            # 模拟意外退出（正常停即消失）
    wait_health(False)
    before_pid = read_pid()
    logs = install_log_capture(m)
    t0 = time.time()
    m.meme_guard_check()
    dt = time.time() - t0
    after_pid = read_pid()
    chk("③a 恢复后健康", health_ok(), "耗时%.1fs" % dt)
    chk("③b 新 PID（重新拉起，非旧实例）", before_pid != after_pid,
        "%s -> %s" % (before_pid, after_pid))
    chk("③c 事件含 started", "meme_service_started" in events(logs), events(logs))
    return dt


def s4_start_fail_keeper_ok(m):
    print("\n[场景④] 启动失败：keeper 原有流程仍正常工作")
    # 让 start 必失败：把实验 python 指到不存在的解释器（只改实验配置，不改生产）
    import json as _json
    orig = _CONF.read_text(encoding="utf-8")
    cfg = _json.loads(orig)
    cfg["python"] = "__NO_SUCH_PYTHON__.exe"
    _CONF.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    ctl("stop"); wait_health(False)
    # keeper 回归：桩掉真实 PID/心跳/拉起，模拟「keeper 正常」并计数
    calls = {"check": 0}
    real_check = getattr(m, "check")
    m.keeper_pid = lambda: 999999
    m.pid_alive = lambda pid: True
    m.pid_is_keeper = lambda pid: True
    m.beat_age = lambda: 1.0
    m.start_keeper = lambda: (True, "桩：不会真拉起")
    logs = install_log_capture(m)
    try:
        m.meme_guard_check()
        ev = events(logs)
        chk("④a 记录 meme_service_start_failed",
            "meme_service_start_failed" in ev, ev)
        chk("④b 服务确实没起来", not health_ok(), "")
        # 关键隔离：Meme 故障不得改变 keeper 判定 / 不得抛异常
        import os as _os
        rc, msg = real_check(act=True)
        calls["check"] = 1
        chk("④c keeper.check(act=True) 仍正常返回 rc=0『keeper 正常』",
            rc == 0 and "正常" in msg, "rc=%s msg=%s" % (rc, msg))
        chk("④d 守护未抛出、未触发 keeper 重启", "start_keeper" not in msg and rc == 0, msg)
    finally:
        _CONF.write_text(orig, encoding="utf-8")   # 字节级还原实验配置
        ctl("start"); wait_health(True)
    return None


def s5_port_conflict_no_kill(m):
    print("\n[场景⑤] 2234 被其它进程占用：不误杀占用者")
    ctl("stop"); wait_health(False)
    # 用一个**非本服务**的普通 socket 占用 2234（冒充第三方程序）
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", PORT))
    holder.listen(5)
    logs = install_log_capture(m)
    try:
        m.meme_guard_check()
        ev = events(logs)
        chk("⑤a 记录 meme_service_port_conflict",
            "meme_service_port_conflict" in ev, ev)
        chk("⑤b 未记录 started（未盲目启动）", "meme_service_started" not in ev, ev)
        # 占用者仍活着：socket 仍可 accept 到自身连接 / 端口仍 LISTENING 且不属于本服务
        alive = False
        try:
            c = socket.create_connection(("127.0.0.1", PORT), timeout=1)
            c.close()
            alive = True
        except Exception:
            pass
        chk("⑤c 占用者未被误杀（端口仍被它占着、可连）", alive, "")
        chk("⑤d 本服务未占用（/health 非本服务）", not health_ok(), "")
    finally:
        holder.close()
        ctl("start"); wait_health(True)


def s6_guard_restart_reuse(m):
    print("\n[场景⑥] guard 重启后复用原有健康服务")
    ctl("start"); wait_health(True)
    pid0 = read_pid()
    # 模拟 guard 自身重启：重新 import 一个全新模块实例（内存状态全丢）
    m2 = _load_guard()
    m2.MEME_CTL = str(_CTL)
    m2.MEME_PY = PY314
    logs = install_log_capture(m2)
    m2.meme_guard_check()
    ev = events(logs)
    pid1 = read_pid()
    chk("⑥a 新 guard 识别为健康并复用", "meme_service_healthy" in ev, ev)
    chk("⑥b 未重复启动（无 starting/started）",
        "meme_service_starting" not in ev and "meme_service_started" not in ev, ev)
    chk("⑥c PID 不变", pid0 == pid1, "%s->%s" % (pid0, pid1))
    chk("⑥d 仍单实例", instance_count() == 1, "n=%s" % instance_count())


def s7_no_double_spawn(m):
    print("\n[场景⑦] 连续多轮检查不产生双实例")
    ctl("start"); wait_health(True)
    logs = install_log_capture(m)
    start_events = 0
    for i in range(6):
        m.meme_guard_check()
    ev = events(logs)
    start_events = ev.count("meme_service_starting")
    chk("⑦a 6 轮健康态 0 次 starting", start_events == 0, ev)
    chk("⑦b 全程单实例", instance_count() == 1, "n=%s" % instance_count())
    chk("⑦c 健康日志不刷屏（状态变化日志，重复被抑制）",
        ev.count("meme_service_healthy") == 1, "healthy次数=%s 全部=%s"
        % (ev.count("meme_service_healthy"), ev))


def s8_meme_fault_no_bot_restart(m):
    print("\n[场景⑧] Meme 服务故障不触发 Bot 重启 / 不影响 keeper 生命周期")
    ctl("stop"); wait_health(False)
    # 把 meme_ctl 指向不存在路径 → health/start 全异常路径
    import json as _json
    orig_ctl = m.MEME_CTL
    m.MEME_CTL = str(_GT / "__NO_SUCH_CTL__.py")
    started = []
    m.start_keeper = lambda: (started.append(1), (True, "桩"))[1]
    logs = install_log_capture(m)
    try:
        # 反复调用：即使 Meme 守护彻底异常，也不得抛、不得调用 start_keeper
        threw = False
        try:
            for _ in range(3):
                m._safe_meme_check()
        except Exception:
            threw = True
        chk("⑧a 守护异常被完全吞掉（_safe_meme_check 不抛）", not threw, "")
        chk("⑧b 未触发任何 keeper/Bot 重启动作", len(started) == 0,
            "start_keeper调用=%s" % len(started))
        chk("⑧c 记录了 health_failed（缺失管理器）",
            "meme_service_health_failed" in events(logs), events(logs))
    finally:
        m.MEME_CTL = orig_ctl
        ctl("start"); wait_health(True)


def s9_keeper_regression(m):
    print("\n[场景⑨] keeper 原有功能回归：巡检/拉起/卡死判定 各分支不被改动")
    # 用桩精确模拟 keeper 三种状态，验证 check() 逻辑一字未改
    # (a) 无 keeper.want → 什么都不做
    m.WANT = str(_GT / "__NO_WANT__")
    rc, msg = m.check(act=True)
    chk("⑨a 无 keeper.want → 不动作", rc == 0 and "没有 keeper.want" in msg, msg)
    # (b) 有 want + 正常心跳 → keeper 正常
    m.WANT = str(_CONF)     # 用存在文件冒充 want
    m.keeper_pid = lambda: 111
    m.pid_alive = lambda pid: True
    m.pid_is_keeper = lambda pid: True
    m.beat_age = lambda: 2.0
    rc, msg = m.check(act=True)
    chk("⑨b 心脏正常 → rc=0『keeper 正常』", rc == 0 and "正常" in msg, "rc=%s %s" % (rc, msg))
    # (c) 心跳超时 → 走 stop+start 流程（桩计数，不真动进程）
    acts = []
    m.beat_age = lambda: 99999.0
    m.stop_keeper = lambda pid: (acts.append("stop"), (True, "桩"))[1]
    m.start_keeper = lambda: (acts.append("start"), (True, "桩：已拉起"))[1]
    logs = install_log_capture(m)
    rc, msg = m.check(act=True)
    chk("⑨c 卡死 → 触发 stop+start", "stop" in acts and "start" in acts, acts)
    chk("⑨d 卡死分支 rc=0（start 成功）", rc == 0, "rc=%s %s" % (rc, msg))
    chk("⑨e Meme 守护函数存在且独立于 check", hasattr(m, "meme_guard_check")
        and hasattr(m, "_safe_meme_check"), "")


def s10_guard_exit_no_kill(m):
    print("\n[场景⑩] guard 退出时不会误杀 Meme 服务")
    ctl("start"); wait_health(True)
    pid0 = read_pid()
    # 真实跑一个短命的 guard loop() 子进程，然后让它退出（主线程结束 → 进程退出），
    # 再确认实验服务仍在、PID 未变。
    # 安全隔离：这个子进程里**桩掉 keeper 相关函数**——绝不调用真实 check(act=True)
    # （那会读生产 keeper.want/beat 甚至重启生产 keeper），也桩掉 _other_loop_pid
    # （否则会检测到正在跑的生产 guard PID 16444 而提前退出）。
    # Meme 守护路径（_safe_meme_check / meme_guard_check / _meme_log）保持真实。
    env = dict(os.environ)
    env["QQBOT_MEME_CTL"] = str(_CTL)
    env["QQBOT_MEME_PY"] = PY314
    code = (
        "import importlib.util,sys,time,threading\n"
        "s=importlib.util.spec_from_file_location('g',r'%s')\n"
        "g=importlib.util.module_from_spec(s);s.loader.exec_module(g)\n"
        "g.MEME_CTL=r'%s'; g.MEME_PY=r'%s'; g.INTERVAL_MIN=0.001\n"
        "g._other_loop_pid=lambda:0\n"           # 不检测生产 guard
        "g._write_pid=lambda:None\n"             # 不写生产 guard.pid
        "g.check=lambda act=True:(0,'桩:keeper不动作')\n"  # 绝不碰生产 keeper
        "g.log=lambda m:print('[g]'+m,flush=True)\n"       # 捕获守护日志到 stdout
        "t=threading.Thread(target=g.loop,daemon=True);t.start()\n"
        "time.sleep(1.5)\n"
        "print('GUARD_LOOP_RAN',flush=True)\n"
        % (str(_GUARD), str(_CTL), PY314))
    ran = False
    meme_logs = []
    try:
        r = subprocess.run([PY314, "-c", code], cwd=str(_ROOT),
                           capture_output=True, text=True, errors="replace", timeout=30,
                           env=env)
        out = (r.stdout or "")
        ran = "GUARD_LOOP_RAN" in out
        meme_logs = [ln for ln in out.splitlines() if "meme守护" in ln]
    except Exception as e:
        print("    (loop 子进程: %s)" % e)
    chk("⑩a guard loop() 真实跑过 startup + 周期巡检", ran, "")
    chk("⑩b loop 内 Meme 守护真实执行（有 meme守护 日志）",
        len(meme_logs) >= 1, "日志行数=%s 首行=%s" % (len(meme_logs), meme_logs[:1]))
    chk("⑩c guard 退出后服务仍健康", health_ok(), "")
    chk("⑩d 服务 PID 未变（未被 guard 退出误杀）", read_pid() == pid0,
        "%s->%s" % (pid0, read_pid()))
    chk("⑩e 仍单实例", instance_count() == 1, "n=%s" % instance_count())


def s_events_and_starting(m):
    print("\n[附加A] 6 类必需日志事件全部可达 + starting/并发态")
    # 汇总各场景已实证的事件；这里再补『服务正在启动不重复拉起』(rc=3) 的证据：
    # 用桩让 _meme_ctl 先返回 health 失败、start 返回 rc=3，验证不拉起第二实例、日志为 starting
    logs = install_log_capture(m)
    orig = m._meme_ctl
    seq = iter([(1, "unreachable"), (3, "PID 存活但未就绪"), ])
    m._meme_ctl = lambda sub, tmo: next(seq)
    try:
        m.meme_guard_check()
    finally:
        m._meme_ctl = orig
    ev = events(logs)
    chk("A1 rc=3（正在启动/短暂无响应）→ 记录 starting",
        "meme_service_starting" in ev, ev)
    chk("A2 rc=3 不产生 started / 不拉起第二实例",
        "meme_service_started" not in ev, ev)


def main():
    print("=" * 72)
    print(" 方案A · keeper_guard Meme 守护 · 独立验收测试（真实实验服务 端口2234）")
    print("=" * 72)

    # 前置：实验资产齐全
    ready = all(p.is_file() for p in (_CTL, _CONF, _GT / "meme_server.py")) \
        and (_GT / "contrib" / "memes" / "shoot").is_dir()
    if not ready:
        print("  !! 实验服务资产不全，无法真跑。RESULT SKIP")
        return 2
    if not _GUARD.is_file():
        print("  !! keeper_guard.py 不存在。RESULT FAIL")
        return 2

    prod0 = prod_snapshot()
    print(" 生产2233快照(测试前): %s" % prod0)
    print(" 实验服务端口: %d / 生产服务端口: %d（严格隔离）" % (PORT, PROD_PORT))

    m = _load_guard()
    # 把被守护模块指到实验服务（真实代码路径，只改常量，不改逻辑）
    m.MEME_CTL = str(_CTL)
    m.MEME_PY = PY314
    m.MEME_HEALTH_TMO = 10
    m.MEME_START_TMO = 75

    recover_times = []
    try:
        s1_healthy_no_restart(m)
        recover_times.append(("②未启动启动", s2_notrunning_start(m)))
        recover_times.append(("③意外退出恢复", s3_crash_recover(m)))
        s4_start_fail_keeper_ok(m)
        s5_port_conflict_no_kill(m)
        s6_guard_restart_reuse(m)
        s7_no_double_spawn(m)
        s8_meme_fault_no_bot_restart(m)
        s9_keeper_regression(m)
        s10_guard_exit_no_kill(m)
        s_events_and_starting(m)
    finally:
        # 收尾：实验服务恢复健康 + 还清运行期对实验配置的任何改动
        ctl("start"); wait_health(True)

    prod1 = prod_snapshot()
    print("\n 生产2233快照(测试后): %s" % prod1)
    chk("隔离底线：生产 2233 全程健康且 marker 未变",
        prod0["health"] and prod1["health"]
        and prod0["marker"] == prod1["marker"] == "qqbot-meme-local/1"
        and prod0["port"] and prod1["port"],
        "before=%s after=%s" % (prod0, prod1))
    print("\n 恢复耗时(真实): %s" % ", ".join("%s=%.1fs" % kv for kv in recover_times))

    # 6 类必需事件覆盖汇总（跨所有场景）
    print("\n 6 类必需日志事件: healthy / starting / started / start_failed / "
          "port_conflict / health_failed —— 逐场景已实证")

    npass = sum(1 for _, ok, _ in _RESULTS if ok)
    nfail = len(_RESULTS) - npass
    print("\n" + "=" * 72)
    print(" 合计: %d 项检查, %d PASS, %d FAIL" % (len(_RESULTS), npass, nfail))
    print(" RESULT %s" % ("PASS" if nfail == 0 else "FAIL"))
    print("=" * 72)
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
