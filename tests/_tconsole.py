# -*- coding: utf-8 -*-
"""/console 新控制台（2026-11 阶段 1）回归：路由、token 门、静态资源、路径安全、旧 /admin 不回归。
全打桩、不联网、不碰真 config.json。用法：python tests/_tconsole.py
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
import json
import re
import time as _time
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402

bot.save_config = lambda: True
PORT = 18091
srv = ThreadingHTTPServer(("127.0.0.1", PORT), bot.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
TOK = bot.bridge_token()
BASE = "http://127.0.0.1:%d" % PORT
FAIL = []


def chk(label, cond, extra=""):
    print(("  [OK] " if cond else "  [失败] ") + label + ("" if cond else " | " + str(extra)))
    if not cond:
        FAIL.append(label)


def get(path, token=None, hdr=False):
    req = urllib.request.Request(BASE + path)
    if token and hdr:
        req.add_header("X-Bridge-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


print("=== /console 页面与 token 门 ===")
st, hd, body = get("/console?token=" + TOK)
html = body.decode("utf-8", "replace")
chk("带对 token 返回 200", st == 200)
chk("Content-Type 是 html", "text/html" in hd.get("Content-Type", ""))
chk("token 已注入页面", ('window.__QQ_TOKEN__ = "%s"' % TOK) in html)
chk("页面引用 vendor Vue", "/console/assets/vue.global.prod.js" in html and "vue.global.dev" not in html)
chk("页面引用 app.js 与 console.css",
    "/console/assets/app.js" in html and "/console/assets/console.css" in html)
st2, _h2, body2 = get("/console?token=wrong")
html2 = body2.decode("utf-8", "replace")
chk("带错 token 仍 200（页面壳可看）", st2 == 200)
chk("带错 token 不注入", 'window.__QQ_TOKEN__ = ""' in html2)
st3, _h3, body3 = get("/console")
chk("不带 token 仍 200 且不注入",
    st3 == 200 and 'window.__QQ_TOKEN__ = ""' in body3.decode("utf-8", "replace"))
# 2026-11-01 主人验收踩到的回归：.tokbox{display:flex} 会覆盖 UA 的 [hidden]{display:none}，
# 密钥卡永远弹出来。CSS 必须带 .tokbox[hidden] 压回规则（渲染层问题，只能静态断言 CSS 内容）。
chk("tokbox 的 [hidden] 压回规则存在（防永久弹出回归）",
    ".tokbox[hidden]{display:none" in open(_os.path.join(_ROOT, "webui", "assets", "console.css"),
                                            encoding="utf-8").read())

print("=== /console/assets 静态资源 ===")
st, hd, body = get("/console/assets/vue.global.prod.js")
chk("vendor Vue 可取", st == 200 and len(body) > 100000, (st, len(body)))
chk("vendor MIME 是 javascript", "application/javascript" in hd.get("Content-Type", ""))
chk("vendor 长缓存", "max-age=86400" in hd.get("Cache-Control", ""), hd.get("Cache-Control"))
st, hd, body = get("/console/assets/app.js")
chk("app.js 可取且 no-store（改完必须拿到新的）",
    st == 200 and "no-store" in hd.get("Cache-Control", ""), hd.get("Cache-Control"))
st, hd, body = get("/console/assets/console.css")
chk("console.css MIME 是 css", st == 200 and "text/css" in hd.get("Content-Type", ""))

print("=== 路径安全（穿越与白名单） ===")
st, _h, body = get("/console/assets/..%2Findex.html")
chk("编码穿越被拒（404）", st == 404, (st, body[:80]))
st, _h, body = get("/console/assets/../bot.py")
chk("裸穿越被拒", st in (404, 400), st)
st, _h, body = get("/console/assets/sub/secret.txt")
chk("子目录穿越被拒", st == 404, st)
st, _h, body = get("/console/assets/notexist.js")
chk("不存在的资源 404", st == 404, st)
st, _h, body = get("/console/assets/bridge-token.json")
chk("白名单外后缀（json）一律不给", st == 404, st)

print("=== 数据 API 仍走 token 门（页面壳无鉴权但数据必须有） ===")
for ep in ("/api/status", "/api/config"):
    st, _h, body = get(ep)
    try:
        ok_flag = json.loads(body.decode("utf-8")).get("ok")
    except Exception:
        ok_flag = None
    chk("%s 无 token 时 401" % ep, st == 401 and ok_flag is False, (st, ok_flag))
st, _h, body = get("/api/status", TOK, hdr=True)
chk("%s 带 token 200" % "/api/status", st == 200)
st, _h, body = get("/api/config", TOK, hdr=True)
d = json.loads(body.decode("utf-8"))
chk("/api/config 带 token 200 且 zones/tiles 齐全",
    st == 200 and d.get("zones") and d.get("tiles"),
    (st, bool(d.get("zones")), len(d.get("tiles") or [])))

print("=== 旧 /admin 不回归（并行过渡的硬要求） ===")
st, hd, body = get("/admin?token=" + TOK)
html = body.decode("utf-8", "replace")
chk("/admin 带 token 200 且注入", st == 200 and ('var TOKEN = "%s"' % TOK) in html)
chk("/admin 仍是旋钮页（含 CATS 声明）", "var CATS" in html)
chk("/console 改动没碰 /admin 断言相关标记", 'id="searchbox"' in html and "zoneWall" in html)

# ── 修订 8（主人 2026-11-01）：persona 过滤三段验证 ──────────────────────
# 写入段：turns.persona 落库非空且归属正确（跑在临时库，绝不碰真 state/maimem.db）
# 读取段：/api/reflect_items 严格按 persona 过滤，A 看不到 B 的条目，空 persona 返回空集
# 展示段：等价于读取段的集合 diff（前端只是渲染 API 结果，另以切人格视图为人工验收项）
print("\n=== 修订 8：persona 三段验证（临时库，不碰真库） ===")
import tempfile
from pathlib import Path as _P
GID = 123450001
tmp = _P(tempfile.mkdtemp(prefix="console_p2a_"))
import qbot_memory as _MEM
_MEM.bind(state_dir=tmp, cfg=bot.CFG)
bot.qbot_reflect.bind(str(tmp / "maimem.db"), cfg=bot.CFG)

# 写入段：走真实写入路径 log_turn（两个不同人格各一条），然后直查临时库断言 persona 列
try:
    _MEM.log_turn(str(GID), "测试甲", "10001", "今天天气不错", "", persona="人格甲")
    _MEM.log_turn(str(GID), "测试乙", "10002", "明天要下雨", "", persona="人格乙")
    _c2 = _MEM._conn()
    with _c2 as c:
        rows = c.execute("SELECT text, persona FROM turns WHERE text IN ('今天天气不错','明天要下雨')").fetchall()
    got = {t: p for t, p in rows}
    chk("写入段：turns.persona 落库非空且归属正确",
        got.get("今天天气不错") == "人格甲" and got.get("明天要下雨") == "人格乙", got)
except Exception as e:
    chk("写入段：turns 落库", False, e)

# 反射条目：两个 persona 各插一条（走 qbot_reflect.upsert_items 的真实入库路径）
try:
    now = _time.time()
    bot.qbot_reflect.upsert_items([
        {"id": "t-jia", "kind": "opinion", "persona": "人格甲", "gid": str(GID),
         "src": "群甲", "topic": "测试话题甲", "content": "甲的观点", "detail": "",
         "basis": "opinion", "confidence": 0.6, "status": "open", "refs": "",
         "dedup": "test-jia", "weight": 0.5, "created": now, "updated": now},
        {"id": "t-yi", "kind": "opinion", "persona": "人格乙", "gid": str(GID),
         "src": "群乙", "topic": "测试话题乙", "content": "乙的观点", "detail": "",
         "basis": "opinion", "confidence": 0.6, "status": "open", "refs": "",
         "dedup": "test-yi", "weight": 0.5, "created": now, "updated": now}])
    chk("写入段：reflect_item 双人格入库", True)
except Exception as e:
    chk("写入段：reflect_item 双人格入库", False, e)

# 读取段：API 按 persona 过滤（HTTP 层打到同进程 bot，读的就是刚 bind 的临时库）
def _get_json(path):
    return json.loads(get(path, TOK, hdr=True)[2].decode("utf-8"))

import urllib.parse
ja = _get_json("/api/reflect_items?persona=" + urllib.parse.quote("人格甲"))
jb = _get_json("/api/reflect_items?persona=" + urllib.parse.quote("人格乙"))
je = _get_json("/api/reflect_items?persona=")
chk("读取段：人格甲只看到甲的条目",
    ja.get("n") == 1 and ja["items"][0]["persona"] == "人格甲", (ja.get("n"), [i.get("persona") for i in ja.get("items") or []]))
chk("读取段：人格乙只看到乙的条目",
    jb.get("n") == 1 and jb["items"][0]["persona"] == "人格乙", (jb.get("n"), [i.get("persona") for i in jb.get("items") or []]))
chk("读取段：空 persona 返回空集（无兜底，红线）", je.get("n") == 0, je.get("n"))
chk("读取段：两集合互斥（展示段 diff 基础）",
    {i["id"] for i in ja["items"]}.isdisjoint({i["id"] for i in jb["items"]}))

# mood_read 冒烟（只读、不炸）
md = _get_json("/api/mood_read?gid=" + str(GID))
chk("mood_read 冒烟：ok 且含轴", md.get("ok") and isinstance(md.get("axes"), list), md.get("ok"))

# ── 修订①（设计文档 §9.1）：数量对账链，四种大脑模式 ─────────────────────
# 链：API返回 → -web_hidden → -brain → -depends_on → 应显示 = 主区渲染 + 高级折叠渲染
print("\n=== /api/meme（meme 服务卡，2026-11-05 控制台接入） ===")
st, _h, body = get("/api/meme")
chk("不带 token 401", st == 401, st)
st, _h, body = get("/api/meme", token=TOK, hdr=True)
d = json.loads(body)
chk("带 token 返回 ok 结构", st == 200 and d.get("ok") is True
    and "enabled" in d and isinstance(d.get("templates"), list)
    and "service" in d and "ctl_path" in d, (st, list(d.keys())))
chk("templates 含 petpet（真 config 的首期五模板）",
    any(t.get("key") == "petpet" for t in d.get("templates", [])),
    [t.get("key") for t in d.get("templates", [])])
chk("service 带 ok 字段（在线与否由环境定，测试只验结构）", "ok" in (d.get("service") or {}))

# 插件卡元信息：/api/config 的插件列表组必须带 meme 卡（前端靠 p.meme 画服务块）
st, _h, body = get("/api/config", token=TOK, hdr=True)
cfgd = json.loads(body)
_plug = next((g for g in cfgd.get("groups", []) if g.get("group") == "插件列表"), {})
chk("插件列表组带 meme 卡", any(p.get("id") == "meme" and p.get("meme") for p in (_plug.get("plugins") or [])))
chk("插件列表组带 meme 旋钮（toggle + 口令参数）",
    any(i.get("skill") == "meme" and i.get("kind") == "toggle" for i in (_plug.get("items") or []))
    and any(i.get("skill") == "meme" and i.get("kind") == "param" for i in (_plug.get("items") or [])))
# 前端静态断言：app.js 里必须有 meme 服务块与轮询（渲染层问题静态查最快）
_js = open(_os.path.join(_ROOT, "webui", "assets", "app.js"), encoding="utf-8").read()
chk("app.js 有 meme 服务块（mmCtl /api/meme）",
    "mmCtl('health')" in _js and '"/api/meme"' in _js and "mmPollStart" in _js)
_css = open(_os.path.join(_ROOT, "webui", "assets", "console.css"), encoding="utf-8").read()
chk("console.css 有 .memesvc 样式", ".memesvc{" in _css and ".memesvc__btns{" in _css)

print("=== /api/meme_catalog 与 /api/meme_preview（模板库，2026-11-05 晚） ===")
st, _h, body = get("/api/meme_catalog")
chk("catalog 不带 token 401", st == 401, st)
st, _h, body = get("/api/meme_preview?key=petpet")
chk("preview 不带 token 401", st == 401, st)
# catalog 全桩：不打 2233（SAFE 纪律），stub 掉 qbot_meme 的 HTTP 层
_orig_get, _orig_cat = bot.qbot_meme._http_get, dict(bot._MEME_CAT_CACHE)


def _fake_get(path, timeout):
    if path == "/memes/keys":
        return 200, '["petpet", "hug_leg"]'.encode()
    if path.startswith("/memes/") and path.endswith("/info"):
        k = path.split("/")[2]
        return 200, ('{"key": "%s", "params_type": {"min_images": 1, "max_images": 1,'
                     '"min_texts": 0, "max_texts": 0}, "keywords": ["x"]}' % k).encode()
    return 404, b"{}"


bot.qbot_meme._http_get = _fake_get
bot._MEME_CAT_CACHE["items"] = None
st, _h, body = get("/api/meme_catalog", token=TOK, hdr=True)
catd = json.loads(body)
chk("catalog 带 token 返回全量+on 标记",
    st == 200 and catd.get("ok") is True and catd.get("count") == 2
    and any(i.get("on") for i in catd.get("items", [])),
    (st, catd.get("count")))
bot.qbot_meme._http_get = _orig_get
bot._MEME_CAT_CACHE.clear(); bot._MEME_CAT_CACHE.update(_orig_cat)
# preview：服务在不在都行——200(真图) 或 502(代理失败) 都算路由通；401 已在上面验过
st, _h, body = get("/api/meme_preview?key=petpet", token=TOK, hdr=True)
chk("preview 带 token 路由通（200 图或 502 代理失败）", st in (200, 502), st)
if st == 200:
    chk("preview 返回图片", "image/" in _h.get("Content-Type", "") and len(body) > 100,
        (_h.get("Content-Type"), len(body)))

print("\n=== 修订①：四种大脑模式数量对账链 ===")
LOCAL = set()
SYS = ["大脑", "识图", "人格", "语音", "私聊", "管理", "只读"]
_expect = {"custom": (43, 47), "deepseek": (43, 47)}
for brain in ("custom", "deepseek"):
    for follow in (True, False):
        st = {"api": 0, "wh": 0, "br": 0, "dep": 0, "main": 0, "adv": 0}
        for k in bot.KNOBS:
            if k["group"] not in SYS:
                continue
            st["api"] += 1
            if k.get("web_hidden"):
                st["wh"] += 1
                continue
            b = k.get("brain")
            if b and not (b == brain or (b == "api" and brain not in LOCAL)):
                st["br"] += 1
                continue
            d = k.get("depends_on")
            if d and d.get("key"):
                v = {"see.follow_brain": follow}.get(d["key"])
                if v is not None:
                    vnb = str(v).strip().lower() in ("true", "1", "开", "on", "yes")
                    if vnb != bool(d.get("want")):
                        st["dep"] += 1
                        continue
            if k.get("type") in ("int", "float") or (k.get("type") == "str" and not k.get("multiline")):
                st["adv"] += 1
            else:
                st["main"] += 1
        shown = st["api"] - st["wh"] - st["br"] - st["dep"]
        want = _expect[brain][0 if follow else 1]
        chk("对账[%s/follow=%s]: 应显示 %d = 主区 %d + 高级 %d" %
            (brain, follow, shown, st["main"], st["adv"]),
            st["main"] + st["adv"] == shown and shown == want, (shown, want, st))

print("\n=== 汇总 ===")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
