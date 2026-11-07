# -*- coding: utf-8 -*-
"""R1 边界：读记忆接口没有会话 ID 时必须拒绝，不能退回全局查询（SAFE，临时库）。

覆盖 action_memory_read（MCP qq_recall / 网页读记忆的后端）：
  ① 群上下文（正整数）只读本群事实；
  ② 私聊上下文（负数会话 key）只读本私聊事实；
  ③ query 为空（“列最近记的”）也按会话过滤；
  ④ 缺 group / 空串 / 非数字 / 0 / bool / float → ok=False，不返回任何 facts；
  ⑤ HTTP GET /api/memory 同样覆盖（MCP 走的就是这个端点）。
不写生产 state，不发 QQ。
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QBOT_TEST"] = "1"

FAIL = []


def chk(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  | %s" % (detail,) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


import bot  # noqa: E402
import qbot_memory as M  # noqa: E402

GA, GB = 812910001, 812910002
PA, PB = -19101, -19102
bot.CFG.setdefault("memory", {})["enabled"] = True

for cid, qq, name, tag in [(GA, "10001", "GA_NAME", "GA"), (GB, "10002", "GB_NAME", "GB"),
                           (PA, "10001", "PA_NAME", "PA"), (PB, "10002", "PB_NAME", "PB")]:
    M.note_person(cid, qq, name)
    M.remember_fact(cid, qq, "FACT_%s_ALPHA" % tag, name=name)

ALL = ["GA", "GB", "PA", "PB"]


def facts_text(res):
    return " ".join(str(x.get("content") or "") for x in (res.get("facts") or []))


def foreign(text, own):
    return [t for t in ALL if t != own and ("FACT_%s" % t) in text]


print("== ① group 上下文（有 query） ==")
r = bot.action_memory_read(GA, "FACT")
chk("group A 只返回 A", r.get("ok") and "FACT_GA_ALPHA" in facts_text(r)
    and not foreign(facts_text(r), "GA"), r.get("facts"))
r = bot.action_memory_read(str(GB), "FACT")
chk("字符串群号同样按会话过滤", r.get("ok") and "FACT_GB_ALPHA" in facts_text(r)
    and not foreign(facts_text(r), "GB"), r.get("facts"))

print("== ② private 上下文 ==")
r = bot.action_memory_read(PA, "FACT")
chk("private A 只返回 A", r.get("ok") and "FACT_PA_ALPHA" in facts_text(r)
    and not foreign(facts_text(r), "PA"), r.get("facts"))
r = bot.action_memory_read(str(PB), "FACT")
chk("字符串私聊 key 同样按会话过滤", r.get("ok") and "FACT_PB_ALPHA" in facts_text(r)
    and not foreign(facts_text(r), "PB"), r.get("facts"))

print("== ③ 空 query（列最近记的）也按会话过滤 ==")
r = bot.action_memory_read(GA, "")
chk("空 query 群 A 只含 A", r.get("ok") and "FACT_GA_ALPHA" in facts_text(r)
    and not foreign(facts_text(r), "GA"), r.get("facts"))
r = bot.action_memory_read(PA, "")
chk("空 query private A 只含 A", r.get("ok") and "FACT_PA_ALPHA" in facts_text(r)
    and not foreign(facts_text(r), "PA"), r.get("facts"))

print("== ③b memory_people 按会话（同一 QQ 在不同会话不同名） ==")
for own, cid in [("GA", GA), ("GB", GB), ("PA", PA), ("PB", PB)]:
    ps = M.people(cid, 20)
    names = " ".join(str(p.get("name") or "") for p in ps)
    chk("people %s 只含本会话" % own,
        ("%s_NAME" % own) in names and not any(("%s_NAME" % t) in names for t in ALL if t != own),
        ps)
r = bot.action_memory_read(GA, "FACT")
pn = " ".join(str(p.get("name") or "") for p in (r.get("people") or []))
chk("action_memory_read 群 A people 只含 A", "GA_NAME" in pn and "PA_NAME" not in pn, r.get("people"))
r = bot.action_memory_read(PA, "FACT")
pn = " ".join(str(p.get("name") or "") for p in (r.get("people") or []))
chk("action_memory_read 私聊 A people 只含 A", "PA_NAME" in pn and "GA_NAME" not in pn, r.get("people"))

print("== ④ 缺/非法 group 一律拒绝 ==")
for label, g in [("None", None), ("空串", ""), ("空白", "   "),
                 ("非数字", "abc"), ("0", 0), ("字符串0", "0"),
                 ("bool", True), ("float", 123.9), ("全角数字", "１２３")]:
    r = bot.action_memory_read(g, "FACT")
    chk("%s → 拒绝且不返回 facts" % label,
        r.get("ok") is False and not (r.get("facts") or []), r)

print("== ⑤ HTTP GET /api/memory（MCP 实际端点） ==")
TOK = "test-memory-token-r1"
bot.bridge_token = lambda: TOK
PORT = 18091
srv = ThreadingHTTPServer(("127.0.0.1", PORT), bot.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def get(query):
    req = urllib.request.Request("http://127.0.0.1:%d/api/memory?%s" % (PORT, query),
                                 headers={"X-Bridge-Token": TOK})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


r = get("group=%d&query=FACT" % GA)
pn = " ".join(str(p.get("name") or "") for p in (r.get("people") or []))
chk("GET 群 A people 只含 A", "GA_NAME" in pn and "PA_NAME" not in pn, r.get("people"))
chk("GET 群 A 只含 A", r.get("ok") and "FACT_GA_ALPHA" in facts_text(r)
    and not foreign(facts_text(r), "GA"), r.get("facts"))
r = get("group=%d&query=FACT" % PA)
chk("GET private A 只含 A", r.get("ok") and "FACT_PA_ALPHA" in facts_text(r)
    and not foreign(facts_text(r), "PA"), r.get("facts"))
r = get("query=FACT")
chk("GET 缺 group → ok=False 且无 facts", r.get("ok") is False and not (r.get("facts") or []), r)
r = get("group=0&query=FACT")
chk("GET group=0 → ok=False", r.get("ok") is False and not (r.get("facts") or []), r)
r = get("group=abc&query=FACT")
chk("GET group=abc → ok=False", r.get("ok") is False and not (r.get("facts") or []), r)
srv.shutdown()

print("\n== 汇总 ==")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
