# -*- coding: utf-8 -*-
"""现场反推探针：拿真实失败图跑 bot 自己的反推链，打印接力顺序与每档耗时/结果。

用法（在项目根跑）：
  python tools/_probe_rev_live.py [图片路径]
不传则默认用 2026-09-18 18:50:06 那单失败图（触发云端审核的敏感图）。
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)          # 这些脚本假定 cwd=项目根（import bot、state/ 相对路径）
import json, time, traceback

import reverser  # noqa: E402

_HERE = _os.path.dirname(_os.path.abspath(__file__))
IMG = _sys.argv[1] if len(_sys.argv) > 1 else r"state\reverse\1789728605_f6eec7.png"
CFG = json.load(open("config.json", encoding="utf-8"))

out = []
out.append("图: %s" % IMG)
out.append("存在: %s  大小: %s B" % (_os.path.exists(IMG), _os.path.getsize(IMG) if _os.path.exists(IMG) else -1))
r = CFG.get("reverse") or {}
out.append("reverse 配置: %s" % json.dumps(r, ensure_ascii=False))
out.append("vl_local_model = %r" % reverser.vl_local_model(CFG))
out.append("vl_local_first = %r" % reverser.vl_local_first(CFG))
out.append("熔断允许尝试(ok_to_try) = %r" % reverser.vl_local_ok_to_try())
out.append("熔断计数 [连续失败, 静默到] = %r  (now=%s)" % (reverser._vl_local_fail, time.time()))

# 打印本次会用的接力链
try:
    provs = (CFG.get("ai") or {}).get("providers") or {}
    prov = (CFG.get("ai") or {}).get("provider") or "zhipu"
    model = (provs.get(prov) or {}).get("model") or ""
    _fn = getattr(reverser, "_candidates", None)
    chain = _fn(provs, prov, (CFG.get("reverse") or {}).get("vl_model") or model, CFG) if _fn else []
    out.append("接力链: %s" % " -> ".join("%s/%s" % (p, m) for p, m in chain))
except Exception as e:
    out.append("接力链取不到: %s" % e)

out.append("-" * 60)
t0 = time.time()
try:
    ok, text, used = reverser.run(IMG, CFG, "vl")
    dt = time.time() - t0
    out.append("ok=%s  用时=%.1fs  实际引擎=%s" % (ok, dt, used))
    out.append("文本(%d字): %s" % (len(text or ""), (text or "")[:800]))
except Exception as e:
    dt = time.time() - t0
    out.append("抛异常  用时=%.1fs: %s" % (dt, e))
    out.append(traceback.format_exc()[-1500:])

out.append("-" * 60)
out.append("熔断计数(跑完) = %r" % (reverser._vl_local_fail,))

txt = "\n".join(str(x) for x in out)
open(_os.path.join(_HERE, "_probe_rev_live.out.txt"), "w", encoding="utf-8").write(txt)
print(txt)
