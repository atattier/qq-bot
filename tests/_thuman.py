# -*- coding: utf-8 -*-
"""拟人化发送 / 发送闸门 / 合批窗口 / 角色卡体检 —— 离线自测（2026-10-19）。

参考 ai-companion-kit 移植的四件东西，全部不联网、不真发：
  · qbot_humanize.py  打字时长、分条、停顿
  · bot._api_send     限速 + 熔断
  · bot.batch_push    群聊合批
  · qbot_persona.lint_persona  角色卡体检

用法：python tests/_thuman.py
"""
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)

import time

_sys.stdout.reconfigure(encoding="utf-8")

from qbot import qbot_humanize as H    # noqa: E402
import bot                   # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    print("  %s %s%s" % ("[OK]" if cond else "[FAIL]", label, ("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


FAST = {"enabled": True, "ms_per_char": 5, "min_ms": 10, "max_ms": 40, "jitter": 0.2,
        "burst_max": 4, "gap_min_ms": 10, "gap_max_ms": 30, "long_gap_chance": 0,
        "long_gap_min_ms": 10, "long_gap_max_ms": 20, "night_slow": False,
        "max_total_ms": 0, "typing_enabled": False}

print("== 分条 ==")
chk("短句不切", H.split("在") == ["在"])
long_one = "今天下午我们去看了那个新开的展。" * 8
parts = H.split(long_one, c={"burst_max": 4})
chk("长句按句末标点切开", len(parts) >= 2, len(parts))
chk("条数被 burst_max 收敛", len(parts) <= 4, len(parts))
chk("短句不分条（<40 字整体留着）", len(H.split("一。二。三。四。五。六。", c={"burst_max": 3})) == 1)
chk("碎句向相邻合并", H.split("嗯\n好\n那我先走了啊") == ["嗯好那我先走了啊"], H.split("嗯\n好\n那我先走了啊"))

print("== 打字时长 / 停顿 ==")
c = H.cfg_of({"jitter": 0, "night_slow": False})
chk("短消息不短于最小值", H.typing_ms("在", c) >= 1200, H.typing_ms("在", c))
chk("长消息撞上限", H.typing_ms("字" * 400, c) == 6000, H.typing_ms("字" * 400, c))
mid = H.typing_ms("字" * 20, c)
chk("中等长度按字数算", 1200 < mid < 6000, mid)
g = [H.gap_ms(H.cfg_of({"long_gap_chance": 0})) for _ in range(50)]
chk("条间停顿在 700~2600", all(700 <= x <= 2600 for x in g), (min(g), max(g)))

print("== say() ==")
sent = []
n, _p = H.say(["一", "二", "三"], lambda p: (sent.append(p), True)[1],
              c=dict(FAST, enabled=False))
chk("关掉拟人化=直发三条", n == 3 and sent == ["一", "二", "三"], sent)
sent.clear()
t0 = time.time()
n, _p = H.say("一。二。三。", lambda p: (sent.append(p), True)[1], c=FAST)
chk("开启后按序发完", n >= 1 and len(sent) == n, sent)
chk("参数压缩后不拖时间", time.time() - t0 < 1.5, "%.2fs" % (time.time() - t0))
chk("send 返回 False 就停", H.say("一。二。三。", lambda p: False, c=FAST)[0] == 0)

print("== 发送闸门（限速） ==")
# 🔴 2026-10-30：本用例专测闸门，必须开逃生舱（`_api_send` 对测试进程默认静音，
#    主人要求「测试的发言不要发群里」）。安全前提：下面 `bot.api` 全程是桩。
bot._MUTE_BYPASS["on"] = True
bot._IN_TEST = False                      # 临时打开真实闸门
bot._send_times.clear()
bot._circuit.update({"fails": 0, "open_until": 0.0})
bot.CFG["send"] = {"rate_on": True, "circuit_on": False,
                   "max_per_minute": 2, "max_per_hour": 99}
bot.api = lambda *a, **kw: {"status": "ok", "retcode": 0}
r1 = bot._api_send(123450001, [{"type": "text", "data": {"text": "a"}}])
r2 = bot._api_send(123450001, [{"type": "text", "data": {"text": "b"}}])
r3 = bot._api_send(123450001, [{"type": "text", "data": {"text": "c"}}])
chk("前两条放行", r1.get("status") == "ok" and r2.get("status") == "ok")
chk("第三条被限速挡住", r3.get("retcode") == -9 and "每分钟" in str(r3.get("msg")), r3)

