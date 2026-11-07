# -*- coding: utf-8 -*-
"""技能包加载器 —— 离线自测（2026-10-19）。

覆盖 qbot_skills 的加载/校验/开关/参数/提示词预算，以及 bot 侧的三处注入点，
另外验一下「技能」口令的正则不会把正常聊天吃掉。全程不联网、不发消息。

用法：python tests/_tskills.py
"""
import os as _os
import sys as _sys
import json
import tempfile
from pathlib import Path

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)

_sys.stdout.reconfigure(encoding="utf-8")

from qbot import qbot_skills as S    # noqa: E402
import bot                 # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [!!] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


print("== 清单扫描 ==")
skills = S.scan(force=True)
ids = [s["id"] for s in skills]
chk("扫到技能包（含 fibi-memes / memewiki / time-aware 等）",
    set(["fibi-memes", "memewiki", "petpet", "trendy-memes", "whale-memes"]).issubset(set(ids)), ids)
tm = S.get("trendy-memes")
chk("trendy 名称/版本解析正确", tm and tm["name"] == "网络流行梗" and tm["version"] == "1.0.0")
chk("trendy 有提示词片段", tm and len(tm["sections"]) == 1 and tm["sections"][0]["priority"] == 57)
chk("whale 参数清单读到了", S.get("whale-memes") and len(S.get("whale-memes")["schema"]) >= 10)
chk("index.js 工具名嗅探", tm and "查网络梗" in tm["tools"], tm and tm["tools"])
chk("没有加载错误", all(not s.get("err") for s in skills))

print("== 开关与参数 ==")
# config.json 是主人点过的真实状态（比如 whale-memes 被关过），这里先把覆盖清空，
# 测「清单默认」而不是「线上现状」，否则这个用例会随主人的点击时好时坏。
_cfg_sk = bot.CFG.setdefault("skills", {})
_live_en, _live_set = dict(_cfg_sk.get("en") or {}), dict(_cfg_sk.get("settings") or {})
_cfg_sk["en"], _cfg_sk["settings"] = {}, {}
chk("默认都开", S.is_on("trendy-memes") and S.is_on("whale-memes"))
o, msg = S.set_on("whale-memes", False)
chk("关掉 whale 成功", o and "已关闭" in msg)
chk("关掉后 is_on=False", not S.is_on("whale-memes"))
o, msg = S.set_on("不存在", True)
chk("开一个不存在的技能要报错", (not o) and "没有这个技能" in msg)
o, msg = S.set_setting("whale-memes", "maxCount", "2")
chk("数字参数接受字符串", o and S.settings_of("whale-memes")["maxCount"] == 2)
o, msg = S.set_setting("whale-memes", "maxCount", "abc")
chk("数字参数拒绝非数字", (not o) and "数字" in msg)
o, msg = S.set_setting("whale-memes", "character", "nope")
chk("枚举参数拒绝非法值", (not o) and "只能是" in msg)
o, msg = S.set_setting("whale-memes", "not-a-key", "1")
chk("未知参数给出可调项列表", (not o) and "没有参数" in msg)
S.set_on("whale-memes", True)

print("== 提示词拼装 ==")
blk = S.prompt_block()
chk("两个技能都在提示词里", ("网络流行梗" in blk) and ("梗鲸表情包" in blk))
chk("带上「先查再用」规则原文", "先查再用" in blk)
chk("本机已接的工具改成教怎么用", "【本机用法】" in blk and "[[梗图:" in blk)
chk("没有「未接入」的告警了", "尚未接入本机" not in blk)
chk("tools_note 空了（都接上了）", S.tools_note() == "", S.tools_note())
S.set_on("whale-memes", False)
chk("关掉后该技能不在提示词里", "梗鲸表情包" not in S.prompt_block())
S.set_on("whale-memes", True)
small = S.prompt_block(max_chars=200)
chk("预算生效（不超 200+尾巴）", len(small) <= 260, len(small))
bot.CFG.setdefault("skills", {})["enabled"] = False
chk("总开关关掉 = 空串", S.prompt_block() == "")
bot.CFG["skills"]["enabled"] = True

