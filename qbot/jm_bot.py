# -*- coding: utf-8 -*-
"""JM 漫画下载：群里/私聊发 `.jm <本子ID>` -> 下载 -> 合成 PDF（多章则打包 ZIP）-> 发回会话。

来源与差异（**别拿原版代码往这儿套**）
--------------------------------------------------------------------------
移植自 GitHub `Doya16/Nonebot-Napcat-JMcomic-QQBot`（作者声明仅供学习交流）。
原版是 **NoneBot2 插件**，本 bot 是自研 bot.py 直连 NapCat，两套框架不兼容，
所以这里只搬了「下载 / 合 PDF / 打包」三件事的思路，代码全部重写。
2026-10-09 实测发现的**原版已失效之处**（照抄必挂）：

  · `client.album(aid)` 在 jmcomic 2.7.8 上**已不存在** -> 现为 `get_album_detail()`
  · `album.photo_count` / `album.chapter_list` 也没了 -> 现为 `page_count` / `episode_list`
  · `move_album_dirs_by_photo_titles` 靠 `photo.title` 猜目录名 -> 这里改成**扫描真实产物**
  · `images_to_pdf` 一次性把全部图 `Image.open()` 进 list，390 页本子在 15.7G 内存的
    机器上很难看 -> 这里改成**生成器逐张编码 + 长边缩放**（PdfImagePlugin 是逐帧写的，
    传生成器不会把 N 张图同时留在内存）

站点
--------------------------------------------------------------------------
jmcomic 会自动联网拉取当前可用域名（2026-10-09 拉到 `www.cdnhjk.net` 等），
内置的 `18comic.vip` 早已失效（403）。**不要在别处硬编码域名**，交给库自己更新。
本机**直连可通，不需要代理**。

安全
--------------------------------------------------------------------------
主人 2026-10-09 明确要求「群里用 + 私聊」，故两条路都开。群里上传后按
`recall_seconds` 自动删群文件 —— 注意这只缩小暴露窗口，**不改变文件已经过
腾讯服务器**这个事实。配置见 config.json 的 `jm` 段。

用法
--------------------------------------------------------------------------
    from qbot import jm_bot
    kind, aid = jm_bot.parse_cmd(".jm 472537")   # ('jm', '472537') / (None, None)
    info = jm_bot.fetch_info(aid)                # dict: title / author / page_count
    path, out_kind = jm_bot.build(aid, workdir, cfg)   # ('...\\472537.pdf', 'pdf')
"""

import json
import logging
import os
import random
import re
import shutil
import time
import zipfile

log = logging.getLogger("qqbot")

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

try:
    import jmcomic
    from jmcomic import JmOption
except Exception:  # pragma: no cover
    jmcomic = None
    JmOption = None

# jmcomic 自己的 INFO 日志很吵（每次都在打域名/请求），压到 WARNING，
# 免得把 bot.log 刷得看不见自己的行。
logging.getLogger("jmcomic").setLevel(logging.WARNING)

# 本子 ID：JM 的 ID 是纯数字，短的 4 位（老本）、长的 8 位，留点余量取 4~9 位。
# 刻意要求「.jm」和数字之间只有空白或常见分隔符，免得把正文里的 "jm" 误吞。
_JM_RE = re.compile(r"^[.。]?\s*jm\s*[:：,，\-]?\s*(\d{4,9})\s*$", re.I)
_ZIP_RE = re.compile(r"^[.。]?\s*jmzip\s*[:：,，\-]?\s*(\d{4,9})\s*$", re.I)

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")

# Windows 文件名非法字符
_BAD_NAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def available():
    """依赖是否就绪。缺哪个都直接给出可执行的补救命令。"""
    if jmcomic is None:
        return False, "缺 jmcomic，管理员在 bot 的 Python 里跑 `pip install jmcomic`"
    if Image is None:
        return False, "缺 Pillow，管理员在 bot 的 Python 里跑 `pip install pillow`"
    return True, ""


