# -*- coding: utf-8 -*-
"""把 ComfyUI prompt-assistant 里已经填好的各家 API key 同步进 qq-bot 的 config.json，
省得在两个地方各填一遍。改完重启 bot.py 生效。"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PA = Path(os.environ.get("QBOT_COMFY_PA_CONFIG") or "")
BOT = Path(_ROOT) / "config.json"
if not PA.is_file():
    raise SystemExit("请用 QQBOT_COMFY_PA_CONFIG 指向 ComfyUI prompt-assistant 的 config.json")

srv = {s["id"]: s for s in json.load(open(PA, encoding="utf-8")).get("model_services", [])}


def key_of(sid):
    s = srv.get(sid) or {}
    return s.get("api_key") or s.get("key") or ""


def base_of(sid):
    return (srv.get(sid) or {}).get("base_url") or ""


cfg = json.loads(BOT.read_text(encoding="utf-8"))
ai = cfg.setdefault("ai", {})
provs = ai.setdefault("providers", {})

provs["zhipu"] = {
    "type": "openai", "url": base_of("zhipu") or "https://open.bigmodel.cn/api/paas/v4",
    "key": key_of("zhipu"), "model": "glm-4.5-flash",
    "temperature": 0.9, "max_tokens": 700, "timeout": 60, "retries": 1,
    "extra": {"thinking": {"type": "disabled"}},
    "_说明": "免费，会思考但已关思考输出，质量比 4-flash 好",
}
provs["zhipu_fast"] = {
    "type": "openai", "url": base_of("zhipu") or "https://open.bigmodel.cn/api/paas/v4",
    "key": key_of("zhipu"), "model": "glm-4-flash-250414",
    "temperature": 0.9, "max_tokens": 500, "timeout": 30, "retries": 1,
    "_说明": "免费且最快(实测<1s)，话少",
}
provs["deepseek"] = {
    "type": "openai", "url": base_of("deepseek") or "https://api.deepseek.com/v1",
    "key": key_of("deepseek"), "model": "deepseek-flash",
    "temperature": 0.9, "max_tokens": 700, "timeout": 60, "retries": 1,
    "_说明": "官方 API 按量计费(很便宜)，中文闲聊最自然",
}
provs["modelscope"] = {
    "type": "openai", "url": base_of("modelscope") or "https://api-inference.modelscope.cn/v1",
    "key": key_of("modelscope"), "model": "Qwen/Qwen3-235B-A22B",
    "temperature": 0.9, "max_tokens": 700, "timeout": 90, "retries": 1,
    "_说明": "魔搭免费每天 2000 次，Qwen3-235B，稍慢",
}
provs["ollama"] = {
    "type": "ollama", "url": "http://127.0.0.1:11434",
    "model": "huihui_ai/qwen3-vl-abliterated:4b", "timeout": 180,
    "_说明": "本地无审查，云端拒答或断网时当备用",
}

ai["provider"] = "zhipu"
ai["fallback"] = "ollama"
for dead in ("ollama_url", "model", "timeout"):
    ai.pop(dead, None)

BOT.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print("已写入 providers:", "、".join(provs))
print("主用:", ai["provider"], " 备用:", ai["fallback"])
for k, v in provs.items():
    print("  %-12s model=%-28s key=%s" % (k, v.get("model"), (v.get("key") or "(无)")[:6] + "..."))
