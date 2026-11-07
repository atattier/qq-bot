"""离线测 agent 工具面：/api/* 路由 + qq-mcp-server.py 的 stdio MCP。不发真消息。"""
import sys
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import atexit, json, os, shutil, subprocess, sys, threading, time, urllib.request
from http.server import ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8")
sys.argv = ["bot-test"]
# 2026-11-05 审计 U2：必须显式进测试态。原版靠 argv 探测，但本脚本把 argv 改成了
# "bot-test"，bot._in_test_env() 单独跑时探不到 tests 路径 ⇒ 生产 state 全裸奔
# （slang 等写的就是真文件）。QBOT_TEST=1 让 bot.py 的临时目录重绑全部生效。
_os.environ.setdefault("QBOT_TEST", "1")

# 2026-11-05 审计 U2：本测试**不再读写真 config.json / state/slang.json**（原版备份→
# 改写→atexit 还原，强杀时 8099 残留进真配置，2026-10-26 踩过）。现在：
#   ① save_config 直接桩掉——listen_port 等改动只存在内存 CFG，永世不落盘；
#   ② slang 走 bot.py 测试态自带的临时目录重绑（tests 态 import bot 时已生效），
#     不再备份/还原生产黑话表。
_ORIG_SAVE = None

import bot

_ORIG_SAVE = bot.save_config


def _no_save():
    return True


bot.save_config = _no_save
# qbot_session 在 bind 时捕获的是 save_config 的**函数对象**，光改 bot.save_config 拦不住
# 它（2026-11-05 实测：8099 又写进了真 config.json，2026-10-26 同一事故二次复现）。
# 会话总控是本测试里唯一会真正触发的落盘链路，把它的 _save 也桩掉才算封死。
bot.qbot_session._save = _no_save

SENT = []
CALLS = []


def fake_api(action, **params):
    CALLS.append((action, params))
    if action == "send_group_msg":
        SENT.append(params)
        return {"status": "ok", "retcode": 0, "data": {"message_id": 700 + len(SENT)}}
    return {"status": "ok", "data": {}}


bot.api = fake_api
bot.SELF_ID = 123450002
bot.SELF_NAME = "低空有断云"
G = 123450001
PORT = 8099
bot.CFG["listen_port"] = PORT
srv = ThreadingHTTPServer(("127.0.0.1", PORT), bot.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)

TOK = bot.bridge_token()
BASE = "http://127.0.0.1:%d" % PORT


