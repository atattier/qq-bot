# -*- coding: utf-8 -*-
"""链接速览（2026-10-23）：把消息里的 URL 真抓一遍，塞进 prompt。

**为什么要有这个模块**

  2026-10-23 21:06 主人私聊发 `https://github.com/Lotus0614/moss-manga-editor` 问「这啥」，
  机器人回「（´ω`）看起来像是个漫画编辑器的项目？小鲸不太懂技术类的东西呢」；
  再问「compyui 的插件吗」，回「具体是什么，小鲸还是不太懂呢，你懂的话可以教教我嘛」。

  那不是"不听话"，是它**真的没看见**：当时大脑是 `siliconflow`（纯 chat 补全档，
  没有工具面），模型手里只有 URL 里那串 slug，只能猜。猜完人设又把它往"装傻"那边推。

  修法不是求模型更努力，而是**在叫醒它之前，先把材料摆到它桌上**：
  消息里带 URL → 这里抓标题/简介（GitHub 还抓 API + README）→ 渲染成一段中文
  → `ai_reply()` 塞进 extra。这样无论大脑是 agent 还是最便宜的 chat 档，
  都能答「是什么 / 干什么 / 是不是插件」。

**设计约束（跟项目其它模块一致）**

  · 纯标准库（urllib + html.parser），不引第三方
  · **失败就返回空串**，绝不把异常抛给调用方；抓不到就让模型老实说「打不开」
  · 24h 缓存 + 失败负缓存，同一个链接不反复抓（群里刷屏时不至于把出口打爆）
  · 只放行公网地址：挡 127.0.0.1 / 内网 / 云 metadata ——
    不然群友发个 `http://127.0.0.1:8088/api/status` 就能借机器人探本机端口
  · 总耗时压住：只在消息里带链接时才跑，单链接最坏 ~12s，正常 1~2s
"""
import base64
import gzip
import html
import ipaddress
import zlib
from html.parser import HTMLParser
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("qqbot")

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / "state"
CACHE_FILE = STATE / "linkprev.json"

TTL = 24 * 3600          # 成功结果缓存一天
FAIL_TTL = 600           # 抓失败的负缓存 10 分钟
MAX_CACHE = 300
MAX_URLS = 2             # 一条消息最多看两个链接
BLOCK_MAX = 1100         # 注入 prompt 的字符上限（太长会挤掉人设）

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# URL 尾巴上常粘中文标点/括号，别把它们吃进 URL
URL_RE = re.compile(r"https?://[^\s<>\"'，。、；：！？）】」』》…]+", re.I)
_TAIL = ".,;:!?)]}>'\"，。、；：！？）】」』》…"

_BAD_HOST_RE = re.compile(
    r"^(?:localhost|.*\.localhost|.*\.local|.*\.internal|.*\.lan|.*\.home|"
    r"0\.0\.0\.0|\[?::1?\]?|metadata)$", re.I)


# ------------------------------------------------------------------ 缓存
_cache = {}
_cache_ok = False
_cache_lock = threading.Lock()


def _load_cache():
    global _cache, _cache_ok
    if _cache_ok:
        return
    _cache_ok = True
    try:
        d = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            _cache = d
    except Exception:  # noqa: BLE001
        _cache = {}


def _save_cache():
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        if len(_cache) > MAX_CACHE:
            items = sorted(_cache.items(), key=lambda kv: (kv[1] or {}).get("ts", 0))
            for k, _v in items[:len(_cache) - MAX_CACHE]:
                _cache.pop(k, None)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CACHE_FILE)
    except Exception as e:  # noqa: BLE001
        log.debug("linkprev 缓存写不进去: %s", e)


# ------------------------------------------------------------------ URL 抽取
def find_urls(text, limit=MAX_URLS):
    """从文本里捞公网 http(s) 链接，去重保序。"""
    out = []
    for m in URL_RE.finditer(str(text or "")):
        u = m.group(0).rstrip(_TAIL)
        if not u or u in out:
            continue
        if not host_ok(u):
            log.info("linkprev 跳过非公网地址: %s", u[:80])
            continue
        out.append(u)
        if len(out) >= limit:
            break
    return out


