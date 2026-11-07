# -*- coding: utf-8 -*-
"""衍生写实.json 体检：走 comfy_gen.graph_workflow 拼 API 图 -> 直接 POST /prompt。
看 400 + node_errors 才算数（bot 跑通不算，它会静默降级成 anima）。"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
import json, sys, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
import comfy_gen  # noqa: E402

CFG = json.load(open("config.json", encoding="utf-8"))["comfyui"]
S = dict((CFG.get("styles") or {}).get("krea2") or {})
print("krea2 档位:", json.dumps(S, ensure_ascii=False)[:400])

NAME = S.get("workflow") or "衍生写实.json"
PROMPT = "a young woman standing by the window, soft morning light, realistic skin texture"

IMG = None
DNZ = None
SEED = 123456789
_av = sys.argv[1:]
for _i, _a in enumerate(_av):
    if _a == "--image" and _i + 1 < len(_av):
        IMG = _av[_i + 1]
    elif _a == "--denoise" and _i + 1 < len(_av):
        DNZ = float(_av[_i + 1])
    elif _a == "--prompt" and _i + 1 < len(_av):
        PROMPT = _av[_i + 1]
    elif _a == "--seed" and _i + 1 < len(_av):
        SEED = int(_av[_i + 1])
if DNZ is None:
    DNZ = (S.get("i2i_denoise") or 0) / 100.0 or None
print("mode:", "i2i 参考图=" + str(IMG) if IMG else "txt2img", " denoise=", DNZ, " seed=", SEED)

g, wh = comfy_gen.graph_workflow(CFG, S, PROMPT, "", 0, 0, SEED,
                                 sys.argv[sys.argv.index("--tag") + 1] if "--tag" in sys.argv else "qqbot_probe",
                                 image=IMG, denoise=DNZ)
print("最终 API 图节点数:", len(g), " 出图宽高:", wh)
print("节点清单:")
for k in sorted(g, key=lambda x: int(x) if str(x).isdigit() else 0):
    n = g[k]
    inp = {kk: (vv if not isinstance(vv, list) else ["<%s>" % vv[0], vv[1]])
           for kk, vv in n["inputs"].items()}
    ct = n["class_type"]
    mark = "  <<<" if ("Lora" in ct) else ""
    print("  #%-4s %-28s %s%s" % (k, ct, json.dumps(inp, ensure_ascii=False)[:200], mark))

print("\n--- POST /prompt ---")
body = json.dumps({"prompt": g, "client_id": "qqbot_probe"}).encode("utf-8")
req = urllib.request.Request(
    "http://127.0.0.1:8188/prompt", data=body,
    headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.load(r)
    print("HTTP 200 OK  prompt_id =", res.get("prompt_id"))
    print("node_errors =", res.get("node_errors"))
except urllib.error.HTTPError as e:
    raw = e.read().decode("utf-8", "replace")
    print("HTTP", e.code)
    try:
        j = json.loads(raw)
        print("error:", json.dumps(j.get("error"), ensure_ascii=False)[:600])
        print("node_errors:", json.dumps(j.get("node_errors"), ensure_ascii=False)[:1200])
    except Exception:
        print(raw[:1500])
except Exception as e:
    print("POST ERR:", type(e).__name__, e)
