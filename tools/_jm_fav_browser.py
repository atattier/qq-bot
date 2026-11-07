# -*- coding: utf-8 -*-
"""用**真浏览器**读 JM 收藏夹 —— 绕开 Cloudflare。

为什么不能用 Python 爬（实测 2026-10-09）
--------------------------------------------------------------------------
- AVS cookie 没问题，但站点有 Cloudflare：请求走 Clash（新加坡出口）跳到
  `jmcomic-zzz.one`，CF 直接返回 `HTTP 403 + Cf-Mitigated: challenge`，正文是
  那段 `Just a moment...`。
- `cf_clearance` 是绑 IP **和 TLS 指纹**的。curl_cffi 的 Chrome 模拟再像，
  JA3/JA4 跟真 Edge 不一样 ⇒ 就算把浏览器里的 cf_clearance 偷出来喂给它，
  大概率还是被拒。
⇒ 所以：**让真浏览器自己过 CF**，我们只负责读 DOM。

而且这条路**真正"实时"**：登录态存在独立的 Edge profile 里（不是 cookie 文件），
下次跑直接就有，不用再手抄、不用再登录。

用法
--------------------------------------------------------------------------
    python tools/_jm_fav_browser.py                 # 开真浏览器读收藏夹
    python tools/_jm_fav_browser.py --ids-only      # 只输出 ID，一行一个
    python tools/_jm_fav_browser.py --json
    python tools/_jm_fav_browser.py --headed        # 显式有头（默认就是有头）
    python tools/_jm_fav_browser.py --headless      # 无头（CF 大概率拦，仅供对比）

首次会弹一个 Edge 窗口：如果没登录，在里面登录一次即可（之后永久有效）。
如果我们已经从别处拿到 AVS，会自动注入，你可能只需要**过一下 Cloudflare**。
"""

import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(ROOT, "state", "jm_edge_profile")
AUTH_PATH = os.path.join(ROOT, "state", "jm_auth.json")

START_URLS = [
    "https://18comic.vip/user/{u}/favorite/albums",
    "https://comic18j-ada.space/user/{u}/favorite/albums",
]
AID_RE = re.compile(r"/album/(\d{3,9})")
# 封面懒加载时，回退取父容器会把整条导航栏也带进来 ⇒ 命中这些就直接判为脏数据
NAV_MARK = ("成人A漫", "JM文庫", "贊助JM", "上傳漫畫", "JM公告", "評論區", "分流")
CF_MARK = ("just a moment", "attention required", "checking your browser",
           "请稍候", "正在验证")


def log(*a):
    """诊断信息一律走 stderr —— 否则 --json 的 stdout 会被污染，下游 json.load 直接炸。"""
    sys.stderr.write(" ".join(str(x) for x in a) + "\n")


