# -*- coding: utf-8 -*-
"""方案 B（NapCat 容器化）的公共零件。

为什么单独抽一个文件：wire.py / docker_mgr.py 都要做同样四件事 —— 找 docker、
跑命令、探端口、改 json。以前各写一份，改一处忘三处（2026-10-22 就出现过 wire.py
认的 docker 路径和 keeper 里的不一样）。现在统一从这里取。

**所有外部命令都经过 run()**，它认 `QQBOT_DOCKER_BIN` / `QQBOT_DISM_BIN` /
`QQBOT_WINGET_BIN` 三个环境变量覆盖可执行文件路径，回归测试靠这个塞假 docker 进去。
"""
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))          # .../qq-bot/docker-napcat
ROOT = os.path.dirname(HERE)                               # .../qq-bot
BOT_CFG = os.path.join(ROOT, "config.json")
NAPCAT_CFG_DIR = os.path.join(HERE, "config")              # 挂进容器 /app/napcat/config
DATA_DIR = os.path.join(HERE, ".config")                   # 挂进容器 /app/.config/QQ
ENV_FILE = os.path.join(HERE, ".env")
ENV_EXAMPLE = os.path.join(HERE, ".env.example")
STATE_DIR = os.path.join(ROOT, "state")
BACKUP_DIR = STATE_DIR
# 测试态隔离（2026-11-04 审计）：回归测试会跑 wire 的备份逻辑，
# 不能把「假配置的备份」写进生产 state/。QBOT_TEST 下统一落到临时目录。
if os.environ.get("QBOT_TEST"):
    import tempfile
    BACKUP_DIR = os.path.join(tempfile.gettempdir(), "qbot_wire_bak_test")
COMPOSE_FILE = os.path.join(HERE, "docker-compose.yml")

CONTAINER = "napcat"
HOST_REF = "host.docker.internal"      # 容器里回宿主机的官方域名
NAPCAT_PORT = 3000                     # OneBot HTTP（宿主机映射）
WEBUI_PORT = 6099                      # NapCat WebUI（扫码用）
BOT_PORT = 8088

# Docker Desktop 的常见安装位置。自定义安装用 QQBOT_DOCKER_BIN 指定，
# 找不到时再由注册表兜底。
DOCKER_CANDIDATES = [
    os.environ.get("QQBOT_DOCKER_BIN") or "",
    r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
    r"C:\Program Files\Docker\resources\bin\docker.exe",
]


def _docker_from_registry():
    """从卸载表读 Docker Desktop 的 InstallLocation —— 主人可以装在任意盘。

    写死路径迟早不够用（上面那次就是），注册表是安装器自己写的，最可信。
    """
    try:
        import winreg
    except Exception:
        return ""
    subs = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
    for sub in subs:
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub)
        except OSError:
            continue
        i = 0
        while True:
            try:
                name = winreg.EnumKey(k, i)
                i += 1
            except OSError:
                break
            try:
                sk = winreg.OpenKey(k, name)
                disp = str(winreg.QueryValueEx(sk, "DisplayName")[0])
            except OSError:
                continue
            if "docker desktop" not in disp.lower():
                continue
            try:
                loc = str(winreg.QueryValueEx(sk, "InstallLocation")[0])
            except OSError:
                continue
            for rel in (r"resources\bin\docker.exe", r"Docker\resources\bin\docker.exe"):
                p = os.path.join(loc, rel)
                if os.path.exists(p):
                    return p
    return ""


# ------------------------------------------------------------------ 进程

