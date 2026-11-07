# -*- coding: utf-8 -*-
"""工作流档「转换体检」：走一遍 comfy_gen.graph_workflow，把最终要提交给 ComfyUI 的
API 图打出来。**改完 ComfyUI 工作流文件（或档位注入点）之后必须先跑这个。**

为什么非跑不可（2026-09-18 血的教训）
    1. ComfyUI 存的工作流里，每个控件值存**两份**：`widgets_values`（按顺序的数组）和
       `widgets_values_named`（按名字的字典）。`comfy_gen._wf_base_inputs` 优先用 named
       （comfy_gen.py:1619），只改数组 = 白改，而且看文件看不出来，必须看**转换后的图**。
    2. 顺便能验证：文本有没有真的注入到 CLIPTextEncode（而不是还连着上游拼接节点）、
       尺寸预设落到哪、放大倍数、种子注入点指向哪个采样器、上游有没有被正确裁掉
       （PromptExpand 这类会调 LLM 的节点必须被裁掉，否则白等几十秒）。

用法：改下面的 NAME / STYLE 即可。
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
import comfy_gen    # noqa: E402

CFG = json.load(open("config.json", encoding="utf-8"))["comfyui"]

NAME = "Z-Image 风景.json"                     # 要体检的工作流文件名
S = {"engine": "wf", "workflow": NAME,           # 想连档位配置一起验就照抄那档的键
     "skip_upscale": True}
PROMPT = "a misty mountain lake at dawn, layered peaks, volumetric light"

g, wh = comfy_gen.graph_workflow(CFG, S, PROMPT, "", 0, 0, 123456789, "qqbot_probe")
print("最终 API 图节点数:", len(g))
print("出图宽高:", wh)
print("种子注入检查（#251 一采 / #86 二采 应指向同一个节点，该节点的值=123456789）:")
for k in ("251", "86", "256"):
    if k in g:
        print("  #%-4s %-22s %s" % (k, g[k]["class_type"],
                                    json.dumps(g[k]["inputs"], ensure_ascii=False)[:130]))
print("\n节点清单:")
for k in sorted(g, key=lambda x: int(x) if str(x).isdigit() else 0):
    n = g[k]
    inp = {kk: (vv if not isinstance(vv, list) else ["<%s>" % vv[0], vv[1]])
           for kk, vv in n["inputs"].items()}
    print("  #%-4s %-26s %s" % (k, n["class_type"], json.dumps(inp, ensure_ascii=False)[:190]))
