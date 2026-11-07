"""qq-bot 保活：等 NapCat 的 3000 端口能连上后，前台拉起 bot.py；bot 退出就自动重启。

为什么单独写成 Python：原来的 start_all.bat 用 `curl` 做探活，依赖 PATH 里的 curl。
2026-09-13 实测踩坑：在某个双击出来的 cmd 窗口里 curl 起不来，报 cmd 的
「系统找不到指定的路径。」，于是保活窗口每 30 秒空转一次、**永远走不到启动 bot 那一行**
（bot.out.log 一直是 0 字节）。改用纯 Python（只用绝对路径、不依赖 PATH）后这类问题消失。

用 `subprocess` 起 bot 会把 stdout/stderr 落到 bot.out.log / bot.err.log，
所以 bot 自己的日志位置和以前一样。

2026-10-14 大改（应对"总是崩"）：
1. **账号离线自愈**：原来只探 3000 端口通不通，"账号被踢下线"时 keeper 完全瞎。
   现在以 `get_login_info` 的 `user_id` 为硬判据：拿不到且端口通 = 「掉线等扫码」，
   只弹二维码 + 写日志，**不重启容器**（票据已作废，重启只会刷掉正等人扫的码）。
   Docker 守护进程不在时尝试拉 Docker Desktop，之后每 60s 重试。
   （2026-10-26 起只留 Docker，注入版「taskkill 进程树 + 弹 UAC」那套已删除。）
2. **防双 keeper**：2026-10-13 22:39 实测出现过第二个 keeper 和第一个打架，
   六连拉 NapCat。现在启动先看 state/keeper.pid，旧 PID 还活着就直接退出。
"""
import atexit
import ctypes
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
LOG = os.path.join(HERE, "state", "keeper.log")
NAPCAT_PORT = 3000
BOT_PORT = 8088
WAIT_NAPCAT = 30
WAIT_RESTART = 10
# —— NapCat 拉起（2026-10-26 改：只留 Docker 容器版） ——
#
# 旧写法：拉起 launcher-win10.bat（把 NapCat 注入官方 QQ.exe，走 UAC 提权），
# 再由纳管逻辑精准杀进程树 + 打开磁盘上的二维码图片。腾讯早把这套按「外挂设备」
# 处理，每 ~2h 作废一次票据，所以 2026-10-22 换成了 Docker 容器版。
# 2026-10-26 主人明确「只用 docker 不要 inject」⇒ 注入版那条路全部删除，
# 连 NAPCAT_DIR / NAPCAT_LAUNCHER / NAPCAT_QRCODE / NAPCAT_LOGDIR / QR_GRACE
# 这些只为注入版存在的常量一起清掉。想找回：git checkout 2ca5c1c -- _keeper.py
LAUNCH_COOLDOWN = 300   # 两次自动拉起容器的最小间隔（秒）：别把 Docker 拉崩
OFFLINE_STRIKES = 3     # 连续 3 次（约 90s）探到账号离线才自愈：防掉线重连中的误伤
# 看门狗（秒）：主循环万一卡死（子进程句柄、阻塞调用），keeper 就彻底哑掉——
# 2026-10-16 07:03 被踢后 keeper 8 小时零动作就是主循环卡死的活案例，
# 当时 bot 空转到上午、群消息全发不出去。所以加一个独立线程兜底：
# 主循环超过这个秒数没心跳，就由看门狗自己探活 + 自愈（不杀自己的进程）。
WATCHDOG_TIMEOUT = 300
# 发送通道健康（1006514）：NapCat 的 get_status 只看「账号在不在线」，但 QQ 到服务器的
# 长连接（NTEvent）断掉时 online 仍是 true，而所有发送都报 1006514「网络连接异常」——
# 2026-10-16 14:02 就是这么假死了 20 分钟：get_status=online，实际一条都发不出去。
# 所以直接读 NapCat 自己的日志统计 1006514，连续失败就重拉 NapCat 重建长连接。
SEND_FAIL_CODE = "1006514"
# 1 次就自愈：实测 1006514 是持续性故障（2026-10-16 14:02 发生后 23 分钟仍未自愈），
# 而 bot 主动开场间隔约 65 分钟，等第二次太久。误判代价（偶发一次就重拉）由
# LAUNCH_COOLDOWN 300 秒兜着，比"消息发不出去"的代价小得多。
SEND_FAIL_STRIKE = 1
SEND_FAIL_WINDOW = 300    # 观察窗口（秒）
# 小鲸号：容器模式下用它做「发给自己的私聊」探活，以及 docker 侧快速登录的号。
NAPCAT_QLINE = "123450002"
# —— NapCat 运行方式（2026-10-26 起只有一种） ——
#   历史：inject = Windows 注入官方 QQ.exe（腾讯按外挂设备处理 ⇒ 每 ~2h 被踢）；
#         docker = Linux 无头容器（换设备指纹 + restart:always 自启）。
#   主人 2026-10-26 定调「只用 docker 不要 inject」，故 inject 分支已全部删除，
#   napcat.mode 这个配置键也退役（config 里留着也不读，见 _check_mode_leftover）。
DOCKER_BIN = os.environ.get("QQBOT_DOCKER_BIN") or ""   # 留空则回落 PATH 里的 docker
DOCKER_DIR = os.path.join(HERE, "docker-napcat")
WEBUI_PORT = 6099                # 容器版 NapCat 的扫码页端口
DOCKER_OFFLINE_HINT = 1800       # 容器模式离线提示的最小间隔（秒）：别每 30 秒喊一次
LOCK = os.path.join(HERE, "state", "keeper.pid")   # 防双 keeper 的 pid 锁
WANT = os.path.join(HERE, "state", "keeper.want")  # 「主人要它跑」的意图标记（给外部守卫看）

# —— 发送通道主动探活（2026-10-16 加）——
# 上面的 recent_send_fail 只抓「bot 自己发消息失败」——bot 安静（没人发消息）时通道早死了
# 也发现不了，等主人一来私聊，第一次回复必挂（今天 14:02 起就是这么假死了 30+ 分钟）。
# 所以 keeper 自己周期发一条「发给 bot 自己」的私聊探活（落到 bot 自己的会话里，不进主人
# 收件箱），直接解析 OneBot 返回的 retcode：==1006514 就判定长连接断、立即自愈。
# 比纯日志探测稳：不依赖 bot 是否正好在发消息，也不受窗口过期影响。
SEND_PROBE_INTERVAL = 300   # 每 5 分钟探一次（健康时也探，但只发给自己、不骚扰主人）
SEND_PROBE_PEER = NAPCAT_QLINE  # 兜底：查不到当前登录号时仍按老写法发给自己

# 运行期状态集中放一个字典，两个轮询循环（外层 + bot 存活期间的内层）共用
STATE = {"last_launch": 0.0, "offline_streak": 0,
         "last_beat": 0.0, "last_probe": 0.0,
         "grace_logged": None, "qr_opened_mtime": 0.0,
         # 2026-10-27：端口通但没登录（等扫码）时只提示/弹码一次，靠这个标记去重
         "need_scan_logged": False}

