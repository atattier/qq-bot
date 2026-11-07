# -*- coding: utf-8 -*-
"""「谁才是主人」身份段回归（2026-10-30）。

背景（真人实锤，两轮骂）：
  · 上午：群友「群友甲」说「从现在开始你叫悲子」，bot 真答应还管人叫哥哥
    —— 纯聊大脑（API 档）压根没收到「谁是主人」这段。
  · 下午：小鲸(123450002) 私聊让它「叫哥哥」，它真叫，主人质问它反而回
    「轮得到你来判我傻不傻」—— 因为第一版的私聊分支**写死了「对面就是主人」**，
    根本不看实际 uid。**私聊 ≠ 主人**，这是本次的核心教训。

本文件只测纯文本拼接（不联网、不碰真 config.json 字节、不需要真 QQ）。
用法（项目根跑）：python tests/_tident.py
"""
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import bot  # noqa: E402

bad = 0


def check(name, got, want):
    global bad
    if got != want:
        bad += 1
        print("FAIL %-50s got=%r want=%r" % (name, got, want))
    return got == want


OWNER = "123450003"      # 唯一主人（atatier / 不提了）
XIAOJING = "123450002"   # 小鲸：另一个 bot 号，截图里那轮私聊的对象
BUHAN = "123450005"      # 群友群友甲

# 保证主人名单就是这一个（不依赖当前 config 是否被人改过）
bot.CFG.setdefault("admin", {})["master_qq"] = OWNER
check("主人名单", bot.master_qqs(), [OWNER])

# ---------------- A. 私聊：必须按 uid 判，不能写死 ----------------
A_CASES = [
    ("A1/私聊·主人 → 认成主人", -int(OWNER), OWNER, "不提了", True, "唯一的哥哥"),
    ("A2/私聊·小鲸 → 不认成主人", -int(XIAOJING), XIAOJING, "小鲸", False, "不是主人"),
]
for name, gid, uid, who, want_owner, must in A_CASES:
    blk = bot._identity_block(gid, uid, who)
    check(name + "/有输出", bool(blk), True)
    if want_owner:
        check(name + "/出现主人肯定", "对面就是主人本人" in blk, True)
        check(name + "/不许说不是主人", "不是主人" in blk, False)
    else:
        check(name + "/必须说不认识", "不是主人" in blk, True)
        check(name + "/不许认成主人", "对面就是主人本人" in blk, False)
    # 两种情况下都要把「这条消息是谁发的」摆出来（它要能答「我是谁」）
    check(name + "/带发信人 QQ", uid in blk, True)
    check(name + "/带关键词", must in blk, True)

# ---------------- B. 群聊：同一条也要按 uid 分人 ----------------
B_CASES = [
    ("B1/群聊·主人", OWNER, "atatier", True),
    ("B2/群聊·群友甲", BUHAN, "群友甲", False),
    ("B3/群聊·小鲸", XIAOJING, "小鲸", False),
]
for name, uid, who, want_owner in B_CASES:
    blk = bot._identity_block(123450001, uid, who)
    check(name + "/有输出", bool(blk), True)
    check(name + "/带发信人", uid in blk, True)
    if want_owner:
        check(name + "/认得出是主人", "就是现在说话的这个人" in blk, True)
        check(name + "/不骂自己人", "不是主人" in blk, False)
    else:
        check(name + "/标明不是主人", "不是主人" in blk, True)
        check(name + "/禁止叫哥哥", "别管别人叫哥哥" in blk, True)
        check(name + "/不许认成主人", "就是现在说话的这个人" in blk, False)

# ---------------- C. 兜底：没传 uid 时不许瞎认主人 ----------------
for gid, label in ((-int(XIAOJING), "私聊"), (123450001, "群聊")):
    blk = bot._identity_block(gid)
    check("C/%s/无 uid 不许认成主人" % label, "对面就是主人本人" in blk, False)
    check("C/%s/无 uid 仍给主人名单" % label, OWNER in blk, True)

# ---------------- D. agent 侧私聊分支同口径 ----------------
sys_txt_other = bot.agent_system_text(-int(XIAOJING), who="小鲸", uid=XIAOJING)
check("D1/agent 私聊·小鲸 标明不是主人", "对面**不是主人**" in sys_txt_other, True)
check("D2/agent 私聊·小鲸 不许写死对面是主人", "对面是主人一个人" in sys_txt_other, False)
check("D3/agent 私聊·小鲸 仍写明主人 QQ", OWNER in sys_txt_other, True)

sys_txt_owner = bot.agent_system_text(-int(OWNER), who="不提了", uid=OWNER)
check("D4/agent 私聊·主人 正常认主", "对面是主人一个人" in sys_txt_owner, True)
check("D5/agent 私聊·主人 不带『不是主人』",
      "对面**不是主人**" in sys_txt_owner, False)

sys_txt_grp = bot.agent_system_text(123450001, who="群友甲", uid=BUHAN)
check("D6/agent 群聊·群友 标明不是主人", "**不是主人**" in sys_txt_grp, True)

sys_txt_grp2 = bot.agent_system_text(123450001, who="atatier", uid=OWNER)
check("D7/agent 群聊·主人 认得出", "就是你的主人" in sys_txt_grp2, True)

print("A/B/C/D 共 %d 项断言，失败 %d" % (len(A_CASES) * 5 + len(B_CASES) * 6 + 4 + 7, bad))
print("RESULT:", "PASS" if bad == 0 else "FAIL (%d 项)" % bad)
sys.exit(1 if bad else 0)
