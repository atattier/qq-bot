# -*- coding: utf-8 -*-
"""值班巡逻一轮：阻塞等新消息，返回并推进 wb_poll.json 游标。"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import json, sys, urllib.request

BASE = "http://127.0.0.1:8088"
TOK = json.load(open("config.json", encoding="utf-8")).get("bridge_token", "")
POLL = r"state\wb_poll.json"

cur = 0
try:
    cur = int(json.load(open(POLL, encoding="utf-8")).get("last_mid", 0))
except Exception:
    pass

after = int(sys.argv[1]) if len(sys.argv) > 1 else cur
tmo = int(sys.argv[2]) if len(sys.argv) > 2 else 50

url = "%s/api/wait_messages?after=%d&timeout_sec=%d" % (BASE, after, tmo)
req = urllib.request.Request(url, headers={"X-Bridge-Token": TOK})
try:
    d = json.loads(urllib.request.urlopen(req, timeout=tmo + 15).read().decode("utf-8"))
except Exception as e:
    print(json.dumps({"error": str(e)}, ensure_ascii=False))
    sys.exit(0)

msgs = d.get("messages") or []
last = d.get("last_id", after)
if last and last > cur:
    json.dump({"last_mid": last, "note": "live patrol"}, open(POLL, "w", encoding="utf-8"))
print(json.dumps({"n": len(msgs), "last": last, "msgs": msgs}, ensure_ascii=False))