def parse_cmd(text):
    """识别口令 -> (kind, album_id)。kind ∈ {'jm','zip'}，认不出返回 (None, None)。

    认这些写法：`.jm 472537` / `jm 472537` / `JM：472537` / `.jmzip 472537`
    """
    t = (text or "").strip()
    if not t:
        return None, None
    m = _ZIP_RE.match(t)
    if m:
        return "zip", m.group(1)
    m = _JM_RE.match(t)
    if m:
        return "jm", m.group(1)
    return None, None


# ---------------- 随机一本（2026-10-09 加）：`.jm随机`（收藏）/ `.jm热`（同人月榜） ----------------
# ⚠️ 数据源两条路，别搞混：
#   · 收藏 = `tools/_jm_fav_browser.py` 用真浏览器抓的缓存 `state/jm_favorites.json`
#     （网页域名挂 Cloudflare，Python 爬不动 ⇒ 只能靠浏览器；缓存没有就提示去抓）
#   · 月榜 = jmcomic **API 客户端**（`impl="api"`），走 `www.cdnhjk.net` 这类 API 域名，
#     **直连可通、不被 CF 拦**（2026-10-09 实测 total=778/页 80 条）—— 绝别用 html 客户端
_RANDOM_RE = re.compile(r"^[.。]?\s*jm\s*(随机|收藏|fav|热|热门|榜|hot|重置|洗牌|reset)\s*$", re.I)
_RANDOM_KIND = {"随机": "fav", "收藏": "fav", "fav": "fav",
                "热": "rank", "热门": "rank", "榜": "rank", "hot": "rank",
                "重置": "reset", "洗牌": "reset", "reset": "reset"}

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAV_JSON = os.path.join(_ROOT, "state", "jm_favorites.json")
SENT_JSON = os.path.join(_ROOT, "state", "jm_sent.json")   # 已发过的本子（去重用）

RANK_CATEGORY = "doujin"   # 同人（主人指定的分类）
RANK_TIME = "m"            # 本月
RANK_ORDER = "mv"          # 最多点阅
RANK_LIMIT = 30            # 只在「最多点开的前 30」里随机
RANK_TTL = 600             # 榜单缓存 10 分钟，别每条口令都去打站方
_rank_cache = {"t": 0.0, "rows": []}


def parse_random_cmd(text):
    """认 `.jm随机` / `.jm热` 这类无 ID 口令。返回 'fav' | 'rank' | None。"""
    m = _RANDOM_RE.match((text or "").strip())
    if not m:
        return None
    return _RANDOM_KIND.get(m.group(1).lower())


def fav_rows():
    """收藏夹缓存 -> [(id, title)]。文件不存在返回 None（= 还没抓过，区别于空）。"""
    if not os.path.exists(FAV_JSON):
        return None
    try:
        with open(FAV_JSON, encoding="utf-8") as f:
            d = json.load(f)
        rows = []
        for a in (d.get("albums") or []):
            aid = str(a.get("id") or "").strip()
            if aid:
                rows.append((aid, str(a.get("title") or "")[:60]))
        return rows
    except Exception as e:
        log.warning("jm: 读收藏缓存失败：%s", e)
        return None


def rank_rows(limit=RANK_LIMIT, category=RANK_CATEGORY):
    """「同人 / 本月 / 最多点阅」榜单前 limit 本 -> [(id, title)]。

    用 `month_ranking(page, category)` = categories_filter(TIME_MONTH, ORDER_BY_VIEW)。
    返回的 content 元素是 `(album_id, {'id','name','author',...})` 二元组（跟收藏夹同形）。
    """
    from jmcomic import JmMagicConstants as MC
    now = time.time()
    if _rank_cache["rows"] and now - _rank_cache["t"] < RANK_TTL:
        return _rank_cache["rows"][:limit]
    cl = JmOption.default().new_jm_client(impl="api")   # ⭐ api，不是 html
    p = cl.month_ranking(1, MC.CATEGORY_DOUJIN if category == "doujin" else category)
    rows = []
    for item in (getattr(p, "content", None) or []):
        try:
            aid, meta = item[0], item[1]
            title = meta.get("name", "") if isinstance(meta, dict) else str(meta)
        except Exception:
            continue
        if str(aid or "").strip():
            rows.append((str(aid), str(title or "")[:60]))
    if not rows:
        raise RuntimeError("榜单拉回来是空的（站点域名可能又换了，稍后再试）")
    _rank_cache["t"] = now
    _rank_cache["rows"] = rows
    log.info("jm: 月榜刷新 %d 本（分类=%s）", len(rows), category)
    return rows[:limit]


