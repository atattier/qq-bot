# -*- coding: utf-8 -*-
"""群/私聊里发 B站 / 抖音链接 -> 下载无水印视频 -> 发回会话。

2026-09-17 加。主人原话：
    「加一个解析群里发送的B站或者抖音链接，然后发送无水印的视频文件到群里的功能，
      这个github上应该有方法，你照抄就行。」

照抄的是开源社区的标准做法（yt-dlp + ffmpeg），不是某一家来路不明的"去水印网站"：

  ┌─ 引擎 ────────────────────────────────────────────────────────────┐
  │ yt-dlp（GitHub 100k+ star，youtube-dl 的活跃分支）                  │
  │   · 原生支持 B站（BiliBiliIE）+ 抖音（DouyinIE）                    │
  │   · 纯 pip 包，不需要外部 exe                                       │
  │ ffmpeg：用 imageio-ffmpeg 自带的二进制（也是 pip 装）                │
  │   · B站 480P 以上都是 DASH **音视频分离**流，必须 ffmpeg 合并        │
  └──────────────────────────────────────────────────────────────────┘

两条链路完全不同，别互相套（2026-09-17 真机踩过一轮，血泪）：

  ┌─ B站：交给 yt-dlp ────────────────────────────────────────────────┐
  │ B站源流本身没有烧进画面的水印（播放器上的 logo 是前端叠的），        │
  │ yt-dlp 下来的就是干净的。唯一要注意的是**选流**（见 pick_stream）。 │
  └──────────────────────────────────────────────────────────────────┘

  ┌─ 抖音：yt-dlp **走不通**，走官方分享页（本文件 _dy_* 那一块）──────┐
  │ ⚠️ yt-dlp 的 DouyinIE 现在必报                                      │
  │    `Fresh cookies (not necessarily logged in) are needed`，         │
  │    而纯请求 www.douyin.com 一定被反爬挑战页拦 → 返回 72914 字节的、   │
  │    无 title 无可见文本的页面（含 `__ac_nonce`，要跑 JS 才解）。       │
  │    匿名 ttwid（`ttwid.bytedance.com/ttwid/union/register/`）能拿到，  │
  │    但**光有 ttwid 还不够**过挑战页。                                  │
  │ ✅ 真正稳的路（社区标准做法，实测 2026-09-17 通）：                   │
  │    ① 短链跟随重定向 → 拿到 aweme_id；                                │
  │    ② 请求 `www.iesdouyin.com/share/video/<id>/`（**移动 UA** + ttwid）│
  │       —— 这个分享页**不设挑战**，HTML 里直接带 `window._ROUTER_DATA`  │
  │       的完整 JSON（含标题/时长/宽高/play_addr）；                    │
  │    ③ 取 `video.play_addr.uri`，拼                                      │
  │       `https://aweme.snssdk.com/aweme/v1/play/?video_id=<uri>&ratio=`  │
  │       **`play` 就是无水印那套地址**（官方另有 `playwm` = 带水印），    │
  │       **已抽帧肉眼验过，画面零水印**；                                 │
  │    ④ `ratio=` 决定清晰度。⚠️ **上限就是 1080p** —— 实测 1440p/2160p/  │
  │       4k 全都返回与 1080p 完全相同的字节数（被服务端 cap 住），       │
  │       所以别费劲要 4K，1080p 就是能拿到的最高。                       │
  │    ⑤ 抖音的 mp4 是**音视频合一**的（不是 DASH 分离流），下完不用合并， │
  │       但**必须带 Referer/UA**，否则 douyinvod CDN 会 403。            │
  └──────────────────────────────────────────────────────────────────┘

用法：
    from qbot import videoparse
    site, url = videoparse.find_link("看这个 https://www.bilibili.com/video/BV1xx")
    res = videoparse.fetch(url, cfg)       # dict: ok / path / title / err
"""

import json
import os
import re
import time
import glob
import shutil
import logging
import urllib.parse
import urllib.request

log = logging.getLogger("qqbot")

# ---------------------------------------------------------------- 链接识别

# ⚠️ 协议前缀 `https?://` 是**可选**的：QQ 里手打的链接常常是 "www.douyin.com/xxx"，
# 没协议头。find_link 会在返回时补回 https://（见 _norm）。
# B站：长链 / 短链 b23.tv / 老 av 号 / 番剧 ep 也认
_BILI_RE = re.compile(
    r"(?:https?://)?(?:"
    r"(?:www\.|m\.)?bilibili\.com/video/(?:[Bb][Vv][0-9A-Za-z]+|av\d+)"
    r"|(?:www\.|m\.)?bilibili\.com/bangumi/play/(?:ep|ss)\d+"
    r"|(?:www\.|m\.)?bilibili\.com/list/[0-9]+\?[^\s]*?bvid=[0-9A-Za-z]+"
    # ⚠️ 短链 id **带 `-` 和 `_`**（如 v.douyin.com/ygw_CFPB-cE/），只写 [0-9A-Za-z] 会把
    #    链接截成 "ygw" —— 2026-09-17 真机踩到，probe 直接报"认不出这是哪个视频"。
    r"|b23\.tv/[0-9A-Za-z\-_]+/?"
    r"|b23\.wtf/[0-9A-Za-z\-_]+/?"
    r")", re.I)

