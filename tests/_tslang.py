# -*- coding: utf-8 -*-
"""黑话四件套边界测试（2026-11-03 人味阶段 A2）。

任务书验收项：
  ① 普通词汇不被大批量误判  ② 未确认释义不当成事实
  ③ MANUAL（管理员确认）不可被 AI 推断覆盖  ④ 无关黑话不进 Prompt
  ⑤ 关掉开关后恢复旧行为（语境释义消失、旧黑话块照常）

全打桩不联网：_chat 用假钩子，_slang_lock 保证并发安全。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QBOT_TEST"] = "1"

FAIL = []


def chk(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  | %s" % (detail,) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


from qbot import qbot_slang as S

# 🔴 隔离（2026-11-03 数据事故教训）：slang_observe/learn 都会 save_slang() 落盘，
# 不把 SLANG_FILE 指到临时目录 = 每跑一次测试就把真 state/slang.json 覆盖掉
# （本次 474 条词库就是这么丢的）。测试永远在 tmp 里读写。
import tempfile
from pathlib import Path
_tmpdir = Path(tempfile.mkdtemp(prefix="tslang_"))
S.SLANG_FILE = _tmpdir / "slang.json"

# ---------- 桩：假词库 + 假 LLM ----------
S.SLANG["terms"] = {}
S.CFG = {"slang": {"enabled": True, "auto_infer": True, "context_meaning": True}}


def reset():
    S.SLANG["terms"].clear()
    S.CFG["slang"] = {"enabled": True, "auto_infer": True, "context_meaning": True}


print("== ① 普通词汇不被误判 ==")
reset()
S._chat = lambda msgs: (None, '{"meaning":"谢谢"}')   # 无论怎么问都答「谢谢」
S.CFG["slang"]["auto_infer"] = False   # 关自动推断，防异步和下面的手动 infer_once 抢阈值
S.SLANG["terms"]["拴Q"] = {"count": 3, "status": "pending", "meaning": "", "source": "群聊提取"}
for i in range(4):   # 第 4 次跨过阈值 4
    S.slang_observe(812, 100 + i, "我真的拴Q")
t = S.SLANG["terms"]["拴Q"]
chk("计数累加且证据带真实 mid", t["count"] == 7 and t["evidence"][0]["mid"] == 100,
    (t["count"], t.get("evidence")))
r = S.slang_infer_once("拴Q")
t = S.SLANG["terms"]["拴Q"]
# 字面义 vs 语境义一致 → 判成普通词，不写释义（不注入就是不会被误当黑话用）
chk("字面/语境一致→判为普通词不写释义", r == "literal" and not t.get("meaning"), (r, t.get("meaning")))
chk("普通词释义不进语境块", "拴Q" not in S.slang_guess_block("我真的拴Q"))

print("== ② 未确认释义不当成事实 ==")
reset()
S.SLANG["terms"]["绝绝子"] = {"count": 5, "status": "pending", "meaning": "非常绝",
                              "source": "群聊提取", "meaning_src": "inferred",
                              "meaning_confirmed": False}
blk = S.slang_guess_block("这也绝绝子了吧")
chk("推断释义必须标「猜的」", "绝绝子=非常绝（这个意思是猜的，不一定对）" in blk, blk)
# 确认过的词条：走正式注入通道，猜测块不再出现（事实和猜测不混一块）
S.SLANG["terms"]["开黑"] = {"count": 9, "status": "confirmed", "meaning": "组队打游戏",
                            "source": "管理员确认"}
chk("confirmed 不进猜测块", S.slang_guess_block("今晚开黑吗") == "")
chk("confirmed 仍走正式注入", "开黑=组队打游戏" in S.slang_block())
h = dict((k, g) for k, _, g in S.slang_context_hits("开黑"))
chk("confirmed 词不在命中面里", "开黑" not in h, h)
# 推断写回永远 pending：status 不因推断变 confirmed
reset()
S._chat = lambda msgs: (None, '{"meaning":"跑"}') if "结合" in msgs[1]["content"] else (None, '{"meaning":"润色"}')
S.SLANG["terms"]["润"] = {"count": 8, "status": "pending", "meaning": "", "source": "群聊提取",
                          "evidence": [{"gid": 812, "mid": 501, "text": "润不动了", "t": time.time()}]}
chk("无证据不推断（绝不编造上下文）",
    S.slang_infer_once("裸词") is None and
    S.slang_infer_once.__doc__ is not None)   # 裸词不在库里本来就 None，真正要验的是下面
S.SLANG["terms"]["无证"] = {"count": 8, "status": "pending", "meaning": "", "source": "群聊提取"}
chk("在库但没证据 → 拒绝推断", S.slang_infer_once("无证") is None)
S.slang_infer_once("润")
t = S.SLANG["terms"]["润"]
chk("推断后仍是 pending", t["status"] == "pending" and t.get("meaning_confirmed") is False,
    (t["status"], t.get("meaning_confirmed")))
chk("推断结果标 inferred", t.get("meaning_src") == "inferred", t.get("meaning_src"))

print("== ③ MANUAL 保护 ==")
reset()
S.slang_learn("磕到了", "嗑到 CP 了，上头", "confirmed", "管理员确认")
chk("管理员确认后 status=confirmed", S.SLANG["terms"]["磕到了"]["status"] == "confirmed")
# AI 推断先跑（同步版）：MANUAL 词直接拒绝
S._chat = lambda msgs: (None, '{"meaning":"AI 瞎猜的"}')
chk("_infer_needs 对 MANUAL 恒 False", S._infer_needs(S.SLANG["terms"]["磕到了"]) is False)
chk("infer_once 拒绝 MANUAL 词", S.slang_infer_once("磕到了") is None)
chk("管理员释义原封不动", S.SLANG["terms"]["磕到了"]["meaning"] == "嗑到 CP 了，上头")
# 竞态防护：推断查询时还 pending、写回时已被管理员确认 → 写回放弃
reset()
S.CFG["slang"]["auto_infer"] = False
S.SLANG["terms"]["深柜"] = {"count": 8, "status": "pending", "meaning": "", "source": "群聊提取",
                            "evidence": [{"gid": 812, "mid": 601, "text": "这谁还分得清", "t": time.time()}]}
_chat_old = S._chat


def race_chat(msgs):
    # 推断跑着的时候管理员拍了板（模拟竞态）
    if "结合" in msgs[1]["content"]:
        S._slang_lock.acquire()
        S.SLANG["terms"]["深柜"]["status"] = "confirmed"
        S.SLANG["terms"]["深柜"]["meaning"] = "管理员正解"
        S.SLANG["terms"]["深柜"]["source"] = "管理员确认"
        S._slang_lock.release()
    return (None, '{"meaning":"AI 瞎猜"}')


S._chat = race_chat
r = S.slang_infer_once("深柜")
chk("写回前重查：管理员刚确认就不覆盖", r is None and S.SLANG["terms"]["深柜"]["meaning"] == "管理员正解",
    (r, S.SLANG["terms"]["深柜"]["meaning"]))
S._chat = _chat_old

print("== ④ 无关黑话不进 Prompt ==")
reset()
S.SLANG["terms"]["赛博菩萨"] = {"count": 12, "status": "pending", "meaning": "大方舍得花钱的人"}
S.SLANG["terms"]["蚌埠住了"] = {"count": 30, "status": "confirmed", "meaning": "绷不住了", "source": "管理员确认"}
gb = S.slang_guess_block("今天天气真不错")
chk("上下文没出现就不注入", gb == "", gb)
gb = S.slang_guess_block("他真是赛博菩萨")
chk("出现了才注入", "赛博菩萨" in gb and "蚌埠住了" not in gb, gb)
# 单字词永不命中（防「你」「哦」这种刷屏）
reset()
S.SLANG["terms"]["哦"] = {"count": 50, "status": "pending", "meaning": "语气词"}
chk("单字词不命中", S.slang_context_hits("哦这样啊") == [])
# rejected 不命中
reset()
S.SLANG["terms"]["下头"] = {"count": 9, "status": "rejected", "meaning": "扫兴"}
chk("rejected 不注入", S.slang_context_hits("好下头") == [])

print("== ⑤ 关闭开关恢复旧行为 ==")
reset()
S.SLANG["terms"]["泰裤辣"] = {"count": 9, "status": "pending", "meaning": "太酷啦", "source": "群聊提取"}
S.CFG["slang"]["context_meaning"] = False
chk("关语境释义→guess_block 空", S.slang_guess_block("这也泰裤辣") == "")
S.CFG["slang"]["auto_infer"] = False
chk("关自动猜义→maybe_infer 空转", S.slang_maybe_infer("泰裤辣") is None)
S.CFG["slang"]["enabled"] = False
chk("关总开关→observe 不计数", (S.slang_observe(812, 999, "泰裤辣"),
    S.SLANG["terms"]["泰裤辣"]["count"] == 9)[1])
S.CFG["slang"]["enabled"] = True
S.CFG["slang"]["context_meaning"] = True
chk("重新打开→恢复注入", "泰裤辣" in S.slang_guess_block("泰裤辣"))
# 已确认词条的固定注入（旧路径 slang_block）不受新开关影响
S.CFG["slang"]["context_meaning"] = False
S.SLANG["terms"]["开黑"] = {"count": 9, "status": "confirmed", "meaning": "组队打游戏"}
chk("旧黑话块照常", "开黑=组队打游戏" in S.slang_block())

print("== 补充：批量安全阀 ==")
reset()
for i in range(8):
    S.SLANG["terms"]["词%d" % i] = {"count": 5, "status": "pending", "meaning": "意思%d" % i,
                                    "meaning_src": "inferred"}
gb = S.slang_guess_block(" ".join("词%d" % i for i in range(8)))
chk("最多注 8 个词", len(gb.splitlines()) <= 9, len(gb.splitlines()))
chk("总字符有预算", len(gb) < 900, len(gb))
# 长词优先
gb = S.slang_guess_block("abc defg")
S.SLANG["terms"]["abc"] = {"count": 5, "status": "pending", "meaning": "短"}
S.SLANG["terms"]["defg"] = {"count": 5, "status": "pending", "meaning": "长"}
lines = [l for l in S.slang_guess_block("abc defg").splitlines() if "=" in l]
chk("长词排前面", lines and lines[0].startswith("defg"), lines)

print("== 补充：生产旧数据形状兼容（无 evidence/meaning_src 字段的存量词条） ==")
reset()
legacy = {"count": 1, "status": "pending", "meaning": "老候选释义", "source": "群聊提取", "t": 1700000000}
S.SLANG["terms"]["火星文"] = dict(legacy)
S.slang_observe(812, 777, "这也太火星文了吧")
t = S.SLANG["terms"]["火星文"]
chk("旧词条能被观察计数+补证据", t["count"] == 2 and t["evidence"][0]["mid"] == 777, (t["count"], t.get("evidence")))
gb = S.slang_guess_block("火星文来两句")
chk("旧词条释义可注入且标猜", "火星文=老候选释义" in gb and "猜的" in gb, gb)
legacy_conf = {"count": 9, "status": "confirmed", "meaning": "官方释义", "source": "联网", "t": 1700000000}
S.SLANG["terms"]["老梗"] = dict(legacy_conf)
chk("旧 confirmed 不重推不注入猜测块", "老梗" not in S.slang_guess_block("这老梗谁还不知道"))
chk("旧 confirmed 仍走正式注入", "老梗=官方释义" in S.slang_block())
# 落盘后重读：新字段与旧字段共存不丢
S.save_slang()
S.load_slang()
t2 = S.SLANG["terms"]["火星文"]
chk("落盘重读不丢字段", t2["count"] == 2 and t2["meaning"] == "老候选释义" and t2["evidence"], t2)

print("\n== 汇总 ==")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