_SELF_QQ = {"qq": "", "at": 0.0}   # 当前登录号缓存（探活用，见 current_self_qq）

# 本地 HTTP 走空代理 opener：万一环境里被人设了 HTTP_PROXY，别让 localhost 请求绕出去
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


_LOG_Q = queue.Queue(maxsize=2000)
_LOG_LOCK = threading.Lock()
_BEAT = os.path.join(HERE, "state", "keeper.beat")


def _console_pump():
    """只负责往控制台打日志的独立线程。

    🔴 2026-10-18 事故：keeper 进程还活着、3 个线程全冻住、看门狗一声没吭，
    而且 **18:18 之后再没写过一行日志**，19:32 小鲸被踢下线时完全没人自愈。
    表现就是主循环被 print() 挂死。最可能是有人在 keeper 黑窗口里点/拖选过文字：
    Windows 控制台的 QuickEdit 会把「下一次写屏」直接挂起，整个进程就此冻住，
    连看门狗都写不出字（它也要走同一个 log()）。
    现在 log() 只做「同步落盘 + 丢进队列」，打印交给这条独立线程：
    控制台被冻住只冻它自己，主循环和看门狗照常干活。
    """
    while True:
        line = _LOG_Q.get()
        try:
            print(line, flush=True)
        except Exception:
            pass


threading.Thread(target=_console_pump, daemon=True, name="keeper-console").start()


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    # ① 先落盘：不依赖控制台，控制台冻住也照样有日志可查
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with _LOG_LOCK:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
    # ② 控制台只是顺手打一下：队列满了宁可丢一行，也绝不阻塞主循环
    try:
        _LOG_Q.put_nowait(line)
    except Exception:
        pass


def beat():
    """把心跳时间写进 state/keeper.beat —— 进程被冻住时外部一眼看得出。
    文件里的时间戳停住 = keeper 卡了，别去赖 NapCat。"""
    try:
        os.makedirs(os.path.dirname(_BEAT), exist_ok=True)
        with open(_BEAT, "w", encoding="utf-8") as f:
            f.write("%s pid=%d\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
    except Exception:
        pass


def port_open(port):
    s = socket.socket()
    s.settimeout(2)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()




# ——————————————————————— 防双 keeper（2026-10-14 加） ———————————————————————

def pid_alive(pid):
    """Windows 上判断进程是否还活着。注意别用 os.kill(pid, 0)：
    那在 Win 上会真的给进程发信号。用 OpenProcess + WaitForSingleObject(0)。"""
    SYNCHRONIZE = 0x00100000
    h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        # 句柄已置位(WAIT_OBJECT_0=0)=进程已退出；WAIT_TIMEOUT(258)=还活着
        return ctypes.windll.kernel32.WaitForSingleObject(h, 0) == 258
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def already_running():
    """已有别的 keeper 在跑就返回它的 PID。两条路都走：
    ① state/keeper.pid 锁文件（新版 keeper 写的）；
    ② 扫 python 进程里命令行含 _keeper.py 的（兼容还没有锁文件的旧 keeper，
    2026-10-14 过渡期用——否则新旧双 keeper 会打架重复拉 NapCat）。"""
    try:
        with open(LOCK, encoding="utf-8") as f:
            old = int(f.read().strip())
    except Exception:
        old = None
    if old and old != os.getpid() and pid_alive(old):
        return old
    return _scan_other_keeper()


def _scan_other_keeper():
    """扫系统里是否另有 python 进程在跑 _keeper.py（排除自己）。"""
    me = os.getpid()
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
           "| Where-Object { $_.CommandLine -match '_keeper\\.py' -and $_.CommandLine -notmatch ' -c ' "
           "-and $_.CommandLine -notmatch 'py_compile' } "
           "| Select-Object -ExpandProperty ProcessId")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                             capture_output=True, text=True, errors="replace",
                             timeout=25, creationflags=_NO_WIN).stdout
    except Exception:
        return None
    for tok in out.split():
        try:
            pid = int(tok)
        except ValueError:
            continue
        if pid != me:
            return pid
    return None


def _write_lock():
    try:
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        with open(LOCK, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _cleanup_lock():
    try:
        with open(LOCK, encoding="utf-8") as f:
            if f.read().strip() == str(os.getpid()):
                os.remove(LOCK)
    except Exception:
        pass


def _write_want():
    """写「我要 keeper 一直活着」的标记（外部守卫据此自动拉起）。"""
    try:
        os.makedirs(os.path.dirname(WANT), exist_ok=True)
        with open(WANT, "w", encoding="utf-8") as f:
            f.write("%s keeper 由 PID %d 启动\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
    except Exception:
        pass


def _drop_want():
    """主人明确停掉（Ctrl+C / --stop）时才撤标记：崩溃或关窗口不撤，守卫要能接手。"""
    try:
        os.remove(WANT)
    except Exception:
        pass


def _check_docker_wiring():
    """启动时检查一次接线：容器网关 IP 变了没有？

    WSL 的 vEthernet 地址每次重启可能变（NAT 重新分配）。变了会怎样：
    容器上报打不进 bot，而本机一切正常 ⇒ 现象是「bot 活着、群里没反应」，
    最容易被误判成「又掉线了」。这里只在启动时查一次并**只告警不改配置**
    （bot 已经读过 config 了，改了也不生效，还会让人以为已经修好）。
    """
    try:
        sys.path.insert(0, DOCKER_DIR)
        import dutil as _u
        ip = _u.host_ip()
        addrs = str((_cfg().get("listen_host") or ""))
        if ip and ip not in addrs:
            log("!! 容器网关 IP 变了（现在 %s，config 里没有）—— 容器上报会打不进来。"
                "跑一次 docker_wire.bat 再重启 bot。" % ip)
    except Exception:
        pass   # 探不到就算了，docker_status.bat 可人工确认


# ——————————————————————— NapCat 账号在线探活（2026-10-14 加） ———————————————————————

def _cfg():
    """现读 config.json（改配置不用重启 keeper）。"""
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


# keeper 是 pythonw.exe（无控制台）起的，subprocess 调 tasklist / netstat /
# docker.exe / powershell 这些控制台程序时，不加 CREATE_NO_WINDOW 就会"啪"地闪一个
# 黑框 —— 主人 2026-10-22 19:5x 反馈"Docker 时不时弹几个命令框出来"就是这个。
_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

_WARNED = set()
_DOCKER_OK = {"v": False, "t": 0.0}
# 🔴 2026-10-22 13:44 事故：Docker Desktop 没设开机自启（settings-store.json
# AutoStart=false），守护进程一没起来，keeper 探一次失败就「立刻」降级 inject，
# 当下拉起 Windows 注入版 NapCat ⇒ UAC 弹窗 + 二维码弹窗莫名糊主人一脸，
# 而且一旦降级就再也不回容器（方案 B 白配，账号继续每 2 小时被踢）。
# 现在改成：先自己把 Docker Desktop 拉起来，再给一段宽限期慢慢等它初始化，
# 宽限期内**死也不降级**，超过宽限期才兜底 inject。
_DOCKER_MISS_SINCE = {"t": 0.0}
_DOCKER_LAUNCH_TRIED = {"t": 0.0}
_THROTTLED = {}
_GUARD_CHECK_T = 0.0
# 拉 Docker Desktop 的重试间隔（秒）。原来是 300，太长：今天 18:34~18:45 那次
# Docker 一直没起来，keeper 却 5 分钟才肯再试一次，白等半天。
_DOCKER_RETRY_SEC = 60


def _warn_once(msg):
    """同一条告警只打一次，免得自愈循环里刷屏。"""
    if msg in _WARNED:
        return
    _WARNED.add(msg)
    log("!! " + msg)


def _log_throttled(key, sec, msg, prefix="!! "):
    """同一条消息最多 sec 秒打一次（比 _warn_once 适合「等待中」这类持续状态）。"""
    import time
    now = time.time()
    if now - _THROTTLED.get(key, 0.0) < sec:
        return
    _THROTTLED[key] = now
    log(prefix + msg)


_IMAGE_CACHE = {"t": {}, "v": {}}
_IMAGE_TTL = 20.0      # 秒：heal 循环每 30s 转一圈，别每圈都 spawn 一个 tasklist


def _image_running(name):
    """某个 exe 现在有没有在跑（名字形如 'Docker Desktop.exe'）。带 20s 缓存。"""
    import time
    now = time.time()
    if now - _IMAGE_CACHE["t"].get(name, 0.0) < _IMAGE_TTL:
        return _IMAGE_CACHE["v"].get(name, False)
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % name, "/NH"],
                             capture_output=True, text=True, errors="replace",
                             timeout=20, creationflags=_NO_WIN).stdout or ""
        v = name.lower() in out.lower()
    except Exception:
        v = False
    _IMAGE_CACHE["t"][name], _IMAGE_CACHE["v"][name] = now, v
    return v


