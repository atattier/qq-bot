# -*- coding: utf-8 -*-
"""方案 B 一键落地：把 NapCat 从「Windows 注入版」搬到「Docker Linux 无头容器」。

为什么要有这个脚本：方案 B 的手工步骤有 8 步（开 WSL 功能 → 重启 → 装 Docker →
启动守护进程 → compose up → 扫码 → 接线 → 切 keeper 模式），任何一步漏了都表现为
「容器起来了但 bot 一条消息都收不到」，很难查。这里把 8 步拆成**可重入的 stage**，
进度记在 docker-napcat/.bootstrap-state.json 里：跑一半断了，再跑一次接着来。

    python tools/docker_bootstrap.py            # 从当前进度继续（幂等，随便跑几次都行）
    python tools/docker_bootstrap.py --check    # 只体检，一个字节都不写
    python tools/docker_bootstrap.py --reset    # 清掉进度，从头来
    python tools/docker_bootstrap.py --stage install   # 只跑某一段

阶段与是否需要管理员：
    env      准备 .env / 目录                     不需要
    features 开 WSL + 虚拟机平台（要重启）        需要管理员
    install  装 Docker Desktop                    需要管理员
    daemon   启动 Docker Desktop 并等它就绪       不需要（但要有桌面会话）
    compose  起 napcat 容器                       不需要
    wire     双向接线（改 bot 配置 + NapCat 配置） 不需要
    switch   停注入版 NapCat、keeper 切 docker 模式 需要管理员（要 taskkill 提权进程）
    verify   端到端验收（调 docker_doctor）        不需要

⚠️ features 之后**必须重启电脑**，脚本会以退出码 10 明确告诉你「重启后再跑一次」。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "docker-napcat"))

import dutil as U

STATE_FILE = os.path.join(U.HERE, ".bootstrap-state.json")


def _boot_epoch():
    """这次开机的时刻（秒）。用 GetTickCount64 反推，不需要管理员。

    用途：主人看到「请重启」之后如果直接又双击一次（没重启），install 会白跑
    十分钟再失败。记下 need_reboot 那一刻的开机时刻，下次跑一对比就知道重启了没。
    """
    try:
        import ctypes
        return time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:
        return 0.0

DOCKER_DESKTOP = [
    r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
]
WINGET_ID = "Docker.DockerDesktop"
DOCKER_DL = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
EXIT_NEED_REBOOT = 10

STAGES = ["env", "features", "install", "daemon", "compose", "wire", "switch", "verify"]


# ------------------------------------------------------------------ 基础设施

def log(msg=""):
    print(msg, flush=True)


def head(title):
    log("")
    log("=" * 62)
    log(" " + title)
    log("=" * 62)


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def state_load():
    return U.load_json(STATE_FILE, {}) or {}


def state_save(st):
    st["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    U.save_json(STATE_FILE, st)


def mark(stage, ok=True, note=""):
    st = state_load()
    st.setdefault("stages", {})[stage] = {
        "ok": bool(ok), "at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": note[:300]}
    state_save(st)


def done(stage):
    return bool((state_load().get("stages") or {}).get(stage, {}).get("ok"))


def feature_state(name):
    """dism 查功能状态，返回 'Enabled'/'Disabled'/''（查不到）。"""
    rc, out = U.run(["dism.exe", "/Online", "/Get-FeatureInfo", "/FeatureName:" + name],
                    timeout=180)
    if rc != 0:
        return ""
    for line in out.splitlines():
        if ":" in line and line.strip().lower().startswith("state"):
            return line.split(":", 1)[1].strip()
    return ""


def wait_for(fn, timeout, step=3, label=""):
    """轮询直到 fn() 为真或超时。返回 bool。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return True
        left = int(timeout - (time.time() - t0))
        if label:
            log("   … %s（还剩 %ds）" % (label, left))
        time.sleep(step)
    return False


# ------------------------------------------------------------------ stages

