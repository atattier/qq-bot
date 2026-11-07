# -*- coding: utf-8 -*-
"""qbot_memory_card 离线自测（不依赖 bot，不碰线上 state）。

移植自 SurfCore memory-card.js 的衰减记忆卡：脱敏/评分/去重/衰减/复习/压缩/
全量归档/注入可见性/开场话头。用法： python tests/_tmemcard.py
"""
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
import qbot_memory_card as M   # noqa: E402
import qbot_memory as _mem     # noqa: E402

ok = True


def chk(name, cond, extra=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + name + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


tmp = Path(tempfile.mkdtemp(prefix="memcard_"))
_mem.bind(state_dir=tmp / "mem")            # turns 表用临时库
M.bind(str(tmp), {"memory_card": {}, "admin": {"owner_qq": ["10001"]}})

print("== 脱敏 ==")
chk("密钥键值", "=[已隐藏]" in M.redact_for_memory("password: hunter2secret"))
chk("中文密钥", "=[已隐藏]" in M.redact_for_memory("密码是 abc123"))
chk("sk 形令牌", "[令牌已隐藏]" in M.redact_for_memory("key sk-abc123456789"))
chk("手机号", "[手机号已隐藏]" in M.redact_for_memory("打我电话 13812345678"))
chk("身份证", "[证件号已隐藏]" in M.redact_for_memory("证号 110101199001011234"))
chk("链接", "（链接已省略）" in M.redact_for_memory("看这个 https://example.com/x?a=1"))
chk("qq scope 保留QQ号", "10001" in M.redact_for_memory("主人QQ是10001", scope="qq"))
chk("共享 scope 抹QQ号", "[号码已隐藏]" in M.redact_for_memory("主人QQ是10001", scope="shared"))
chk("QQ号不被手机号正则误伤", "10001" in M.redact_for_memory("主人QQ是10001"))
chk("字数截断", len(M.redact_for_memory("长" * 900, max_chars=600)) <= 601)

print("== 评分 ==")
sc = M.score_memory("主人：今天有点难过，想抱抱我", is_owner=True, is_private=True)
chk("主人私聊 +42", sc["importance"] >= 50, sc)
chk("情绪浓度计分", sc["emotional"] >= 16, sc["emotional"])
chk("主人私聊 kind=owner", sc["kind"] == "owner", sc["kind"])
sc2 = M.score_memory("哈哈")
chk("太短 -12", sc2["importance"] <= 1, sc2["importance"])
sc3 = M.score_memory("这是我第一次跟你说这个", is_owner=True, is_first=True)
chk("第一次+主人=100且置顶", sc3["importance"] == 100 and sc3["pinned"])
sc4 = M.score_memory("下次记得带伞", is_owner=True)
chk("承诺 +16（群里+32+16=56）", sc4["importance"] == 56, sc4["importance"])

print("== 摄取 turns ==")
card = M._empty_card()
cfg = M._cfg()
rows = [
    (1, time.time() - 100, "private:10001", "阿伟", "10001", "主人：今天有点难过，想抱抱我", "抱抱你，不要难过"),
    (2, time.time() - 200, "group:812", "小鱼", "10002", "群友：我考研上岸啦，太开心了！！", "恭喜恭喜"),
]
added = M.ingest_turns(card, rows, {"10001"}, cfg)
chk("两条入库", added == 2, added)
owner_rec = [r for r in card["records"] if r["source"] == "qq-owner"][0]
chk("主人私聊=qq-owner+42分档", owner_rec["importance"] >= 50, owner_rec["importance"])
chk("回复并进 botReply", owner_rec["botReply"] == "抱抱你，不要难过")
chk("第一条主人互动=初遇锚点", bool(card["meta"].get("firstOwnerRecordId")))
grp = [r for r in card["records"] if r["source"] == "qq-group"][0]
chk("群友消息带说话人", grp["speaker"] == "小鱼", grp["speaker"])

print("== 摄取门槛（桥接 looksNoteworthyMessage 等价）==")
card3 = M._empty_card()
boring = [
    (10, time.time(), "group:812", "路人", "20001", "群友：哈哈在吗", "在的"),
    (11, time.time(), "group:812", "路人", "20002", "群友：确实", ""),
    (12, time.time(), "group:812", "小鱼", "20003", "群友：下次一起开黑", ""),  # 约定信号
    (13, time.time(), "private:20001", "路人", "20001", "路人：你机器人真好玩", "嘿嘿"),
]
added3 = M.ingest_turns(card3, boring, {"10001"}, cfg)
chk("群聊流水账被拦、约定/私聊放行", added3 == 2, added3)
chk("群聊拦下的没有入库", all(r["sourceId"] not in ("turn:10", "turn:11") for r in card3["records"]))
chk("纯文本门槛直接判定", M._noteworthy("group:812", "30001", set(), "草这个好玩") is False)
chk("私聊门槛直接判定", M._noteworthy("private:30001", "30001", set(), "随便说啥") is True)

print("== 去重/复习 ==")
added2 = M.ingest_turns(card, rows, {"10001"}, cfg)
chk("重复摄取不再入库", added2 == 0, added2)
M.touch_memories(card, "抱抱")
owner_rec2 = [r for r in card["records"] if r["source"] == "qq-owner"][0]
chk("复习刷新 lastSeenAt+recallCount", owner_rec2["recallCount"] >= 1)

print("== 衰减 ==")
r = {"pinned": False, "createdAt": time.time() - 90 * 86400, "lastSeenAt": time.time() - 90 * 86400,
     "importance": 20, "recallCount": 0}
chk("90天前低分→遗忘", M.target_clarity(r, cfg) == "forgotten")
r2 = {"pinned": True, "createdAt": time.time() - 90 * 86400, "lastSeenAt": time.time(), "importance": 30}
chk("置顶永远清晰", M.target_clarity(r2, cfg) == "vivid")
r3 = {"pinned": False, "createdAt": time.time() - 90 * 86400, "lastSeenAt": time.time(),
      "importance": 80, "recallCount": 2, "tags": ["owner"]}
chk("主人重要≥65 保底清楚", M.target_clarity(r3, cfg) in ("vivid", "clear"))
card2 = M._empty_card()
rec_low = M._make_record(card2, source="qq-group", text="群友：随便聊聊天气如何", conversation_key="group:812")
card2["records"].append(rec_low)
chk("新记忆初始 clarity 合理", rec_low["clarity"] in ("clear", "vivid"))
card2["lastDecayDate"] = ""
steps = M.decay_days_between("2026-08-01")
chk("补跑天数 1~30 封顶", steps == 30, steps)

print("== 全量归档 ==")
fullp = tmp / "full.jsonl"
card3 = M._empty_card()
M._PATHS["full"] = fullp
rec_a = M._make_record(card3, source="qq-group", text="群友：这个游戏挺好玩的推荐给你", conversation_key="group:812")
card3["records"].append(rec_a)
n = M.archive_to_full(card3)
chk("首归档计数", n == 1, n)
chk("打了 fullArchivedAt", bool(rec_a["fullArchivedAt"]))
n2 = M.archive_to_full(card3)
chk("幂等不重复归档", n2 == 0, n2)
lines = fullp.read_text(encoding="utf-8").strip().splitlines()
chk("jsonl 一行一条且 vivid", len(lines) == 1 and '"clarity": "vivid"' in lines[0])

print("== 压缩 ==")
card4 = M._empty_card()
c4 = M._cfg(); c4["max_chars"] = 4000; c4["warn_chars"] = 4000
for i in range(30):
    rr = M._make_record(card4, source="qq-group", text="群友：聊天内容第%d次说到了天气和午饭" % i,
                        conversation_key="group:812", created_at=time.time() - (100 + i) * 86400)
    rr["importance"] = 12
    rr["clarity"] = "clear"
    card4["records"].append(rr)
compacted = M.compact_card(card4, c4, target_ratio=0.3)
chk("压缩有动作", compacted > 0, compacted)
chk("压缩压到目标附近（trace 的按设计删除）", len(card4["records"]) <= 30 and compacted >= 10,
    len(card4["records"]))
chk("ratio 夹在 0.3~0.95", M.compact_card(card4, c4, target_ratio=0.05) == 0)
c4["max_chars"] = 0; c4["warn_chars"] = 60000

print("== 注入可见性 ==")
card5 = M._empty_card()
priv = M._make_record(card5, source="qq-owner", text="主人：偷偷跟你说个事",
                      is_owner=True, is_private=True, private=True, conversation_key="private:10001")
grp2 = M._make_record(card5, source="qq-group", text="群友：那个联机游戏真有意思",
                      speaker="小鱼", conversation_key="group:812")
card5["records"] += [priv, grp2]
blk_group = M.render_block("group:812")
M._CARD = card5
blk_group = M.render_block("group:812")
chk("群块不含私聊记忆", "偷偷跟你说" not in blk_group, blk_group[:80])
chk("群块含本群记忆", "联机游戏" in blk_group)
blk_priv = M.render_block("private:10001")
chk("私聊块含私聊记忆", "偷偷跟你说" in blk_priv)
chk("私聊块不含群记忆", "联机游戏" not in blk_priv)
chk("空 key=群渲染不出私聊", "偷偷跟你说" not in M.render_block("group:999"))

print("== 开场话头 ==")
card6 = M._empty_card()
_p1 = M._make_record(card6, source="qq-group", text="群友：下次那个联机游戏记得叫我一起玩",
                     speaker="小鱼", conversation_key="group:812")
_p2 = M._make_record(card6, source="qq-group", text="群友：今天天气还行", conversation_key="group:812")
card6["records"] += [_p1, _p2]
M._CARD = card6
cands = M.opener_candidates("group:812")
chk("承诺/情绪记忆能当话头", len(cands) >= 1, cands)
chk("纯流水账不当话头", all("天气还行" not in c["text"] for c in cands))
if cands:
    M.purpose_mark(cands[0]["text"])
    chk("提过的不再出", all(M.purpose_seen(c["text"]) for c in cands if c["text"] == cands[0]["text"]))
M.purpose_mark("随便一句话测试去重归一")
chk("去重标点归一", M.purpose_seen("随便一句话测试去重归一！"))
M.touch_by_ids([grp2["id"]])
chk("touch_by_ids 不炸", True)

print("== maintain 端到端 ==")
M._mem._DB = None  # 强制走 qbot_memory 的临时库（bind 已指到 tmp/mem）
_mem.bind(state_dir=tmp / "mem")
_mem.log_turn("group:812", "小鱼", "10002", "群友：下次周末要不要一起去看展", "好呀我也想去", persona="菲比")
M._CARD = M._empty_card()
M._CARD = M._load_card(M._PATHS["card"])
M.maintain()
loaded = M._load_card(M._PATHS["card"])
chk("maintain 落盘且摄取了 turns", len(loaded["records"]) >= 1, len(loaded["records"]))
chk("游标推进", int(loaded["meta"].get("turnCursor") or 0) >= 1, loaded["meta"].get("turnCursor"))
M.maintain()
loaded2 = M._load_card(M._PATHS["card"])
chk("二次维护不重复摄取", len(loaded2["records"]) == len(loaded["records"]))

print("== enabled 开关 ==")
M._CFG["memory_card"] = {"enabled": False}
chk("关掉后 render 空", M.render_block("group:812") == "")
chk("关掉后 opener 空", M.opener_candidates("group:812") == [])
M._CFG["memory_card"] = {}

print()
print("ALL PASS" if ok else "FAILED")
sys.exit(0 if ok else 1)
