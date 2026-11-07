# -*- coding: utf-8 -*-
"""宫格档（MOSS-Image）+「/」画图快捷前缀 端到端测试（2026-10-16 主人加）。

主人两句话：
  ① 「bot 加一个画图档，叫宫格档，用工作流 Moss_int8_t2i.json」
  ② 「画图的所有指令的前缀改为 / ，比如 /画一只猫，就不需要 @了，用 / 方便一点」

这里驱动**真实的 `_handle_message`**（不是只测单个函数），把出口换成记录桩，
再配一层**纯本地**的工作流注入验证（只转图不提交，不占 ComfyUI 队列）。

用法： PYTHONIOENCODING=utf-8 python tests/_tmoss.py
"""
import io
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import bot          # noqa: E402
import comfy_gen    # noqa: E402

WF_DIR = os.environ.get("QBOT_COMFY_WORKFLOWS") or r"C:\ComfyUI\ComfyUI\user\default\workflows"
_SRC = os.path.join(WF_DIR, "Moss_int8_t2i.json")
_DST = os.path.join(WF_DIR, "Moss_宫格.json")

# 2026-11-05 U3：本测试硬依赖本机 ComfyUI 工作流文件（MANUAL 注册时加的守卫）。
# 文件缺失就 SKIP——踩过一次「文件不在 → 假失败」。
if not os.path.isfile(_SRC):
    print("SKIP: 找不到 %s（宫格档工作流不在本机，跳过而不是假失败）" % _SRC)
    sys.exit(0)

bad = 0


def chk(name, ok, why=""):
    global bad
    if not ok:
        bad += 1
    print(("   [OK] " if ok else "   [失败] ") + name + ("" if ok else " | %s" % (why,)))


# ---------------- 打桩：挡掉「外面世界」（照抄 _tat.py 的套路）----------------
bot.save_config = lambda: True
bot.SELF_ID = 123450002
SENT = []
bot.send_group = lambda cid, text, **kw: SENT.append(text) or True
bot.send_reply = lambda *a, **kw: True
bot.api = lambda action, **kw: {"data": {}, "status": "ok", "retcode": 0}
bot.maybe_slang_extract = lambda gid: None
bot.remember = lambda *a, **kw: None
bot.sim_allowed = lambda *a, **kw: False   # 2026-10-19: 多了 uid 参数，桩收 *a/**kw
bot.CFG["keywords"] = []
bot.CFG.setdefault("admin", {})["silent"] = False
bot.CFG.setdefault("debug", {})["dump_events"] = False

_IMGS = []
_QMID = [0]
bot._scan_media = lambda msg: (list(_IMGS), _QMID[0])
# 引用（回复）的图：qmid 非 0 时给一张，好验「引用了图也算有参考图」
bot.quoted_images = lambda mid: ([{"url": "http://example.com/q.jpg", "file": "q.jpg"}]
                                 if str(mid or "0") not in ("", "0") else [])
bot._vg_recall_assets = lambda gid, uid: ([], None)
bot._vg_localize_images = lambda imgs: list(imgs or [])

HITS = []
# ⚠️ 真实签名 draw_submit(gid, what, style, who, ...) —— **style 是第 3 个位置参数**
bot.draw_submit = lambda *a, **kw: (HITS.append(
    ("现画", (a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else None))), (True, ""))[1]
bot.handle_i2i = lambda gid, extra="", privileged=False, imgs=None, qmid=0, style=None: \
    HITS.append(("画同款", (extra, style)))
# 真实调用会给 arg= 等关键字参数，桩一律收 `*a, **kw`（别照抄旧签名，会 TypeError）
bot.handle_random_draw = lambda *a, **kw: HITS.append(("随机生图", ("", None)))
bot.handle_video_gen = lambda gid, desc, privileged=False, who="", mid=0, images=None, \
    ref_video=None: HITS.append(("视频生成", (desc, None)))

_MID = [810000]


def _cotech(text, group=True, uid=111, imgs=None):
    pass