print("== 发送闸门（熔断） ==")
bot._send_times.clear()
bot._circuit.update({"fails": 0, "open_until": 0.0})
bot.CFG["send"] = {"rate_on": False, "circuit_on": True,
                   "circuit_fails": 3, "circuit_open_sec": 180, "max_per_minute": 99}


def _boom(*a, **kw):
    raise RuntimeError("upstream refused")


bot.api = _boom
for _ in range(3):
    try:
        bot._api_send(123450001, [{"type": "text", "data": {"text": "x"}}])
    except Exception:
        pass
chk("连续 3 次失败后熔断打开", bot._circuit["open_until"] > time.time())
bot.api = lambda *a, **kw: {"status": "ok", "retcode": 0}
r = bot._api_send(123450001, [{"type": "text", "data": {"text": "y"}}])
chk("熔断期间一个包都不发", r.get("retcode") == -9 and "熔断" in str(r.get("msg")), r)
bot._circuit["open_until"] = 0
bot._api_send(123450001, [{"type": "text", "data": {"text": "z"}}])
chk("成功后失败计数清零", bot._circuit["fails"] == 0)

print("== 合批窗口 ==")
got = []
bot._BATCH.clear()
bot.CFG["batch"] = {"group_ms": 250, "max_items": 6}
bot.batch_push(123450001, {"text": "第一条"}, lambda g, items: got.append([i["text"] for i in items]))
bot.batch_push(123450001, {"text": "第二条"}, lambda g, items: got.append([i["text"] for i in items]))
chk("窗口内先不处理", got == [], got)
time.sleep(0.6)
chk("到点一次性交出去", got == [["第一条", "第二条"]], got)
got.clear()
bot.CFG["batch"] = {"group_ms": 5000, "max_items": 2}
bot.batch_push(1, {"text": "a"}, lambda g, items: got.append(len(items)))
bot.batch_push(1, {"text": "b"}, lambda g, items: got.append(len(items)))
time.sleep(0.2)
chk("攒满 max_items 提前处理", got == [2], got)
got.clear()
bot.CFG["batch"] = {"group_ms": 0, "max_items": 6}
bot.batch_push(2, {"text": "now"}, lambda g, items: got.append(len(items)))
chk("group_ms=0 立即处理", got == [1], got)

print("== 角色卡体检 ==")
bot._IN_TEST = True
bot._MUTE_BYPASS["on"] = False             # 闸门段测完就关掉，别把逃生舱留着
r = bot.lint_persona()
# 2026-10-27 改：原来这条断言的是「**当前**人设体检通过」—— 主人随手在网页上换个人设
# （比如换成内容还没写完的草稿卡），整套回归就红了，看着像代码坏了，其实是数据问题。
# 体检器本身该测的是「合格卡判过 / 不合格卡判不过」，所以固定用一份自造样卡测，
# 当前人设的真实结论只打出来给主人看，不参与判定。
GOOD_CARD = (
    "你是小鲸，群里的老熟人。\n"
    "【怎么说话】短句、口语、爱用「～」；接梗快，不端着。\n"
    "【不做什么】不要写长段大作文；不要说教；不要用 Markdown 列表。\n"
    "【说话示例】\n"
    "- 被叫「在吗」→「在的在的，咋啦～」\n"
    "- 被夸 →「嘿嘿，这还用你说～」\n"
)
g = bot.lint_persona(GOOD_CARD)
chk("合格卡体检通过", g["ok"], g["warn"])
print("  [--] 当前人设「%s」（%d 字）体检：%s"
      % (bot.persona_active(), r.get("chars") or 0,
         "通过" if r["ok"] else "；".join(r["warn"])))
chk("空卡报错", bot.lint_persona("")["ok"] is False)
w = bot.lint_persona("你是小鲸。你是助手。")["warn"]
chk("身份写成助手会报警", any("助手" in x for x in w), w)
w2 = bot.lint_persona("你是小鲸。不要 Markdown。")["warn"]
chk("缺「怎么说话」会报警", any("怎么说话" in x for x in w2), w2)
chk("超长段落会报警", any("超长段落" in x for x in bot.lint_persona("不要\n" + "啊" * 500)["warn"]))

print("\n==== " + ("ALL PASS" if ok else "有用例失败") + " ====")
_sys.exit(0 if ok else 1)
