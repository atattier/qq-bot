# -*- coding: utf-8 -*-
"""探一下智谱的联网搜索怎么调：A) 独立 web_search 接口  B) chat/completions 里挂 web_search 工具。"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import json
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CFG = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "config.json"), encoding="utf-8"))
P = CFG["ai"]["providers"]["zhipu_fast"]
KEY, BASE = P["key"], P["url"].rstrip("/")
Q = "2026年最火的网络梗 热梗"


def post(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return "OK", json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return "HTTP %s" % e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:
        return "ERR", str(e)[:200]


print("=== A1 /web_search (search_query) ===")
st, body = post("/web_search", {"search_query": Q, "count": 5, "search_engine": "search_std"})
print(st, json.dumps(body, ensure_ascii=False)[:600] if not isinstance(body, str) else body)

print("=== A2 /web_search (query) ===")
st, body = post("/web_search", {"query": Q, "count": 5})
print(st, json.dumps(body, ensure_ascii=False)[:400] if not isinstance(body, str) else body)

print("=== B1 chat + tools[web_search] ===")
st, body = post("/chat/completions", {
    "model": P["model"],
    "messages": [{"role": "user", "content": "2026年流行的网络梗有哪些？不知道就别编"}],
    "tools": [{"type": "web_search",
               "web_search": {"enable": True, "search_engine": "search_std",
                              "count": 5, "content_size": "medium"}}],
    "tool_choice": "web_search",
    "max_tokens": 500,
})
print(st, json.dumps(body, ensure_ascii=False)[:900] if not isinstance(body, str) else body)

print("=== B2 chat + search_model ===")
st, body = post("/chat/completions", {
    "model": P["model"],
    "messages": [{"role": "user", "content": "2026年流行的网络梗有哪些？"}],
    "search": {"enable": True},
    "max_tokens": 500,
})
print(st, json.dumps(body, ensure_ascii=False)[:400] if not isinstance(body, str) else body)