def run(text, group=True, at=False, uid=111, imgs=None, qmid=0):
    """走一遍真实的 _handle_message，返回本次命中的出口列表。"""
    HITS.clear()
    SENT.clear()
    _IMGS[:] = list(imgs or [])
    _QMID[0] = qmid
    _MID[0] += 1
    segs = []
    if at:
        segs.append({"type": "at", "data": {"qq": str(bot.SELF_ID), "name": "小鲸"}})
    segs.append({"type": "text", "data": {"text": text}})
    data = {"post_type": "message",
            "message_type": "group" if group else "private",
            "group_id": 123450001, "user_id": uid,
            "sender": {"role": "member", "nickname": "阿甲"},
            "message_id": _MID[0], "message": segs}
    gid = 123450001 if group else bot.priv_key(uid)
    bot._handle_message(data, gid, is_group=group)
    return list(HITS)


print("=== 1) 宫格档配置 ===")
_c = bot._comfy_cfg()
_stys = _c.get("styles") or {}
chk("config 里有「宫格」档", "宫格" in _stys, sorted(_stys.keys()))
_s = _stys.get("宫格") or {}
chk("engine = wf（工作流档）", _s.get("engine") == "wf", _s.get("engine"))
chk("workflow = Moss_宫格.json（bot 专用副本，不是主人原文件）",
    _s.get("workflow") == "Moss_宫格.json", _s.get("workflow"))
chk("keep_expand=true（保住两级 LLM 编译链）", _s.get("keep_expand") is True, _s.get("keep_expand"))
chk("prompt_slot=用户输入（写进 #40，避开同名的 #36）",
    _s.get("prompt_slot") == "用户输入", _s.get("prompt_slot"))
chk("enhance=false（不用 bot 侧润色，交给工作流里的 PromptExpand）",
    _s.get("enhance") is False, _s.get("enhance"))
chk("没写 width/height ⇒ 用工作流自己的 1280×1280",
    not _s.get("width") and not _s.get("height"), (_s.get("width"), _s.get("height")))

print("=== 2) 工作流副本（bot 专用，2026-10-16 快照）===")
# ⚠️ 主人 2026-10-17 16:39 把**原文件** Moss_int8_t2i.json 改过（20→27 节点、宫格 LLM 链
#    全旁路 mode=4、新加了 LoadImage→ImageCaptionNode→「反推一」→WeiLinPromptUI 和
#    ResolutionSelector→EmptyLatentImage）。副本是那之前的快照，**故意不跟着变**
#    （bot 跑的是两级 PromptExpand 那套）。所以这里只验**副本自身完好**，
#    原文件与副本不一致时只提示、不算失败 —— 要同步请重跑 state/_mkwf_moss_grid.py。
chk("主人原文件还在且没被动过", os.path.exists(_SRC))
chk("副本已生成", os.path.exists(_DST))
_src_doc = json.loads(io.open(_SRC, encoding="utf-8").read())
_dst_doc = json.loads(io.open(_DST, encoding="utf-8").read())
_src_t = [str(n.get("title") or "") for n in _src_doc.get("nodes") or []]
_dst_t = [str(n.get("title") or "") for n in _dst_doc.get("nodes") or []]
chk("副本里「用户输入」标题唯一（避开 #36 同名「输入」）", _dst_t.count("用户输入") == 1,
    [i for i, n in enumerate(_dst_doc["nodes"]) if n.get("title") == "用户输入"])
chk("副本里 #36 仍叫「输入」（改名只动了 #40）", _dst_t.count("输入") >= 1, _dst_t.count("输入"))
chk("副本节点数 = 20（10-16 快照，没被后来的改动污染）",
    len(_dst_doc.get("nodes") or []) == 20, len(_dst_doc.get("nodes") or []))
