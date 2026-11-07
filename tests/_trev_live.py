# -*- coding: utf-8 -*-
"""真机反推接力验证：直接打 live ComfyUI。
预期：DeepSeek 当前限流(429-as-text) → 被新逻辑识别为假成功 → 落 Ollama 返真串。"""
import os, sys, json
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from qbot import reverser

cfg = json.load(open("config.json", encoding="utf-8"))
img = os.environ.get("QBOT_REV_TEST_IMG") or r"C:\ComfyUI\ComfyUI\input\qqbot_rev\test.jpg"
assert os.path.exists(img), "测试图不存在"

print("引擎:", cfg.get("reverse", {}).get("engine"))
print("服务链:", reverser._rev_services(cfg))

# 第二轮：强制首选档为一个不存在的模型，逼出错误→验证 Ollama 兜底真能用（live）
cfg2 = json.loads(json.dumps(cfg))
cfg2.setdefault("reverse", {})["wf_vlm_primary"] = "DeepSeek/__force_fail_nonexistent__"
cfg2["reverse"]["wf_vlm_fallback"] = "Ollama/aha2025/llama-joycaption-beta-one-hf-llava:Q4_K_M"
print("\n[强制首选报错] 服务链:", reverser._rev_services(cfg2))
print("开始真机反推（首选报错，应落 Ollama joycaption）...")
try:
    txt, via = reverser.rev_wf_text(cfg2, img, timeout=180)
    print("RESULT_OK via=", via)
    print("文本前 200 字:", txt[:200])
    print("含 429/频率限制?", ("429" in txt) or ("频率限制" in txt))
    print("走的是 Ollama 兜底?", "joycaption" in via)
except reverser.RevError as e:
    print("RESULT_FAIL:", str(e)[:300])