def decode(b):
    """外部命令输出解码。

    wsl.exe / dism.exe 在中文 Windows 上大量输出是 **UTF-16LE**，按 utf-8 解就是
    「c\\ufffdaa.\\ufffd\\ufffd[ň…」这种满屏乱码 —— 2026-10-22 早上主人跑 features
    阶段就被这个坑了：明明是在说「正在安装 WSL，请重启」，窗口里全是乱码。

    判据用「有没有 \\x00」而不是「乱码多不多」：这些命令的输出里 utf-8 / gbk
    都不可能出现 NUL 字节，只有 UTF-16 有（ASCII 字符的高字节）。用乱码率判会
    在 UTF-16 和 GBK 之间来回猜错（两边都能「成功」解出汉字）。
    """
    if not b:
        return ""
    if isinstance(b, str):
        return b
    if b"\x00" in b:
        n = len(b) - (len(b) % 2)
        for enc in ("utf-16-le", "utf-16"):
            try:
                s = b[:n].decode(enc)
            except Exception:
                continue
            if "\ufffd" not in s:
                return s
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "replace")


def run(cmd, timeout=120, cwd=None, env=None):
    """跑一条外部命令，返回 (rc, 合并输出)。永不抛异常（超时/找不到都给 -1）。"""
    cmd = [str(c) for c in cmd]
    # 无控制台的上下文（pythonw）里调 docker.exe / netstat 等会闪黑框，统一压掉
    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd,
                           env=env, creationflags=no_win)
        out = decode(r.stdout) + decode(r.stderr)
        # UTF-16 解出来会带一堆 \x00 残留（奇数长度截断），清掉免得刷屏
        out = out.replace("\x00", "")
        return r.returncode, out.strip()
    except FileNotFoundError:
        return -1, "找不到命令: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return -1, "命令超时(%ss): %s" % (timeout, " ".join(cmd[:3]))
    except Exception as e:
        return -1, repr(e)


def find_exe(env_key, candidates, names=()):
    """按「环境变量覆盖 → 候选绝对路径 → PATH 里的名字」找可执行文件，找不到返回 ''。"""
    p = os.environ.get(env_key) or ""
    if p:
        return p
    for c in candidates:
        if c and os.path.exists(c):
            return c
    for n in names:
        for d in (os.environ.get("PATH") or "").split(os.pathsep):
            if not d:
                continue
            f = os.path.join(d, n)
            if os.path.exists(f):
                return f
    return ""


def docker_bin():
    """找 docker.exe：环境变量 → 已知布局 → PATH → 注册表 InstallLocation。"""
    p = find_exe("QQBOT_DOCKER_BIN", DOCKER_CANDIDATES[1:], ("docker.exe", "docker"))
    return p or _docker_from_registry()


def docker_desktop_exe():
    """找 Docker Desktop.exe（要它来拉守护进程）。从 docker.exe 位置反推最稳。"""
    cands = []
    db = docker_bin()
    if db:
        # ...\Docker Desktop\resources\bin\docker.exe → 上溯三级是安装根
        root = os.path.dirname(os.path.dirname(os.path.dirname(db)))
        cands.append(os.path.join(root, "Docker Desktop.exe"))
    cands += [
        r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
        r"C:\Program Files\Docker\Docker Desktop.exe",
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return ""


def docker(args, timeout=180, cwd=None):
    """跑 docker 子命令。返回 (rc, 输出)。"""
    d = docker_bin()
    if not d:
        return -1, "没找到 docker 命令（Docker Desktop 装了没 / 启动了没）"
    return run([d] + list(args), timeout=timeout, cwd=cwd or HERE)


def docker_ok(timeout=25):
    """守护进程在不在。返回 (ok, 版本或错误)。"""
    rc, out = docker(["version", "--format", "{{.Server.Version}}"], timeout=timeout)
    if rc == 0 and out.strip():
        return True, out.strip().splitlines()[-1]
    return False, out.strip()[:300]


# ------------------------------------------------------------------ 网络

def port_open(port, host="127.0.0.1", timeout=3):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def http_json(url, payload=None, token="", timeout=6):
    """POST 一段 json 拿 json 回来（绕系统代理，否则 localhost 也可能被代理劫走）。"""
    data = json.dumps(payload if payload is not None else {}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def host_ip():
    """找 Windows 侧跟 WSL/容器通信那张虚拟网卡的 IPv4（容器要回连它）。

    优先认 vEthernet (WSL) / Docker / Hyper-V 网卡（形如 172.25.0.1）；
    找不到就退而取一个私有地址；都没有返回 ''。
    """
    rc, out = run(["ipconfig"], timeout=30)
    if rc != 0 or not out:
        return ""
    cur, ips = "", {}
    for line in out.splitlines():
        if line and not line[0].isspace() and ":" in line:      # 网卡名行
            cur = line.split(":")[0].strip().lower()
            ips.setdefault(cur, [])
            continue
        m = re.search(r"IPv4[^:]*:\s*([\d.]+)", line)
        if m and cur:
            ips[cur].append(m.group(1))
    for key in ips:                                             # WSL / Docker 网卡优先
        if any(t in key for t in ("wsl", "docker", "hyper-v")):
            for ip in ips[key]:
                if not ip.startswith("127."):
                    return ip
    for _key, lst in ips.items():
        for ip in lst:
            if ip.startswith(("172.", "192.168.", "10.")):
                if ip != "172.17.0.1":                          # Docker 默认网桥网关，不是宿主机
                    return ip
    return ""


# ------------------------------------------------------------------ 文件

def load_json(path, default=None):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2))
        f.write("\n")


