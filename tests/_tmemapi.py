# -*- coding: utf-8 -*-
"""长期记忆的接口层冒烟测试：MCP stdio（不需要 bot 起来）。

验证三件事：
  1. tools/list 里必须有 qq_remember / qq_recall，且 schema 合法（能被 MCP 客户端接受）
  2. 工具能被正常派发（bot 没起时会返回「连不上 bot」，**不能抛异常/不能崩进程**）
  3. 未知工具名要有正常报错，不能静默成功

用法： python tests/_tmemapi.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRV = str(BASE / "qq-mcp-server.py")
PY = sys.executable
ok = True


def chk(name, cond, extra=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + name + (("  " + str(extra)[:220]) if extra else ""))
    if not cond:
        ok = False


def rpc(proc, obj):
    proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
    proc.stdin.flush()


env = dict(os.environ, QQBOT_TOKEN="dummy-token", PYTHONIOENCODING="utf-8")
proc = subprocess.Popen([PY, SRV], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, env=env, cwd=str(BASE),
                        text=True, encoding="utf-8", errors="replace")
try:
    rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
               "params": {"name": "qq_recall",
                          "arguments": {"group_id": 123450001, "query": "考研"}}})
    rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
               "params": {"name": "qq_remember",
                          "arguments": {"group_id": 123450001, "content": "在准备考研"}}})
    rpc(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
               "params": {"name": "qq_不存在", "arguments": {}}})
    proc.stdin.close()

    res = {}
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            print("  (非 JSON 行) " + line[:160])
            continue
        if o.get("id"):
            res[o["id"]] = o
    proc.wait(timeout=30)

    print("== initialize ==")
    chk("握手成功", (res.get(1) or {}).get("result", {}).get("serverInfo", {}).get("name") == "qqbot",
        res.get(1))

    print("== tools/list ==")
    body = (res.get(2) or {}).get("result", {})
    names = [t["name"] for t in body.get("tools", [])]
    chk("工具列表非空", len(names) > 0, names)
    chk("含 qq_recall", "qq_recall" in names)
    chk("含 qq_remember", "qq_remember" in names)
    for n in ("qq_recall", "qq_remember"):
        t = [x for x in body.get("tools", []) if x["name"] == n]
        if t:
            sch = t[0].get("inputSchema") or {}
            chk("%s schema 合法(有 properties/type)" % n,
                sch.get("type") == "object" and isinstance(sch.get("properties"), dict))

    print("== tools/call（bot 没起，应优雅报错而不是崩）==")
    for rid, label in ((3, "qq_recall"), (4, "qq_remember")):
        o = res.get(rid) or {}
        c = ((o.get("result") or {}).get("content") or [{}])[0].get("text", "")
        chk("%s 有返回体" % label, bool(c), c[:160])
        try:
            payload = json.loads(c)
        except Exception:
            payload = {}
        chk("%s 返回是 JSON 且不静默成功" % label,
            isinstance(payload, dict) and payload.get("ok") is not True,
            payload.get("error") or payload)
    o5 = res.get(5) or {}
    c5 = ((o5.get("result") or {}).get("content") or [{}])[0].get("text", "")
    chk("未知工具有报错", "没有这个工具" in c5, c5[:160])

    err = proc.stderr.read() or ""
    if err.strip():
        print("  (stderr) " + err.strip()[:300])
finally:
    if proc.poll() is None:
        proc.kill()

print("\n==== " + ("ALL PASS" if ok else "有用例失败") + " ====")
sys.exit(0 if ok else 1)