def stage_env(st):
    head("① 准备 .env 与目录")
    if not os.path.exists(U.ENV_FILE):
        if not os.path.exists(U.ENV_EXAMPLE):
            return False, "缺 .env.example，没法生成 .env"
        with open(U.ENV_EXAMPLE, "r", encoding="utf-8") as a, \
                open(U.ENV_FILE, "w", encoding="utf-8", newline="\n") as b:
            b.write(a.read())
        return False, ("已生成 docker-napcat/.env —— 打开它把 NAPCAT_ACCOUNT 改成机器人 QQ 号、"
                       "WEBUI_TOKEN 改成你自己的口令，保存后再跑一次本脚本")
    env = U.read_env()
    uin = U.uin_from_env_or_cfg()
    if not uin:
        return False, "docker-napcat/.env 里 NAPCAT_ACCOUNT 是空的，填上机器人 QQ 号再来"
    for d in (U.NAPCAT_CFG_DIR, U.DATA_DIR, os.path.join(U.HERE, "logs")):
        os.makedirs(d, exist_ok=True)
    tok = env.get("WEBUI_TOKEN") or ""
    log("   QQ 号: %s" % uin)
    log("   WebUI 口令: %s" % ("已设置（%d 位）" % len(tok) if tok and tok != "napcat"
                              else "⚠️ 还是默认/空口令，6099 端口谁都能打开扫码页"))
    log("   目录: config/ .config/ logs/ 已就绪")
    return True, ""


def stage_features(st):
    head("② 启用 WSL2 与虚拟机平台")
    if not is_admin():
        return False, ("这一步要管理员权限。别直接跑 python，"
                       "双击 install_docker.bat（它会自己提权）")
    need = []
    for feat, label in (("Microsoft-Windows-Subsystem-Linux", "WSL"),
                        ("VirtualMachinePlatform", "虚拟机平台")):
        cur = feature_state(feat)
        if cur == "Enabled":
            log("   %s 已启用，跳过" % label)
            continue
        log("   启用 %s …" % label)
        rc, out = U.run(["dism.exe", "/Online", "/Enable-Feature",
                         "/FeatureName:" + feat, "/All", "/NoRestart"], timeout=900)
        # 0=成功 3010=成功但要重启
        if rc not in (0, 3010):
            return False, "启用 %s 失败(rc=%s)：%s" % (label, rc, out[-300:])
        need.append(label)
        if rc == 3010:
            st["need_reboot"] = True
    # 🔴 刚启用功能时**立刻**返回，别顺手去跑 wsl 子命令：
    #    2026-10-22 09:45 实测，功能启用后 wsl.exe 会自己拉一遍安装包并卡在
    #    「正在安装…请重启」，`wsl --set-default-version` 返回 rc=1、输出还是
    #    UTF-16 乱码，`wsl --update` 又白等好几分钟。重启后再做这些才有意义。
    if need:
        st["need_reboot"] = True
        st["boot_at_need_reboot"] = _boot_epoch()
        return True, "已启用 %s —— **必须重启电脑**（重启后 wsl 相关的活会自动接着做）" % "、".join(need)
    return True, ""


def _install_via_winget(loc=""):
    winget = U.find_exe("QQBOT_WINGET_BIN", [], ("winget.exe",))
    if not winget:
        return False, "没有 winget"
    args = [winget, "install", "-e", "--id", WINGET_ID, "--silent",
            "--accept-package-agreements", "--accept-source-agreements"]
    if loc:
        args += ["--location", loc]
    rc, out = U.run(args, timeout=1800)
    log("   winget rc=%s%s" % (rc, ("（带 --location %s）" % loc) if loc else ""))
    for line in (out or "").splitlines()[-6:]:
        log("   | " + line)
    return rc == 0, out[-400:]


