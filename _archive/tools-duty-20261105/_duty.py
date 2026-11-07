# -*- coding: utf-8 -*-
"""值班批量轮询：连跑 N 轮 wait_messages，有消息就写回游标并打印。"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
POLL = os.path.join(HERE, "state", "wb_poll.json")

cur = 340
try:
    cur = json.load(open(POLL, encoding="utf-8")).get("last_mid", 340)
except Exception:
    pass

rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
for i in range(rounds):
    r = subprocess.run(
        [PY, os.path.join(HERE, "_wbqq.py"), "wait", "123450001", str(cur), "45"],
        capture_output=True, text=True, encoding="utf-8")
    try:
        data = json.loads(r.stdout or "{}")
    except Exception:
        print("WAIT_ERR", (r.stdout or r.stderr or "")[:200])
        continue
    msgs = data.get("messages") or []
    if not msgs:
        print("-- r%d empty" % (i + 1))
        continue
    for m in msgs:
        print(json.dumps(m, ensure_ascii=False))
    try:
        cur = max(cur, max(int(m.get("id", 0)) for m in msgs))
    except Exception:
        pass
    try:
        json.dump({"last_mid": cur}, open(POLL, "w", encoding="utf-8"))
    except Exception as e:
        print("POLL_WRITE_FAIL", e)
print("CURSOR", cur)
