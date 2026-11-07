# -*- coding: utf-8 -*-
"""「随机画」离线功能测试：
  ① 触发词识别  ② WeiLin 模块名解析  ③ 多模块 / 指定数量 / 固定tag混搭抽 tag
  ④ 未知模块提示  ⑤ 帮助  ⑥（信息性）LLM 普通随机回落链。"""
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import bot

SENT = []
bot.send_group = lambda gid, text: SENT.append(text) or True
CAP = {}
def _fake_submit(gid, what, style, who, **kw):
    CAP["last"] = {"gid": gid, "what": what, "style": style, "who": who, "kw": kw}
    return True, "已排队 #test"
bot.draw_submit = _fake_submit
# ⚠️ 2026-10-25 修：`draw_gate` 签名 2026-10-24 改成了 (gid, privileged, engine=None)
#    （两条链路各自按群开关），这里的桩还停在旧的单参数版 ⇒ 每次跑到这就 TypeError。
#    收 *a/**kw 最稳，以后再改签名也不用动它。
bot.draw_gate = lambda *a, **kw: None      # 绕过冷却/配额/ComfyUI 闸门

bad = 0
def chk(name, cond, extra=""):
    global bad
    print(("OK  " if cond else "FAIL ") + name + ("" if cond else " | " + str(extra)))
    if not cond:
        bad += 1

def run(arg):
    SENT.clear(); CAP.clear()
    bot.handle_random_draw(123450001, True, "测试人", arg=arg)
    return "".join(SENT), CAP.get("last") or {}

# 1) 触发词
POS = ["随机画一张", "随机画个赛博朋克城市", "帮我随机画一张", "随机 来一张", "随机生图",
       "给我随机出一张", "随机抽一张", "随机抽张图", "随机画 动漫角色"]
NEG = ["随机", "随机是什么意思", "今天真随机", "画一张猫", "随机数", "别随机画画了嗯"]
for t in POS:
    chk("触发 " + t, bot.wants_random_draw(t))
for t in NEG:
    chk("不误报 " + t, not bot.wants_random_draw(t))

# 2) 模块名解析
names = bot.weilin_partition_names()
chk("读到 WeiLin 模块表(>=15)", len(names) >= 15, len(names))
chk("解析 动漫->动漫角色", bot._resolve_partition("动漫", names) == "动漫角色")
chk("解析 情绪->情绪神态", bot._resolve_partition("情绪", names) == "情绪神态")
chk("精确 构图镜头", bot._resolve_partition("构图镜头", names) == "构图镜头")
chk("歧义 角色->None", bot._resolve_partition("角色", names) is None)

# 3) 多模块各抽 2 个
msg, cap = run("情绪神态 动作姿势 构图镜头")
chk("多模块:3 个模块都抽到", all(("×" in msg) for _ in [0]) and msg.count("×") >= 3, msg[:160])
chk("多模块:提交了提示词", bool(cap.get("what")), cap)
chk("多模块:逗号分隔", "," in (cap.get("what") or ""), cap)

# 4) 指定数量（模块:数量）
msg, cap = run("情绪神态:3 动漫角色")
chk(":数量 生效 情绪×3", "情绪神态×3" in msg, msg[:160])
chk(":数量 生效 动漫×2", "动漫角色×2" in msg, msg[:160])

# 5) 固定 tag + 模块混搭（固定在前）
msg, cap = run("1girl blue_hair 情绪神态")
chk("固定 tag 排在最前", (cap.get("what") or "").startswith("1girl, blue_hair,"), cap.get("what"))

# 6) 纯固定 tag（全英文，无模块）→ 直接出图
msg, cap = run("1girl solo white_hair")
chk("纯固定 tag 直接出图", (cap.get("what") or "").startswith("1girl, solo, white_hair"), cap.get("what"))

# 7) 未知模块（含中文）→ 提示清单且不提交
msg, cap = run("瞎写的模块")
chk("未知模块→提示且不提交", ("没认出来" in msg) and not cap.get("what"), msg[:160])

# 8) 帮助
msg, cap = run("帮助")
chk("帮助列出可用模块", ("可用模块" in msg) and ("情绪神态" in msg), msg[:120])

print("--- 多模块示例输出 ---")
print(run("情绪神态 动作姿势 构图镜头")[0][:200].replace("\n", " / "))
print()
print("RESULT:", "PASS" if bad == 0 else "FAIL(%d)" % bad)

# 9)（信息性，不参与判定）不带模块 → LLM 普通随机
SENT.clear(); CAP.clear()
bot.handle_random_draw(123450001, True, "测试人")
print("[信息] 普通随机 LLM 回落：", "".join(SENT)[:120].replace("\n", " / "))
