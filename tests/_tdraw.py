# -*- coding: utf-8 -*-
"""现画链路离线自测：不连 NapCat、不连 ComfyUI，全部打桩。只验 bot.py 里的接线。"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import json
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
import bot          # noqa: E402
from qbot import comfy_gen    # noqa: E402

_orig_req_at = bot.CFG.get("comfyui", {}).get("require_addressing")
bot.CFG.setdefault("comfyui", {})["require_addressing"] = False

# ⚠️ 2026-10-25 修（这个回归件从 2026-10-24 起就是**废的**，一跑必 IndexError）：
#    10-24 主人把生图拆成两条链路，按群白名单开关（config 的 `draw.comfyui/moss.groups`）。
#    真配置里 `draw.comfyui.groups` 是 `[]`、只有 `draw.moss.groups` 有群
#    ⇒ `draw_engine(GID)` 返回 "moss" ⇒ 这个**测 ComfyUI 链路**的文件里
#    一次都不会调 `comfy_gen.submit` ⇒ `COMFY_CALLS` 空 ⇒ `COMFY_CALLS[-1]` 直接崩。
#    修法：**把本文件要测的那条链路显式打开**（把 GID 塞进 draw.comfyui.groups，
#    同时清掉 moss，保证引擎唯一），退出时还原。这样测的仍是 ComfyUI 这条，
#    不受真配置里「这个群现在用哪条」影响。
_orig_draw_cfg = json.loads(json.dumps(bot.CFG.get("draw") or {}))


def _force_comfy_engine():
    d = bot.CFG.setdefault("draw", {})
    d["comfyui"] = {"groups": [123450001]}
    d["moss"] = {"groups": []}


def _restore_draw_cfg():
    bot.CFG["draw"] = json.loads(json.dumps(_orig_draw_cfg))


_force_comfy_engine()

OUT = []


def fake_send_group(gid, text):
    OUT.append(("text", gid, text))
    return True


def fake_send_segments(gid, segs):
    OUT.append(("segs", gid, segs))
    return True


bot.send_group = fake_send_group
bot.send_segments = fake_send_segments
bot.maybe_slang_extract = lambda gid: None
bot.sim_allowed = lambda *a, **kw: False   # 2026-10-19: 多了 uid 参数，桩收 *a/**kw
bot.comfy_up = lambda timeout=3: True
comfy_gen.boot = lambda c: True
COMFY_CALLS = []

# 打桩的模型嘴：按 system 提示词分辨这一步是探测、查证还是润色，全程不打云端接口
SUBJECT_HIT = "LOOK: long black hair in a high ponytail, blue eyes, white and blue idol costume"


def fake_llm(msgs):
    sys = msgs[0].get("content", "") if msgs else ""
    q = msgs[-1].get("content", "") if msgs else ""
    if "挑出" in sys:                       # SUBJECT_PROBE_SYS
        return "花海咲季" if "花海咲季" in q else "无"
    if "设定资料" in sys:                   # 润色：资料已经拼进来了
        return "RENDER(" + q.replace("\n", " ")[:180] + ")"
    return "plain-en-prompt: " + q[:24]


WEB_Q = []


def fake_web(msgs, query=""):
    WEB_Q.append(query)
    return ("WHO: 《学园偶像大师》里的偶像\n" + SUBJECT_HIT + "\n（搜了：%s）" % query)


def fake_book():
    return comfy_gen.SubjectBook(path=str(STATE_T), enabled=True, max_names=2, budget_sec=10)


STATE_T = __import__("pathlib").Path(__file__).with_name("state") / "_tsubjects.json"
STATE_T.parent.mkdir(exist_ok=True)
def _enh_on():
    return bool(bot.CFG["comfyui"].get("enhance", True))


bot._draw_llm = lambda style=None: (fake_llm if _enh_on() else None)
bot._draw_webllm = lambda style=None: (fake_web if _enh_on() else None)
bot._subject_book = fake_book

def fake_generate(cfg, prompt, style=None, llm=None, out_dir=None, timeout=None,
                  landscape=False, book=None, web_llm=None, lookup=True,
                  image=None, denoise=None, aspect=None):
    st = ((cfg.get("styles") or {}).get(style or cfg.get("default_style") or "") or {})
    engine = st.get("engine") or "krea2"
    brief, names = "", []
    if book is not None and lookup and cfg.get("enhance", True):
        brief, names = book.lookup(prompt, probe=llm, web=web_llm)
    used = comfy_gen.enhance(prompt, llm, engine, brief=brief) if cfg.get("enhance", True) else prompt
    COMFY_CALLS.append({"prompt": prompt, "used": used, "style": style, "landscape": landscape,
                        "engine": engine, "out": str(out_dir), "lookup": lookup,
                        "aspect": aspect,
                        "subject": names, "i2i": image, "denoise": denoise})
    if prompt == "炸":
        raise comfy_gen.ComfyError("Path was C:\\ComfyUI\\models but broke")
    size = [1248, 2304] if st.get("vsr", True) is not False else [832, 1536]
    return {"file": str(out_dir) + "/fake.png", "name": "fake", "seconds": 12.3,
            "prompt_used": used, "size": size, "comfy_name": "qqbot_00001_.png",
            "style": style or cfg.get("default_style"), "engine": engine, "note": "",
            "subject": names, "i2i": bool(image), "denoise": denoise or 1.0}


bot.comfy_gen.generate = fake_generate
bot.comfy_gen.up = lambda c, timeout=3: True
threading.Thread(target=bot.draw_worker, daemon=True).start()

GID = 123450001


_mid = [1000]


def event(text, uid=123450005, role="admin", mid=None, ats_self=False):
    _mid[0] += 1
    mid = _mid[0]
    msg = []
    if ats_self:
        msg.append({"type": "at", "data": {"qq": str(bot.SELF_ID or 123450002), "name": "bot"}})
    msg.append({"type": "text", "data": {"text": text}})
    return {"self_id": 123450002, "post_type": "message", "message_type": "group",
            "group_id": GID, "message_id": mid, "user_id": uid,
            "sender": {"role": role, "card": "测试%s" % role}, "message": msg}


def wait(n=1.5):
    time.sleep(n)


def show(tag):
    print("\n=== %s ===" % tag)
    while OUT:
        kind, gid, body = OUT.pop(0)
        if kind == "text":
            print("  [文字->%s] %s" % (gid, body))
        else:
            desc = " + ".join(s["type"] == "image" and "图:%s" % s["data"]["file"].split("/")[-1]
                              or (s["type"] == "reply" and "引用%s" % s["data"]["id"] or s["data"].get("text"))
                              for s in body)
            print("  [分段->%s] %s" % (gid, desc))


def drain():
    OUT.clear()


def settle(sec=10):
    """等 worker 把队列排空，别让它上一条的润色延迟污染断言。"""
    end = time.time() + sec
    while time.time() < end:
        if _idle():
            return True
        time.sleep(0.05)
    return False


def wait_calls(min_calls=1, timeout=3.0):
    """高效等待打桩 worker 产出，避免无谓 sleep(8)。"""
    end = time.time() + timeout
    while len(COMFY_CALLS) < min_calls and time.time() < end:
        time.sleep(0.02)


def _idle():
    return not _busy() and _qempty()


def _busy():
    return bool(bot._draw_state.get("busy"))


def _qempty():
    try:
        return bot._draw_q.empty()
    except Exception:
        return True


print("CFG comfyui.members_allowed =", bot.CFG["comfyui"].get("members_allowed"))

bot.handle_group_message(event("画：一只戴帽子的猫"))
wait(6)
show("① 「画：xxx」口令 → 排队 → 出图进群")
print("  comfy 调用:", COMFY_CALLS[-1] if COMFY_CALLS else None)

drain()
bot.handle_group_message(event("画图 赛博朋克少女"))
wait(2.5)
show("② 紧跟着再来一张（应被冷却闸挡下）")

drain()
bot._draw_state["last"] = 0.0
bot.handle_group_message(event("帮我画张日落", uid=999111, role="member"))
wait(6)
show("③ 普通群友「帮我画张日落」（members_allowed=true 应放行）")

drain()
bot.CFG["comfyui"]["members_allowed"] = False
bot._draw_state["last"] = 0.0
bot.handle_group_message(event("画：猫", uid=999111, role="member"))
wait(2.5)
show("④ members_allowed=false 时群友应被拒")
bot.CFG["comfyui"]["members_allowed"] = True

drain()
bot.handle_group_message(event("这画面太好看了", uid=999111, role="member"))
bot.handle_group_message(event("今天天气不错", uid=999111, role="member"))
wait(2.5)
show("⑤ 正常闲聊不该触发画图（期望：没有任何输出）")

drain()
bot.handle_group_message(event("画图 状态"))
wait(2.5)
show("⑥ 「画图 状态」")

drain()
bot.handle_group_message(event("画图 启动", uid=999111, role="member"))
wait(2.5)
show("⑦ 群友发「画图 启动」（应要管理员权限）")

drain()
bot._draw_state["last"] = 0.0
bot.handle_group_message(event("画：炸"))
settle()
show("⑧ 出图报错（本机路径必须被抹掉）")

drain()
bot._draw_state["last"] = 0.0        # ⑧ 失败也上了冷却，这里清掉再验提交链路
r = bot.action_generate(GID, "穿汉服的白虎鲸少女，海边夕阳", "", 0)
print("\n=== ⑨ action_generate（agent/MCP 口）===\n ", r)
settle()
show("⑨ 续：worker 把图画好发进群")

drain()
r = bot.action_generate(812100000, "猫")
print("\n=== ⑩ 非白名单群现画 ===\n ", r)
drain()
r = bot.action_generate(GID, "")
print("=== ⑪ 空 prompt ===\n ", r)

drain()
bot._draw_state["last"] = 0.0
bot._draw_state["count"] = 0
i = bot.comfy_info()
print("\n=== ⑫ comfy_info / action_status.comfy ===\n ", i)
print("  status.comfy =", bot.action_status().get("comfy"))

# 新档位口令：不再白打云端润色接口
bot.CFG["comfyui"]["enhance"] = False
drain()
bot._draw_state["last"] = 0.0
bot.handle_group_message(event("快画：一只赛博朋克猫"))
settle()
print("\n=== ⑬ 「快画」应走 Krea2 轻量档 fast（style=fast / engine=krea2）===\n ",
      COMFY_CALLS[-1] if COMFY_CALLS else None)

drain()
bot._draw_state["last"] = 0.0
bot.handle_group_message(event("画：雪山上的经幡，横图"))
settle()
print("=== ⑭ 「横图」应置 landscape 且不进提示词 ===\n ", COMFY_CALLS[-1] if COMFY_CALLS else None)

drain()
bot._draw_state["last"] = 0.0
bot.handle_group_message(event("画：写实的老年渔夫"))
settle()
print("=== ⑮ 写实题材仍走默认质量档（style 应为 None）===\n ", COMFY_CALLS[-1] if COMFY_CALLS else None)

print("\n=== ⑯ parse_draw 直读 ===")
for s in ["画：猫", "快画：猫", "帮我画个赛博朋克少女", "画一张日落 横图", "这画面太好看",
          "快画：写实风格的猫", "画完了吗", "@bot 画只猫", "速涂个头像",
          "不查：花海咲季", "画：明日方舟的黍 别查", "画：刘备 直接画"]:
    print("   %-22r -> %s" % (s, bot.parse_draw(s, addressed=True)))

print("\n=== ⑰ 引擎白名单 {krea2, anima, wf}：旧档名 anime/real/sd15 要落回默认，表里不许有别的引擎 ===")
_c = bot.CFG["comfyui"]
# 2026-09-18 起「写实档实现」旋钮可把 krea2 换成工作流档（engine=wf），写实风景本来就是 wf 档 —— 白名单补 wf
_OK_ENGINES = ("krea2", "anima", "wf")
_bad = [n for n, v in (_c.get("styles") or {}).items()
        if (v.get("engine") or "krea2").lower() not in _OK_ENGINES]
print("   白名单外的档位（应为 []）:", _bad)
print("   default_style(应 anima):", _c.get("default_style"))
for stale in ("anime", "real", "sd15", ""):
    print("   style=%-8r -> %s / engine=%s" % (
        stale, comfy_gen._style(_c, stale)[0],
        (comfy_gen._style(_c, stale)[1].get("engine") or "krea2")))

# ---------------- 主体查证（先查设定再画） ----------------
bot.CFG["comfyui"]["enhance"] = True
if STATE_T.exists():
    STATE_T.unlink()

drain()
bot._draw_state["last"] = 0.0
bot.handle_group_message(event("画：花海咲季在演唱会舞台上"))
settle()
c = COMFY_CALLS[-1]
print("\n=== ⑱ 带角色名：应先查设定、资料喂进润色、群里说一句查了谁 ===")
print("   lookup(应 True) =", c["lookup"], "| subject(应['花海咲季']) =", c["subject"])
print("   搜过:", WEB_Q)
print("   润色吃到的提示词:", c["used"][:200])
print("   资料进去了吗(应 True):", "long black hair" in c["used"])
print("   群消息:", [t for k, g, t in OUT if k == "text"][-1:])

drain()
WEB_Q.clear()
bot._draw_state["last"] = 0.0
bot.handle_group_message(event("不查：花海咲季在演唱会舞台上"))
settle()
c = COMFY_CALLS[-1]
print("\n=== ⑲ 「不查：」应跳过查证 ===")
print("   lookup(应 False) =", c["lookup"], "| subject(应[]) =", c["subject"],
      "| 又搜了吗(应 0) =", len(WEB_Q))
print("   描述里开关词被摘掉了吗(应 True):", "不查" not in c["prompt"], "| prompt =", c["prompt"])
print("   群里不该再提查设定(应 True):", "设定" not in "".join(t for k, g, t in OUT if k == "text"))

drain()
WEB_Q.clear()
bot._draw_state["last"] = 0.0
bot.handle_group_message(event("画：花海咲季，横图"))
settle()
print("\n=== ⑳ 查过的角色应命中缓存，不再联网 ===")
print("   又搜了吗(应 0) =", len(WEB_Q), "| subject =", COMFY_CALLS[-1]["subject"],
      "| landscape =", COMFY_CALLS[-1]["landscape"])

drain()
bot.handle_group_message(event("画图 重查"))
settle()
print("\n=== ㉑ 「画图 重查」应清掉设定缓存 ===")
print("   回话:", [t for k, g, t in OUT if k == "text"])
print("   缓存文件还在吗(应 False):", STATE_T.exists())
WEB_Q.clear()
bot._draw_state["last"] = 0.0
bot.handle_group_message(event("画：花海咲季在演唱会舞台上"))
settle()
print("   清完再画应重新搜:", WEB_Q)

# ---------------- ㉒ 画同款（反推 → 认人 → 图生图） ----------------
import pathlib        # noqa: E402
from qbot import reverser       # noqa: E402

_orig = (reverser.run, reverser.identify, bot._fetch_rev_image, bot.CFG.get("reverse"))
reverser.run = lambda path, cfg, engine=None: (
    True, "a girl with long hair, standing, upper body", "vl·打桩")
_ident_calls = [0]


def _fake_ident(path, cfg, lookup=None):
    _ident_calls[0] += 1
    return {
        "known": True, "name": "Test Char", "series": "Test Series", "confidence": 0.9,
        "tags": "test char, test series, blue eyes", "brief": "", "via": "打桩"}
bot._fetch_rev_image = lambda d: pathlib.Path(str(d.get("file")))
bot.CFG["reverse"] = dict(_orig[3] or {}, i2i=True, identify=True)
reverser.identify = _fake_ident
bot.CFG["comfyui"]["enabled"] = True


def event_img(text, img="fake_ref.png"):
    """带一张图片段的消息（画同款要图）。"""
    ev = event(text)
    ev["message"] = [{"type": "image", "data": {"file": img, "url": ""}}] + ev["message"]
    return ev


try:
    bot._rev_cache.clear()
    drain()
    bot._draw_state["last"] = 0.0
    bot.handle_group_message(event_img("画同款 撑着一把红伞站在雨里的街道上"))
    wait_calls(1, timeout=3.0)
    settle(5)
    c = COMFY_CALLS[-1] if COMFY_CALLS else {}
    print("\n=== ㉒ 画同款：反推 → 认人 → 图生图 ===")
    print("   i2i(应是参考图) =", c.get("i2i"))
    print("   denoise(加了描述应 0.75) =", c.get("denoise"))
    print("   提示词带角色 tag(应 True) =", "test char" in (c.get("prompt") or ""))
    print("   提示词带补充描述(应 True) =", "红伞" in (c.get("prompt") or ""))
    print("   群消息:", [t for k, g, t in OUT if k == "text"])
    print("   配图说明含「照着参考图」(应 True) =",
          any("照着参考图" in str(s) for k, g, s in OUT if k == "segs"))

    drain()
    bot._draw_state["last"] = 0.0
    n_before = len(COMFY_CALLS)
    bot.handle_group_message(event_img("画同款"))
    wait_calls(n_before + 1, timeout=3.0)
    settle(5)
    c2 = COMFY_CALLS[-1] if COMFY_CALLS else {}
    print("   没加描述时 denoise(应 0.6) =", c2.get("denoise"))

    # ㉓ 档位指定（2026-09-15 加）：主人发「用krea2画同款」时参考图丢了 ——
    # I2I_RE 是 ^ 锚定的，带「用xx」前缀整句落到现画口令、画成纯文生图。这里守门。
    drain()
    COMFY_CALLS.clear()
    bot._draw_state["last"] = 0.0
    bot._draw_state["count"] = 0
    ident_before = _ident_calls[0]
    bot.handle_group_message(event_img("用krea2画同款 举起手来"))
    wait_calls(1, timeout=3.0)
    settle(5)
    c3 = COMFY_CALLS[-1] if COMFY_CALLS else {}
    print("\n=== ㉓ 画同款指定档位 ===")
    print("   指定 krea2 时 style(应 krea2) =", c3.get("style"))
    print("   参考图还在吗(应 fake_ref.png) =", c3.get("i2i"))
    print("   krea2 档不加 tag：提示词无角色串(应 False) =",
          "test char" in (c3.get("prompt") or ""))
    print("   krea2 档跳过认人：这轮认人调用增量(应 0) =",
          _ident_calls[0] - ident_before)
    print("   denoise(加了描述应 0.75) =", c3.get("denoise"))

    drain()
    COMFY_CALLS.clear()
    bot._draw_state["last"] = 0.0
    bot._draw_state["count"] = 0
    bot.handle_group_message(event_img("用Anima工作流画同款"))
    wait_calls(1, timeout=3.0)
    settle(5)
    c4 = COMFY_CALLS[-1] if COMFY_CALLS else {}
    # 2026-09-17 语义更新：Anima工作流 2026-09-16 起结构上能吃参考图（wf_style_i2i_ok=True），
    # 点名它画同款应正常带图走，不再回落/回绝；但它是 wf 引擎 → 不认人不加 tag。
    print("   点名工作流档 → style(应 Anima工作流) =", c4.get("style"))
    print("   参考图仍在(应 fake_ref.png) =", c4.get("i2i"))
    print("   不该有「接不了参考图」提示(应 False) =",
          any("接不了参考图" in t for k, g, t in OUT if k == "text"))
    print("   wf 档不加 tag：提示词无角色串(应 False) =",
          "test char" in (c4.get("prompt") or ""))
    print("   「用anima画同款」该落到引擎档 anima =",
          bot.strip_style_prefix("用anima画同款", resolver=bot.resolve_i2i_style)[0])

    drain()
    COMFY_CALLS.clear()
    bot._draw_state["last"] = 0.0
    bot._draw_state["count"] = 0
    bot.handle_group_message(event("用你的画风画只猫"))
    time.sleep(2)
    settle(30)
    c5 = COMFY_CALLS[-1] if COMFY_CALLS else {}
    print("   不误伤：「用你的画风画只猫」走普通现画、无参考图(应 None) =", c5.get("i2i"))
    print("   档位没被误认(应 None) =", c5.get("style"))

    # ㉔ 中文画风别名（2026-09-15 加）：主人要「一个写实画风、一个动漫画风」 ——
    # 口语说「写实」「动漫」就该分别落 krea2 / anima，且不能啃掉正文里的「动漫展」「动漫杂志」。
    print("\n=== ㉔ 写实/动漫别名 ===")
    # ⚠️ 2026-10-25 修：「用快档画只猫 → fast」这条**已经过期**了。
    #    `fast` 档在 2026-10-13 后就不再出现在 config 的 `comfyui.styles` 里
    #    （现在只有 anima / krea2 / 厚涂 / 宫格），`strip_style_prefix` 按 styles 反查，
    #    自然剥不出 fast ⇒ 返回 None。这不是 bug，是主人的档位配置变了。
    #    改成断言「不认识的老档名要老实返回 None，别瞎猜」，这才是现在该测的行为。
    for said, want in [("用写实画只猫", "krea2"), ("用动漫画只猫", "anima"),
                       ("动漫画只猫", "anima"), ("写实画一张城市夜景", "krea2"),
                       ("二次元 画个初音", "anima"), ("画[动漫]猫", "anima"),
                       ("画只猫 用动漫档", "anima"), ("用快档画只猫", None)]:
        got, rest = bot.strip_style_prefix(said)
        print("   %-18s → %-6s (应 %-6s) %s | 剩 %r"
              % (said, got, want, "OK" if got == want else "!!", rest))
    for said in ["画个动漫展的海报", "画动漫杂志封面", "用你的画风画只猫", "画只猫"]:
        got, rest = bot.strip_style_prefix(said)
        print("   不误伤 %-20s → 档=%s 原话保持=%s" % (said, got, rest == said))

    # 「用动漫画同款」要真吃到 anima 引擎档（能吃参考图），不能掉进工作流档
    drain()
    COMFY_CALLS.clear()
    bot._draw_state["last"] = 0.0
    bot._draw_state["count"] = 0
    bot.handle_group_message(event_img("用动漫画同款"))
    wait_calls(1, timeout=3.0)
    settle(5)
    c6 = COMFY_CALLS[-1] if COMFY_CALLS else {}
    print("   『用动漫画同款』style(应 anima) =", c6.get("style"))
    print("   参考图还在吗(应 fake_ref.png) =", c6.get("i2i"))

    # ㉕ 真档名直接接指令动词（2026-09-16 加）：「Anima画…」「krea2画…」「Anima工作流画…」
    # 以前句首副词形态只认中文别名，拉丁档名剥不掉 → 整句掉进聊天大脑(agent)，
    # 既慢 15~20 秒，还被当成聊天内容处理。派发必须自己吃掉，不该交给模型判断。
    print("\n=== ㉕ 真档名直接接动词 ===")
    # ⚠️ 2026-10-25 修：「Anima工作流」也已不在 `comfyui.styles` 里（同上，档位配置变了），
    #    所以改成断言「剥不掉就返回 None」——保证它不会瞎猜一个不存在的档。
    for said, want in [("Anima画只猫", "anima"), ("anima画只猫", "anima"),
                       ("krea2画只猫", "krea2"), ("Anima工作流画只猫", None),
                       ("动漫画只猫", "anima"), ("写实画一张城市夜景", "krea2")]:
        got, rest = bot.strip_style_prefix(said)
        print("   %-20s → %-10s (应 %-10s) %s | 剩 %r"
              % (said, got, want, "OK" if got == want else "!!", rest))
    for said in ["画个动漫展的海报", "画动漫杂志封面", "用你的画风画只猫",
                 "动漫展里的初音", "画只猫"]:
        got, rest = bot.strip_style_prefix(said)
        print("   不误伤 %-20s → 档=%s 原话保持=%s" % (said, got, rest == said))
    _d = bot.parse_draw("Anima画只猫", addressed=True)
    print("   parse_draw('Anima画只猫') 进画图链路(应不是 None) =", _d)
    for said, want in [("anima图生图", "anima"), ("动漫图生图", "anima"),
                       ("用anima画同款", "anima")]:
        got, rest = bot.strip_style_prefix(said, resolver=bot.resolve_i2i_style)
        hit = bool(bot.I2I_RE.match(rest))
        print("   %-18s → 档=%-6s 剩=%-8r 认成画同款=%s %s"
              % (said, got, rest, hit, "OK" if (got == want and hit) else "!!"))
finally:
    (reverser.run, reverser.identify, bot._fetch_rev_image) = _orig[:3]
    if _orig[3] is None:
        bot.CFG.pop("reverse", None)
    else:
        bot.CFG["reverse"] = _orig[3]
    if _orig_req_at is not None:
        bot.CFG.setdefault("comfyui", {})["require_addressing"] = _orig_req_at

# ---------------- ㉖ 成品 tag 串 / 英文串直送工作流（2026-09-18 主人定） ----------------
# 主人踩到：发「画explicit, a smartphone screenshot…」被当成聊天送去给 AI 大脑，
# 云端模型先审一遍再回「我无法满足你的要求」——而审核闸 09-13 就撤了，不该有这道关。
# 定案：写好的串（成品 tag 串 / 纯英文）直接进现画链路，不查证、不润色。
print("\n=== ㉖ 成品串 / 英文串直送工作流 ===")
for said, addr, want_what, want_nolook in [
        ("画explicit, a smartphone screenshot showing a dark-skinned woman", True,
         "explicit, a smartphone screenshot showing a dark-skinned woman", True),
        ("hatsune miku, vocaloid, teal eyes", True, "hatsune miku, vocaloid, teal eyes", True),
        ("@free_style_(yohan1754), hatsune miku, vocaloid, long twintails", True,
         "hatsune miku, vocaloid, long twintails", True),
        ("画hatsune miku, vocaloid, teal eyes", True, "hatsune miku, vocaloid, teal eyes", True),
        ("画个初音未来", True, "初音未来", False)]:
    r = bot.parse_draw(said, addressed=addr)
    got = None if not r else (r[0], r[3])
    ok = bool(r) and r[0] == want_what and r[3] is want_nolook
    print("   %-52s → %s %s" % (repr(said)[:52], "OK " if ok else "!! ",
                                "拿到 %r nolook=%s" % (r[0][:34], r[3]) if r else "None（掉进聊天大脑）"))
# 不误伤：群里没@我时，裸串/中文闲聊都不该开显卡
for said in ["hatsune miku, vocaloid, teal eyes", "画得真好", "画面太好看了", "画explicit test"]:
    r = bot.parse_draw(said, addressed=False)
    print("   不误伤 %-40s → %s %s" % (repr(said)[:40], "OK " if r is None else "!! ",
                                      "None" if r is None else r[0][:30]))

# ---------------- ㉗ 超长成品串不能被长度闸挡掉（2026-09-18 主人踩到） ----------------
# 以前 parse_draw 开头硬闸 `len(t) <= 1000` 和软闸 what_len_max 都是 1000：
# danbooru 成品串一两千字很常见，超了就整条掉出画图链路、被当聊天送进 AI 大脑，
# 云端模型先审一遍再拒答 —— 主人反馈成"发 tag 还是被审核然后说不给生成"。
# 定案：硬闸 = max(4000, what_len_max*2)（"根本不像指令"的兜底，超了才 None）；
#       软闸 = what_len_max（默认 2000）→ 回一句明确提示，**绝不静默掉进 AI 大脑**。
print("\n=== ㉗ 超长成品串不被长度闸挡掉 ===")
_LONG = ("1girl, solo, long hair, blue eyes, school uniform, smile, standing, "
         "outdoor, cherry blossoms, sunlight, detailed background, masterpiece, best quality")


def _mk(n):
    s = _LONG
    while len(s) < n:
        s += ", " + _LONG
    return s[:n]


_wlm = bot._what_len_max()
print("   what_len_max(应 2000) =", _wlm)
for n in (900, 1200, _wlm):
    r = bot.parse_draw("画" + _mk(n), addressed=True)
    # 尾部逗号/空格会被 strip 掉几个字，差 5 以内都算对
    ok = isinstance(r, tuple) and abs(len(r[0]) - n) <= 5
    print("   画 + %-5d 字 → %s %s" % (n, "OK " if ok else "!! ",
          ("拿到 %d 字 nolook=%s" % (len(r[0]), r[3])) if isinstance(r, tuple) else r))
r = bot.parse_draw("画" + _mk(_wlm + 50), addressed=True)
print("   超软闸 %d 字(应 draw_too_long) = %s %s"
      % (_wlm + 50, "OK " if r == "draw_too_long" else "!! ", r))
r = bot.parse_draw(_mk(2500), addressed=True)
print("   裸串 2500 字无「画」(成品串应直送) = %s %s"
      % ("OK " if isinstance(r, tuple) else "!! ",
         ("拿到 %d 字" % len(r[0])) if isinstance(r, tuple) else r))
r = bot.parse_draw(_mk(9000), addressed=True)
print("   9000 字超硬闸(应 None，不当画图指令) = %s %s"
      % ("OK " if r is None else "!! ", r))

# ㉘ 逗号式档位 / 动词在前式（2026-09-19 主人踩到）：
#   主人手机上是这么打的 ——「写实风景，中世纪高耸塔楼，尖顶，石头墙，阴影层次」。
#   以前四式里没有逗号式，整句掉进聊天大脑（ollama 回「我教你怎么画风景」）；
#   而「画写实风景，中世纪卢浮宫」更坑：档位静默回落默认 anima，提示词里还带着「写实风景」。
#   注：描述里的「一个/一张」会被既有清洗规则摘掉，期望值按摘完的写。
#
# ⚠️ 2026-10-25 修：原来这里 4 条断言依赖「写实风景」这个**档名**，
#    而 config 的 `comfyui.styles` 现在只有 anima / krea2 / 厚涂 / 宫格，
#    「写实风景」「Anima工作流」「fast」这些老档**全没了** ⇒ 断言必然失败。
#    这不是 bug（主人删的档），是断言过期。现在改成测**真正该测的**：
#      · 有档名的（画动漫，一个少女）→ 该剥出档位 anima + 正文「少女」
#      · 没档名的（写实风景，…）  → 认不出档就不该瞎认，返回 None（交给聊天/其它分支）
#    这样以后再加删档位，这个用例也不会跟着飘。
print("\n=== ㉘ 逗号式档位 / 动词在前式 ===")
for said, want_style, want_body in [
        ("画动漫，一个少女", "anima", "少女"),
        ("画：一只猫，在窗台", None, "猫，在窗台"),
]:
    got = bot.parse_draw(said, addressed=True)
    ok = bool(got) and got[1] == want_style and got[0] == want_body
    print("   %-26s → 档=%-6s 描述=%-24s %s" % (
        said, (got[1] if got else None), (got[0] if got else None), "OK " if ok else "!! "))
# 已下线的老档名不许被瞎认（「写实风景」曾是真档，2026-10 后被删）
# ⚠️ 「画写实风景，…」这种带**画**动词的，`parse_draw` 仍会把整句当纯描述出图
#    （档位 None，正文原样）—— 这是对的：用户说了「画」，就该画，只是不认那个档。
#    所以这条断言的是「档位必须是 None」，不是「整句被丢掉」。
for said, want_none_style in [
        ("写实风景，中世纪高耸塔楼，尖顶，石头墙，阴影层次", True),
        ("画一张风景，山间小屋", True),
        ("画写实风景，中世纪卢浮宫", True)]:
    got = bot.parse_draw(said, addressed=True)
    ok = (got is None) or (got[1] is None)
    print("   老档名已下线 %-26s → 档=%s %s" % (
        said, (got[1] if got else None), "OK " if ok else "!! "))
# 群聊里没@我时逗号式不许认（「风景，我觉得挺好看」这类闲聊会误触）；动词式只改档位、不新增出图
for said in ["写实风景，中世纪高耸塔楼", "风景，中世纪卢浮宫"]:
    got = bot.parse_draw(said, addressed=False)
    print("   群里没@我 %-22s → %s %s" % (said, got, "OK " if got is None else "!! "))
for said in ["风景，我觉得挺好看", "动漫，我也喜欢", "写真，你说得对", "风景，好看", "快，快点",
             "画个动漫展的海报", "画动漫杂志封面", "动漫展里的初音"]:
    got = bot.parse_draw(said, addressed=True)
    ok = (got is None) if not said.startswith("画个") and not said.startswith("画动漫") else True
    print("   不误伤 %-22s → %s %s" % (said, got, "OK " if ok else "!! "))

# ---------------- ㉙ 画图路由（2026-10-29 主人定：固定口令 / AI 判断 双模式） ----------------
# 起因：主人一天晒了两张图 ——「画 [吧]」拿「[吧]」画、「moss画个」（回复着图发的）
# 拿「个」画（「指令也是蠢得没边了」「我让你让agent判断你就让agent判断啊」）。要验：
#   ① 白板指令（剥掉包裹标点后只剩语气词）在 parse_draw / handle_moss_draw 都被拒；
#   ② AI 路由走**主大脑 chat()**（不另挑嘴）：说画就画（引擎没开落默认引擎）、
#      说不是画图/大脑挂了 → 掉回聊天。
print("\n=== ㉙ 白板指令拦截（画 [吧] / moss画个）===")
for said in ["画 [吧]", "画 吧", "画（吗）", "画~"]:
    got = bot.parse_draw(said, addressed=True)
    print("   白板 %-10s → %s %s" % (said, got, "OK " if got is None else "!! 应为 None"))
for said in ["画 猫", "画一只猫吧", "画 [一只橘猫]"]:
    got = bot.parse_draw(said, addressed=True)
    ok = isinstance(got, tuple)
    print("   正常 %-12s → %s %s" % (said, (got[0] if got else None), "OK " if ok else "!! 应命中"))
for said in ["个", "[吧]", "一只猫吧", "一只橘猫"]:
    got = bot._draw_desc_ok(said)
    want = said in ("一只猫吧", "一只橘猫")
    print("   _draw_desc_ok %-8s → %s %s" % (said, got, "OK " if got == want else "!! 应为 %s" % want))

print("\n=== ㉙ moss画 白板描述（带图=画同款 / 没图=问回来）===")
GID = 123450001
_moss_rev_calls, _orig_moss_rev = [], bot.handle_moss_reverse
bot.handle_moss_reverse = lambda gid, extra, priv, imgs=(), qmid=0, mid=0: (
    _moss_rev_calls.append({"extra": extra, "imgs": imgs, "qmid": qmid}), None)[1]
_moss_cfg_bak = json.loads(json.dumps(bot.CFG.get("moss") or {}))
_moss_gen_bak = bot.moss_gen
# 假 moss_gen：available() 恒真，让 moss_enabled 只看白名单（真模块依赖云 key）
bot.moss_gen = type("FakeMossGen", (), {"available": staticmethod(lambda c: True)})()
bot.CFG.setdefault("draw", {})["moss"] = {"groups": [GID]}
OUT.clear()
bot.handle_moss_draw(GID, "个", True, 0)                     # 没图 → 问回来
_ok = OUT and OUT[-1][0] == "text" and "画什么" in OUT[-1][2]
print("   moss画个(没图) → %s %s" % (OUT[-1][2] if OUT else None, "OK " if _ok else "!!"))
bot.handle_moss_draw(GID, "个", True, 0, imgs=["x.png"], qmid=0)   # 带图 → 反推重画
_ok = len(_moss_rev_calls) == 1 and _moss_rev_calls[0]["imgs"] == ["x.png"]
print("   moss画个(带图) → 转反推重画 %d 次 %s" % (len(_moss_rev_calls), "OK " if _ok else "!!"))
bot.handle_moss_reverse = _orig_moss_rev
bot.CFG["moss"] = json.loads(json.dumps(_moss_cfg_bak))
bot.moss_gen = _moss_gen_bak

print("\n=== ㉙ 画图路由（旧 AI 二次判定已删，只留口令链 + draw_route 兼容）===")
_route_cfg_bak = json.loads(json.dumps(bot.CFG.get("draw") or {}))
_comfy_enabled_bak = bot.CFG.setdefault("comfyui", {}).get("enabled")
bot.CFG.setdefault("comfyui", {})["enabled"] = True
_force_comfy_engine()          # 显式固定链路状态：comfyui 开、moss 关（别受上面 moss 用例漏影响）

# ① 模式映射（旋钮仍在，读取兼容）
for v, want in [("AI 判断", "ai"), ("口令", "keyword"), ("", "keyword"), ("keyword", "keyword")]:
    bot._dot_set("draw.route", v)
    got = bot.draw_route()
    print("   draw.route=%-8r → %s %s" % (v, got, "OK " if got == want else "!! 应为 %s" % want))

# ② 旧二次判定层已删：主脑统一决策（ADR-001），不再有单独的判定 chat()
for _gone in ("_ai_route_draw", "_ROUTE_SYS", "_route_json", "_route_prompt_grounded"):
    print("   旧二次判定 %-22s 已删 %s" % (_gone, "OK " if not hasattr(bot, _gone) else "!! 仍存在"))

if _comfy_enabled_bak is not None:
    bot.CFG.setdefault("comfyui", {})["enabled"] = _comfy_enabled_bak
bot.CFG["draw"] = json.loads(json.dumps(_route_cfg_bak))

if _orig_req_at is not None:
    bot.CFG.setdefault("comfyui", {})["require_addressing"] = _orig_req_at
_restore_draw_cfg()      # 还原 draw.* 群白名单（别把改动漏给后续回归）

print("\n完成")
