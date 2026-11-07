# -*- coding: utf-8 -*-
"""从主人的 Edge 里「问」出禁漫站 cookie（明文），写进 state/jm_auth.json。

为什么要这么绕
--------------------------------------------------------------------------
新版 Edge（本机 154）的 cookie 是 **App-Bound 加密（v20）**，离线谁都解不开：
  · `browser_cookie3` 只认 v10/v11，没有 v20 分支；
  · 直接读 Cookies 文件也不行 —— Chromium 是**独占**打开（实测 CreateFileW 带
    FILE_SHARE_READ|WRITE|DELETE 仍报 err=32），Edge 一开着就摸不到。
但浏览器**自己**能解。所以思路是：
  ① 复制一份**最小 profile**（Local State + Default/Network/Cookies + Preferences，约 1MB）；
  ② 用同一个 msedge.exe 启动这份副本，连 CDP，让 Edge 把 cookie 明文交出来。

实测（2026-10-09）：
  · ABE cookie **能跨目录解密**（整份搬 21/21 保活；最小副本同样 21/21，
    即磁盘上有的全在）；
  · Playwright 能驱动真实 Edge（`channel="msedge"`，1.4s 起来）。

⚠️ 前置条件：**必须先把 Edge 完全退出**（否则 Cookies 文件被独占，复制必失败）。

用法
--------------------------------------------------------------------------
    python tools/_jm_grab_cookie.py            # 抓取 → 写 state/jm_auth.json
    python tools/_jm_grab_cookie.py --show     # 只显示（值打码），不落盘
    python tools/_jm_grab_cookie.py --verify   # 抓完顺手拉一次收藏夹验证
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

EDGE_DATA = os.path.join(os.environ.get("LOCALAPPDATA") or "", "Microsoft", "Edge", "User Data")
TMP_PROFILE = os.path.join(ROOT, "state", "_edge_grabbed")
AUTH_PATH = os.path.join(ROOT, "state", "jm_auth.json")

# 禁漫站用过的域名（jmcomic 会自动换域名，所以按关键词匹配，别写死）
DOMAIN_HINTS = ("18comic", "comic18", "jm365", "jmcomic",
                "cdnhjk", "cdngwc", "cdnhjk", "18comic")
# 真正要的凭据
WANT_COOKIES = ("AVS", "cf_clearance", "cloudflare", "__cf_bm", "jmid", "jmtoken")


def edge_running():
    """Edge 是否在跑 —— 跑着的话 Cookies 文件被独占，必须先退。"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq msedge.exe", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return None
    n = sum(1 for ln in out.splitlines() if "msedge.exe" in ln.lower())
    return n


def _copy_min(profile_dst):
    """只搬最小集，别搬 3.3GB。"""
    os.makedirs(os.path.join(profile_dst, "Default", "Network"), exist_ok=True)
    pairs = [
        ("Local State", "Local State"),
        (os.path.join("Default", "Network", "Cookies"),
         os.path.join("Default", "Network", "Cookies")),
        (os.path.join("Default", "Preferences"), os.path.join("Default", "Preferences")),
    ]
    total = 0
    for rel_src, rel_dst in pairs:
        s = os.path.join(EDGE_DATA, rel_src)
        if not os.path.exists(s):
            print("  !! 缺 %s" % rel_src)
            continue
        d = os.path.join(profile_dst, rel_dst)
        shutil.copy2(s, d)
        total += os.path.getsize(d)
    return total


def grab():
    """返回 (cookies_dict, raw_list)。cookies_dict = {name: value}"""
    if os.path.isdir(TMP_PROFILE):
        shutil.rmtree(TMP_PROFILE, ignore_errors=True)
    size = _copy_min(TMP_PROFILE)
    print("  最小副本 %.2f MB" % (size / 1048576.0))

    from playwright.sync_api import sync_playwright
    picked, raw = {}, []
    pw = sync_playwright().start()
    try:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=TMP_PROFILE, channel="msedge", headless=True,
            args=["--no-first-run", "--no-default-browser-check"])
        try:
            allck = ctx.cookies()
        finally:
            ctx.close()
    finally:
        pw.stop()

    print("  读回 cookie 共 %d 条" % len(allck))
    for c in allck:
        dom = (c.get("domain") or "").lower()
        if any(h in dom for h in DOMAIN_HINTS):
            raw.append(c)
            v = c.get("value") or ""
            # 同名多条时优先留 cf_clearance / AVS 这种长值
            if c["name"] not in picked or len(v) > len(picked[c["name"]]):
                picked[c["name"]] = v
    return picked, raw


def mask(v):
    v = str(v or "")
    return (v[:5] + "…(%d)" % len(v)) if len(v) > 8 else "<空>"


def main():
    ap = argparse.ArgumentParser(description="从 Edge 抓禁漫站 cookie")
    ap.add_argument("--show", action="store_true", help="只显示，不写文件")
    ap.add_argument("--verify", action="store_true", help="抓完顺手拉一次收藏夹")
    ap.add_argument("--user", default="atatier", help="JM 用户名")
    arg = ap.parse_args()

    n = edge_running()
    if n is None:
        print("查不到 Edge 进程，继续尝试…")
    elif n > 0:
        print("✗ Edge 正在运行（%d 个进程）。" % n)
        print("  它独占着 Cookies 文件，复制必然失败。")
        print("  ⇒ 请**完全退出 Edge**（所有窗口 + 托盘图标），再跑本脚本。")
        return 2

    print("[1] 复制最小 profile")
    cookies, raw = grab()

    if not cookies:
        print("[2] ✗ 没读到禁漫相关 cookie。")
        print("    可能：① Edge 里其实没登录；② 登录的是别的 profile（本机只发现 Default）")
        print("    raw 条数 =", len(raw))
        return 1

    print("[2] 禁漫相关 cookie：")
    for c in sorted(raw, key=lambda x: x["name"]):
        print("    %-16s %-24s %s" % (c["name"], c["domain"], mask(c.get("value"))))

    must = cookies.get("AVS")
    if not must:
        print()
        print("⚠️ 没有 AVS —— 这是登录态凭据，没有它拉不到收藏夹。")
        print("   （其它 cookie 仍会保存，但大概率不够）")

    if arg.show:
        print("[3] --show：不落盘")
        return 0

    auth = {"username": arg.user, "cookies": cookies,
            "_grabbed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "_source": "edge-min-profile-copy"}
    os.makedirs(os.path.dirname(AUTH_PATH), exist_ok=True)
    with open(AUTH_PATH, "w", encoding="utf-8") as f:
        json.dump(auth, f, ensure_ascii=False, indent=2)
    print("[3] 已写入 %s（%d 条）" % (AUTH_PATH, len(cookies)))

    if arg.verify:
        print("[4] 验证：拉一次收藏夹")
        try:
            from tools import _jm_favorites as jf  # noqa
        except Exception:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "_jm_favorites", os.path.join(ROOT, "tools", "_jm_favorites.py"))
            jf = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(jf)
        jf._no_proxy()
        try:
            rows, total = jf.fetch(arg.user, cookies)
            print("    ✓ 成功：收藏 %s 本，取到 %d 本" % (total, len(rows)))
            for r in rows[:5]:
                print("      %-9s %s" % (r["id"], r["title"][:50]))
        except Exception as e:
            print("    ✗ 失败：%s: %s" % (type(e).__name__, str(e)[:200]))
            return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP_PROFILE, ignore_errors=True)
