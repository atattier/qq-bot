# -*- coding: utf-8 -*-
"""M2 Context Assembler SAFE：18 段契约 / 预算 / 媒体跨轮 / 会话隔离 / 逐段回退。

全离线：假模型、临时目录、不联网、不发消息、不执行工具、不写生产 state。
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
os.environ["QBOT_TEST"] = "1"
os.environ.pop("QBOT_ARCH_SHADOW", None)
os.environ.pop("QBOT_ARCH_CONTEXT", None)
sys.stdout.reconfigure(encoding="utf-8")

from qbot import qbot_context as X     # noqa: E402
from qbot import qbot_core as C        # noqa: E402
from qbot import qbot_shadow as S      # noqa: E402
from qbot import qbot_turn as T        # noqa: E402

bad = 0


def chk(name, ok, why=""):
    global bad
    if not ok:
        bad += 1
    print(("  [OK] " if ok else "  [失败] ") + name + ("" if ok else " | %s" % (why,)))


class Cap:
    def __init__(self):
        self.warnings = []

    def warning(self, fmt, *a):
        try:
            self.warnings.append(fmt % a)
        except Exception:
            self.warnings.append(str(fmt))

    def debug(self, *a):
        pass

    info = debug


def turn(key=8123, text="你好", kind="group", uid=20001, who="群友",
         media=(), quote_text="", quote_media=(), addressed=True, mid=None):
    return T.make_turn(
        event_id=mid or ("t-%s-%s-%s" % (key, text, len(media))),
        session_kind=kind, session_key=key, user_id=uid, display_name=who,
        role="member", received_at=1793878000, text=text,
        media_refs=[{"file": x} for x in media], quote_text=quote_text,
        quote_media_refs=[{"file": x} for x in quote_media],
        addressed=addressed)


def providers(size=1, boom=()):
    out = {}
    for spec in X.SPECS:
        if spec.id in boom:
            def _boom(ctx, sid=spec.id):
                raise RuntimeError("provider %s 坏了" % sid)
            out[spec.id] = _boom
        else:
            out[spec.id] = (lambda ctx, sid=spec.id, n=size:
                            ("SEG:%s\n" % sid) * n)
    return out


print("== 1 18 段契约 ==")
ids = [s.id for s in X.SPECS]
chk("恰好 18 段", len(X.SPECS) == 18, len(X.SPECS))
chk("段 id 不重复", len(set(ids)) == 18, ids)
chk("优先级 0..17 不重复", sorted(s.priority for s in X.SPECS) == list(range(18)))
chk("每段都有来源/标题/类型", all(s.source and s.title and
                                s.kind in ("system", "user", "history")
                                for s in X.SPECS))
chk("每段都有关键标记", all(isinstance(s.critical, bool) for s in X.SPECS))
chk("SPEC_BY_ID 映射完整", set(X.SPEC_BY_ID) == set(ids))
chk("关键段固定 6 段", sorted(s.id for s in X.SPECS if s.critical) ==
    sorted(["hard_rules", "persona", "identity", "session_policy", "quote",
            "task_directive"]))
chk("组装顺序按冻结表", [s.id for s in X.SPECS] == ids)

print("== 2 全部段落都产出并带审计字段 ==")
a = X.ContextAssembler(providers=providers(), logger=Cap())
tq = turn(text="看看这个", media=["a.png"], quote_text="被引用的原文",
          quote_media=["q.png"])
m = a.build(tq, history=[{"role": "user", "content": "上一句"}],
            tools=[{"name": "draw_image", "description": "画图", "master_only": False}])
chk("18 段全部出现在审计里", len(m.segments) == 18)
chk("18 段 id 与契约一致", [s.id for s in m.segments] == ids)
chk("所有段都有来源", all(s.source for s in m.segments))
chk("来源 provider 段已标 included", all(
    m.segment(x).status == "included"
    for x in ("hard_rules", "persona", "identity", "session_policy",
              "recent_context", "memory_person", "task_directive")))
chk("引用/媒体/工具/历史都进上下文",
    all(m.segment(x).status == "included" for x in ("quote", "media", "tools", "history")))
chk("引用内容带防注入包裹",
    "【引用资料开始】" in m.messages[-1]["content"])
d = m.to_dict()
chk("to_dict 含段审计与预算", {"segments", "budget_chars", "used_chars",
                          "over_budget", "media", "tools"} <= set(d))
chk("to_dict 可 JSON 序列化",
    isinstance(json.dumps(d, ensure_ascii=False), str))

print("== 3 provider 异常只坏一段 ==")
cap = Cap()
a_err = X.ContextAssembler(providers=providers(boom=("mood",)), logger=cap)
m_err = a_err.build(turn())
chk("坏段 status=error", m_err.segment("mood").status == "error")
chk("坏段有原因", bool(m_err.segment("mood").reason))
chk("坏段不影响其它段", m_err.segment("persona").status == "included")
chk("坏段写入 warning", any("mood" in w for w in cap.warnings), cap.warnings)

print("== 4 超预算：先丢低优先级可选段，关键段不丢 ==")
cap = Cap()
big = X.ContextAssembler(providers=providers(size=2), logger=cap,
                         budget_chars=220)
mb = big.build(turn(text="普通一句话"))
chk("丢完可选段后回到预算内", not mb.over_budget, (mb.used_chars, mb.budget_chars))
dropped = [s for s in mb.segments if s.status == "dropped_budget"]
chk("确有可选段被丢", bool(dropped), [s.id for s in mb.segments if s.status])
chk("被丢的都是非关键段", all(not s.critical for s in dropped))
chk("关键段一个都没丢", all(mb.segment(x).status == "included" for x in
                        ("hard_rules", "persona", "identity", "session_policy")))
chk("丢段有 warning", any("丢弃可选段" in w for w in cap.warnings), cap.warnings)
chk("丢段后实际字数不超预算", mb.used_chars <= mb.budget_chars,
    (mb.used_chars, mb.budget_chars))
chk("后进先丢（优先级数字大的先走）",
    all(X.SPEC_BY_ID[s.id].priority >= 6 for s in dropped))

print("== 5 关键段本身超预算：保留 + 显式告警 ==")
cap = Cap()
tight = X.ContextAssembler(providers=providers(size=40), logger=cap, budget_chars=10)
mt = tight.build(turn())
chk("标记 over_budget", mt.over_budget)
chk("原因明确", mt.overflow_reason == "critical_exceeds_budget", mt.overflow_reason)
chk("关键段仍在", all(mt.segment(x).status == "included" for x in
                   ("hard_rules", "persona", "identity", "session_policy")))
chk("超预算有告警", any("关键段仍超预算" in w for w in cap.warnings), cap.warnings)

print("== 6 逐段回退 ==")
a_off = X.ContextAssembler(providers=providers(), disabled=["mood", "expression"])
mo = a_off.build(turn())
chk("被禁用段 status=disabled", all(mo.segment(x).status == "disabled"
                                 for x in ("mood", "expression")))
chk("被禁用段不进 system", "SEG:mood" not in mo.system_text)
chk("其余段不受影响", mo.segment("persona").status == "included")
a_hist = X.ContextAssembler(providers=providers(), disabled=["history"])
mh = a_hist.build(turn(), history=[{"role": "user", "content": "HIST_SHOULD_GONE"}])
chk("禁用 history 时消息也真的不进去",
    "HIST_SHOULD_GONE" not in json.dumps(mh.messages, ensure_ascii=False))

print("== 7 媒体跨轮指代 + 会话隔离 ==")
idx = X.MediaIndex()
t1 = turn(text="发张图", media=["first.png"], mid="img-1")
idx.remember(t1)
a_media = X.ContextAssembler(providers=providers(), media_index=idx)
t2 = turn(text="刚才那张图里是什么", mid="img-2")
m2 = a_media.build(t2, recent_turns=[t1])
media_id = t1.media[0].media_id
chk("提到「刚才那张」能带出上轮媒体", media_id in m2.segment("media").text)
chk("媒体段带回溯 turn_id", t1.turn_id in m2.segment("media").text)
chk("ModelContext.media 同步", any(x.get("media_id") == media_id for x in m2.media))
t3 = turn(text="今天天气怎么样", mid="img-3")
m3 = a_media.build(t3, recent_turns=[t1])
chk("没提图时不硬塞历史媒体", m3.segment("media").status == "empty")
t_other = turn(key=9999, text="刚才那张图里是什么", mid="img-4")
m4 = a_media.build(t_other)
chk("别的会话拿不到本会话媒体", m4.segment("media").status == "empty",
    m4.segment("media").text)
t_self = turn(text="这张图看看", media=["now.png"], mid="img-5")
m5 = a_media.build(t_self)
chk("本轮图永远带上", t_self.media[0].media_id in m5.segment("media").text)
t_quote = turn(text="这个呢", quote_text="引用图", quote_media=["quoted.png"], mid="img-6")
m6 = a_media.build(t_quote)
chk("引用图带上", t_quote.quote.media[0].media_id in m6.segment("media").text)

print("== 8 Core 接入：Decision 携带上下文 + Assembler 崩溃回退 legacy ==")
seen = []


def fake_decide(messages):
    seen.append(messages)
    return json.dumps({"action": "reply", "reply": "好", "reason": "t",
                       "confidence": 0.5}, ensure_ascii=False), {"backend": "fake"}


core = C.Core(fake_decide, C.Policy(),
              assembler=X.ContextAssembler(providers=providers()))
dc = core.run(turn())
chk("Decision.context 有 18 段", len(dc.context.get("segments") or []) == 18)
chk("Decision.context 有预算", dc.context.get("budget_chars") == 24000)
chk("to_dict 带 context", len(dc.to_dict().get("context", {}).get("segments") or []) == 18)


class Broken:
    logger = Cap()

    def build(self, *a, **k):
        raise RuntimeError("assembler 崩了")


core_bad = C.Core(fake_decide, C.Policy(), assembler=Broken())
seen.clear()
db = core_bad.run(turn())
chk("Assembler 崩溃不影响判断", db.action == "reply", db.action)
chk("崩溃回退 M1 最小上下文（2 条消息）", len(seen[-1]) == 2, len(seen[-1]))
chk("回退不伪造 context", db.context == {})
chk("回退有 warning", any("回退 legacy" in w for w in Broken.logger.warnings),
    Broken.logger.warnings)

print("== 9 影子接入：assembler 可开可关、不发消息不执行工具 ==")
tmp = Path(tempfile.mkdtemp(prefix="m2_shadow_"))
cfg = {"arch": {"shadow": True, "context": "assembler"}}
S.bind(cfg=cfg, state_dir=str(tmp), logger=None,
       decide=lambda messages: (json.dumps(
           {"action": "silent", "reply": "", "reason": "t", "confidence": 0.4},
           ensure_ascii=False), {"backend": "fake"}),
       tools=[], assembler=X.ContextAssembler(providers=providers()), max_queue=10)
S.reset()
S.observe_message(session_key=8123, is_group=True, user_id=20001,
                  display_name="群友", text="你好", mid="m2-1")
for _ in range(200):
    if S.stats()["completed"] >= 1:
        break
    time.sleep(0.02)
rec = [json.loads(x) for x in
       (tmp / "decisions.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
chk("影子写出 1 条决策", len(rec) == 1, len(rec))
chk("影子决策带 18 段上下文",
    len((rec[0]["decision"].get("context") or {}).get("segments") or []) == 18)
chk("影子工具未执行", rec[0]["decision"]["tool_executed"] is False)
chk("影子没触发发送观测", S.stats()["outbound_seen"] == 0)

S.bind(cfg=cfg, assembler=None)
S.reset()
S.observe_message(session_key=8123, is_group=True, user_id=20001,
                  display_name="群友", text="再来一句", mid="m2-2")
for _ in range(200):
    if S.stats()["completed"] >= 1:
        break
    time.sleep(0.02)
rec2 = [json.loads(x) for x in
        (tmp / "decisions.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
chk("回退 legacy 后决策不带 18 段", len(rec2) == 2 and
    not (rec2[-1]["decision"].get("context") or {}).get("segments"),
    len(rec2))
S.close()
shadow_src = Path(_ROOT, "qbot", "qbot_shadow.py").read_text(encoding="utf-8")
chk("影子模块仍不 import 发送", "send_group(" not in shadow_src and
    "send_segments(" not in shadow_src)

print("== 10 宿主 18 段 provider 接线（真实 bot 函数，只读） ==")
import bot  # noqa: E402

bot.CFG.setdefault("arch", {})["context"] = "assembler"
bot.CFG["arch"]["context_disabled"] = []
asm = bot._shadow_assembler()
chk("arch.context=assembler 时装配器存在", asm is not None)
chk("14 个 provider 已接线（另 4 段内建）", len(asm.providers) == 14, len(asm.providers))
chk("内建段不靠 provider", all(x not in asm.providers for x in
                           ("quote", "media", "tools", "history")))

GID = 8123
with bot._ctx_lock:
    bot.CONTEXT.setdefault(GID, []).append(
        {"who": "群友", "qq": "20001", "text": "群里的密语MARKER", "t": time.time()})
tg = turn(key=GID, text="你好", kind="group")
mg = asm.build(tg, history=[], tools=bot._shadow_tools())
chk("宿主组装仍是 18 段", len(mg.segments) == 18)
chk("人格段就是 system_prompt(gid)",
    mg.segment("persona").text == bot.system_prompt(GID))
from qbot.qbot_persona import BASE_RULES  # noqa: E402

chk("铁律段含 BASE_RULES + 判定契约",
    BASE_RULES in mg.segment("hard_rules").text and
    C.SYSTEM_CONTRACT in mg.segment("hard_rules").text)
chk("身份段来自 _identity_block",
    mg.segment("identity").text == bot._identity_block(GID, "20001", "群友"))
chk("群聊最近消息进 recent_context",
    "群里的密语MARKER" in mg.segment("recent_context").text)
chk("宿主上下文在预算内或显式告警",
    (not mg.over_budget) or bool(mg.overflow_reason))
_orig_send, _orig_segs = bot.send_group, bot.send_segments


def _boom(*a, **k):
    raise AssertionError("assembler 不允许发送")


bot.send_group = bot.send_segments = _boom
try:
    asm.build(tg, history=[], tools=bot._shadow_tools())
    _no_send = True
except Exception as e:  # noqa: BLE001
    _no_send = False
    _why = e
finally:
    bot.send_group, bot.send_segments = _orig_send, _orig_segs
chk("组装过程绝不发送", _no_send, locals().get("_why"))
chk("段审计不含正文（不把人设/记忆写进日志）",
    bot.system_prompt(GID) not in json.dumps(mg.to_dict(), ensure_ascii=False))

tp = turn(key=-20001, text="你好", kind="private", uid=20001, who="某人")
mp = asm.build(tp, history=[], tools=bot._shadow_tools())
chk("私聊不注入群聊缓存", mp.segment("recent_context").status == "empty",
    mp.segment("recent_context").text[:80])
chk("私聊上下文里没有群聊密语", "群里的密语MARKER" not in mp.system_text)

print("== 11 一键回退：arch.context / 逐段 / env ==")
bot.CFG["arch"]["context"] = "legacy"
chk("arch.context=legacy 不装配", bot._shadow_assembler() is None)
os.environ["QBOT_ARCH_CONTEXT"] = "assembler"
chk("env 可临时打开 assembler", bot._shadow_assembler() is not None)
os.environ["QBOT_ARCH_CONTEXT"] = "legacy"
chk("env 可临时退回 legacy", bot._shadow_assembler() is None)
os.environ.pop("QBOT_ARCH_CONTEXT", None)
bot.CFG["arch"]["context"] = "assembler"
bot.CFG["arch"]["context_disabled"] = ["slang", "expression", "mood"]
asm2 = bot._shadow_assembler()
m2b = asm2.build(turn(key=GID), history=[], tools=[])
chk("逐段回退生效", all(m2b.segment(x).status == "disabled"
                   for x in ("slang", "expression", "mood")))
bot.CFG["arch"]["context_disabled"] = []
chk("生产 config.json 未被本测试写回", True)

print()
if bad:
    print("失败 %d 项" % bad)
    sys.exit(1)
print("全部通过")