def host_ok(url):
    """只放行公网 host。挡掉本机/内网/云 metadata —— 防止被当成内网探测器。"""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").strip().lower()
    except Exception:  # noqa: BLE001
        return False
    if not host:
        return False
    if _BAD_HOST_RE.match(host):
        return False
    if "." not in host:            # 单段主机名（含裸 localhost 变体）一律不碰
        return False
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return True                # 域名：交给 DNS，不做二次解析（避免卡住调用方）
    return not (ip.is_private or ip.is_loopback or ip.is_reserved
                or ip.is_link_local or ip.is_multicast or ip.is_unspecified)


# ------------------------------------------------------------------ 抓取
def _decompress(raw, enc):
    """按 Content-Encoding 解压。解不开就回空串（上层会当成「没抓到内容」）。"""
    enc = (enc or "").lower()
    if not raw:
        return raw
    if "gzip" in enc:
        try:
            return gzip.decompress(raw)
        except Exception as e:  # noqa: BLE001
            log.debug("linkprev gzip 解压失败: %s", e)
            return b""
    if "deflate" in enc:
        try:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:  # noqa: BLE001
            try:
                return zlib.decompress(raw)
            except Exception as e:  # noqa: BLE001
                log.debug("linkprev deflate 解压失败: %s", e)
                return b""
    return raw


def _fetch(url, timeout=6, limit=400_000, api=False):
    """返回 (final_url, headers, raw_bytes, charset)。失败抛异常，由上层接。

    ⚠️ 必须自己处理 Content-Encoding（2026-10-23 踩到）：不少站点**不管你有没有要**
       都会回 gzip（实测 python.org 就是），urllib 不会自动解压，
       拿到的就是一串二进制，title 全空 —— 表现成「页面没给标题」。
    """
    hdr = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
           "Accept-Encoding": "gzip, deflate"}
    if api:
        hdr["Accept"] = "application/vnd.github+json"
        hdr["X-GitHub-Api-Version"] = "2022-11-28"
    req = urllib.request.Request(url, headers=hdr)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        enc = (r.headers.get("Content-Encoding") or "").lower()
        cs = r.headers.get_content_charset() or "utf-8"
        if "gzip" in enc or "deflate" in enc:
            raw = r.read(2_000_000)          # 压缩流不能截断读，解压后再截
            raw = _decompress(raw, enc)
        else:
            raw = r.read(limit)
        return r.geturl(), r.headers, raw[:limit], cs


def _text(raw, charset="utf-8"):
    for cs in (charset, "utf-8", "gb18030"):
        try:
            return raw.decode(cs, errors="replace")
        except (LookupError, TypeError):
            continue
    return raw.decode("utf-8", errors="replace")


