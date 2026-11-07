# -*- coding: utf-8 -*-
"""时机层边界测试（2026-11-04 人味阶段 B）。

四机制（MaiBot 移植，研究文档 §1）：
  ① reply_likelihood 概率函数：锚点数值 + 特征方向 + @ 强制放行
  ② dynamic_gate 自校准：只收紧不放宽 / 样本不足退回静态 / talk_value 生效
  ③ 指数退避 + 积压绕过 + 回复重置
  ④ [WAIT:N] 状态机：封顶 / 剩余文本 / 到期唤醒 / @ 打断 / flush 滚动
  ⑤ 总闸关 = 全部旧行为（bool+理由双断言）
  ⑥ 旋钮在册（防「网页上关不掉」）
全打桩不联网。隔离：state 指到临时目录。
"""
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QBOT_TEST"] = "1"

FAIL = []


def chk(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  | %s" % (detail,) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


from qbot import qbot_timing as T

TMP = tempfile.mkdtemp(prefix="ttiming_")
T.bind(cfg={"timing": {"enabled": True}}, names=lambda: ["小鲸"],
       self_id=lambda: "123450002", context_items=lambda g: [],
       is_placeholder=lambda t: t.strip() in ("[图片]", "[表情]"),
       state_dir=TMP)

RECENT = {"self_ratio": 0, "count": 0, "secs_since_self": 3600}


def reset(cfg=None):
    with T._lock:
        T._STATE.clear()
        T._STATE.update({"gate_th": 0.35, "rounds": [], "sends": [], "gid": {}})
    T.CFG["timing"] = dict({"enabled": True}, **(cfg or {}))


print("== ① reply_likelihood 概率函数 ==")
reset()
import math
p0 = T.reply_likelihood("随便聊聊", 1, RECENT)
expect = 1.0 / (1.0 + math.exp(-(-1.2005 + 0.0571 * math.log1p(3600) + 0.1012)))
chk("锚点数值符合权重表", abs(p0 - expect) < 1e-9, (p0, expect))
p_m = T.reply_likelihood("小鲸你看这个", 1, RECENT)
p_at = T.reply_likelihood("@别人 来看看", 1, RECENT)
p_q = T.reply_likelihood("这个怎么弄啊？", 1, RECENT)
p_ph = T.reply_likelihood("[图片]", 1, RECENT)
chk("提名字加分", p_m > p0, (p_m, p0))
chk("@别人减分（在跟别人说话）", p_at < p0, (p_at, p0))
chk("问号加分", p_q > p0, (p_q, p0))
chk("纯图减分", p_ph < p0, (p_ph, p0))
chk("批内条数封顶 MAX_PENDING",
    abs(T.reply_likelihood("聊", 20, RECENT) - T.reply_likelihood("聊", 25, RECENT)) < 1e-12)
p_self = T.reply_likelihood("聊", 1, {"self_ratio": 0.5, "count": 4, "secs_since_self": 60})
chk("bot 参与度高加分", p_self > p0, (p_self, p0))

print("== ② dynamic_gate 自校准 ==")
reset()
with T._lock:
    T._STATE["rounds"] = [{"t": time.time(), "p": 0.3} for _ in range(25)]
    T._STATE["sends"] = []
th0, cal0 = T._evaluate()
chk("样本足够→校准生效且从静态起", cal0 and abs(th0 - 0.35) < 1e-9, (th0, cal0))
with T._lock:
    T._STATE["sends"] = [time.time() for _ in range(20)]   # 实际 20 >> 预期 7.5
th1, _ = T._evaluate()
chk("说多了→收紧", th1 > th0, (th0, th1))
with T._lock:
    T._STATE["sends"] = []
# 说少了是**缓退**（每次 -0.02，绝不低于静态），不是一步打回 0.35 —— 连算几轮直到贴住静态
for _ in range(5):
    T._evaluate()
th2 = T._STATE["gate_th"]
chk("说少了→缓退并贴住静态（不许比主人基线更话痨）", abs(th2 - 0.35) < 1e-9, th2)
chk("校准阈值永不低于静态", T._STATE["gate_th"] >= 0.35 - 1e-9)
with T._lock:
    T._STATE["rounds"] = [{"t": time.time(), "p": 0.3} for _ in range(5)]   # 样本不足
th3, cal3 = T._evaluate()
chk("样本<20 退回静态阈值", (not cal3) and abs(th3 - 0.35) < 1e-9, (th3, cal3))
# talk_value 生效：目标翻倍 → 同样实际次数算「说少了」
reset({"talk_value": 3.0})
with T._lock:
    T._STATE["rounds"] = [{"t": time.time(), "p": 0.3} for _ in range(25)]
    T._STATE["sends"] = [time.time() for _ in range(10)]
T._evaluate()
chk("talk_value 提高目标→同样次数不再收紧", T._STATE["gate_th"] <= 0.35 + 1e-9, T._STATE["gate_th"])

print("== ③ 指数退避 + 积压绕过 ==")
reset()
items1 = [{"text": "随便一句话"}]
# 造连续 SILENT
T.note_silent(812)
T.note_silent(812)
chk("两次 SILENT → 进入退避", not T.should_judge(812, items1)[0], T.should_judge(812, items1))
go, why = T.should_judge(812, [{"text": "聊%d" % i} for i in range(6)])
chk("积压 ≥6 绕过退避", go, why)
chk("绕过理由没说退避", "退避" not in why, why)
T.note_reply(812)
go2, why2 = T.should_judge(812, items1)
# 重置的是**退避**，不是概率门：普通陈述本身 p(≈0.35) 就可能低于门槛 —— 那没关系，
# 这里只断言「不再因退避被拦」（退避已清零）。
chk("回复/被点名 → 退避重置", "退避" not in why2 and "连续" not in why2, why2)
# 退避窗算术：15s × 2^(n-2) 封顶
chk("n=2 → 退避窗 15s", abs(T._backoff_window(2) - 15.0) < 1e-9, T._backoff_window(2))
reset()
for i in range(2):
    T.note_silent(812)
with T._lock:
    st = T._STATE["gid"]["812"]
    st["last_silent"] = time.time() - 14     # 15s 窗还剩 1s
go3, why3 = T.should_judge(812, items1)
chk("退避窗内被拦（理由带剩余秒）", (not go3) and "退避" in why3, (go3, why3))
reset({"backoff_cap": 60})
for i in range(8):
    T.note_silent(812)
w = T._backoff_window(8)
chk("指数封顶 backoff_cap", abs(w - 60) < 1e-9, w)

print("== ④ [WAIT:N] 状态机 ==")
reset({"wait_max": 180})
sec, rest = T.parse_wait("[WAIT:120]")
chk("解析 [WAIT:秒]", sec == 120 and rest == "", (sec, rest))
sec, rest = T.parse_wait("[wait:30]")
chk("小写也认", sec == 30, (sec, rest))
sec, rest = T.parse_wait("[WAIT:99999]")
chk("被 wait_max 封顶", sec == 180, sec)
sec, rest = T.parse_wait("[WAIT:60] 我还是说一句吧")
chk("后面带正文=其实想说", sec == 0 and rest == "我还是说一句吧", (sec, rest))
sec, rest = T.parse_wait("普通回复")
chk("没匹配=原样", sec == 0 and rest == "普通回复", (sec, rest))
T.set_wait(812, 120)
chk("wait_remaining>0", 100 < T.wait_remaining(812) <= 120, T.wait_remaining(812))
T.note_reply(812)
chk("@/回复立即唤醒", T.wait_remaining(812) == 0, T.wait_remaining(812))

print("== ⑤ 总闸关 = 全部旧行为 ==")
reset()
T.CFG["timing"]["enabled"] = False
go, why = T.should_judge(812, items1)
chk("关闸恒放行", go and why == "时机层未启用", (go, why))
for i in range(5):
    T.note_silent(812)
go, _ = T.should_judge(812, items1)
chk("关闸连 SILENT 都不记账→仍放行", go)
T.set_wait(812, 100)
chk("关闸 set_wait no-op", T.wait_remaining(812) == 0, T.wait_remaining(812))
sec, rest = T.parse_wait("[WAIT:120]", wait_max=0)
chk("wait_max=0 不解析", sec == 0 and "[WAIT:120]" in rest, (sec, rest))
T.CFG["timing"]["enabled"] = True
T.CFG["timing"]["wait_max"] = 0
sec, _ = T.parse_wait("[WAIT:120]")
chk("wait_max=0（开着总闸）也不给 WAIT", sec == 0, sec)
T.CFG["timing"]["wait_max"] = 180
# 源码级守卫（防以后接线被删/挪到闸外）：总闸关时提示词必须逐字节不变
_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "bot.py"), encoding="utf-8").read()
chk("WAIT 提示词受「总闸 + wait_max」双重把守",
    "qbot_timing.enabled() and _wmax > 0" in _src and "可以只输出 [WAIT:%d]" in _src)