# 抖音：分享短链 / 长链 / 老 iesdouyin 分享页 / 图文 note
_DOUYIN_RE = re.compile(
    r"(?:https?://)?(?:"
    r"v\.douyin\.com/[0-9A-Za-z\-_]+/?"      # 短链 id 含 - / _，见上面 B站那条注释
    r"|(?:www\.)?douyin\.com/video/\d+"
    r"|(?:www\.)?douyin\.com/note/\d+"
    r"|(?:www\.)?douyin\.com/slide/slide\?[^\s]*?modal_id=\d+"
    r"|(?:www\.)?iesdouyin\.com/share/video/\d+"
    r"|(?:www\.)?iesdouyin\.com/(?:web/)?(?:share/)?(?:aweme|video)/\d+"
    r")", re.I)


def _norm(u):
    """补回协议头，并去掉尾巴上的中文标点（群里复制常带个句号/逗号）。"""
    u = (u or "").strip().rstrip("。，、；！？)）】」』>\"'")
    if not u:
        return u
    if not u.lower().startswith(("http://", "https://")):
        u = "https://" + u.lstrip("/")
    return u


# 群里常见：链接被包在【】里、前后带中文、或前面有「分享」「看这个」之类的字
# 统一把整段文字里的第一条链接揪出来。
def find_link(text):
    """从一段话里找第一条 B站/抖音链接。返回 (site, url) 或 (None, None)。"""
    if not text:
        return None, None
    # QQ 常把长链显示成 "…https://xxx" 或者带转义，先做点清洗
    t = text.replace("\\/", "/").replace("&amp;", "&")
    m = _BILI_RE.search(t)
    if m:
        return "bilibili", _norm(m.group(0))
    m = _DOUYIN_RE.search(t)
    if m:
        return "douyin", _norm(m.group(0))
    return None, None


SITE_NAME = {"bilibili": "B站", "douyin": "抖音"}


# ---------------------------------------------------------------- 环境

def ffmpeg_path():
    """ffmpeg 可执行文件。优先 imageio-ffmpeg（pip 自带），其次 PATH。"""
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    for cand in ("ffmpeg", "ffmpeg.exe"):
        from shutil import which
        p = which(cand)
        if p:
            return p
    return None


def yt_dlp_available():
    try:
        import yt_dlp  # noqa
        return True
    except ImportError:
        return False


_TT_CACHE = {"v": None, "t": 0.0}

# 抖音全线只认手机 UA（2026-09-17 实测：桌面 UA 打分享页会被判成爬虫）
_UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def _ttwid():
    """抖音匿名身份 cookie（不登录也能拿）。失败返回 None。

    这是公开接口，等价"浏览器打开抖音首页时服务器下发的匿名 ttwid"，
    不是绕过登录 —— 登录才能看的内容照样拿不到。

    ⚠️ 带半小时缓存：抖音一次解析要请求好几次（分享页 + 逐档探体积 + 下载），
       每次都去注册一个新 ttwid 又慢又没必要。
    """
    now = time.time()
    if _TT_CACHE["v"] and now - _TT_CACHE["t"] < 1800:
        return _TT_CACHE["v"]
    try:
        import http.cookiejar
        # ⚠️ 用**手机 UA**：分享页和 ttwid 接口都只吃移动 UA，桌面 UA 会掉到挑战页。
        ua = _UA_MOBILE
        body = json.dumps({
            "region": "cn", "aid": 1768, "needFid": False,
            "service": "www.ixigua.com",
            "migrate_info": {"ticket": "", "source": "node"},
            "cbUrlProtocol": "https", "union": True,
        }).encode()
        req = urllib.request.Request(
            "https://ttwid.bytedance.com/ttwid/union/register/", data=body,
            headers={"Content-Type": "application/json", "User-Agent": ua,
                     "Referer": "https://www.douyin.com/"})
        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        try:
            op.open(req, timeout=15).read()
        except Exception:
            pass          # 这个接口 400/302 也会带 Set-Cookie，忽略响应体
        for c in cj:
            if c.name == "ttwid":
                _TT_CACHE["v"] = c.value
                _TT_CACHE["t"] = now
                return c.value
    except Exception as e:
        log.debug("拿 ttwid 失败: %s", e)
    return None


# ---------------------------------------------------------------- 配置