# ---------------- 已发记录（主人 2026-10-10 定：随机/推荐不要重复发同一本） ----------------
# 落 `state/jm_sent.json`（⚠️ 跟收藏夹缓存一样放 state，**别进 config.json**——
# `save_config()` 会无条件整体 dump 内存 CFG，把新段冲掉）。结构：
#   {"fav": {"<id>": {"t": 时间戳, "title": "..."}}, "rank": {...}}
# 按来源分开记：收藏夹和月榜是两个池，各算各的「还剩几本没发过」。

def _sent_load():
    if not os.path.exists(SENT_JSON):
        return {}
    try:
        with open(SENT_JSON, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        log.warning("jm: 读已发记录失败（当空处理）：%s", e)
        return {}


def _sent_save(d):
    try:
        tmp = SENT_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SENT_JSON)
    except Exception as e:
        log.warning("jm: 写已发记录失败：%s", e)


def sent_ids(source):
    """这个来源已经发过的 ID 集合。"""
    return set((_sent_load().get(source) or {}).keys())


def sent_count(source=None):
    d = _sent_load()
    if source:
        return len(d.get(source) or {})
    return sum(len(v or {}) for v in d.values())


def mark_sent(source, aid, title=""):
    """入队成功就记一笔（跟冷却/配额同时机：没发成就不算发过）。"""
    if not aid:
        return
    d = _sent_load()
    d.setdefault(source, {})[str(aid)] = {"t": int(time.time()),
                                          "title": str(title or "")[:60]}
    _sent_save(d)


def reset_sent(source=None):
    """洗牌：清掉某个来源（默认全清）的已发记录。"""
    if source is None:
        _sent_save({})
    else:
        d = _sent_load()
        d.pop(source, None)
        _sent_save(d)


def pick_random(source):
    """随机挑一本（**跳过已发过的**）。返回 (aid, title, 来源说明)。

    ⚠️ 候选全发过 ⇒ 自动洗牌重开（并在说明里告知），不会返回空。
    """
    if source == "fav":
        rows = fav_rows()
        if rows is None:
            raise RuntimeError(
                "收藏夹还没抓过：在电脑上跑一次 `python tools/_jm_fav_browser.py`"
                "（登录态会存下来，之后一直能用）")
        if not rows:
            raise RuntimeError("收藏夹缓存是空的")
        label = "收藏夹"
    else:
        rows = rank_rows()
        label = "同人·本月·最多点阅 前%d" % len(rows)

    sent = sent_ids(source)
    fresh = [r for r in rows if r[0] not in sent]
    shuffled = ""
    if not fresh:                      # 全发过一遍了 → 洗牌，重新有得挑
        reset_sent(source)
        fresh = list(rows)
        shuffled = "（都发过了，重新洗牌）"
    aid, title = random.choice(fresh)
    left = len(fresh) - 1              # 这一本待会儿也会记进已发
    note = "%s，还剩 %d 本没发过%s" % (label, left, shuffled)
    return aid, title, note


# ---------------------------------------------------------------- jmcomic 封装

def _option(cfg, workdir=None):
    """建 JmOption。⭐ 每次现建，别缓存 —— 上面要按请求挂不同的 base_dir。"""
    opt = JmOption.default()
    if workdir:
        opt.dir_rule.base_dir = str(workdir)
    # 限制图片下载并发：站方不喜欢被打 + 本机还要跑 ComfyUI，4 条够了。
    try:
        opt.download.threading.image = int((cfg or {}).get("threads") or 4)
    except Exception as e:
        log.warning("jm: 设下载线程数失败（不影响主流程）：%s", e)
    return opt


