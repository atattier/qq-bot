# -*- coding: utf-8 -*-
"""链接速览 —— 离线自测（2026-10-23）。

背景：主人发 GitHub 链接问「这啥，comfyui 的插件吗」，机器人回「小鲸不太懂技术类的东西呢」。
根因是当时大脑是纯 chat 档（没工具面），手里只有一串 URL。修法是 `qbot_linkprev` 先替它抓回来。

这里验证（**全程不联网**，`_fetch` 打桩）：
  · URL 抽取（中文标点尾巴、去重、条数上限、只放公网）
  · GitHub 仓库解析：简介/语言/星标/README + **三档 ComfyUI 判定**
  · 普通网页：title / og:description；gzip 解压（python.org 就这毛病）
  · 缓存命中不再抓第二次；失败也有负缓存
  · 渲染成中文段落 + 超长截断；没链接/抓空返回空串

用法：python tests/_tlinkprev.py
"""
import base64
import gzip
import json
import os as _os
import sys as _sys
import tempfile
from pathlib import Path

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
_sys.stdout.reconfigure(encoding="utf-8")

import qbot_linkprev as L    # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [!!] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


_tmp = Path(tempfile.mkdtemp(prefix="linkprev_test_"))
L.CACHE_FILE = _tmp / "linkprev.json"     # 别写进真 state/
L._cache, L._cache_ok = {}, False

print("== URL 抽取 ==")
u = L.find_urls("看看这个https://github.com/Lotus0614/moss-manga-editor。")
chk("能从粘连中文里抠出链接", u == ["https://github.com/Lotus0614/moss-manga-editor"], u)
u = L.find_urls("https://www.python.org/，还有 https://example.com/a?b=1)")
chk("中文逗号/右括号被剥掉", u[0] == "https://www.python.org/" and u[1] == "https://example.com/a?b=1", u)
u = L.find_urls("a https://a.com/1 b https://a.com/1 c https://a.com/2 d https://a.com/3")
chk("去重 + 最多 2 个", u == ["https://a.com/1", "https://a.com/2"], u)
chk("没有链接就是空", L.find_urls("今天天气不错") == [])

print("== 只放行公网（别被当成内网探测器）==")
for bad in ("http://127.0.0.1:8088/api/status", "http://localhost/x",
            "http://192.168.1.1/", "http://10.0.0.5/", "http://169.254.169.254/latest",
            "http://metadata.google.internal/", "http://intranet/"):
    chk("挡住 %s" % bad, L.host_ok(bad) is False)
chk("放行 github.com", L.host_ok("https://github.com/a/b") is True)
chk("放行公网 IP", L.host_ok("http://8.8.8.8/") is True)

print("== gzip 解压（python.org 这种不问就给压缩的站）==")
_raw = gzip.compress("<html><title>你好</title></html>".encode())
chk("gzip 能解开", L._decompress(_raw, "gzip").decode().endswith("</html>"))
chk("没压缩就原样返回", L._decompress(b"abc", "") == b"abc")
chk("坏数据不抛异常", L._decompress(b"\x00\x01\x02", "gzip") == b"")

print("== GitHub：普通仓库（调 ComfyUI 但不是插件）==")
API = {"full_name": "Lotus0614/moss-manga-editor",
       "description": "一个对使用 MOSS Image 模型进行漫画创作生成的编辑器",
       "language": "JavaScript", "stargazers_count": 10, "forks_count": 2,
       "pushed_at": "2026-10-24T06:00:00Z", "topics": ["manga"],
       "license": {"spdx_id": "MIT"}, "archived": False, "homepage": ""}
README = "# moss-manga\n\n用 MOSS Image 做漫画。\n\nComfyUI 工作流与占位符见 references。\n"
CALLS = []


def _stub_fetch(url, **kw):
    CALLS.append(url)
    if url.endswith("/readme"):
        body = base64.b64encode(README.encode()).decode()
        return url, {}, json.dumps({"content": body, "encoding": "base64"}).encode(), "utf-8"
    if "/repos/" in url:
        return url, {}, json.dumps(API).encode(), "utf-8"
    return url, {}, b"", "utf-8"