DEFAULT_CFG = {
    "enabled": True,
    "dir": "state/videos",
    "max_duration_sec": 600,   # 超过这个时长直接拒（QQ 发大文件很痛苦）
    "max_size_mb": 100,        # 超过这个体积拒发并删文件
    "max_height": 2160,        # 清晰度上限（B站未登录也能拿 1080P）
    "cookie_file": "",         # 可选：浏览器导出的 cookies.txt（Netscape 格式）
    "keep_hours": 6,           # 缓存保留时长
    "cooldown_sec": 20,        # 每个会话的冷却，防刷
    "max_per_hour": 12,        # 每个会话每小时上限
    "use_browser": True,       # 抖音新路也失败时，允许起真实浏览器再试
}


def cfg_of(bot_cfg):
    """把 bot 的 CFG['video'] 和默认值合并。"""
    c = dict(DEFAULT_CFG)
    c.update(bot_cfg.get("video") or {})
    return c


def out_dir(cfg, base):
    d = cfg.get("dir") or DEFAULT_CFG["dir"]
    p = d if os.path.isabs(d) else os.path.join(str(base), d)
    os.makedirs(p, exist_ok=True)
    return p


# ---------------------------------------------------------------- 下载

def _codec_of(f):
    v = (f.get("vcodec") or "")
    if v.startswith("avc1") or v.startswith("h264"):
        return "h264"
    if v.startswith("av01"):
        return "av1"
    if v.startswith("vp") or v.startswith("vp09"):
        return "vp9"
    if v.startswith("hev") or v.startswith("h265"):
        return "h265"
    return v or "?"


def _est_mb(f, dur):
    """估这个流大概多少 MB。估不出来返回 None（后面下完还会复查真大小）。"""
    sz = f.get("filesize") or f.get("filesize_approx")
    if sz:
        return sz / 1048576.0
    rate = f.get("tbr") or f.get("vbr") or f.get("abr") or 0
    if rate and dur:
        return rate * 1000.0 * dur / 8.0 / 1048576.0
    return None


def pick_stream(formats, dur, max_mb, max_h):
    """从 format 列表里挑一组「视频 + 音频」，在体积上限内尽量给最高画质。

    ⚠️ 为什么不能直接用 `bestvideo[height<=N]`（2026-09-17 踩到，画质被坑惨）：
      yt-dlp 的 "best" 排序把 **编码效率高的 AV1/VP9 排在 H.264 前面** ——
      于是 `bestvideo[height<=720]` 在 B站会挑到 `100022`（AV1 的 **360P** 163kbps），
      而不是 H.264 的 720P（742kbps）。实测同一视频：那样下出来 360×640/2.8MB，
      换成显式挑流是 1080×1920 H.264/16.9MB —— 差了 5 倍码率，主人一眼就看出糊。

    排序键 =（H.264 优先 → 分辨率高 → 码率高）：
      · **H.264 优先是为了 QQ 能播**：AV1/HEVC 在手机 QQ 上不保证解码，发过去播不了等于白下。
      · 体积超 `max_mb` 的档直接跳过 —— 于是长视频会自动降档，短视频能吃满 1080P。
    """
    if not formats:
        return None, None
    vids, auds = [], []
    for f in formats:
        vc, ac = f.get("vcodec"), f.get("acodec")
        has_v = vc and vc != "none"
        has_a = ac and ac != "none"
        if has_v and f.get("height"):
            vids.append(f)
        if has_a and not has_v:
            auds.append(f)
    if not vids:
        return None, None
    if max_h:
        vids = [v for v in vids if (v.get("height") or 0) <= int(max_h)]
        if not vids:
            return None, None
    # 音频挑码率最高的（体积都小，不值得省）
    auds.sort(key=lambda a: (a.get("abr") or a.get("tbr") or 0), reverse=True)
    aud = auds[0] if auds else None

    def fits(v):
        e = _est_mb(v, dur)
        if e is None:
            return True          # 估不出来就先放行，下完按真实大小复查
        if aud:
            ae = _est_mb(aud, dur)
            if ae:
                e += ae
        return e <= max_mb * 1.02

    cand = [v for v in vids if fits(v)] or vids
    cand.sort(key=lambda v: (1 if _codec_of(v) == "h264" else 0,
                             v.get("height") or 0,
                             v.get("tbr") or v.get("vbr") or 0), reverse=True)
    v = cand[0]
    if aud:
        expr = "%s+%s" % (v.get("format_id"), aud.get("format_id"))
        desc = "%s %sx%s %s %sk" % (_codec_of(v), v.get("width"), v.get("height"),
                                    _codec_of(v), int(v.get("tbr") or v.get("vbr") or 0))
    else:
        expr = str(v.get("format_id"))
        desc = "%s %sx%s（含音轨）" % (_codec_of(v), v.get("width"), v.get("height"))
    return expr, desc