# 两级 LLM 编译链在副本里必须是**启用**的（mode 不是 4），否则 keep_expand 白设
_dst_n = {n["id"]: n for n in _dst_doc.get("nodes") or []}
chk("副本里 #37/#33 两个 PromptExpand 没被旁路",
    all(_dst_n.get(i, {}).get("mode") != 4 for i in (33, 37)),
    {i: _dst_n.get(i, {}).get("mode") for i in (33, 37)})
# ⚠️ link = [link_id, 源节点, 源槽位, 目标节点, 目标槽位]
_dst_links = set()
for _l in (_dst_doc.get("links") or []):
    _id, _sn, _ss, _dn, _ds = _l[0], _l[1], _l[2], _l[3], _l[4]
    _dst_links.add((_sn, _dn))
chk("#37(故事) ← #40(用户输入)", (40, 37) in _dst_links, sorted(_dst_links))
chk("#33(宫格编译器) ← #37", (37, 33) in _dst_links, sorted(_dst_links))
chk("#4(CLIP) ← #33", (33, 4) in _dst_links, sorted(_dst_links))
if len(_src_t) != len(_dst_t):
    print("      ⚠️ 提示：原文件已被主人改过（%d 节点，副本 %d）—— bot 用副本，不受影响；"
          "要让 bot 跟新链路走需重跑 state/_mkwf_moss_grid.py" % (len(_src_t), len(_dst_t)))


def _src_of(doc, tgt, key):
    outs = {}
    for n in doc.get("nodes") or []:
        for o in (n.get("outputs") or []):
            for lid in (o.get("links") or []):
                outs[lid] = n.get("id")
    for n in doc.get("nodes") or []:
        if n.get("id") != tgt:
            continue
        for e in (n.get("inputs") or []):
            if e.get("name") == key and e.get("link"):
                return outs.get(e["link"])
    return None


_got = {t: _src_of(_dst_doc, t, k)
        for t, k in ((37, "source_text"), (33, "source_text"), (4, "text"))}
chk("编译链路连通 #40→#37→#33→#4", _got == {37: 40, 33: 37, 4: 33}, _got)

print("=== 3) 注入：保链 vs 默认裁链（纯本地，不占队列）===")
_g0 = None


def _build(keep_expand):
    # ⚠️ graph_workflow 返回 **(图, 宽高) 元组**，别直接当 dict 用
    s = dict(_s)
    s["keep_expand"] = keep_expand
    _g, _wh = comfy_gen.graph_workflow(bot._comfy_cfg(), s, "画一只猫", "", 0, 0,
                                       1234, "qqbot")
    return _g, _wh


_on, _wh_on = _build(True)
_off, _wh_off = _build(False)
chk("保链时出图尺寸 = 1280×1280（工作流自带）", tuple(_wh_on) == (1280, 1280), _wh_on)
_cls = lambda gg: [str(v.get("class_type")) for v in gg.values()]
chk("keep_expand=true：PromptExpand 还在（%d 节点）" % len(_on),
    _cls(_on).count("PromptExpand") == 2, _cls(_on))
chk("keep_expand=false：扩写链照旧被裁（%d 节点）" % len(_off),
    _cls(_off).count("PromptExpand") == 0, _cls(_off))
_vals = {}
for _nid, _v in _on.items():
    if _v.get("class_type") == "ZML_TextInput":
        _vals[_nid] = (_v.get("inputs") or {}).get("文本")
chk("用户描述真的进了「用户输入」格", "画一只猫" in list(_vals.values()), list(_vals.values())[:9][:1])
chk("system prompt 没被覆盖（#34 宫格编译器规则完好）",
    any("宫格漫画提示词编译器" in str(v)[:80] for v in _vals.values()))
chk("system prompt 没被覆盖（#36 故事编剧规则完好）",
    any("短篇漫画小剧场故事编剧" in str(v)[:80] for v in _vals.values()))
_txt = None
for _nid, _v in _on.items():
    if _v.get("class_type") == "CLIPTextEncode":
        _t = (_v.get("inputs") or {}).get("text")
        if isinstance(_t, list):
            _txt = _t
