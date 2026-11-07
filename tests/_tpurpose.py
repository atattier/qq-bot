# -*- coding: utf-8 -*-
"""主动性层（purpose-engine）SAFE 测试（2026-11-04 人味阶段 C）。

覆盖主任务书要求：
  ① 记忆素材来源与归属（证据必须带 card_id；群友经历不写成自己的）
  ② 不可靠 / 过期 / 隐私内容过滤
  ③ goal 生命周期与多层去重
  ④ 池容量、TTL、候选轮换
  ⑤ 每日上限、随机间隔、安静时段、重启恢复（补一枪）
  ⑥ [SILENT] 与 attempts 计数（silent 不占成功额度）
  ⑦ owner-active / idle / unknown 三态
  ⑧ mood 硬闸不被绕过（模块不设 sim_bias 硬阈值；wiring 顺序在源码级断言）
  ⑨ timing 开场退避正确叠加
  ⑩ 发送失败与状态落盘失败（只读模式）
  ⑪ purpose.enabled=False = 旧行为
  ⑫ 测试不写生产 state（路径断言）
全离线、无 LLM、无网络。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QBOT_TEST"] = "1"

FAIL = []


def chk(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  | %s" % (detail,) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


from qbot import qbot_purpose as P  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="tpurpose_"))
NOW = time.mktime((2026, 11, 4, 14, 0, 0, 0, 0, -1))      # 下午 2 点，非安静时段
CANDS = []


def bind(cfg=None):
    P.bind(cfg={"purpose": dict({"enabled": True}, **(cfg or {}))},
           memory_candidates=lambda g: list(CANDS),
           purpose_seen=lambda t: False, purpose_mark=lambda t: None,
           touch_ids=lambda ids: None,
           chat=lambda msgs: ("", "行"), system_prompt=lambda g: "SYS",
           is_placeholder=lambda t: False, state_dir=str(TMP))


def reset(gid=812):
    with P._lock:
        P._STATE["gid"].pop(str(gid), None)


def mk(now=NOW, gid=812, **kw):
    c = {"id": kw.get("id", 1), "text": kw.get("text", "上次说好一起去看展"),
         "importance": kw.get("importance", 24), "clarity": kw.get("clarity", "vivid"),
         "createdAt": kw.get("createdAt", now - 86400)}
    c.update(kw)
    return c


def force_due(gid=812, now=NOW):
    with P._lock:
        st = P._gstate(gid)
        st["next_fire_at"] = now - 1


print("== ① 记忆素材来源与归属 ==")
bind()
reset()
g = P.goal_from_memory(mk(), NOW, 812)
chk("goal 字段齐全", all(k in g for k in ("source", "need", "confidence", "risk", "value",
                                          "status", "created_at", "expires_at", "evidence")), g)
chk("来源=memory + 证据带 card_id", g["source"] == "memory" and g["evidence"]["source_id"] == "1", g)
chk("来源归属写进提示词（是自己的旧事）",
    "你自己记得" in P._build_messages(812, g, P._gstate(812), 90, "")[0]["content"])
g2 = P.goal_from_memory(mk(id=7, text="群友说他下周去日本"), NOW, 812)
obs = P.goal_from_observation(812, "群友说他下周去日本", 99, NOW)
msg = P._build_messages(812, obs, P._gstate(812), 90, "")[0]["content"]
chk("observation 提示词不冒充自己经历", "不是你的经历" in msg and "我也去过" in msg, msg[:120])
chk("observation 默认 shadow（不进正式竞争）", obs["status"] == "shadow", obs["status"])
chk("shadow 不会被选中", P.pick_goal([obs]) is None)

print("== ② 不可靠 / 过期 / 隐私过滤 ==")
chk("模糊记忆不收", P.goal_from_memory(mk(clarity="fuzzy"), NOW, 812) is None)
chk("太轻的记忆不收", P.goal_from_memory(mk(importance=8), NOW, 812) is None)
chk("太旧的记忆不收（>7天）", P.goal_from_memory(mk(createdAt=NOW - 8 * 86400), NOW, 812) is None)
chk("没有来源证据不收", P.goal_from_memory(mk(id=None), NOW, 812) is None)
chk("空文本不收", P.goal_from_memory(mk(text="   "), NOW, 812) is None)
chk("手机号隐私不收",
    P.goal_from_memory(mk(text="他电话 13800138000 说下次一起"), NOW, 812) is None)
chk("密码/token 不收",
    P.goal_from_memory(mk(text="你的 token 是 abc123"), NOW, 812) is None)

print("== ③ 生命周期与多层去重 ==")
bind()
reset()
st = P._gstate(812)
st["next_fire_at"] = NOW - 1
cands = [mk(id=1, text="上次说好一起去看展"), mk(id=1, text="上次说好一起去看展"),
         mk(id=2, text="上次说好一起去看展吧")]
CANDS[:] = cands
d = P.decide(812, now=NOW, cold_minutes=90, owner_last_ts=NOW - 7200)
chk("同 source_id/高相似只进一条", len(st["goals"]) == 1, [x["text"] for x in st["goals"]])
st["recent_texts"] = ["上次说好一起去看展"]
chk("与最近发过的高相似不收",
    not P.dedup_ok(P.goal_from_memory(mk(id=3, text="上次说好一起去看展"), NOW, 812),
                   [], st["recent_texts"]))
P.bind(cfg={"purpose": {"enabled": True}}, memory_candidates=lambda g: [], purpose_seen=lambda t: True,
       chat=lambda m: ("", "x"), system_prompt=lambda g: "", is_placeholder=lambda t: False,
       state_dir=str(TMP))
chk("去重账本命中不收",
    not P.dedup_ok(P.goal_from_memory(mk(id=4, text="完全不同的新话头"), NOW, 812), [], []))
bind()
# 生命周期：试 3 次仍 silent → expired
reset()
st = P._gstate(812)
g = P.goal_from_memory(mk(), NOW, 812)
st["goals"] = [g]
st["next_fire_at"] = NOW - 1
for i in range(3):
    P.finish(812, g["id"], sent=False, silent=True, now=NOW + i)
chk("单 goal 试满 3 次 → expired", g["status"] == "expired", g["status"])

print("== ④ 池容量 / TTL / 轮换 ==")
now = NOW
goals = []
for i in range(20):
    x = P.goal_from_memory(mk(id=100 + i, text="素材编号%d这件事" % i,
                              importance=20 + (i % 5)), now, 812)
    goals.append(x)
kept, _ = P.prune_pool(goals, now)
chk("总池 ≤12", len(kept) <= 12, len(kept))
chk("每 need ≤3", max((sum(1 for g in kept if g["need"] == n)
                      for n in ("share", "ask", "invite")), default=0) <= 3)
chk("active ≤6", len([g for g in kept if g["status"] == "active"]) <= 6)
old = P.goal_from_memory(mk(id=999, createdAt=now - 8 * 86400), now, 812)
chk("TTL 到期前已被过滤（不生成）", old is None)
sh = P.goal_from_observation(812, "群友聊到的新话题", 5, now)
sh_kept, _ = P.prune_pool([sh], now + 4 * 86400, shadow=True)
chk("shadow TTL 3 天到期被清", sh_kept == [], sh_kept)
# 轮换：最近 3 次同 need → 降权；连续 share → 非 share 加权
gw = P.goal_from_memory(mk(id=1, text="分享素材甲"), now, 812)
w0 = P.goal_weight(gw, [])
w1 = P.goal_weight(gw, ["share", "share", "share"])
chk("最近 3 次同 need 降权", w1 < w0, (w0, w1))
gask = P.goal_from_memory(mk(id=2, text="这个怎么弄？"), now, 812)
chk("连续 share 后非 share 加权",
    P.goal_weight(gask, ["share", "share"]) > P.goal_weight(gask, []),
    P.goal_weight(gask, ["share", "share"]))
chk("need 推断（约定/问句/陈述）",
    P.infer_need("说好下次一起") == "invite" and P.infer_need("这个怎么弄？") == "ask"
    and P.infer_need("今天天气不错") == "share")

print("== ⑤ 调度：上限 / 间隔 / 安静 / 重启 ==")
bind()
reset()
st = P._gstate(812)
st["success"] = 4
chk("每日成功上限 4 → 不再开口",
    P.decide(812, now=NOW, cold_minutes=90, owner_last_ts=0)["action"] == "skip")
st["success"] = 0
st["attempts"] = 6
chk("每日尝试上限 6 → 不再开口",
    P.decide(812, now=NOW, cold_minutes=90, owner_last_ts=0)["action"] == "skip")
bind()
reset()
fire = P.plan_next_fire(NOW, rng=type("R", (), {"uniform": lambda self, a, b: a})())
chk("间隔下限 2.5h", abs(fire - (NOW + 2.5 * 3600)) < 1e-6, (fire - NOW) / 3600)
fire = P.plan_next_fire(NOW, rng=type("R", (), {"uniform": lambda self, a, b: b})())
chk("间隔上限 5h", abs(fire - (NOW + 5 * 3600)) < 1e-6, (fire - NOW) / 3600)
chk("安静时段 23:45 命中", P.in_quiet(time.mktime((2026, 11, 4, 23, 45, 0, 0, 0, -1))))
chk("白天 14:00 不命中", not P.in_quiet(NOW))
q = time.mktime((2026, 11, 4, 23, 45, 0, 0, 0, -1))
nq = P.next_quiet_end(q)
chk("安静中调度挪到 09:00 后", time.localtime(nq).tm_hour >= 9 or time.localtime(nq).tm_mday != 4,
    time.strftime("%m-%d %H:%M", time.localtime(nq)))
qfire = P.plan_next_fire(q, rng=type("R", (), {"uniform": lambda self, a, b: a})())
chk("排到安静时段会自动顺延", not P.in_quiet(qfire), time.strftime("%m-%d %H:%M", time.localtime(qfire)))
# 重启恢复：next_fire 已过很久 → 只补一枪（now+2min）
reset()
st = P._gstate(812)
st["next_fire_at"] = NOW - 3600
bind()                                    # 重新 bind = 重新 load 磁盘
st = P._gstate(812)
st["next_fire_at"] = NOW - 3600
d = P.decide(812, now=NOW, cold_minutes=90, owner_last_ts=0)
chk("停机恢复不集中补发（最早 now+2min）",
    abs(float(st["next_fire_at"]) - (NOW + 120)) < 1e-6, st["next_fire_at"] - NOW)

print("== ⑥ SILENT 与 attempts ==")
bind()
reset()
st = P._gstate(812)
g = P.goal_from_memory(mk(), NOW, 812)
st["goals"] = [g]
st["next_fire_at"] = NOW - 1
r = P.finish(812, g["id"], sent=False, silent=True, now=NOW)
chk("SILENT 不占成功额度", r["success"] == 0 and r["attempts"] == 1, r)
chk("SILENT 置信度下降", g["confidence"] < 0.85, g["confidence"])
r2 = P.finish(812, g["id"], sent=True, now=NOW + 10)
chk("成功 +1 且 goal done", r2["success"] == 1 and g["status"] == "done", (r2, g["status"]))

print("== ⑦ owner 三态 ==")
chk("active（10 分钟前）", P.classify_owner_state(NOW - 600, NOW) == "active")
chk("idle（2 小时前）", P.classify_owner_state(NOW - 7200, NOW) == "idle")
chk("unknown（没记录）", P.classify_owner_state(0, NOW) == "unknown")
bind()
reset()
st = P._gstate(812)
st["next_fire_at"] = NOW - 1
d = P.decide(812, now=NOW, cold_minutes=90, owner_last_ts=NOW - 600)
chk("主人在场 → 拦截", d["action"] == "skip" and "主人在场" in d["reason"], d)
d2 = P.decide(812, now=NOW, cold_minutes=90, owner_last_ts=0)
chk("unknown 不拦截（照常到选素材）", d2["reason"] != "主人在场", d2)
chk("unknown 已记账", st.get("owner_unknown", 0) >= 1, st.get("owner_unknown"))
# 24h 连续拦截 → 自动回退放行
bind()
reset()
st = P._gstate(812)
st["blocked_since"] = NOW - 25 * 3600
st["next_fire_at"] = NOW - 1
d3 = P.decide(812, now=NOW, cold_minutes=90, owner_last_ts=NOW - 60)
chk("连续 24h 拦截 → 临时放行", d3["reason"] != "主人在场", d3)
chk("回退窗口已记录", float(st.get("owner_fallback_until") or 0) > NOW)

print("== ⑧ mood 只做软权重（不设硬阈值） ==")
gw2 = P.goal_from_memory(mk(id=11, text="低心情时的素材"), NOW, 812)
w_low = P.goal_weight(gw2, [], mood_bias=0.0)
w_high = P.goal_weight(gw2, [], mood_bias=1.0)
chk("mood_bias 软权重生效", w_low < w_high, (w_low, w_high))
chk("低心情仍可选中（模块没有 sim_bias 硬闸）", P.pick_goal([gw2], mood_bias=0.0) is not None)
_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "bot.py"), encoding="utf-8").read()
_tick_src = _src.split("def idle_opener_tick", 1)[1].split("\ndef ", 1)[0]
chk("wiring 顺序：mood/既有硬闸在 purpose 之前",
    _tick_src.index("silent_on()") < _tick_src.index("qbot_purpose.enabled()")
    and _tick_src.index("qbot_session.flag(gid, \"sim\", True)") < _tick_src.index("qbot_purpose.enabled()"))
chk("purpose 发送复用 send_bubbles（不新增通道）",
    "def _purpose_send" in _src and "send_bubbles(gid, text)" in _src)

print("== ⑨ timing 开场退避叠加 ==")
# 用中午 12 点做基准：+10h=22:00 不落安静时段（23:30 才开始），能干净验证倍率
NOW2 = time.mktime((2026, 11, 4, 12, 0, 0, 0, 0, -1))
f1 = P.plan_next_fire(NOW2, backoff=4.0, rng=type("R", (), {"uniform": lambda self, a, b: a})())
chk("退避 4×（2.5h→10h）", abs(f1 - (NOW2 + 10 * 3600)) < 1e-6, (f1 - NOW2) / 3600)
bind()
reset()
st = P._gstate(812)
st["next_fire_at"] = NOW - 1
CANDS[:] = [mk()]
d = P.decide(812, now=NOW, cold_minutes=90, owner_last_ts=0, backoff=2.0)
P.finish(812, d["goal_id"], sent=True, now=NOW, backoff=2.0)
chk("decide/finish 都接受退避倍率（间隔 ≥5h）",
    float(st["next_fire_at"]) - NOW >= 5 * 3600 - 1, (float(st["next_fire_at"]) - NOW) / 3600)

print("== ⑩ 发送失败 / 落盘失败 ==")
bind()
reset()
st = P._gstate(812)
g = P.goal_from_memory(mk(), NOW, 812)
st["goals"] = [g]
r = P.finish(812, g["id"], sent=False, silent=False, now=NOW)
chk("发送失败不占成功额度", r["success"] == 0, r)
chk("发送失败按 15 分钟重试",
    abs(float(st["next_fire_at"]) - (NOW + 15 * 60)) < 1e-6, st["next_fire_at"] - NOW)
_saved = P.STATE_FILE
P.STATE_FILE = Path(tempfile.mkdtemp(prefix="tpurpose_ro_")) / "not_a_dir" / "purpose.json"
Path(str(P.STATE_FILE.parent)).write_text("x", encoding="utf-8")   # 父路径是文件 → 写必失败
ok = P._save(force=True)
chk("落盘失败 → 只读模式且不抛异常", ok is False and P.read_only(), (ok, P.read_only()))
chk("只读模式继续决策不炸",
    P.decide(812, now=NOW + 3600, cold_minutes=90, owner_last_ts=0)["action"] in ("skip", "speak"))
P.STATE_FILE = _saved
P.bind(state_dir=str(TMP))                 # 恢复（read_only 在新 bind 的 load 里复位）

print("== ⑪ purpose.enabled=False = 旧行为；观察采集与开口解耦 ==")
reset()
bind({"enabled": False})
chk("关闸 decide 直接跳过", P.decide(812, now=NOW)["reason"] == "目的层未启用")
chk("关闸默认仍采集 shadow（与开口解耦）",
    P.add_observation(812, "下次一起去看展吧", "m200", NOW) is not None)
chk("关闸采集了也不会开口", P.decide(812, now=NOW)["reason"] == "目的层未启用")
bind({"enabled": False, "observe": False})
reset()
chk("observe=False 才真正零副作用", P.add_observation(812, "下次一起去看展吧", "m201", NOW) is None)
chk("旧 idle_opener_tick 逻辑仍在（关闸分支不执行）",
    "_idle_state[\"count\"] >= int(idle.get(\"max_per_day\", 2))" in _src)

print("== ⑫ 路径隔离 + 旋钮在册 ==")
chk("purpose 状态在临时目录", "tpurpose_" in str(P.STATE_FILE), P.STATE_FILE)
import bot  # noqa: E402
for label, key, default in [("目的层", "purpose.enabled", False),
                            ("观察采集", "purpose.observe", True),
                            ("主动每日上限", "purpose.daily_cap", 4),
                            ("主动间隔下限", "purpose.gap_min_h", 2.5),
                            ("主动间隔上限", "purpose.gap_max_h", 5.0),
                            ("安静时段", "purpose.quiet", "23:30-09:00"),
                            ("主人在场分钟", "purpose.owner_active_min", 30),
                            ("目标池上限", "purpose.pool_max", 12)]:
    e = bot.knob_find(label)
    chk("旋钮在册：%s" % label, e is not None and e["key"] == key, e and e.get("key"))
    chk("  默认值 %s=%s" % (key, default), e is not None and e.get("default") == default,
        e and e.get("default"))

print("== ⑬ observation shadow 入口（只记录、不开口） ==")
chk("计划/偏好句命中", P.looks_like_observation("下次一起去吃那家火锅吧")
    and P.looks_like_observation("我想养一只橘猫"))
chk("纯陈述/链接/口令/隐私不命中",
    not P.looks_like_observation("今天天气不错啊")
    and not P.looks_like_observation("https://example.com/想去看展")
    and not P.looks_like_observation("/status 我想看看")
    and not P.looks_like_observation("想联系我 13800138000 就行"))
bind()
reset()
o1 = P.add_observation(812, "下次一起去看展吧", "m100", NOW)
o2 = P.add_observation(812, "下次一起去看展吧", "m100", NOW)
with P._lock:
    sh = list(P._gstate(812)["shadow"])
chk("shadow 记录成功", o1 is not None and o1["status"] == "shadow", o1)
chk("同来源去重（只一条）", o2 is None and len(sh) == 1, sh)
chk("shadow 不参与开口", P.pick_goal(sh) is None)
_src2 = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "bot.py"), encoding="utf-8").read()
chk("消息入口接了 shadow（与开口解耦）",
    "qbot_purpose.looks_like_observation" in _src2
    and "qbot_purpose.add_observation" in _src2)

print("\n== 汇总 ==")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