def _ydl_opts(cfg, outdir, name, cookie=None, fmt=None):
    """拼 yt-dlp 参数。"""
    h = int(cfg.get("max_height") or 720)
    o = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        # fmt 是 pick_stream 挑好的精确组合；挑不出来才退回这个兜底串
        "format": fmt or ("bestvideo[vcodec^=avc1]+bestaudio/best/bestvideo+bestaudio/best"),
        "outtmpl": os.path.join(outdir, name + ".%(ext)s"),
        "merge_output_format": "mp4",
        "concurrent_fragment_downloads": 4,
        "retries": 3,
        "socket_timeout": 20,
    }
    ff = ffmpeg_path()
    if ff:
        o["ffmpeg_location"] = ff
    ck = cfg.get("cookie_file") or ""
    if ck and os.path.exists(ck):
        o["cookiefile"] = ck
    return o


def _cookie_header(cfg, site):
    """给 http_headers 塞的 Cookie 串（抖音的 ttwid 走这里）。"""
    parts = []
    if site == "douyin":
        t = _ttwid()
        if t:
            parts.append("ttwid=%s" % t)
    return "; ".join(parts)


def _headers(site, cfg):
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    h = {"User-Agent": ua}
    if site == "bilibili":
        h["Referer"] = "https://www.bilibili.com/"
    elif site == "douyin":
        # 抖音一律手机 UA + 中文 Accept-Language，配合 ttwid 才过得去
        h["User-Agent"] = _UA_MOBILE
        h["Referer"] = "https://www.douyin.com/"
        h["Accept-Language"] = "zh-CN,zh;q=0.9"
    ck = _cookie_header(cfg, site)
    if ck:
        h["Cookie"] = ck
    return h


# ---------------------------------------------------------------- 抖音专用链路

# play 接口的清晰度档位。**1080p 就是天花板**，再往上服务端照样 cap 回 1080p 的字节数。
_DY_RATIOS = ("1080p", "720p", "540p", "480p")


def _dy_id(url, cfg):
    """把各种抖音链接归一成 aweme_id（纯数字串）。认不出返回 None。

    能直接认的：/video/<id>、/note/<id>、/share/video/<id>、modal_id=<id>。
    短链 `v.douyin.com/xxxx/` 必须**跟随重定向**看最终地址（urllib 默认就跟），
    跟不到再从落地页的 body 里抠。
    """
    u = url or ""
    for pat in (r"/video/(\d+)", r"/note/(\d+)", r"/share/(?:aweme|video)/(\d+)",
                r"modal_id=(\d+)", r"[?&]aweme_id=(\d+)"):
        m = re.search(pat, u)
        if m:
            return m.group(1)
    try:
        req = urllib.request.Request(u, headers=_headers("douyin", cfg))
        with urllib.request.urlopen(req, timeout=20) as r:
            final = r.geturl() or ""
            body = r.read(8192).decode("utf-8", "ignore")
        m = re.search(r"/video/(\d+)", final)
        if m:
            return m.group(1)
        m = re.search(r'"aweme_id"\s*:\s*"(\d+)"', body)
        if m:
            return m.group(1)
        m = re.search(r"\b(\d{15,20})\b", body)
        if m:
            return m.group(1)
    except Exception as e:
        log.debug("抖音短链跟随失败: %s", e)
    return None


def _router_data(html):
    """从分享页 HTML 里抠 `window._ROUTER_DATA = {...}`。

    ⚠️ 不能用 `\\{.*?\\}` 这种偷懒正则 —— ROUTER_DATA 里本身就嵌着 `}` 和字符串，
    非贪婪会在第一个 `</script>` 就断。这里老实做**花括号配平**（并跳过字符串里的括号）。
    """
    m = re.search(r"_ROUTER_DATA\s*=\s*", html or "")
    if not m:
        return None
    i = html.find("{", m.end())
    if i < 0:
        return None
    depth, end, in_str, esc = 0, None, False, False
    for j in range(i, len(html)):
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = j + 1
                break
    if not end:
        return None
    try:
        return json.loads(html[i:end])
    except Exception:
        m2 = re.search(r"_ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", html, re.S)
        if m2:
            try:
                return json.loads(m2.group(1))
            except Exception:
                return None
        return None


def _dy_item(data):
    """从 ROUTER_DATA 里挖出第一条视频。key 名（`video_(id)/page`）会变，所以遍历找。"""
    ld = (data or {}).get("loaderData") or {}
    for _k in ld:
        node = ld[_k] or {}
        if not isinstance(node, dict):
            continue
        lst = ((node.get("videoInfoRes") or {}).get("item_list")
               or node.get("item_list") or [])
        if lst:
            return lst[0]
    # 老版本页面：直接挂在 ROUTER_DATA 根下
    for key in ("videoInfoRes", "aweme"):
        node = (data or {}).get(key) or {}
        lst = node.get("item_list") or []
        if lst:
            return lst[0]
    if (data or {}).get("aweme_id") or (data or {}).get("video"):
        return data
    return None