def fetch_info(aid, cfg=None):
    """只取元信息，不下载。用于「先回一条这是啥」再决定要不要下。"""
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    a = _option(cfg).build_jm_client().get_album_detail(str(aid))
    return {
        "id": str(a.album_id),
        "title": (getattr(a, "title", "") or "").strip(),
        "author": (getattr(a, "author", "") or "").strip(),
        "page_count": int(getattr(a, "page_count", 0) or 0),
        "episodes": len(getattr(a, "episode_list", None) or []),
    }


def download(aid, workdir, cfg=None):
    """下载整本到 <workdir>/<专辑名>/...。返回 album 详情对象（拿标题用）。"""
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    opt = _option(cfg, workdir)
    res = opt.download_album(str(aid))
    detail = getattr(res, "detail", None)
    if detail is None:
        # 老/新版本差异兜底：download_album 有时直接返回 album
        detail = res if hasattr(res, "title") else None
    return detail


# ---------------------------------------------------------------- 图片 -> PDF

def _safe_name(name, fallback="x"):
    s = _BAD_NAME.sub("_", str(name or "")).strip().strip(".")
    return (s or fallback)[:80]


def _iter_images(d):
    import os as _os
    return [f for f in sorted(_os.listdir(d)) if f.lower().endswith(IMG_EXT)]


def _has_images(d):
    try:
        return bool(_iter_images(d))
    except Exception:
        return False


def _chapter_dirs(d):
    """返回 [(名字, 路径)] —— d 下**含图片**的子目录，按名字排序（章序）。"""
    out = []
    try:
        for x in sorted(os.listdir(d)):
            p = os.path.join(d, x)
            if os.path.isdir(p) and _has_images(p):
                out.append((x, p))
    except Exception:
        pass
    return out


def images_to_pdf(image_dir, pdf_path, max_edge=1400, quality=82):
    """目录里的图 -> 单 PDF。

    ⚠️ 这里和原版最不一样的地方：用**生成器**喂给 PIL，而不是先 open 一堆塞 list。
    390 页 × 1080×1500 的图全塞 list 要 1GB 级内存，本机扛不住。
    （PIL 的 PdfImagePlugin `_save_all` 是逐帧 save 到同一个 fp 的，所以生成器安全。）

    max_edge 控制长边像素、quality 控制 JPEG 质量：QQ 群文件有 100MB 上限，
    原图直接塞进去一本 300+ 页的本子会超。默认参数下一本 390 页大约 30~50MB。
    """
    files = _iter_images(image_dir)
    if not files:
        return 0

    def gen():
        for name in files:
            try:
                im = Image.open(os.path.join(image_dir, name))
                im = im.convert("RGB")
                if max_edge:
                    w, h = im.size
                    long_edge = max(w, h)
                    if long_edge > max_edge:
                        k = max_edge / float(long_edge)
                        im = im.resize((max(1, int(w * k)), max(1, int(h * k))),
                                       Image.LANCZOS)
                yield im
            except Exception as e:
                log.warning("jm: 跳过坏图 %s：%s", name, e)

    try:
        it = gen()
        first = next(it)
    except StopIteration:
        return 0
    except Exception as e:
        log.warning("jm: 合 PDF 失败（首图就打不开）：%s", e)
        return 0

    try:
        first.save(pdf_path, "PDF", save_all=True, append_images=it,
                   quality=quality, optimize=True)
    except Exception as e:
        # 生成器方案万一在某个 PIL 版本上不被接受，退回一次性列表（慢但保底）
        log.warning("jm: 生成器合成失败，退回列表模式：%s", e)
        imgs = []
        for name in files:
            try:
                im = Image.open(os.path.join(image_dir, name)).convert("RGB")
                if max_edge:
                    w, h = im.size
                    k = min(1.0, max_edge / float(max(w, h)))
                    if k < 1.0:
                        im = im.resize((max(1, int(w * k)), max(1, int(h * k))),
                                       Image.LANCZOS)
                imgs.append(im)
            except Exception:
                continue
        if not imgs:
            return 0
        imgs[0].save(pdf_path, "PDF", save_all=True, append_images=imgs[1:],
                     quality=quality, optimize=True)
    return len(files)


def zip_files(paths, zip_path):
    """把若干 PDF 打成 zip（章节名做 arcname）。"""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            if os.path.exists(p) and os.path.getsize(p) > 0:
                z.write(p, arcname=os.path.basename(p))


