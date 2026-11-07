# -*- coding: utf-8 -*-
"""人格串台修复测试：清上下文、身份锚点、人格反查一致性。"""
import os, sys, tempfile
from pathlib import Path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import bot

bad = 0
_AI_ORIG = dict(bot.CFG.get("ai", {}))
G = 123450001
# 1) _clear_history 连 CONTEXT 一起清
bot.remember(G, "小鲸", 123450002, "旧人设的发言：本店布丁欠三份")
bot.HISTORY[G] = bot.deque([{"role": "user", "content": "旧"}], maxlen=20)
before_ctx = len(bot.CONTEXT.get(G) or [])
bot._clear_history()
if (G in bot.CONTEXT) or (G in bot.HISTORY):
    print("FAIL: 清理后 CONTEXT/HISTORY 还有旧货"); bad += 1
else:
    print("OK: _clear_history 连 CONTEXT 一起清（清前 ctx=%d 条）" % before_ctx)

# 2) 人设正文旋钮挂了 after 钩子
e = bot.knob_find("人设正文")
# 2026-10-19：after 从 _clear_history 换成 _persona_text_edited（清历史 + 转自定义 + 留底）
_aft = (e or {}).get("after")
_pc0 = bot.CFG["ai"].get("persona_custom")
try:
    # 传空串：只验「调用后转自定义」，不会把 state/persona_last.md 那份回滚底稿覆盖掉
    _aft("", None) if callable(_aft) else None
    _flipped = bot.CFG["ai"].get("persona_custom") is True
finally:
    if _pc0 is None:
        bot.CFG["ai"].pop("persona_custom", None)
    else:
        bot.CFG["ai"]["persona_custom"] = _pc0
if _aft is bot._persona_text_edited and _flipped:
    print("OK: 人设正文挂 _persona_text_edited（清历史 + 转自定义）")
else:
    print("FAIL: 人设正文 after 不对：%r / 调用后转自定义=%s" % (_aft, _flipped)); bad += 1

# 3) 人格应用与反查一致性（测试主动应用，不假定 config 初值）
# ⚠️ 不写死角色名：roles/ 里的卡会增删（原来的「真人群友.md」已经删了），
# 取当前实际存在的两张卡来测「load 完反查得到自己」。
_rs = bot.role_names() or []
_a, _b = (_rs[0] if _rs else "Yuki"), (_rs[1] if len(_rs) > 1 else "")
orig_sys = bot.CFG.get("ai", {}).get("system")
bot.persona_load(_a)
act_yuki = bot.persona_active()
if act_yuki != _a:
    print("FAIL: persona_load(%r) 后反查 = %r" % (_a, act_yuki)); bad += 1
else:
    print("OK: persona_active = %s（load 与 roles 文件一致）" % _a)

if _b:
    bot.persona_load(_b)
    act_real = bot.persona_active()
    if act_real != _b:
        print("FAIL: persona_load(%r) 后反查 = %r" % (_b, act_real)); bad += 1
    else:
        print("OK: persona_active = %s（load 与 roles 文件一致）" % _b)

# 4) build_messages 注入了身份锚点（得先有上下文，blk 非空才注入）
bot.remember(G, "群友", 123, "随便聊聊")
bot.remember(G, "群友", 123, "再来一句凑上下文")
bot.CFG.setdefault("ai", {})["system"] = "测试人设：安静温和。"
msgs = bot.build_messages(G, "在吗")
sys_txt = msgs[0]["content"]
if "换人设之前说的" in sys_txt and "以本轮系统设定为准" in sys_txt:
    print("OK: 上下文块带身份锚点")
else:
    print("FAIL: 没看到身份锚点"); bad += 1

# 5) 新版 Yuki 人设的降温关键词自检：不该再有「撒娇/黏人」当常态词
#    ⚠️ 2026-10-29：roles/Yuki.md 已被删（现役人设换成小柚）—— 文件不在就跳过，
#    别让回归挂在一张人设卡的生死上。
_yuki_path = os.path.join(_ROOT, "roles", "Yuki.md")
if os.path.exists(_yuki_path):
    yuki = open(_yuki_path, encoding="utf-8").read()
    if "温柔黏人" in yuki or "撒娇，情绪细腻，爱哭" in yuki:
        print("FAIL: Yuki.md 还留着高热情措辞"); bad += 1
    elif "安静温和" in yuki and "不是热情黏人型" in yuki:
        print("OK: Yuki.md 已降温")
    else:
        print("WARN: Yuki.md 内容出乎预期，人工看一眼")
else:
    print("SKIP: roles/Yuki.md 已删（现役人设换了卡），降温自检跳过")