def _dy_play(uri, ratio):
    """拼无水印播放地址：`play`（不是 `playwm`）+ ratio 定档。"""
    return "https://aweme.snssdk.com/aweme/v1/play/?video_id=%s&ratio=%s&line=0" % (uri, ratio)


def _dy_share(vid, cfg):
    """拉 iesdouyin 分享页拿元信息。返回 dict(ok, ...)。"""
    page = "https://www.iesdouyin.com/share/video/%s/" % vid
    try:
        html = urllib.request.urlopen(
            urllib.request.Request(page, headers=_headers("douyin", cfg)),
            timeout=25).read().decode("utf-8", "ignore")
    except Exception as e:
        return {"ok": False, "err": "打不开抖音分享页（%s）" % str(e)[:60]}
    data = _router_data(html)
    if not data:
        return {"ok": False, "err": "分享页里没有视频数据 —— 视频可能已删除、仅自己可见，或需要登录"}
    it = _dy_item(data) or {}
    v = it.get("video") or {}
    uri = ((v.get("play_addr") or {}).get("uri")
           or (it.get("play_addr") or {}).get("uri") or "")
    if not uri:
        if it.get("images"):
            return {"ok": False, "err": "这是图文作品，没有视频可以下"}
        return {"ok": False, "err": "拿不到播放地址 —— 视频可能已删除或需要登录"}
    # 时长： douyin 给的是**毫秒**。>=1000 才当毫秒换算（秒制的长视频最长也就几百秒，
    # 真按秒给的长视频必然 >600 会被时长闸拦掉，不会误放行）。
    d = 0
    try:
        d = int(v.get("duration") or it.get("duration") or 0)
    except (TypeError, ValueError):
        d = 0
    if d >= 1000:
        d = d // 1000
    return {"ok": True, "uri": uri, "id": vid,
            "title": (it.get("desc") or "").strip(),
            "duration": int(d),
            "author": ((it.get("author") or {}).get("nickname") or "")}


def _dy_size(uri, ratio, hdr):
    """探这一档多少字节（只连一下读 1KB 就走）。拿不到返回 None。"""
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(_dy_play(uri, ratio), headers=hdr), timeout=25)
        cl = int(r.headers.get("Content-Length") or 0)
        r.read(1024)
        r.close()
        return cl or None
    except Exception as e:
        log.debug("抖音探档 %s 失败: %s", ratio, e)
        return None


def _dy_save(uri, ratio, hdr, path, maxmb):
    """流式下到 path。中途超体积就停手删半成品（别白占硬盘）。"""
    tmp = path + ".part"
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(_dy_play(uri, ratio), headers=hdr), timeout=60)
    except Exception as e:
        return None, "下载失败：%s" % str(e)[:80]
    try:
        cl = int(r.headers.get("Content-Length") or 0)
        if cl and cl / 1048576.0 > maxmb * 1.05:
            r.close()
            return None, "这个视频 %.1f MB，超过上限 %.0f MB，QQ 传着也费劲" % (
                cl / 1048576.0, maxmb)
        got = 0
        with open(tmp, "wb") as f:
            while True:
                b = r.read(262144)
                if not b:
                    break
                f.write(b)
                got += len(b)
                if got / 1048576.0 > maxmb * 1.08:
                    r.close()
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                    return None, "下到一半就超了 %.0f MB，QQ 传着也费劲" % maxmb
        r.close()
        if got < 1024:
            raise IOError("只下了 %d 字节" % got)
        os.replace(tmp, path)
        return path, None
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None, "下载中断：%s" % str(e)[:80]


