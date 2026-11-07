# -*- coding: utf-8 -*-
"""重打 Z-Image 风景工作流的二采修复。

背景：主人 12:57 在 ComfyUI 里 Ctrl+S，用的是界面内存里的**旧参数**，
把 11:14 那次的修复（start_at_step=4 / bislerp）覆盖回去了。
⚠️ 这个文件改完，**必须让主人在 ComfyUI 里重新打开一次工作流**，
否则他界面里还是旧值，下次保存又会盖掉。

⚠️ ComfyUI 存的工作流每个控件值存两份：
     widgets_values（按顺序的数组）+ widgets_values_named（按名字的字典）。
   bot 的 comfy_gen._wf_base_inputs 优先用 **named**，ComfyUI 自己加载也以它为准。
   只改数组 = 白改，所以两处必须一起写。

备份写到工作流目录**外面**：留在 workflows/ 里会被 ComfyUI 当成一份工作流列出来
（主人已经把上一个 .bak 删了）。
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

WF = Path(os.environ.get("QBOT_WF_ZIMAGE") or "Z-Image 风景.json")
BAK_DIR = Path(os.environ.get("QBOT_WF_BACKUP_DIR") or "state/_wf_backups")

BAK_DIR.mkdir(parents=True, exist_ok=True)
bak = BAK_DIR / ("%s.bak-%s" % (WF.name, datetime.now().strftime("%Y%m%d-%H%M%S")))
shutil.copy2(WF, bak)
print("备份 ->", bak)

d = json.loads(WF.read_text(encoding="utf-8"))
nodes = {n["id"]: n for n in d["nodes"]}
changed = []


def set_widget(nid, idx, name, new):
    n = nodes[nid]
    w = n.get("widgets_values")
    if isinstance(w, list) and idx < len(w):
        old = w[idx]
        if old != new:
            w[idx] = new
            changed.append("#%s widgets_values[%d] %s: %r -> %r" % (nid, idx, name, old, new))
    nm = n.get("widgets_values_named")
    if isinstance(nm, dict) and name in nm:
        old = nm[name]
        if old != new:
            nm[name] = new
            changed.append("#%s named.%-16s: %r -> %r" % (nid, name, old, new))
    elif isinstance(nm, dict):
        changed.append("!! #%s named 里没有 %s" % (nid, name))


n86 = nodes[86]
assert n86["widgets_values"][3] == 9, "二采 steps 变了，先确认: %s" % (n86["widgets_values"],)
set_widget(86, 7, "start_at_step", 4)          # 跑 5 步，denoise≈0.56
set_widget(177, 0, "upscale_method", "bislerp")

WF.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

print("\n改了 %d 处：%s" % (len(changed), "（本来就对，无需改）" if not changed else ""))
for c in changed:
    print("  -", c)

print("\n回读校验：")
d2 = json.loads(WF.read_text(encoding="utf-8"))
n2 = {n["id"]: n for n in d2["nodes"]}
print("  #86  二采:", n2[86]["widgets_values"][:4], "... start =",
      n2[86]["widgets_values"][7],
      "| named =", (n2[86]["widgets_values_named"] or {}).get("start_at_step"))
print("  #177 放大:", n2[177]["widgets_values"],
      "| named =", (n2[177]["widgets_values_named"] or {}).get("upscale_method"))
print("  节点数 %d" % len(d2["nodes"]))
