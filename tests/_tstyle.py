# -*- coding: utf-8 -*-
"""A1 验收：反 GPT 腔提示词改造前后对照（MANUAL，真网关，会花钱）。

**做什么**：固定 10 条日常闲聊，分别用「改造后」与「改造前」的 system 提示词
各跑一遍真模型（同一模型、同一温度、同一历史），两边输出逐条对照。

改造前的提示词从 git 里取，不手抄：
    git show ab7b83c:qbot_persona.py   （ab7b83c = 改造前最后一版）
    git show ab7b83c:bot.py            （取旧 voice 行做替换）
取不到就跳过对照、只跑改造后（绝不伪造基线）。

**判定（机械痕迹检测，确定性规则，不靠人眼看）**：
  客服腔  —— 「好的」「没问题」「希望对你有帮助」「如需」「随时告诉我」「请问还有什么」
  机械总结 —— 「总的来说」「总之」「综上」「希望对你有帮助」「以上」
  无意义追问 —— 句尾「还有什么」「还需要」「要不要我再」，或整句只有一个问句且没有信息
  模板分点 —— 「第一」「其一」「1.」（正文里出现编号列表开头）
  过长     —— 闲聊回复 >180 字（改前改后都记）

⚠️ 这不是「改后必须全 0」的硬闸 —— 措辞是概率性的。它给的是**可对比的证据**：
   改后每一类的命中数不应高于改前；若某类变差，说明该条改动起了反作用。

用法：
    python tests/_tstyle.py            # 跑对照（需要 git 里有 ab7b83c）
    python tests/_tstyle.py --after    # 只跑改造后（基线取不到时）
"""
import json
import os as _os
import re
import subprocess
import sys
import time

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import bot  # noqa: E402

bot.save_config = lambda: True
bot.SELF_ID = 123450002
GID = 123450001
UID = 123450005          # 主人号：闲聊对照走「主人私聊」口径，最接近真实使用

BASELINE_COMMIT = "ab7b83c"

# 固定闲聊集：覆盖打招呼 / 求安慰 / 吐槽 / 玩梗 / 纯陈述 / 追问 / 求推荐 / 反问 / 短回复场景
CHATS = [
    "在吗",
    "今天好累啊，不想动",
    "这游戏也太难了吧",
    "我刚吃完饭",
    "你说是先有鸡还是先有蛋",
    "帮我推荐个好看的番",
    "你怎么看那种天天加班的人",
    "哦",
    "哈哈哈哈哈",
    "我刚才把咖啡撒键盘上了……",
]

# ---- 机械痕迹检测器 ----------------------------------------------------
PATTERNS = {
    "客服腔": re.compile(
        r"好的[，,。！!]|没问题[！!。]|希望对(你|您)有帮助|如需|随时(告诉|联系)我|"
        r"请问还有什么|很高兴(为|帮)|乐意为您|不客气[，,]"),
    "机械总结": re.compile(r"总的来说|总之[，,]|综上|以上(就是|所述)|希望(这些|以上)"),
    "无意义追问": re.compile(
        r"还有什么|还需要[^。！？\n]{0,6}[吗呢？?]|要不要我再|需要我(帮|做)[^。！？\n]{0,8}[吗？?]|"
        r"还想(听|聊|看)[^。！？\n]{0,6}[吗？?]"),
    "模板分点": re.compile(r"(^|\n)\s*(第[一二三四五]|其[一二三]|1[.、]|2[.、]|- )"),
}
LONG_CHARS = 180


def detect(text):
    t = str(text or "")
    hits = {k: bool(p.search(t)) for k, p in PATTERNS.items()}
    hits["过长"] = len(t) > LONG_CHARS
    return hits, len(t)


# ---- 取改造前基线提示词 ------------------------------------------------
def _git_show(path):
    try:
        out = subprocess.run(["git", "show", "%s:%s" % (BASELINE_COMMIT, path)],
                             capture_output=True, timeout=30)
        if out.returncode != 0:
            return ""
        return out.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def old_base_rules():
    """从旧 qbot_persona.py 里抽出 BASE_RULES 字面量。"""
    src = _git_show("qbot_persona.py")
    m = re.search(r'BASE_RULES = """(.*?)"""', src, re.S)
    return m.group(1) if m else ""