def _dy_fetch(url, cfg, outdir):
    """抖音全流程：分享页 → 探档选流 → 下载。返回与 fetch 同构的 dict。"""
    maxmb = float(cfg.get("max_size_mb") or 100)
    vid = _dy_id(url, cfg)
    if not vid:
        return {"ok": False, "err": "认不出这是哪个抖音视频（链接可能被截断了）"}
    info = _dy_share(vid, cfg)
    if not info.get("ok"):
        return info
    dur = int(info.get("duration") or 0)
    maxdur = int(cfg.get("max_duration_sec") or 600)
    if dur and dur > maxdur:
        return {"ok": False, "err": "这个视频 %d 分 %d 秒，太长了（上限 %d 分钟），发群里也卡。"
                % (dur // 60, dur % 60, maxdur // 60)}

    path = os.path.join(outdir, "dy_" + vid + ".mp4")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        log.info("视频解析·抖音命中缓存：%s", path)
        return {"ok": True, "path": path, "title": info.get("title") or "",
                "duration": dur, "id": vid,
                "size_mb": round(os.path.getsize(path) / 1048576.0, 2),
                "site": "douyin", "cached": True}

    hdr = _headers("douyin", cfg)
    chosen, sizes = None, {}
    for ratio in _DY_RATIOS:
        cl = _dy_size(info["uri"], ratio, hdr)
        if not cl:
            continue
        sizes[ratio] = cl
        log.info("抖音·档位 %s = %.2f MB", ratio, cl / 1048576.0)
        if cl / 1048576.0 <= maxmb * 1.02:
            chosen = ratio
            break
    if not chosen:
        if sizes:
            smallest = min(sizes.values())
            return {"ok": False, "err": "最低那档也有 %.1f MB，超过上限 %.0f MB，发不出去。"
                    % (smallest / 1048576.0, maxmb)}
        chosen = _DY_RATIOS[0]      # 探不到体积就硬着头皮下，下载时还有体积闸

    log.info("抖音·选档 %s，开始下载", chosen)
    p, err = _dy_save(info["uri"], chosen, hdr, path, maxmb)
    if not p:
        return {"ok": False, "err": err or "下载失败"}
    size = os.path.getsize(p) / 1048576.0
    if size > maxmb:
        try:
            os.remove(p)
        except OSError:
            pass
        return {"ok": False, "err": "下下来 %.1f MB，超过上限 %.0f MB。" % (size, maxmb)}
    return {"ok": True, "path": p, "title": info.get("title") or "",
            "duration": dur, "id": vid, "size_mb": round(size, 2),
            "site": "douyin"}


def probe(url, site, cfg):
    """只探测元信息（不下载）。返回 dict(ok, title, duration, err)。

    目的：先拿时长，超 `max_duration_sec` 的话连下都不下，省时间也省流量。
    """
    # 抖音：yt-dlp 的 DouyinIE 现在必报 Fresh cookies needed，直接走分享页
    if site == "douyin":
        vid = _dy_id(url, cfg)
        if not vid:
            return {"ok": False, "err": "认不出这是哪个抖音视频（链接可能被截断了）"}
        r = _dy_share(vid, cfg)
        r.setdefault("id", vid)
        return r
    if not yt_dlp_available():
        return {"ok": False, "err": "没装 yt-dlp（pip install -U yt-dlp）"}
    try:
        import yt_dlp
        o = {"quiet": True, "no_warnings": True, "skip_download": True,
             "noplaylist": True, "socket_timeout": 20}
        o["http_headers"] = _headers(site, cfg)
        ck = cfg.get("cookie_file") or ""
        if ck and os.path.exists(ck):
            o["cookiefile"] = ck
        ff = ffmpeg_path()
        if ff:
            o["ffmpeg_location"] = ff
        with yt_dlp.YoutubeDL(o) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return {"ok": False, "err": "解析不出视频信息"}
        # 合集/播放列表时 yt-dlp 会返回 entries，这里只取第一个
        if info.get("_type") == "playlist":
            ents = [e for e in (info.get("entries") or []) if e]
            if not ents:
                return {"ok": False, "err": "这是个合集，但里面没有可下的视频"}
            info = ents[0]
        return {"ok": True, "title": (info.get("title") or "").strip(),
                "duration": info.get("duration") or 0,
                "id": info.get("id") or "",
                "uploader": info.get("uploader") or info.get("channel") or "",
                "_formats": info.get("formats") or []}
    except Exception as e:
        msg = str(e).replace("\n", " ")
        return {"ok": False, "err": _human_err(msg, site)}


def _human_err(msg, site):
    """把 yt-dlp 的英文报错翻成人话。"""
    low = msg.lower()
    if "fresh cookies" in low or "__ac_nonce" in low:
        return ("%s 现在有反爬挑战，需要登录态。管理员去浏览器登录 %s 后，"
                "把 cookies.txt 放到 config 里 `video.cookie_file` 指的路径。"
                % (SITE_NAME.get(site, site), "B站" if site == "bilibili" else "抖音"))
    if "video unavailable" in low or "not exist" in low or "404" in low:
        return "这个视频不存在、已删除，或者是仅自己可见。"
    if "private" in low or "login" in low or "sign in" in low:
        return "这个视频要登录才能看，得配 cookie 才行。"
    if "geo" in low:
        return "这个视频有地区限制。"
    if "unable to download" in low or "urlopen" in low or "timed out" in low:
        return "下载时网络出错（超时/被掐），稍后再试。"
    if "ffmpeg" in low:
        return "合并音视频失败 —— 缺 ffmpeg（pip install imageio-ffmpeg）。"
    return msg[:180]


def _download_ytdlp(url, site, cfg, outdir, name, fmt=None):
    """主路：yt-dlp 下载。返回 (path, err)。"""
    try:
        import yt_dlp
        o = _ydl_opts(cfg, outdir, name, fmt=fmt)
        o["http_headers"] = _headers(site, cfg)
        before = set(glob.glob(os.path.join(outdir, name + ".*")))
        with yt_dlp.YoutubeDL(o) as ydl:
            ydl.download([url])
        after = set(glob.glob(os.path.join(outdir, name + ".*")))
        new = sorted(after - before)
        # yt-dlp 可能留下 .part / .ytdl 之类的中间文件，只认能播的
        new = [p for p in new if not p.endswith((".part", ".ytdl", ".f4f"))]
        if not new:
            # ⚠️ 同名文件已经存在时 yt-dlp **会直接跳过**（默认不覆盖），
            #    于是一个新文件都不会产生 —— 这不是失败，是命中缓存，直接用旧的即可。
            #    （2026-09-17 真机踩到：同一个视频第二次发被判成"下载完没找到文件"）
            old = [p for p in sorted(after)
                   if not p.endswith((".part", ".ytdl", ".f4f")) and os.path.getsize(p) > 0]
            if not old:
                return None, "下载完没找到文件"
            old.sort(key=os.path.getmtime, reverse=True)
            log.info("视频解析·命中缓存（同名文件已存在，直接复用）：%s", old[0])
            return old[0], None
        new.sort(key=os.path.getsize, reverse=True)
        return new[0], None
    except Exception as e:
        return None, _human_err(str(e).replace("\n", " "), site)


def _download_browser(url, site, cfg, outdir, name):
    """兜底：起真实浏览器（Playwright）拿播放器里的源地址，再直接下。

    只给抖音用 —— B站 yt-dlp 本身就够，没必要开浏览器。
    抖音的风控是 JS 挑战，纯请求过不去，浏览器能过。
    """
    if not cfg.get("use_browser", True):
        return None, "已禁用浏览器兜底"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "没装 playwright，浏览器兜底不可用"
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
    src = None
    try:
        with sync_playwright() as p:
            try:
                br = p.chromium.launch(channel="chrome", headless=True,
                                       args=["--mute-audio"])
            except Exception:
                br = p.chromium.launch(headless=True, args=["--mute-audio"])
            ctx = br.new_context(user_agent=ua, locale="zh-CN",
                                 viewport={"width": 1440, "height": 900})
            pg = ctx.new_page()
            pg.goto(url, timeout=60000, wait_until="domcontentloaded")
            for _ in range(8):
                time.sleep(2)
                vids = pg.eval_on_selector_all(
                    "video", "els=>els.map(e=>({s:e.currentSrc||e.src}))")
                for v in vids:
                    s = v.get("s") or ""
                    # 排除抖音自己的静态占位视频（uuu_xxx.mp4 之类）
                    if s and "douyinstatic" not in s and s.startswith("http"):
                        src = s
                        break
                if src:
                    break
            br.close()
    except Exception as e:
        return None, "浏览器兜底失败：%s" % str(e)[:100]
    if not src:
        return None, "浏览器打开后拿不到视频源（多半是视频已删除或必须登录）"
    # 拿到源地址后用普通 HTTP 下载（带 Referer，抖音 CDN 会校验）
    try:
        import urllib.request
        path = os.path.join(outdir, name + ".mp4")
        req = urllib.request.Request(src, headers=_headers(site, cfg))
        with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
            shutil.copyfileobj(r, f)
        return path, None
    except Exception as e:
        return None, "拿到地址但下载失败：%s" % str(e)[:100]


def fetch(url, site, cfg, base="."):
    """全流程：探测 -> 下载 -> 体检。返回 dict。

    ok=True 时含 path/title/duration/size_mb；否则含 err（已经是人话）。
    """
    if not cfg.get("enabled", True):
        return {"ok": False, "err": "视频解析功能关着"}
    outdir = out_dir(cfg, base)
    _sweep(outdir, cfg)

    # 抖音单独一条链路，完全不碰 yt-dlp；失败了才回落到真实浏览器
    if site == "douyin":
        log.info("视频解析·抖音走分享页链路 %s", url[:60])
        res = _dy_fetch(url, cfg, outdir)
        if res.get("ok"):
            return res
        if cfg.get("use_browser", True):
            log.info("抖音新路失败（%s），换浏览器兜底", (res.get("err") or "")[:60])
            name = "dy_" + (res.get("id") or re.sub(r"[^0-9A-Za-z_\-]", "", url)[-16:] or "v")
            p, e2 = _download_browser(url, site, cfg, outdir, name)
            if p:
                sz = os.path.getsize(p) / 1048576.0
                maxmb = float(cfg.get("max_size_mb") or 100)
                if sz > maxmb:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                    return {"ok": False, "err": "下下来 %.1f MB，超过上限 %.0f MB。" % (sz, maxmb)}
                return {"ok": True, "path": p, "title": res.get("title") or "",
                        "duration": res.get("duration") or 0,
                        "size_mb": round(sz, 2), "site": "douyin"}
            if e2:
                res = {"ok": False, "err": "%s；（浏览器兜底也失败：%s）"
                       % (res.get("err") or "下载失败", e2)}
        return res

    if not yt_dlp_available():
        return {"ok": False, "err": "没装 yt-dlp（管理员：pip install -U yt-dlp）"}

    log.info("视频解析·开始探测 %s %s", site, url[:60])
    info = probe(url, site, cfg)
    log.info("视频解析·探测完成 ok=%s err=%s", info.get("ok"), info.get("err"))
    if not info.get("ok"):
        # 抖音：yt-dlp 探不出来时，浏览器也救不了探测（页面都打不开），
        # 但下载阶段仍可一试，所以这里不直接返回，只记下。
        log.info("探测失败(%s)：%s", site, info.get("err"))
        title, dur = "", 0
        probe_err = info.get("err")
    else:
        title, dur = info.get("title") or "", int(info.get("duration") or 0)
        probe_err = None

    maxdur = int(cfg.get("max_duration_sec") or 600)
    if dur and dur > maxdur:
        return {"ok": False, "err": "这个视频 %d 分 %d 秒，太长了（上限 %d 分钟），发群里也卡。"
                % (dur // 60, dur % 60, maxdur // 60)}

    name = (info.get("id") or re.sub(r"\W+", "", url)[-16:] or "video")
    name = re.sub(r"[^0-9A-Za-z_\-]", "", name) or "video"

    # 挑流：在体积上限内给最高画质（H.264 优先，QQ 才播得动）
    fmt = None
    fs = info.get("_formats") or []
    if fs:
        fmt, desc = pick_stream(fs, dur, float(cfg.get("max_size_mb") or 100),
                                int(cfg.get("max_height") or 0))
        if fmt:
            log.info("视频解析·选流 %s -> %s", desc, fmt)
        else:
            log.info("视频解析·没有符合上限的流，退回兜底串")

    log.info("视频解析·开始下载 %s -> %s", site, name)
    path, err = _download_ytdlp(url, site, cfg, outdir, name, fmt=fmt)
    log.info("视频解析·下载结果 path=%s err=%s", path, err)
    if not path and site == "douyin":
        log.info("yt-dlp 走不通（%s），换浏览器兜底", (err or "")[:60])
        path, err2 = _download_browser(url, site, cfg, outdir, name)
        if path:
            err = None
        elif err2:
            err = err2
    if not path:
        return {"ok": False, "err": err or probe_err or "下载失败"}

    size = os.path.getsize(path) / 1048576.0
    maxmb = float(cfg.get("max_size_mb") or 100)
    if size > maxmb:
        try:
            os.remove(path)
        except OSError:
            pass
        return {"ok": False, "err": "下下来 %.1f MB，超过上限 %.0f MB，QQ 传着也费劲。"
                % (size, maxmb)}
    return {"ok": True, "path": path, "title": title, "duration": dur,
            "size_mb": round(size, 2), "site": site}


def _sweep(outdir, cfg):
    """清掉旧视频与残留碎片，免得硬盘被吃光（E盘空间敏感）。"""
    try:
        keep = float(cfg.get("keep_hours") or 6) * 3600
        max_total_mb = float(cfg.get("max_cache_total_mb") or 2048)  # 视频缓存池默认上限 2GB
        now = time.time()
        files = []
        for p in glob.glob(os.path.join(outdir, "*")):
            try:
                if not os.path.isfile(p):
                    continue
                # 清理超过 30 分钟的未完成临时碎片
                if p.endswith((".part", ".ytdl", ".tmp")) and (now - os.path.getmtime(p) > 1800):
                    os.remove(p)
                    continue
                # 清理过期成品视频
                if now - os.path.getmtime(p) > keep:
                    os.remove(p)
                    continue
                files.append(p)
            except OSError:
                pass

        # 检查总容量：超过上限时按修改时间由旧到新淘汰
        total_sz = sum(os.path.getsize(f) for f in files if os.path.exists(f))
        if total_sz > max_total_mb * 1048576:
            files.sort(key=lambda x: os.path.getmtime(x))
            for p in files:
                try:
                    if os.path.isfile(p):
                        sz = os.path.getsize(p)
                        os.remove(p)
                        total_sz -= sz
                        if total_sz <= max_total_mb * 1048576 * 0.8:
                            break
                except OSError:
                    pass
    except Exception as e:
        log.debug("清理视频缓存失败: %s", e)