def _try_start_docker_desktop():
    """守护进程不在时，帮主人把 Docker Desktop 拉起来（冷启动要 1~3 分钟）。

    keeper 是主人登录时由启动目录的 vbs 拉起的**普通用户进程**，没有沙箱限制，
    所以这里 Popen 出来的 Docker Desktop 能正常调 wsl.exe；反过来，AI 会话里
    Popen 拉起来的会被程序黑名单拦住（wsl.exe Access is denied）直接崩。
    拉不起来也不抛异常，交给宽限期逻辑兜底。

    🔴 2026-10-22 18:3x 事故补丁：**已经在跑就别再开第二个**。
    两个 Docker Desktop 实例会抢 `%APPDATA%\\Docker Desktop\\GPUPersistentCache`
    的锁，报「另一个程序正在使用此文件」→ GPU 进程崩 → 两个一起死。
    今天 Docker 每 2~80 分钟就"无痕消失"一次（连 Application Error 都没有），
    很可能就是 keeper 反复开第二个实例自己打自己。
    """
    import subprocess
    import time
    now = time.time()
    if _DOCKER_LAUNCH_TRIED["t"] and now - _DOCKER_LAUNCH_TRIED["t"] < _DOCKER_RETRY_SEC:
        return False
    _DOCKER_LAUNCH_TRIED["t"] = now
    if _image_running("Docker Desktop.exe"):
        _log_throttled("dd_running", 120,
                       "Docker Desktop.exe 已在运行但守护进程还没起来 —— 不再开第二个"
                       "（双实例会抢 GPU 缓存锁互杀），继续等")
        return False
    try:
        if DOCKER_DIR not in sys.path:
            sys.path.insert(0, DOCKER_DIR)
        import dutil
        exe = dutil.docker_desktop_exe()
    except Exception:
        exe = ""
    if not exe or not os.path.exists(exe):
        log("!! 想拉起 Docker Desktop，但没找到它的 exe（装了吗？）")
        return False
    try:
        subprocess.Popen([exe], close_fds=True,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP
        log("Docker 守护进程不在，已尝试拉起 Docker Desktop：%s（初始化要 1~3 分钟，期间不降级）" % exe)
        return True
    except Exception as e:
        log("!! 拉起 Docker Desktop 失败：%r" % (e,))
        return False


def _check_mode_leftover():
    """启动自检：config 的 napcat 段状态对不对（2026-10-26 起只认 docker）。

    两件事：
      ① 还留着已退役的 mode / docker_fallback ⇒ 提醒一次「留着不生效」
      ② **mode 缺失或不是 docker ⇒ 大声告警**。这条是 2026-10-26 21:22 事故的补丁：
         当时我删了 config 的 mode 键，但**旧版 keeper 还活着**，它读不到 mode
         就落到 NAPCAT_MODE_DEFAULT="inject"，于是走了 inject 分支去
         `pid_on_port(3000)` → taskkill → **PID 恰好是 com.docker.backend**，
         把 Docker 引擎本体杀了，容器跟着死，登录票据作废，主人被迫重扫一次码。
         现在代码里已经没有任何杀宿主进程的路径了，但这条自检留着当「第二道锁」：
         只要 mode 不对，日志第一屏就能看见，不用等出事才翻。
    """
    nc = _cfg().get("napcat") or {}
    mode = str(nc.get("mode") or "").strip().lower()
    if "mode" in nc or "docker_fallback" in nc:
        _warn_once("config.json 的 napcat.mode / napcat.docker_fallback 已退役"
                   "（2026-10-26 起只支持 docker 容器版）——留着不生效，可以删掉。"
                   "要恢复注入版请看 HANDOVER.md §11")
    if mode and mode != "docker":
        log("!! 【严重】config.json 的 napcat.mode = %r，但代码里只支持 docker。"
            "这个值不会生效（缺失/非法时一律按 docker 跑），下次改配置前请确认。"
            % mode)
    elif not mode:
        # 缺键是正常状态（退役后本来就该没有），只打一行低噪音的确认，方便排障时对账
        log("napcat.mode 已退役（缺键属正常）—— 按 docker 容器版运行")


def _ps_cmdlines(image):
    """用 PowerShell 拿某类进程的命令行。失败返回 None。

    🔴 2026-10-22：这台机器**没有 wmic.exe**（`FileNotFoundError`），
    原来用 wmic 的探测全都静默失败。PowerShell 带 _NO_WINDOW 调，不闪窗。
    """
    try:
        cmd = ("Get-CimInstance Win32_Process -Filter \"Name='%s'\" "
               "| Select-Object -ExpandProperty CommandLine" % image)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", cmd],
                           capture_output=True, text=True, errors="replace",
                           timeout=30, creationflags=_NO_WIN)
        if r.returncode != 0:
            return None
        return r.stdout or ""
    except Exception:
        return None


def _guard_alive():
    """守卫（tools/keeper_guard.py --loop）还在跑吗？"""
    out = _ps_cmdlines("pythonw.exe")
    if out is None:
        return True          # 查不了就别乱拉，宁可不动
    return ("keeper_guard.py" in out) and ("--loop" in out)