def backup(path, tag="bak"):
    """复制一份到 state/ 带时间戳，返回备份路径（失败返回 ''）。"""
    if not os.path.exists(path):
        return ""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(BACKUP_DIR, "%s.%s-%s" % (os.path.basename(path), tag, stamp))
        with open(path, "rb") as a, open(dst, "wb") as b:
            b.write(a.read())
        return dst
    except Exception:
        return ""


def read_env(path=ENV_FILE):
    """.env 是 KEY=VALUE 一行一个，不引第三方库（项目零依赖纪律）。"""
    out = {}
    try:
        with io.open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def bot_cfg():
    return load_json(BOT_CFG, {}) or {}


def napcat_token():
    return str(bot_cfg().get("token") or "")


def napcat_status(timeout=6):
    """问 NapCat 账号在不在线。返回 (state, detail)：state = True/False/None(探不到)。

    2026-10-22 19:2x 修：QQ 长连接重连时 `/get_status` 的 `online` 会**闪一下 false**
    （实测：bot 正在正常收发消息，体检却报"账号离线"）。单次探测会误报，
    keeper 虽然有三连击去抖，但单次体检报出来容易骗人。
    现在拿到 False 就隔 2 秒再探一次，两次都 False 才认。
    """
    if not port_open(NAPCAT_PORT):
        return None, "127.0.0.1:%d 连不上" % NAPCAT_PORT
    for attempt in range(2):
        try:
            d = http_json("http://127.0.0.1:%d/get_status" % NAPCAT_PORT,
                          {}, napcat_token(), timeout=timeout)
            data = d.get("data") or {}
            online = data.get("online")
            if online is None:
                return None, "get_status 没给 online 字段"
            detail = "uin=%s online=%s good=%s" % (
                data.get("uin"), online, data.get("good"))
            if online:
                return True, detail
            if attempt == 0:
                time.sleep(2.0)      # 闪断？再探一次
                continue
            return False, detail + "（连续两次探到离线）"
        except Exception as e:
            if attempt == 0:
                time.sleep(2.0)
                continue
            return None, "get_status 异常: %r" % (e,)
    return False, "unreachable"


def uin_from_env_or_cfg():
    """要登录的 QQ 号：.env 的 NAPCAT_ACCOUNT 优先，其次 config.json 里的 napcat.uin。"""
    u = read_env().get("NAPCAT_ACCOUNT") or ""
    if not u:
        u = str((bot_cfg().get("napcat") or {}).get("uin") or "")
    return re.sub(r"\D", "", u)


def die(msg, code=1):
    print(msg)
    return code


if __name__ == "__main__":
    # 手工排查用：python docker-napcat/dutil.py
    print("ROOT      =", ROOT)
    print("docker    =", docker_bin() or "(未找到)")
    print("docker ok =", docker_ok())
    print("host_ip   =", host_ip() or "(未找到)")
    print("napcat    =", napcat_status())
    print("uin       =", uin_from_env_or_cfg() or "(未配置)")