chk("flush 的 wait 出口与 batch_push 同源取窗（防测试态同步递归）",
    '_batch_cfg().get("group_ms")' in _src)

print("== ⑥ 旋钮在册 + 持久化 ==")
# ⚠️ import bot 会执行 _bind_modules()，把 qbot_timing 重绑到**真实** state 目录
#    和真实 CFG。必须先 import、再立刻用测试绑定盖回来 —— 否则本套件会写脏
#    state/timing.json（2026-11-04 实测发生：gate_th / gid 812 被写进真文件）。
import bot
T.bind(cfg={"timing": {"enabled": True}}, names=lambda: ["小鲸"],
       self_id=lambda: "123450002", context_items=lambda g: [],
       is_placeholder=lambda t: t.strip() in ("[图片]", "[表情]"),
       state_dir=TMP)
for label, key, default in [
        ("时机门", "timing.enabled", False), ("概率门槛", "timing.likelihood_min", 0.35),
        ("话痨系数", "timing.talk_value", 1.0), ("退避封顶", "timing.backoff_cap", 300),
        ("积压绕过", "timing.bypass_pending", 6), ("等待上限", "timing.wait_max", 180),
        ("开场退避", "timing.opener_backoff", True)]:
    e = bot.knob_find(label)
    chk("旋钮在册：%s" % label, e is not None and e["key"] == key, e and e.get("key"))
    chk("  默认值 %s=%s" % (key, default), e is not None and e.get("default") == default,
        e and e.get("default"))
