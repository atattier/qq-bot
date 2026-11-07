# -*- coding: utf-8 -*-
"""反推工作流「VLM 服务接力」离线测试：DeepSeek 优先、失败自动换 Ollama 补位。

桩掉 comfy_gen（不真连 ComfyUI），模拟三种情形：
  ①深搜成功不补位  ②深搜报错→Ollama 补位成功  ③全部失败→抛 RevError。"""
import os, sys, types
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import reverser

CALLS = []
MODE = {"m": "ds_ok"}

fake = types.ModuleType("comfy_gen")
fake.wf_load = lambda ccfg, name: ("/x/图片反推提示词.json", {"nodes": []})
fake.upload_image = lambda ccfg, p, subfolder=None: "up.png"
fake.base = lambda ccfg: "http://x"

def wf_to_api(doc, texts=None):
    return ({
        "2": {"class_type": "ImageCaptionNode",
              "inputs": {"vlm_service": "Ollama/aha2025/llama-joycaption-beta-one-hf-llava:Q4_K_M"}},
        "4": {"class_type": "ShowText|pysssss", "inputs": {}},
    }, {})

def _req(url, payload, timeout=None):
    if url.endswith("/prompt"):
        svc = payload["prompt"]["2"]["inputs"]["vlm_service"]
        CALLS.append(svc)
        pid = "p_ds" if svc.startswith("DeepSeek") else "p_ol"
        return {"prompt_id": pid}
    if "/history/p_ds" in url:
        if MODE["m"] == "ds_429_text":
            # 真实踩坑：DeepSeek 限流时节点不标 status_str=error，
            # 而是把 429 原文写进 ShowText 当正常结果返回
            return {"p_ds": {"outputs": {"4": {"text": [
                "429 您的使用量已超出频率限制，将在 2026-10-14 23:00:00 UTC+8 重置，您也可以切换其他模型继续使用。"]}}}}
        if MODE["m"] in ("ds_fail", "all_fail"):
            return {"p_ds": {"status": {"status_str": "error",
                    "messages": [["execution_error", {"exception_message": "HTTP 401 boom"}]]}}}
        return {"p_ds": {"outputs": {"4": {"text": ["a young woman standing in a garden with soft light"]}}}}
    if "/history/p_ol" in url:
        if MODE["m"] == "all_fail":
            return {"p_ol": {"status": {"status_str": "error",
                    "messages": [["execution_error", {"exception_message": "boom2"}]]}}}
        return {"p_ol": {"outputs": {"4": {"text": ["a young woman standing in a garden with soft light, cinematic lighting"]}}}}
    return {}

fake.wf_to_api = wf_to_api
fake._req = _req
sys.modules["comfy_gen"] = fake

bad = 0
def chk(name, cond, extra=""):
    global bad
    print(("OK  " if cond else "FAIL ") + name + ("" if cond else " | " + str(extra)))
    if not cond:
        bad += 1

# 0) 服务链默认值
svcs = reverser._rev_services({"reverse": {}})
chk("默认首选 DeepSeek", svcs[0] == "DeepSeek/deepseek-flash", svcs)
chk("默认补位 Ollama", svcs[1].startswith("Ollama/"), svcs)
chk("config 覆盖生效", reverser._rev_services(
    {"reverse": {"wf_vlm_primary": "A/x", "wf_vlm_fallback": "B/y"}}) == ["A/x", "B/y"])

# ① 深搜成功，不应补位
MODE["m"] = "ds_ok"; CALLS.clear()
txt, via = reverser.rev_wf_text({"reverse": {}}, "img.png", timeout=5)
chk("①只调一次", len(CALLS) == 1, CALLS)
chk("①首选 DeepSeek", CALLS and CALLS[0] == "DeepSeek/deepseek-flash", CALLS)
chk("①拿到文本", "young woman" in txt, txt)
chk("①via 标 deepseek", "deepseek-flash" in via, via)

# ② 深搜报错 → Ollama 补位
MODE["m"] = "ds_fail"; CALLS.clear()
txt, via = reverser.rev_wf_text({"reverse": {}}, "img.png", timeout=5)
chk("②调了两次", len(CALLS) == 2, CALLS)
chk("②第二次是 Ollama", len(CALLS) == 2 and CALLS[1].startswith("Ollama/"), CALLS)
chk("②补位拿到文本", "young woman" in txt, txt)
chk("②via 标 ollama", "joycaption" in via, via)

# ③ 全失败 → RevError
MODE["m"] = "all_fail"; CALLS.clear()
try:
    reverser.rev_wf_text({"reverse": {}}, "img.png", timeout=5)
    chk("③全失败抛 RevError", False, "没抛")
except reverser.RevError as e:
    chk("③全失败抛 RevError", True)
    print("    错误信息:", str(e)[:80])
    chk("③已尝试两档", len(CALLS) == 2, CALLS)

# ④ DeepSeek 返 429-as-text（不标 status_str=error）→ 必须识别假成功并落 Ollama
MODE["m"] = "ds_429_text"; CALLS.clear()
txt, via = reverser.rev_wf_text({"reverse": {}}, "img.png", timeout=5)
chk("④调了两次（识别假成功后兜底）", len(CALLS) == 2, CALLS)
chk("④第二次是 Ollama", len(CALLS) == 2 and CALLS[1].startswith("Ollama/"), CALLS)
chk("④拿到的是 Ollama 真串（无 429 文本）", "cinematic lighting" in txt and "429" not in txt, txt)
chk("④via 标 ollama", "joycaption" in via, via)

print()
print("RESULT:", "PASS" if bad == 0 else "FAIL(%d)" % bad)