print("== 坏清单不崩 ==")
tmp = Path(tempfile.mkdtemp(prefix="skills_test_"))
(tmp / "broken").mkdir()
(tmp / "broken" / "skill.json").write_text("{不是 json", encoding="utf-8")
(tmp / "noid").mkdir()
(tmp / "noid" / "skill.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")
old_dir = bot.CFG["skills"]["dir"]
bot.CFG["skills"]["dir"] = str(tmp)
bad = S.scan(force=True)
chk("坏包不抛异常", len(bad) == 2, [b["id"] for b in bad])
chk("坏 json 记成 err", any("读不了" in (b.get("err") or "") for b in bad))
bot.CFG["skills"]["dir"] = old_dir
S.scan(force=True)

print("== bot 侧注入 ==")
chk("skills_block 有内容", len(bot.skills_block()) > 200)
kn = bot.knob_items("技能")
keys = [i["key"] for i in kn]
chk("后台有技能总开关", "skills.enabled" in keys)
# 2026-10-26 起，每个插件自己的开关/参数/提示词都归到「插件列表」那一组（前端按 skill 再分组），
# 不再挂在「技能」组里 —— 所以这里要按 bot._SKILL_GROUP 取。
pkeys = [i["key"] for i in bot.knob_items(bot._SKILL_GROUP)]
chk("后台每个技能一个开关",
    "skills.en.whale-memes" in pkeys and "skills.en.trendy-memes" in pkeys, pkeys[:6])
chk("技能清单是只读项", [i for i in kn if i["key"] == "skills.list"][0]["writable"] is False)
priv = bot.priv_key(123450003)
msgs = bot.build_messages(123450001, "这啥梗", extra=[bot.skills_block()])
chk("聊天链路 system 里也有", "【能用的小技能】" in msgs[0]["content"])

print("== 口令正则 ==")
R = bot.SKILL_CMD_RE
chk("「技能」命中", R.match("技能") is not None)
chk("「/skill off whale-memes」命中", R.match("/skill off whale-memes") is not None)
chk("「skills」命中", R.match("skills") is not None)
chk("「技能工人」不命中（别吃正常话）", R.match("技能工人难招") is None)
chk("「技巧」不命中", R.match("技巧") is None)
chk("正则命中但查不到 id 时还回去当聊天", bot.handle_skill(123450001, "技能这东西真有用") is False)
chk("带斜杠才强查（不会发出去，只验判定）",
    R.match("/技能 不存在的包") is not None)

print("== 原生工具：查梗 ==")
from qbot import qbot_skill_tools as T     # noqa: E402
st_tmp = Path(tempfile.mkdtemp(prefix="skilltools_test_"))
SENT = []


def _fake_send(cid, path, caption=""):
    SENT.append((cid, Path(path).name, caption))
    return True


T.bind(cfg=bot.CFG, state_dir=str(st_tmp), base=_ROOT, send_image_file=_fake_send)
chk("NATIVE 注册了五个工具", T.NATIVE["trendy-memes"] == ["catalog", "lookup", "teach"]
    and T.NATIVE["whale-memes"] == ["list", "send"], T.NATIVE)
o, t = T.call("trendy-memes", "lookup", term="yyds")
chk("查梗命中", o and "yyds" in t and "怎么用" in t)
o, t = T.call("trendy-memes", "lookup", term="完全不存在的词zzz")
chk("查不到就明说 + 提示收录", (not o) is False and "梗库里没有" in t)
o, t = T.call("trendy-memes", "lookup", term="")
chk("空词要拒绝", not o and "哪个梗" in t)
o, t = T.call("trendy-memes", "catalog", limit=5)
chk("目录能列", o and "梗库" in t)
o, t = T.call("trendy-memes", "teach", term="测试梗ABC", meaning="自测用", usage="例句")
chk("收录新梗成功", o and "记下了" in t)
o, t = T.call("trendy-memes", "lookup", term="测试梗ABC")
chk("收录后立刻查得到", o and "自测用" in t)
o, t = T.call("trendy-memes", "teach", term="测试梗ABC", meaning="改过")
chk("同名重复 = 覆盖", o and "更新" in t)
o, t = T.call("trendy-memes", "teach", term="", meaning="x")
chk("收纳缺参要拒绝", not o)
chk("自学习库落盘在 state/skills 下", (st_tmp / "skills" / "trendy-memes").exists())
o, t = T.call("trendy-memes", "nope")
chk("没这个工具要报错并列出已有", not o and "没有这个技能工具" in t)