def load_auth():
    if not os.path.exists(AUTH_PATH):
        return {}
    try:
        with open(AUTH_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def wait_cloudflare(page, timeout=90):
    """真浏览器会自动解 CF 挑战，我们只要等它解完。"""
    t0 = time.time()
    waited = 0.0
    while time.time() - t0 < timeout:
        try:
            title = (page.title() or "").lower()
        except Exception:
            title = ""
        if not any(m in title for m in CF_MARK):
            return waited
        time.sleep(2)
        waited = time.time() - t0
    return waited


EVAL_JS = """() => {
    const out = [];
    const clean = s => (s || '').trim().replace(/\\s+/g, ' ');
    document.querySelectorAll('a[href*="/album/"]').forEach(a => {
        const h = a.getAttribute('href') || '';
        // 卡片一定有封面图；导航栏里那种纯文字 /album 链接没有图 ⇒ 用这个区分
        const hasImg = !!a.querySelector('img');
        const img = a.querySelector('img');
        let t = clean(a.innerText);
        if (!t && img) t = clean(img.getAttribute('alt') || img.getAttribute('title'));
        if (!t) {
            const p = a.closest('div') || a.parentElement;
            if (p) t = clean(p.innerText);
        }
        out.push([h, t, hasImg]);
    });
    // 顺带抓一下"共 N 本"
    const body = document.body ? document.body.innerText : '';
    const m = body.match(/共\\s*([\\d,]+)\\s*本/);
    return {items: out, total: m ? m[1] : null};
}"""


def eval_albums(page, tries=6):
    """⚠️ Cloudflare 验证时会**刷新页面** ⇒ 执行上下文被销毁，evaluate 直接抛
    'Execution context was destroyed'。所以必须重试，别一发就判失败。"""
    last = None
    for _ in range(tries):
        try:
            return page.evaluate(EVAL_JS)
        except Exception as e:
            last = e
            time.sleep(2.5)
    raise last


def goto_robust(page, ctx, url, timeout=90000):
    """⚠️ Cloudflare 挑战有时会把当前 page 关掉（'Target page... has been closed'）。
    ⇒ 关了就重建一个 page 再试，别直接放弃整轮。"""
    if page is not None and not page.is_closed():
        try:
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            return page
        except Exception as e:
            if "has been closed" not in str(e):
                raise
    page = ctx.new_page()
    page.goto(url, timeout=timeout, wait_until="domcontentloaded")
    return page


def wait_albums(page, min_count=4, timeout=90):
    """⚠️ 不能"导航完就抓"：Cloudflare 挑战页里也有 /album 链接（导航栏/推荐），
    一抓就是 1 条假数据。必须等到**带封面的卡片**真的渲染出来。"""
    t0, n = time.time(), 0
    while time.time() - t0 < timeout:
        try:
            if any(m in (page.title() or "").lower() for m in CF_MARK):
                time.sleep(2)
                continue
            n = page.evaluate(
                "() => document.querySelectorAll('a[href*=\"/album/\"] img').length") or 0
        except Exception:
            n = 0
        if n >= min_count:
            return n
        time.sleep(2)
    return n


def scrape(page, url, ctx=None):
    if ctx is not None:
        page = goto_robust(page, ctx, url)
    else:
        page.goto(url, timeout=90000, wait_until="domcontentloaded")
    w = wait_cloudflare(page)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    n = wait_albums(page, min_count=4, timeout=60)
    if n < 4:                       # 大概率还卡在挑战页，重载一次再等
        try:
            page.reload(timeout=60000, wait_until="domcontentloaded")
            w += wait_cloudflare(page)
            wait_albums(page, min_count=4, timeout=60)
        except Exception:
            pass
    host = re.sub(r"^https?://", "", page.url).split("/")[0]
    try:
        data = eval_albums(page)
    except Exception as e:
        return page, [], host, w, None, "evaluate 失败: %s" % str(e)[:120]
    items = (data or {}).get("items") or []
    total = (data or {}).get("total")
    def build(it):
        """返回 (有效行, 剔除行)。
        ⚠️ 每页第 1 条是导航栏里的"随机推荐位"（ID 每次刷新都变、标题是导航文本），
        不是收藏内容 ⇒ 命中导航栏特征或标题为空的一律剔除。"""
        rows, dropped, seen = [], [], set()
        for it_ in it:
            href, title = it_[0], it_[1]
            m = AID_RE.search(href or "")
            if not m:
                continue
            aid = m.group(1)
            if aid in seen:
                continue
            seen.add(aid)
            t = (title or "")[:80]
            if (not t) or any(mk in t for mk in NAV_MARK):
                dropped.append({"id": aid, "why": "导航栏/随机位"})
                continue
            rows.append({"id": aid, "title": t})
        return rows, dropped

    # 优先只留"有封面图"的卡片；数量太少才退回全量
    card = [x for x in items if len(x) > 2 and x[2]]
    rows, dropped = build(card)
    if len(rows) < 3:
        rows, dropped = build(items)
    if dropped:
        sys.stderr.write("  剔除 %d 条疑似导航栏随机位：%s\n"
                         % (len(dropped), ",".join(d["id"] for d in dropped)))
    return page, rows, host, w, total, None


def main():
    ap = argparse.ArgumentParser(description="用真浏览器读 JM 收藏夹")
    ap.add_argument("--ids-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--headed", action="store_true", default=True)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--user", default=None)
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--no-cookie", action="store_true", help="不注入已有 AVS")
    arg = ap.parse_args()

    auth = load_auth()
    user = arg.user or auth.get("username") or "atatier"
    cookies = {} if arg.no_cookie else dict(auth.get("cookies") or {})

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print("没有 playwright：%s" % e)
        return 1

    log("用户名 = %s" % user)
    log("待注入 cookie = %s" % (list(cookies) or "无"))
    log("profile = %s" % PROFILE)

    all_rows, seen = [], set()
    info = {}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE,
            channel="msedge",
            headless=bool(arg.headless),
            viewport={"width": 1280, "height": 900},
            args=[
                "--no-first-run", "--no-default-browser-check",
                # 反自动化检测：CF 会读 navigator.webdriver，默认被 Playwright 置为 true
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            # ① 先探一次，只为拿到**当前生效的域名**（未登录时会跳到 /login，
            #    所以只能取 host，绝不能把跳转后的 URL 当收藏夹地址）
            host = None
            for tpl in START_URLS:
                u = tpl.format(u=user)
                try:
                    page.goto(u, timeout=60000, wait_until="domcontentloaded")
                    host = re.sub(r"^https?://", "", page.url).split("/")[0]
                    log("域名 = %s（探测时落在 %s）" % (host, page.url))
                    break
                except Exception as e:
                    print("  打不开 %s：%s" % (u, str(e)[:90]))

            if not host:
                log("✗ 一个都打不开 —— 检查网络/代理")
                return 1

            # ② 把 cookie 挂到那个域上
            if cookies:
                ck = [{"name": k, "value": v, "domain": host, "path": "/"}
                      for k, v in cookies.items() if v]
                try:
                    ctx.add_cookies(ck)
                    log("  已注入 %d 条 cookie 到 %s" % (len(ck), host))
                except Exception as e:
                    print("  注入失败：%s" % str(e)[:120])

            # ③ 重新构造正确的收藏夹地址（关键：不复用探测时跳到的 /login）
            base = "https://%s/user/%s/favorite/albums" % (host, user)

            for pg in range(1, arg.max_pages + 1):
                url = base if pg == 1 else "%s?page=%d" % (base, pg)
                try:
                    page, rows, host, waited, total, err = scrape(page, url, ctx=ctx)
                except Exception as e:
                    print("  第 %d 页导航异常：%s（跳过本页）" % (pg, str(e)[:100]))
                    break
                if err:
                    print("  第 %d 页出错：%s" % (pg, err))
                    break
                if pg == 1:
                    log("  Cloudflare 等待 %.1fs，最终域名 %s" % (waited, host))
                    info["host"] = host
                    if total:
                        info["site_total"] = total
                        log("  站点标注总数 = %s" % total)
                    if not rows:
                        title = ""
                        try:
                            title = page.title()
                        except Exception:
                            pass
                        print("  ⚠️ 第 1 页没解析到本子。页面标题 = %r" % title[:60])
                        print("     若标题仍是 Cloudflare/登录页 —— 请在弹出的窗口里登录或过一下验证")
                        try:
                            print("     正文片段：", (page.inner_text("body") or "")[:300].replace("\n", " "))
                        except Exception:
                            pass
                new = [r for r in rows if r["id"] not in seen]
                for r in new:
                    seen.add(r["id"])
                    all_rows.append(r)
                sys.stderr.write("  第 %d 页：+%d（累计 %d）\n" % (pg, len(new), len(all_rows)))
                if not new or len(rows) < 20:
                    break
        finally:
            ctx.close()

    if arg.json:
        print(json.dumps({"username": user, **info, "count": len(all_rows),
                          "albums": all_rows}, ensure_ascii=False, indent=2))
    elif arg.ids_only:
        for r in all_rows:
            print(r["id"])
    else:
        print("\n%s 的收藏夹：%d 本" % (user, len(all_rows)))
        print("-" * 72)
        for i, r in enumerate(all_rows, 1):
            print("%4d. %-9s %s" % (i, r["id"], r["title"][:56]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
