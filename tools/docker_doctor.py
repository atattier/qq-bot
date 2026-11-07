# -*- coding: utf-8 -*-
"""方案 B 体检：容器化之后，一条消息从 QQ 到 bot 要穿过 5 个环节，任何一环断了
表现都一样 —— 「bot 没反应」。这个脚本逐环探活，直接告诉你断在第几环、怎么修。

链路：
    QQ 服务器 ⇄ [1]容器里的 NapCat ⇄ [2]宿主机 3000 端口 ⇄ [3]bot 进程
                     ↓ [4]上报事件
              host.docker.internal:8088 ⇄ [5]bot 的 /onebot 监听

用法：
    python tools/docker_doctor.py          # 体检（只读）
    python tools/docker_doctor.py --quiet  # 只输出结论（给别的脚本调）
退出码：0 全通 / 1 有环节断了 / 2 环境还没到这一步（比如还没装 docker）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "docker-napcat"))

import dutil as U

QUIET = False
ROWS = []


def say(msg=""):
    if not QUIET:
        print(msg, flush=True)


def row(name, ok, detail, fix=""):
    ROWS.append((name, ok, detail, fix))
    if not QUIET:
        mark = {True: "✅", False: "❌", None: "⚠️ "}[ok]
        print("  %s %-22s %s" % (mark, name, detail))
        if ok is not True and fix:
            print("     ↳ 修：%s" % fix)


def _container_state():
    rc, out = U.docker(["ps", "-a", "--filter", "name=" + U.CONTAINER,
                        "--format", "{{.State}}\t{{.Status}}"], timeout=60)
    if rc != 0:
        return "", ""
    line = (out or "").strip().splitlines()
    if not line:
        return "", ""
    parts = line[0].split("\t")
    return (parts[0].strip().lower(), parts[1] if len(parts) > 1 else "")


def _container_curl(url, timeout=8):
    """在容器里发一次 HTTP 请求（先 curl，没有就退 node —— NapCat 镜像里 node 一定有）。"""
    py = ("const http=require('http');"
          "const u=new URL('%s');"
          "const r=http.request({host:u.hostname,port:u.port,path:u.pathname,"
          "method:'GET',timeout:%d},s=>{let b='';s.on('data',d=>b+=d);"
          "s.on('end',()=>{console.log(s.statusCode+' '+b.slice(0,200));process.exit(0)})});"
          "r.on('error',e=>{console.log('ERR '+e.message);process.exit(0)});"
          "r.on('timeout',()=>{console.log('ERR timeout');process.exit(0)});r.end();") % (url, timeout * 1000)
    cmd = ["exec", U.CONTAINER, "sh", "-c",
           "curl -s -m %d -o - -w ' %%{http_code}' %s 2>/dev/null || node -e %s"
           % (timeout, url, _shquote(py))]
    rc, out = U.docker(cmd, timeout=timeout + 25)
    return (out or "").strip()[:300]


def _shquote(s):
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _http_get(url, timeout=5):
    """取一段 http 正文（绕系统代理）。抽成函数是为了自测能替换掉。"""
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return op.open(url, timeout=timeout).read().decode("utf-8", "replace")


def main(argv=None):
    global QUIET
    argv = list(sys.argv[1:] if argv is None else argv)
    QUIET = "--quiet" in argv

    say("=" * 62)
    say(" 方案 B 体检：QQ → 容器 NapCat → bot 全链路")
    say("=" * 62)
    say("")

    # ---- 0. docker ----
    d = U.docker_bin()
    if not d:
        row("docker 命令", False, "没装（找不到 docker.exe）",
            "双击 install_docker.bat 装环境（要重启一次）")
        return _verdict(2)
    ok, ver = U.docker_ok()
    row("docker 守护进程", ok, ver if ok else str(ver)[:120],
        "启动 Docker Desktop，等托盘变绿再试")
    if not ok:
        return _verdict(2)

    # ---- 1. 容器 ----
    cstate, cstatus = _container_state()
    if not cstate:
        row("napcat 容器", False, "不存在",
            "双击 docker_up.bat（或 python tools/docker_bootstrap.py --stage compose）")
        return _verdict(1)
    row("napcat 容器", cstate == "running", "%s %s" % (cstate, cstatus),
        "docker start napcat（或 docker_up.bat）")
    if cstate != "running":
        return _verdict(1)

    rc, pol = U.docker(["inspect", "-f", "{{.HostConfig.RestartPolicy.Name}}",
                        U.CONTAINER], timeout=60)
    row("容器自启策略", (pol or "").strip() in ("always", "unless-stopped"),
        (pol or "").strip() or "查不到", "compose 里写的是 always，重建容器即可")

    # ---- 2. 端口 ----
    p3000 = U.port_open(U.NAPCAT_PORT)
    row("宿主机 3000", p3000, "OneBot HTTP %s" % ("通" if p3000 else "不通"),
        "容器没映射出来：docker compose up -d；或注入版 NapCat 还占着 3000，先停掉它")
    p6099 = U.port_open(U.WEBUI_PORT)
    row("WebUI 6099", p6099, "扫码页 %s" % ("通" if p6099 else "不通"),
        "docker_up.bat 后等 30 秒；仍不通看 docker logs napcat")

    # ---- 3. 账号在线 ----
    st, detail = U.napcat_status()
    if st is None:
        row("账号在线", False, detail, "NapCat 没起来或 token 不对（config.json 的 token）")
    else:
        row("账号在线", st, detail,
            "去 http://127.0.0.1:%d/webui 扫码（口令见 docker-napcat/.env）" % U.WEBUI_PORT)

    # ---- 4. 容器 → 宿主机（最容易断的一环）----
    url = "http://%s:%d/health" % (U.HOST_REF, U.BOT_PORT)
    out = _container_curl(url)
    reach = bool(out) and (" 200" in out or out.startswith("200") or "200" in out[:8])
    row("容器→宿主机", reach, (out[:120] or "无输出"),
        "① bot 的 listen_host 要含容器网关 IP（跑 wire.py）"
        " ② Windows 防火墙可能挡了 8088（允许专用网络）")

    # ---- 5. bot ----
    bot_local = U.port_open(U.BOT_PORT)
    row("bot 127.0.0.1:8088", bot_local, "本机回环 %s" % ("通" if bot_local else "不通"),
        "双击 restart_bot.bat（keeper 会在 NapCat 在线后自动拉 bot）")
    if bot_local:
        try:
            body = _http_get("http://127.0.0.1:%d/health" % U.BOT_PORT)
            row("bot /health", True, body[:120])
        except Exception as e:
            row("bot /health", False, repr(e)[:120], "看 bot.err.log 尾部")

    ip = U.host_ip()
    addrs = str(U.bot_cfg().get("listen_host") or "")
    row("bot 监听含网关IP", bool(ip) and ip in addrs,
        "listen_host=%s / 网关=%s" % (addrs, ip or "未找到"),
        "跑 docker-napcat/wire.py 把网关 IP 追加进 listen_host（127.0.0.1 不能删）")

    # ---- 6. NapCat 上报地址 ----
    uin = U.uin_from_env_or_cfg()
    cfgp = os.path.join(U.NAPCAT_CFG_DIR, "onebot11_%s.json" % uin) if uin else ""
    post_url = ""
    if cfgp and os.path.exists(cfgp):
        d2 = U.load_json(cfgp, {}) or {}
        for c in (d2.get("network") or {}).get("httpClients") or []:
            if c.get("enable", True):
                post_url = str(c.get("url") or "")
                break
    row("上报地址", U.HOST_REF in post_url, post_url or "没配 httpClients",
        "跑 docker-napcat/wire.py（把 127.0.0.1 改成 host.docker.internal）")

    # ---- 7. keeper ----
    alive, why = keeper_alive()
    row("keeper 保活", alive, why, "双击 start_all.bat；或让计划任务守卫自动拉起")

    return _verdict(0 if all(r[1] is True for r in ROWS) else 1)


def keeper_alive():
    pidf = os.path.join(ROOT, "state", "keeper.pid")
    try:
        with open(pidf, encoding="utf-8") as f:
            pid = int((f.read() or "0").strip() or 0)
    except Exception:
        return False, "没有 keeper.pid"
    if pid <= 0:
        return False, "keeper.pid 是空的"
    rc, out = U.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], timeout=20)
    if str(pid) in (out or ""):
        return True, "PID %d 在跑" % pid
    return False, "PID %d 已不存在（keeper 死了没人拉起）" % pid


def _verdict(code):
    bad = [r for r in ROWS if r[1] is False]
    warn = [r for r in ROWS if r[1] is None]
    say("")
    say("-" * 62)
    if code == 0 and not bad:
        say(" 🎉 全链路通：QQ 消息能进 bot，bot 能发回 QQ。")
    elif code == 2:
        say(" ⏸ 环境还没到这一步：%s" % (bad[0][0] if bad else "docker 不可用"))
    else:
        say(" ❌ 断了 %d 环，按上面 ↳ 的建议修：" % len(bad))
        for r in bad:
            say("    · %s —— %s" % (r[0], r[3] or r[2]))
    if warn:
        say(" ⚠️ 另有 %d 项不确定：%s" % (len(warn), "、".join(r[0] for r in warn)))
    say("-" * 62)
    return code


if __name__ == "__main__":
    sys.exit(main())
