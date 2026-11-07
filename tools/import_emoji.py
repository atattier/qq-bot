"""把 Phoebe Hub（菲比表情包站）的表情包批量下载到 bot 本地图库。

为什么需要它：bot 发图只能发**本地文件**（OneBot 的 image 段收本地绝对路径，
NapCat 自己上传），不能直接把网页 URL 丢给 QQ。所以表情库必须落盘。

数据源（两个站是同一个项目，GitHub Pages 那个是旧的数据托管页，自己都写了
"请跳转到新网站观看更佳"）：
  · 新站      https://phoebehub.top
  · 旧站/数据  https://kato-shoko705.github.io/Phoebe-Hub/#recommended
  · 仓库      Kato-Shoko705/Phoebe-Hub  → data/memes.json + images/
索引结构：{"memes":[{"id","title","url":"images/xxx.webp","category":[...],"isGif":bool}]}

⚠️ 国内直连 raw.githubusercontent 会被 RST（WinError 10054），实测：
   cdn.jsdelivr.net  ✗ 也被 RST
   fastly.jsdelivr.net  JSON 能下、图片被 RST
   **gcore.jsdelivr.net  ✅ 都行**  ← 默认走这个

用法：
  python tools/import_emoji.py                 # 全量（1178 个，8 线程，几分钟）
  python tools/import_emoji.py --limit 200     # 先下一批试试
  python tools/import_emoji.py --gif-only      # 只要动态图
  python tools/import_emoji.py --static-only   # 只要静态图
  python tools/import_emoji.py --dry-run       # 只看清单不下载
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "images" / "emoji"

CDNS = [
    "https://gcore.jsdelivr.net/gh/Kato-Shoko705/Phoebe-Hub@main/",
    "https://fastly.jsdelivr.net/gh/Kato-Shoko705/Phoebe-Hub@main/",
    "https://raw.githubusercontent.com/Kato-Shoko705/Phoebe-Hub/main/",
]
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url, timeout=40):
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        "Referer": "https://phoebehub.top/",
    })
    return _OP.open(req, timeout=timeout).read()


def _get_any(rel):
    """按 CDN 顺序试，返回 (bytes, 用到的 CDN)。"""
    last = None
    for cdn in CDNS:
        try:
            return _get(cdn + urllib.parse.quote(rel)), cdn
        except Exception as e:
            last = e
            continue
    raise RuntimeError(str(last)[:120])


def fetch_index():
    raw, cdn = _get_any("data/memes.json")
    data = json.loads(raw.decode("utf-8"))
    memes = data.get("memes") or []
    if not memes:
        raise RuntimeError("memes.json 里没有 memes 数组")
    print("索引来自 %s，共 %d 个表情" % (cdn.split("/")[2], len(memes)))
    return memes


def _safe_name(meme):
    """文件名：用 URL 里的原名（就是标题），去掉 Windows 非法字符。"""
    base = os.path.basename(urllib.parse.unquote(meme.get("url") or ""))
    stem, ext = os.path.splitext(base)
    for ch in '\\/:*?"<>|':
        stem = stem.replace(ch, "_")
    stem = stem.strip().strip(".") or ("meme_%s" % meme.get("id"))
    if len(stem) > 80:
        stem = stem[:80]
    return stem + (ext or ".webp").lower()


def download_one(meme):
    name = _safe_name(meme)
    dst = DEST / name
    if dst.is_file() and dst.stat().st_size > 512:
        return ("skip", name, dst.stat().st_size)
    try:
        blob, cdn = _get_any(meme["url"])
    except Exception as e:
        return ("fail", name, str(e)[:80])
    if len(blob) < 512:
        return ("fail", name, "too small")
    # 同名不同图（极少数标题重复）→ 加 id 前缀，别互相覆盖
    if dst.is_file():
        dst = DEST / ("%s_%s" % (meme.get("id"), name))
    dst.write_bytes(blob)
    return ("ok", dst.name, len(blob))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="最多下几个（0=全部）")
    ap.add_argument("--gif-only", action="store_true")
    ap.add_argument("--static-only", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    memes = fetch_index()
    if args.gif_only:
        memes = [m for m in memes if m.get("isGif")]
    if args.static_only:
        memes = [m for m in memes if not m.get("isGif")]
    if args.limit:
        memes = memes[:args.limit]

    if args.dry_run:
        for m in memes[:20]:
            print("  ", _safe_name(m), "|", m.get("title"), "|", ",".join(m.get("category") or []))
        print("… 共 %d 个" % len(memes))
        return 0

    DEST.mkdir(parents=True, exist_ok=True)
    print("目标目录：%s" % DEST)
    print("开始下载 %d 个（%d 线程）…" % (len(memes), args.workers))

    ok = skip = fail = 0
    fails = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(download_one, m): m for m in memes}
        for i, f in enumerate(as_completed(futs), 1):
            kind, name, info = f.result()
            if kind == "ok":
                ok += 1
            elif kind == "skip":
                skip += 1
            else:
                fail += 1
                fails.append((name, info))
            if i % 25 == 0 or i == len(memes):
                print("  [%d/%d] 新下 %d / 已有 %d / 失败 %d  (%.0fs)" % (
                    i, len(memes), ok, skip, fail, time.time() - t0), flush=True)

    # 清单：标题 → 文件名，给「发图 找 xx」之类的检索用
    manifest = {}
    for m in memes:
        n = _safe_name(m)
        if not (DEST / n).is_file() and (DEST / ("%s_%s" % (m.get("id"), n))).is_file():
            n = "%s_%s" % (m.get("id"), n)
        manifest[str(m.get("title") or n)] = n
    (DEST / "_manifest.json").write_text(
        json.dumps({"source": "https://phoebehub.top/",
                    "count": len(manifest), "files": manifest},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    total = sum(p.stat().st_size for p in DEST.iterdir() if p.is_file() and p.suffix != ".json")
    print("\n完成：新下 %d，已有 %d，失败 %d，共 %.1f MB，用时 %.0fs" % (
        ok, skip, fail, total / 2 ** 20, time.time() - t0))
    if fails:
        print("失败的（前 10）：")
        for n, e in fails[:10]:
            print("   ", n, "|", e)
    print("清单：%s" % (DEST / "_manifest.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