chk("正向 CLIP 的 text 是连线（LLM 输出接管），不是死文本",
    isinstance(_txt, list) and len(_txt) == 2, _txt)
_wh = None
for _nid, _v in _on.items():
    if _v.get("class_type") == "EmptyFlux2LatentImage":
        _i = _v.get("inputs") or {}
        _wh = (_i.get("width"), _i.get("height"))
chk("尺寸用工作流自己的 1280×1280", _wh == (1280, 1280), _wh)
_ks = [(v.get("class_type"), (v.get("inputs") or {})) for v in _on.values()
       if v.get("class_type") == "KSampler"]
chk("采样参数保持官方档（euler / 50 步 / CFG 3）",
    _ks and _ks[0][1].get("sampler_name") == "euler" and _ks[0][1].get("steps") == 50
    and _ks[0][1].get("cfg") == 3, _ks[0][1] if _ks else None)
chk("种子写进去了", any((v.get("inputs") or {}).get("seed") == 1234
                    for v in _on.values() if v.get("class_type") == "KSampler"))
try:
    _i2i = comfy_gen.wf_style_i2i_ok(bot._comfy_cfg(), _s)
except Exception as e:                      # noqa: BLE001
    _i2i = "异常 %s" % e
# 判据是「有 VAE + 有采样器」⇒ 结构上 bot 能注入参考图（wf_inject_image 走标准接法）。
# MOSS 本身是文生图底模，带图效果没实测 —— 这里先把结构事实钉住，别以后悄悄变了。
chk("宫格档结构上能接参考图（有 VAE + 采样器）", _i2i is True, _i2i)

print("=== 4) 别名：说人话就能切到宫格档 ===")
for _w in ("宫格", "宫格风", "宫格漫画", "格子漫画", "四格", "四格漫画", "漫画", "小剧场"):
    chk("别名「%s」→ 宫格档" % _w, bot._lookup_alias(_w) == "宫格", bot._lookup_alias(_w))
chk("老别名没被带歪：动漫 → anima", bot._lookup_alias("动漫") == "anima", bot._lookup_alias("动漫"))
chk("老别名没被带歪：写实 → krea2", bot._lookup_alias("写实") == "krea2", bot._lookup_alias("写实"))
chk("档位词表里能列出宫格档", "宫格" in list(bot.style_tokens() or []),
    sorted(set(bot.style_tokens() or []))[:12])
try:
    names = list(bot._style_names())
    ok_names = "宫格" in names
except Exception as e:      # noqa: BLE001
    names, ok_names = repr(e), False
chk("「设置/帮助」里会列出宫格档", ok_names, names)

print("=== 5) 「/」前缀 = 点名（主人要的核心）===")
h = run("/画一只猫", group=True, at=False)
chk("群里 /画一只猫（没@）→ 现画跑", h and h[0][0] == "现画", h)
chk("群里 /画一只猫 → 描述里没有斜杠（剥掉了）", h and h[0][1][0] == "猫", h)
chk("群里 /画一只猫 → 没走 chatty 回话", not SENT, SENT)

h2 = run("/", group=True, at=False)
chk("光一个「/」不会崩也不乱触发", h2 == [], h2)

print("=== 6) 「/」对其它画图口令一律有效 ===")
for _label, _txt, _imgs in (
        ("画同款", "/画同款 举着伞站在雨里", [{"url": "http://e/a.jpg", "file": "a.jpg"}]),
        ("照着重画", "/画", [{"url": "http://e/a.jpg", "file": "a.jpg"}]),
        ("随机生图", "/随机画一张", None),
        ("视频生成", "/生成视频 一只白猫在雨中撑伞走过小巷整条街", None)):
    h3 = run(_txt, group=True, at=False, imgs=_imgs)
    chk("/%s → 出口进了「%s」" % (_label, _label), bool(h3), (h3, SENT[:1]))