def ensure_guard_running():
    """keeper 顺带保证守卫活着。

    2026-10-22 补：守卫自己死了**没人拉它** —— Startup 里的 vbs 只在登录时跑一次。
    以前只能等下次登录才换上新代码（守卫的 subprocess 闪窗修复就卡在这一步）。
    现在由 keeper 代管：每 120s 查一次，不在就用 pythonw + CREATE_NO_WINDOW 重拉
    （不闪窗），也不会形成互拉循环（守卫只在 keeper 真死时才动作）。
    """
    global _GUARD_CHECK_T
    import time
    now = time.time()
    if now - _GUARD_CHECK_T < 120:
        return
    _GUARD_CHECK_T = now
    if _guard_alive():
        return
    try:
        subprocess.Popen([sys.executable, "-u",
                          os.path.join(HERE, "tools", "keeper_guard.py"), "--loop"],
                         cwd=HERE, close_fds=True,
                         creationflags=_NO_WIN | 0x00000008)   # +DETACHED_PROCESS
        log("守卫（keeper_guard.py）不在，已由 keeper 重新拉起（新版，不闪窗）")
    except Exception as e:
        log("!! 拉起守卫失败：%r" % (e,))


def docker_available(timeout=45, ttl=60.0):
    """docker 能不能用（守护进程在不在）。带 60s 缓存，免得每次自愈都 fork 一次。

    timeout 原来是 25s，内存紧张时 WSL 响应慢会**误判成 docker 死了** →
    keeper 去开第二个 Docker Desktop → 双实例抢 GPU 缓存锁 → 两个一起无痕消失。
    今天 18:3x 那种「每几分钟死一次」的 flapping 就是这么来的，故放宽到 45s。
    """
    import time
    now = time.time()
    if _DOCKER_OK["t"] and now - _DOCKER_OK["t"] < ttl:
        return _DOCKER_OK["v"]
    rc, out = _docker(["version", "--format", "{{.Server.Version}}"], timeout=timeout)
    ok = (rc == 0 and bool(out.strip()))
    _DOCKER_OK["v"], _DOCKER_OK["t"] = ok, now
    return ok


def docker_ready():
    """Docker 守护进程能不能用（唯一运行方式）。不可用就顺手拉一把并等宽限期。

    🔴 2026-10-26：这里原来是 napcat_mode()，inject/docker 二选一 + 可降级。
    主人定调「只用 docker 不要 inject」后，降级那条路已删干净：
    现在 Docker 不可用时**只有等**，不做任何别的替代——日志会一直催你开 Docker Desktop。

    宽限期（napcat.docker_grace_sec，默认 1800s = 30 分钟）只用来控制日志口吻
    （宽限期内"正在初始化"，过了"是不是装坏了"），不再影响走哪条路。
    """
    import time
    if docker_available():
        if _DOCKER_MISS_SINCE["t"]:
            log("Docker 守护进程已恢复 —— 容器模式继续")
        _DOCKER_MISS_SINCE["t"] = 0.0
        return True
    now = time.time()
    if not _DOCKER_MISS_SINCE["t"]:
        _DOCKER_MISS_SINCE["t"] = now
    # 🔴 2026-10-30 修的 bug：这里原来只在**第一次**发现不可用时拉一次
    #    （`if not _DOCKER_MISS_SINCE["t"]: _try_start_docker_desktop()`），
    #    之后整个等待期**一次都不再重试** —— 可日志却一直说「keeper 每 60s 自动重拉」。
    #    实测（10-30 23:44~次日 00:08，等了两小时）：`已尝试拉起 Docker Desktop`
    #    在 keeper.log 里**最后一条是 10-24**，根本没有重试。主人不在跟前就永久卡死。
    #    现在改成**周期性**调用；频次由 `_try_start_docker_desktop` 自己的
    #    `_DOCKER_LAUNCH_TRIED` + `_DOCKER_RETRY_SEC`（60s）节流控制，
    #    且它内部先查「Docker Desktop.exe 是否已在跑」⇒ **不会开第二个实例**
    #    （双实例抢 GPU 缓存锁互杀，那是 10-22 的事故，别再拆掉那道判据）。
    _try_start_docker_desktop()
    try:
        grace = float((_cfg().get("napcat") or {}).get("docker_grace_sec") or 1800)
    except Exception:
        grace = 1800.0
    waited = now - _DOCKER_MISS_SINCE["t"]
    if waited < grace:
        _log_throttled("docker_wait", 60,
                       "Docker 守护进程未就绪（已等 %ds/%ds）—— 没有注入版可退，只能等。"
                       "NapCat 暂不自愈；若它没自己起来，请从开始菜单打开 Docker Desktop（别用管理员）"
                       % (int(waited), int(grace)))
    else:
        _log_throttled("docker_no_fallback", 300,
                       "Docker 守护进程已等 %ds 仍不可用（超过宽限 %ds）—— 没有注入版可退，"
                       "bot 暂时不可用。keeper 每 60s 自动重拉 Docker Desktop；"
                       "想快点恢复请自己双击桌面的 Docker Desktop。"
                       % (int(waited), int(grace)))
    return False


def _docker_bin():
    """docker.exe 的位置：优先问 dutil（它认注册表 + 多种安装布局），再回落。

    10-22 09:55 踩过：Docker 用 --installation-dir=<工具安装目录>\\Docker 装出来是
    <工具安装目录>\\Docker\\resources\\bin\\docker.exe，**没有中间那层 Docker\\**，
    而这里原来写死的是带那一层的路径 —— keeper 会一直以为「docker 不可用」
    然后悄悄按 inject 兜底，容器模式等于白配。
    """
    try:
        if DOCKER_DIR not in sys.path:
            sys.path.insert(0, DOCKER_DIR)
        import dutil
        p = dutil.docker_bin()
        if p:
            return p
    except Exception:
        pass
    for p in (DOCKER_BIN,
              r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"):
        if p and os.path.exists(p):
            return p
    return "docker"


def _container_name():
    return str((_cfg().get("napcat") or {}).get("container") or "napcat")


def _docker(args, timeout=180):
    try:
        r = subprocess.run([_docker_bin()] + list(args), capture_output=True,
                           text=True, errors="replace", timeout=timeout,
                           cwd=DOCKER_DIR, creationflags=_NO_WIN)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return -1, repr(e)


def napcat_docker_restart():
    """容器模式自愈：restart 容器（compose 里 restart:always 负责兜底拉起）。"""
    if not docker_available(timeout=8, ttl=15.0):
        return False          # 守护进程不在，别在这空转（宽限期日志由 napcat_mode 负责）
    name = _container_name()
    rc, out = _docker(["restart", name], timeout=120)
    log("  容器自愈：docker restart %s rc=%s %s" % (name, rc, out[:120]))
    return rc == 0


def napcat_docker_up():
    """容器模式拉起：start；容器没了就 compose up -d 重建。"""
    if not docker_available(timeout=8, ttl=15.0):
        return False          # 同上：守护进程不在就别发命令，等宽限期
    name = _container_name()
    rc, out = _docker(["start", name], timeout=120)
    if rc == 0:
        log("  docker start %s 成功" % name)
        return True
    log("  docker start %s 失败(%s)，改用 compose 重建" % (name, out[:80]))
    rc2, out2 = _docker(["compose", "up", "-d"], timeout=300)
    log("  docker compose up rc=%s %s" % (rc2, out2[:160]))
    return rc2 == 0


def napcat_online():
    """POST NapCat OneBot 的 get_status。返回 True=账号在线 / False=被踢或离线 /
    None=探不到或状态不明（一律不动作，防误杀）。token 每次现读 config.json，
    改配置不用重启 keeper 就生效。"""
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            token = (json.load(f) or {}).get("token", "") or ""
    except Exception:
        token = ""
    req = urllib.request.Request(
        "http://127.0.0.1:3000/get_status",
        data=b"{}", method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token})
    try:
        with _OPENER.open(req, timeout=5) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("status") != "ok":
        return None          # token 错/接口异常：状态不明，不动作
    data = d.get("data")
    online = data.get("online") if isinstance(data, dict) else None
    if online is None:
        return None          # 没拿到明确的 online 字段，不动作
    return bool(online)




