# -*- coding: utf-8 -*-
"""临时：把 bot 的 HTTP 层真起在 127.0.0.1:18088 上，端到端验 /admin 与 /api/config。
验完即删（不是长期回归，长期回归是 _tset.py）。
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import json
import re
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402

bot.save_config = lambda: True          # 别写真的 config.json
PORT = 18088
srv = ThreadingHTTPServer(("127.0.0.1", PORT), bot.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
TOK = bot.bridge_token()
BASE = "http://127.0.0.1:%d" % PORT
FAIL = []


def get(path, token=None, hdr=True):
    req = urllib.request.Request(BASE + path)
    if hdr and token:
        req.add_header("X-Bridge-Token", token)
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status, r.read().decode("utf-8", "replace")


def post(path, obj, token=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Bridge-Token", token)
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status, r.read().decode("utf-8", "replace")


def chk(label, cond, extra=""):
    print("   %s %s%s" % ("[OK]" if cond else "[失败]", label, (" | " + str(extra)[:160]) if extra else ""))
    if not cond:
        FAIL.append(label)


print("=== /admin 网页 ===")
st, html = get("/admin")
chk("HTTP 200 且是 html", st == 200 and "<!doctype html" in html.lower(), st)
chk("没带 token 时不把密钥注进页面", "__TOKEN__" not in html and 'var TOKEN = ""' in html)
chk("页面含预设档与分区/栅格容器",
    "预设档" in html and 'id="grid"' in html and 'id="catnav"' in html)
# 分区按钮是 JS 运行时建的，静态 HTML 里只有兜底 CATS 声明 —— 就查声明层：
# 2026-11-01 总控重构：分区表改由 bot.py 的 ZONES 下发（/api/config 的 d.zones），
# 前端 CATS 只是 zones 缺席时的兜底；分区从 11 档归成 9 档（私聊并入对话、
# 插件市场并入插件、管理挪进系统），名称中文、无「全部」档。
# bot.py 侧的分区结构在 _tset.py 校验（zones 字段与磁贴）。
_EXPECT_CATS = ["总控", "对话", "群友", "生图", "素材", "反推", "视频", "插件", "系统"]
chk("九个分区声明齐全（总控/对话/群友/生图/素材/反推/视频/插件/系统）",
    all(('name:"%s"' % n) in html for n in _EXPECT_CATS))
_cats_decl = re.search(r"var CATS = \[(.*?)\];", html, re.S)
chk("分区声明条数正好九档（不多不漏）",
    bool(_cats_decl) and _cats_decl.group(1).count('name:"') == len(_EXPECT_CATS),
    (_cats_decl.group(1).count('name:"'), _cats_decl.group(1)) if _cats_decl else "?")
chk("分区声明里没有「全部」那一档（2026-09-15 主人定）", 'name:"全部"' not in html)
# 分区归属现在以 bot.py ZONES 为准；前端兜底映射里各组名只能归一个分区
chk("兜底映射覆盖全部业务组（识图/反推/图库/私聊/插件市场）",
    ('"识图":"对话"' in html) and ('"反推":"反推"' in html)
    and ('"图库":"素材"' in html) and ('"私聊":"对话"' in html)
    and ('"插件市场":"插件"' in html))
chk("兜底映射里没有组名归「总控」以外重复出现（归属唯一）",
    len(re.findall(r'"群友":"群友"', html)) == 1)
chk("页面有顶部搜索框（搜索定位配置项）",
    'id="searchbox"' in html and "applySearch" in html)
chk("页面有分区磁贴墙渲染（域总开关一屏管）",
    "zoneWall" in html and "ztiles" in html)
chk("页面有高级参数折叠（细节收纳）", "advFold" in html and "fold--adv" in html)
st, html2 = get("/admin?token=" + TOK)
chk("带对 token 时注入了 token", ('var TOKEN = "%s"' % TOK) in html2)
st, html3 = get("/admin?token=wrong")
chk("带错 token 不注入", 'var TOKEN = ""' in html3)

print("\n=== /api/config 鉴权 ===")
try:
    get("/api/config")
    chk("无 token 应 401", False)
except urllib.error.HTTPError as e:
    chk("无 token 返回 401", e.code == 401, e.code)
try:
    get("/api/config", token="bad")
    chk("错 token 应 401", False)
except urllib.error.HTTPError as e:
    chk("错 token 返回 401", e.code == 401, e.code)

print("\n=== /api/config 读 ===")
st, body = get("/api/config", token=TOK)
d = json.loads(body)
chk("带对 token 返回 200/ok", st == 200 and d.get("ok"), st)
# 2026-10-26 两处变动：
#  ①「系统」组消失了 —— 它唯一一根旋钮 napcat.mode 随 inject 一起删了（只跑 Docker，没跑法可切）
#  ②「插件·<id>」组是动态的（装几个技能包就几组），**不能硬编码数量**
# 所以只钉固定组的顺序，插件组另判是「全部 插件· 前缀」。
_FIXED_GROUPS = ["现画", "云图moss", "反推", "识图", "大脑", "人格", "群务", "图库", "群友", "黑话",
                 "表达", "时机", "管理", "私聊", "视频", "视频生成", "只读", "工作流选择", "语音", "技能"]
# 插件相关的三个组也是动态的，一起排除在外单独判：
#   `插件·<id>`（装了哪个技能包就一组）、`插件列表`、`插件市场`
_PLUG_PREFIX = "插件"
_got = [g["group"] for g in d["groups"]]
_got_fixed = [g for g in _got if not g.startswith(_PLUG_PREFIX)]
_got_plug = [g for g in _got if g.startswith(_PLUG_PREFIX)]
chk("分组齐全（固定组顺序不变，且不再有「系统」组）", _got_fixed == _FIXED_GROUPS, _got_fixed)
chk("插件相关组单独归类（插件·<id> / 插件列表 / 插件市场）",
    all(g.startswith(_PLUG_PREFIX) for g in _got_plug), _got_plug)
chk("返回了 profiles", bool(d.get("profiles")))
chk("密钥明文不在响应里", TOK not in body)
sec = [i for g in d["groups"] for i in g["items"] if i["type"] == "secret"]
chk("密钥项 value 都是 null", all(i["value"] is None for i in sec), [i["label"] for i in sec])

print("\n=== /api/config 写 ===")
st, body = post("/api/config", {"key": "画图档", "value": "krea2"}, token=TOK)
r = json.loads(body)
chk("改画图档成功", r.get("ok") and bot.CFG["comfyui"]["default_style"] == "krea2", r)
st, body = post("/api/config", {"key": "画图档", "value": "sd15"}, token=TOK)
r = json.loads(body)
chk("网页口也拦得住非法档位（铁律）", not r.get("ok") and bot.CFG["comfyui"]["default_style"] == "krea2", r)
st, body = post("/api/config", {"key": "自动拉起GPU", "value": "开"}, token=TOK)
r = json.loads(body)
chk("网页口也开不了 autostart（铁律）", not r.get("ok") and bot.CFG["comfyui"]["autostart"] is False, r)
st, body = post("/api/config", {"profile": "日常"}, token=TOK)
r = json.loads(body)
chk("网页口切预设档成功", r.get("ok"), r)
st, body = post("/api/config", {"key": "联网"}, token=TOK)
r = json.loads(body)
chk("只给 key = 查询", r.get("ok") and "display" in r, r)
try:
    post("/api/config", {"key": "画图档", "value": "fast"})
    chk("无 token 的写应 401", False)
except urllib.error.HTTPError as e:
    chk("无 token 的写返回 401（改不了配置）", e.code == 401, e.code)

print("\n=== /api/skill 技能工具（本地桩，不联网） ===")
import qbot_skill_tools as _T   # noqa: E402
_real_call = _T.call
_T.call = lambda s, t, **kw: (True, "stub %s.%s %s" % (s, t, kw))
st, body = post("/api/skill", {"skill": "trendy-memes", "tool": "lookup",
                               "args": {"term": "yyds"}}, token=TOK)
r = json.loads(body)
chk("/api/skill 通且回 ok/text", st == 200 and r.get("ok") and "trendy-memes.lookup" in r["text"], r)
st, body = post("/api/skill", {"skill": "whale-memes", "tool": "send",
                               "args": {"cid": 123450001, "keyword": "生气"}}, token=TOK)
r = json.loads(body)
chk("/api/skill 参数透传到工具", r.get("ok") and "123450001" in r["text"], r)
try:
    post("/api/skill", {"skill": "trendy-memes", "tool": "lookup", "args": {}})
    chk("无 token 的技能调用应 401", False)
except urllib.error.HTTPError as e:
    chk("无 token 的技能调用返回 401", e.code == 401, e.code)
_T.call = _real_call

print("\n=== 回归旧接口仍在 ===")
st, body = get("/health")
chk("/health 正常", st == 200 and json.loads(body).get("ok"), st)
st, body = get("/api/status", token=TOK)
d2 = json.loads(body)
chk("/api/status 正常且含 comfy", d2.get("ok") and "comfy" in d2, list(d2)[:6])

srv.shutdown()
print("\n========== 汇总 ==========\n失败 %d 条" % len(FAIL))
for f in FAIL:
    print("  [失败]", f)
sys.exit(1 if FAIL else 0)