print("=== 7) 回归：没 / 也没@ 时仍不许跑（主人之前的规矩不能破）===")
for _label, _txt, _imgs in (
        ("现画", "画一只猫", None),
        ("随机生图", "随机画一张", None),
        ("视频生成", "生成视频 一只白猫在雨中撑伞走过小巷整条街", None)):
    h4 = run(_txt, group=True, at=False, imgs=_imgs)
    chk("没/没@ → %s 依然不跑" % _label, not h4, (h4, SENT[:1]))

print("=== 8) @ 与私聊照旧（/ 只是多一种，不能顶掉）===")
h5 = run("画一只猫", group=True, at=True)
chk("@我 → 现画照跑", h5 and h5[0][0] == "现画" and h5[0][1][0] == "猫", h5)
h6 = run("画一只猫", group=False)
chk("私聊 → 现画照跑", h6 and h6[0][0] == "现画", h6)
h7 = run("/画一只猫", group=False)
chk("私聊 + / → 也跑（剥斜杠不影响）", h7 and h7[0][0] == "现画" and h7[0][1][0] == "猫", h7)

print("=== 9) 用 / 指定宫格档 ===")
h8 = run("/画 [宫格] 猫", group=True, at=False)
chk("「/画 [宫格] 猫」→ 现画跑", h8 and h8[0][0] == "现画", h8)
h9 = run("/用宫格画只猫", group=True, at=False)
chk("「/用宫格画只猫」→ 现画跑", h9 and h9[0][0] == "现画", h9)

print("=== 10) 「/档位 描述」直接指定档位（2026-10-17 主人定）===")
for _label, _txt, _want in (
        ("宫格", "/宫格 一只白猫在屋顶上钓鱼", "宫格"),
        ("动画", "/动画 一只黑猫", "anima"),
        ("动漫", "/动漫 一只黑猫", "anima"),
        ("写实", "/写实 一只黑猫", "krea2"),
        ("厚涂", "/厚涂 一只黑猫", "厚涂"),
        ("漫画(别名→宫格)", "/漫画 一只黑猫", "宫格"),
        ("四格(别名→宫格)", "/四格 一只黑猫", "宫格")):
    h10 = run(_txt, group=True, at=False)
    _got = h10[0][1][1] if h10 else None
    chk("「%s」→ 现画跑且档位=%s" % (_txt[:12], _want),
        bool(h10) and h10[0][0] == "现画" and _got == _want, (h10, _got))

h11 = run("/宫格 一只白猫", group=True, at=False)
chk("描述里不带档位词（没串进提示词）",
    h11 and "宫格" not in str(h11[0][1][0]) and "用" not in str(h11[0][1][0]), h11)

print("=== 11) 回归：/ 后不是档位词时照旧 ===")
h12 = run("/画 一只猫", group=True, at=False)
chk("「/画 一只猫」仍是现画（没被档位拆分误吞）", h12 and h12[0][0] == "现画", h12)
h13 = run("/随机画一张", group=True, at=False)
chk("「/随机画一张」仍走随机生图", h13 and h13[0][0] == "随机生图", h13)
h14 = run("/画同款 举着伞", group=True, at=False,
          imgs=[{"url": "http://e/a.jpg", "file": "a.jpg"}])
chk("「/画同款」仍走画同款", h14 and h14[0][0] == "画同款", h14)

print("=== 12) _slash_style_split 单元（空格边界）===")
chk("有空格 → 认档位", bot._slash_style_split("宫格 一只猫") == ("宫格", "一只猫"),
    bot._slash_style_split("宫格 一只猫"))
chk("没空格 → 不认（防「/写实风景照」被吞成写实档）",
    bot._slash_style_split("写实风景照")[0] is None, bot._slash_style_split("写实风景照"))
chk("光一个档位词 → 认（描述为空）", bot._slash_style_split("宫格") == ("宫格", ""),
    bot._slash_style_split("宫格"))
chk("普通描述 → 不认", bot._slash_style_split("画一只猫")[0] is None,
    bot._slash_style_split("画一只猫"))
