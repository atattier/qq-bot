# -*- coding: utf-8 -*-
"""视频工作流「真 POST」探针 —— 验证 视频生成.json 能不能进 bot。

用法：python tools/_probe_video_post.py [--keep]
默认：POST /prompt 通过后立刻 POST /interrupt（避免真跑几十分钟把机器拖死）。
--keep 则不中断，轮询 /history 直到完成（慎用：本机跑 30s H3 视频可能 1 小时+）。
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
import json, sys, time, urllib.request, urllib.error

sys.stdout.reconfigure(encoding="utf-8")
import comfy_gen

BASE = "http://127.0.0.1:8188"
CFG = json.load(open("config.json", encoding="utf-8"))["comfyui"]

def req(path, payload=None, timeout=30):
    r = urllib.request.Request(BASE + path,
                               data=json.dumps(payload).encode() if payload is not None else None,
                               headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(r, timeout=timeout)

doc = comfy_gen.wf_load(CFG, "视频生成.json")[1]
g, meta = comfy_gen.wf_to_api(doc)
print("转换后节点:", len(g), "| kind:", meta["kind"], "| 输出:", meta["output"], "| 全部视频输出:", meta["video_outs"])
print("Prompt 节点 #346 前 60 字:", str(g.get("346", {}).get("inputs", {}).get("text", ""))[:60])

try:
    with req("/prompt", {"prompt": g, "client_id": "qqbot_video_probe"}) as r:
        body = json.loads(r.read())
    print("POST /prompt ->", r.status, "| prompt_id =", body.get("prompt_id"))
    if body.get("node_errors"):
        print("node_errors:", json.dumps(body["node_errors"], ensure_ascii=False)[:600])
    pid = body.get("prompt_id")
    if not pid or body.get("node_errors"):
        sys.exit(1)
except urllib.error.HTTPError as e:
    print("POST /prompt -> HTTP", e.code)
    print(json.dumps(json.loads(e.read()), ensure_ascii=False)[:800])
    sys.exit(1)

if "--keep" not in sys.argv:
    time.sleep(2)   # 给验证一点时间
    with req("/interrupt", {}) as r:
        print("已发 /interrupt（防真跑）")
    time.sleep(2)
    try:
        with req("/history/" + pid) as r:
            h = json.loads(r.read())
        st = h.get(pid, {}).get("status", {})
        print("history status:", json.dumps(st.get("status_str") or st, ensure_ascii=False)[:200])
    except Exception as e:
        print("history 读取:", e)
else:
    print("（--keep：轮询模式，按 Ctrl+C 停）")
    while True:
        time.sleep(20)
        with req("/history/" + pid) as r:
            h = json.loads(r.read())
        st = h.get(pid, {}).get("status", {})
        print(time.strftime("%H:%M:%S"), st.get("status_str"), "| outputs:", list((h.get(pid, {}).get("outputs") or {}).keys()))
        if st.get("completed"):
            outs = h.get(pid, {}).get("outputs") or {}
            for k, v in outs.items():
                for im in v.get("gifs", []) or v.get("images", []):
                    print("  产物:", im)
            break
