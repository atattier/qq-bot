# -*- coding: utf-8 -*-
"""`/notify` 鉴权与群白名单回归（SAFE，2026-11-04 R3）。

修复前：任何能访问 8088 的进程都能往任意群发消息（无 token、无白名单）。
本测试把 HTTP 层起在本地临时端口，发送函数全部打桩，**不产生真实 QQ 消息**：
  ① 无 token / 错 token → 401，且不发送；
  ② 缺 group / 非法 group / 非正数 → 400，且不发送；
  ③ 非白名单群 → 403，且不发送；
  ④ 正确 token + 白名单群（文本/图片）→ 200 且只发送一次；
  ⑤ token 走 query 参数也认（沿用 _bridge_ok 旧能力）。
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

TOK = "test-bridge-token-r3"
bot.bridge_token = lambda: TOK
bot.save_config = lambda: True
SENT = []
bot.send_group = lambda cid, text, **kw: SENT.append(("text", int(cid), text)) or True
bot.send_image = lambda cid, name, caption="", **kw: SENT.append(("img", int(cid), name)) or True

GID = int((bot.CFG.get("groups") or [123450001])[0])
OTHER = 999999
PORT = 18089
srv = ThreadingHTTPServer(("127.0.0.1", PORT), bot.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % PORT


def call(obj, token=None, query="", path="/notify"):
    req = urllib.request.Request(BASE + path + query, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Bridge-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


print("== ① 鉴权 ==")
SENT.clear()
st, body = call({"group": GID, "text": "无 token"})
chk("无 token → 401", st == 401, (st, body))
chk("无 token 未发送", SENT == [], SENT)
st, body = call({"group": GID, "text": "错 token"}, token="wrong")
chk("错 token → 401", st == 401, (st, body))
chk("错 token 未发送", SENT == [], SENT)

print("== ② gid 校验 ==")
for label, obj in [("缺 group", {"text": "x"}),
                   ("字符串 group", {"group": "abc", "text": "x"}),
                   ("零", {"group": 0, "text": "x"}),
                   ("负数", {"group": -123, "text": "x"}),
                   ("浮点 group", {"group": 123.9, "text": "x"}),
                   ("bool group", {"group": True, "text": "x"}),
                   ("null group", {"group": None, "text": "x"})]:
    SENT.clear()
    st, body = call(obj, token=TOK)
    chk("%s → 400" % label, st == 400, (st, body))
    chk("%s 未发送" % label, SENT == [], SENT)

print("== ③ 白名单 ==")
SENT.clear()
st, body = call({"group": OTHER, "text": "不该发"}, token=TOK)
chk("非白名单群 → 403", st == 403, (st, body))
chk("非白名单群未发送", SENT == [], SENT)

print("== ④ 正常路径 ==")
SENT.clear()
st, body = call({"group": GID, "text": "白名单群文本"}, token=TOK)
chk("正确 token + 白名单文本 → 200", st == 200 and json.loads(body).get("ok") is True, (st, body))
chk("只发送一次且内容正确", SENT == [("text", GID, "白名单群文本")], SENT)
SENT.clear()
st, body = call({"group": str(GID), "text": "数字字符串群号"}, token=TOK)
chk("数字字符串 group → 200", st == 200 and json.loads(body).get("ok") is True, (st, body))
chk("数字字符串只发送一次", SENT == [("text", GID, "数字字符串群号")], SENT)
SENT.clear()
st, body = call({"group": GID, "image": "cat.png", "text": "配图"}, token=TOK)
chk("图片路径 → 200", st == 200, (st, body))
chk("只发送一次图片", SENT == [("img", GID, "cat.png")], SENT)
SENT.clear()
st, body = call({"group": GID}, token=TOK)
chk("白名单但无内容 → 400 且不发送", st == 400 and SENT == [], (st, body, SENT))

print("== ⑤ 路径精确匹配 ==")
SENT.clear()
st, body = call({"group": GID, "text": "不该进 notify"}, token=TOK, path="/notifyXYZ")
chk("/notifyXYZ 不进入 notify 发送逻辑", st != 200 and SENT == [], (st, body, SENT))

print("== ⑥ query token 兼容 ==")
SENT.clear()
st, body = call({"group": GID, "text": "query token"}, query="?token=" + TOK)
chk("query token → 200", st == 200, (st, body))
chk("query token 发送一次", len(SENT) == 1, SENT)

srv.shutdown()
print("\n== 汇总 ==")
print("失败 %d 条" % len(FAIL))
sys.exit(1 if FAIL else 0)