def kill_napcat_qq():
    """重启 NapCat 容器。

    🔴 2026-10-26：原来是「沿父链精准杀注入版 QQ 进程树」+ docker 双分支。
    inject 删掉后只剩一件事：重启容器。所以整个函数收敛成一行转发，
    保留函数名是因为 watchdog / napcat_alive_or_heal 两个调用点都在用它。
    """
    return napcat_docker_restart()


def napcat_login_id():
    """NapCat 当前登录的 QQ 号；没登录 / 探不到返回 0。

    🔴 2026-10-27 补这条判据的原因（当天 21:41 那次掉线的真凶）：
    等扫码的时候，NapCat 的 OneBot **HTTP 服务是通的** —— `GET /` 回 403、
    端口 3000 在 LISTENING（wslrelay + docker backend 在转发），所以
    `port_open(3000)` 是 True；而 `get_status` 给不出 `online` 字段，
    `napcat_online()` 只能返回 None（"状态不明，不动作"）。
    于是 keeper 对「掉线等扫码」**完全瞎**：21:41:59 被踢、21:42 开始每 2 分钟
    刷一张码，keeper 从 21:42 到 22:19 **一个字都没记**，主人只能自己发现。
    硬判据只有一个：`get_login_info` 拿不到 user_id = 没登录。
    """
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            token = (json.load(f) or {}).get("token", "") or ""
    except Exception:
        token = ""
    req = urllib.request.Request(
        "http://127.0.0.1:3000/get_login_info",
        data=b"{}", method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token})
    try:
        with _OPENER.open(req, timeout=5) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return 0
    if not isinstance(d, dict) or d.get("status") != "ok":
        return 0
    data = d.get("data")
    try:
        return int((data or {}).get("user_id") or 0)
    except Exception:
        return 0


def napcat_alive_or_heal():
    """NapCat 端口通的时候调：探账号在不在线，被踢了就把扫码页摆到人面前。
    返回 False = 刚重启了容器，调用方应 continue 让重拉流程接手。

    🔴 容器模式下**绝不因为"账号离线"去重启容器**。
    被踢是腾讯服务端把票据作废，重启容器不会让票据复活，反而会把 WebUI 上
    正在等人扫的那张码刷掉（码是登录态的一部分，容器一重启就换新的），
    于是主人扫的永远是上一张废码。正确的动作只有一个：把扫码页摆到人面前，等。
    """
    st = napcat_online()
    if st is True:
        if STATE["offline_streak"]:
            log("账号恢复在线（离线观察计数清零）")
            STATE["offline_streak"] = 0
        STATE["qr_opened_mtime"] = None      # 已登录 ⇒ 允许下次掉线时重新弹码
        STATE["need_scan_logged"] = False
        return True
    if st is None and not napcat_login_id():
        # 端口通 + get_status 状态不明 + get_login_info 拿不到账号 ⇒ 在等扫码。
        # 这时**绝不能重启容器**（会把主人正对着的那张码换掉），只需把码摆到他面前。
        STATE["offline_streak"] = 0
        if not STATE.get("need_scan_logged"):
            STATE["need_scan_logged"] = True
            STATE["qr_opened_mtime"] = None   # 允许这一次重新弹（上一轮可能已经弹过）
            log("NapCat 端口通，但 get_login_info 拿不到账号 —— 判定为掉线等扫码，"
                "不重启容器。码约 2 分钟换一张，过期就双击 看二维码.cmd")
            show_qr_once()
        return True
    if st is not False:
        # st is None 但 get_login_info 拿得到账号 ⇒ 只是 get_status 没回 online，账号在的
        if STATE["offline_streak"]:
            log("账号恢复在线（离线观察计数清零）")
            STATE["offline_streak"] = 0
        STATE["qr_opened_mtime"] = None
        STATE["need_scan_logged"] = False
        return True
    STATE["offline_streak"] += 1
    if STATE["offline_streak"] < OFFLINE_STRIKES:
        log("NapCat 端口通但账号离线（第 %d/%d 次确认，可能是掉线重连中，先观察）"
            % (STATE["offline_streak"], OFFLINE_STRIKES))
        return True
    now = time.time()
    if now - STATE.get("docker_hint", 0) >= DOCKER_OFFLINE_HINT:
        STATE["docker_hint"] = now
        log("账号离线（被踢/未登录）。容器模式不自愈重启 —— 需要人工扫一次码")
        STATE["qr_opened_mtime"] = None      # 允许重新弹
        show_qr_once()
    STATE["offline_streak"] = 0
    return True


def bot_healthy(timeout=10):
    """bot 的 /health 正不正常（接管已有 bot 时用）。"""
    try:
        with _OPENER.open("http://127.0.0.1:%d/health" % BOT_PORT, timeout=timeout) as r:
            body = r.read(600).decode("utf-8", "replace")
        return ('"ok"' in body) and ("true" in body)
    except Exception:
        return False


class _AdoptedBot(object):
    """keeper 重启时**接管**上一个 keeper 拉起的 bot，假装成一个子进程对象。

    2026-10-22 19:04 事故：守卫把 keeper 拉起来时，旧 keeper 起的 bot 还活着，
    旧逻辑死等 8088 释放 ⇒ keeper 永远卡在"等 30 秒再看"，NapCat 掉了也不管。
    现在端口通 + /health 正常就接管：wait() 在超时内轮询端口，
    端口没了 = "进程退出"（返回 1 让外层走重启流程），否则抛 TimeoutExpired 让监护继续。
    """

    def wait(self, timeout=0):
        deadline = time.time() + float(timeout or 0)
        while True:
            if not port_open(BOT_PORT):
                return 1
            if time.time() >= deadline:
                raise subprocess.TimeoutExpired("adopted-bot", timeout or 0)
            time.sleep(2)

    def poll(self):
        return None if port_open(BOT_PORT) else 1


def launch_napcat():
    """拉起 NapCat 容器（docker compose up）。

    🔴 2026-10-26：原来是「start launcher-win10.bat + -q 快速登录」的注入版写法，
    已随 inject 一起删除。现在只剩 napcat_docker_up() 一条路。
    """
    return napcat_docker_up()