# 持久化：校准阈值/退避跨进程
reset()
for i in range(30):
    with T._lock:
        T._STATE["rounds"].append({"t": time.time(), "p": 0.9})
    with T._lock:
        T._STATE["sends"].append(time.time())
T._evaluate()
T.note_silent(812)
T._save(force=True)
from qbot import qbot_timing as T2
import importlib
importlib.reload(T2)
T2.bind(cfg={"timing": {"enabled": True}}, names=lambda: [], self_id=lambda: "0",
        context_items=lambda g: [], state_dir=TMP)
chk("校准阈值落盘可恢复", T2._STATE["gate_th"] > 0.35, T2._STATE["gate_th"])
chk("退避状态落盘可恢复", int((T2._STATE["gid"].get("812") or {}).get("silent") or 0) == 1,
    T2._STATE["gid"])

print("== ⑦ flush 集成：wait 搁置 + 时机门 ==")
reset()
# flush 的 wait 出口靠**真合批窗**滚动；测试态 _batch_cfg 会把 group_ms 强制 0
# （直接同步判定、没有窗口可滚），所以这一段临时关掉 _IN_TEST 拿真窗口，测完还原。
bot._IN_TEST = False
bot.CFG.setdefault("batch", {})["group_ms"] = 6000
bot.CFG["sim"] = {"enabled": True, "single_quiet": True, "sticker_quiet": True,
                  "cooldown_sec": 0, "max_per_hour": 99, "min_gap_msgs": 0,
                  "min_activity": 0}
bot._sim_state.update({"last": 0.0, "count": 0})
bot.CONTEXT.clear()
bot.CONTEXT[812] = []
CALLS = []
bot.sim_join = lambda gid, who, text, quote="", uid="", force=False, imgs=(), qmid=0: CALLS.append(text)
bot.chat = lambda msgs, *a, **kw: ("", "这是一句插话")
bot._idle_state.update({"day": time.strftime("%Y%m%d"), "count": 0, "last": 0.0})
bot._last_human.clear()
# wait 激活：flush 应把这批原样塞回合批窗、不判（窗口 6s，测试等不及 → 稍后清掉）
T.set_wait(812, 120)
bot._sim_batch_flush(812, [{"who": "a", "uid": "1", "text": "这个问题怎么办？"}])
time.sleep(0.1)
chk("wait 激活时 flush 不判（塞回窗口）", CALLS == [], CALLS)
with bot._batch_lock:                                 # 掐掉滚动中的 Timer，别等 6 秒
    _b = bot._BATCH.get(812) or {}
    if _b.get("timer"):
        _b["timer"].cancel()
    _b["items"] = []
    _b["timer"] = None
T.note_reply(812)                                     # @/回复唤醒
bot._sim_batch_flush(812, [{"who": "a", "uid": "1", "text": "这个问题怎么办？"}])
time.sleep(0.1)
chk("唤醒后照常判定并插话", len(CALLS) == 1, CALLS)
# 时机门概率拦：门槛拉到 1，谁都别想说话（用问句先过 sim 闸，确保是时机门拦的）
T.CFG["timing"]["likelihood_min"] = 1.0
bot._sim_batch_flush(812, [{"who": "b", "uid": "2", "text": "这又是怎么回事？"}])
time.sleep(0.1)
chk("概率低于门槛被拦（且是时机门拦的）", len(CALLS) == 1, CALLS)
T.CFG["timing"]["likelihood_min"] = 0.35
bot._IN_TEST = True
bot.CFG["batch"]["group_ms"] = 0

print("\n== 汇总 ==")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
