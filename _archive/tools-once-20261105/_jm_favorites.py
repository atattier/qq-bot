# -*- coding: utf-8 -*-
"""拉取 JM 收藏夹里的本子（ID + 标题）—— 网页版收藏夹，就是主人给的那个页面。

    https://18comic.vip/user/atatier/favorite/albums
                              ^^^^^^^ username

凭据
--------------------------------------------------------------------------
需要浏览器登录后的 cookies。**至少要有 `AVS`**；如果站点还要过 Cloudflare，
把 `cf_clearance` 也一起给。

⚠️ 为什么不"自动从浏览器读"：实测过两条路都死（2026-10-09）——
  ① Edge/Chrome 新版的 cookie 是 **App-Bound 加密（v20）**，`browser_cookie3 0.20.1`
     源码里根本没有 v20 分支（只支持 v10/v11），解不出来；
  ② Edge 开着时故意独占锁了 Cookies 文件（`cp` 报 Device or resource busy），
     绕锁要卷影复制（需管理员）。
⇒ 所以走「手动取一次 cookie」这条路。JM 的 AVS 有效期通常几个月，取一次能用很久。

cookie 存哪
--------------------------------------------------------------------------
放在 **`state/jm_auth.json`**，**故意不写进 config.json** —— 因为 bot 的
`save_config()` 会用内存 CFG 整体覆盖 config.json（2026-10-09 踩过，见 MEMORY），
凭据放那儿会随时被冲掉。

    {"username": "atatier",
     "cookies": {"AVS": "……", "cf_clearance": "……"}}

用法
--------------------------------------------------------------------------
    python tools/_jm_favorites.py                 # 列表：序号 / ID / 标题
    python tools/_jm_favorites.py --ids-only      # 只打 ID，一行一个（方便喂给 .jm）
    python tools/_jm_favorites.py --json          # JSON，便于程序消费
    python tools/_jm_favorites.py --folder 12345  # 只看某个收藏夹（默认 0 = 全部）
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
AUTH_PATH = os.path.join(ROOT, "state", "jm_auth.json")
DEFAULT_USER = "atatier"
PAGE_SIZE = 20          # JM 收藏夹每页 20 条


def _no_proxy():
    """⚠️ 本机（WorkBuddy 会话 / 某些终端）会注入 HTTP(S)_PROXY，curl_cffi 会照样走它，
    结果请求禁漫域名时直接 `CONNECT tunnel failed, response 502`。
    实测 2026-10-09：清掉代理后同一个域名立刻能通 ⇒ **禁漫站一律绕代理走直连**。"""
    for k in ("http_proxy", "https_proxy", "all_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(k, None)


def load_auth(path=AUTH_PATH):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("读取 %s 失败：%s" % (path, e))
        return None


def fetch(username, cookies, folder_id="0", max_pages=60, on_page=None):
    """翻页拉全部收藏，返回 (rows, total)。rows = [{'id':..,'title':..}, ...]"""
    from jmcomic import JmOption
    opt = JmOption.default()
    if cookies:
        opt.update_cookies(dict(cookies))
    cl = opt.new_jm_client(impl="html")     # 网页版客户端：走 /user/<name>/favorite/albums

    rows, total = [], None
    for page in range(1, max_pages + 1):
        p = cl.favorite_folder(page=page, folder_id=folder_id, username=username)
        total = getattr(p, "total", total)
        content = list(getattr(p, "content", None) or [])
        if not content:
            break
        for item in content:
            # jmcomic 的解析结果是 (aid, {'name': title})
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                aid = str(item[0])
                meta = item[1]
                title = meta.get("name", "") if isinstance(meta, dict) else str(meta)
            elif isinstance(item, dict):
                aid = str(item.get("id") or item.get("album_id") or "")
                title = item.get("name") or item.get("title") or ""
            else:
                continue
            if aid:
                rows.append({"id": aid, "title": title})
        if on_page:
            on_page(page, len(rows), total)
        if len(content) < PAGE_SIZE:
            break
    return rows, total


def main():
    ap = argparse.ArgumentParser(description="拉取 JM 收藏夹的本子 ID")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--ids-only", action="store_true", help="只输出 ID，一行一个")
    ap.add_argument("--folder", default="0", help="收藏夹 ID，默认 0=全部")
    ap.add_argument("--user", default=None, help="JM 用户名，默认读 jm_auth.json")
    arg = ap.parse_args()

    auth = load_auth() or {}
    cookies = auth.get("cookies") or {}
    username = arg.user or auth.get("username") or DEFAULT_USER

    _no_proxy()

    if not cookies.get("AVS"):
        print("缺凭据：请在 %s 里填 cookies.AVS（浏览器登录后复制）" % AUTH_PATH)
        print('格式： {"username": "%s", "cookies": {"AVS": "你的AVS值"}}' % username)
        return 2

    def on_page(page, got, total):
        sys.stderr.write("  第 %d 页…（累计 %d/%s）\n" % (page, got, total if total else "?"))

    try:
        rows, total = fetch(username, cookies, folder_id=arg.folder, on_page=on_page)
    except Exception as e:
        print("拉取失败：%s: %s" % (type(e).__name__, str(e)[:300]))
        return 1

    if arg.json:
        print(json.dumps({"username": username, "total": total, "count": len(rows),
                          "albums": rows}, ensure_ascii=False, indent=2))
    elif arg.ids_only:
        for r in rows:
            print(r["id"])
    else:
        print("用户 %s：收藏 %s 本（本次取到 %d 本）" % (username, total if total else "?", len(rows)))
        print()
        for i, r in enumerate(rows, 1):
            print("%4d. %-9s %s" % (i, r["id"], r["title"][:60]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
