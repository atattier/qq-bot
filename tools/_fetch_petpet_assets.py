# -*- coding: utf-8 -*-
"""下载 nonebot-plugin-petpet 官方模板素材到 skills/petpet/assets/（2026-10-23）。

为什么要单独一个脚本：petpet 的 Python 代码在 PyPI 包里（40KB），但**模板图不在包里** ——
上游是运行时从 GitHub raw 拉的。本机直连 raw.githubusercontent.com 会 SSL EOF，
所以走镜像（ghproxy.net / gh-proxy.com 都通），并按 resource_list.json 里的 md5 校验。

用法：
    python tools/_fetch_petpet_assets.py                # 下默认那批模板
    python tools/_fetch_petpet_assets.py petpet pat     # 只下指定模板
    python tools/_fetch_petpet_assets.py --all          # 下全部 99 个模板（约 1091 个文件）

素材版权归上游项目（MIT）与原作者，这里只是本地缓存。
"""
import hashlib
import threading
import json
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ASSETS = BASE / "skills" / "petpet" / "assets"

RAW = ("https://raw.githubusercontent.com/noneplugin/nonebot-plugin-petpet/"
       "v0.3.x/resources/")
MIRRORS = [
    "https://ghproxy.net/" + RAW,
    "https://gh-proxy.com/" + RAW,
    "https://gcore.jsdelivr.net/gh/noneplugin/nonebot-plugin-petpet@v0.3.x/resources/",
]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 默认这批：单头像、帧数少、都是群里真用得上的老梗
DEFAULT = ["petpet", "pat", "rub", "bite", "knock", "punch", "pound", "thump",
           "roll", "twist", "throw_gif", "capoo_rub", "support"]


def _get(path, tries=3):
    last = None
    for m in MIRRORS:
        for _ in range(tries):
            try:
                req = urllib.request.Request(m + path, headers=UA)
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.read()
            except Exception as e:  # noqa: BLE001
                last = e
    raise RuntimeError("拉不到 %s：%s" % (path, last))


def main(argv):
    want_all = "--all" in argv
    picks = [a for a in argv if not a.startswith("-")] or ([] if want_all else DEFAULT)
    print("拉资源清单…")
    listing = json.loads(_get("resource_list.json").decode("utf-8"))
    todo = []
    for it in listing:
        p = it["path"]
        if not p.startswith("images/"):
            continue
        tpl = p.split("/")[1]
        if want_all or tpl in picks:
            todo.append(it)
    print("清单 %d 个文件，本次要下 %d 个（模板 %s）"
          % (len(listing), len(todo), "全部" if want_all else "/".join(picks)))

    # 并发下：单线程实测 20~30 秒/文件（镜像限速），97 个文件要半小时。
    # 8 路并发能压到 3~4 分钟，镜像那边也不会翻脸。
    from concurrent.futures import ThreadPoolExecutor
    stats = {"ok": 0, "skip": 0, "bad": 0}
    lock = threading.Lock()

    def _one(it):
        rel = it["path"][len("images/"):]          # 例：petpet/0.png
        dst = ASSETS / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and hashlib.md5(dst.read_bytes()).hexdigest() == it["hash"]:
            with lock:
                stats["skip"] += 1
            return
        try:
            body = _get("images/" + rel)
        except Exception as e:  # noqa: BLE001
            print("  ✗ %s %s" % (rel, e))
            with lock:
                stats["bad"] += 1
            return
        if hashlib.md5(body).hexdigest() != it["hash"]:
            print("  ✗ %s md5 不对（可能被镜像插了错误页）" % rel)
            with lock:
                stats["bad"] += 1
            return
        dst.write_bytes(body)
        with lock:
            stats["ok"] += 1
            if stats["ok"] % 20 == 0:
                print("  …已下 %d" % stats["ok"])

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_one, todo))
    print("完成：新下 %d，已存在 %d，失败 %d → %s"
          % (stats["ok"], stats["skip"], stats["bad"], ASSETS))
    return 0 if stats["bad"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