def old_voice_line():
    """从旧 bot.py 里抽 voice 第一条（改造后那里改成了「按铁律来」）。"""
    src = _git_show("bot.py")
    m = re.search(r'voice = \["(3\..*?)",\n', src, re.S)
    return m.group(1) if m else ""


# ---- 跑一条 ------------------------------------------------------------
def run_one(text, persona_rules_override=None, voice_override=None):
    """用当前生产链路拼 system（可选替换铁律/voice 行），跑一轮真模型。"""
    import qbot_persona
    _old_rules = qbot_persona.BASE_RULES
    _old_voice = None
    if persona_rules_override:
        qbot_persona.BASE_RULES = persona_rules_override
    try:
        if voice_override is not None:
            bot.SELF_ID = bot.SELF_ID
        sys_txt = bot.build_messages(GID, text, "", extra=None, slang=True,
                                     remember=False, who="测试主人", uid=str(UID))[0]["content"]
        if voice_override:
            new_line = "3. 口语和句式风格按最上面的【铁律】来，这里只管量："
            if new_line in sys_txt:
                sys_txt = sys_txt.replace(new_line, voice_override, 1)
        bot.HISTORY.clear()
        name, ans, _used = bot.brain_with_tools(
            GID, text, sys_text=sys_txt, who="测试主人", uid=str(UID),
            privileged=True, imgs=(), qmid=0, mid=0)
        return (ans or "").strip()
    except Exception as e:
        return "【调用失败】%s" % str(e)[:120]
    finally:
        qbot_persona.BASE_RULES = _old_rules


def main():
    only_after = "--after" in sys.argv
    old_rules = "" if only_after else old_base_rules()
    old_voice = "" if only_after else old_voice_line()
    if not only_after and (not old_rules or not old_voice):
        print("⚠️ 取不到 %s 的基线提示词，只跑改造后（不伪造对照）。" % BASELINE_COMMIT)
        only_after = True

    p = bot.provider_cfg("custom") or {}
    print("网关:", p.get("url"), "| 模型:", p.get("model"))
    print("对照基线:", (BASELINE_COMMIT + " 的 BASE_RULES/voice") if not only_after else "无")
    print()

    rows = []
    for i, text in enumerate(CHATS, 1):
        after = run_one(text)
        before = "" if only_after else run_one(text, old_rules, old_voice)
        ha, la = detect(after)
        hb, lb = (detect(before) if before else ({}, 0))
        rows.append({"no": i, "text": text, "after": after, "before": before,
                     "after_hits": ha, "before_hits": hb, "after_len": la, "before_len": lb})
        print("── #%d %s" % (i, text))
        if before:
            print("   改前(%d字): %s" % (lb, before[:200]))
        print("   改后(%d字): %s" % (la, after[:200]))
        marks = [k for k, v in ha.items() if v]
        if before:
            bmarks = [k for k, v in hb.items() if v]
            print("   痕迹  改前=%s 改后=%s" % (bmarks or "无", marks or "无"))
        else:
            print("   痕迹  改后=%s" % (marks or "无"))
        print()
        time.sleep(1.2)      # 别把网关打满

    print("=== 汇总 ===")
    def tally(key):
        n = 0
        for r in rows:
            if r[key].get(k):
                n += 1
        return n
    for k in list(PATTERNS) + ["过长"]:
        line = "  %-6s 改后 %d/%d" % (k, tally("after_hits"), len(rows))
        if not only_after:
            line += "   改前 %d/%d" % (tally("before_hits"), len(rows))
        print(line)
    if not only_after:
        worse = [k for k in list(PATTERNS) + ["过长"] if tally("after_hits") > tally("before_hits")]
        print("  变差的类别:", worse or "无")
    out = _os.path.join(bot.STATE, "_tstyle_result.json")
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("详细结果:", out)


if __name__ == "__main__":
    main()