L._fetch = _stub_fetch
d = L.preview("https://github.com/Lotus0614/moss-manga-editor")
chk("认出是 GitHub 仓库", d.get("repo") == "Lotus0614/moss-manga-editor", d.get("repo"))
chk("简介抓到了", "MOSS Image" in (d.get("desc") or ""), d.get("desc"))
chk("星标格式化", d.get("stars") == "10", d.get("stars"))
chk("README 抓到了", "moss-manga" in (d.get("readme") or ""))
chk("提到 comfyui 但不是插件", d.get("comfy") is True and d.get("comfy_node") is False)
txt = L.render(d)
chk("渲染里有简介", "简介：" in txt)
chk("判定为「不是插件但相关」", "**不是** ComfyUI 插件" in txt, txt[-60:])

print("== GitHub：真·custom_nodes 插件 ==")
README2 = "Install: git clone https://x/y.git ComfyUI/custom_nodes/comfyui-moss\n然后重启 ComfyUI"
L._cache.clear()


def _stub_fetch2(url, **kw):
    if url.endswith("/readme"):
        body = base64.b64encode(README2.encode()).decode()
        return url, {}, json.dumps({"content": body, "encoding": "base64"}).encode(), "utf-8"
    api2 = dict(API, full_name="a/comfyui-moss", description="ComfyUI 节点")
    return url, {}, json.dumps(api2).encode(), "utf-8"


L._fetch = _stub_fetch2
d2 = L.preview("https://github.com/a/comfyui-moss")
chk("判定成插件", d2.get("comfy_node") is True)
chk("抓到 custom_nodes 安装行", "custom_nodes" in (d2.get("install") or ""), d2.get("install"))
chk("渲染写「是插件」", "自定义节点/插件" in L.render(d2))

print("== GitHub：完全无关的仓库 ==")
L._cache.clear()


def _stub_fetch3(url, **kw):
    if url.endswith("/readme"):
        body = base64.b64encode(b"just a cli tool").decode()
        return url, {}, json.dumps({"content": body, "encoding": "base64"}).encode(), "utf-8"
    api3 = dict(API, full_name="a/tool", description="a tiny cli")
    return url, {}, json.dumps(api3).encode(), "utf-8"


L._fetch = _stub_fetch3
d3 = L.preview("https://github.com/a/tool")
chk("comfy 为假", d3.get("comfy") is False)
chk("渲染写「跟 ComfyUI 没关系」", "没关系" in L.render(d3))

print("== 普通网页 ==")
L._cache.clear()
HTML = ("<html><head><title>页面标题</title>"
        "<meta property='og:description' content='一句话简介'></head><body>x</body></html>")


def _stub_html(url, **kw):
    return url, {}, HTML.encode(), "utf-8"


L._fetch = _stub_html
d4 = L.preview("https://example.com/post")
chk("title 抓到了", d4.get("title") == "页面标题", d4.get("title"))
chk("og:description 抓到了", d4.get("desc") == "一句话简介", d4.get("desc"))
chk("站点名去掉 www", d4.get("site") == "example.com", d4.get("site"))

print("== 缓存 ==")
L._cache.clear()
CALLS.clear()
L._fetch = _stub_fetch
L.preview("https://github.com/Lotus0614/moss-manga-editor")
n1 = len(CALLS)
CALLS.clear()
L.preview("https://github.com/Lotus0614/moss-manga-editor")
chk("第二次走缓存（一次都没抓）", n1 > 0 and len(CALLS) == 0, "第一次 %d 次 / 第二次 %d 次" % (n1, len(CALLS)))

print("== 抓失败 ==")
L._cache.clear()


def _stub_dead(url, **kw):
    raise OSError("connection refused")


L._fetch = _stub_dead
d5 = L.preview("https://example.com/dead")
chk("不抛异常，带 err", bool(d5.get("err")), d5.get("err"))
chk("渲染里写明抓不到", "抓取说明" in L.render(d5))

print("== block() ==")
L._cache.clear()
L._fetch = _stub_fetch
b = L.block("这啥 https://github.com/Lotus0614/moss-manga-editor")
chk("有链接时返回整段", b.startswith("【链接速览") and "Lotus0614" in b, b[:40])
chk("无链接返回空串", L.block("今天吃啥") == "")
chk("超预算会截断", len(L.block("x https://github.com/a/b", budget=80)) <= 200)
chk("内网链接不抓（返回空）", L.block("http://127.0.0.1:8088/api/status") == "")

print()
print("==== ALL PASS ====" if ok else "==== 有失败 ====")
_sys.exit(0 if ok else 1)
