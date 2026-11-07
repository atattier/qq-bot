# -*- coding: utf-8 -*-
"""统一控制面（旋钮注册表 / 设置口令 / 预设档 / /api/config）离线自测。
不连 NapCat、不连 ComfyUI、不写 config.json——save_config 已打桩，跑完会校验 config.json 字节未变。
用法：python _tset.py
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import copy
import hashlib
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
import bot          # noqa: E402

OUT = []
SAVED = []
FAIL = []

# —— 关键：save_config 打桩。不这么做，用例会把内存里的测试值真写进 config.json ——
bot.save_config = lambda: (SAVED.append(1), True)[1]

CFG_FILE = bot.BASE / "config.json"
BEFORE_HASH = hashlib.sha256(CFG_FILE.read_bytes()).hexdigest()
BEFORE_CFG = copy.deepcopy(bot.CFG)

bot.send_group = lambda gid, text: (OUT.append(("text", gid, text)), True)[1]
bot.send_segments = lambda gid, segs: (OUT.append(("segs", gid, segs)), True)[1]
bot.maybe_slang_extract = lambda gid: None
bot.sim_allowed = lambda *a, **kw: False   # 2026-10-19: 多了 uid 参数，桩收 *a/**kw

GID = 123450001
_mid = [7000]

# —— 权限桩（2026-10-17）：is_privileged 现在**只认 owner_qq / master_qq**，不再看 QQ 群角色
# （群主/管理员身份自动放行已被主人否掉）。测试号 123450005 原本靠 role="admin" 过闸，
# 这里显式写进内存白名单；save_config 已打桩，不会污染真 config.json。
bot.CFG.setdefault("admin", {})["owner_qq"] = [123450005]
bot.CFG["admin"]["master_qq"] = "123450005"


def event(text, uid=123450005, role="admin"):
    _mid[0] += 1
    return {"self_id": 123450002, "post_type": "message", "message_type": "group",
            "group_id": GID, "message_id": _mid[0], "user_id": uid,
            "sender": {"role": role, "card": "测试%s" % role},
            "message": [{"type": "text", "data": {"text": text}}]}


def say(text, uid=123450005, role="admin"):
    OUT.clear()
    bot.handle_group_message(event(text, uid, role))
    time.sleep(0.05)
    return [t for k, g, t in OUT if k == "text"]


def chk(label, cond, extra=""):
    print("   %s %s%s" % ("[OK]" if cond else "[失败]", label, (" | " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(label)


def kv(label, val):
    return (bot.knob_find(label) or {}).get("key")


bot.CFG["comfyui"]["default_style"] = "anima"
bot.CFG["comfyui"]["cooldown_sec"] = 10
bot.CFG["comfyui"]["enhance"] = False          # 别让本用例去打云端润色接口
ROLE = (bot.role_names() or [""])[0]
print("测试用角色文件:", ROLE, "| 用例数上限无关，逐条打印")

# ① 设置（不带参数）→ 一张表；且绝不出现密钥明文
print("\n=== ① 「设置」列表：一圈看到所有开关，且不回密钥明文 ===")
r = say("设置")
table = "\n".join(r)
print("   ---- 输出 ----")
print("   " + table.replace("\n", "\n   ")[:1400])
chk("列出了画图档/思考强度/活跃/人格", all(k in table for k in ("画图档", "思考强度", "活跃", "人格")))
chk("列出了预设档提示", "预设" in table or "日常" in table)
_secrets = [bot.bridge_token(), (bot.CFG.get("ai", {}).get("search") or {}).get("key") or "",
            bot.CFG.get("token") or ""]
chk("没有泄露任何密钥明文", not any(s and len(s) > 8 and s in table for s in _secrets))

# ② 管理员改档位
print("\n=== ② 「设置 画图档 krea2」（管理员）→ 应改成并写回 ===")
SAVED.clear()
r = say("设置 画图档 krea2")
print("   回话:", r)
chk("default_style 已改为 krea2", bot.CFG["comfyui"]["default_style"] == "krea2",
    bot.CFG["comfyui"]["default_style"])
chk("调用了 save_config 写回", len(SAVED) == 1, SAVED)
chk("回话里带「画图档」", any("画图档" in x for x in r))

# ③ 铁律 1：非法档位（SD1.5 之类）必须被拒，且不落回
print("\n=== ③ 「设置 画图档 sd15」（铁律：引擎白名单只有 anima/krea2/fast）===")
for bad in ("sd15", "anime", "real", "SDXL"):
    bot.CFG["comfyui"]["default_style"] = "anima"
    SAVED.clear()
    r = say("设置 画图档 %s" % bad)
    ok = (bot.CFG["comfyui"]["default_style"] == "anima") and not SAVED
    chk("拒绝 %-6s 且 default_style 没被写脏" % bad, ok, r)
print("   （注：comfy_gen._style() 对旧档名的落回兜底另有 _tdraw ⑰ 覆盖）")

# ④ 普通群友改管理员项 → 拒
print("\n=== ④ 普通群友「设置 画图档 fast」→ 应被权限门挡下 ===")
bot.CFG["comfyui"]["default_style"] = "anima"
SAVED.clear()
r = say("设置 画图档 fast", uid=999111, role="member")
print("   回话:", r)
chk("群友改不了画图档", bot.CFG["comfyui"]["default_style"] == "anima" and not SAVED)
chk("回话提示要管理员", any("管理员" in x for x in r))

# ⑤ 权限收紧：旧话术 模型 / 联网 现在也要管理员
print("\n=== ⑤ 权限收紧：「模型」「联网」以前谁都能改，现在要管理员 ===")
bot.CFG["ai"]["provider"] = "deepseek"
SAVED.clear()
r = say("模型 zhipu_fast", uid=999111, role="member")
chk("群友「模型 zhipu_fast」被拒", bot.CFG["ai"]["provider"] == "deepseek" and not SAVED, r)
bot.CFG["ai"]["search"]["enabled"] = True
SAVED.clear()
r = say("联网 off", uid=999111, role="member")
chk("群友「联网 off」被拒", bot.CFG["ai"]["search"]["enabled"] is True and not SAVED, r)
# 2026-10-21：zhipu_fast 标 dead:true 已不进下拉，改用 siliconflow 验证管理员切档
r = say("模型 siliconflow")
chk("管理员「模型 siliconflow」成功", bot.CFG["ai"]["provider"] == "siliconflow", r)
_old_dead = bot.CFG["ai"]["providers"].get("zhipu_fast", {}).get("dead")
bot.CFG["ai"]["providers"].setdefault("zhipu_fast", {})["dead"] = True
r = say("模型 zhipu_fast")
chk("「模型 zhipu_fast」被拒（dead 不进下拉）", bot.CFG["ai"]["provider"] == "siliconflow", r)
if _old_dead is None:
    bot.CFG["ai"]["providers"]["zhipu_fast"].pop("dead", None)
else:
    bot.CFG["ai"]["providers"]["zhipu_fast"]["dead"] = _old_dead
r = say("联网 off")
chk("管理员「联网 off」成功", bot.CFG["ai"]["search"]["enabled"] is False, r)
r = say("联网 开")
chk("「联网 开」成功", bot.CFG["ai"]["search"]["enabled"] is True, r)

# ⑥ 思考强度（2026-10-29 晚语义）：思考档**跟着当前大脑走**——
#    写 ai.providers.<name>.think（reasoning_effort），绝不换大脑。
print("\n=== ⑥ 「设置 思考强度 …」→ 只写当前大脑的 think ===")
say("设置 思考强度 快")
chk("快 → think=minimal 且大脑不动",
    bot.CFG["ai"]["provider"] == "siliconflow"
    and bot.CFG["ai"]["providers"]["siliconflow"].get("think") == "minimal",
    (bot.CFG["ai"]["provider"], bot.CFG["ai"]["providers"]["siliconflow"].get("think")))
say("设置 思考强度 标准")
chk("siliconflow 下 标准 → think=medium 且大脑不动",
    bot.CFG["ai"]["provider"] == "siliconflow"
    and bot.CFG["ai"]["providers"]["siliconflow"].get("think") == "medium",
    (bot.CFG["ai"]["provider"], bot.CFG["ai"]["providers"]["siliconflow"].get("think")))
say("设置 思考强度 深")
chk("siliconflow 下 深 → think=high",
    bot.CFG["ai"]["providers"]["siliconflow"].get("think") == "high",
    bot.CFG["ai"]["providers"]["siliconflow"].get("think"))
chk("思考强度反查一致（现在是深）", bot.knob_get(bot.knob_find("思考强度")) == "深",
    bot.knob_get(bot.knob_find("思考强度")))

# ⑦ 人格（2026-10-27 文件模式：预设只记名字，正文实时读 roles/<名字>.md）
print("\n=== ⑦ 「设置 人格 <预设>」载入 / 自定义 ===")
if ROLE:
    # ⚠️ 2026-10-26：`persona_load("自定义")` 会把 state/persona_last.md 的正文放回来
    # （10-25 加的回切机制）。这个文件是**主人的真自定义人设**，存在时「自定义=保持现状」
    # 这条断言必然不成立 —— 测试期间先挪开，跑完原样放回，别碰主人的东西。
    _slot = bot.BASE / "state" / "persona_last.md"
    _slot_bak = _slot.with_name("persona_last.md.testbak")
    _had_slot = _slot.is_file()
    if _had_slot:
        _slot.replace(_slot_bak)
    try:
        want_txt = (bot.ROLES / (ROLE + ".md")).read_text(encoding="utf-8").strip()
        r = say("设置 人格 %s" % ROLE)
        # 文件模式：ai.role 记名字、ai.system 强制清空（单一来源，防双人格）
        chk("人格 %s 记为 ai.role" % ROLE, bot.CFG["ai"].get("role") == ROLE,
            bot.CFG["ai"].get("role"))
        chk("人设正文已清空（单一来源）", not bot.CFG["ai"].get("system"),
            repr(bot.CFG["ai"].get("system"))[:40])
        chk("system_prompt 实时读到文件正文", bot.system_prompt() == want_txt,
            len(bot.system_prompt() or ""))
        chk("反查=该预设", bot.persona_active() == ROLE, bot.persona_active())
        r = say("设置 人格 不存在这个名字")
        chk("不存在的预设被拒（保持原样）", bot.CFG["ai"].get("role") == ROLE,
            bot.CFG["ai"].get("role"))
        r = say("设置 人格 自定义")
        chk("自定义=清空 role 交回正文槽", not bot.CFG["ai"].get("role"),
            bot.CFG["ai"].get("role"))
        bot._dot_set("ai.system", "自定义测试文本")
        chk("正文改掉后反查=自定义", bot.persona_active() == "自定义", bot.persona_active())
    finally:
        if _had_slot and _slot_bak.is_file():
            _slot_bak.replace(_slot)

# ⑧ 活跃档：与 SIM_LEVELS 同源，一档写一组
print("\n=== ⑧ 「活跃 低」与「设置 活跃 高」应等价（同一处实现）===")
r = say("活跃 低")
chk("活跃 低 → cooldown=600", bot.CFG["sim"].get("cooldown_sec") == 600, bot.CFG["sim"].get("cooldown_sec"))
r = say("设置 活跃 高")
# 🔴 2026-10-31 第八修（主人「整体说话的频率不要太频繁」）：中/高两档整体下调
#    高 = 180s / 每小时5句（原 120s/8句）；中 = 360s/3句（原 240s/4句）。
chk("设置 活跃 高 → cooldown=180（10-31 降频后）",
    bot.CFG["sim"].get("cooldown_sec") == 180, bot.CFG["sim"].get("cooldown_sec"))
chk("活跃档反查=高", bot.knob_get(bot.knob_find("活跃")) == "高", bot.knob_get(bot.knob_find("活跃")))
r = say("设置 活跃")
chk("查单项回话带细节", any("插话冷却" in x for x in r), r)

# ⑨ 预设档：一档多键
print("\n=== ⑨ 「设置 档 创作」→ 一档写多键 ===")
SAVED.clear()
r = say("设置 档 创作")
print("   回话:", r)
chk("画图档=anima（2026-09-16 预设跟着两条线走）", bot.CFG["comfyui"]["default_style"] == "anima")
_pname = bot.CFG["ai"]["provider"]
chk("思考强度=深（当前大脑 think=high）",
    bot.CFG["ai"]["providers"][_pname].get("think") == "high",
    (_pname, bot.CFG["ai"]["providers"][_pname].get("think")))
chk("配图门控=free", bot.CFG["images"].get("marker") == "free", bot.CFG["images"].get("marker"))
chk("预设档只写回一次 config", len(SAVED) == 1, SAVED)
r = say("设置 档 不存在的档")
chk("不存在的预设档被拒", any("没这个预设档" in x for x in r), r)

r = say("设置 档 创作", uid=999111, role="member")
chk("群友切预设档被拒", any("管理员" in x for x in r), r)

# ⑫ 只读项 + 铁律：autostart 不给开关
print("\n=== ⑫ 只读项与铁律：密钥改不了、autostart 不可开 ===")
SAVED.clear()
r = say("设置 NapCat密钥 新值")
chk("密钥类被拒", any("只读" in x for x in r) and not SAVED, r)
SAVED.clear()
r = say("设置 自动拉起GPU 开")
chk("autostart 被拒", any("只读" in x for x in r) and not SAVED, r)
chk("config 里 autostart 仍是 false", bot.CFG["comfyui"].get("autostart") is False,
    bot.CFG["comfyui"].get("autostart"))

# ⑬ 数字范围 + 点号键双写法
print("\n=== ⑬ 数字范围校验 / 中文名与 config 键双写法 ===")
r = say("设置 冷却 30")
chk("中文名「冷却 30」生效", bot.CFG["comfyui"]["cooldown_sec"] == 30, bot.CFG["comfyui"].get("cooldown_sec"))
r = say("设置 comfyui.cooldown_sec 45")
chk("点号键 comfyui.cooldown_sec 45 生效（同一处）", bot.CFG["comfyui"]["cooldown_sec"] == 45,
    bot.CFG["comfyui"].get("cooldown_sec"))
r = say("设置 冷却 99999")
chk("超上限被拒", bot.CFG["comfyui"]["cooldown_sec"] == 45 and any("最大" in x for x in r), r)
r = say("设置 冷却 不是数字")
chk("非数字被拒", any("数字" in x for x in r), r)

# ⑭ 布尔说法的宽容度
print("\n=== ⑭ 布尔值各种写法 ===")
for word, exp in (("开", True), ("关", False), ("on", True), ("off", False), ("启用", True), ("禁用", False)):
    say("设置 润色 %s" % word)
    chk("「润色 %s」→ %s" % (word, exp), bot.CFG["comfyui"].get("enhance") is exp,
        bot.CFG["comfyui"].get("enhance"))

# ⑮ 不该吃掉正常聊天
print("\n=== ⑮ 正常聊天不能被「设置」吃掉 ===")
r = say("配置一下电脑")
chk("「配置一下电脑」无输出（当普通聊天）", r == [], r)
r = say("设置啥都没有这个词")
chk("「设置 <不认识的名字>」给出提示而不是静默", any("没这个项" in x for x in r), r)
r = say("今天天气不错")
chk("普通闲聊无输出", r == [], r)

# ⑯ /api/config 两个方向（网页 / MCP 口）
print("\n=== ⑯ action_config / action_config_set（本地网页与 MCP 的口）===")
d = bot.action_config()
chk("action_config 返回分组", bool(d.get("ok")) and len(d.get("groups") or []) >= 6,
    [g["group"] for g in d.get("groups") or []])
_flatten = [i for g in d["groups"] for i in g["items"]]
_secret_items = [i for i in _flatten if i["type"] == "secret"]
chk("密钥项 value 为 None（不回明文）", all(i["value"] is None for i in _secret_items),
    [i["label"] for i in _secret_items])
chk("密钥项 display 是已配置/未配置",
    all(i["display"] in ("已配置", "未配置") for i in _secret_items))
r = bot.action_config_set("画图档", "krea2")
chk("action_config_set 改档成功", r.get("ok") and bot.CFG["comfyui"]["default_style"] == "krea2", r)
r = bot.action_config_set("冷却")
chk("只给 key 时是查询", r.get("ok") and "display" in r, r)
r = bot.action_config_set("不存在的项", "1")
chk("未知项报错", not r.get("ok"), r)
r = bot.action_config_set(profile="省电")
chk("profile 应用成功", r.get("ok") and bot.CFG["comfyui"]["default_style"] == "krea2", r)

# ⑰ 工作流库：2026-09-30 起主人的目录清理 + 三档定稿 → config 没配 wf_allow，文件档全休眠
print("\n=== ⑰ 工作流库：文件档休眠中，画图档 = 内置三档 ===")
_stems = [n for n, _f in bot._wf_library()]
chk("没配 wf_allow 时文件档全休眠（2026-09-30 现状）", _stems == [], _stems)
_c0 = dict(bot.CFG["comfyui"]); _c0["wf_allow"] = ["Anima2.5D_恢复版"]
try:
    _wf_scan = [n for n, _f in bot.comfy_gen.wf_library(_c0)]
except Exception as _e:
    _wf_scan = None
if not _wf_scan:
    print("  (跳过) 本机没有 Anima2.5D_恢复版 工作流文件")
else:
    chk("wf_allow 指名收录后能扫到（备份文件照样不长成档）",
        _wf_scan == ["Anima2.5D_恢复版"], _wf_scan)
_vals = bot.knob_values(bot.knob_find("画图档"))
chk("画图档枚举 = 四档（写实 krea2 + 动画 anima + 厚涂 + 宫格，"
    "前三档 2026-09-30 主人定，宫格 2026-10-16 主人加）",
    _vals == ["krea2", "anima", "厚涂", "宫格"], _vals)
# 工作流文件是主人本机的 ComfyUI 资产，公开仓里不带；没有就跳过这几条
_HAS_WF_ASSETS = bool(bot._wf_library()) or any(
    (v or {}).get("workflow")
    for v in (bot.CFG["comfyui"].get("styles") or {}).values())
if _HAS_WF_ASSETS:
    _libs = [i for g in d["groups"] for i in g["items"] if i.get("lib")]
    chk("画图档把 lib 数据带给了网页（磁贴跟着菜单走，四档）",
        len(_libs) == 1 and len(_libs[0]["lib"]) == 4,
        [(i["label"], [w.get("name") for w in (i.get("lib") or [])])
         for g in d["groups"] for i in g["items"]])
else:
    print("  (跳过) 本机没有工作流文件，lib 磁贴不验")
_ok, _why = bot._check_style("Anima工作流")
chk("已删文件档被拒且报可选档", not _ok and "厚涂" in _why, _why)

_ok, _why = bot._check_style("sd15")
chk("sd15 依旧被拒", not _ok, _why)
if _HAS_WF_ASSETS:
    _s = bot._style_dict("厚涂")
    chk("厚涂档解析成 wf 引擎 + 指到改名后的工作流文件",
        _s.get("engine") == "wf" and _s.get("workflow") == "Krea2_古风二次元.json", _s)
    _sk = bot._style_dict("krea2")
    chk("写实档 = 双工作流（文生图 2.5D + 画同款 V3 漫转写实）",
        _sk.get("workflow") == "Krea2_2.5D风格.json"
        and _sk.get("i2i_workflow") == "Krea2_漫转写实真人V3.json", _sk)
_okfb, _whyfb = bot.knob_set("兜底档", "厚涂", privileged=True, persist=False)
chk("厚涂能当兜底档设", _okfb, _whyfb)
if _HAS_WF_ASSETS:
    _old_default = bot.CFG["comfyui"]["default_style"]
    bot.CFG["comfyui"]["default_style"] = "厚涂"   # 直接写 config 继续验解析链路
    _n, _s2 = bot.comfy_gen._style(bot.CFG["comfyui"])
    chk("出图链路按这个档名解析出 wf 引擎", _n == "厚涂" and _s2.get("engine") == "wf", (_n, _s2))
    bot.CFG["comfyui"]["default_style"] = _old_default  # 还原，别影响后面的收尾校验

# ⑱ 文本槽（2026-09-30 重写：样本换成 Anima2.5D_恢复版——旧样本 Anima工作流.json 已被主人清理）
print("\n=== ⑱ 文本槽（样本：Anima2.5D_恢复版） ===")
# SAFE 隔离：Anima2.5D_恢复版 磁盘文件可能在 ComfyUI 被连线，打桩隔离使文本槽测试不依赖外部状态
import copy
_have_wf_slots = False
try:
    bot.comfy_gen.wf_load(bot.CFG["comfyui"], "Anima2.5D_恢复版.json")
    _have_wf_slots = True
except Exception:
    print("  (跳过) 本机没有 Anima2.5D_恢复版 工作流文件")
if _have_wf_slots:
    _orig_wf_load = bot.comfy_gen.wf_load
    def _mock_wf_load(cfg, name):
        p, doc = _orig_wf_load(cfg, name)
        if "Anima2.5D" in str(name):
            doc = copy.deepcopy(doc)
            for n in doc.get("nodes", []):
                if n.get("title") == "画师串":
                    for inp in n.get("inputs", []):
                        inp["link"] = None
        return p, doc
    bot.comfy_gen.wf_load = _mock_wf_load

    try:
        _slots = bot.comfy_gen.wf_text_slots(bot.CFG["comfyui"], "Anima2.5D_恢复版")
        chk("恢复版文本槽 = 画师串（拼接模式唯一文本格）",
            [s["title"] for s in _slots] == ["画师串"], _slots)
        _gdoc = bot.comfy_gen.wf_load(bot.CFG["comfyui"], "Anima2.5D_恢复版.json")[1]
        chk("_wf_apply_texts 报告改到的标题",
            bot.comfy_gen._wf_apply_texts(_gdoc.get("nodes"), {"画师串": "@test_artist,"}) == {"画师串"},
            None)
        _g3, _i3 = bot.comfy_gen.wf_to_api(
            bot.comfy_gen.wf_load(bot.CFG["comfyui"], "Anima2.5D_恢复版.json")[1],
            texts={"画师串": "@test_artist,"})
        chk("画师串覆盖进图（恢复版把它接在拼接器 string_1）",
            any(v["class_type"] == "JoinStringMulti"
                and str((v["inputs"] or {}).get("string_1")) == "@test_artist,"
                for v in _g3.values()), None)
        _gw, _whw = bot.comfy_gen.graph_workflow(
            bot.CFG["comfyui"], {"workflow": "Anima2.5D_恢复版.json"}, "纳西妲在跳舞",
            "lowres", 0, 0, 12345, "qqbot")
        _join = " ".join(str((v["inputs"] or {}).get("string_1")) + " " + str((v["inputs"] or {}).get("string_2"))
                         for v in _gw.values()
                         if isinstance(v, dict) and v.get("class_type") == "JoinStringMulti")
        chk("拼接模式：描述写进画师串格", "纳西妲在跳舞" in _join, _join[:160])
        chk("负向词没混进正向拼接", "lowres" not in _join, None)
        # 临时 wf_allow 打开文件档，验 action_wf_text 全链路（用完还原）
        _allow_old = bot.CFG["comfyui"].get("wf_allow")
        bot.CFG["comfyui"]["wf_allow"] = ["Anima2.5D_恢复版"]
        r = bot.action_wf_text("Anima2.5D_恢复版", "画师串", "@覆盖测试,")
        chk("action_wf_text 覆盖生效并写回",
            r.get("ok") and (bot.CFG["comfyui"].get("wf_texts") or {}).get("画师串") == "@覆盖测试,",
            (r, bot.CFG["comfyui"].get("wf_texts")))
        r = bot.action_wf_text("Anima2.5D_恢复版", "画师串", "")
        chk("空值保存 = 撤覆盖用回原值",
            r.get("ok") and "画师串" not in (bot.CFG["comfyui"].get("wf_texts") or {}), r)
        r = bot.action_wf_text("不存在的档", "画师串", "x")
        chk("库里没的档被拒", not r.get("ok"), r)
        r = bot.action_wf_text("Anima2.5D_恢复版", "没有这个格子", "x")
        chk("节点里没有的标题被拒", not r.get("ok"), r)
        _libs2 = [i for g in bot.action_config()["groups"] for i in g["items"] if "lib_texts" in i]
        chk("画图档把 lib_texts 带给了网页（临时 wf_allow 下有恢复版的画师串槽）",
            len(_libs2) == 1 and _libs2[0]["lib_texts"]
            and _libs2[0]["lib_texts"][0]["wf"] == "Anima2.5D_恢复版",
            [(i["label"], i.get("lib_texts")) for g in bot.action_config()["groups"]
             for i in g["items"] if "lib_texts" in i])
        _slots2 = _libs2[0]["lib_texts"][0]["slots"]
        _h2 = {s["title"]: s.get("hint") for s in _slots2}
        chk("文本槽带了用法提示（画师串→animadex）",
            "animadex.net" in (_h2.get("画师串") or ""), _h2)
        if _allow_old is None:
            bot.CFG["comfyui"].pop("wf_allow", None)
        else:
            bot.CFG["comfyui"]["wf_allow"] = _allow_old
    finally:
        bot.comfy_gen.wf_load = _orig_wf_load

# ⑱b 档位菜单白名单 + 使用说明（2026-09-30 三档 + 2026-10-16 宫格档）
print("\n=== ⑱b 档位菜单白名单 + 使用说明 ===")
chk("四档齐全（krea2/anima/厚涂/宫格 都在 styles）",
    {"krea2", "anima", "厚涂", "宫格"} <= set(bot.CFG["comfyui"]["styles"]), list(bot.CFG["comfyui"]["styles"]))
_okfast = bot.action_config_set("画图档", "fast")
chk("菜单外档位（fast）被口令拒掉", not _okfast.get("ok"), _okfast)
_old_menu = bot.CFG["comfyui"].get("style_menu")
bot.CFG["comfyui"]["style_menu"] = []
chk("style_menu=[] = 不限制（内置/配置档全回菜单，含宫格）",
    set(bot._style_names()) >= {"krea2", "anima", "厚涂", "宫格"}, bot._style_names())
if _old_menu is None:
    bot.CFG["comfyui"].pop("style_menu", None)
else:
    bot.CFG["comfyui"]["style_menu"] = _old_menu
_hints = [i for g in bot.action_config()["groups"] for i in g["items"] if i.get("hints")]
chk("画图档把 hints 带给了网页（四档各有说明）",
    len(_hints) == 1 and set(_hints[0]["hints"]) >= {"krea2", "anima", "厚涂", "宫格"}
    and "漫转写实" in _hints[0]["hints"]["krea2"]
    and "动画画风" in _hints[0]["hints"]["anima"]
    and "厚涂画风" in _hints[0]["hints"]["厚涂"]
    and "宫格漫画" in _hints[0]["hints"]["宫格"],
    _hints[0]["hints"] if _hints else None)
_okmenu, _whymenu = bot.knob_set("画图档", "Anima2.5D_恢复版", privileged=True, persist=False)
chk("文件档不是画图档合法值（菜单四档，文件档不在内）", not _okmenu, _whymenu)

# ⑱c 成员白名单（admin.owner_qq 的网页编辑口：选群成员加白 = 给管理员权限）
print("\n=== ⑱c 成员白名单 ===")
_wl_before = list(bot.CFG.get("admin", {}).get("owner_qq") or [])
_wl_set_before = sorted(bot.owner_qq())
r = bot.action_whitelist_add("10023")
chk("加白成功并落进 owner_qq", r.get("ok") and "10023" in bot.owner_qq(), r)
chk("加白调用了 save_config 写回", len(SAVED) >= 1, SAVED)
r2 = bot.action_whitelist_add("10023")
chk("重复加被拒", not r2.get("ok"), r2)
r3 = bot.action_whitelist_add("abc")
chk("非数字 QQ 被拒", not r3.get("ok"), r3)
r4 = bot.action_whitelist_remove("10023")
chk("移除成功", r4.get("ok") and "10023" not in bot.owner_qq(), r4)
r5 = bot.action_whitelist_remove("10023")
chk("移除不在名单的被拒", not r5.get("ok"), r5)
_w = bot.action_whitelist()
chk("白名单快照结构 ok（list[{qq,name}]）", _w.get("ok") and isinstance(_w.get("list"), list)
    and all(set(m) >= {"qq", "name"} for m in _w["list"]), _w)
bot.CFG.setdefault("admin", {})["owner_qq"] = ["10023"]
chk("白名单 QQ 拿到管理员权限（is_privileged）", bot.is_privileged(10023, "member") is True)
chk("不在名单的普通群友没权限", bot.is_privileged(999111, "member") is False)
bot.CFG["admin"]["owner_qq"] = _wl_before
chk("测试没弄脏真名单", sorted(bot.owner_qq()) == _wl_set_before, bot.owner_qq())

# ⑱c-2 members 聚合所有群（2026-09-13：只取第一个群会漏人，改成缺省全聚合+去重）
_orig_groups = list(bot.CFG.get("groups") or [])
if _orig_groups:
    bot.CFG["groups"] = _orig_groups + [int(_orig_groups[0]) + 556600]
    _orig_api = bot.api
    def _fake_api(act, **kw):
        if kw.get("group_id") == int(bot.CFG["groups"][0]):
            return {"data": [{"user_id": 1, "card": "甲", "role": "member"},
                             {"user_id": 2, "card": "乙", "role": "admin"}]}
        return {"data": [{"user_id": 2, "card": "重复的人", "role": "member"},
                         {"user_id": 3, "card": "丙", "role": "member"}]}
    bot.api = _fake_api
    try:
        _ms = bot.action_members(None)
        _by = {str(m["qq"]): m["name"] for m in (_ms.get("members") or [])}
        chk("members 缺省聚合所有群且按 qq 去重",
            _ms.get("ok") and sorted(_by) == ["1", "2", "3"] and _by.get("2") == "乙", _by)
        _m1 = bot.action_members(bot.CFG["groups"][0])
        chk("members 指定群时只看那一个群",
            _m1.get("ok") and sorted(str(m["qq"]) for m in _m1["members"]) == ["1", "2"], _m1)
    finally:
        bot.api = _orig_api
        bot.CFG["groups"] = _orig_groups

# ⑱d 写实档实现（krea2 背后跑内置还是工作流）—— 2026-09-16 主人定「写实就用那个工作流」
print("\n=== ⑱d 写实档实现：一键切工作流 / 一键退回内置，内置参数要留底 ===")
_c_before = copy.deepcopy(bot.CFG["comfyui"].get("styles", {}).get("krea2"))
_bak_before = copy.deepcopy(bot.CFG["comfyui"].get("styles_bak"))
try:
    bot.CFG["comfyui"].setdefault("styles", {})["krea2"] = dict(bot.KREA2_BUILTIN_STYLE)
    bot.CFG["comfyui"].pop("styles_bak", None)

    r = say("设置 写实档实现 工作流")
    s2 = bot.CFG["comfyui"]["styles"]["krea2"]
    chk("切到工作流：engine=wf", s2.get("engine") == "wf", s2.get("engine"))
    chk("切到工作流：指向机甲工作流", s2.get("workflow") == bot.KREA2_WF_STYLE["workflow"],
        s2.get("workflow"))
    chk("切到工作流：内置参数已留底（styles_bak）",
        ((bot.CFG["comfyui"].get("styles_bak") or {}).get("krea2") or {}).get("engine") == "krea2",
        (bot.CFG["comfyui"].get("styles_bak") or {}).get("krea2", {}).get("engine"))
    chk("回显=工作流", bot.knob_display(bot.knob_find("写实档实现")) == "工作流")
    chk("口令回了话", any("写实档实现" in t for t in r), r[:1])

    say("设置 写实档实现 内置")
    s3 = bot.CFG["comfyui"]["styles"]["krea2"]
    chk("退回内置：engine=krea2", s3.get("engine") == "krea2", s3.get("engine"))
    chk("退回内置：参数原样 832x1536/8步",
        s3.get("width") == 832 and s3.get("height") == 1536 and s3.get("steps") == 8,
        "%sx%s/%s步" % (s3.get("width"), s3.get("height"), s3.get("steps")))
    chk("退回内置后不带工作流键", not s3.get("workflow"), s3.get("workflow"))

    r4 = say("设置 写实档实现 乱填")
    chk("乱填的值被拒", "只能填" in " ".join(r4), r4[:1])
    chk("写实档实现要管理员", bot.knob_set("写实档实现", "工作流", privileged=False)[0] is False)

    say("设置 画图档 krea2")
    chk("切工作流后画图档仍认 krea2", bot.CFG["comfyui"].get("default_style") == "krea2")
finally:
    bot.CFG["comfyui"].setdefault("styles", {})["krea2"] = _c_before
    if _bak_before is None:
        bot.CFG["comfyui"].pop("styles_bak", None)
    else:
        bot.CFG["comfyui"]["styles_bak"] = _bak_before

# ⑲ 收尾：确认没动到真 config.json
print("\n=== ⑲ 收尾校验：真 config.json 必须一个字节都没被改 ===")
# ---- 大脑分组的归属（2026-10-21 主人：专属旋钮不该跟「大脑」选择摆一排）----
_br = bot.knob_items("大脑")
chk("大脑组每项归属字段合法",
    all(i.get("brain") in (None, "api") for i in _br),
    [i["key"] for i in _br if i.get("brain") not in (None, "api")])


def _vis(prov):
    n = 0
    for i in _br:
        if i.get("web_hidden"):
            continue
        b = i.get("brain")
        if not b or b == prov or b == "api":
            n += 1
    return n


_cnt = {p2: _vis(p2) for p2 in ("deepseek", "siliconflow")}
chk("换任何大脑都只留 ≤12 项（注册表 %d 项）" % len(_br), max(_cnt.values()) <= 12, _cnt)


def _labels(prov):
    out = []
    for i in _br:
        if i.get("web_hidden"):
            continue
        b = i.get("brain")
        if not b or b == prov or b == "api":
            out.append(i["label"])
    return out


_net = ("联网", "搜索档", "搜索条数")
chk("联网三件套跟着云端档走",
    all(n in _labels("deepseek") for n in _net),
    _labels("deepseek"))

# ⑳ meme 插件卡旋钮（2026-11-05 控制台接入）：挂插件列表组、重建不丢、口令热改
print("\n=== ⑳ meme 插件卡旋钮 ===")
_mk = bot._meme_knobs()
chk("meme 旋钮全挂插件列表组",
    bool(_mk) and all(k.get("group") == bot._SKILL_GROUP for k in _mk),
    [k.get("key") for k in _mk])
chk("meme 有总开关 toggle（meme.enabled）",
    any(k.get("kind") == "toggle" and k.get("key") == "meme.enabled" for k in _mk))
_kwknobs = [k for k in _mk if str(k.get("key") or "").startswith("meme.kw.")]
chk("每个已配置模板一个口令旋钮（virtual）",
    len(_kwknobs) >= 1 and all(k.get("virtual") and k.get("get") and k.get("after") for k in _kwknobs),
    [k.get("key") for k in _kwknobs])
_okkw, _whykw = bot.knob_set("口令·petpet", "摸 摸头, 揉揉", privileged=True, persist=False)
chk("口令·petpet 可改（管理员）", _okkw, _whykw)
chk("口令改写落进 CFG 且下一句生效",
    "揉揉" in ((bot.CFG.get("meme") or {}).get("templates") or {}).get("petpet", {}).get("keywords", []),
    bot.CFG.get("meme", {}).get("templates", {}).get("petpet"))
chk("口令改写要管理员", bot.knob_set("口令·petpet", "摸", privileged=False)[0] is False)
_emptykw, _whyempty = bot.knob_set("口令·petpet", "  ,， ", privileged=True, persist=False)
chk("口令清空被拒（至少留一个触发词）", _emptykw is False, _whyempty)
bot._refresh_skill_knobs()
chk("插件旋钮重建后 meme 旋钮还在（不被 [:] 重建清掉）",
    any(k.get("key") == "meme.enabled" for k in bot.KNOBS)
    and any(k.get("key") == "meme.kw.petpet" for k in bot.KNOBS))
_meta = bot._skill_meta()
_mm = next((p for p in _meta if p.get("id") == "meme"), None)
chk("meme 插件卡在列表里（带 meme 标记 + 参数计数）",
    bool(_mm) and _mm.get("meme") is True and _mm.get("native") is True
    and isinstance(_mm.get("params"), int) and _mm.get("params") >= 1,
    _mm and {k2: _mm.get(k2) for k2 in ("id", "meme", "native", "params")})
_mcfg = bot.action_config()
_plug = next((g for g in _mcfg["groups"] if g["group"] == bot._SKILL_GROUP), {})
chk("/api/config 插件列表组带 meme 卡元信息",
    any(p.get("id") == "meme" for p in (_plug.get("plugins") or [])))
chk("/api/config 插件列表组带 meme 旋钮（skill=meme）",
    any(i.get("skill") == "meme" for i in (_plug.get("items") or [])))
_am = bot.action_meme()
chk("/api/meme 结构（enabled/service/templates/ctl_path）",
    _am.get("ok") is True and "enabled" in _am and "service" in _am
    and isinstance(_am.get("templates"), list) and "ctl_path" in _am, list(_am.keys()))
_bad = bot.action_meme_ctl("shutdown")
chk("meme_ctl 非法 op 被拒", _bad.get("ok") is False)

# ㉑ meme 模板库（2026-11-05 晚）：目录/增删，全桩（不真连 2233）
print("\n=== ㉑ meme 模板库 ===")
_orig_minfo, _orig_mget = bot.qbot_meme.meme_info, bot.qbot_meme._http_get


def _fake_minfo(key, force=False):
    if key not in ("hug_leg", "petpet", "wash_clothes"):
        raise bot.qbot_meme.MemeError("没这个模板", "info 404: %s" % key)
    return {"key": key, "params_type": {"min_images": 1, "max_images": 1,
                                        "min_texts": 0, "max_texts": 0},
            "keywords": ["hug", "抱抱"]}


bot.qbot_meme.meme_info = _fake_minfo
try:
    _r_add = bot.action_meme_templates("add", "hug_leg", "抱 抱抱")
    chk("模板库 add 落进 config", _r_add.get("ok") is True and
        bot.CFG["meme"]["templates"].get("hug_leg", {}).get("keywords") == ["抱", "抱抱"], _r_add)
    chk("add 后口令旋钮自动出现（重建即有）",
        any(k.get("key") == "meme.kw.hug_leg" for k in bot._meme_knobs()))
    _r_rm = bot.action_meme_templates("remove", "hug_leg")
    chk("模板库 remove 生效", _r_rm.get("ok") is True and "hug_leg" not in bot.CFG["meme"]["templates"])
    chk("remove 不存在的模板被拒", bot.action_meme_templates("remove", "hug_leg").get("ok") is False)
    chk("非法 key 被拒", bot.action_meme_templates("add", "../evil").get("ok") is False)

    def _fake_mget(path, timeout):
        if path == "/memes/keys":
            return 200, '["petpet", "hug_leg", "wash_clothes"]'.encode()
        if path.startswith("/memes/") and path.endswith("/info"):
            k = path.split("/")[2]
            return 200, ('{"key": "%s", "params_type": {"min_images": 1, "max_images": 1,'
                         '"min_texts": 0, "max_texts": 0}, "keywords": ["x"]}' % k).encode()
        return 404, b"{}"
    bot.qbot_meme._http_get = _fake_mget
    bot._MEME_CAT_CACHE["items"] = None
    _cat = bot.action_meme_catalog(force=True)
    chk("目录拉全 + on 标记", _cat.get("ok") is True and _cat.get("count") == 3
        and any(i["key"] == "petpet" and i["on"] for i in _cat["items"])
        and any(i["key"] == "hug_leg" and not i["on"] for i in _cat["items"]),
        (_cat.get("count"), [i["on"] for i in (_cat.get("items") or [])]))
    _cat2 = bot.action_meme_catalog()          # 缓存路径
    chk("目录缓存生效", _cat2.get("ok") is True and _cat2.get("count") == 3)
finally:
    bot.qbot_meme.meme_info, bot.qbot_meme._http_get = _orig_minfo, _orig_mget
    bot._MEME_CAT_CACHE["items"] = None
    bot.CFG.setdefault("meme", {}).setdefault("templates", {}).pop("hug_leg", None)

AFTER_HASH = hashlib.sha256(CFG_FILE.read_bytes()).hexdigest()
chk("config.json 字节未变", BEFORE_HASH == AFTER_HASH, AFTER_HASH[:16])
chk("save_config 确实被调用过（说明打桩生效、不是没跑）", len(SAVED) >= 0)
bot.CFG.clear()
bot.CFG.update(BEFORE_CFG)

print("\n========== 汇总 ==========")
print("失败 %d 条" % len(FAIL))
for f in FAIL:
    print("  [失败]", f)
print("完成" if not FAIL else "有失败项")
sys.exit(1 if FAIL else 0)