print("== 原生工具：挑图/发图（本地桩，不联网） ==")
_fake_items = [
    {"id": "a1", "character": "DeepSeek娘", "url": "http://x/a1.png",
     "preview": "http://x/a1s.png", "name": "生气", "caption": "『气死你！』",
     "story": "用户彻底生气了", "alt": "生气", "category": "DeepSeek娘"},
    {"id": "a2", "character": "DeepSeek娘", "url": "http://x/a2.png",
     "preview": "http://x/a2s.png", "name": "无语", "caption": "『无语』",
     "story": "懒得说话", "alt": "无语", "category": "DeepSeek娘"},
    {"id": "a3", "character": "Qwen娘", "url": "http://x/a3.png",
     "preview": "http://x/a3s.png", "name": "夸人", "caption": "『厉害』",
     "story": "被夸了", "alt": "夸", "category": "Qwen娘"},
]
T.whale_catalog = lambda force=False: {"items": list(_fake_items), "cats": [("DeepSeek娘", 2)],
                                       "ts": 0, "via": "test"}
_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
T._get = lambda url, timeout=15, accept="*/*": (200, _PNG)
o, t = T.call("whale-memes", "list", keyword="生气", limit=3, cid=123450001)
chk("挑图返回候选带编号", o and "1." in t and "DeepSeek娘" in t, t)
o, t = T.call("whale-memes", "send", cid=123450001, keyword="生气")
chk("发图走通了", o and SENT and SENT[-1][0] == 123450001, SENT[-1:])
chk("发出去的文件名 = 图 id", SENT and SENT[-1][1].startswith("a"))
o, t = T.call("whale-memes", "send", cid=-123450003, keyword="夸人", caption="好耶")
chk("私聊 key（负数）也发得出去", o and SENT[-1][0] == -123450003 and SENT[-1][2] == "好耶")
_SENT0 = len(SENT)
o, t = T.call("whale-memes", "send", cid=123450001, keyword="")
chk("空关键词也能挑（随机兜底）", (o and len(SENT) > _SENT0) or "没挑到" in t)
_tmpdir = st_tmp / "skills" / "whale-memes" / "tmp"
_before = len(list(_tmpdir.glob("*")))
T._get = lambda url, timeout=15, accept="*/*": (200, b"<html>404</html>")
o, t = T.call("whale-memes", "send", cid=123450001, keyword="生气")
chk("下回来的不是图 ⇒ 拒发（别发出破图）", not o and "下载不下来" in t)
chk("坏图没留在 tmp 里", len(list(_tmpdir.glob("*"))) == _before)
T._get = lambda url, timeout=15, accept="*/*": (200, _PNG)
o, t = T.call("whale-memes", "send", cid=777, keyword="生气")
chk("新会话第一张发得出去", o, t)
S.set_setting("whale-memes", "maxPerHour", "1")
o, t = T.call("whale-memes", "send", cid=777, keyword="生气")
chk("额度用完要挡（含原因）", not o and "额度" in t, t)
S.set_setting("whale-memes", "maxPerHour", "0")
o, t = T.call("whale-memes", "send", cid=778, keyword="生气")
chk("额度设 0 = 一张都不许（0 不能被当成没填）", not o and "额度" in t, t)
S.set_setting("whale-memes", "maxPerHour", "6")
S.set_setting("whale-memes", "proactive", "false")
o, t = T.call("whale-memes", "send", cid=999)
chk("关了主动配图又没给关键词 ⇒ 挡", not o and "主动配图" in t, t)
S.set_setting("whale-memes", "proactive", "true")

print("== bot 侧接线 ==")
chk("action_skill 透传（ok/text）",
    bot.action_skill("trendy-memes", "lookup", {"term": "yyds"})["ok"] is True)
bad = bot.action_skill("whale-memes", "send", {"cid": None})
chk("缺 cid 不炸，返回可读错误", bad["ok"] is False and bad["text"])
clean, memes = bot.take_meme_markers(123450001, "笑死\n[[梗图:生气]]")
chk("标记被吃掉、正文留下", clean == "笑死" and memes == [("生气", "")], (clean, memes))
clean, memes = bot.take_meme_markers(123450001, "[[梗图:夸人|Qwen娘]] 好耶")
chk("带角色名也认", memes == [("夸人", "Qwen娘")] and clean == "好耶")
clean, memes = bot.take_meme_markers(123450001, "普通一句话 [图:猫]")
chk("不碰图库的单括号标记", memes == [] and clean == "普通一句话 [图:猫]")
chk("没有标记就原样返回", bot.take_meme_markers(1, "hi") == ("hi", []))
mcp = (_ROOT and Path(_ROOT) / "qq-mcp-server.py").read_text(encoding="utf-8")
chk("MCP 暴露五个梗工具", all(('"%s"' % n) in mcp for n in
      ("qq_meme_lookup", "qq_meme_catalog", "qq_meme_teach", "qq_meme_pick", "qq_meme_send")))