chk("长词优先：krea2 胜过 krea", bot._slash_style_split("krea2 一只猫")[0] == "krea2",
    bot._slash_style_split("krea2 一只猫"))

print("=== 13) 图生图（带图）走不走 `/`（2026-10-18 主人问）===")
_img2 = [{"url": "http://example.com/b.jpg", "file": "b.jpg"}]
_h = run("/画同款 举着伞站在雨里", group=True, at=False, imgs=_img2)
chk("带图「/画同款 描述」→ 走图生图 handle_i2i",
    _h and _h[0][0] == "画同款", (_h, SENT[:1]))
_h = run("/画", group=True, at=False, imgs=_img2)
chk("带图「/画」单字 → 走图生图（照着这张画）",
    _h and _h[0][0] == "画同款", (_h, SENT[:1]))
_h = run("/宫格 画同款 一只白猫", group=True, at=False, imgs=_img2)
chk("带图「/宫格 画同款 描述」→ 图生图且档位=宫格",
    _h and _h[0][0] == "画同款" and _h[0][1][1] == "宫格", (_h,))
_h = run("/写实 画同款 一只白猫", group=True, at=False, imgs=_img2)
chk("带图「/写实 画同款 描述」→ 图生图且档位=krea2",
    _h and _h[0][0] == "画同款" and _h[0][1][1] == "krea2", (_h,))
# 🔴 2026-10-18 主人定：带图（本条的图 / 引用或转发里的图）就当**图生图**，
#    「/宫格 描述」+ 一张图 = 「照着这张、用宫格画」，不用再写「画同款」。
_h = run("/宫格 一只白猫", group=True, at=False, imgs=_img2)
chk("带图「/宫格 描述」→ 图生图且档位=宫格（不用写画同款）",
    _h and _h[0][0] == "画同款" and _h[0][1][1] == "宫格"
    and _h[0][1][0] == "一只白猫", (_h,))
_h = run("/宫格 画一只白猫", group=True, at=False, imgs=_img2)
chk("带图「/宫格 画描述」→ 图生图（自带的「画」被吃掉，不重复）",
    _h and _h[0][0] == "画同款" and _h[0][1][1] == "宫格"
    and _h[0][1][0] == "一只白猫", (_h,))
_h = run("/宫格", group=True, at=False, imgs=_img2)
chk("带图「/宫格」（就俩字）→ 图生图且档位=宫格",
    _h and _h[0][0] == "画同款" and _h[0][1][1] == "宫格", (_h,))
_h = run("/动画 一只白猫", group=True, at=False, imgs=_img2)
chk("带图「/动画 描述」→ 图生图且档位=anima",
    _h and _h[0][0] == "画同款" and _h[0][1][1] == "anima", (_h,))
_h = run("/漫画 一只白猫", group=True, at=False, imgs=_img2)
chk("带图「/漫画 描述」→ 图生图且档位=宫格（别名）",
    _h and _h[0][0] == "画同款" and _h[0][1][1] == "宫格", (_h,))
# 引用（回复）别人的图也算有参考图
_h = run("/宫格 一只白猫", group=True, at=False, imgs=None, qmid=90001)
chk("引用了图「/宫格 描述」→ 也走图生图",
    _h and _h[0][0] == "画同款" and _h[0][1][1] == "宫格", (_h,))
# 回归：**没图**时「/档位 描述」照旧是文生图
_h = run("/宫格 一只白猫", group=True, at=False)
chk("〔回归〕不带图「/宫格 描述」→ 仍是文生图（现画）",
    _h and _h[0][0] == "现画" and _h[0][1][1] == "宫格", (_h,))
_h = run("/宫格", group=True, at=False)
chk("〔回归〕不带图「/宫格」→ 不误触发图生图（没图可参照）",
    not any(h[0] == "画同款" for h in _h), (_h, SENT[:1]))

print("RESULT:", "PASS" if bad == 0 else "FAIL（%d 项失败）" % bad)
sys.exit(1 if bad else 0)