def _installer_cache():
    """安装包缓存位置：优先 QQBOT_TOOL_DIR/DockerInstaller（重启/清 TEMP 都不会丢），
    老位置 %TEMP% 作为兜底。620MB 的包，重下一次要好几分钟。"""
    stable = os.path.join(os.environ.get("QQBOT_TOOL_DIR")
                          or os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                          "DockerInstaller", "DockerDesktopInstaller.exe")
    try:
        os.makedirs(os.path.dirname(stable), exist_ok=True)
        # 老位置（%TEMP%）已经有下好的包就搬过来，别为了换个目录再下一次
        old = os.path.join(os.environ.get("TEMP") or "", "DockerDesktopInstaller.exe")
        if (not os.path.exists(stable) and old and os.path.exists(old)
                and os.path.getsize(old) > 50 * 1024 * 1024):
            import shutil
            log("   把 %TEMP% 里的安装包搬到 %s（重启不会被清）" % os.path.dirname(stable))
            shutil.move(old, stable)
        return stable
    except Exception:
        return os.path.join(os.environ.get("TEMP") or ROOT, "DockerDesktopInstaller.exe")


def _proxy_candidates():
    """下载用的代理候选。

    为什么不是「直接用默认」：这台机器上 `HTTP_PROXY` 环境变量指向的是**别的程序**
    塞进来的本地代理（实测会 SSL EOF 断流），而系统代理（FlClash 127.0.0.1:10808）
    和直连都能通。所以按「环境默认 → 常见本地代理 → 直连」依次试，谁通用谁。
    """
    out = [None]
    for p in ("http://127.0.0.1:10808", "http://127.0.0.1:7890", "http://127.0.0.1:7897"):
        out.append({"http": p, "https": p})
    out.append({})
    return out


def _open_url(url, timeout=120, headers=None):
    """按候选代理依次尝试打开 url，返回 (响应对象, 说明)。"""
    import urllib.request
    last = ""
    for i, proxies in enumerate(_proxy_candidates()):
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
            req = urllib.request.Request(url, headers=headers or {
                "User-Agent": "Mozilla/5.0 qqbot-bootstrap"})
            return op.open(req, timeout=timeout), ("直连" if proxies == {} else
                                                   ("默认" if proxies is None else
                                                    str(proxies.get("https"))))
        except Exception as e:
            last = "%r" % (e,)
            log("   通道 %d 不通（%s），换下一个" % (i, str(e)[:80]))
    return None, last


