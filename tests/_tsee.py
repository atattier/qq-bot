# -*- coding: utf-8 -*-
"""聊天识图「不只是认人、什么图都要看懂」回归（2026-10-10 主人要求）。

全打桩、不联网、不碰真 config.json 字节。

覆盖三块：
  A. reverser._split_role —— 看图输出末尾 `ROLE: yes/no` 标记行的切分
  B. bot 侧「像不像作品角色图」的双判据（模型标记优先 / 关键词兜底）
  C. bot.sim_pic_chat 端到端分支：非角色图要不要接话、角色图走不走认人

用法（项目根跑）：python tests/_tsee.py
"""
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import bot, reverser  # noqa: E402

bad = 0


def check(name, got, want):
    global bad
    if got != want:
        bad += 1
        print("FAIL %-46s got=%r want=%r" % (name, got, want))
    return got == want


# ---------------- A. ROLE 标记切分 ----------------
A_CASES = [
    ("带 yes（末尾）", "一只橘猫躺在沙发上。\nROLE: yes", "一只橘猫躺在沙发上。", True),
    ("带 no（末尾）", "五个鸡蛋放在白盘子里。\nROLE: no", "五个鸡蛋放在白盘子里。", False),
    ("中文冒号 + 大小写", "雪山湖泊风景照\nROLE：NO", "雪山湖泊风景照", False),
    ("标记在开头", "ROLE: yes\n一名银发少女", "一名银发少女", True),
    ("无标记 → None", "只是一段描述，没有标记", "只是一段描述，没有标记", None),
    ("两个标记取最后一个", "前文\nROLE: yes\nROLE: no", "前文", False),
    ("标记后还有废话", "控制台截图\nROLE: no（界面截图）", "控制台截图", False),
    ("空串", "", "", None),
]
for name, raw, want_body, want_flag in A_CASES:
    body, flag = reverser._split_role(raw)
    check("A/" + name, (body.strip(), flag), (want_body.strip(), want_flag))
print("A. _split_role: %d 项" % len(A_CASES))


# ---------------- B. 关键词兜底闸门（画风词 ∧ 人形词）----------------
def gate(desc):
    return bool(desc) and bool(bot._CHAR_STYLE_HINT.search(desc)
                               and bot._CHAR_BODY_HINT.search(desc))


B_CASES = [
    ("一盘鸡蛋（写成「插画」）", "这是一幅简单示意图，一个白色椭圆盘子里摆着五个淡黄色的蛋。", False),
    ("巨龙（写成「概念插画」）", "左右拼接的奇幻龙类插画，两侧各有一只黑鳞龙头。", False),
    ("界面截图（含「动画人物」字样）",
     "软件控制台界面截图，列出写实人物、动画人物、写实风景三个选项。", False),
    ("风景照", "雪山与湖泊的实景照片，天色阴沉。", False),
    ("动漫少女", "一张动漫截图，画面中一名银紫色长发、金瞳的二次元少女。", True),
    ("游戏角色立绘", "游戏角色立绘：一名持剑的少女，站在城墙上。", True),
]
for name, desc, want in B_CASES:
    check("B/" + name, gate(desc), want)
print("B. 关键词兜底闸门: %d 项" % len(B_CASES))


# ---------------- C. sim_pic_chat 端到端（全打桩）----------------
SENT, CALLED = [], {}


class _StubRev:
    def __init__(self, desc, flag):
        self.desc, self.flag = desc, flag

    def describe_scene(self, path, cfg, provider=None, model=None):
        CALLED["desc"] = CALLED.get("desc", 0) + 1
        return self.desc, "stub/模型", self.flag

    def identify(self, path, cfg, lookup=None, provider=None, model=None,
                 require_lookup=False):
        CALLED["ident"] = CALLED.get("ident", 0) + 1
        return {"known": False, "name": "", "via": "stub"}


def run_pic(desc, flag, scene_on=True, ident=None):
    """跑一次 sim_pic_chat，返回发出去的话（没发就是 ""）。"""
    SENT.clear()
    CALLED.clear()
    # 🔴 2026-10-30：本文件测的是**两跳老链路**（视觉模型描述 → 大脑照着文字接话）。
    #    新加的两条「一跳」路径必须显式关掉，否则它们会**真联网**——本次第一版没关，
    #    _tsee 跑了 34.9s 还真调了 custom 大脑（日志「总控大脑看图…用工具=True」），
    #    3 项断言因此挂掉。这两条各自有专门的守卫：`_tsee_follow.py`（一图直达）、
    #    `_tomni.py`（总控大脑）。
    bot.CFG.setdefault("ai", {})["brain_sees_image"] = False
    bot.CFG["ai"]["omni_brain"] = False
    bot.reverser = _StubRev(desc, flag)
    bot.webai_identify_chat = lambda img, timeout=None: ident or {"known": False}
    bot.chat = lambda msgs, **kw: (None, "（模型口吻）" + (desc or "")[:20])
    bot.send_bubbles = lambda gid, ans, **kw: SENT.append(ans)
    bot._fetch_rev_image = lambda x: r"state\_vqa_eggs.png"
    cfg = bot.CFG.setdefault("sim", {})
    cfg["pic_chat_scene"] = scene_on
    bot.sim_pic_chat(123450001, "群友A", ["http://x/y.png"], addressed=False)
    return (SENT[0] if SENT else "")


# C1 非角色图（鸡蛋）→ 没认人、直接用画面接话
out = run_pic("白色盘子里摆着五个淡黄色的蛋。", False)
check("C1/非角色图要接话", bool(out), True)
check("C1/非角色图不跑认人", CALLED.get("ident"), None)

# C2 同一个非角色图 + 开关关掉 → 闭嘴（退回老行为）
out = run_pic("白色盘子里摆着五个淡黄色的蛋。", False, scene_on=False)
check("C2/开关关掉就闭嘴", out, "")

# C3 角色图（flag=True）→ 会去认人
run_pic("动漫截图，一名银发金瞳的二次元少女。", True)
check("C3/角色图会跑认人", CALLED.get("ident"), 1)

# C4 认出角色 → 发的是角色名那条（认人成功，不走画面描述）
out = run_pic("动漫截图，一名银发金瞳的二次元少女。", True,
              ident={"known": True, "name": "Hatsune Miku", "series": "Vocaloid",
                     "confidence": 0.9, "via": "stub"})
check("C4/认出角色仍会说话", bool(out), True)

# C5 看图模型没给标记（None）+ 描述像角色 → 兜底判据生效，去认人
run_pic("动漫截图，一名银发金瞳的二次元少女。", None)
check("C5/标记缺失时兜底判据生效", CALLED.get("ident"), 1)

# C6 看图模型没给标记 + 描述不像角色 → 不认人
run_pic("软件控制台界面截图，列出动画人物选项。", None)
check("C6/兜底不误判界面截图", CALLED.get("ident"), None)

# C7 视觉链全挂（描述空）+ 认不出人 → 闭嘴，不许拿空描述套话
out = run_pic("", None)
check("C7/没看懂就闭嘴", out, "")

# C8 描述空但认人链仍要试一次（别让网页版那条通道连带哑火）
run_pic("", None)
check("C8/描述空也试一次认人", CALLED.get("ident"), 1)

print("C. sim_pic_chat 分支: 8 项")
print("RESULT:", "PASS" if bad == 0 else "FAIL (%d 项)" % bad)
sys.exit(1 if bad else 0)
