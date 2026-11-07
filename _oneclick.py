"""一键启动：确保 NapCat（QQ 网关）在线 → 拉起 bot（保活复用 _keeper.py）→ 带 token 打开本机控制台。

为什么单独一个文件：这三件事以前是三处手工动作 —— 起 NapCat、双击
start_all.bat、再自己去 config.json 里翻 bridge_token 拼出 /admin 的 URL。合成一条链路后，
主人只需要双击 start_qq.bat。

几个必须遵守的前提（都是踩出来的）：
- **顺序不能反**：NapCat 是 bot 的上游，登录态不通时 bot 起来也拿不到 self_id。
- **NapCat 只跑 Docker 容器**（注入版已删除，2026-10-26 起）：本文件的前置动作就是
  调 `_keeper.napcat_docker_up()`；Docker Desktop 本身没跑时要主人手动双击桌面图标。
- **保活不在这里重写**：直接复用 `_keeper.py`（探活/拉起/崩溃重启都已被 `_tboot.py` 测过）。
  本文件只负责「补上前置的 NapCat」和「补上后置的浏览器」，中间那段原样交给 keeper。
- **NapCat 认证**是 `Authorization: Bearer <config.token>`（注意：不是 bridge_token，那个只管 bot 自己的 /api）。

命令行开关（给回归测试和重启用，正常使用不需要传）：
    --check      只做前置检查（探 NapCat/bot、查登录态、验 token），不启动任何东西、不保活
    --no-browser 不弹浏览器（但仍会等 bot 就绪并打日志）
    --print-url  只打印控制台 URL（token 打码），用于验证拼装
    --restart    若 bot 已在跑，先结束它再拉新的（载入刚改的代码）。不带这个参数时，
                 bot 已在跑会**交互问一句**（15 秒不按当 N），键盘不可用则默认不动 —— 绝不静默杀进程。
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import _keeper

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")

NAPCAT_PORT = _keeper.NAPCAT_PORT      # 3000
BOT_PORT = _keeper.BOT_PORT            # 8088

WAIT_NAPCAT_START = 150                # 首次起容器：要过 Docker 初始化 + 可能要扫码
WAIT_BOT_READY = 240                   # 等 bot 起来（NapCat 还没就绪时 keeper 也会等，所以给宽一点）
HEALTH_URL = "http://127.0.0.1:%d/health" % BOT_PORT
ADMIN_URL = "http://127.0.0.1:%d/admin?token=%%s" % BOT_PORT


def log(msg):
    _keeper.log(msg)


def load_cfg():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log("读 config.json 失败：%s" % e)
        return {}


def mask(tok):
    if not tok:
        return "(空)"
    if len(tok) <= 6:
        return "*" * len(tok)
    return tok[:2] + "*" * (len(tok) - 4) + tok[-2:]


def admin_url(cfg, masked=False):
    tok = (cfg.get("bridge_token") or "")
    return ADMIN_URL % (mask(tok) if masked else tok)


def napcat_login_state(cfg):
    """查 NapCat 登录态。查不到就说查不到，别假装知道。返回 (状态, 账号或None)。"""
    base = (cfg.get("napcat_http") or "").rstrip("/")
    if not base:
        return ("未知（config 里没有 napcat_http）", None)
    req = urllib.request.Request(base + "/get_login_info")
    if cfg.get("token"):
        req.add_header("Authorization", "Bearer " + cfg["token"])
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        d = d.get("data") or d
        return ("已登录", "%s(%s)" % (d.get("nickname", "?"), d.get("user_id", "?")))
    except Exception as e:
        return ("查不到（%s）" % type(e).__name__, None)


def start_napcat():
    """拉起 NapCat —— 容器版（2026-10-26 起只有这一种）。

    原来是 `cmd /c start launcher-win10.bat`（注入版，要 UAC + 可能弹二维码图片）。
    inject 删掉后改成让 keeper 的 docker 逻辑去做，这里只是代理一下：
    docker compose up -d + 等 6099 起来。

    返回 True = 已提交启动（不代表已登录），False = Docker 都没起来。
    """
    ok = _keeper.napcat_docker_up()
    if not ok:
        log("拉起 NapCat 容器失败 —— Docker 守护进程不在？"
            "请双击桌面的 Docker Desktop（别用管理员），等它转绿再双击 start_qq.bat")
        return False
    log("已请求拉起 napcat 容器（Docker 里跑 Linux 无头 QQ）")
    return True


def wait_port(port, timeout, label, tick=3):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _keeper.port_open(port):
            return True
        log("等 %s（127.0.0.1:%d）…… 已等 %ds / 最多 %ds"
            % (label, port, int(time.time() - t0), timeout))
        time.sleep(tick)
    return False


def listener_pid(port):
    """谁在监听这个端口？返回 pid，拿不到返回 0。

    为什么不用 `state/bot.lock`：它是**独占文件锁**，运行中根本读不出来（PermissionError）。
    netstat -ano 是可靠的替代，并且能和下面的 tasklist 交叉验证是不是 python.exe。
    """
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                           encoding="gbk", errors="replace", timeout=25)
    except Exception as e:
        log("netstat 失败：%s" % e)
        return 0
    pat = ":%d " % port
    for line in (r.stdout or "").splitlines():
        if pat in line and "LISTENING" in line.upper():
            tail = line.split()
            if tail and tail[-1].isdigit():
                return int(tail[-1])
    return 0


def is_python(pid):
    """确认这个 pid 是 python.exe —— 防止误杀别的服务。"""
    if not pid:
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, encoding="gbk",
                           errors="replace", timeout=15)
        return "python.exe" in (r.stdout or "").lower()
    except Exception:
        return False


def kill_bot(pid, port=None):
    """结束旧 bot 进程。只杀 python.exe，其余一律拒绝（安全阀）。

    `port` 是"杀完等它释放"的端口，默认 bot 端口；回归测试用替身进程时会传别的端口。
    """
    port = port or BOT_PORT
    if not pid:
        log("没找到在跑的 bot 进程，跳过")
        return False
    if not is_python(pid):
        log("pid %d 不是 python.exe，拒绝结束（防止误杀别人的服务）" % pid)
        return False
    log("结束旧 bot（pid %d）…… 之后 keeper 会把它重新拉起来，新代码就生效了" % pid)
    try:
        r = subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True,
                           text=True, encoding="gbk", errors="replace", timeout=20)
    except Exception as e:
        log("taskkill 失败：%s" % e)
        return False
    log("taskkill rc=%s %s" % (r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()[:120]))
    if r.returncode != 0:
        return False
    t0 = time.time()
    while time.time() - t0 < 20 and _keeper.port_open(port):
        time.sleep(0.5)
    if _keeper.port_open(port):
        log("注意：端口 %d 仍被占用（可能还有别的实例）" % port)
    else:
        log("端口 %d 已释放" % port)
    return True


def ask_yes(prompt, timeout=15):
    """有键盘才问。非 tty（重定向/自动化）或超时一律当 no —— 绝不在这里阻塞测试。"""
    try:
        import msvcrt
    except Exception:
        return False
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return False
    except Exception:
        return False
    print(prompt, end="", flush=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                print(ch, flush=True)
                return ch.lower() == "y"
        except Exception:
            return False
        time.sleep(0.15)
    print("（%d 秒没按键，按 N 处理）" % timeout, flush=True)
    return False


def open_admin_when_ready(cfg, timeout=WAIT_BOT_READY, do_open=True):
    """后台等 bot 的 /health 通，然后开浏览器带 token 进控制台。

    do_open=False 时**仍然等、仍然打日志**，只是不真去开浏览器 —— 这样 `--no-browser`
    既能当"别弹我浏览器"用，也能让回归测试完整走一遍「等就绪」这条路径。
    """
    def worker():
        t0 = time.time()
        n = 0
        while time.time() - t0 < timeout:
            if _keeper.port_open(BOT_PORT):
                try:
                    with urllib.request.urlopen(HEALTH_URL, timeout=4) as r:
                        if r.status == 200:
                            if not do_open:
                                log("控制台就绪（--no-browser：跳过开浏览器）：%s"
                                    % admin_url(cfg, masked=True))
                                return
                            log("控制台就绪，打开浏览器：%s" % admin_url(cfg, masked=True))
                            ok = False
                            try:
                                ok = webbrowser.open(admin_url(cfg))
                            except Exception as e:
                                log("webbrowser 抛错：%s" % e)
                            if not ok:
                                # 开不起来就把完整链接打到本机控制台（只在这个窗口里）
                                log("浏览器没自动打开。请手动访问：%s" % admin_url(cfg))
                            return
                except Exception:
                    pass
            n += 1
            if n % 20 == 0:
                log("还在等 bot 就绪（已 %ds / 最多 %ds）……" % (int(time.time() - t0), timeout))
            time.sleep(1.5)
        log("等 %ds 仍未见 bot 就绪，控制台没自动打开；bot 起来后手动开也行（%s）"
            % (timeout, admin_url(cfg, masked=True)))

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    return th


def main(argv):
    check_only = "--check" in argv
    no_browser = "--no-browser" in argv
    print_url = "--print-url" in argv
    restart = "--restart" in argv

    cfg = load_cfg()

    if print_url:
        print(admin_url(cfg, masked=True))
        return 0

    log("================ 一键启动 ================")

    # 1) 前置：NapCat
    if _keeper.port_open(NAPCAT_PORT):
        log("NapCat 已在运行（127.0.0.1:%d 通），跳过启动" % NAPCAT_PORT)
    else:
        log("NapCat 没在跑 —— 先把它拉起来（bot 的上游）")
        if check_only:
            log("--check：只报不做，不启动 NapCat")
            return 1
        if not start_napcat():
            return 1
        if not wait_port(NAPCAT_PORT, WAIT_NAPCAT_START, "NapCat 就绪"):
            log("NapCat 容器在 %ds 内没就绪。常见原因：Docker 还在初始化 / 需要扫码登录 / "
                "容器反复重启（跑 docker_status.bat 看容器状态）。" % WAIT_NAPCAT_START)
            log("不退出，继续交给 keeper 等（它每 30 秒重试一次）")

    st, acct = napcat_login_state(cfg)
    log("NapCat 登录态：%s%s" % (st, ("  账号 " + acct) if acct else ""))
    if acct is None and not check_only:
        log("提示：若 NapCat 窗口在等扫码，请扫一下；不然 bot 连上也没群消息。")

    # 2) token 自检（只报状态，不回明文）
    tok = cfg.get("bridge_token") or ""
    log("bridge_token：%s（%d 位，控制台链接用）" % ("已配置" if tok else "未配置", len(tok)))
    bot_up = _keeper.port_open(BOT_PORT)
    pid = listener_pid(BOT_PORT) if bot_up else 0
    log("bot 端口 %d 现在：%s%s" % (
        BOT_PORT, "已在监听" if bot_up else "未监听",
        ("（pid %d，%s）" % (pid, "python.exe 确认" if is_python(pid) else "不是 python.exe，不会动它"))
        if pid else ""))

    if check_only:
        log("--check 结束：未启动任何进程、未开浏览器、未杀进程")
        return 0

    # 2.5) 已在跑 → 问要不要重启（默认不动，避免白杀一个正在干活的 bot）
    if bot_up:
        if restart:
            log("--restart：要求重启已在跑的 bot")
            kill_bot(pid)
        elif ask_yes("bot 已在跑（pid %d）。要重启以载入新代码吗？[y/N] " % pid, 15):
            kill_bot(pid)
        else:
            log("照旧用现在这个 bot（没重启）。改过代码要它生效，就用 --restart 再跑一次")

    # 3) 后置：等 bot 就绪 → 开控制台（后台线程，别挡住下面的保活）
    if no_browser:
        log("--no-browser：仍会等 bot 就绪并打日志，但不开浏览器")
    open_admin_when_ready(cfg, do_open=not no_browser)

    # 4) 中间那段原样交给 keeper：探活 → 起 bot → 崩了自动重启（阻塞）
    log("移交 keeper 保活（这个窗口别关；关了就没人看 bot 死活了）")
    try:
        _keeper.main()
    except KeyboardInterrupt:
        # 这里是主人亲手 Ctrl+C 停的 —— 撤掉 keeper.want，否则外部守卫
        # （tools/keeper_guard.py）5 分钟后又会把它拉起来，主人会以为「关不掉」。
        _keeper._drop_want()
        raise
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("一键启动被 Ctrl+C 停掉", flush=True)
        sys.exit(0)