def _download_installer():
    """下官方安装包到 TEMP，返回 (ok, 路径或错误)。

    先写 .part 再改名：断线/超时留下的半截文件如果直接叫 .exe，下次会被
    「文件已存在且够大」的判断骗过去，装的时候才炸。这里按 Content-Length 校验完整才改名。
    """
    dst = _installer_cache()
    part = dst + ".part"
    log("   下载官方安装包（约 620MB，慢就等会儿）…")
    resp, how = _open_url(DOCKER_DL, timeout=120)
    if resp is None:
        return False, "所有下载通道都不通：%s" % how
    try:
        with resp, open(part, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            last = 0
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if got - last >= 100 * 1024 * 1024:
                    last = got
                    log("   … %.0f%%（%dMB）" % (got * 100.0 / total if total else 0,
                                                got // (1024 * 1024)))
        if total and os.path.getsize(part) != total:
            return False, "下载不完整（%d/%d 字节）" % (os.path.getsize(part), total)
        os.replace(part, dst)
        log("   ✅ 安装包已就绪（走 %s，%.0fMB）" % (how, os.path.getsize(dst) / 1048576.0))
        return True, dst
    except Exception as e:
        try:
            os.remove(part)
        except Exception:
            pass
        return False, "下载中断：%r" % (e,)


def _install_via_download(loc=""):
    """跑官方安装包静默安装（安装包支持 --installation-dir）。"""
    dst = _installer_cache()
    if not os.path.exists(dst) or os.path.getsize(dst) < 50 * 1024 * 1024:
        ok, info = _download_installer()
        if not ok:
            return False, info
    args = [dst, "install", "--quiet", "--accept-license", "--backend=wsl-2"]
    if loc:
        args += ["--installation-dir=" + loc]
    rc, out = U.run(args, timeout=1800)
    log("   安装包 rc=%s %s" % (rc, out[-300:]))
    return rc == 0, out[-400:]


def stage_install(st):
    head("③ 安装 Docker Desktop")
    ok, ver = U.docker_ok()
    if ok:
        log("   已经装好并在跑（服务端 %s），跳过" % ver)
        return True, ""
    if U.docker_bin():
        log("   已装（%s），只是守护进程没起 —— 跳过安装" % U.docker_bin())
        return True, ""
    if not is_admin():
        return False, "装 Docker 要管理员。双击 install_docker.bat（会自动提权）"

    # 装到哪：.env 里 DOCKER_INSTALL_DIR 优先（keeper 的 DOCKER_BIN 也认这个位置），
    # 但**装不进去不算失败** —— 不同版本的安装器对 --location/--installation-dir
    # 支持程度不一样，带参数被拒就退到官方默认目录再来一次。
    loc = (U.read_env().get("DOCKER_INSTALL_DIR") or "").strip()
    cached = os.path.exists(_installer_cache()) and os.path.getsize(_installer_cache()) > 50 * 1024 * 1024
    attempts = []
    if cached:
        # 安装包已经在本地（今晚预先下好的）⇒ 直接用它，省掉 winget 再下一次
        log("   发现本地已缓存安装包：%s（%.0fMB）"
            % (_installer_cache(), os.path.getsize(_installer_cache()) / 1048576.0))
        if loc:
            attempts.append(("download", loc))
        attempts.append(("download", ""))
    else:
        if loc:
            attempts.append(("winget", loc))
        attempts.append(("winget", ""))
        if loc:
            attempts.append(("download", loc))
        attempts.append(("download", ""))

    for how, where in attempts:
        log("   试 %s%s…" % (how, ("（装到 %s）" % where) if where else "（默认目录）"))
        good, info = (_install_via_winget(where) if how == "winget"
                      else _install_via_download(where))
        if good:
            break
        log("   这条不行，换下一条")
    else:
        return False, "自动安装失败，手动装：https://www.docker.com/products/docker-desktop/"

    if not U.docker_bin():
        return False, "装完了但找不到 docker.exe —— 可能要重启后才注册好，重启后再跑一次"
    return True, ""


def _fix_wslconfig():
    """修 `~/.wslconfig` 里指向不存在文件的 `kernel=`。

    2026-10-22 12:00 真机踩到：主人 2024-05 装模拟器时留了
        kernel=<旧内核路径>
    那个文件早就没了。只要这一行还在，WSL 就**拒绝注册任何新发行版**：
        WSL_E_CUSTOM_KERNEL_NOT_FOUND
    Docker Desktop 首次启动要 `wsl --import-in-place docker-desktop`，正好卡死在这。
    报错信息只说「找不到自定义内核」，不告诉你在哪个文件 —— 所以自动修掉。
    """
    p = os.path.join(os.path.expanduser("~"), ".wslconfig")
    try:
        with open(p, "rb") as f:
            raw = f.read()
    except Exception:
        return ""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        enc = "utf-16"
    elif raw.startswith(b"\xef\xbb\xbf"):
        enc = "utf-8-sig"
    else:
        enc = "utf-8"
    try:
        text = raw.decode(enc, "replace")
    except Exception:
        return ""
    out, changed = [], False
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith(("#", ";")) and s.split("=", 1)[0].strip().lower() == "kernel":
            val = s.split("=", 1)[1].strip().strip('"').strip("'")
            val = val.replace("\\\\", "\\")
            if val and not os.path.exists(val):
                out.append("# [auto-disabled 2026-10-22] kernel file not found, "
                           "breaks every WSL distro registration:")
                out.append("# " + line)
                changed = True
                continue
        out.append(line)
    if not changed:
        return ""
    try:
        bak = p + ".bak-auto"
        if not os.path.exists(bak):
            with open(bak, "wb") as f:
                f.write(raw)
        with open(p, "w", encoding=enc, newline="\n") as f:
            f.write("\n".join(out) + "\n")
    except Exception as e:
        return "想把 .wslconfig 里失效的 kernel= 注释掉，但写不进去：%r" % (e,)
    return ("已注释掉 ~/.wslconfig 里指向不存在文件的 kernel=（原文件备份为 .wslconfig.bak-auto）"
            "—— 不注释的话 WSL 会拒绝注册任何新发行版")


def _wsl_prep():
    """重启之后才做的 WSL 收尾：设默认版本 2 + 装内核。全程不致命。

    放在 daemon 阶段而不是 features：features 一旦启用功能就得重启，
    重启前跑这些只会卡住（见 stage_features 里的注释）。
    """
    fixed = _fix_wslconfig()
    if fixed:
        log("   " + fixed)
        U.run(["wsl", "--shutdown"], timeout=120)
    log("   wsl --set-default-version 2 …")
    rc, out = U.run(["wsl", "--set-default-version", "2"], timeout=180)
    log("   rc=%s %s" % (rc, (out or "").strip()[:200]))
    log("   wsl --update（有新版内核就装上，免得 Docker 起来又提示要更新；慢就等）…")
    rc, out = U.run(["wsl", "--update", "--web-download"], timeout=900)
    log("   rc=%s %s" % (rc, (out or "").strip()[:200]))
    if rc != 0:
        log("   ⚠️ wsl --update 没成功，不阻塞 —— Docker Desktop 首次启动会自己再补一次")


def _docker_backend_error():
    """读 Docker Desktop 的 backend.error.json，捞一句人话出来。

    2026-10-22 11:56 踩过：Docker Desktop 起来后立刻退出，日志里只有
    「fork/exec C:\\WINDOWS\\System32\\wsl.exe: Access is denied.」——
    它自己调不动 wsl.exe。这个文件里就有原因，直接读比让人猜快得多。
    """
    p = os.path.join(os.environ.get("LOCALAPPDATA") or "", "Docker", "backend.error.json")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            s = f.read()
    except Exception:
        return ""
    if "Access is denied" in s:
        return ("Docker Desktop 调不动 wsl.exe（Access is denied）—— 它多半是被"
                "某个安全策略/沙箱环境拉起来的。请**你自己**从开始菜单打开 "
                "Docker Desktop（不要用管理员身份运行）。")
    for kw, msg in (("wsl", "WSL 相关错误"),
                    ("virtualization", "虚拟化未开启"),
                    ("hyper-v", "Hyper-V 相关错误")):
        if kw in s.lower():
            return "%s，详见 %s" % (msg, p)
    return ""


def stage_daemon(st):
    head("④ 启动 Docker Desktop 并等它就绪")
    ok, ver = U.docker_ok()
    if ok:
        log("   守护进程已在跑（服务端 %s）" % ver)
        return True, ""
    _wsl_prep()
    exe = U.docker_desktop_exe()
    if not exe:
        return False, "没找到 Docker Desktop.exe —— 装了吗？"
    log("   启动 %s" % exe)
    log("   ⚠️ 首次启动可能弹两个窗口：①「接受服务协议」②「跳过登录 / Sign in」。")
    log("      都要你点一下 —— 不点的话守护进程不会起来，这里会一直等。")
    if is_admin():
        log("   ⚠️ 这个窗口是管理员权限，而 Docker Desktop 官方不建议用管理员启动。")
        log("      如果下面一直等不到守护进程，请手动从开始菜单打开 Docker Desktop。")
    try:
        import subprocess
        subprocess.Popen([exe], close_fds=True)
    except Exception as e:
        log("   自动启动没成（%r）—— 请手动从开始菜单打开 Docker Desktop" % (e,))
    # 首次启动要现装 docker-desktop 这个 WSL 发行版，2~5 分钟很正常，
    # 给 10 分钟；轮询间隔放大，免得刷屏。
    if not wait_for(lambda: U.docker_ok()[0], 600, 15, "等 docker 守护进程"):
        why = _docker_backend_error()
        return False, ("等了 10 分钟还没起来。%s 常见原因：① WSL 功能没重启生效 "
                       "② Docker Desktop 卡在「接受协议/登录」弹窗 ③ 被安全策略拦住"
                       "（它调不动 wsl.exe）。"
                       % ((why + " ") if why else ""))
    log("   ✅ docker 就绪：%s" % U.docker_ok()[1])
    return True, ""


def stage_compose(st):
    head("⑤ 起 napcat 容器")
    if not U.docker_ok()[0]:
        return False, "docker 守护进程没起来，先过 ④"
    # 注入版还占着 3000 的话容器映射不上，先提醒（真正的清理在 switch 阶段）
    if U.port_open(U.NAPCAT_PORT):
        log("   ⚠️ 3000 端口已被占（多半是 Windows 注入版 NapCat 还在跑）——"
            "等下 switch 阶段会停掉它，若容器起不来就先手动关掉注入版")
    rc, out = U.docker(["compose", "up", "-d"], timeout=900, cwd=U.HERE)
    for line in (out or "").splitlines()[-8:]:
        log("   | " + line)
    if rc != 0:
        return False, "compose up 失败(rc=%s)：%s" % (rc, out[-500:])
    log("   容器已启动，等 NapCat 把 WebUI 端口 %d 拉起来…" % U.WEBUI_PORT)
    if not wait_for(lambda: U.port_open(U.WEBUI_PORT) or U.port_open(U.NAPCAT_PORT),
                    180, 5, "等 NapCat 就绪"):
        log("   ⚠️ 端口还没通，先继续后面的步骤（NapCat 可能还在初始化）")
    log("")
    log("   👉 现在去扫码：http://127.0.0.1:%d/webui   （口令见 .env 的 WEBUI_TOKEN）"
        % U.WEBUI_PORT)
    log("      扫完不用等我，脚本接着往下走；最后 verify 会告诉你到底通没通。")
    return True, ""


def stage_wire(st):
    head("⑥ 双向接线")
    sys.path.insert(0, U.HERE)
    import wire
    ok, ver = U.docker_ok()
    ip = U.host_ip()
    if not ip:
        return False, "没找到容器网关 IP（Docker Desktop 起来了吗？）"
    uin = U.uin_from_env_or_cfg()
    log("   容器回宿主机域名: %s   宿主机在容器网段的 IP: %s" % (U.HOST_REF, ip))
    err, msg = wire.seed_napcat_cfg(uin, False)
    log("   " + (("[!] " + err) if err else msg))
    err, msg = wire.wire_napcat_cfg(False)
    log(("   [!] " + err) if err else msg)
    err, msg = wire.wire_bot_cfg(ip, False)
    log("   " + (("[!] " + err) if err else msg))
    if err:
        return False, err
    return True, ""


def stage_switch(st):
    head("⑦ 让 keeper 用容器版 NapCat")
    # 🔴 2026-10-26：这里原来做两件事——① 杀 Windows 注入版 NapCat 进程树；
    # ② 把 config 的 napcat.mode 改成 docker。inject 整条路删掉后两者都不需要了：
    # 容器版是唯一跑法，没有"另一套"要停，也没有 mode 可切。
    # 只保留最后一件事：让 keeper 带着新接线重启（它会自己把容器拉起来）。
    rc, out = U.run([sys.executable, os.path.join(HERE, "restart_keeper.py")], timeout=180)
    log("   keeper 重启: rc=%s %s" % (rc, (out or "").strip()[:200]))
    return True, ""


def stage_verify(st):
    head("⑧ 端到端验收")
    import docker_doctor
    return docker_doctor.main([]) == 0, ""


# ------------------------------------------------------------------ 主流程

def preflight():
    head("方案 B 环境体检")
    log("  管理员: %s" % ("是" if is_admin() else "否（features/install/switch 会要求提权）"))
    d = U.docker_bin()
    log("  docker : %s" % (d or "未安装"))
    if d:
        ok, ver = U.docker_ok()
        log("  守护进程: %s" % (("就绪 " + ver) if ok else "没起（" + str(ver)[:80] + "）"))
    log("  WSL    : %s" % (feature_state("Microsoft-Windows-Subsystem-Linux") or "查不到（要管理员）"))
    log("  虚拟机平台: %s" % (feature_state("VirtualMachinePlatform") or "查不到（要管理员）"))
    log("  容器   : %s" % container_brief())
    log("  网关IP : %s" % (U.host_ip() or "未找到"))
    log("  bot 配置: listen_host=%s  容器=%s" % (
        U.bot_cfg().get("listen_host"), U.CONTAINER))
    log("  NapCat : %s" % (U.napcat_status()[1]))
    log("")
    log("  进度: " + "  ".join(
        ("✅" if done(s) else "⬜") + s for s in STAGES))


def container_brief():
    rc, out = U.docker(["ps", "-a", "--filter", "name=" + U.CONTAINER,
                        "--format", "{{.Status}}"], timeout=60)
    if rc != 0:
        return "查不到（docker 没就绪）"
    return out.strip() or "不存在"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    only = ""
    if "--stage" in argv:
        i = argv.index("--stage")
        only = argv[i + 1] if i + 1 < len(argv) else ""
    if "--reset" in argv:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        log("进度已清零")
    if "--check" in argv:
        preflight()
        return 0

    st = state_load()
    log("=" * 62)
    log(" 方案 B 一键落地（Docker Linux 无头 NapCat）")
    log(" 进度：%s" % ("  ".join(("✅" if done(s) else "⬜") + s for s in STAGES)))
    log("=" * 62)

    # 「请重启」之后如果没重启又双击一次，install 会白等十分钟再失败。
    # 拿这次开机时刻和立标记时的开机时刻比一下就知道。
    boot_then = st.get("boot_at_need_reboot") or 0
    if st.get("need_reboot") and boot_then:
        if abs(_boot_epoch() - boot_then) < 60:
            log("")
            log("⏸ 检测到**还没重启**（这次开机时间和上次一样）。")
            log("   先重启电脑，再双击 docker_go.bat。不重启的话 WSL 功能没生效，")
            log("   装 Docker 会白等十分钟然后失败。")
            return EXIT_NEED_REBOOT
        log("   （已重启过，继续）")
        st.pop("need_reboot", None)
        st.pop("boot_at_need_reboot", None)
        state_save(st)

    todo = [only] if only else STAGES
    had_reboot = bool(st.get("need_reboot"))
    for stage in todo:
        fn = globals().get("stage_" + stage)
        if not fn:
            log("[!] 没有这个阶段: %s" % stage)
            return 2
        if not only and done(stage):
            log("\n· %s 已完成，跳过（要重跑：--stage %s）" % (stage, stage))
            continue
        try:
            ok, note = fn(st)
        except Exception:
            import traceback
            log("\n[!] %s 阶段抛异常：\n%s" % (stage, traceback.format_exc()))
            mark(stage, False, "exception")
            return 1
        mark(stage, ok, note or "")
        if note:
            log("   → " + note)
        if not ok:
            log("")
            log("❌ 卡在 %s 阶段。修好后重跑本脚本，会从这里接着走。" % stage)
            return 1
        # 只有「这一阶段刚刚把 need_reboot 立起来」才要求重启；
        # 否则装完重启回来跑 install 会又被上一轮留下的标记挡一次。
        now_reboot = bool(state_load().get("need_reboot"))
        if now_reboot and not had_reboot and stage in ("features", "install"):
            log("")
            log("🔁 需要重启电脑才能生效。")
            log("   重启后：**再双击一次 docker_go.bat**（会自动跳过已完成的部分）")
            return EXIT_NEED_REBOOT
        had_reboot = now_reboot

    log("")
    log("=" * 62)
    log(" ✅ 全流程跑完。")
    log("=" * 62)
    log(" 日常只用：docker_status.bat 看状态 / restart_bot.bat 重启 bot")
    log(" 出问题：docker_doctor.bat 体检（它会指出哪一环断了）")
    log(" 想退回注入版：docker_rollback.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