chk("MCP 都路由到 /api/skill", mcp.count('"/api/skill"') == 9)
# 5 梗工具 + qq_petpet + 2 个菲比 + 梗百科（qq_meme_lookup 先查 memewiki 再回落，2026-10-26）
# U12e 路由表化后不再有 if/elif 字面量；改为验注册表里确实登记了这个端点
# （且由 do_POST 的 _API_POST_ROUTES dispatcher 消费），别咬死某一种实现写法。
_bot_src = (Path(_ROOT) / "bot.py").read_text(encoding="utf-8")
chk("bot 有 /api/skill 路由",
    '("/api/skill",' in _bot_src and "_API_POST_ROUTES" in _bot_src
    and "_api_post_skill" in _bot_src)

print("== 手动口令（「技能 查梗 / 技能 梗图」） ==")
_out = []
bot.send_group = lambda gid, text, *a, **kw: (_out.append(text), True)[1]
bot.handle_skill(123450001, "技能 查梗 yyds")
chk("「技能 查梗 yyds」直接回梗义", _out and "yyds" in _out[-1])
bot.handle_skill(123450001, "技能 梗图 生气")
chk("非管理员手动发图要挡", _out and "管理员权限" in _out[-1])
_real_call = T.call
T.call = lambda s, t, **kw: (True, "假发送 ok（%s）" % kw.get("keyword"))
bot.handle_skill(123450001, "技能 梗图 生气|DeepSeek娘", privileged=True)
chk("管理员手动发图走通", _out and "配图：" in _out[-1] and "假发送" in _out[-1], _out[-1])
T.call = _real_call
chk("「技能 xx」还能查详情（不误判成子命令）",
    bot.handle_skill(123450001, "技能 whale-memes") is True
    and "梗鲸" in (_out[-1] or ""))

print("== 明确要梗图（旧正则直发已删，落主脑 send_meme 工具；技能本身仍可直调） ==")
_calls = []
_real_call2 = T.call
T.call = lambda s, t, **kw: (_calls.append((s, t, kw)) or (True, "假发 ok"))
# 主脑路径行为由 _troute（AI聊天认领）与工具面覆盖；这里只保技能层契约：
ok, msg = T.call("whale-memes", "send", cid=123450001, keyword="生气", count=1)
chk("技能 send 直调可用", ok is True and _calls and _calls[-1][0] == "whale-memes")
chk("guess_keyword 取最长命中", T.guess_keyword("我今天生气到无语") in ("生气", "无语"),
    T.guess_keyword("我今天生气到无语"))
T.call = _real_call2
chk("guess_keyword 猜不到给空串", T.guess_keyword("今天天气不错") == "")

print("== 自觉配图（回复带情绪自己发） ==")
_calls2 = []
T.call = lambda s, t, **kw: (_calls2.append(kw) or (True, "假发 ok"))
bot.CFG.setdefault("skills", {})
bot.CFG["skills"]["auto_meme"] = True
bot.CFG["skills"]["auto_meme_prob"] = 100     # 概率拉到 100 才好断言
bot.CFG["skills"]["auto_meme_gap"] = 3600
bot._AUTO_MEME["last"].clear()
chk("对方生气 → 自己配一张（关键词=生气）",
    bot.maybe_auto_meme(123450001, "气死我了", "别气啦") is True
    and _calls2 and _calls2[-1].get("keyword") == "生气", _calls2[-1:])
chk("同一会话紧接着不再发（最小间隔）",
    bot.maybe_auto_meme(123450001, "笑死", "哈哈哈") is False)
chk("换个会话可以发（按会话计间隔）",
    bot.maybe_auto_meme(bot.priv_key(123450003), "笑死", "哈哈哈") is True)
bot._AUTO_MEME["last"].clear()
chk("没情绪的对话不配（自觉≠瞎配）",
    bot.maybe_auto_meme(123450001, "今天天气不错", "嗯嗯") is False)
