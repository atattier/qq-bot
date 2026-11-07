# -*- coding: utf-8 -*-
"""方案 B 自测：**没有 Docker 也能把整条链路跑一遍**。

为什么需要它：这台机器还没装 Docker（装 Docker 要管理员 + 重启，装不了就整条链路
一次都没跑过）。而方案 B 最容易错的地方恰恰不是 docker 本身，是**我们自己写的那些
命令拼装和配置改写**：compose 在哪个目录跑、上报地址改没改、listen_host 会不会把
127.0.0.1 弄丢、体检脚本断链时判断对不对。

做法：把 dutil.run / port_open / napcat_status 换成假的（假 docker 按 argv 返回预置输出），
然后真跑一遍 wire.py、docker_mgr.py、docker_doctor.py、docker_bootstrap.py，
断言它们的**副作用**（改了哪些配置、返回什么结论）符合预期。

    python tools/docker_selftest.py           # 跑全部用例
    python tools/docker_selftest.py -v        # 显示每一步的细节

退出码 0 = 全过。
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "docker-napcat"))
sys.path.insert(0, HERE)

import dutil as U

VERBOSE = "-v" in sys.argv
RESULTS = []
TMP = tempfile.mkdtemp(prefix="qqbot-selftest-")

# 假环境的状态：各用例按需改
FAKE = {
    "docker": True,          # 守护进程在不在
    "container": "running",  # running / exited / missing
    "online": True,          # 账号在线
    "p3000": True,
    "p6099": True,
    "p8088": True,
    "reach": True,           # 容器能否回连宿主机
    "logs": "",
}


def say(msg=""):
    if VERBOSE:
        print(msg, flush=True)


def case(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print("  ✅ %s" % name)
    except AssertionError as e:
        RESULTS.append((name, False, str(e)))
        print("  ❌ %s —— %s" % (name, e))
    except Exception as e:
        import traceback
        RESULTS.append((name, False, repr(e)))
        print("  ❌ %s —— 异常 %r" % (name, e))
        if VERBOSE:
            print(traceback.format_exc())


def eq(a, b, what=""):
    assert a == b, "%s 期望 %r，实际 %r" % (what or "值", b, a)


def contains(hay, needle, what=""):
    assert needle in (hay or ""), "%s 里没有 %r（实际 %r）" % (what, needle, str(hay)[:200])


# ------------------------------------------------------------------ 假 docker

def fake_run(cmd, timeout=120, cwd=None, env=None):
    argv = [str(c) for c in cmd]
    say("      $ " + " ".join(argv[:8]))
    if not argv:
        return 0, ""
    base = os.path.basename(argv[0]).lower()
    if base.startswith("docker"):
        return fake_docker(argv[1:])
    if base == "ipconfig":
        return 0, ("Windows IP 配置\n\n"
                   "以太网适配器 vEthernet (WSL):\n\n"
                   "   IPv4 地址 . . . . . . . . . . . . : 172.25.0.1\n"
                   "   子网掩码  . . . . . . . . . . . . : 255.255.240.0\n")
    if base == "tasklist":
        return 0, "INFO: No tasks are running which match the specified criteria.\n"
    if base == "taskkill":
        return 0, "SUCCESS"
    if base.startswith("dism"):
        return 0, "状态: 已启用\n"
    return 0, ""


def fake_docker(args):
    a = args[0] if args else ""
    if a == "version":
        return (0, "27.3.1\n") if FAKE["docker"] else (
            1, "error during connect: The system cannot find the file specified.")
    if a == "ps":
        if FAKE["container"] == "missing":
            return 0, ""
        return 0, "%s\tUp 2 hours\n" % FAKE["container"]
    if a == "inspect":
        return 0, "always\n"
    if a in ("compose", "start", "restart", "stop", "down"):
        return 0, "ok"
    if a == "logs":
        return 0, FAKE["logs"] or "10-22 00:00:00 [info] napcat started"
    if a == "exec":
        # docker exec napcat sh -c "curl ... http://host.docker.internal:8088/health"
        return (0, '{"ok": true} 200') if FAKE["reach"] else (0, "ERR connect ECONNREFUSED")
    return 0, ""


def fake_port_open(port, host="127.0.0.1", timeout=3):
    return {3000: FAKE["p3000"], 6099: FAKE["p6099"], 8088: FAKE["p8088"]}.get(port, False)


def fake_status(timeout=6):
    if not FAKE["p3000"]:
        return None, "127.0.0.1:3000 连不上"
    return (True, "uin=123450002 online=True good=True") if FAKE["online"] else (
        False, "uin=123450002 online=False good=False")


def install_fakes():
    U.run = fake_run
    U.port_open = fake_port_open
    U.napcat_status = fake_status
    U.host_ip = lambda: "172.25.0.1"
    U.docker_bin = lambda: r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    U.docker_ok = lambda timeout=25: ((True, "27.3.1") if FAKE["docker"] else (False, "not running"))
    # 把要改的配置指到临时目录，绝不碰真 config.json
    U.BOT_CFG = os.path.join(TMP, "config.json")
    U.NAPCAT_CFG_DIR = os.path.join(TMP, "napcat-config")
    U.ENV_FILE = os.path.join(TMP, ".env")
    os.makedirs(U.NAPCAT_CFG_DIR, exist_ok=True)
    with io.open(U.BOT_CFG, "w", encoding="utf-8") as f:
        json.dump({"listen_host": "127.0.0.1", "listen_port": 8088,
                   "token": "TESTTOKEN", "napcat": {"container": "napcat"}}, f, ensure_ascii=False)
    with io.open(U.ENV_FILE, "w", encoding="utf-8") as f:
        f.write("NAPCAT_ACCOUNT=123450002\nWEBUI_TOKEN=testpass\n")


def read_bot_cfg():
    with io.open(U.BOT_CFG, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ 用例

def t_dutil():
    eq(U.host_ip(), "172.25.0.1", "host_ip 认 vEthernet")
    eq(U.uin_from_env_or_cfg(), "123450002", "uin 从 .env 读")
    eq(U.napcat_token(), "TESTTOKEN", "token 从 config 读")


def t_dutil_decode():
    """wsl/dism 吐 UTF-16LE 时必须解对（10-22 早上乱码事故的回归）。"""
    s = "正在安装: 适用于 Linux 的 Windows 子系统\r\n请重启系统以完成安装。"
    eq(U.decode(s.encode("utf-16-le")), s, "UTF-16LE 解码")
    eq(U.decode("中文正常".encode("utf-8")), "中文正常", "UTF-8 解码")
    eq(U.decode("中文GBK".encode("gbk")), "中文GBK", "GBK 解码")
    eq(U.decode(b""), "", "空输出")
    eq(U.decode("plain docker 27.0".encode("utf-8")), "plain docker 27.0", "纯 ASCII")


def t_wire_seed():
    import wire
    err, msg = wire.seed_napcat_cfg("123450002", False)
    eq(err, None, "seed 不应报错")
    path = os.path.join(U.NAPCAT_CFG_DIR, "onebot11_123450002.json")
    assert os.path.exists(path), "没生成容器配置"
    with io.open(path, encoding="utf-8") as f:
        d = json.load(f)
    net = d["network"]
    eq(net["httpClients"][0]["url"], "http://host.docker.internal:8088/onebot", "上报地址")
    eq(net["httpServers"][0]["host"], "0.0.0.0", "HTTP 服务监听")
    eq(net["httpServers"][0]["token"], "TESTTOKEN", "HTTP 服务 token")
    # 幂等：再跑一次不应重复写
    err2, msg2 = wire.seed_napcat_cfg("123450002", False)
    contains(msg2, "已存在", "二次调用应识别已存在")


def t_wire_napcat_cfg():
    import wire
    # 造一个「还是 127.0.0.1」的配置，验证会被改
    path = os.path.join(U.NAPCAT_CFG_DIR, "onebot11_999.json")
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump({"network": {
            "httpServers": [{"host": "127.0.0.1", "port": 3000}],
            "httpClients": [{"url": "http://127.0.0.1:8088/onebot"}],
        }}, f)
    err, msg = wire.wire_napcat_cfg(False)
    eq(err, None, "接线不应报错")
    with io.open(path, encoding="utf-8") as f:
        d = json.load(f)
    eq(d["network"]["httpClients"][0]["url"], "http://host.docker.internal:8088/onebot", "上报地址改写")
    eq(d["network"]["httpServers"][0]["host"], "0.0.0.0", "监听改写")


def t_wire_bot_cfg():
    import wire
    err, msg = wire.wire_bot_cfg("172.25.0.1", False)
    eq(err, None, "wire_bot_cfg 不应报错")
    eq(read_bot_cfg()["listen_host"], "127.0.0.1,172.25.0.1", "listen_host 追加但保留回环")
    # 幂等
    wire.wire_bot_cfg("172.25.0.1", False)
    eq(read_bot_cfg()["listen_host"], "127.0.0.1,172.25.0.1", "重复跑不应重复追加")
    # 0.0.0.0 不追加
    cfg = read_bot_cfg()
    cfg["listen_host"] = "0.0.0.0"
    U.save_json(U.BOT_CFG, cfg)
    err, msg = wire.wire_bot_cfg("172.25.0.1", False)
    eq(read_bot_cfg()["listen_host"], "0.0.0.0", "0.0.0.0 不该被追加")
    cfg["listen_host"] = "127.0.0.1"
    U.save_json(U.BOT_CFG, cfg)


def t_doctor_all_green():
    import docker_doctor
    for k in ("docker", "p3000", "p6099", "p8088", "reach"):
        FAKE[k] = True
    FAKE["container"] = "running"
    FAKE["online"] = True
    cfg = read_bot_cfg()
    cfg["listen_host"] = "127.0.0.1,172.25.0.1"
    U.save_json(U.BOT_CFG, cfg)
    # 让 doctor 的 keeper 检查与 /health 也通过（真机上它们连的是真进程）
    orig = docker_doctor.keeper_alive
    orig_get = docker_doctor._http_get
    docker_doctor.keeper_alive = lambda: (True, "PID 123 在跑")
    docker_doctor._http_get = lambda url, timeout=5: '{"ok":true,"bot":"selftest"}'
    try:
        rc = docker_doctor.main(["--quiet"])
    finally:
        docker_doctor.keeper_alive = orig
        docker_doctor._http_get = orig_get
    eq(rc, 0, "全绿时退出码")
    bad = [r[0] for r in docker_doctor.ROWS if r[1] is not True]
    eq(bad, [], "不该有失败项")


def t_doctor_broken_reach():
    import docker_doctor
    for k in ("docker", "p3000", "p6099", "p8088"):
        FAKE[k] = True
    FAKE["reach"] = False
    docker_doctor.ROWS.clear()
    orig = docker_doctor.keeper_alive
    orig_get = docker_doctor._http_get
    docker_doctor.keeper_alive = lambda: (True, "PID 123 在跑")
    docker_doctor._http_get = lambda url, timeout=5: '{"ok":true}'
    try:
        rc = docker_doctor.main(["--quiet"])
    finally:
        docker_doctor.keeper_alive = orig
        docker_doctor._http_get = orig_get
    eq(rc, 1, "容器回不来时退出码")
    names = [r[0] for r in docker_doctor.ROWS if r[1] is False]
    eq(names, ["容器→宿主机"], "应精确指出断在容器→宿主机")
    FAKE["reach"] = True


def t_doctor_no_docker():
    import docker_doctor
    FAKE["docker"] = False
    docker_doctor.ROWS.clear()
    orig = docker_doctor.keeper_alive
    docker_doctor.keeper_alive = lambda: (False, "x")
    try:
        rc = docker_doctor.main(["--quiet"])
    finally:
        docker_doctor.keeper_alive = orig
    eq(rc, 2, "没装 docker 时应返回 2（环境没到这一步）")
    FAKE["docker"] = True


def t_mgr_status():
    import docker_mgr
    FAKE["docker"] = True
    FAKE["container"] = "running"
    FAKE["p3000"] = FAKE["p6099"] = True
    FAKE["online"] = True
    eq(docker_mgr.cmd_status(), 0, "在线时 status 返回 0")
    FAKE["online"] = False
    eq(docker_mgr.cmd_status(), 1, "离线时 status 返回 1")
    FAKE["online"] = True
    FAKE["p3000"] = FAKE["p6099"] = False
    eq(docker_mgr.cmd_up(), 0, "compose up 成功返回 0")
    FAKE["p3000"] = FAKE["p6099"] = True


def t_bootstrap_stages():
    import docker_bootstrap as B
    # 让 bootstrap 的临时路径与假环境一致；is_admin 在自测里假定为真
    B.STATE_FILE = os.path.join(TMP, "bootstrap-state.json")
    B.is_admin = lambda: True
    FAKE["docker"] = True
    FAKE["p3000"] = FAKE["p6099"] = True

    ok, note = B.stage_env({})
    eq(ok, True, "stage_env 应通过（.env 已就绪）")
    ok, note = B.stage_features({})
    eq(ok, True, "stage_features 在假 dism 下应通过")
    ok, note = B.stage_daemon({})
    eq(ok, True, "stage_daemon 在假 docker 下应通过")
    ok, note = B.stage_compose({})
    eq(ok, True, "stage_compose 应通过")
    ok, note = B.stage_wire({})
    eq(ok, True, "stage_wire 应通过")
    contains(read_bot_cfg()["listen_host"], "172.25.0.1", "wire 后 listen_host")


def t_bootstrap_env_guard():
    """没 .env 时必须停下来要人填账号，不能瞎猜一个号去登录。"""
    import docker_bootstrap as B
    B.STATE_FILE = os.path.join(TMP, "bootstrap-state2.json")
    real = U.ENV_FILE
    U.ENV_FILE = os.path.join(TMP, "missing.env")
    try:
        ok, note = B.stage_env({})
    finally:
        U.ENV_FILE = real
    eq(ok, False, "缺 .env 时应失败")
    contains(note, "NAPCAT_ACCOUNT", "提示要填账号")


def t_wslconfig_fix():
    """~/.wslconfig 里 kernel= 指向不存在的文件时，必须自动注释掉。

    2026-10-22 12:00 真机事故：2024 年装模拟器留下的 kernel= 让 WSL 拒绝注册
    任何新发行版（WSL_E_CUSTOM_KERNEL_NOT_FOUND），Docker Desktop 首启直接死。
    """
    import docker_bootstrap as B
    home = os.path.join(TMP, "fakehome")
    os.makedirs(home, exist_ok=True)
    p = os.path.join(home, ".wslconfig")
    with open(p, "w", encoding="utf-8") as f:
        f.write("[wsl2]\n"
                "kernel=C:\\\\Tools\\\\kernel_wsa\n"
                "memory=7GB\n")
    old = os.environ.get("USERPROFILE")
    os.environ["USERPROFILE"] = home
    try:
        msg = B._fix_wslconfig()
        eq(bool(msg), True, "应识别出失效的 kernel=")
        with open(p, encoding="utf-8") as f:
            txt = f.read()
        active = [l for l in txt.splitlines()
                  if l.strip() and not l.strip().startswith(("#", ";"))]
        eq(any("kernel" in l.lower() for l in active), False, "不应留下生效的 kernel=")
        eq(any("memory=7GB" in l for l in active), True, "其它配置要保留")
        eq(B._fix_wslconfig(), "", "第二次跑应无改动（幂等）")
        eq(os.path.exists(p + ".bak-auto"), True, "要留备份")
    finally:
        if old is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = old






def t_keeper_docker_mode():
    """keeper 只剩 docker 一条路（2026-10-26 起 inject 已删除）。

    原来测的是 napcat_mode() 在 inject/docker 间怎么选、宽限期过了怎么降级。
    主人定调只用 docker 后，napcat_mode 换成了 docker_ready()：
      可用 → True；不可用 → False（只等，不降级）。
    所以这里断言这两件事，顺带守一条底线：**代码里不能再有 inject 分支**。
    """
    sys.path.insert(0, ROOT)
    import _keeper

    _keeper._DOCKER_OK["t"] = 0.0
    _keeper.docker_available = lambda timeout=25, ttl=60.0: True
    eq(_keeper.docker_ready(), True, "docker 可用时应返回 True")

    # ⚠️ `docker_available()` 的结果**按 ttl 缓存**在 `_DOCKER_OK["t"]/["v"]`，
    #    上面那次调用刚把 True 缓存进去 ⇒ 换桩后必须清缓存，否则断言恒失败。
    _keeper._DOCKER_OK["t"] = 0.0
    _keeper.docker_available = lambda timeout=25, ttl=60.0: False
    _keeper._DOCKER_MISS_SINCE["t"] = time.time() - 7200      # 早就过宽限期了
    eq(_keeper.docker_ready(), False,
       "docker 不可用（即使过了宽限期）：返回 False，只等不降级")
    _keeper._DOCKER_MISS_SINCE["t"] = 0.0

    # 底线：这些只属于 inject 时代的东西不该再出现在**可执行代码**里。
    # ⚠️ 只扫代码行（去掉注释/docstring），否则「原来是 napcat_mode()」这类
    #    解释性注释会误判成残留 —— 2026-10-26 第一次写就踩了这个坑。
    import re as _re
    ksrc = open(os.path.join(ROOT, "_keeper.py"), encoding="utf-8").read()
    code_lines = [l.split("#")[0] for l in ksrc.split("\n")]
    code = "\n".join(code_lines)
    # 再去掉三引号 docstring 内容（粗略够用：成对切）
    code = _re.sub(r'""".*?"""', "", code, flags=_re.S)
    for token in ("napcat_mode", "NAPCAT_LAUNCHER", "NAPCAT_QRCODE",
                  "_try_kill_injected_elevated", "launcher-win10"):
        eq(token in code, False, "keeper 代码里不该再有 %s（inject 已删）" % token)




def _img_tag_ok(image):
    """镜像 tag 检查：官方镜像钉 `v4.x.y` / `latest`；本地自建 `napcat-mixed:*` 也行。

    ⚠️ 2026-10-26 接受 `latest`：compose 切成 latest（主人当时要新版修 OneBot 的 bug）。
    ⚠️ 2026-10-29 接受本地自建：compose 切到混合镜像 `napcat-mixed:4.15.19-qq3.2.34`
    （issue #1728 降级方案：NapCat 4.15.19 + QQ 3.2.34，官方 latest 会被风控检测踢下线）。
    本地镜像 tag 由构建脚本定，只要求非空、不含空格野字符。
    """
    img = str(image or "")
    base = img.rsplit("/", 1)[-1]
    if base.startswith("napcat-mixed:"):
        tag = base.split(":", 1)[1]
        return bool(tag) and re.match(r"^[\w.\-]+$", tag) is not None
    tag = base.rsplit(":", 1)[-1] if ":" in base else ""
    return tag == "latest" or re.match(r"^v\d+\.\d+\.\d+$", tag or "") is not None


def t_compose_yaml():
    """compose 文件必须是合法 YAML，且关键项（镜像 tag/自启/卷/端口）没写错。"""
    path = os.path.join(ROOT, "docker-napcat", "docker-compose.yml")
    assert os.path.exists(path), "compose 文件不见了"
    try:
        import yaml
        with io.open(path, encoding="utf-8") as f:
            d = yaml.safe_load(f)
    except ImportError:
        # 项目零依赖：没有 pyyaml 就退化成文本断言
        txt = io.open(path, encoding="utf-8").read()
        contains(txt, "restart: always", "自启策略")
        _m = re.search(r"image:\s*(\S*napcat-(?:docker|mixed):\S+)", txt)
        assert _m and _img_tag_ok(_m.group(1)), "镜像 tag 不合法：%s" % (txt[:200],)
        contains(txt, "6099:6099", "WebUI 端口")
        contains(txt, "./.config:/app/.config/QQ", "登录态卷")
        return
    svc = d["services"]["napcat"]
    eq(svc["restart"], "always", "自启策略")
    assert _img_tag_ok(svc["image"]), "镜像 tag 不合法：%s" % svc["image"]
    assert re.search(r"napcat-(docker|mixed)", str(svc["image"])), \
        "镜像名不认识（官方 napcat-docker / 本地 napcat-mixed）：%s" % svc["image"]
    assert "./.config:/app/.config/QQ" in svc["volumes"], "登录态卷必须挂"
    assert "3000:3000" in svc["ports"], "OneBot 端口必须映射"


def t_bats_exist():
    # 2026-10-26：docker_rollback.bat 已归档（它唯一作用就是切回 inject，而 inject 已删）
    need = ["install_docker.bat", "docker_go.bat", "docker_doctor.bat",
            "docker_up.bat", "docker_status.bat",
            "docker_wire.bat", "docker_down.bat", "keeper_guard.bat"]
    for n in need:
        p = os.path.join(ROOT, n)
        assert os.path.exists(p), "缺 %s" % n
        b = open(p, "rb").read()
        assert b.count(b"\r\n") == b.count(b"\n"), "%s 必须是 CRLF（cmd 会解析错）" % n
        assert b"chcp 65001" in b, "%s 缺 chcp 65001（中文会乱码）" % n


def main():
    print("=" * 62)
    print(" 方案 B 自测（假 docker，无需真装 Docker）")
    print(" 临时目录: %s" % TMP)
    print("=" * 62)
    install_fakes()

    case("dutil 基础读取（IP/token/uin）", t_dutil)
    case("dutil 输出解码（UTF-16/UTF-8/GBK）", t_dutil_decode)
    case("wire: 预置容器 NapCat 配置", t_wire_seed)
    case("wire: 上报地址改 host.docker.internal", t_wire_napcat_cfg)
    case("wire: listen_host 追加网关且保留回环", t_wire_bot_cfg)
    case("doctor: 全链路通 → 0", t_doctor_all_green)
    case("doctor: 容器回不来 → 精确指认", t_doctor_broken_reach)
    case("doctor: 没 docker → 2", t_doctor_no_docker)
    case("docker_mgr: up/status 判定", t_mgr_status)
    case("bootstrap: 各阶段可重入", t_bootstrap_stages)
    case("bootstrap: 缺 .env 必须停下", t_bootstrap_env_guard)
    case("wsl: .wslconfig 失效 kernel= 自愈", t_wslconfig_fix)
    case("keeper: 只有 docker 一条路", t_keeper_docker_mode)
    case("compose 文件结构", t_compose_yaml)
    case("bat 入口齐全且 CRLF", t_bats_exist)

    bad = [r for r in RESULTS if not r[1]]
    print("")
    print("-" * 62)
    print(" %d/%d 通过" % (len(RESULTS) - len(bad), len(RESULTS)))
    if bad:
        for name, _ok, why in bad:
            print("   ❌ %s：%s" % (name, why))
    print("-" * 62)
    try:
        shutil.rmtree(TMP, ignore_errors=True)
    except Exception:
        pass
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
