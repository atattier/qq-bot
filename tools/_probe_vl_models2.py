# -*- coding: utf-8 -*-
"""逐个测本机 ollama 视觉模型对「触发云端审核的那张敏感图」的反推表现。
目的：挑本地档。**2026-09-18 用它定了结论：本地档别选 Qwen3 系（思考型）**——
`qwen3-vl-abliterated:4b` 思考段吃光预算 → 43.2s 失败；`qwen2.5-vl-abliterated:7b`
12.7s 成功、`:3b` 9.6s 成功。结果留档在同目录 `_probe_vl_models2.out.txt`。

用法（在项目根跑）：python tools/_probe_vl_models2.py
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)          # 这些脚本假定 cwd=项目根（import bot、state/ 相对路径）
import json, time, copy

import reverser  # noqa: E402

_HERE = _os.path.dirname(_os.path.abspath(__file__))
IMG = r"state\reverse\1789728605_f6eec7.png"
BASE = json.load(open("config.json", encoding="utf-8"))

MODELS = [
    "huihui_ai/qwen3-vl-abliterated:4b",      # 现在配置里的（思考型，实测挂）
    "huihui_ai/qwen2.5-vl-abliterated:7b",    # 备选（非思考型）
    "huihui_ai/qwen2.5-vl-abliterated:3b",    # 备选（非思考型，小）
]

out = []
for m in MODELS:
    cfg = copy.deepcopy(BASE)
    cfg.setdefault("reverse", {})["vl_local_model"] = m
    cfg["reverse"]["vl_local_first"] = True
    # 只试本地这一档：把云端的 key 清掉，避免混入云端结果
    provs = (cfg.get("ai") or {}).get("providers") or {}
    for pname in list(provs.keys()):
        if str(pname) != "ollama":
            provs[pname]["key"] = ""
    reverser._vl_local_fail[0], reverser._vl_local_fail[1] = 0, 0.0
    t0 = time.time()
    try:
        g = reverser._vl_text(IMG, cfg)
        if isinstance(g, tuple):
            text, used = g[0], g[1]
        else:
            text, used = g, "?"
        dt = time.time() - t0
        out.append("[OK ] %-42s %.1fs  %s" % (m, dt, used))
        out.append("      %s" % (text or "").replace("\n", " ")[:300])
    except Exception as e:
        dt = time.time() - t0
        out.append("[FAIL] %-42s %.1fs  %s" % (m, dt, str(e)[:220]))
    out.append("")

txt = "\n".join(out)
open(_os.path.join(_HERE, "_probe_vl_models2.out.txt"), "w", encoding="utf-8").write(txt)
print(txt)