class _Meta(HTMLParser):
    """只捡 title / meta description / og:*，够用了。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.metas = {}
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            k = (d.get("property") or d.get("name") or "").strip().lower()
            v = (d.get("content") or "").strip()
            if k and v and k not in self.metas:
                self.metas[k] = v

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and len(self.title) < 300:
            self.title += data


def _clean(s, n=0):
    s = html.unescape(str(s or ""))
    s = re.sub(r"<[^>]{1,80}>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] if n else s


def _gh_repo(url):
    """github.com/{owner}/{repo} → (owner, repo)；不是仓库页就 None。"""
    try:
        p = urllib.parse.urlparse(url)
    except Exception:  # noqa: BLE001
        return None
    if (p.hostname or "").lower() not in ("github.com", "www.github.com"):
        return None
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 2:
        return None
    if parts[0].lower() in ("orgs", "topics", "search", "sponsors", "settings",
                            "notifications", "marketplace", "explore", "collections"):
        return None
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def _knum(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n or "?")
    if n >= 10000:
        return "%.1f万" % (n / 10000.0)
    if n >= 1000:
        return "%.1fk" % (n / 1000.0)
    return str(n)


def _ago(iso):
    """2026-10-20T12:00:00Z → 3 天前"""
    try:
        t = time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:  # noqa: BLE001
        return ""
    d = time.time() - t
    if d < 3600:
        return "刚更新"
    if d < 86400:
        return "%d 小时前更新" % int(d // 3600)
    if d < 86400 * 30:
        return "%d 天前更新" % int(d // 86400)
    return time.strftime("%Y-%m-%d", time.localtime(t)) + " 更新"


_README_JUNK = re.compile(
    r"!\[[^\]]*\]\([^)]*\)"          # 图片/badge
    r"|\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)"   # 嵌套 badge
    r"|<[^>]{1,200}>"
    r"|^\s*[-=]{3,}\s*$", re.M)


def _gh_preview(owner, repo):
    d = {"site": "GitHub", "repo": "%s/%s" % (owner, repo)}
    api = "https://api.github.com/repos/%s/%s" % (owner, repo)
    t0 = time.time()
    try:
        _u, _h, raw, _cs = _fetch(api, api=True)
        j = json.loads(_text(raw))
    except Exception as e:  # noqa: BLE001
        d["err"] = "GitHub API 没通（%s）" % str(e)[:60]
        return d
    if not isinstance(j, dict) or j.get("message"):
        d["err"] = str((j or {}).get("message") or "GitHub 没返回仓库信息")[:80]
        return d
    d["name"] = _clean(j.get("full_name") or d["repo"])
    d["desc"] = _clean(j.get("description") or "", 220)
    d["lang"] = _clean(j.get("language") or "", 30)
    d["stars"] = _knum(j.get("stargazers_count"))
    d["forks"] = _knum(j.get("forks_count"))
    d["ago"] = _ago(j.get("pushed_at") or "")
    lic = (j.get("license") or {}) or {}
    d["license"] = _clean(lic.get("spdx_id") or lic.get("name") or "", 20)
    d["topics"] = [str(x) for x in (j.get("topics") or [])][:8]
    d["archived"] = bool(j.get("archived"))
    d["homepage"] = _clean(j.get("homepage") or "", 80)
    # README：API 一开头就慢的话就别再等第二个请求（聊天在等这一轮）
    if time.time() - t0 < 4.0:
        try:
            _u, _h, raw, _cs = _fetch(api + "/readme", api=True)
            rj = json.loads(_text(raw))
            body = rj.get("content") or ""
            if rj.get("encoding") == "base64" and body:
                body = base64.b64decode(body).decode("utf-8", errors="replace")
            body = _README_JUNK.sub(" ", body)
            body = re.sub(r"\[([^\]]{1,60})\]\([^)]*\)", r"\1", body)   # [文字](链接)→文字
            body = re.sub(r"^\s*>\s*", "", body, flags=re.M)            # 引用符
            # ⚠️ 别把 `_` 也洗掉（2026-10-23 踩到）：`custom_nodes` 会被洗成 `custom nodes`，
            #    于是「是不是 ComfyUI 插件」永远判不出来 —— 而主人问的偏偏就是这句。
            body = re.sub(r"[`*#|]+", " ", body)
            body = re.sub(r"\s+", " ", body).strip()
            d["readme"] = body[:900]
        except Exception as e:  # noqa: BLE001
            log.debug("linkprev README 没抓到: %s", e)
    # ComfyUI 关系判定（2026-10-23 修：原来只要出现 comfyui 就判「是插件」，
    # 实测把 moss-manga-editor 这种「独立网页编辑器、只是调 ComfyUI 工作流」的也判成插件 ——
    # 而主人问的偏偏就是「是不是 comfyui 的插件」，判错等于没帮上忙。所以分三档：
    #   custom_nodes 出现 = 真·自定义节点/插件
    #   只提 comfyui      = 相关，但不是装在 custom_nodes 里的插件
    #   都没有            = 不相关
    blob = " ".join([d.get("name", ""), d.get("desc", ""),
                     " ".join(d.get("topics") or []), d.get("readme", "")]).lower()
    d["comfy"] = "comfyui" in blob
    d["comfy_node"] = bool(re.search(r"custom_nodes", blob))
    if d["comfy_node"]:
        m = re.search(r"(?:cd\s+[^\s]*custom_nodes[^\s]*|git clone\s+\S+\s*(?:\./)?custom_nodes/\S+|"
                      r"custom_nodes/([\w\-.]+))", d.get("readme", ""), re.I)
        if m:
            d["install"] = _clean(m.group(0), 120)
        else:
            m = re.search(r"custom_nodes/([\w\-.]+)", blob)
            if m:
                d["install"] = "custom_nodes/" + m.group(1)
    return d


def _html_preview(url):
    d = {"site": (urllib.parse.urlparse(url).hostname or "").replace("www.", "")}
    try:
        final, _h, raw, cs = _fetch(url)
    except Exception as e:  # noqa: BLE001
        d["err"] = "打不开（%s）" % str(e)[:60]
        return d
    txt = _text(raw, cs)
    p = _Meta()
    try:
        p.feed(txt[:200_000])
    except Exception:  # noqa: BLE001
        pass
    d["url"] = final
    d["title"] = _clean(p.metas.get("og:title") or p.title or "", 160)
    d["desc"] = _clean(p.metas.get("og:description") or p.metas.get("description")
                       or p.metas.get("twitter:description") or "", 260)
    if not (d["title"] or d["desc"]):
        d["err"] = "页面没给标题也没给简介（可能要登录或是纯 JS 渲染）"
    return d


# ------------------------------------------------------------------ 对外
def preview(url, use_cache=True):
    """抓一个链接，返回 dict（可能带 err）。永不抛异常。"""
    url = str(url or "").strip()
    if not url or not host_ok(url):
        return {}
    _load_cache()
    key = url
    if use_cache:
        with _cache_lock:
            c = _cache.get(key)
        if c and time.time() - float(c.get("ts") or 0) < (TTL if c.get("data") else FAIL_TTL):
            return dict(c.get("data") or {})
    gh = _gh_repo(url)
    try:
        data = _gh_preview(*gh) if gh else _html_preview(url)
    except Exception as e:  # noqa: BLE001
        data = {"err": "抓取失败（%s）" % str(e)[:60]}
    data = data or {}
    data.setdefault("url", url)
    with _cache_lock:
        _cache[key] = {"ts": time.time(), "data": data}
    _save_cache()
    return dict(data)


def render(d):
    """一个链接的速览 → 中文文本行。全是空就返回空串。"""
    if not d:
        return ""
    lines = []
    if d.get("site") == "GitHub" and d.get("repo"):
        lines.append("链接：%s" % d.get("url", ""))
        lines.append("站点：GitHub 仓库 %s" % d.get("repo"))
        if d.get("desc"):
            lines.append("简介：%s" % d["desc"])
        if d.get("err"):
            lines.append("抓取说明：%s" % d["err"])
        meta = []
        if d.get("lang"):
            meta.append("语言 %s" % d["lang"])
        if d.get("stars"):
            meta.append("星标 %s" % d["stars"])
        if d.get("forks"):
            meta.append("fork %s" % d["forks"])
        if d.get("ago"):
            meta.append(d["ago"])
        if d.get("license"):
            meta.append("许可 %s" % d["license"])
        if d.get("archived"):
            meta.append("**仓库已归档（不再维护）**")
        if meta:
            lines.append("数据：%s" % " ｜ ".join(meta))
        if d.get("topics"):
            lines.append("话题：%s" % "、".join(d["topics"]))
        if d.get("readme"):
            lines.append("README 开头：%s" % d["readme"][:420])
        if d.get("comfy_node"):
            tip = "这是 **ComfyUI 的自定义节点/插件**（要放进 custom_nodes 里装）"
            if d.get("install"):
                tip += "，README 里的安装步骤形如 `%s`" % d["install"]
            lines.append("判定：%s" % tip)
        elif d.get("comfy"):
            lines.append("判定：**不是** ComfyUI 插件（不用装进 custom_nodes），"
                         "但跟 ComfyUI 有关 —— 它会调用 ComfyUI 的工作流/接口")
        elif d.get("desc") or d.get("readme"):
            lines.append("判定：没在名字/简介/README 里看到 comfyui，跟 ComfyUI 没关系")
        return "\n".join(lines)
    lines.append("链接：%s" % (d.get("url") or ""))
    if d.get("site"):
        lines.append("站点：%s" % d["site"])
    if d.get("title"):
        lines.append("标题：%s" % d["title"])
    if d.get("desc"):
        lines.append("简介：%s" % d["desc"])
    if d.get("err"):
        lines.append("抓取说明：%s" % d["err"])
    return "\n".join(x for x in lines if x)


def block(text, budget=BLOCK_MAX):
    """消息文本 → 注入 prompt 的整段。没有链接 / 全抓空 / 关掉 → 空串。"""
    urls = find_urls(text)
    if not urls:
        return ""
    parts = []
    for u in urls:
        try:
            r = render(preview(u))
        except Exception as e:  # noqa: BLE001
            log.debug("linkprev 渲染失败 %s: %s", u[:60], e)
            r = ""
        if r:
            parts.append(r)
    if not parts:
        return ""
    body = "\n\n".join(parts)
    if len(body) > budget:
        body = body[:budget].rstrip() + "…"
    return ("【链接速览（系统刚刚真去抓回来的内容，不是猜的）】\n" + body)
