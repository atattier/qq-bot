# -*- coding: utf-8 -*-
"""值班大脑薄壳：转调 bot.py 本地 /api/*（与 qq-mcp-server.py 同协议）。
用法：
  python _wbqq.py status
  python _wbqq.py wait <group> <after> <timeout>
  python _wbqq.py history <group> <limit> [after]
  python _wbqq.py send <group> <text> [image] [reply_to]
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = "http://127.0.0.1:8088"


def token():
    for f in ("state/bridge-token.json", "state/bridge_token.json"):
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            try:
                t = json.load(open(p, encoding="utf-8")).get("token", "")
                if t:
                    return t
            except Exception:
                pass
    try:
        return json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8")).get("bridge_token", "")
    except Exception:
        return ""


def call(method, params=None, timeout=45):
    url = API + method
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"X-Bridge-Token": token() or ""})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "HTTP %s" % e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def post(method, payload, timeout=45):
    req = urllib.request.Request(
        API + method, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Bridge-Token": token() or "",
                 "X-QQ-Source": "agent"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "HTTP %s" % e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "status"
    if cmd == "status":
        print(json.dumps(call("/api/status"), ensure_ascii=False))
    elif cmd == "wait":
        t = min(max(int(a[3]), 10), 55)
        print(json.dumps(call("/api/wait_messages", {"group": int(a[1]), "after": int(a[2]),
                                                     "timeout": t}, timeout=t + 10), ensure_ascii=False))
    elif cmd == "history":
        after = int(a[3]) if len(a) > 3 else 0
        print(json.dumps(call("/api/history", {"group": int(a[1]), "limit": int(a[2]),
                                               "after": after}), ensure_ascii=False))
    elif cmd == "send":
        payload = {"group": int(a[1]), "text": a[2]}
        if len(a) > 3 and a[3]:
            payload["image"] = a[3]
        if len(a) > 4 and a[4]:
            payload["reply_to"] = int(a[4])
        print(json.dumps(post("/api/send", payload), ensure_ascii=False))
    else:
        print(json.dumps({"ok": False, "error": "unknown cmd"}))


if __name__ == "__main__":
    main()
