# -*- coding: utf-8 -*-
"""双击就出登录二维码 —— 不用再找人帮你导。

主人 2026-10-27：「每次都是你给我二维码。我都不知道怎么调出二维码」。
所以做成双击可跑：查状态 → 掉线就把容器里当前那张码捞出来自动打开 → 已经在线就直说。

用法（三种都行）：
  1. 双击 qq-bot 目录下的「看二维码.cmd」
  2. 命令行：python tools/show_qr.py
  3. 加 --open-webui 直接开 NapCat WebUI（那边二维码会自己刷新，适合守着等它变）

为什么不用 WebUI 当唯一入口：WebUI 是 React 单页，得先过登录页、再点进「配置」才看得到码，
掉线着急的时候多几步都是坑。这里直接给图片，双击→看见，两秒。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "state", "_qr_live.png")
CONTAINER = "napcat"


def _read_secret(name, config_key, dotenv_key=None):
    """凭据一律不写死在源码里（2026-11-05 审计 U1）：环境变量优先，其次 config.json，
    最后 docker-napcat/.env。都取不到返回空串，由调用方报错。
    """
    v = os.environ.get(name, "").strip()
    if v:
        return v
    try:
        import json
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
            v = (json.load(f).get(config_key) or "").strip()
        if v:
            return v
    except Exception:
        pass
    try:
        with open(os.path.join(ROOT, "docker-napcat", ".env"), encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith((dotenv_key or name) + "="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


HTTP_TOKEN = _read_secret("NAPCAT_HTTP_TOKEN", "token")      # NapCat HTTP Server 的 token
_WEBUI_TOKEN = _read_secret("NAPCAT_WEBUI_TOKEN", "", dotenv_key="WEBUI_TOKEN")
WEBUI_URL = "http://127.0.0.1:6099/webui?token=%s" % _WEBUI_TOKEN

# 2026-10-27：主人要验「回退到 QQ 3.2.21」，起了一个独立测试容器 napcat-old。
# 它和主容器的区别只有端口：3100/3101/6199。换码节奏一样是 2 分钟一张
# （22:28:57 → 22:33:18 → 22:35:19 实测，之前我一度以为旧版不换码，是错的）。
OLD_CONTAINER = "napcat-old"
OLD_WEBUI_URL = "http://127.0.0.1:6199/webui?token=%s" % _WEBUI_TOKEN
OLD_QR_MAX_AGE = 100                     # 超过这个秒数就重启容器换新码（留给扫码的余量）

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)


def _run(args, timeout=25):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              errors="replace", timeout=timeout)
    except Exception as e:                                    # noqa: BLE001
        print("  ！执行失败：%s（%s）" % (" ".join(args[:2]), e))
        return None


def container_state():
    """返回 ('running'|'stopped'|'none', 说明)。"""
    r = _run(["docker", "ps", "-a", "--filter", "name=%s" % CONTAINER,
              "--format", "{{.Status}}"])
    if r is None or r.returncode != 0:
        return "none", "docker 命令不可用（Docker Desktop 可能没启动）"
    txt = (r.stdout or "").strip()
    if not txt:
        return "none", "找不到 napcat 容器"
    if txt.startswith("Up"):
        return "running", txt
    return "stopped", txt


def login_info():
    """NapCat 的 get_login_info。返回 (已登录?, 昵称/QQ)。

    ⚠️ 注意：这个接口在**长连接断掉时仍然返回 ok**（只是读内存里的账号），
    所以它说"在线"不等于能收发。真要确认通不通，得实际发一条消息。
    这里只用来判断「要不要给你看二维码」——二维码只在真掉线时才有意义。
    """
    r = _run(["docker", "exec", CONTAINER, "curl", "-s", "-m", "6",
              "-X", "POST", "-H", "Content-Type: application/json",
              "-H", "Authorization: Bearer %s" % HTTP_TOKEN,
              "-d", "{}", "http://127.0.0.1:3000/get_login_info"])
    if r is None:
        return False, "接口无响应"
    body = (r.stdout or "").strip()
    if not body:
        return False, "接口返回空（NapCat 的 OneBot 层可能已经退出）"
    try:
        import json
        d = json.loads(body)
    except Exception:
        return False, body[:60]
    data = d.get("data") or {}
    if d.get("status") == "ok" and data.get("user_id"):
        return True, "%s(%s)" % (data.get("nickname") or "?", data["user_id"])
    return False, d.get("message") or "未登录"


def export_qr():
    """把容器里的 qrcode.png 捞到本地。返回 (成功?, 路径/原因)。"""
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    r = _run(["docker", "cp", "%s:/app/napcat/cache/qrcode.png" % CONTAINER, OUT])
    if r is None or r.returncode != 0:
        err = (r.stderr or "").strip() if r else "docker cp 失败"
        return False, err[:120] or "docker cp 失败"
    if not os.path.isfile(OUT) or os.path.getsize(OUT) < 200:
        return False, "捞出来的文件不对（太小，可能容器里还没生成码）"
    # 147x147 的原图在 1080p 屏上手机不好对焦，NEAREST 放大 6 倍（不动像素，仍是标准 QR）
    try:
        from PIL import Image
        im = Image.open(OUT)
        if im.width < 300:
            big = OUT.replace(".png", "_big.png")
            im.convert("RGB").resize((im.width * 6, im.height * 6), Image.NEAREST).save(big)
            return True, big
    except Exception:
        pass
    return True, OUT


def qr_age_seconds():
    """容器里那张码是多久以前生成的（秒）。拿不到返回 None。

    NapCat 日志形如 `10-27 22:33:18 [warn] 二维码已保存到 /app/napcat/cache/qrcode.png`。
    为什么要它：NapCat 每 2 分钟换一张码，如果捞到的是三分钟前那张，扫了只会提示
    「二维码已失效」。知道码龄就能在太旧时先重启换新的，省得主人反复试。
    """
    r = _run(["docker", "logs", "--tail", "200", CONTAINER], timeout=20)
    if r is None or r.returncode != 0:
        return None
    stamp = None
    for line in (r.stdout or "").splitlines():
        if "二维码已保存" in line:
            stamp = line[:14]                      # "10-27 22:33:18"
    if not stamp:
        return None
    try:
        t = time.strptime("%d-%s" % (time.localtime().tm_year, stamp), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    return max(0, int(time.time() - time.mktime(t)))


def restart_for_fresh_qr():
    """重启容器并等它画出新码（旧版容器专用）。返回 (成功?, 说明)。"""
    print(" 这张码已经过期，重启容器换一张新的（约 20 秒）…")
    r = _run(["docker", "restart", CONTAINER], timeout=90)
    if r is None or r.returncode != 0:
        return False, "docker restart 失败"
    for _ in range(25):
        time.sleep(3)
        age = qr_age_seconds()
        if age is not None and age <= 60:
            return True, "新码已出（%ds 前）" % age
    return False, "等了 75 秒还没看到新码"


def main():
    global CONTAINER, WEBUI_URL, OUT

    if "--old" in sys.argv:
        CONTAINER, WEBUI_URL = OLD_CONTAINER, OLD_WEBUI_URL
        OUT = os.path.join(ROOT, "state", "_qr_old.png")

    print("=" * 52)
    print(" 小鲸登录二维码%s" % ("（旧版测试容器）" if CONTAINER == OLD_CONTAINER else ""))
    print("=" * 52)

    if not HTTP_TOKEN or not _WEBUI_TOKEN:
        print(" 取不到凭据：需要环境变量 NAPCAT_HTTP_TOKEN / NAPCAT_WEBUI_TOKEN，")
        print(" 或本目录 config.json 的 token 字段 + docker-napcat/.env 的 WEBUI_TOKEN。")
        return 1

    if "--open-webui" in sys.argv:
        print("打开 WebUI：%s" % WEBUI_URL)
        os.startfile(WEBUI_URL)                                # noqa: S606
        return 0

    state, note = container_state()
    print("容器：%s - %s" % (state, note))
    if state != "running":
        print()
        print(" 容器没在跑，先把 Docker Desktop 打开（桌面图标双击）。")
        print(" 起来之后再双击本文件。")
        return 1

    ok, info = login_info()
    print("账号：%s" % info)
    if ok:
        print()
        print(" 已经登录了，没有二维码可看。")
        print(" 如果群里/私聊还是不回话，多半是长连接断了但接口还显示在线 ——")
        print(" 那种情况要重启容器：docker restart napcat（重启后大概率要重新扫码）")
        return 0

    print()
    age = qr_age_seconds()
    if CONTAINER == OLD_CONTAINER:
        if age is None or age > OLD_QR_MAX_AGE:
            ok2, why2 = restart_for_fresh_qr()
            print(" %s" % why2)
        else:
            print(" 码龄 %ds，还新鲜，直接用" % age)
    elif age is not None:
        print(" 码龄 %ds" % age)

    print("正在从容器里取二维码…")
    good, path_or_err = export_qr()
    if not good:
        print(" 取不到：%s" % path_or_err)
        print(" 试试等 10 秒再双击一次 —— NapCat 大概每 2 分钟刷一张新码。")
        return 1

    size = os.path.getsize(path_or_err)
    mtime = time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(path_or_err)))
    print(" 已取到：%s（%d 字节，%s 那张）" % (path_or_err, size, mtime))
    print()
    print(" 手机 QQ -> 扫一扫 -> 扫刚弹出来的这张图")
    print(" 【注意】二维码大概 2 分钟过期。扫的时候提示失效，就再双击本文件一次。")
    print(" 【注意】扫完记得在手机上退出小鲸这个号，别让手机端跟容器抢。")
    print()
    try:
        os.startfile(path_or_err)                              # noqa: S606
        print(" 图片已用默认看图程序打开。")
    except Exception as e:                                     # noqa: BLE001
        print(" 自动打开失败（%s），手动双击这个文件：%s" % (e, path_or_err))
    return 0


if __name__ == "__main__":
    sys.exit(main())