# 6) 「自定义」要能钉住（2026-10-19 主人踩到：选了自定义、刷新又跳回预设）
# ⚠️ 不写死角色名：roles/ 里的卡会增删（「真人群友.md」已被删掉），
# 取当前实际存在的第一张卡来测「正文=预设时显示该预设」。
# ⚠️ 2026-10-27 文件模式：persona_active() **先看 ai.role**（有值就直接按名字认人，
#    正文压根不看），所以想测「靠正文反查出预设」这条兼容路径，必须先把 role 清空，
#    测完再还原 —— 否则 role 还留着上一步的「富江」，断言永远指向富江。
_probe = (bot.role_names() or [""])[0]
_saved_role = bot.CFG["ai"].get("role")
_saved_system = bot.CFG["ai"].get("system")
_saved_custom = bot.CFG["ai"].get("persona_custom")
bot.CFG["ai"]["role"] = ""
bot.CFG.setdefault("ai", {})["system"] = open(
    os.path.join(_ROOT, "roles", _probe + ".md"), encoding="utf-8").read().strip()
bot.CFG["ai"].pop("persona_custom", None)
if bot.persona_active() != _probe:
    print("FAIL: 正文=预设时该显示该预设（%s），实际 %s" % (_probe, bot.persona_active())); bad += 1
else:
    print("OK: 正文=预设时反查出该预设（%s，role 为空时的兼容路径）" % _probe)
bot.CFG["ai"]["persona_custom"] = True
if bot.persona_active() == "自定义":
    print("OK: 选自定义后不会被内容比对顶回预设")
else:
    print("FAIL: 自定义没钉住"); bad += 1
bot.CFG["ai"].pop("persona_custom", None)
# 2026-10-27 补：**system 也要还原**。上一版只还原了 role，system 里还留着 Yuki 的正文，
# 一旦后面某处 save_config() 落盘，就把文件模式（role 有名、system 空）写回成旧模式
# （role 空、system 有正文），人设看着"生效"但靠的是内容比对，改 roles/*.md 不再即时生效。
bot.CFG["ai"]["role"] = _saved_role or ""
bot.CFG["ai"]["system"] = _saved_system or ""
if _saved_custom is not None:
    bot.CFG["ai"]["persona_custom"] = _saved_custom

# 7) 人设正文不许存空（手滑清空 = 机器人没人味）
_ok, _why = bot.knob_set("人设正文", "   ", privileged=True, persist=False)
if _ok is False and "不能存空" in _why:
    print("OK: 人设正文空值被拒")
else:
    print("FAIL: 人设正文能被清空 —— %s" % _why); bad += 1

# 8) 上限放宽到 20000（旧值 4000 撑不住长角色卡）
_ent = bot.knob_find("人设正文")
if _ent and int(_ent.get("max_len") or 0) >= 20000:
    print("OK: 人设正文上限 %s 字" % _ent.get("max_len"))
else:
    print("FAIL: 人设正文上限还是 %s" % (_ent or {}).get("max_len")); bad += 1

# 9) 网页人格管理保存也必须遵守文件模式（预设=role，自定义=system，二者互斥）
from qbot import qbot_persona as QP
_saved_ai = dict(bot.CFG.get("ai", {}))
_saved_roles = (bot.ROLES, QP.ROLES)
_saved_state = bot.STATE
_saved_hooks = (bot.save_config, bot._clear_history)
try:
    with tempfile.TemporaryDirectory(prefix="persona_save_test_") as td:
        bot.ROLES = QP.ROLES = Path(td)
        bot.STATE = Path(td)
        (Path(td) / "测试人格.md").write_text("旧正文\n", encoding="utf-8")
        bot.CFG["ai"].update({"role": "测试人格", "system": "", "persona_custom": False})
        bot.save_config = lambda: None
        bot._clear_history = lambda *a, **kw: None
        r1 = bot.action_personas("save", name="测试人格", content="新正文")
        if (r1.get("ok") and bot.CFG["ai"].get("role") == "测试人格"
                and not bot.CFG["ai"].get("system")
                and (Path(td) / "测试人格.md").read_text(encoding="utf-8").strip() == "新正文"):
            print("OK: 当前预设保存后仍走 role 文件模式，system 保持空")
        else:
            print("FAIL: 当前预设保存退回旧模式: %r / %r" % (r1, bot.CFG["ai"])); bad += 1
        r2 = bot.action_personas("save", name="自定义", content="独立自定义正文")
        if (r2.get("ok") and not bot.CFG["ai"].get("role")
                and bot.CFG["ai"].get("system") == "独立自定义正文"
                and bot.CFG["ai"].get("persona_custom") is True):
            print("OK: 自定义保存清掉 role，正文不会被预设文件盖住")
        else:
            print("FAIL: 自定义保存仍有双来源: %r / %r" % (r2, bot.CFG["ai"])); bad += 1
finally:
    bot.ROLES, QP.ROLES = _saved_roles
    bot.STATE = _saved_state
    bot.save_config, bot._clear_history = _saved_hooks
    bot.CFG["ai"].clear(); bot.CFG["ai"].update(_saved_ai)

# 整个文件的所有用例结束后统一还原，避免后续 save_config() 把测试状态写进真配置。
bot.CFG["ai"].clear(); bot.CFG["ai"].update(_AI_ORIG)
print("RESULT:", "PASS" if bad == 0 else "FAIL")
