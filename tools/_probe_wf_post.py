# -*- coding: utf-8 -*-
"""通用「工作流档真 POST」探针 —— 换工作流**之前**先验，不用先改 config.json。

用法：
    python tools/_probe_wf_post.py --wf "Krea2 3DCG UE5 黑神话质感.json" [--style krea2] [--image X.png] [--denoise 0.8]
    python tools/_probe_wf_post.py --wf "X.json" --json      # 只看转换后的图，不 POST

做的事：拿 config.json 里指定档位（默认 krea2）的参数字典，只把 `workflow` 换成 --wf，
走 comfy_gen.graph_workflow 拼出真正要提交的 API 图 → 打印节点清单 → 直接 POST 127.0.0.1:8188/prompt。
**必须看到 HTTP 200 且 node_errors 为空**才叫通过；400 = bot 会静默降级成 anima。
加 --wait 会继续轮询 /history/<pid> 确认真跑完（出图很慢，默认不轮询）。
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
import json, sys, time, urllib.request, urllib.error

sys.stdout.reconfigure(encoding="utf-8")
import comfy_gen  # noqa: E402

CFG = json.load(open("config.json", encoding="utf-8"))["comfyui"]

av = sys.argv[1:]
def opt(name, default=None):
    return av[av.index(name) + 1] if name in av else default

WF = opt("--wf")
if not WF:
    print("缺 --wf <工作流文件名>"); sys.exit(2)
STYLE_NAME = opt("--style", "krea2")
IMG = opt("--image")
DNZ = float(opt("--denoise")) if opt("--denoise") else None
PROMPT = opt("--prompt", "a young woman standing by the window, soft morning light, realistic skin texture")
SEED = int(opt("--seed", "123456789"))

S = dict((CFG.get("styles") or {}).get(STYLE_NAME) or {})
S["engine"] = "wf"
S["workflow"] = WF

# 参考图必须先按 bot 的真实做法传到 ComfyUI 的 input/ 下（/prompt 只认相对名，
# 直接塞绝对路径会被 LoadImage 的 VALIDATE_INPUTS 判 invalid image file —— 2026-09-21 踩过）
if IMG:
    IMG = comfy_gen.upload_image(CFG, IMG, subfolder="qqbot_probe")
    print("参考图已上传 ->", IMG)
print("档位 %s（workflow 已覆盖为 %s）：" % (STYLE_NAME, WF))
print("  ", json.dumps(S, ensure_ascii=False)[:500])
if DNZ is None:
    DNZ = (S.get("i2i_denoise") or 0) / 100.0 or None
print("mode:", ("i2i 参考图=" + IMG) if IMG else "txt2img", " denoise=", DNZ, " seed=", SEED)

g, wh = comfy_gen.graph_workflow(CFG, S, PROMPT, "", 0, 0, SEED, "qqbot_probe",
                                 image=IMG, denoise=DNZ)
print("\n最终 API 图节点数:", len(g), " 出图宽高:", wh)
print("节点清单:")
for k in sorted(g, key=lambda x: int(x) if str(x).isdigit() else 0):
    n = g[k]
    inp = {kk: (vv if not isinstance(vv, list) else ["<%s>" % vv[0], vv[1]])
           for kk, vv in n["inputs"].items()}
    mark = "  <<< LoRA" if "Lora" in n["class_type"] else ""
    print("  #%-4s %-30s %s%s" % (k, n["class_type"], json.dumps(inp, ensure_ascii=False)[:230], mark))

# 关键节点抽查
print("\n关键节点抽查:")
for k in sorted(g, key=lambda x: int(x) if str(x).isdigit() else 0):
    ct = g[k]["class_type"]
    if any(s in ct for s in ("Lora", "Save", "Resolution", "KSampler")):
        print("  #%-4s %-30s %s" % (k, ct, json.dumps(g[k]["inputs"], ensure_ascii=False)[:400]))

if "--json" in av:
    sys.exit(0)

print("\n--- POST http://127.0.0.1:8188/prompt ---")
body = json.dumps({"prompt": g, "client_id": "qqbot_probe"}).encode("utf-8")
req = urllib.request.Request("http://127.0.0.1:8188/prompt", data=body,
                             headers={"Content-Type": "application/json"})
pid = None
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.load(r)
    pid = res.get("prompt_id")
    ne = res.get("node_errors")
    print("HTTP 200 OK  prompt_id =", pid)
    print("node_errors =", json.dumps(ne, ensure_ascii=False)[:800] if ne else "(空)  ✅ 提交通过")
except urllib.error.HTTPError as e:
    raw = e.read().decode("utf-8", "replace")
    print("HTTP", e.code, " ❌")
    try:
        j = json.loads(raw)
        print("error:", json.dumps(j.get("error"), ensure_ascii=False)[:600])
        print("node_errors:", json.dumps(j.get("node_errors"), ensure_ascii=False)[:2000])
    except Exception:
        print(raw[:1500])
    sys.exit(1)
except Exception as e:
    print("POST ERR:", type(e).__name__, e); sys.exit(1)

if "--wait" in av and pid:
    print("\n--- 等 /history/%s（最长 600s） ---" % pid)
    t0 = time.time()
    while time.time() - t0 < 600:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8188/history/%s" % pid, timeout=15) as r:
                h = json.load(r)
        except Exception as e:
            h = {}
        if pid in h:
            st = h[pid].get("status", {})
            print("完成 %.1fs status=%s" % (time.time() - t0, json.dumps(st, ensure_ascii=False)[:300]))
            outs = h[pid].get("outputs", {})
            for nid, o in outs.items():
                print("  输出 #%s -> %s" % (nid, json.dumps(o, ensure_ascii=False)[:400]))
            break
        time.sleep(5)
    else:
        print("超时未完成（可能还在排队/跑）")