def waiting_for_scan():
    """NapCat 是不是正在等扫码？是的话就别去重启容器。

    账号未登录时 NapCat 的 3000 端口本就不监听，keeper 只看端口会误判成"挂了"，
    于是重启容器 -> WebUI 上那张码换新 -> 主人刚扫的那张作废 -> 永远登不上。

    🔴 2026-10-26：原来有两种判据（注入版看磁盘二维码文件 mtime + 进程在不在，
    docker 看 6099 通不通）。inject 删掉后只剩后者：
    容器活着、6099 通 = NapCat 起来了只是没人扫码（码画在 WebUI 页面上）。
    """
    return port_open(WEBUI_PORT)


QR_IN_CONTAINER = "/app/napcat/cache/qrcode.png"
QR_LOCAL = os.path.join(HERE, "state", "_qr_live.png")
QR_LOCAL_BIG = os.path.join(HERE, "state", "_qr_big.png")


def _webui_token():
    """从 docker-napcat/.env 读 WebUI 口令（读不到就返回空串，只影响能不能免密进页面）。"""
    try:
        with open(os.path.join(HERE, "docker-napcat", ".env"), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("WEBUI_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _export_qr():
    """把容器里当前那张二维码捞到本地并放大 6 倍。返回 (可打开的路径 or None, 说明)。

    为什么放大：NapCat 画的 qrcode.png 只有 147x147，手机对着 1080p 屏扫经常对不上焦。
    NEAREST 插值不动像素，放大后仍是标准 QR 矩阵，扫得动。
    没装 PIL 就退回原图（能扫，只是小）。
    """
    name = _container_name()
    try:
        if os.path.exists(QR_LOCAL):
            os.remove(QR_LOCAL)   # 先删，免得 cp 失败时把上一张旧码当成新码打开
    except Exception:
        pass
    rc, out = _docker(["cp", "%s:%s" % (name, QR_IN_CONTAINER), QR_LOCAL], timeout=60)
    if rc != 0 or not os.path.exists(QR_LOCAL):
        return None, ("docker cp 失败 rc=%s %s" % (rc, (out or "").strip()[:120]))
    try:
        from PIL import Image
        im = Image.open(QR_LOCAL)
        if im.width < 300:
            im.convert("RGB").resize(
                (im.width * 6, im.height * 6), Image.NEAREST).save(QR_LOCAL_BIG)
            return QR_LOCAL_BIG, "ok（放大到 %dx%d）" % (im.width * 6, im.height * 6)
    except Exception as e:
        return QR_LOCAL, "ok（原图，PIL 不可用：%r）" % (e,)
    return QR_LOCAL, "ok"


def show_qr_once(m=None):
    """把「当前有效的二维码」直接弹到主人屏幕上（同一轮等扫码只弹一次，别刷屏）。

    2026-10-16 事故：原来只有 watch_qrcode() 会打开，而它只在"不在等扫码"的分支跑；
    一旦进了"等扫码"分支反而永远不给主人看码——主人干等、没法扫。所以补上主动打开。

    🔴 2026-10-26：inject 删掉后没有本地二维码图片了，一度只剩「拉起 WebUI 扫码页」，
    但那个页面**要口令**，主人打开只会看到登录框，等于没给码。2026-10-27 主人第三次
    抱怨「每次都是你给我二维码，我都不知道怎么调出二维码」，改成两条路一起走：
      1. docker cp 把容器里的 qrcode.png 捞出来 → 放大 6 倍 → os.startfile 弹图（首选）
      2. 顺带把带 token 的 WebUI 扫码页也开一个（图打不开时的兜底）
    参数 m 保留为兼容位（旧调用点会传 mtime），实际不再使用。
    """
    if STATE.get("qr_opened_mtime") == "webui":
        return
    STATE["qr_opened_mtime"] = "webui"
    shown = []

    path, why = _export_qr()
    if path:
        try:
            os.startfile(path)
            shown.append("二维码图片")
            log("已弹出登录二维码 %s（%s）——手机 QQ 扫码后还要在手机上点「确认登录」；"
                "码约 2 分钟换一张，过期就双击 看二维码.cmd 重新取" % (path, why))
        except Exception as e:
            log("打开二维码图片失败：%r —— 手动打开 %s" % (e, path))
    else:
        log("导出二维码失败：%s" % why)

    token = _webui_token()
    url = "http://127.0.0.1:%d/webui%s" % (WEBUI_PORT, ("?token=" + token) if token else "")
    try:
        import webbrowser
        webbrowser.open(url)
        shown.append("WebUI 扫码页")
    except Exception as e:
        log("打开扫码页失败：%r —— 手动访问 %s" % (e, url))

    if shown:
        log("扫码入口已就绪：%s" % " + ".join(shown))


def recent_send_fail():
    r"""最近 SEND_FAIL_WINDOW 秒内，NapCat 日志里 1006514 出现了几次。

    NapCat 的 fileLog 已在 2026-10-16 打开（config\napcat.json），
    日志形如 `10-16 14:02:32 [error] 小鲸 | 发生错误 ... "result": 1006514`。

    🔴 2026-10-26：日志全在容器里（不落宿主机盘），只读 `docker logs --tail 300`。
    原来还有一条「读宿主机 <工具安装目录>\NapCat\app\logs 最新文件」的分支，随 inject 一起删了。
    """
    rc, out = _docker(["logs", "--tail", "300", _container_name()], timeout=60)
    if rc != 0 or not out:
        return 0
    return _count_send_fail(out.splitlines()[-200:])


def _count_send_fail(lines):
    """在若干行日志里数「窗口期内出现了几次 1006514」。"""
    now = time.localtime()
    year = now.tm_year
    lines = list(lines)[-200:]
    # 先给每行算时间戳：多行错误栈里，`"result": 1006514` 自己那行是**没有**时间戳前缀的，
    # 时间戳在它上面几行的 `10-16 14:02:32 [error] ...` 上 ⇒ 必须"继承上一条时间戳"，
    # 否则永远匹配不到（2026-10-16 实测踩过：窗口放到 86400 秒仍是 0 次）。
    stamps = [None] * len(lines)
    cur = None
    for i, line in enumerate(lines):
        m = re.match(r"\s*(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)", line)
        if m:
            try:
                cur = time.mktime((year, int(m.group(1)), int(m.group(2)),
                                   int(m.group(3)), int(m.group(4)), int(m.group(5)),
                                   0, 0, -1))
            except Exception:
                cur = None
        stamps[i] = cur
    cnt = 0
    for i, line in enumerate(lines):
        if SEND_FAIL_CODE not in line or not stamps[i]:
            continue
        if time.time() - stamps[i] <= SEND_FAIL_WINDOW:
            cnt += 1
    return cnt


def current_self_qq():
    """NapCat 现在登录的是哪个号（探活要发给它自己）。

    2026-10-21 踩坑：探活对象写死成小鲸(123450002)，NapCat 换号登录成低空段云
    (123450004) 之后，每 5 分钟就往一个**不是好友**的号发私聊，NapCat 一直报
    `result 16 发送失败，请先添加对方为好友`，探活等于没探。
    """
    now = time.time()
    if _SELF_QQ["qq"] and now - _SELF_QQ["at"] < 600:
        return _SELF_QQ["qq"]
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            token = (json.load(f) or {}).get("token", "") or ""
    except Exception:
        token = ""
    req = urllib.request.Request(
        "http://127.0.0.1:3000/get_login_info", data=b"{}", method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token})
    try:
        with _OPENER.open(req, timeout=5) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        qq = str(((d.get("data") or {}).get("user_id")) or "").strip()
    except Exception:
        return _SELF_QQ["qq"] or SEND_PROBE_PEER
    if not qq:
        return _SELF_QQ["qq"] or SEND_PROBE_PEER
    if _SELF_QQ["qq"] and qq != _SELF_QQ["qq"]:
        log("NapCat 登录号变了: %s -> %s（探活对象跟着换）" % (_SELF_QQ["qq"], qq))
    _SELF_QQ["qq"], _SELF_QQ["at"] = qq, now
    return qq


def send_self_probe():
    """主动发一条「发给 bot 自己」的私聊探活，验证发送通道是不是真的通。

    1006514 是 QQ 到腾讯长连接断了，但 get_status 还显示 online——这种假在线只有
    「真的发一条消息」才能暴露。发给 bot 自己当前的登录号（`current_self_qq()`），
    探活消息落在 bot 自己的会话里、主人收件箱看不到，不骚扰。
    返回 True=通道正常 / False=1006514 死 / None=接口异常（不动作）。"""
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            token = (json.load(f) or {}).get("token", "") or ""
    except Exception:
        token = ""
    payload = json.dumps({"user_id": current_self_qq(),
                          "message": "[自检] 通道探活，无需回复"}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:3000/send_private_msg",
        data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token})
    try:
        with _OPENER.open(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    # 实测：死通道时 OneBot 返回 {"status":"failed","retcode":200,
    #   "message":"...EventRet:\n{\"result\": 1006514,\"errMsg\":\"网络连接异常!\"}\n"}
    # —— 真正的错误码 1006514 藏在 message/wording 文本里，retcode 本身只是 200，别只看 retcode。
    if not isinstance(d, dict):
        return None
    blob = "%s %s" % (d.get("message", ""), d.get("wording", ""))
    if "1006514" in blob:
        return False
    if d.get("status") == "ok" or d.get("retcode") == 0:
        return True
    return None


def send_channel_ok_or_heal(self_probe_dead=False):
    """发送通道自检：1006514 连击（recent_send_fail）或主动探活返回 False 就重拉 NapCat
    重建长连接。返回 True=正常（或刚做完自愈，调用方 continue 即可）。"""
    if not self_probe_dead:
        cnt = recent_send_fail()
        if cnt < SEND_FAIL_STRIKE:
            return True
        msg = "发送通道异常：最近 %d 秒内 %d 次 1006514「网络连接异常」" % (SEND_FAIL_WINDOW, cnt)
    else:
        msg = "主动探活发现发送通道 1006514「网络连接异常」"
    now = time.time()
    if now - STATE["last_launch"] < LAUNCH_COOLDOWN:
        return True
    log("!! %s（get_status 仍显示 online，但消息根本发不出去）—— 重启 NapCat 容器重建长连接" % msg)
    kill_napcat_qq()
    STATE["last_launch"] = now
    launch_napcat()
    return False


def watchdog():
    """独立守护线程：主循环万一卡死，由这里兜底。

    为什么需要：主循环里 `proc.wait(timeout=30)` 之类一旦卡住（Windows 子进程句柄偶发
    不释放），整个 keeper 就静默哑掉——不探活、不自愈、不写日志，外表看"进程还在"。
    2026-10-16 07:03 被踢后 keeper 8 小时零动作就是踩了这个坑，bot 空转到上午。

    策略：看主循环的心跳，超时就自己干 kill+relaunch。**不 os._exit**（那只会让 bat
    停在 pause 等按键，服务照样不回来）；自己修，修不了也留下日志让人看见。"""
    while True:
        time.sleep(60)
        try:
            idle = time.time() - STATE.get("last_beat", 0.0)
            if idle < WATCHDOG_TIMEOUT:
                continue
            log("!! 看门狗：主循环已 %.0f 秒无心跳（阈值 %ds），判定卡死，由看门狗兜底"
                % (idle, WATCHDOG_TIMEOUT))
            st = napcat_online()
            if port_open(NAPCAT_PORT) and st is not False:
                log("  看门狗复核：NapCat 端口通且账号在线，只是主循环卡了 —— "
                    "本次不动 NapCat（避免误杀在线会话）")
                STATE["last_beat"] = time.time()   # 归零，免得每分钟刷一次
                continue
            if waiting_for_scan():
                log("  看门狗：正在等扫码，不动")
                STATE["last_beat"] = time.time()
                continue
            log("  看门狗执行自愈：重启 NapCat 容器")
            kill_napcat_qq()
            STATE["last_launch"] = time.time()
            launch_napcat()
            STATE["last_beat"] = time.time()
        except Exception as e:
            log("看门狗异常（不影响主循环）：%r" % (e,))




def main():
    other = already_running()
    if other:
        log("检测到已有 keeper 在运行（PID %d），本实例 20 秒后退出"
            "（防双 keeper 反复重拉 NapCat；要换新版 keeper 请先结束旧 keeper）" % other)
        time.sleep(20)
        return 0
    _write_lock()
    atexit.register(_cleanup_lock)
    # keeper.want = 「主人要求 keeper 在跑」的意图标记，给外部守卫 tools/keeper_guard.py 看：
    # 有它 + 进程没了 ⇒ 拉起；没它 ⇒ 主人明确不要跑，守卫不许自作聪明复活。
    # 2026-10-22 00:39 事故：keeper 和 bot 一起消失，一小时没人发现，就是因为
    # 「应该跑」这件事只存在于主人的脑子里，机器上没有任何痕迹。
    _write_want()
    log("keeper 启动（探活走 python 的端口连通性，不依赖 curl；bot 端口 %d）" % BOT_PORT)
    _check_mode_leftover()
    _check_docker_wiring()
    threading.Thread(target=watchdog, daemon=True, name="keeper-watchdog").start()
    log("看门狗线程已启动（主循环超过 %d 秒无心跳就由它兜底自愈）" % WATCHDOG_TIMEOUT)
    while True:
        STATE["last_beat"] = time.time()
        beat()   # 心跳落文件：外部（我/主人）能一眼看出 keeper 是不是卡死了
        if not port_open(NAPCAT_PORT):
            now = time.time()
            if waiting_for_scan():
                # 正在等扫码就别动容器：重启会把 WebUI 上那张码换掉，主人扫的前功尽弃
                if STATE.get("grace_logged") != "webui":   # 每次进入等扫码只提示一次，别刷屏
                    STATE["grace_logged"] = "webui"
                    log("NapCat 端口未通，但 WebUI(6099) 开着 —— 判断为正在等扫码，"
                        "暂不重启容器（免得冲掉刚扫的码）")
                    show_qr_once()   # ★ 主动把扫码页打开给主人，别再让他干等
                time.sleep(WAIT_NAPCAT)
                continue
            STATE["grace_logged"] = None    # 离开等扫码状态，下次重新允许提示
            if now - STATE["last_launch"] >= LAUNCH_COOLDOWN:
                # 🔴 2026-10-30 修的第二个 bug：这里从来**不先确认 Docker 活着** ——
                #    `launch_napcat()` → `napcat_docker_up()` 的第一步就是
                #    `if not docker_available(...): return False`，而 **Docker Desktop 的
                #    拉起逻辑只写在 `docker_ready()` 里，可 `docker_ready()` 全项目
                #    没有任何调用点**（grep 只见定义）。结果：Docker 没起来时，
                #    主循环每 34 秒打一句「拉起容器」，然后**立刻失败返回**，
                #    Docker Desktop 一次都不会被尝试启动 —— 主人不在跟前就永久卡死。
                #    （日志里 `已尝试拉起 Docker Desktop` 最后一条是 10-24，这就是铁证：
                #      那几天是别处碰巧调过，后来没人调了。）
                #    现在先走 `docker_ready()`：它会在不可用时拉起 Docker Desktop
                #    （内部有 60s 节流 + 「已在跑就别开第二个」判据），并打出口吻合适的日志。
                if docker_ready():
                    log("NapCat 没应答（127.0.0.1:%d 连不上），拉起容器 napcat……" % NAPCAT_PORT)
                    if launch_napcat():
                        STATE["last_launch"] = now
                # Docker 还没就绪 ⇒ 不打「拉起容器」（那是假的：根本没发命令），
                # docker_ready() 自己已经打了「未就绪/超过宽限」的日志。
            time.sleep(WAIT_NAPCAT)
            continue
        # 端口通 ≠ 账号在线：被踢下线时端口照样活，这里补上在线探活与自愈
        if not napcat_alive_or_heal():
            time.sleep(WAIT_NAPCAT)
            continue
        # online=true 也可能发不出消息（QQ 长连接断 → 1006514），单独查发送通道
        if not send_channel_ok_or_heal():
            time.sleep(WAIT_NAPCAT)
            continue
        # 主动探活：bot 安静（没人发消息）时通道悄悄死了也发现不了，所以 keeper 自己周期发一条
        # 发给 bot 自己的私聊，API 返回里含 1006514 就立即自愈（落 bot 自己会话，不骚扰主人）
        now = time.time()
        if now - STATE["last_probe"] >= SEND_PROBE_INTERVAL:
            STATE["last_probe"] = now
            probe = send_self_probe()
            if probe is False and not send_channel_ok_or_heal(self_probe_dead=True):
                time.sleep(WAIT_NAPCAT)
                continue
        if port_open(BOT_PORT):
            # 🔴 2026-10-22 19:04 事故：keeper 由守卫重启时，上一个 keeper 拉起的 bot
            # 还活着（端口 8088 正常监听）。旧逻辑在这儿死等端口释放，于是 keeper 永远
            # 卡在"等 30 秒再看"里，NapCat 掉了也不管 —— 看门狗形同虚设。
            # 现在：端口通就顺带探一下 bot 的 /health，活着就**接管**它（进下面的监护循环），
            # 只有探不通才当"重复实例"继续等。
            if bot_healthy():
                log("检测到已有 bot 在跑且 /health 正常（PID 见端口 %d），本 keeper 接管监护" % BOT_PORT)
                # 借一个壳进程占位：heal 循环的 proc.wait() 需要一个对象，
                # 这里用 None 会让它立刻返回，所以改成轮询端口代替。
                proc = _AdoptedBot()
            else:
                log("端口 %d 已有人在监听但 /health 不通，等 %d 秒再看（避免起重复实例）"
                    % (BOT_PORT, WAIT_NAPCAT))
                time.sleep(WAIT_NAPCAT)
                continue
        else:
            log("NapCat 在线，启动 bot……")
            with open(os.path.join(HERE, "bot.out.log"), "ab") as out, \
                    open(os.path.join(HERE, "bot.err.log"), "ab") as err:
                proc = subprocess.Popen([PY, "-u", "bot.py"], cwd=HERE,
                                        stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                        creationflags=_NO_WIN)
        # 2026-09-18 修：此前这里直接 proc.wait() 阻塞，bot 活着期间绝不看 NapCat——
        # NapCat 半夜掉线 keeper 毫无察觉（真事故：09-18 19:31 NapCat 掉线，bot 空转到深夜）。
        # 改成轮询：bot 存活期间也盯 3000，掉了按同一冷却逻辑自动拉起。
        # 2026-10-14 再加：端口活着也探账号在不在线（被踢下线自愈）。
        rc = None
        while True:
            STATE["last_beat"] = time.time()
            beat()   # bot 存活期间也要续心跳，不然外部看不出 keeper 还活着
            ensure_guard_running()   # 顺带看着守卫，别让它悄悄死掉
            try:
                rc = proc.wait(timeout=WAIT_NAPCAT)
                break                       # bot 退出了 → 走下面的重启流程
            except subprocess.TimeoutExpired:
                pass
            if not port_open(NAPCAT_PORT):
                now = time.time()
                if waiting_for_scan():
                    if STATE.get("grace_logged") != "webui":
                        STATE["grace_logged"] = "webui"
                        log("bot 运行中但 NapCat 端口未通；WebUI(6099) 开着 —— 判断为正在等扫码，"
                            "暂不重启容器")
                        show_qr_once()   # ★ 主动打开扫码页给主人
                    continue
                STATE["grace_logged"] = None
                # 🔴 2026-10-26：inject 删掉后这里只剩一条路——拉容器。
                # 原来那段「注入版占着 3000/6099 就弹 UAC 杀掉」的补丁已删（没有注入版了）。
                if now - STATE["last_launch"] >= LAUNCH_COOLDOWN:
                    if not napcat_docker_up():
                        _log_throttled("docker_heal", 60,
                                       "NapCat 掉了，但容器起不来：Docker 守护进程不在"
                                       "（正在自动拉起/等待）。没有注入版可退，只能等 Docker。")
                continue
            napcat_alive_or_heal()
            if not send_channel_ok_or_heal():
                continue
        log("bot 退出了（exit=%s），%d 秒后自动重启……" % (rc, WAIT_RESTART))
        time.sleep(WAIT_RESTART)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("keeper 被 Ctrl+C 停掉")
        _drop_want()      # 主人亲手停的 ⇒ 撤标记，别让守卫 5 分钟后又把它拉起来
        sys.exit(0)
    except SystemExit:
        # main() 自己 return 了（比如 already_running 挡掉）：原样退出，但要留痕，
        # 否则控制台一关就再也说不清「它为什么没了」。
        rc = sys.exc_info()[1]
        log("keeper 正常退出（exit=%r）" % (getattr(rc, "code", rc),))
        raise
    except BaseException:
        # 🔴 2026-10-18：keeper 两次在拉起 NapCat 前后静默消失，控制台里没留下任何线索。
        # 以后崩溃必须把完整栈写进 state/keeper.log —— 控制台窗口关掉也查得到。
        log("keeper 崩溃：\n" + traceback.format_exc())
        raise