def _album_root(workdir):
    """下载产物落点：<workdir>/<专辑名>/。jmcomic 默认 dir_rule 就是这样。"""
    subs = []
    try:
        for x in sorted(os.listdir(workdir)):
            p = os.path.join(workdir, x)
            if os.path.isdir(p):
                subs.append(p)
    except Exception:
        pass
    if len(subs) == 1:
        return subs[0]
    # 不止一层（或没有）：退化成用 workdir 自己
    return workdir if subs else workdir


def build(aid, workdir, cfg=None, progress=None):
    """下载 + 合成，返回 (文件绝对路径, 'pdf'|'zip')。失败抛异常。

    progress: 可选回调 progress(msg) —— 目前只在关键节点报一句，够用。
    """
    cfg = cfg or {}
    if os.path.isdir(workdir):
        shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    if progress:
        progress("开始下载 %s…" % aid)
    download(aid, workdir, cfg)
    return pack(_album_root(workdir), aid, cfg, progress)


def pack(root, aid, cfg=None, progress=None):
    """把**已经下载好**的目录合成 -> (文件绝对路径, 'pdf'|'zip')。

    从 build() 里抽出来是为了能离线测：造几个目录就能验多章打包，
    不用真去下一本 1300 页的（省时间也省站方流量）。
    """
    cfg = cfg or {}
    max_edge = int(cfg.get("pdf_max_edge") or 1400)
    quality = int(cfg.get("pdf_quality") or 82)
    max_images = int(cfg.get("max_images") or 800)

    # 先算总图数，超上限就别合了（合出来也发不出去，白烧时间）
    total = 0
    chapters = _chapter_dirs(root)
    if chapters:
        for _n, p in chapters:
            total += len(_iter_images(p))
    else:
        total = len(_iter_images(root))
    if total == 0:
        raise RuntimeError("下载目录里没找到任何图片")
    if total > max_images:
        raise RuntimeError("这本有 %d 张图，超过上限 %d —— 太大了，先别下"
                           "（管理员可调 config.json 的 jm.max_images）" % (total, max_images))

    if progress:
        progress("下好了 %d 张，正在合成…" % total)

    if chapters:
        # 多章：每章一个 PDF -> 打包 zip
        pdfs = []
        for name, p in chapters:
            pdf = os.path.join(root, "%s.pdf" % _safe_name(name, "chapter"))
            n = images_to_pdf(p, pdf, max_edge, quality)
            if n and os.path.getsize(pdf) > 0:
                pdfs.append(pdf)
        if not pdfs:
            raise RuntimeError("章节 PDF 一个都没合出来")
        zip_path = os.path.join(root, "%s.zip" % aid)
        zip_files(pdfs, zip_path)
        if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
            raise RuntimeError("打包 zip 失败")
        return zip_path, "zip"

    pdf_path = os.path.join(root, "%s.pdf" % aid)
    n = images_to_pdf(root, pdf_path, max_edge, quality)
    if not n or not os.path.exists(pdf_path) or os.path.getsize(pdf_path) == 0:
        raise RuntimeError("合成 PDF 失败")
    return pdf_path, "pdf"


def cleanup(workdir):
    """删掉整个工作目录。下载的原图很大，发完必须清。"""
    try:
        if workdir and os.path.isdir(workdir):
            shutil.rmtree(workdir, ignore_errors=True)
            return True
    except Exception as e:
        log.warning("jm: 清理失败 %s：%s", workdir, e)
    return False


def prune_old(base_dir, keep_days=1):
    """清掉 base_dir 下超过 keep_days 天的陈旧工作目录（上次崩了没清干净的）。"""
    import time
    if not base_dir or not os.path.isdir(base_dir):
        return 0
    n = 0
    cutoff = time.time() - keep_days * 86400
    try:
        for x in os.listdir(base_dir):
            p = os.path.join(base_dir, x)
            try:
                if os.path.getmtime(p) < cutoff:
                    shutil.rmtree(p, ignore_errors=True)
                    n += 1
            except Exception:
                continue
    except Exception:
        pass
    return n
