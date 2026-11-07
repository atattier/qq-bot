# -*- coding: utf-8 -*-
"""方案 B 接线：把 Windows 上跑的 bot 和 Docker 里的 NapCat 接起来。

用法（双击 docker_wire.bat 即可，它会自动提权；也可以在任意窗口直接跑）：
    python docker-napcat/wire.py            # 执行接线（自动备份 + 幂等，可反复跑）
    python docker-napcat/wire.py --check    # 只读预检，不写任何文件

为什么要接线？
    bot 只监听 127.0.0.1。容器是**独立网络命名空间**，容器里访问 127.0.0.1 连的是
    容器自己，打不到宿主机的 bot ⇒ 一条消息都收不到。所以两头都要改：

    A. 容器侧（NapCat 配置）：上报地址 127.0.0.1:8088 → host.docker.internal:8088。
       host.docker.internal 是 Docker Desktop 内置域名，指向宿主机，IP 变了也不用改
       （比写死 172.x 可靠）。HTTP 服务监听改 0.0.0.0，否则端口映射进不来。
    B. 宿主机侧（bot 配置）：listen_host 追加容器网关 IP，让容器能回连；
       **127.0.0.1 必须保留** —— keeper 探活、后台 /admin、回归测试全靠它。
       所以这里写的是 "127.0.0.1,<网关IP>"，bot.py 会为每个地址各绑一个 socket。
    D. 宿主机侧（bot 配置）：把 NapCat 的 `httpClients.token` 同步到
       `config.json` 的 `napcat_report_token`，供 `/onebot` 上报认证。
       只读 `httpClients.token`，绝不读 `httpServers.token`（那是 API token）。

⚠️ 顺序：本脚本随时可跑（幂等），但真正生效要重启 bot。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dutil as U

WIN_NAPCAT_CFG = os.environ.get("QBOT_NAPCAT_WIN_CFG") or ""   # Windows 版 NapCat 配置目录（抄 token 用）


# ------------------------------------------------------------------ 容器侧

def _win_post_token():
    """从 Windows 版 NapCat 配置里抄上报 token —— 保持两边一致，切回来时不用再改。"""
    try:
        for f in os.listdir(WIN_NAPCAT_CFG):
            if f.startswith("onebot11_") and f.endswith(".json"):
                d = U.load_json(os.path.join(WIN_NAPCAT_CFG, f), {}) or {}
                for c in (d.get("network") or {}).get("httpClients") or []:
                    t = str(c.get("token") or "")
                    if t:
                        return t
    except Exception:
        pass
    return ""


def seed_napcat_cfg(uin, check):
    """预置容器里的 NapCat 网络配置：第一次起容器就自带正确的上报地址。

    不预置会怎样：容器首次启动自己生成一份默认配置（httpServers 只有 127.0.0.1、
    没有 httpClients），于是「扫码成功但 bot 一条消息都收不到」，还得进 WebUI 手改。
    """
    if not uin:
        return None, "不知道要登录哪个 QQ 号（.env 的 NAPCAT_ACCOUNT 没填）"
    path = os.path.join(U.NAPCAT_CFG_DIR, "onebot11_%s.json" % uin)
    if os.path.exists(path):
        return None, "已存在 %s（保留你/容器现有的设置）" % os.path.basename(path)
    if check:
        return None, "将生成 %s（--check 未写入）" % os.path.basename(path)

    post_token = _win_post_token() or U.napcat_token()
    cfg = {
        "network": {
            "httpServers": [{
                "enable": True, "name": "http", "host": "0.0.0.0", "port": 3000,
                "enableCors": True, "enableWebsocket": False,
                "messagePostFormat": "array", "token": U.napcat_token(), "debug": False,
            }],
            "httpSseServers": [],
            "httpClients": [{
                "enable": True, "name": "post",
                "url": "http://%s:%d/onebot" % (U.HOST_REF, U.BOT_PORT),
                "reportSelfMessage": True, "messagePostFormat": "array",
                "token": post_token, "debug": False,
            }],
            "websocketServers": [], "websocketClients": [], "plugins": [],
        },
        "musicSignUrl": "", "enableLocalFile2Url": False, "parseMultMsg": False,
        "imageDownloadProxy": "",
        "timeout": {"baseTimeout": 10000, "uploadSpeedKBps": 256,
                    "downloadSpeedKBps": 256, "maxTimeout": 1800000},
    }
    U.save_json(path, cfg)
    return None, "已生成 %s（上报 → http://%s:%d/onebot）" % (
        os.path.basename(path), U.HOST_REF, U.BOT_PORT)


def wire_napcat_cfg(check):
    """容器侧：上报地址改 host.docker.internal；服务监听改 0.0.0.0。"""
    try:
        files = [os.path.join(U.NAPCAT_CFG_DIR, f) for f in os.listdir(U.NAPCAT_CFG_DIR)
                 if f.startswith("onebot11_") and f.endswith(".json")]
    except Exception:
        files = []
    if not files:
        return None, ("容器配置还没有（%s 下无 onebot11_*.json）——先跑一次 docker_up.bat，"
                      "再回来跑本脚本" % os.path.basename(U.NAPCAT_CFG_DIR))

    lines = []
    for p in files:
        d = U.load_json(p, None)
        if d is None:
            lines.append("  %s 读不出来（不是合法 json），跳过" % os.path.basename(p))
            continue
        net = d.setdefault("network", {})
        changed = []

        for c in net.get("httpClients") or []:
            u = str(c.get("url") or "")
            if not u:
                continue
            if re.search(r"//(127\.0\.0\.1|localhost)([:/]|$)", u):
                c["url"] = re.sub(r"//(127\.0\.0\.1|localhost)", "//" + U.HOST_REF, u)
                changed.append("上报 → %s" % c["url"])
        for key in ("httpServers", "websocketServers"):
            for s in net.get(key) or []:
                if str(s.get("host") or "") in ("127.0.0.1", "localhost", ""):
                    s["host"] = "0.0.0.0"
                    changed.append("%s 监听 → 0.0.0.0:%s" % (key, s.get("port")))

        if not changed:
            lines.append("  %s 已是接线状态" % os.path.basename(p))
            continue
        if check:
            lines.append("  %s 需改写：%s（--check 未写入）"
                         % (os.path.basename(p), "；".join(changed)))
            continue
        bak = U.backup(p, "bak-wired")
        U.save_json(p, d)
        lines.append("  %s 已接线：%s（备份 %s）"
                     % (os.path.basename(p), "；".join(changed),
                        os.path.basename(bak) if bak else "无"))
    return None, "\n".join(lines)


# ------------------------------------------------------------------ 宿主机侧

def _report_token_sources():
    """收集 NapCat 各上报端的 httpClients.token（只读，不读 httpServers.token）。"""
    try:
        files = [os.path.join(U.NAPCAT_CFG_DIR, f) for f in os.listdir(U.NAPCAT_CFG_DIR)
                 if f.startswith("onebot11_") and f.endswith(".json")]
    except Exception:
        files = []
    sources = []
    for p in sorted(files):
        d = U.load_json(p, None)
        if not isinstance(d, dict):
            continue
        net = d.get("network") or {}
        for c in net.get("httpClients") or []:
            if not isinstance(c, dict):
                continue
            t = str(c.get("token") or "").strip()
            if t:
                sources.append((os.path.basename(p), t))
    return sources


def _norm_report_tokens(raw):
    if isinstance(raw, str):
        vals = [raw]
    elif isinstance(raw, (list, tuple)):
        vals = list(raw)
    else:
        vals = []
    out = []
    for v in vals:
        s = str(v or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def wire_report_token(check):
    """把 NapCat 上报 token 同步到 bot config 的 napcat_report_token。

    只读 `httpClients.token`；绝不读 `httpServers.token`（那是 Bot -> NapCat API token）。
    输出只报端数/唯一 token 数，不打印 token 值。
    """
    sources = _report_token_sources()
    if not sources:
        return "NapCat 配置里没有可用的 httpClients.token（未写入）", None
    tokens = []
    for _, t in sources:
        if t not in tokens:
            tokens.append(t)
    if not os.path.exists(U.BOT_CFG):
        return "缺少 %s" % U.BOT_CFG, None
    cfg = U.load_json(U.BOT_CFG, {}) or {}
    cur = _norm_report_tokens(cfg.get("napcat_report_token"))
    detail = "%d 个上报端 / %d 个唯一 token" % (len(sources), len(tokens))
    if cur == tokens:
        return None, "napcat_report_token 已一致（%s）" % detail
    if check:
        return None, "napcat_report_token 未配置/不一致 → 将写入 %s（--check 未写入）" % detail
    bak = U.backup(U.BOT_CFG, "bak-report-token")
    cfg["napcat_report_token"] = tokens[0] if len(tokens) == 1 else tokens
    U.save_json(U.BOT_CFG, cfg)
    return None, "napcat_report_token 已写入 %s（备份 %s）" % (
        detail, os.path.basename(bak) if bak else "无")


def wire_bot_cfg(ip, check):
    """宿主机侧：listen_host 追加容器网关 IP，127.0.0.1 永远保留。"""
    if not os.path.exists(U.BOT_CFG):
        return "缺少 %s" % U.BOT_CFG, None
    cfg = U.load_json(U.BOT_CFG, {}) or {}
    raw = cfg.get("listen_host", "127.0.0.1")
    if isinstance(raw, (list, tuple)):
        addrs = [str(x).strip() for x in raw if str(x).strip()]
    else:
        addrs = [a for a in re.split(r"[,;\s]+", str(raw or "")) if a]
    old = ",".join(addrs)
    # 0.0.0.0 已经是「全听」，再追加没意义，而且两个 socket 会抢端口
    if "0.0.0.0" in addrs:
        return None, "listen_host 已是 0.0.0.0（全接口），无需改动"
    if ip not in addrs:
        addrs.append(ip)
    new = ",".join(addrs)
    if new == old:
        return None, "listen_host 已是 %s，无需改动" % new
    if check:
        return None, "listen_host: %s → %s（--check 未写入）" % (old or "(空)", new)
    bak = U.backup(U.BOT_CFG, "bak-wired")
    cfg["listen_host"] = new
    U.save_json(U.BOT_CFG, cfg)
    return None, "listen_host: %s → %s（备份 %s）" % (
        old or "(空)", new, os.path.basename(bak) if bak else "无")


# ------------------------------------------------------------------ 主流程

def main():
    check = "--check" in sys.argv

    print("=" * 62)
    print(" 方案 B 接线：Windows bot  <-->  Docker NapCat%s" % ("  [预检模式]" if check else ""))
    print("=" * 62)

    ok, ver = U.docker_ok()
    if ok:
        print("\n[ok] Docker 守护进程就绪（服务端 %s）" % ver)
    else:
        print("\n[!] Docker 还没就绪：%s" % (ver or "无响应"))
        print("    先双击 install_docker.bat 装环境（要重启一次），启动 Docker Desktop 后再来")
        print("    现在继续做**不依赖 docker** 的那部分（预置容器配置 + bot 监听地址）")

    ip = U.host_ip()
    uin = U.uin_from_env_or_cfg()

    print("\n--- A. 容器侧：预置 NapCat 网络配置 ---")
    err, msg = seed_napcat_cfg(uin, check)
    print("   [!] " + err if err else "   " + msg)
    print("\n--- B. 容器侧：上报地址改 host.docker.internal ---")
    err, msg = wire_napcat_cfg(check)
    print("   [!] " + err if err else msg)

    print("\n--- C. 宿主机侧：bot 监听地址 ---")
    if not ip:
        print("   [!] 没找到 WSL/容器虚拟网卡 —— Docker Desktop 还没真正跑起来。")
        print("       启动它之后重跑本脚本；仍不行就把 listen_host 手工加一条")
        print("       ipconfig 里 vEthernet (WSL) 的 IPv4（逗号分隔，127.0.0.1 别删）。")
    else:
        print("   [ok] 容器回宿主机用 %s（Docker Desktop 内置域名）" % U.HOST_REF)
        err, msg = wire_bot_cfg(ip, check)
        print("   [!] " + err if err else "   " + msg)
        if not ok:
            print("   ⚠️ Docker 未就绪，这个 IP 只是预估值 —— 等 Docker running 后重跑本脚本再写入。")

    print("\n--- D. 宿主机侧：NapCat 上报 token ---")
    err, msg = wire_report_token(check)
    print("   [!] " + err if err else "   " + msg)

    print("\n" + "=" * 62)
    print(" 接线完成。剩下三步：")
    print("=" * 62)
    print(" 1) docker_up.bat        启动 NapCat 容器")
    print(" 2) http://127.0.0.1:%d/webui  扫码（首次一次，之后登录态在 .config 卷里）"
          % U.WEBUI_PORT)
    print(" 3) restart_bot.bat      重启 bot 让新的监听地址生效")
    print(" 4) docker_doctor.bat    端到端验收（容器→宿主机这条路通不通）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