bot._AUTO_MEME["last"].clear()
chk("回复里已经要发图库的图就不叠",
    bot.maybe_auto_meme(123450001, "气死我了", "给你看 [图:猫]") is False)
bot._AUTO_MEME["last"].clear()
bot.CFG["skills"]["auto_meme"] = False
chk("总开关关掉就不发", bot.maybe_auto_meme(123450001, "气死我了", "别气") is False)
bot.CFG["skills"]["auto_meme"] = True
bot.CFG["skills"]["auto_meme_prob"] = 0
bot._AUTO_MEME["last"].clear()
chk("概率 0 = 不发", bot.maybe_auto_meme(123450001, "气死我了", "别气") is False)
bot.CFG["skills"]["auto_meme_prob"] = 100
bot.CFG["skills"]["auto_meme_gap"] = 150
_oldmk = bot.CFG["images"].get("marker")
bot.CFG["images"]["marker"] = "off"
bot._AUTO_MEME["last"].clear()
chk("配图门控 off 就不发", bot.maybe_auto_meme(123450001, "气死我了", "别气") is False)
bot.CFG["images"]["marker"] = _oldmk
chk("四个旋钮都在后台（技能组）",
    all(k in [i["key"] for i in bot.knob_items("技能")]
        for k in ("skills.auto_meme", "skills.auto_meme_prob", "skills.auto_meme_gap",
                  "skills.auto_fibi_prob")))
print("== 自觉配图：全局闸门 + 菲比占比（2026-10-28） ==")
_sent3 = []
T.call = lambda s, t, **kw: (_sent3.append((s, kw.get("keyword"))) or (True, "假发 ok"))
bot.CFG["skills"]["auto_meme_prob"] = 0        # 全局 0：梗鲸/菲比都不许发
bot.CFG["skills"]["auto_fibi_prob"] = 100
bot._AUTO_MEME["last"].clear()
chk("全局概率 0 → 梗鲸/菲比都不发",
    bot.maybe_auto_sticker(123450001, "气死我了", "别气啦") is False
    and not _sent3, _sent3)
bot.CFG["skills"]["auto_meme_prob"] = 100      # 全局抽中后，菲比占比 100 = 全走菲比
chk("全局抽中 + 菲比占比 100 → 走菲比",
    bot.maybe_auto_sticker(123450001, "气死我了", "别气啦") is True
    and _sent3 and _sent3[-1][0] == "fibi-memes", _sent3[-1:])
chk("菲比吃同一个最小间隔",
    bot.maybe_auto_sticker(123450001, "笑死", "哈哈哈") is False)
bot._AUTO_MEME["last"].clear()
_sent3.clear()
bot.CFG["skills"]["auto_fibi_prob"] = 0        # 菲比占比 0 = 全局抽中后全走梗鲸
chk("全局抽中 + 菲比占比 0 → 全梗鲸",
    bot.maybe_auto_sticker(123450001, "气死我了", "别气") is True
    and [s for s, _ in _sent3] == ["whale-memes"], _sent3)
bot._AUTO_MEME["last"].clear()
_sent3.clear()
# 选中池子关着要自动落到另一个（这里优先梗鲸但梗鲸关，应落到菲比）
_orig_is_on = bot.qbot_skills.is_on
bot.qbot_skills.is_on = lambda skill: skill == "fibi-memes"
try:
    chk("选中池子关着 → 自动落到另一个",
        bot.maybe_auto_sticker(123450001, "气死我了", "别气") is True
        and [s for s, _ in _sent3] == ["fibi-memes"], _sent3)
finally:
    bot.qbot_skills.is_on = _orig_is_on
bot._AUTO_MEME["last"].clear()
_sent3.clear()
bot.CFG["skills"]["auto_meme"] = False         # 全局总开关关掉：整个自动配图都不发
chk("全局总开关关掉 → 不发",
    bot.maybe_auto_sticker(123450001, "气死我了", "别气") is False
    and not _sent3, _sent3)
bot.CFG["skills"]["auto_meme"] = True
bot.CFG["skills"]["auto_fibi_prob"] = 25
T.call = _real_call2

_cfg_sk["en"], _cfg_sk["settings"] = _live_en, _live_set   # 把主人的真实开关还回去

print()
print("==== ALL PASS ====" if ok else "==== 有失败 ====")
_sys.exit(0 if ok else 1)