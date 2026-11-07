# -*- coding: utf-8 -*-
"""异步画图任务的真实回执：排队不等于出图，失败要有终态。

离线全桩：不联网、不发消息、不碰生产 state、不跑真 ComfyUI。
所有可能触发外部调用的现画 worker 都用假队列隔离；只验结构化事实。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
os.environ["QBOT_TEST"] = "1"

BAD = []


def chk(name, ok, why=""):
    if not ok:
        BAD.append(name)
    print(("  [PASS] " if ok else "  [FAIL] ") + name + ("" if ok else " | " + str(why)))


class FakeQueue:
    """收下 job，但绝不启动 worker、不碰真实 Queue。"""

    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)

    def qsize(self):
        return len(self.items)

    def empty(self):
        return not self.items

    def get(self):
        return self.items.pop(0)

    def task_done(self):
        pass


import bot  # noqa: E402
import time  # noqa: E402

bot.save_config = lambda: True
real_queue = bot._draw_q
fake_q = FakeQueue()
bot._draw_q = fake_q
bot._draw_results.clear()
bot.comfy_info = lambda gid=None: {
    "server_up": True, "drawing": False, "queued": 0,
    "engine_here": "moss", "cooldown_left_sec": 0, "done_this_hour": 0,
}
bot.comfy_up = lambda *a, **kw: True
bot.comfy_enabled = lambda *a, **kw: True
bot.draw_gate = lambda *a, **kw: None
bot.engine_on_here = lambda *a, **kw: True
bot.style_local_engine = lambda style: "comfyui"
bot._draw_state["last"] = 0.0
bot._draw_state["count"] = 0
bot._draw_state["since"] = 0.0
bot._draw_state["busy"] = False
bot._draw_state["up"] = None
bot._draw_state["up_t"] = 0.0

# ① draw_image 结果必须明确写「排队」，不许把 ok 冒充出图完成。
r = bot._exec_bot_tool("draw_image", {"what": "一只橘猫", "style": "厚涂"},
                       123450001, uid="123450005", priv=True, who="验证")
chk("draw_image 只声明 queued", isinstance(r, dict)
    and r.get("ok") is True and r.get("queued") is True
    and r.get("done") is False and r.get("status") == "queued", r)
job = fake_q.items[0] if fake_q.items else {}
results = getattr(bot, "_draw_results", None)
chk("画图入队后有可查询的任务终态表", isinstance(results, dict), type(results).__name__)
if isinstance(results, dict):
    rec = results.get(job.get("id"))
    chk("任务初始状态是 queued", bool(rec) and rec.get("status") == "queued", rec)
    chk("任务初始状态明确未完成", bool(rec) and rec.get("done") is False, rec)
    chk("任务回执包含真实链路", bool(rec) and rec.get("engine") == "comfyui", rec)
    chk("任务回执包含会话", bool(rec) and rec.get("gid") == 123450001, rec)

# ② 主脑跨轮查状态时必须读到最近任务的真实终态，且能区分会话默认引擎。
st = bot._exec_bot_tool("bot_status", {}, 123450001, priv=True)
last = st.get("last_draw") if isinstance(st, dict) else None
chk("bot_status 含最近任务真实终态", isinstance(last, dict), st)
chk("bot_status 区分会话默认引擎与本次任务链路",
    isinstance(st, dict) and st.get("last_draw", {}).get("engine") == "comfyui"
    and st.get("engine_here") == "moss", st)

# ③ worker 的失败出口必须把 status/error 写进同一份回执表。
# 这里直接调用生产 do_draw 的失败包装同源函数，不起线程、不调外部服务。
_G = bot.draw_submit.__globals__
_G["_draw_result_set"](job.get("id"), status="running", done=False)
_G["_draw_result_set"](job.get("id"), status="failed", done=True,
                       error="工作流提交失败")
if isinstance(results, dict):
    rec = results.get(job.get("id")) or {}
    chk("后台失败后任务终态为 failed", rec.get("status") == "failed", rec)
    chk("后台失败原因对状态查询可见", "工作流提交失败" in str(rec.get("error") or ""), rec)
    st = bot._exec_bot_tool("bot_status", {}, 123450001, priv=True)
    chk("跨轮 bot_status 能读到最近失败",
        isinstance(st, dict) and (st.get("last_draw") or {}).get("status") == "failed", st)
    chk("跨轮 bot_status 能读到失败原因",
        isinstance(st, dict) and "工作流提交失败" in str(
            ((st.get("last_draw") or {}).get("error") or "")), st)

# ④ 决策层不新增提示词规矩；bot_status 的返回事实已由 ②③ 覆盖。
chk("没有新增提示词话术", "queued 不是 done" not in bot._OMNI_SYS_HINT
    and "查 last_draw" not in bot._OMNI_SYS_HINT, bot._OMNI_SYS_HINT[-160:])

# ⑤ 2026-11-07 审阅 Critical ①：裸画图指令没有任何内容来源时，绝不直发空提示词，
#    必须落主脑追问（_bare_draw_source 契约「空 = 必须追问」）。
_submits = []
bot.draw_submit = lambda gid, what, style, who, *a, **kw: (
    _submits.append((gid, what)), (True, ""))[1]
bot.draw_engine = lambda gid=None: "moss"
bot.handle_moss_reverse = lambda *a, **kw: None
bot._last_img_turn.clear()
r = bot._route_bare_draw_and_submit(
    123450001, "123450005", "画个", "", 0, [], {}, "验证", 0, True, True, False)
chk("空来源裸指令不直发 draw_submit", not _submits, _submits)
chk("空来源裸指令交主脑（defer）", r == (False, None), r)
bot._last_prompt.pop(123450001, None)

# ⑥ 2026-11-07 审阅 Critical ②：大脑一轮结束后，工具上下文必须清干净——
#    _TOOL_CTX 是进程级单例，残留 gid/uid/priv/allow_draw 就是串群+越权。
_ctx_seen = {}


def _fake_chat_once_tools(name, msgs, tools=None, **kw):
    _ctx_seen.update(dict(bot._TOOL_CTX))
    return "（桩）", True


bot.chat_once_tools = _fake_chat_once_tools
bot._TOOLS_BROKEN["until"] = 0.0
bot.private_brain = lambda gid=None: "fake"
bot.provider_cfg = lambda name: {"url": "http://127.0.0.1/fake"}
bot._omni_tools = lambda privileged, allow_draw=True: []
bot._identity_block = lambda *a, **kw: ""
bot._tool_img_segs = lambda *a, **kw: []
bot.quoted_images = lambda *a, **kw: []
bot.brain_with_tools(111, "在吗", "（系统桩）", uid="999", privileged=True)
chk("大脑轮内上下文就位", _ctx_seen.get("gid") == 111 and _ctx_seen.get("priv") is True,
    _ctx_seen)
_after = dict(bot._TOOL_CTX)
chk("大脑轮后上下文已清空", _after.get("gid") == 0 and _after.get("uid") == ""
    and _after.get("priv") is False, _after)

bot._draw_q = real_queue

# ⑦ 2026-11-07 审阅 Important（send_segments 固定文案收口）：handle_video_gen
#    受理/拒绝返回结构化事实，不再在工具层直接说人话；gen_video 透传拒绝原因。
bot._draw_q = real_queue
bot._vg_q = FakeQueue()
bot._vg_state["busy"] = False
bot._vg_state["last"] = 0.0
bot._vg_state["count"] = 0
bot._vg_state["since"] = time.time() if "time" in dir(bot) else 0.0
bot.CFG.setdefault("video_gen", {})["min_desc"] = 10
bot.CFG["video_gen"]["cooldown_sec"] = 0
bot.CFG["video_gen"]["max_per_hour"] = 5
bot.CFG["video_gen"]["enabled"] = True
bot.CFG["video_gen"]["members_allowed"] = True
r = bot._exec_bot_tool("gen_video", {"desc": "一只猫在雪地里奔跑，说：太冷了"},
                       123450001, uid="123450005", priv=True)
chk("gen_video 受理返回 queued 事实", isinstance(r, dict)
    and r.get("ok") is True and r.get("queued") is True, r)
chk("gen_video 受理后任务进队列", len(bot._vg_q.items) == 1, bot._vg_q.items)
r2 = bot._exec_bot_tool("gen_video", {"desc": "短"},
                        123450001, uid="123450005", priv=True)
chk("gen_video 描述太短返回结构化拒绝", isinstance(r2, dict)
    and r2.get("ok") is False and "字" in str(r2.get("error") or ""), r2)
bot._vg_state["busy"] = True
r3 = bot._exec_bot_tool("gen_video", {"desc": "一只猫在雪地里奔跑，说：太冷了"},
                        123450001, uid="123450005", priv=True)
bot._vg_state["busy"] = False
chk("gen_video 忙时返回结构化拒绝", isinstance(r3, dict)
    and r3.get("ok") is False and "生成" in str(r3.get("error") or ""), r3)

# ⑧ send_latest_comfy_image 不再拼固定图注；返回结构化事实由出口层发送。
bot.images_on = lambda: True
bot.is_priv = lambda gid=None: True
bot.latest_comfy_image = lambda: None
r4 = send_latest = bot.send_latest_comfy_image(123450001, True)
chk("最新图无产出返回结构化未找到", isinstance(r4, dict)
    and r4.get("ok") is False and r4.get("found") is False, r4)

print()
if BAD:
    print("失败 %d 项：%s" % (len(BAD), BAD))
    sys.exit(1)
print("PASS 画图终态回执 全部验证通过")