def call(method, path, payload=None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"X-Bridge-Token": TOK,
                                          "Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:120]


bot.remember(G, "阿坤", 123450009, "今晚开黑不")
bot.remember(G, "小鱼", 123450003, "开，我选辅助")
bot.dispatch({"post_type": "message", "message_type": "group", "group_id": G,
              "user_id": bot.SELF_ID, "message_id": 8801,
              "sender": {"user_id": bot.SELF_ID}, "message": "我也来"})

print("== 1 无 token 应当 401 ==")
req = urllib.request.Request(BASE + "/api/status", method="GET")
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        print("  意外放行:", r.status)
except urllib.error.HTTPError as e:
    print("  HTTP", e.code)

print("== 2 GET /api/status /groups /members /history /slang ==")
for p in ("/api/status", "/api/groups", "/api/history?limit=5", "/api/slang"):
    st, d = call("GET", p)
    print("  %s -> %s %s" % (p, st, json.dumps(d, ensure_ascii=False)[:190]))
st, d = call("GET", "/api/members?group=%d" % G)
print("  /api/members(白名单) ->", st, str(d)[:150])
st, d = call("GET", "/api/members?group=123456")
print("  /api/members(非白名单) ->", st, str(d)[:120])
st, d = call("GET", "/api/history?limit=3&after=0")
print("  history last:", json.dumps(d, ensure_ascii=False)[-160:])

print("== 3 POST /api/send ==")
SENT.clear()
st, d = call("POST", "/api/send", {"group": G, "text": "来了来了", "image": ""})
print("  ", st, d, "| 实发:", SENT[-1] if SENT else None)
n = len(CALLS)
st, d = call("POST", "/api/send", {"group": G, "text": "密钥在 C:/secrets/config.json"})
print("  审计拦截:", st, json.dumps(d, ensure_ascii=False)[:120], "| api 增量", len(CALLS) - n)
st, d = call("POST", "/api/send", {"group": 123456, "text": "发到别的群"})
print("  白名单外:", st, json.dumps(d, ensure_ascii=False)[:120])
st, d = call("POST", "/api/send", {"group": G, "text": "带@的", "reply_to": 8801})
print("  带引用:", st, json.dumps(SENT[-1], ensure_ascii=False)[:200] if SENT else None)

print("== 4 POST /api/slang 只能提候选 ==")
st, d = call("POST", "/api/slang", {"term": "开黑", "meaning": "一起组队打游戏"})
print("  普通提议:", st, json.dumps(d, ensure_ascii=False)[:150])
st, d = call("POST", "/api/slang", {"term": "偷跑", "meaning": "提前泄露", "status": "confirmed"})
print("  夹带confirmed:", st, json.dumps(d, ensure_ascii=False)[:170])
st, d = call("GET", "/api/slang")
terms = d.get("terms", {})
print("  实际状态:", {k: terms[k].get("status") for k in ("开黑", "偷跑") if k in terms})

print("== 4b POST /api/session_control（会话总控 2026-10-26）==")
st, d = call("POST", "/api/session_control", {})
_rows = d.get("rows") or []
_priv = [r for r in _rows if r.get("private")]
_grp = [r for r in _rows if not r.get("private")]
print("  列表:", st, "| 私聊成员", len(_priv), "人，群", len(_grp), "个")
print("  私聊首行:", json.dumps(_priv[0], ensure_ascii=False) if _priv else None)
print("  人格候选:", d.get("personas"), "| 全局:", d.get("global_persona"),
      "| 私聊总开关:", d.get("private_enabled"))
print("  私聊行的 sim 应为 None:", _priv[0].get("sim") if _priv else "?")

A = -123450003          # 主人私聊（记忆库里有记录）
B = -123450005          # 另一个人 —— 用来验证「按人隔离」
# 群：关本地工具 -> 技能提示词段必须变空（这才是「关工具」的真效果）
st, d = call("POST", "/api/session_control", {"gid": G, "field": "tools", "value": False})
print("  群关工具:", st, d, "| flag:", bot.qbot_session.flag(G, "tools"),
      "| skills_block 长度:", len(bot.skills_block(G)))
st, d = call("POST", "/api/session_control", {"gid": G, "field": "persona", "value": "小鲸"})
_txt = bot.system_prompt(G)
print("  群改人格:", st, d, "| 与全局不同:", _txt != bot.system_prompt(),
      "|", _txt[:18].replace("\n", " "))
st, d = call("POST", "/api/session_control", {"gid": G, "field": "sim", "value": False})
print("  群关插话:", st, d, "| sim_allowed:", bot.sim_allowed(G, ""))

print("  -- 私聊按人：A 关了不能连带 B --")
st, d = call("POST", "/api/session_control", {"gid": A, "field": "tools", "value": False})
print("   A 关工具:", st, "| A:", bot.qbot_session.flag(A, "tools"),
      "| B:", bot.qbot_session.flag(B, "tools"))
st, d = call("POST", "/api/session_control", {"gid": A, "field": "comfyui", "value": False})
print("   A 关 comfyui:", st, "| A:", bot.engine_on(A, "comfyui"),
      "| B:", bot.engine_on(B, "comfyui"), "| A moss:", bot.engine_on(A, "moss"))
st, d = call("POST", "/api/session_control", {"gid": A, "field": "persona", "value": "小鲸"})
print("   A 改人格:", st, "| A 与 B 不同:", bot.system_prompt(A) != bot.system_prompt(B),
      "| B 仍跟全局:", bot.system_prompt(B) == bot.system_prompt())
st, d = call("POST", "/api/session_control", {"gid": A, "field": "ai", "value": False})
print("   A 静音:", st, "| A 响应:", bot.qbot_session.flag(A, "ai"),
      "| B 响应:", bot.qbot_session.flag(B, "ai"),
      "| 私聊总开关没被动:", bot.CFG["private"].get("enabled"))
st, d = call("POST", "/api/session_control", {"gid": A, "field": "sim", "value": False})
print("   私聊没有插话，应报错:", st, d)
# 还原（写回默认值 = 配置里不留痕迹）
for _p in ({"gid": G, "field": "tools", "value": True},
           {"gid": G, "field": "sim", "value": True},
           {"gid": G, "field": "persona", "value": ""},
           {"gid": A, "field": "tools", "value": True},
           {"gid": A, "field": "comfyui", "value": True},
           {"gid": A, "field": "persona", "value": ""},
           {"gid": A, "field": "ai", "value": True}):
    call("POST", "/api/session_control", _p)
print("  还原后 session 段:", json.dumps(bot.CFG.get("session"), ensure_ascii=False)[:200])

# ---------------- MCP server over stdio ----------------
print("== 5 qq-mcp-server.py stdio MCP ==")
env = dict(os.environ)
env["QQBOT_API"] = BASE
env["QQBOT_TOKEN"] = TOK
p = subprocess.Popen([sys.executable, "qq-mcp-server.py"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     text=True, encoding="utf-8", env=env)


def rpc(i, method, params=None):
    m = {"jsonrpc": "2.0", "method": method}
    if i is not None:
        m["id"] = i
    if params is not None:
        m["params"] = params
    p.stdin.write(json.dumps(m, ensure_ascii=False) + "\n")
    p.stdin.flush()
    if i is None:
        return None
    line = p.stdout.readline()
    if not line.strip():
        print("  !! 子进程无输出 rc=%s stderr=%s" % (p.poll(), p.stderr.read()[:600]))
        return None
    return json.loads(line)


r = rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "test", "version": "1"}})
print("  initialize:", json.dumps(r.get("result", {}), ensure_ascii=False)[:200])
rpc(None, "notifications/initialized", {})
r = rpc(2, "tools/list")
tools = [t["name"] for t in r["result"]["tools"]]
print("  tools:", tools)
r = rpc(3, "tools/call", {"name": "qq_history", "arguments": {"group_id": G, "limit": 5}})
txt = r["result"]["content"][0]["text"]
print("  qq_history ->", txt[:170].replace("\n", " "), "| isError", r["result"].get("isError"))
SENT.clear()
r = rpc(4, "tools/call", {"name": "qq_send", "arguments": {"group_id": G, "text": "MCP 发的一条"}})
print("  qq_send ->", r["result"]["content"][0]["text"][:120], "| 实发:", SENT)
r = rpc(5, "tools/call", {"name": "qq_status", "arguments": {}})
print("  qq_status ->", r["result"]["content"][0]["text"][:200])
r = rpc(6, "tools/call", {"name": "qq_slang_propose",
                          "arguments": {"term": "辅助", "meaning": "队里保人的位置"}})
print("  propose ->", r["result"]["content"][0]["text"][:150])
p.stdin.close()
try:
    p.wait(timeout=5)
except Exception:
    p.kill()

srv.shutdown()

print("== 6 agent 环境/命令行拼装 ==")
print("  agent_enabled:", bot.agent_enabled(), "selected:", bot.agent_selected())
e = bot.agent_env()
print("  清掉的 SDK 变量还在吗:", [k for k in bot.AGENT_ENV_SCRUB if k in e])
print("  QQBOT_API/TOKEN:", e.get("QQBOT_API"), (e.get("QQBOT_TOKEN") or "")[:6] + "...")
print("  mcp file:", bot.agent_mcp_file(),
      json.dumps(json.load(open("state/agent-mcp.json", encoding="utf-8")), ensure_ascii=False)[:150])
print("  system 长度:", len(bot.agent_system_text(G, "阿坤")))
print(bot.agent_system_text(G, "阿坤")[:520])

print("\nsave_config 已全程桩掉，生产 config.json / state/slang.json 零接触")
