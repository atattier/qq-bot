# -*- coding: utf-8 -*-
"""本地 ComfyUI 出图客户端（纯标准库，给 qq-bot 用）。

引擎只有两条能用（style 里的 engine）：
  * anima  —— 2026-09-13 起**默认档**：照主人 Styles/comfy/1 的「Anima工作流」复刻，anima_baseV10
              + 双 Anima LoRA(各1.0) + ModelSamplingAuraFlow(shift3) + qwen_3_06b_base 编码器，
              30 步 er_sde/simple cfg5 @1024x1536 直出；质量词前缀与 @画师 由脚本按模板约定拼最前，
              润色走 ENHANCE_SYS_ANIMA（模板 SFW 骨架：槽位顺序/一行小写/禁光线色调标签）。
              原图的 UltimateSDUpscale 分支主人本来就旁路着，没复刻。实测见 config est_sec。
  * krea2  —— 照主人自己的「krea2基础流·二采锐化版」，但默认关掉二采：UNETLoader(krea2_turbo_fp8)
              + CLIPLoader(qwen3vl, type=krea2) + VAELoader(qwen_image_vae)
              + Detailer-KREA2 LoRA → 8 步首采 832x1536 → RTX VSR 1.5x(ULTRA) 放大 → 1248x2304 出图。
              实测 8G 卡约 58 秒/张。style 里写 "refine": true 才接回 VAEEncode + 12 步里从第 6 步起
              的二采精修，那张约 143 秒、细节更实。
  * sd15   —— 已停用：主人明确要求「永远不要用 SD1.5」，engine 白名单只有 krea2 和 anima，其余报错。
              graph() 那 7 个节点的拼图留着只是备用，配置里不再有这种档位。
要"快点出图"用 fast 档＝同一套 Krea2 但不做 RTX 放大，832x1536 直出。
都不碰主人手搓的那些大工作流；节点不依赖除 RTX 节点以外的自定义节点，缺 RTX VSR 时自动只做首采。
润色分两套：krea2 吃自然语言长句（文本编码器是 qwen3vl）；anima 按主人模板的槽位式 tag 行
（ENHANCE_SYS_ANIMA，SD1.5 那套旧 tag 串已删）。
出图前有一步「主体查证」：描述里出现角色名/作品名/历史人物/梗这类必须知道长什么样才画得对的词，
先让云端模型（可联网）查一份外观设定卡，喂给润色，查到的东西按名字缓存在 state/subjects.json。
传 lookup=False（群里说「不查：…」）可以跳过。查不到或接口挂了都安静退回原样描述，不影响出图。
内容审核闸曾按主人要求加过，2026-09-13 主人明确「全部撤掉不拦」后已整体移除，别再主动加回来。

对外：up() / boot() / wait_up() / generate() / est_seconds() / unload() / vram() / SubjectBook。
"""
import json
import logging
import os
import random
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MAX_SEED_NUM = 1125899906842624  # 2^50，超过会被服务端静默剪掉整条图像分支
log = logging.getLogger("comfy")


class ComfyError(RuntimeError):
    pass


# ---------------- HTTP 小工具 ----------------
def _req(url, data=None, timeout=20, raw=False):
    body = None
    headers = {"Accept": "application/json"}
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
    except urllib.error.HTTPError as e:
        try:
            txt = e.read().decode("utf-8", "replace")
        except Exception:
            txt = ""
        raise ComfyError("HTTP %s：%s" % (e.code, txt[:300]))
    except Exception as e:
        raise ComfyError("连不上 ComfyUI：%s" % str(e)[:120])
    return b if raw else (json.loads(b.decode("utf-8")) if b else {})


def base(cfg):
    return (cfg.get("url") or "http://127.0.0.1:8188").rstrip("/")


def up(cfg, timeout=10):
    try:
        _req(base(cfg) + "/system_stats", timeout=timeout)
        return True
    except Exception:
        return False


def vram(cfg):
    try:
        d = _req(base(cfg) + "/system_stats", timeout=5)
        dev = (d.get("devices") or [{}])[0]
        total = (dev.get("vram_total") or 0) / 1e9
        free = (dev.get("vram_free") or 0) / 1e9
        return {"total_gb": round(total, 1), "free_gb": round(free, 1)}
    except Exception as e:
        return {"error": str(e)[:80]}


# ---------------- 拉起服务 ----------------
def boot(cfg):
    """后台把 ComfyUI 开起来（不开浏览器窗口）。已经在跑就直接返回。"""
    if up(cfg):
        return True
    exe = cfg.get("python")
    cwd = cfg.get("comfy_dir")
    if not exe or not cwd or not os.path.exists(exe):
        raise ComfyError("没配 comfyui.python/comfy_dir，起不了服务")
    args = [exe, "-s", cfg.get("main", "main.py")] + list(
        cfg.get("launch_args") or ["--windows-standalone-build", "--disable-auto-launch", "--port", "8188"])
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    logf = open(Path(cwd) / "comfyui_qqbot.log", "ab")
    subprocess.Popen(args, cwd=cwd, stdout=logf, stderr=subprocess.STDOUT,
                     creationflags=flags, close_fds=True)
    log.info("正在启动本地 ComfyUI（%s）", cwd)
    return wait_up(cfg, float(cfg.get("boot_wait_sec", 120)))


def wait_up(cfg, sec=120):
    end = time.time() + sec
    while time.time() < end:
        if up(cfg):
            log.info("ComfyUI 已就绪")
            return True
        time.sleep(2)
    return False


def unload(cfg):
    """把模型从显存里踢出去（8G 卡要用空间给别的程序时喊一声）。"""
    return _req(base(cfg) + "/free", {"unload_models": True, "free_memory": True}, timeout=15)


def queue_idle(cfg, timeout=5):
    """队列里没有正在跑/排队的任务。翻唱要借显存前先问一句，别打断主人的活。"""
    try:
        d = _req(base(cfg) + "/queue", timeout=timeout)
        return not (d.get("queue_running") or d.get("queue_pending"))
    except Exception:
        return False


# ---------------- 服务端合法取值 ----------------
_opts = {"t": 0.0, "v": None}


def _opt_list(b, node, field, timeout=12):
    """问某个节点某个下拉框的合法取值（各家版本不一样，别硬猜）。"""
    try:
        d = _req(b + "/object_info/" + urllib.parse.quote(node), timeout=timeout).get(node, {})
        req = ((d.get("input") or {}).get("required") or {})
        return list((req.get(field) or [[]])[0])
    except Exception:
        return []


def _node_exists(b, name, timeout=10):
    """某个自定义节点在这台服务上有没有（没有就走降级路径），不抛异常。"""
    try:
        return bool(_req(b + "/object_info/" + urllib.parse.quote(name), timeout=timeout).get(name))
    except Exception:
        return False


def node_options(cfg, timeout=12):
    """这台 ComfyUI 认哪些模型/采样器、有没有 RTX 放大节点。缓存 5 分钟。"""
    now = time.time()
    if _opts["v"] and now - _opts["t"] < 300:
        return _opts["v"]
    b = base(cfg)
    try:
        d = _req(b + "/object_info/KSampler", timeout=timeout).get("KSampler", {})
        req = (d.get("input") or {}).get("required") or {}
        samplers = list((req.get("sampler_name") or [[]])[0])
        schedulers = list((req.get("scheduler") or [[]])[0])
        ckpts = list((req.get("ckpt_name") or [[]])[0])
        if not ckpts:
            c = _req(b + "/object_info/CheckpointLoaderSimple", timeout=timeout)
            c = c.get("CheckpointLoaderSimple", {})
            ckpts = list(((c.get("input") or {}).get("required") or {}).get("ckpt_name", [[]])[0])
    except ComfyError as e:
        log.warning("问不到 KSampler 的合法取值（%s），照配置原样提交", e)
        return _opts["v"] or {}
    v = {"samplers": samplers, "schedulers": schedulers, "ckpts": ckpts,
         "unets": _opt_list(b, "UNETLoader", "unet_name", timeout),
         "clips": _opt_list(b, "CLIPLoader", "clip_name", timeout),
         "vaes": _opt_list(b, "VAELoader", "vae_name", timeout),
         "loras": _opt_list(b, "LoraLoader", "lora_name", timeout),
         "rtx_vsr": _node_exists(b, "RTXVideoSuperResolution", timeout)}
    _opts.update({"t": now, "v": v})
    return v


def _pick(want, options, fallbacks):
    """配置里的名字服务端不认时，按 fallbacks 顺序换一个能用的。"""
    if not options or want in options:
        return want
    for f in fallbacks:
        if f in options:
            log.warning("服务端没有 %r，改用 %r", want, f)
            return f
    return options[0]


# ---------------- 工作流 ----------------
def graph(prompt, negative, width, height, steps, cfg_scale, seed, ckpt, sampler, scheduler, prefix):
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": ckpt}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"seed": int(seed), "steps": int(steps), "cfg": float(cfg_scale),
                         "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                         "model": ["1", 0],
                         "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["6", 0]}},
    }


def graph_hires(prompt, negative, width, height, seed, k, prefix, vsr=True, refine=True,
                image=None, denoise=1.0):
    """Krea2 Turbo 二采锐化（照主人 krea2基础流 的图结构，实测 8G 卡 143s/张、峰值 7.0GB）。

    k 是 style 字典；vsr=False 时这台服务没有 RTX 放大节点，就只交首采那张。
    refine=False 时不做二采精修——RTX 放大完直接出图，省掉一半多时间，细节稍软。
    image 给了就走图生图：空画布换成参考图编码，denoise 越小越像原图。
    """
    scale = float(k.get("vsr_scale") or 1.5)
    steps2 = int(k.get("refine_steps") or 12)
    start2 = int(k.get("refine_start") or 6)
    lora = k.get("lora")
    model_ref, clip_ref = ["1", 0], ["2", 0]      # 没配 LoRA 就用裸模型/裸文本编码器
    g = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": k["unet"], "weight_dtype": k.get("weight_dtype", "default")}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": k["clip"], "type": k.get("clip_type", "krea2"),
                         "device": k.get("clip_device", "default")}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": k["vae"]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": clip_ref}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": clip_ref}},
        "7": {"class_type": "EmptyLatentImage",
              "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": model_ref, "positive": ["5", 0], "negative": ["6", 0],
                         "latent_image": ["7", 0], "seed": int(seed),
                         "steps": int(k.get("steps") or 8), "cfg": float(k.get("cfg") or 1.0),
                         "sampler_name": k.get("sampler") or "euler_ancestral",
                         "scheduler": k.get("scheduler") or "normal", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
    }
    if lora:
        st = float(k.get("lora_strength") if k.get("lora_strength") is not None else 1.0)
        st_clip = float(k.get("lora_strength_clip") if k.get("lora_strength_clip")
                        is not None else st)
        g["4"] = {"class_type": "LoraLoader",
                  "inputs": {"model": ["1", 0], "clip": ["2", 0], "lora_name": lora,
                             "strength_model": st, "strength_clip": st_clip}}
        g["5"]["inputs"]["clip"] = ["4", 1]
        g["6"]["inputs"]["clip"] = ["4", 1]
        g["8"]["inputs"]["model"] = ["4", 0]
        model_ref = ["4", 0] if k.get("refine_lora") else ["1", 0]
    else:
        model_ref = ["1", 0]
    if image:   # 图生图：空画布换成参考图（VAE 是 "3"）
        _i2i_nodes(g, "7", "8", ["3", 0], image, width, height, denoise,
                   ("70", "71", "72"))
    if not vsr:
        g["13"] = {"class_type": "SaveImage", "inputs": {"images": ["9", 0],
                                                        "filename_prefix": prefix}}
        return g
    # 动态组合节点：API 里子输入要用点号键（resize_type.scale），照 UI 的写法直接给 scale 会被判缺失
    g["10"] = {"class_type": "RTXVideoSuperResolution",
               "inputs": {"images": ["9", 0], "resize_type": "scale by multiplier",
                          "resize_type.scale": scale, "quality": k.get("vsr_quality") or "ULTRA"}}
    if not refine:
        g["13"] = {"class_type": "SaveImage", "inputs": {"images": ["10", 0],
                                                        "filename_prefix": prefix}}
        return g
    g["11"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["3", 0]}}
    g["12"] = {"class_type": "KSamplerAdvanced",
               "inputs": {"model": model_ref,
                          "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["11", 0],
                          "add_noise": "enable", "noise_seed": int(seed),
                          "steps": steps2, "cfg": float(k.get("refine_cfg") or k.get("cfg") or 1.0),
                          "sampler_name": k.get("refine_sampler") or "euler",
                          "scheduler": k.get("refine_scheduler") or "beta",
                          "denoise": 1.0, "start_at_step": start2,
                          "end_at_step": int(k.get("refine_end") or steps2),
                          "return_with_leftover_noise": "disable"}}
    g["14"] = {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}}
    g["15"] = {"class_type": "SaveImage", "inputs": {"images": ["14", 0],
                                                    "filename_prefix": prefix}}
    return g


def est_seconds(cfg, style=None):
    """这张大概要多久：(热机, 冷机)。群里先给个准话，别让人干等。"""
    s = _style(cfg, style)[1]
    warm = float(s.get("est_sec") or cfg.get("est_sec") or 20)
    cold = float(s.get("est_cold_sec") or (warm * 1.6 + 15))
    return int(warm), int(cold)


ENHANCE_SYS_KREA2 = (
    "输出要求：只准写英文，全篇不得出现一个汉字（中文风格词也要翻过去）。\n"
    "你在把用户的中文描述扩写成一段英文画面描述，给 Krea2 Turbo 用（它的文本编码器读自然语言长句，"
    "不要写成逗号堆叠的 tag）。铁律：只画用户给了的东西——用户没提人物就不许出现人物，不许自己加角色、"
    "情节、动作、文字、logo、水印；用户没点风格（写实/二次元/油画/赛博朋克等）就不要声明风格。"
    "性别/人数/物种是最高优先级、一个字都不许改：用户说男就写 male/man，说女就写 woman/female，"
    "说动物就照实写；绝不允许把男性改成女性、也不许凭语感脑补性别。"
    "用户没提性别时用中性写法（a person / a figure），禁止默认成女性。"
    "扩写成 1~2 句、30~70 词，顺序固定："
    "主体（用户给什么写什么，别换别加）→ 环境与前景 → 光线（主光方向、色温、边缘光）→ "
    "镜头（焦段、景深、机位）→ 质感（材质细节、胶片颗粒、色调）。"
    "只输出这段英文描述本身，不要解释、不要引号、不要换行。")

# Anima 档的润色词：按主人 Styles/comfy/1/提示词模板.txt v3.0 的 SFW 骨架浓缩。
# 只取槽位顺序/输出协议/冲突表/镜头场景氛围库；模板的 R18 章节（体位/液体等）一律不进这里。
ENHANCE_SYS_ANIMA = (
    "你是 Anima3 动漫模型的提示词工程师，把用户的中文画面描述转写成一条英文 prompt（仅具体内容部分）。\n"
    "输出硬规则：只有 1 行、标签间用「, 」分隔、全部小写、禁止 (tag:1.2) 这类权重语法；"
    "tag 为主，标签说不清的（谁对谁做什么、复杂站位、分镜关系）才在整行末尾用英文短句补一句。\n"
    "槽位顺序（前面的权重更高，严格照排）：[人数/性别] → [角色名,作品名] → [外貌] → [服装] → "
    "[动作/姿势] → [表情] → [镜头] → [场景] → [质感/氛围] → 末尾自然语言。\n"
    "各槽位要点：人数/性别照用户原话，一个字都不许改——男→1boy、女→1girl、"
    "男女同框→1girl, 1boy, hetero、多人→2girls 这类按实写；"
    "用户点明了性别就绝不允许改写（说男/男性/帅哥/男人，全句就绝不许出现 1girl）；"
    "只有用户没提性别时才默认 1girl, solo；"
    "IP 角色必须写「角色名, 作品名」并跟至少 5 个外观锚点（发型/发色/瞳色/标志服饰/配饰），"
    "没把握的特征宁可只写用户说的，绝不编造；外貌写发色发型/瞳色/体型/肤色/兽耳翅膀尾巴这类非人特征/纹身；"
    "服装写具体单品+材质；表情 1~2 个；镜头景别必填（close-up / upper body / cowboy shot / full body / wide shot "
    "选一）+ 按需加视角（from side / from behind / from above / from below）；"
    "场景写场所+天气时辰（rain/snow/fog/night/sunset 这类可以，但禁止一切光线光影色调标签——"
    "sunlight/moonlight/rim light/warm lighting/candlelight/golden hour 等一律不许出现，模型自带）；"
    "质感最多 1 个（ink wash / film grain / painterly / sketch），氛围最多 1 个（cinematic / ethereal / "
    "dark atmosphere / poetic）。\n"
    "自查：单人且用户没说背影/侧脸时末尾镜头槽加 direct eye contact, facing viewer；"
    "多人时每个角色用「角色名 with 发色+瞳色+特征」的短语绑定外观，防止张冠李戴；"
    "视角标签互斥（from front 和 from behind 不同现，pov 和 full body 不同现，close-up 和 full body 不同现），"
    "同一身体部位的状态标签不矛盾不重复；标签总数单人 16~30、多人 22~40；"
    "服装场景氛围同一个世界观（古风配古风、赛博配赛博）。\n"
    "内容红线：只画用户给的东西，没提人物就不许出现人物，不许加文字/logo/watermark；"
    "绝不输出裸露、性行为、色情擦边相关的任何标签，保持全年龄向。\n"
    "只输出这一行，不要解释、不要引号、不要换行。")


def enhance(text, llm=None, engine="krea2", brief=""):
    """中文描述扩写成引擎爱吃的英文：krea2=自然语言长句，anima=模板槽位式 tag 行。
    （SD1.5 那套 tag 串已随档位一起删了。）没 llm 或失败就原样返回。
    brief 是 SubjectBook 查到的外观设定资料，给了就要求润色时照它写。"""
    if not llm:
        return text
    sys_txt = ENHANCE_SYS_ANIMA if engine == "anima" else ENHANCE_SYS_KREA2
    lo = 40 if engine == "krea2" else 20
    user = text
    if brief:
        sys_txt += ("\n\n这条描述里另附了一段【查证过的设定资料】：那是查出来的事实（可能是人物，也可能是"
                    "画作、风景、建筑、物品），不算你\"自己加的东西\"，画面主体必须按它写；"
                    "但**资料里没有人物就绝对不许加人物**——画作/风景/静物类主体就照它描述画面本身，"
                    "别硬塞一个角色进去；资料没覆盖的地方仍严格照用户原话，不要多脑补人物、动作、文字。")
        user = "%s\n\n【用户原本的画面要求】\n%s" % (brief, text)
    try:
        out = llm([{"role": "system", "content": sys_txt},
                   {"role": "user", "content": user}])
        out = (out or "").strip().strip('"').strip()
        return out if len(out) > lo else text
    except Exception as e:
        log.warning("提示词润色失败，用原文：%s", e)
        return text


# ---------------- 画师串自动选配（anima 档）：按画面主题换 @画师 tag ----------------
# anima 的画风基本由开头的 @画师 tag 决定（animadex.net 那套触发词），写死一个就千篇一律。
# 两档玩法：配了 artist_pool（"tag|一句话说明" 列表）就让模型在库里挑，稳；
# 没配（或 artist_pick 写 "free"）就联网搜 animadex 自由挑，可能挑歪，所以只收 @开头一条 tag。
ARTIST_FREE_SYS = (
    "你在给 Anima 动漫模型配「触发词 tag」。先记牢 Anima 官方 tag 规范，写错就失效：\n"
    "1) 角色 tag 就是 danbooru 原样 tag：**全小写、空格代替下划线、绝对不要加 @**。"
    "写法固定为「角色名, 系列名」两段，后面再补该角色的关键外观 tag（发色/瞳色/发型/标志物/常服，"
    "用 danbooru 说法，如 `blonde hair, twin braids, blue eyes` 这类）"
    "——只给名字模型只能瞎猜，补外观才画得像。\n"
    "2) 画师 tag 必须 @ 开头（`@` 后面接画师名，全小写、空格分隔）；不加 @ 画风几乎不生效。\n"
    "3) 输出顺序固定：角色名 → 系列名 → 外观 tag → @画师。\n"
    "4) 角色名和系列名一律转成**日文/英文原名**再写（danbooru 只认英日文 tag，中文名搜不到东西）；"
    "画面里点了哪个角色就写哪个，**绝不写当前画面里没有出现的角色名或作品名**。\n"
    "输出格式：一行、逗号分隔的 tag 串；画面里没有具体角色就只给 @画师；什么都拿不准就只输出 NONE。"
    "整个串控制在 10 个 tag 以内，外观只写**这个角色独有的、冲突的那几个**——"
    "绝不把搜到的清单整段搬过来（发色只能写一个，别把金发银发黑发全列上）。"
    "宁缺毋滥——绝不编造没见过的角色名/系列名/@画师。")

_ARTIST_CACHE = {}
_ROLE_KINDS = ("角色", "人物", "role", "character")

# ---------------- AnimaDex 官方目录：角色/画师的权威 trigger（不靠模型瞎猜） ----------------
_DEX = None          # None=还没读；False=没这份文件/读坏了；dict=读好了


def _dex(path=None):
    """懒加载 animadex.json（官方目录快照，state/_fetch_animadex.py 抓的）。没有就返回 None。"""
    global _DEX
    if _DEX is None:
        p = Path(path) if path else Path(__file__).with_name("animadex.json")
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            ch = [c for c in (d.get("characters") or []) if isinstance(c, dict)]
            ar = [a for a in (d.get("artists") or []) if isinstance(a, dict)]
            _DEX = {"characters": ch, "artists": ar} if (ch or ar) else False
        except Exception:
            _DEX = False
    return _DEX or None


def _dex_keys(c):
    """一个角色条目可以用来认名字的所有写法：中日文别名 + slug + 英文名。"""
    keys = [str(x).strip() for x in (c.get("aliases") or [])]
    slug = str(c.get("slug") or "").strip()
    if slug:
        keys += [slug, slug.replace("_", " "), re.split(r"_\(", slug)[0].replace("_", " ")]
    trig = str(c.get("trigger") or "")
    if trig:
        keys.append(trig.split(",")[0].strip())
    return [k for k in keys if len(k) >= 2]


def _dex_character(text):
    """画面点名的角色在官方目录里 → 返回准确的 danbooru tag 串（`角色名, 系列名, 核心外观 tag`）。
    别名的中日文按子串认，英文/数字按词边界认（免得 `2b` 命中 `2b小姐姐` 之外的杂串）。"""
    dex = _dex()
    if not dex or not str(text or "").strip():
        return ""
    raw, low = str(text), str(text).lower()
    for c in sorted(dex["characters"], key=lambda x: -int(x.get("count") or 0)):
        for k in _dex_keys(c):
            if re.search(r"[a-z0-9]", k, re.I):
                if re.search(r"(?<![a-z0-9])" + re.escape(k.lower()) + r"(?![a-z0-9])", low):
                    return _dex_tag(c)
            elif k in raw:
                return _dex_tag(c)
    return ""


def _dex_tag(c):
    return re.sub(r"\s+", " ", ", ".join(
        [x for x in (str(c.get("trigger") or "").strip(),
                     str(c.get("core_tags") or "").strip()) if x])).strip()


def _dex_artist_ok(tag):
    """@画师 tag 是不是官方目录里真实存在的（防模型编造 @画师 把画风带歪）。"""
    dex = _dex()
    if not dex:
        return True                      # 没目录就不拦（老行为）
    want = str(tag or "").lstrip("@").strip().lower()
    if not want:
        return False
    for a in dex["artists"]:
        for k in (str(a.get("trigger") or ""), str(a.get("slug") or "")):
            if want == k.replace("_", " ").strip().lower():
                return True
    return False


def _norm_tag(tag, kind):
    """按 Anima 官方规范整理 tag（模型卡原文：画师必须写 `@artist name`，没有 @ 风格几乎不生效；
    角色/通用 tag 就是 danbooru tag，全小写、空格代替下划线、**不带 @**）。
    所以池子里按类型自动规范化，主人 @ 写不写、大小写怎么写都能用。

    * 角色：`@FujitaKotone` / `hatsune_miku` → `fujita kotone`
    * 画师：`SolomonJudge` / `@free_style_(yohan1754)` → `@solomonjudge`（保留下划线转空格）
    """
    t = str(tag or "").strip().strip(",").strip().lstrip("@").strip()
    if not t:
        return ""
    if _is_role(kind):
        # 角色名常被写成驼峰（FujitaKotone），danbooru 里是空格分开的（fujita kotone），所以拆
        t = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", t)
    t = t.replace("_", " ")          # 官方卡：tag 用空格代替下划线（108_gou → 108 gou）
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    if _is_role(kind):
        return t.lower()
    return "@" + t


def _artist_pool(cfg, st):
    """候选池。条目两种写法：`tag|说明`、`tag|说明|类型`（类型缺省=画师）。
    类型写「角色」的当角色触发词——只有画面里真要画这个角色时才挑；其余都当画师/画风 tag。
    档位里写 artist_pool 会盖过全局 cfg。tag 一律经 _norm_tag 规范化后再参与匹配与拼装。"""
    pool = st.get("artist_pool") or cfg.get("artist_pool") or []
    out = []
    for x in pool:
        parts = [p.strip() for p in str(x).split("|", 2)]
        raw = parts[0] if parts else ""
        if not raw:
            continue
        desc = parts[1] if len(parts) > 1 else ""
        kind = parts[2] if len(parts) > 2 and parts[2] else "画师"
        tag = _norm_tag(raw, kind)
        if tag:
            out.append((tag, desc, kind))
    return out


def _is_role(kind):
    return str(kind).strip().lower() in _ROLE_KINDS


def _hit_tag(tag, out):
    """tag 有没有出现在模型输出里（带词边界，别让 @ab 命中 @abcd）。
    danbooru 里下划线 ≡ 空格、@ 前缀可有可无，所以两边先归一化再比——
    这样池子里写 `hatsune miku` 也能命中模型吐的 `@hatsune_miku`。
    组合条目（如 `fujita kotone, gakuen idolmaster` 整串当一个候选）只用第一段主 tag 判命中。"""
    key = str(tag).split(",")[0].strip().lstrip("@").strip()
    if not key:
        return False
    flat = lambda s: re.sub(r"[\s_]+", " ", str(s).replace("@", " ")).strip().lower()
    return re.search(r"(?<![a-z0-9])" + re.escape(flat(key)) + r"(?![a-z0-9])",
                     flat(out)) is not None


def _match_pool_tags(out, pool):
    """把输出里命中的候选挑出来：角色最多 1 个、画风最多 1 个，角色排前面。"""
    roles, styles = [], []
    for t, _d, k in pool:
        if _hit_tag(t, out):
            (roles if _is_role(k) else styles).append(t)
    if not roles and not styles:                  # 没命中就整串比对兜底（老行为）
        o = str(out).strip().lower()
        for t, _d, _k in pool:
            if o == t.lower():
                return [t]
    return roles[:1] + styles[:1]


_COLOR_WORDS = {"blonde", "black", "brown", "red", "pink", "blue", "green", "purple", "yellow",
                "white", "silver", "grey", "gray", "aqua", "orange", "cyan", "multicolored",
                "two-tone", "platinum", "golden", "teal", "violet", "turquoise"}


def _appearance_slot(tag):
    """这个 tag 占哪个「同类只有一个」的外观槽位（发色 / 瞳色），不占就返回 ""。
    搜索页经常把一堆发色全列出来，模型照抄就会自相矛盾（金发+银发+黑发…），所以要压。"""
    parts = str(tag).split()
    if len(parts) == 2 and parts[0] in _COLOR_WORDS:
        if parts[1] == "hair":
            return "hair color"
        if parts[1] in ("eyes", "eye"):
            return "eye color"
    return ""


def _dedup_tags(tags, limit=10):
    """外观同类去重 + 总数封顶，保住顺序（LLM 越靠前越相关）。"""
    out, taken = [], set()
    for t in tags:
        slot = _appearance_slot(t)
        if slot:
            if slot in taken:
                continue
            taken.add(slot)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def _split_role_artist(out, role_limit=10):
    """把联网选配的输出拆成「角色/系列/外观 tag 串」和「@画师」两半。
    Anima 规范里角色 tag 不带 @、画师必须带 @，所以按 @ 直接分就够了。
    返回 (角色串, 画师串)；任一半为空就是 ""。"""
    roles, artists = [], []
    for raw in re.split(r"[,，、\n;；|]+", str(out)):
        t = re.sub(r"^(角色|人物|系列|作品|外观|服装|画师|画风|风格|character|series|"
                   r"copyright|artist|style|role|tags?)\s*[:：]\s*", "",
                   raw.strip().strip("。.· \t\"'`"), flags=re.I).strip()
        if not t or t.lower() in ("none", "n/a", "na", "无"):
            continue
        if t.startswith("@"):
            t = re.sub(r"\s+", " ", t)
            if t.lower() not in [a.lower() for a in artists]:
                artists.append(t)
            continue
        t = re.sub(r"[\s_]+", " ", t).strip().lower()     # 下划线一律归一成空格，跟提示词要求一致
        if not t or len(t) > 40 or t.count(" ") > 6:      # 像句子不像 tag，丢掉
            continue
        if t in _COLOR_WORDS:                             # 裸颜色词（`yellow`）不是 tag
            continue
        if re.search(r"[:：]", t) or t in roles:          # 带冒号的不是 tag；重复的丢掉
            continue
        if re.search(r"[<>{}\[\]]", t):                   # 模板占位符残留（模型照抄 `<颜色> hair`）
            continue
        roles.append(t)
    return ", ".join(_dedup_tags(roles, role_limit)), ", ".join(artists[:1])


# 提示词里曾经拿来做「格式示范」的角色/系列 tag。模型有把示范值当答案照抄的毛病——
# 2026-09-14 血案：凡是走主体查证的角色，TAGS 全被写成 `fujita kotone, gakuen idolmaster`
# 开头（黍、花海咲季、能天使、阿芙佳朵全中招），存进缓存固化后出图千篇一律地画成藤田琴音。
# 现在示范串已从提示词里删干净，这里再留一道校验：吐出来的头号角色 tag 命中这些值、
# 而要查的词里又找不到该角色的原名依据（中文名/日文名/同样的罗马字）时，判为照抄，整串丢掉。
_DEMO_ECHO = {
    "fujita kotone": ("藤田琴音", "琴音", "fujita", "kotone"),
    "gakuen idolmaster": ("学园偶像大师", "學園偶像大師", "学园偶像", "gakuen", "idolmaster", "学マス"),
    "hatsune miku": ("初音未来", "初音ミク", "初音", "hatsune", "miku"),
    "nahida": ("纳西妲", "納西妲", "ナヒーダ", "nahida"),
}


def _demo_echo(tags, name, who=""):
    """角色 tag 串的头一段是不是在照抄提示词里的示范值（而不是本次要查的那个角色）。
    name/who 里能对上该角色的原名（中文/日文/罗马字任一）就不算抄——这样真画初音时不会被误杀。"""
    head = re.split(r"[,，]", str(tags or ""))[0].strip().lower().replace("_", " ")
    head = re.sub(r"\s+", " ", head)
    alias = _DEMO_ECHO.get(head)
    if alias is None:
        return False
    hay = ("%s %s" % (name or "", who or "")).lower()
    return not any(a.lower() in hay for a in alias)


# danbooru 只认英文/罗马字 tag。删掉提示词里的中英对照示例后，模型偶尔会「诚实但没用」地
# 把中文名原样当 tag 吐（2026-09-14：黍 → `黍, 明日方舟, red hair...`），中文 tag 模型不认。
_CJK_RE = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")


def _cjk_head(tags):
    """角色 tag 串的头一段（角色名）是不是中文/日文假名——是就说明这次没转成 danbooru tag，
    整串不可信（后续外观 tag 也可能跟着歪），调用方应当弃用。"""
    head = re.split(r"[,，]", str(tags or ""))[0]
    return bool(_CJK_RE.search(head))


# 🔴 2026-10-31：查证模型「查不到」时给的是**一句漂亮的否定话**，不是空串。
#   实测原文：`WHO: 未查到确切出处。` / `LOOK: No verified visual reference found;
#   the appearance of this term cannot be confirmed from available material.`
#   旧代码只看「字段非空」就当查到了 ⇒ 这段废话被当官方设定塞进布局提示词。
_NULL_ANSWER = re.compile(
    r"(未查到|沒查到|没查到|查不到|未找到|找不到|无法确定|无法确认|未确认|不确定|不认识"
    r"|没有找到|无相关|暂未|暂无"
    r"|no\s+(?:verified|reliable|visual|reference|information|data)"
    r"|cannot\s+be\s+(?:confirmed|verified|determined|found)"
    r"|not\s+(?:found|available|confirmed|verified|known)"
    r"|unknown|unconfirmed|no\s+info)", re.I)


def _is_null_answer(txt):
    """这段「设定资料」是不是其实在说「查不到」——是的话调用方必须当没查到。"""
    s = str(txt or "").strip()
    if not s:
        return True
    return bool(_NULL_ANSWER.search(s))


def pick_artist(cfg, st, prompt, brief, llm=None, web_llm=None, dex_tags="", dex_artist=""):
    """tag 选配（内置 anima 档 + wf 档）：按画面主题挑 tag，返回要拼上去的串
    （最多两个：角色在前、画风在后；"" = 一个都没挑中，维持档位里写死的 prompt_artist）。
    候选池 `comfyui.artist_pool` / 档位 artist_pool：`tag|说明` 或 `tag|说明|类型`，
    类型写「角色」的当角色触发词（画面里真要画这个角色才挑），其余当画师/画风 tag。
    dex_tags: 主体查证顺带拿到的 danbooru 角色 tag，比事后联网猜准，优先用。
    artist_pick: True/开 = 配了库在库里挑、没配联网挑；"free" = 强制联网挑（忽略候选库）；False/关 = 不动。"""
    mode = cfg.get("artist_pick", True)
    if mode in (False, None, "", 0) or str(mode).lower() in ("off", "关"):
        return ""
    force_web = str(mode).lower() in ("free", "自由", "联网")
    pool = [] if force_web else _artist_pool(cfg, st)
    # 查证（联网小模型）顺带给的 @画师：同样过官方目录，防编造把画风带歪
    dex_artist = str(dex_artist or "").strip()
    if dex_artist and not dex_artist.startswith("@"):
        dex_artist = "@" + dex_artist
    if dex_artist and not _dex_artist_ok(dex_artist):
        log.info("查证给的画师 tag「%s」不在官方目录里，丢掉防编造", dex_artist[:60])
        dex_artist = ""
    dex_hit = _dex_character(prompt) or _dex_character(brief)
    src = "候选库" if pool else ("官方目录" if dex_hit else
                                 ("查证" if (str(dex_tags or "").strip() or dex_artist)
                                  else "联网"))
    key = (src, hash(tuple(pool)), prompt[:80], brief[:60], str(dex_tags or "")[:90], dex_hit[:90])
    hit = _ARTIST_CACHE.get(key)
    if hit is not None:
        return hit
    tag, user = "", "画面描述：%s\n%s" % (prompt, ("设定卡：%s" % brief) if brief else "")
    try:
        if pool and llm is not None:
            menu = "\n".join("- %s%s%s" % (t, "（角色）" if _is_role(k) else "（画风）",
                                           ("：" + d) if d else "")
                             for t, d, k in pool)
            out = (llm([
                {"role": "system", "content":
                    "从下面的候选 tag 里挑最贴合画面主题的，最多两个：角色 tag 最多一个"
                    "（只有画面里真要画这个角色时才挑；描述里没提角色、或没有对应的角色 tag 就不挑），"
                    "画风 tag 最多一个。只输出挑中的 tag 本身，多个用逗号分隔、角色在前；"
                    "一个都不合适就只输出 NONE。不许输出解释或换行。\n候选：\n" + menu},
                {"role": "user", "content": user},
            ]) or "").strip()
            tag = ", ".join(_match_pool_tags(out, pool))
        elif dex_hit:
            tag = dex_hit
            log.info("画师/角色选配：官方目录命中「%s」", tag[:90])
        elif str(dex_tags or "").strip() or dex_artist:
            tag = ", ".join([x for x in (str(dex_tags or "").strip(), dex_artist) if x])
            log.info("画师/角色选配：用查证给的 tag「%s」", tag[:90])
        elif web_llm is not None:
            out = (web_llm([
                {"role": "system", "content": ARTIST_FREE_SYS},
                {"role": "user", "content": user +
                 "\n（提醒：角色名请用日文/英文原名，角色 tag 不带 @、画师才带 @）"},
            ], query="danbooru tags for %s (character name + series + appearance + @artist, "
                     "anima model, use english or japanese name)" % prompt[:70]) or "").strip()
            roletxt, artistxt = _split_role_artist(out)
            if roletxt and _cjk_head(roletxt):        # danbooru 不认中文/假名 tag，整串丢
                log.info("联网选配的角色串是中文或假名（%s），danbooru 不认，丢掉", roletxt[:40])
                roletxt = ""
            if roletxt and _demo_echo(roletxt, prompt, brief):
                log.info("联网选配的角色串疑似照抄示范值（%s），丢掉", roletxt[:60])
                roletxt = ""
            if artistxt and not _dex_artist_ok(artistxt):
                log.info("画师 tag「%s」不在官方目录里，丢掉防编造", artistxt)
                artistxt = ""
            tag = ", ".join([x for x in (roletxt, artistxt) if x])
            log.info("选配联网输出: %s -> 角色「%s」画师「%s」",
                     out[:110].replace("\n", " "), roletxt[:70], artistxt)
    except Exception as e:
        log.warning("tag 选配失败（%s），用默认画师串", e)
        tag = ""
    if len(_ARTIST_CACHE) > 200:
        _ARTIST_CACHE.clear()
    _ARTIST_CACHE[key] = tag
    if tag:
        log.info("画师/角色选配：%s（%s）", tag, src)
    return tag


# ---------------- 主体查证：先搞清楚画的是什么，再画 ----------------
# 单字专有名词的护栏（2026-09-16 加）：中文角色名确实有单字的（明日方舟「黍/令/夕/年」
# 全是单字），原来那句 `len(n) > 1` 把这类角色**一律丢掉**、永远触发不了查证。
# 但放行单字噪声也大，所以只收**单个汉字**、且不在下面这张功能词/通用词表里。
_ONE_CHAR_STOP = set(
    "的了吗呢吧啊呀哦嗯我你他她它这那是个只张条片和与在有不没画图来去看说要想会很太给把被跟从对为就也都还"
    "猫狗鸟鱼花草树山水人男女老少龙马牛羊鸡虎兔蛇猴猪象熊鹿鹤鹰")


def _ok_subject_name(n):
    """挑出来的词收不收：既防幻觉垃圾串，又放行单字中文角色名。"""
    n = (n or "").strip()
    if not n or len(n) > 24:
        return False
    if len(n) == 1:
        return bool(re.fullmatch(r"[\u4e00-\u9fff]", n)) and n not in _ONE_CHAR_STOP
    return True


# 🔴 2026-10-31：**作品名不能当角色去查外观**。
#   实测「把这只猪画成鸣潮的珂莱塔」挑出 `['鸣潮', '珂莱塔']` 两个都去查，
#   「鸣潮 角色 外观」搜回来的是「pink hair / white sleeveless coat」——
#   那是某个别的角色的描述，却被当成鸣潮的设定塞进提示词，直接把珂莱塔带成粉发。
#   作品名只该用来**定位角色**（作为搜索词的上下文），不该单独产出「外观」。
#   判据：常见游戏/动漫名 + 长度像作品名却明显是系列名的词。宁可窄，别误杀角色。
_SERIES_ONLY = {
    "鸣潮", "原神", "崩坏", "崩坏3", "崩坏星穹铁道", "星穹铁道", "绝区零", "明日方舟",
    "蔚蓝档案", "碧蓝航线", "少女前线", "重返未来", "无期迷途", "白夜极光",
    "fate", "fgo", "genshin", "genshinimpact", "honkai", "starrail", "zenless",
    "arknights", "bluearchive", "azurlane", "nikke", "gf2", "wuwa", "wutheringwaves",
    "东方", "touhou", "vocaloid", "宝可梦", "pokemon", "塞尔达", "zelda",
    "最终幻想", "finalfantasy", "尼尔", "nier", "英雄联盟", "lol", "守望先锋", "overwatch",
    "守望", "valorant", "无畏契约", "apex", "minecraft", "我的世界", "罗小黑",
}


def _is_series_name(n):
    """这个词是**作品名**而不是角色名吗（作品名不单独查外观）。"""
    s = re.sub(r"[\s《》「」()（）:：]", "", str(n or "")).lower()
    return bool(s) and s in _SERIES_ONLY


# ---------------------------------------------------------------------------
# 成品 tag 串 / 画师串的判定（2026-09-16 加）
# 主人给的如果已经是 danbooru tag 串或带 @ 的画师串，说明画风/角色他自己定好了，
# 再丢给联网模型查一遍纯属白等 20~30 秒。判成"成品"就直接出图。
# ---------------------------------------------------------------------------
_CJK_ANY_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")
# danbooru tag 的合法字符：小写英文/数字/下划线/空格/括号/少量标点。
# 注意要放行空格（`fujita kotone`）和括号（`free_style_(yohan1754)`）。
_TAG_TOKEN_RE = re.compile(r"^[0-9A-Za-z_()\[\]{}+!.\-:/' ]+$")
_ARTIST_AT_RE = re.compile(r"@[0-9A-Za-z_\-][0-9A-Za-z_\-() .]{1,}")


def looks_like_tagline(text, min_tokens=3):
    """这段提示词是不是「主人自己写好的成品串」——是就不用再联网查证。

    判据（从强到弱）：
      1. 有中文 → 不是成品（中文描述/中文角色名还得靠查证补全外观）；
      2. 有 `@画师` tag（Anima 规范里画师必须带 @）→ 成品，画风主人已指定；
      3. 纯 ASCII 且逗号切出 ≥min_tokens 个 danbooru 风格 token → 成品。

    ⚠️ 第 1 条优先：主人写 `@画师 tag 画个初音` 时中文角色名仍然要查，
    所以「有中文」一律走查证，不因为带了 @ 就跳过。
    """
    t = str(text or "").strip()
    if not t:
        return False
    if _CJK_ANY_RE.search(t):
        return False
    if _ARTIST_AT_RE.search(t):
        return True
    toks = [x.strip() for x in re.split(r"[,，]", t) if x.strip()]
    if len(toks) < min_tokens:
        return False
    good = 0
    for x in toks:
        if not _TAG_TOKEN_RE.match(x):
            continue
        # 单个超长英文短语（一整句英文描述）不算 tag
        if len(x) > 48 and " " in x and "_" not in x:
            continue
        good += 1
        if good >= min_tokens:
            return True
    return False


def want_enhance(prompt, cfg_enhance=True):
    """这一步要不要润色/扩写。

    主人 2026-09-18 重提的老规矩（之前只做了一半，这里钉死）：**用户自己写好了
    tag 串就不润色也不扩写，原样送进工作流** —— 一串精心配好的 tag 被小模型按
    anima 模板重写一遍等于白写，还会把用户自己的画师/角色串改掉。
    判据两条（命中任一就不润色）：
      1. 成品串判定 `looks_like_tagline()`（无中文 + 带 @画师，或 ≥3 个 danbooru token）；
      2. **整条没有中文/日文** —— 主人自己写的英文提示词就是给模型的成品，
         让小模型按模板再翻一遍只会改坏（2026-09-18：主人发英文串被重写，实测踩到）。
    它跟「成品串跳过查证」是同一条口径的两半，只做查证那半会让用户的串照样被重写。
    """
    if not cfg_enhance:
        return False
    p = str(prompt or "").strip()
    if not p:
        return False
    if looks_like_tagline(p):
        return False
    if not _CJK_ANY_RE.search(p):      # 纯英文串：原样送，别重写
        return False
    return True


SUBJECT_PROBE_SYS = (
    "看下面这段中文画面描述，挑出「必须先知道它长什么样才画得对」的具体专有名词："
    "游戏/动漫/影视/VTuber 角色名、作品名、历史人物、神话人物、网络梗、外号、账号名或 UP 主名、"
    "品牌或产品名。通用名词（猫、少女、森林、武士、龙）、风格词（赛博朋克、二次元、水墨、写实）、"
    "职业、动物、常见物件、地名都不算。"
    "另外这些一律算：看着像人名/外号/ID 的陌生词；两个常见词拼起来特指某个东西的组合（品牌+动物、"
    "修饰+角色）；不像普通英文单词的英文串；「X的Y」这种写法里的 X（多半是作品名）；"
    "**单个汉字如果是角色名/人名（不少游戏角色就是单字名，如「黍」「令」「夕」）也算**——"
    "判断依据是它在这句话里像不像一个名字，而不是字数。"
    "判不准就往「算」那边靠：只要你说不出它确切的发色、服装、长相，就列出来。"
    "输出：照抄描述里的写法，最多 3 个，用逗号分隔；一个都没有就只输出「无」。"
    "不要解释、不要引号、不要换行、不要加书名号。")

SUBJECT_CARD_SYS = (
    "你在替文生图模型整理「某个专有名词到底长什么样」。下面会附一份联网查来的资料，"
    "以那份资料为准（优先立绘、设定、百科类条目的说法）；资料互相矛盾或明显是同名别的东西时，"
    "按最像那个角色/人物的说法取，绝不自己编造。只输出下面三行，格式严格照抄：\n"
    "WHO: 一句中文，说清是谁、出自哪部作品或哪段历史；没查到就写「未查到确切出处」。\n"
    "LOOK: 一段英文，只写画面里看得见的特征：发型发色与长度、瞳色、脸型与年龄感、"
    "每件服装单品（颜色、材质、纹样、剪裁）、配饰与道具（武器、徽章、纹身、帽子等）、"
    "整体配色主调、体态气质。30~70 词。禁止写背景、构图、镜头、光线、剧情。\n"
    "🔴 LOOK 必须写**角色的默认/主线官方立绘**那套造型，"
    "**不要写皮肤、泳装、活动限定服饰、联名款** —— 资料里常常混进这类，"
    "写错了角色就被画成泳装/礼服，跟本人完全不像，而且这类描述还容易被上游安全策略拦。"
    "资料里同时出现多套服装时，选**最常规、最常在立绘里出现**的那套；"
    "实在只有泳装/皮肤套装可考，就在 LOOK 开头写 `default outfit:` 并说明这是非默认造型。\n"
    "🔴 LOOK 写成**穿好了的衣服**：不写 underwear / lingerie / bare skin / cleavage / "
    "exposing / midriff / nudity 这类词，服装一律按正常穿着的说法（dress, coat, shirt, "
    "skirt, pants, boots）。\n"
    "TAGS: 一行 danbooru tag，给动漫模型认角色用。格式死规定：全小写、空格代替下划线、"
    "**不带 @**、**只能是英文或罗马字**（danbooru 没有中文 tag，写中文等于没写、还会误导）；"
    "顺序＝角色名, 系列名, 关键外观 tag"
    "（tag 之间**只用一个英文逗号**分隔，每个 tag 内部可以用空格，"
    "但**绝不许用空格把各个 tag 连成一长串**——那样整行就废了）。"
    "**角色名和系列名只有在你确实知道这个角色在 danbooru 上的英日文原名时才写**："
    "角色名写完整的姓+名两段式，不要只写姓或只写名；系列名写完整系列名，不要用缩写简称。"
    "**上面那份资料里如果已经明确写出了角色名与作品名的英日文写法，直接照抄进 TAGS 最前两格**"
    "（那是抄资料、不是自己编），别因为保守就不写——模型认角色很吃这两个 tag。"
    "**只要对写法没有把握，就跳过角色名和系列名、只写外观 tag**——外观 tag 照样有用，"
    "**绝不许硬拼一个拿不准的名字**（拼错会把画面带成另一个人，比不写糟得多），"
    "**也不许拿中文名顶替**。"
    "外观 tag 把 LOOK 里每个显著特征都转成 danbooru 说法（顺序是颜色在前、部位在后，"
    "如 `black hair`、`blue eyes` 这种两词写法，别写成 `hair black`；双麻花辫就是 "
    "`twin braids` 这种 danbooru 通用词；标志道具和常服同理），"
    "整个串 10 个 tag 以内，发色瞳色各只写一个。"
    "不是具体角色（风景/物品/历史场景）或整行都拿不准，就写「无」；"
    "**绝不许写任何与本次要查的词无关的角色或作品名**。\n"
    "除了这三行什么都别输出，不要 markdown、不要编号、不要客套。")


def _role_tag_of(s):
    """把「名字」变成 danbooru 能认的 tag 写法（小写、空格替下划线、去非 ASCII）。"""
    t = re.sub(r"[\s_]+", " ", str(s or "").strip()).strip().lower()
    t = re.sub(r"[^\x20-\x7E]", "", t).strip(" ,.-")
    return t if 1 < len(t) <= 40 else ""


def _merge_role_tags(name, series, tags):
    """把「视觉模型看图认出来的名字」并进 tag 串最前。

    整理模型偶尔不按逗号格式来（整行被当句子丢掉，2026-09-15 实测偶发），
    但名字是它看图认出来的、也是最靠谱的角色 tag 来源，不该因为格式问题白丢。
    已有的不重复加。
    """
    head = [x for x in (_role_tag_of(name), _role_tag_of(series)) if x]
    body = (tags or "").strip().strip(",")
    low = body.lower()
    add = [h for h in head if h not in low]
    merged = ", ".join(add + ([body] if body else []))
    # 只去「一模一样」的重复项。别做子串去重——角色名常常是系列名的一部分
    # （`frieren` ⊂ `sousou no frieren`、`miku` ⊂ `hatsune miku`），那样会把角色名误删。
    seen, keep = set(), []
    for t in (x.strip() for x in merged.split(",")):
        if not t or t.lower() in seen:
            continue
        seen.add(t.lower())
        keep.append(t)
    return ", ".join(keep)


class SubjectBook:
    """查过的角色外观设定缓存。probe＝不联网的快速模型，web＝能联网的模型。"""

    def __init__(self, path=None, enabled=True, max_names=2, budget_sec=45, max_entries=300):
        self.path = Path(path) if path else None
        self.enabled = bool(enabled)
        self.max_names = max(1, int(max_names))
        self.budget_sec = float(budget_sec)
        self.max_entries = int(max_entries)
        self._cards = {}
        self._lock = threading.Lock()
        self._loaded = False

    def _fetch_card(self, name, text, f):
        """拿一张设定卡：交给小模型压成 WHO/LOOK/TAGS，失败重试一次。"""
        for attempt in (1, 2):     # provider 偶发 400/超时；单条失败不该拖垮整步查证
            try:
                return self._ask_card(name, text, f)
            except Exception as e:
                log.warning("主体查证「%s」第 %d 次失败（%s）", name, attempt, e)
                time.sleep(0.8)
        return {}

    # ---- 缓存读写 ----
    def _load(self):
        self._loaded = True
        if not self.path or not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                self._cards = {k: v for k, v in d.items() if isinstance(v, dict)}
        except Exception as e:
            log.warning("设定缓存读失败（%s），当空的用", e)

    def _save(self):
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if len(self._cards) > self.max_entries:   # 最久没用的先扔
                drop = sorted(self._cards, key=lambda k: self._cards[k].get("ts", 0))
                for k in drop[:len(self._cards) - self.max_entries]:
                    self._cards.pop(k, None)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cards, ensure_ascii=False, indent=0),
                           encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:
            log.warning("设定缓存写失败：%s", e)

    def clear(self, name=None):
        """「画图 重查」：给名字就删那一条，不给就整本清空。返回剩下条数。"""
        with self._lock:
            if not self._loaded:
                self._load()
            if name:
                self._cards.pop(self._norm(name), None)
            else:
                self._cards = {}
                try:
                    if self.path and self.path.exists():
                        self.path.unlink()
                        return 0
                except Exception as e:
                    log.warning("设定缓存文件没删掉：%s", e)
            self._save()
            return len(self._cards)

    def size(self):
        with self._lock:
            if not self._loaded:
                self._load()
            return len(self._cards)

    @staticmethod
    def _norm(name):
        return re.sub(r"\s+", "", (name or "").strip().strip("《》「」“”\"'")).lower()

    @staticmethod
    def _in_text(name, text):
        """挑词模型偶尔会把系统提示当输出（吐「照抄描述里的写法」「最多 3 个」这种），
        还会把这种垃圾名字连同查到的资料一起存进缓存。所以要求挑出来的名字必须真的
        出现在原文里，否则一律当幻觉丢掉（宁可不查，也不要查错角色）。
        单字名（「黍」）在中文文本里必然出现，这条校验对它没意义，直接放行（另有护栏）。"""
        n = re.sub(r"\s+", "", str(name or ""))
        t = re.sub(r"\s+", "", str(text or ""))
        if len(n) == 1:
            return True
        return len(n) > 1 and n in t

    # ---- 三步：挑词 → 查词 → 拼资料 ----
    def _ask_names(self, text, probe):
        out = (probe([{"role": "system", "content": SUBJECT_PROBE_SYS},
                      {"role": "user", "content": text}]) or "").strip()
        if not out or out in ("无", "無", "无。", "没有") or len(out) > 60:
            return []
        names = []
        for n in re.split(r"[,，、;；\n]+", out):
            n = n.strip().strip("《》「」“”\"'")
            n = re.sub(r"^(?:角色|人物|名字|专有名词|需要查证)[:：]\s*", "", n).strip()
            # 🔴 作品名丢掉（见 `_is_series_name`）：它当搜索上下文有用，但单独查外观
            #    会查到**别的角色**的描述，把主角带成另一个人（实测珂莱塔被带成粉发）。
            if _is_series_name(n) or (not _ok_subject_name(n)) or n in names:
                continue
            if self._in_text(n, text):
                names.append(n)
            if len(names) >= self.max_names:
                break
        return names

    def _ask_card(self, name, text, web):
        """web 的约定：web(messages, query)->str，query 是让我们先去联网搜的词。

        🔴 2026-10-31 实测：查询词**越短越准**。
           原来写 `"%s 是谁 角色 设定 外观 介绍"`，实测「妄想天使」那条
           被搜索引擎判成泛查询 ⇒ 搜狗/百度反爬后落到 Bing ⇒ 返回**宜家床架**
           （"Affordable & Modern Beds - Bed Frames - IKEA"），一条都不相关。
           改成 `"妄想天使 角色 外观"` ⇒ 萌娘百科条目直达，拿到
           「南宫羽 粉发梢 流星锤 翅膀 SURIEL / 千夏 绿发 / 爱芮」全部设定。
           ⇒ 只用「名字 + 角色/外观」两个限定词，别堆问句。
        """
        query = ("%s 角色 外观" % name).strip()

        msgs = [{"role": "system", "content": SUBJECT_CARD_SYS},
                {"role": "user",
                 "content": "要查的词：%s\n它出现在这段画面描述里：%s" % (name, text)}]
        try:
            out = web(msgs, query)
        except TypeError:
            out = web(msgs)                      # 老回调：只接 messages
        who = look = tags = ""
        for line in (out or "").splitlines():
            l = line.strip().lstrip("-*• ").strip()
            if l[:4].upper().startswith("WHO"):
                who = re.sub(r"^WHO\s*[:：]\s*", "", l, flags=re.I)
            elif l[:4].upper().startswith("LOOK"):
                look = re.sub(r"^LOOK\s*[:：]\s*", "", l, flags=re.I)
            elif l[:4].upper().startswith("TAGS"):
                tags = re.sub(r"^TAGS\s*[:：]\s*", "", l, flags=re.I)
        out = (out or "").strip()
        if not look and len(out) > 40 and "未查到" not in out:
            look = re.sub(r"\s+", " ", out)[:600]      # 模型没按格式来，整段兜住
        # 🔴 2026-10-31 真 bug：模型查不到时会老实吐
        #    `WHO: 未查到确切出处` + `LOOK: No verified visual reference found...`
        #    旧代码只要 look 非空就当「查到了」，把这段**否定式废话**当成官方设定
        #    塞进布局提示词 —— 布局模型于是以为有依据，反而更放胆瞎编。
        #    主人原话「你都不搜下妄想天使三人组是谁吗」：搜了，但搜空的结果被当成功了。
        #    判据：`_is_null_answer()`，命中就把 look/who/tags 一起清空（等同没查到）。
        #
        # 🔴 2026-10-31 自查改（第一版写错了，自己踩的）：
        #    原来写 `if _is_null_answer(look) or _is_null_answer(who)` —— 而
        #    `_is_null_answer("")` 对**空串也返回 True**，于是「who 查到了、look 空」
        #    这种**半成功**的卡会被整张清掉。实测现场：
        #    `who='《绝区零》中的三人女子偶像组合「妄想天使」，成员为南宫羽、千夏'`
        #    明明有价值（知道出自哪、成员是谁），却因为 LOOK 那行模型没写，
        #    被判成「查不到」⇒ 资料全丢 ⇒ 又回到瞎画。
        #    正确判据：**who 明确说查不到** → 整张废；否则 who 有内容就保留 who，
        #    只有 look 也真没有时才降级成「只知道是谁、不知道长什么样」。
        if _is_null_answer(who):
            log.info("主体查证：「%s」模型回答「查不到是谁」（%r），当没查到处理",
                     name, who[:40])
            return {"who": "", "look": "", "tags": "", "ts": time.time()}
        if _is_null_answer(look):
            log.info("主体查证：「%s」只知道是谁、外观没给出（who=%r）", name, who[:40])
            look, tags = "", ""
            if not who.strip():
                return {"who": "", "look": "", "tags": "", "ts": time.time()}

        role = _split_role_artist(tags)[0] if tags else ""
        if role and _cjk_head(role):                    # 中文/假名 tag，danbooru 不认，整串弃用
            log.info("主体查证：「%s」的角色 tag 是中文（%s），danbooru 不认，弃用", name, role[:40])
            role = ""
        if role and _demo_echo(role, name, who):        # 防把提示词示范值当答案抄进缓存
            log.warning("主体查证：「%s」吐的角色 tag「%s」疑似照抄示范值，丢掉", name, role[:60])
            role = ""
        return {"who": who[:160], "look": re.sub(r"\s+", " ", look).strip()[:900],
                "tags": role, "ts": time.time()}

    def lookup(self, text, probe=None, web=None):
        """返回 (要喂给润色的资料段, 查到的名字列表)。失败一律空资料，不抛异常。"""
        if not self.enabled or not (probe or web):
            return "", []
        # 主人自己给了成品 tag 串 / 画师串：画风角色他都定好了，别再联网查一遍白等
        # （联网查证一次 20~30 秒）。中英混写（`@画师 画个初音`）不算成品。
        if looks_like_tagline(text):
            log.info("主体查证跳过：提示词已是成品 tag 串/画师串（%d 字）", len(text or ""))
            return "", []
        t0 = time.time()
        try:
            with self._lock:
                if not self._loaded:
                    self._load()
                names = self._ask_names(text, probe or web)
                if not names:
                    return "", []
                got, changed = [], False
                for n in names:
                    k = self._norm(n)
                    card = self._cards.get(k)
                    # 老卡片（加 TAGS 之前查的）没有 tags 字段要重查一次补齐；
                    # 但官方目录认得的角色不靠卡片出 tag，就别为这个白跑一趟联网。
                    if card and card.get("look") and ("tags" in card or _dex_character(n)):
                        got.append((n, card))
                        continue
                    if time.time() - t0 > self.budget_sec:
                        log.info("主体查证超时预算用尽，剩下 %s 不查了", n)
                        break
                    card = self._fetch_card(n, text, web or probe)
                    # 🔴 2026-10-31：判「查到了」不能只看 look。实测模型常常
                    #    先答对了「是谁、出自哪部作品、成员有谁」，但把 LOOK 那行留空
                    #    （思考型模型 max_tokens 不够时会这样）。只看 look 就整张丢，
                    #    等于把有用的线索也扔了。who 有内容同样算查到。
                    if card.get("look") or card.get("who"):
                        self._cards[k] = card
                        got.append((n, card))
                        changed = True
                    else:
                        log.info("主体查证：「%s」没查到设定，照原样画", n)
                if changed:
                    self._save()
            if not got:
                return "", []
            parts = []
            for n, c in got:
                seg = "【%s 的设定资料】" % n
                if c.get("who"):
                    seg += "\n是谁：" + c["who"]
                if c.get("look"):
                    seg += "\n外观（英文）：" + c["look"]
                parts.append(seg)
            return "\n\n".join(parts), [n for n, _ in got]
        except Exception as e:
            log.warning("主体查证整步失败（%s），直接照原样画", e)
            return "", []

    def card_for(self, name, series="", probe=None, web=None):
        """按名字直接查一张设定卡——跳过「从中文描述里挑专有名词」那一步。

        反推认人专用：名字是视觉模型看图自己说出来的，不该再让挑词模型猜一遍。
        返回 (资料段, danbooru tag 串, who)；查不到一律空串，不抛异常。命中缓存不重复联网。
        """
        if not self.enabled or not name or not (probe or web):
            return "", "", ""
        q = ("%s %s" % (name, series)).strip()
        card = {}
        try:
            with self._lock:
                if not self._loaded:
                    self._load()
                k = self._norm(name)
                card = self._cards.get(k) or {}
                if not (card.get("look") and ("tags" in card or _dex_character(name))):
                    card = self._fetch_card(q, "反推识图：%s" % q, web or probe)
                    if card.get("look"):
                        self._cards[k] = card
                        self._save()
                    else:
                        card = {}
        except Exception as e:
            log.warning("认人查证「%s」整步失败（%s）", name, e)
            return "", "", ""
        if not card.get("look"):
            return "", "", ""
        who = card.get("who") or ""
        brief = ("是谁：" + who + "\n外观（英文）：" + card["look"]) if who \
            else ("外观（英文）：" + card["look"])
        return brief, _merge_role_tags(name, series, card.get("tags") or ""), who

    def artist_for(self, names):
        """取查证时顺带拿到的 @画师（联网查证那条路会给）。没有就 ""。"""
        for n in (names or []):
            with self._lock:
                if not self._loaded:
                    self._load()
                a = str((self._cards.get(self._norm(n)) or {}).get("artist") or "").strip()
            if a:
                return a
        return ""

    def tags_for(self, names):
        """取查证时顺带拿到的 danbooru tag（给「画师/角色选配」当角色串用）。
    查证和出图在同一个上下文里，tag 与外观描述天然一致，比事后拿主题去联网猜准得多；
    没有就返回 ""，调用方退回原来的候选池/联网路径。"""
        for n in (names or []):
            with self._lock:
                if not self._loaded:
                    self._load()
                c = self._cards.get(self._norm(n)) or {}
            t = str(c.get("tags") or "").strip()
            if not t:
                continue
            if _demo_echo(t, n, c.get("who")):         # 老缓存里抄来的示范值，一律不认
                log.warning("设定缓存「%s」的角色 tag 疑似抄的示范值，忽略：%s", n, t[:60])
                continue
            t = _split_role_artist(t)[0]               # 老缓存可能残留占位符（`<黑> hair`）/中文段，就地净化
            if t and not _cjk_head(t):
                return t
        return ""


def _cancel(b, pid):
    """超时收场：我们排着就删自己的任务；确实在跑才 interrupt；主人自己的工作流别碰。"""
    try:
        q = _req(b + "/queue", timeout=10)
    except ComfyError:
        return "服务没应答，任务状态未知"
    run = [x[1] for x in (q.get("queue_running") or []) if len(x) > 1]
    pend = [x[1] for x in (q.get("queue_pending") or []) if len(x) > 1]
    if pid in pend:
        try:
            _req(b + "/queue", {"delete": [pid]}, timeout=10)
            return "前面任务太多，已把这张从队列里撤掉"
        except ComfyError:
            pass
    if pid in run:
        try:
            _req(b + "/interrupt", {}, timeout=8)
        except Exception:
            pass
        return "画太久，已中断"
    return "前面还有别的工作流在跑，这张先不等了"


def _free(b):
    """画完（或画挂）就把显存还回去。主人定死的规矩：不留常驻模型，所以这里没有开关。"""
    try:
        _req(b + "/free", {"unload_models": True, "free_memory": True}, timeout=10)
    except Exception:
        pass


def graph_anima(prompt, negative, width, height, seed, k, prefix, image=None, denoise=1.0):
    """主人 Styles/comfy/1 的 Anima 工作流：anima_baseV10 → 双 LoRA(各1.0) → ModelSamplingAuraFlow(shift3)
    → KSampler 30步 er_sde/simple cfg5 → 1024x1536 直出。
    原图里的 UltimateSDUpscale 分支在主人保存时就是旁路状态（mode=4），照实不复刻；
    rgthree/Comfyroll 那几个节点只是界面开关和文本框，API 图直接绕掉。
    模板约定质量词和 @画师 由脚本拼，不走润色模型——这里照原图的 JoinStringMulti 顺序拼在最前。"""
    head = " ".join(x for x in (k.get("prompt_prefix"), k.get("prompt_artist")) if x)
    if head:
        prompt = head + " " + prompt
    model = ["1", 0]
    if k.get("lora"):
        model = ["2", 0]
        if k.get("lora2"):
            model = ["3", 0]
    g = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": k["unet"], "weight_dtype": k.get("weight_dtype", "default")}},
        "5": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": k["clip"], "type": k.get("clip_type", "stable_diffusion")}},
        "8": {"class_type": "EmptyLatentImage",
              "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["5", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["5", 0]}},
        "9": {"class_type": "KSampler",
              "inputs": {"seed": int(seed), "steps": int(k.get("steps", 30)),
                         "cfg": float(k.get("cfg", 5.0)),
                         "sampler_name": k.get("sampler", "er_sde"),
                         "scheduler": k.get("scheduler", "simple"), "denoise": 1.0,
                         "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                         "latent_image": ["8", 0]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["11", 0]}},
        "11": {"class_type": "VAELoader", "inputs": {"vae_name": k.get("vae")}},
        "12": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["10", 0]}},
    }
    if k.get("lora"):
        g["2"] = {"class_type": "LoraLoaderModelOnly",
                  "inputs": {"model": ["1", 0], "lora_name": k["lora"],
                             "strength_model": float(k.get("lora_strength", 1.0))}}
    if k.get("lora2"):
        g["3"] = {"class_type": "LoraLoaderModelOnly",
                  "inputs": {"model": ["2", 0] if k.get("lora") else ["1", 0],
                             "lora_name": k["lora2"],
                             "strength_model": float(k.get("lora2_strength", 1.0))}}
    g["4"] = {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": model, "shift": float(k.get("shift", 3.0))}}
    if image:   # 图生图：空画布换成参考图（VAE 是 "11"）
        _i2i_nodes(g, "8", "9", ["11", 0], image, width, height, denoise,
                   ("81", "82", "83"))
    return g


# ---------------- 工作流库：直接用主人自己存在 ComfyUI 里的 workflow 文件 ----------------
# 主人 2026-09-13 要的：「能在界面自由选 ComfyUI 里的工作流，而不是内置那两个」。
# 目录里存的是 ComfyUI 的「UI 格式」（nodes + links + widgets_values），
# 而 /prompt 只吃「API 格式」（{节点id: {class_type, inputs}}），这里负责转换 + 注入。
#
# 转换规则（照 ComfyUI 前端的做法，2026-09-13 在这台机器上逐个文件实测出来的）：
#   * 控件值优先用 widgets_values_named；没有就按 inputs 数组里带 widget 的那几项顺序
#     把 widgets_values 一个个对上（前端就是按这个顺序存的）。
#   * 前端自己塞的 control_after_generate / "Update inputs" 不是节点输入，必须滤掉，
#     带上会被服务端判 unknown input。滤法 =「不在控件顺序里，且点号前缀也不在」。
#   * mode=2（静音）整节点丢；mode=4（旁路）把下游改成接它同类型的对应输入；
#     MarkdownNote / GetNode / SetNode 这些前端虚拟节点在这一层消化掉。
#   * 只保留通向「第一个图片输出」的那条支路：图小、提交快、结果确定，
#     也顺手解决了「二采工作流里哪个采样器算数」的问题。
WF_IMG_OUT = ("SaveImage", "PreviewImage", "SaveImageWebsocket")
WF_VID_OUT = ("SaveVideo", "SaveWEBM", "VHS_VideoCombine", "CreateVideo", "SaveAudio",
              "SaveAnimatedWEBP")
WF_SAMPLERS = ("KSampler", "KSamplerAdvanced", "KSampler (Efficient)",
               "KSamplerAdvanced (Efficient)", "SamplerCustom", "SamplerCustomAdvanced",
               "DPMSolverSampler", "SamplerCustom (Efficient)")
# 前端虚拟节点 / 纯展示节点：服务端根本没有这些类，别拿它们报「缺节点」
WF_FRONT_ONLY = {"MarkdownNote", "Note", "GetNode", "SetNode",
                 "Fast Groups Bypasser (rgthree)", "Fast Groups Muter (rgthree)"}
# 文本容器（纯 STRING 直通，只有一个字符串控件 + 一个 prompt 输出）：
# 服务端多半没装这个类（2026-09-13 主人的 Anima 工作流用了 Comfyroll 的 CR Prompt Text，
# /prompt 直接 400 missing_node_type），但它只是存一段字 —— 转换时把字面值内联进下游
# STRING 输入，节点本身从图里消失。只准加实测过「纯直通」的类，别把有逻辑的节点放进来。
WF_TEXT_HOLDER = {"CR Prompt Text"}
# cg-use-everywhere 的广播节点（Anything Everywhere 系列）：前端专属，按前缀认
WF_FRONT_PREFIX = ("Anything Everywhere",)


def _wf_front_only(t):
    t = str(t or "")
    return t in WF_FRONT_ONLY or t.startswith(WF_FRONT_PREFIX)
WF_NOT_INPUT = {"control_after_generate", "Update inputs"}
WF_CTRL_WORDS = ("randomize", "fixed", "increment", "decrement")
WF_SEEDY = re.compile(r"seed", re.I)
# 预检「必填输入有没有值」时认的类型：控件型 + 标准连线型
WF_CHECK_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "MODEL", "CLIP", "VAE", "LATENT",
                  "IMAGE", "MASK", "CONDITIONING", "CONTROL_NET", "GUIDER", "SAMPLER",
                  "SIGMAS", "NOISE", "AUDIO", "VIDEO", "LORA_STACK", "UPSCALE_MODEL",
                  "CLIP_VISION", "STYLE_MODEL", "TRANSFORMER", "LORA_MODEL"}
# 能被我们当成「提示词格」改写的输入名。text/prompt 是文本格本身，链着也照改
# （Anima 工作流的正向是 JoinStringMulti 拼出来的，只能从它下游那个 CLIPTextEncode 落笔）。
WF_TEXT_KEYS = ("text", "prompt", "string")
# 「提示词面板」类节点：它们的正向文本槽**不叫** text/prompt，而叫 `positive`，
# 所以上面的 WF_TEXT_KEYS 认不出来（2026-10-06 实测：`Krea2_一些二采.json` 里
# 采样器的 positive 来自 `#287 WeiLinPromptUI`，bot 报「找不到正向提示词的落点」，
# 整段描述进不去 ⇒ 画出来不相干）。
# ⚠️ 这类面板的 `positive` 槽**经常被连线占着**（主人用 `#288 DanbooruGalleryNode`
#    取图 → `#263 ImageCaptionNode` 反推 → 喂进面板当提示词），所以链着也要认 ——
#    反推链切口会把上游内联掉，我们随后整体覆写成 bot 的提示词。
# ⚠️ 别把 `positive` 并进 WF_TEXT_KEYS：别的节点的 positive 是 CONDITIONING（不是文本），
#    一律当文本槽会写坏图。只认名单里的面板类。
WF_PROMPT_PANEL = ("WeiLinPromptUI",)
WF_LOAD_IMAGE = ("LoadImage", "LoadImageMask", "LoadImageOutput", "LoadVideo",
                 "VHS_LoadVideo", "LoadImageFromUrl")
# 存图节点的名字各家不一样（主人的机甲工作流用 ZML_SaveImageV2 / SaveToGallery），
# 所以不认名字，认「没人接的终端 + 有 IMAGE 输入」，名字只用来排优先级。
WF_OUT_SAVE = re.compile(r"save[_ ]?image|saveimage", re.I)
WF_OUT_GALLERY = re.compile(r"save[_ ]?gallery|gallery[_ ]?save", re.I)
WF_OUT_BAD = re.compile(r"compare|comparer|showtext|showanything|previewany|maskpreview", re.I)


def wf_is_store_out(cls):
    """真「存图」节点（SaveImage / ZML_SaveImageV2 / SaveToGallery…）—— 区别于
    PreviewImage / Image Comparer 这类**展示型**节点。

    ⚠️ 为什么要单独认：判定「谁是出图节点」时，旧逻辑是「节点没有下游才算终端」，
    但主人**常把 SaveImage 的输出再接给 Image Comparer 做对比**
    （实测 2026-10-06 `Krea2_一些二采.json`：#220 SaveImage → #277 Comparer）。
    那样 SaveImage 就成了「有下游」，被一刀切排除掉 —— 而 SaveImage 和 PreviewImage
    的优先级**都是 0**，于是文件里更靠后的旁路 `PreviewImage` 胜出，那条链上没有采样器
    ⇒ 空壳提交、把 ComfyUI `input/` 里的残留图发回给主人。
    **存图节点有没有下游都照样会存图**，所以它不该参与「有下游就排除」。
    """
    c = str(cls or "")
    return c in WF_IMG_OUT and "Preview" not in c or bool(
        WF_OUT_SAVE.search(c) or WF_OUT_GALLERY.search(c))
# 放大类节点（skip_upscale 时的跳过目标）：这类分块重采样在 8G 卡上极慢且容易拖崩服务
WF_UPSCALER = ("UltimateSDUpscale", "UltimateSDUpscaleCustomScript",
               "UltimateSDUpscaleNoUpscale")
# 负向天生就是空的（ConditioningZeroOut 这类）：这时候不报「没找到负向编码器」
WF_ZERO_COND = ("ConditioningZeroOut", "ConditioningSetTimestepRange", "ConditioningCombine",
                "ConditioningAverage", "ConditioningConcat", "ConditioningSetArea")
# 现画只收录哪些工作流文件（2026-09-13 主人定：只用两个档 —— 内置 krea2 +
# Anima工作流这一个文件档，目录里其它 .json（视频类、实验类）不长成可选档）。
# config 写 comfyui.wf_allow 可改（列表或逗号分隔串）；显式给 [] = 不限制全收录。
WF_ALLOW_DEFAULT = ["Anima工作流"]
# 文本槽编辑器跳过的类：正/负向编码器的文本归引擎管（注入点会整体替换它们的 text），
# 拿出来让主人编辑会被静默顶掉，干脆不列。
WF_SLOT_SKIP = re.compile(r"CLIPTextEncode", re.I)


def wf_has_sampler(g):
    """这份 API 图里到底有没有采样器（KSampler / KSamplerAdvanced / SamplerCustom…）。

    **存在的意义是拦住「空壳提交」**（2026-10-06 主人踩到的真事故）：
    动漫档被配成 `Krea2_一些二采.json`，而它真正出图的那个 SaveImage 在 ComfyUI
    界面里是**静音（mode=4）**的 —— bot 的输出节点选择会跳过 mode=2/4，于是选中了
    另一条 `LoadImage → SaveImage` 的直通支路。提交上去只有 2 个节点、不跑采样、
    提示词也没落点，结果就是把 ComfyUI `input/` 里**上一张残留图**原样存出来发给了主人
    （日志：`出图完成 …（1.5s）` + `["LoadImage", "SaveImage"]`）。
    ⇒ 没有采样器就别提交，直接报错，**宁可画不出也不能发一张不相干的图**。
    """
    for v in (g or {}).values():
        if isinstance(v, dict) and "Sampler" in str(v.get("class_type") or ""):
            return True
    return False


def wf_allow(cfg):
    """收录白名单：config comfyui.wf_allow（列表或逗号分隔串）；没写就用默认俩字。"""
    a = cfg.get("wf_allow")
    if a is None:
        return list(WF_ALLOW_DEFAULT)
    if isinstance(a, str):
        a = re.split(r"[,，、\n]+", a)
    return [str(x).strip() for x in (a or []) if str(x).strip()]


def wf_texts(cfg):
    """config comfyui.wf_texts：{工作流节点标题: 文本} 覆盖表（脏数据过滤）。"""
    t = cfg.get("wf_texts")
    if not isinstance(t, dict):
        return {}
    return {str(k).strip(): str(v) for k, v in t.items() if str(k).strip()}


def wf_dirs(cfg):
    """工作流目录列表：显式配置的目录 + 默认 user/default/workflows + 外部 Styles 目录。
    2026-10-15 优化：主人的工作流同时分布在秋叶整合包的 workflows 与 Styles 两个目录，
    自动聚合扫描，避免用户选工作流时找不到或报目录匹配不上。"""
    dirs = []
    d = str(cfg.get("workflow_dir") or "").strip()
    if d:
        p = Path(d)
        if p.is_dir() and p not in dirs:
            dirs.append(p)
    # 默认 workflows 目录
    def_dir = Path(str(cfg.get("comfy_dir") or ".")) / "user" / "default" / "workflows"
    if def_dir.is_dir() and def_dir not in dirs:
        dirs.append(def_dir)
    # Styles 目录（秋叶包安装目录的上级 Styles）
    styles_cands = [
        Path(str(cfg.get("comfy_dir") or ".")).parent.parent / "Styles",
    ]
    for sc in styles_cands:
        if sc.is_dir() and sc not in dirs:
            dirs.append(sc)
    return dirs or [def_dir]


def wf_dir(cfg):
    """主工作流目录：兼容旧调用。"""
    return wf_dirs(cfg)[0]


def _wf_paths(d):
    """扫描指定目录或全局聚合目录下的所有非备份 json 工作流。"""
    if isinstance(d, (list, tuple)):
        all_dirs = d
    elif isinstance(d, dict):
        all_dirs = wf_dirs(d)
    else:
        all_dirs = [Path(d)]
    
    seen = {}
    for one_d in all_dirs:
        one_d = Path(one_d)
        if not one_d.is_dir():
            continue
        for p in one_d.rglob("*.json"):
            if p.name.startswith("."):
                continue
            if re.search(r"\.bak|\.backup|\.old", p.name, re.I):
                continue
            if p.name not in seen:
                seen[p.name] = p
    return sorted(seen.values(), key=lambda p: (p.name.startswith("."), str(p).lower()))


def wf_allow(cfg):
    """收录白名单：config comfyui.wf_allow（列表或逗号分隔串）；未配置时默认空（文件档休眠）。"""
    a = cfg.get("wf_allow")
    if a is None:
        return []
    if isinstance(a, str):
        a = re.split(r"[,，、\n]+", a)
    return [str(x).strip() for x in (a or []) if str(x).strip()]


def wf_library(cfg):
    """工作流库：目录里能当「文件档」用的 (档名=stem 去空白, 文件名) 列表。
    跳过隐藏文件和 *.backup-*.json 这类备份；
    只收录 wf_allow 白名单里的档（未配置时默认休眠，保持内置三档定稿原则）。"""
    allow = wf_allow(cfg)
    if not allow:
        return []
    out = []
    for p in _wf_paths(wf_dirs(cfg)):
        stem = p.stem.strip()
        if not stem or re.search(r"\.bak|\.backup|\.old", p.name, re.I):
            continue
        if stem in allow or p.name in allow:
            out.append((stem, p.name))
    return sorted(set(out), key=lambda t: t[0].lower())


def wf_virtual(cfg, name):
    """工作流库里有没有这个名字的文件档：有就给一份虚拟档位字典（不进 config）。
    est/timeout 先按保守值报；**skip_upscale 默认开**——工作流尾巴上的 UltimateSDUpscale
    分块重采样在 8G 卡上又慢（5 分钟起）又把 ComfyUI 拖崩过（2026-09-13 实测），
    档位同名键写 skip_upscale:false 可以开回来。其他项同名键也都能覆盖。"""
    want = str(name or "").strip()
    for stem, fname in wf_library(cfg):
        if stem == want:
            return {"engine": "wf", "workflow": fname,
                    "est_sec": 120, "est_cold_sec": 240, "timeout_sec": 900,
                    "skip_upscale": True}
    return {}


def style_dict(cfg, name=None):
    """把一个档名解析成完整字典：config styles 里同名键的值 > 工作流库虚拟档 > 空。
    （显式声明永远赢过虚拟档 —— 想给某个文件档定制润色口味就写 enhance_style。）"""
    styles = cfg.get("styles") or {}
    n = str(name or cfg.get("default_style") or "krea2").strip()
    s = dict(wf_virtual(cfg, n))
    s.update(styles.get(n) or {})
    return s


def wf_load(cfg, name):
    """按档名 / 文件名 / 相对路径找一份工作流，返回 (路径, 文档)。找不到就抛。
    2026-10-15 优化：在所有工作流聚合目录下寻找，无论保存在哪个目录都能准确定位。"""
    dirs = wf_dirs(cfg)
    want = str(name or "").strip()
    if not want:
        raise ComfyError("「%s」这档没写要跑哪个工作流文件" % want)
    cands = [Path(want)]
    for d in dirs:
        cands.extend([d / want, d / (want + ".json")])
    hit = next((p for p in cands if p.is_file()), None)
    if hit is None:
        all_paths = _wf_paths(dirs)
        hit = next((p for p in all_paths if p.stem == want or p.name == want), None)
    if hit is None:
        have = "、".join(p.stem for p in _wf_paths(dirs)) or "（空）"
        raise ComfyError("工作流目录里没有 %r。搜索目录：%s，现有：%s" % (want, " + ".join(str(d) for d in dirs), have[:200]))
    try:
        doc = json.loads(Path(hit).read_text(encoding="utf-8"))
    except Exception as e:
        raise ComfyError("读不了工作流 %s：%s" % (Path(hit).name, str(e)[:120]))
    if "nodes" not in doc:
        # API 格式直通（2026-10-02）：{节点: {class_type, inputs}}，主人界面跑通后导出的图
        vals = [v for v in doc.values() if isinstance(v, dict)]
        if vals and all("class_type" in v for v in vals):
            return Path(hit), doc
        raise ComfyError("%s 不是 UI 格式的工作流（得是 ComfyUI 里存出来的那种）" % Path(hit).name)
    return Path(hit), doc


def _wf_links(doc):
    """links 拆成 {link_id: (源节点, 源槽)}；列表和字典两种写法都认。"""
    out = {}
    for l in doc.get("links") or []:
        if isinstance(l, dict):
            if l.get("id") is not None:
                out[l["id"]] = (l.get("origin_id"), l.get("origin_slot"))
        elif isinstance(l, (list, tuple)) and len(l) >= 3:
            out[l[0]] = (l[1], l[2])
    return out


def _wf_widget_slots(n):
    """这个节点的控件位：[（名字, 类型）]，顺序就是 widgets_values 的顺序。"""
    return [(e.get("name"), e.get("type")) for e in (n.get("inputs") or [])
            if isinstance(e.get("widget"), dict) and e.get("name")]


# 文本槽认这些控件名：text/prompt/string（含 string_N）。delimiter 这类结构性格不算
WF_SLOT_NAMES = ("text", "prompt", "string")


def _wf_text_slot_name(n):
    """这个节点有没有「空着的文本格」（没接线的 STRING 控件）：有就返回控件名。
    接了线的 STRING（如正向 CLIPTextEncode.text）不算 —— 那是给注入点替换的；
    控件名也得像内容格（text/prompt/string_N），拼节点的 delimiter 不算。"""
    for e in (n.get("inputs") or []):
        wn = str(e.get("name") or "").lower()
        if isinstance(e.get("widget"), dict) and e.get("name") \
                and e.get("link") is None \
                and str(e.get("type") or "").upper() in ("STRING", "TEXT") \
                and (wn in WF_SLOT_NAMES or wn.startswith("string_")):
            return e["name"]
    return None


def _wf_type_ok(t, v):
    """值跟声明类型搭不搭（拿不准的自定义类型一律放过）。"""
    t = str(t or "").upper()
    if t == "BOOLEAN":
        return isinstance(v, bool)
    if t == "INT":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "FLOAT":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "STRING" or "TEXT" in t:
        return isinstance(v, str)
    if t in ("MODEL", "CLIP", "VAE", "LATENT", "IMAGE", "MASK", "CONDITIONING", "LORA_STACK",
             "CONTROL_NET", "GUIDER", "SAMPLER", "SIGMAS", "NOISE", "AUDIO", "VIDEO"):
        return False
    return True


def _wf_unzip(order, values):
    """控件顺序 × 值列表。两个坑（2026-09-13 实测）：
    * 前端的 control_after_generate 是插在 seed 后面的一格、不在顺序里，不跳过的话
      从它往后全部错位（旧文件没有 named 映射，全靠这一步）。
    * 有些节点（LoraManager）会在值列表最前面塞一个自己的 meta 字典，按类型挑一下跳过。
    """
    out, i = {}, 0
    for name, typ in order:
        while i < len(values) and not _wf_type_ok(typ, values[i]):
            i += 1
        if i >= len(values):
            break
        out[name] = values[i]
        i += 1
        if WF_SEEDY.search(str(name)) and i < len(values) \
                and isinstance(values[i], str) and values[i].lower() in WF_CTRL_WORDS:
            i += 1
    return out


def _wf_base_inputs(n):
    """控件值 → inputs 字典（这一步不碰连线，连线在后面覆盖）。

    list/dict 值必须包一层 {"__value__": …}：API 定式里「list 就是连线 [节点, 槽]」，
    直接送数组会被判 bad_linked_input；ComfyUI 的 execution.py 见到 __value__ 才解开
    （LoraManager 的 loras 就是这么传的 —— 2026-09-13 从节点源码 + execution.py 确认）。
    """
    slots = _wf_widget_slots(n)
    order = [s[0] for s in slots]
    named = n.get("widgets_values_named")
    if isinstance(named, dict) and named:
        base = dict(named)
        if order:
            base = {k: v for k, v in base.items()
                    if k in order or str(k).split(".")[0] in order}
        else:
            base = {k: v for k, v in base.items() if k not in WF_NOT_INPUT}
    elif slots and isinstance(n.get("widgets_values"), (list, tuple)):
        base = _wf_unzip(slots, list(n.get("widgets_values")))
    else:
        base = {}
    out = {}
    for k, v in base.items():
        if isinstance(v, (list, dict)) and not (isinstance(v, dict) and "__value__" in v):
            v = {"__value__": v}
        out[k] = v
    return out


def _wf_const(n):
    """GetNode/SetNode 的变量名（就是那格 Constant 文本）。"""
    named = n.get("widgets_values_named")
    if isinstance(named, dict) and named.get("Constant"):
        return str(named["Constant"])
    for v in (n.get("widgets_values") or []):
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _wf_link(v, g=None):
    """这一格是不是一条连线；是就返回源节点 id。"""
    if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], str):
        return v[0] if (g is None or v[0] in g) else None
    return None


def _wf_upstream(g, seeds):
    """从这些节点往上走，返回所有祖先 id（离种子近的在前）。"""
    seen, q, out = set(), [], []
    for s in seeds:
        if s in g and s not in seen:
            seen.add(s)
            q.append(s)
    while q:
        cur = q.pop(0)
        for v in (g.get(cur, {}).get("inputs") or {}).values():
            nid = _wf_link(v, g)
            if nid is not None and nid not in seen:
                seen.add(nid)
                out.append(nid)
                q.append(nid)
    return out


def _wf_text_value(n):
    """文本格的当前值：named 命名值优先，位置值兜底（跟 _wf_base_inputs 同一判法）。
    容器内联（wf_to_api 的 holders）也用它取值，保证「命名值改了、位置值没改」的
    新前端工作流（2026-09-13 主人的 Anima 工作流就是这样）不会拿旧值。"""
    name = _wf_text_slot_name(n)
    if not name:
        return None
    named = n.get("widgets_values_named")
    if isinstance(named, dict) and isinstance(named.get(name), str):
        return named[name]
    slots = _wf_widget_slots(n)
    vals = n.get("widgets_values")
    if not slots or not isinstance(vals, (list, tuple)):
        return None
    i = 0
    for wn, wt in slots:
        while i < len(vals) and not _wf_type_ok(wt, vals[i]):
            i += 1
        if i >= len(vals):
            return None
        if wn == name:
            return vals[i] if isinstance(vals[i], str) else None
        i += 1
    return None


def _wf_apply_texts(nodes, texts):
    """把 {节点标题: 文本} 写进 UI 节点的控件值。必须在转 API 之前做：文本容器
    （CR Prompt Text）的内联、下游拼接链全走正常路径，不会留悬空节点。
    返回真正改到的标题集合（调用方好知道哪个标题没对上节点）。"""
    done = set()
    if not isinstance(texts, dict) or not texts:
        return done
    for n in nodes:
        t = str(n.get("title") or "").strip()
        v = texts.get(t)
        if t not in texts or not isinstance(v, str):
            continue
        name = _wf_text_slot_name(n)
        if not name:
            # 2026-10-16 宫格档：ZML/WeiLin 系节点的文本控件叫「文本」（**中文**名），
            # 英文名白名单认不出来 ⇒ 退一步：认「带 widget、没接线、widgets_values_named 里
            # 正好存着同名字符串」的那一个。只在**写入**这一步放宽，`_wf_prompt_slot` 的
            # 描述格自动识别仍走老规则（免得别的档位的自动落点被带跑偏）。
            named0 = n.get("widgets_values_named")
            if isinstance(named0, dict):
                for e in (n.get("inputs") or []):
                    nm = e.get("name")
                    if isinstance(e.get("widget"), dict) and nm and e.get("link") is None \
                            and str(e.get("type") or "").upper() in ("STRING", "TEXT") \
                            and isinstance(named0.get(nm), str):
                        name = nm
                        break
        if not name:
            continue
        named = n.get("widgets_values_named")
        if isinstance(named, dict) and name in named:      # 新式命名值：直接改
            named[name] = v
            done.add(t)
            continue
        slots = _wf_widget_slots(n)
        vals = n.get("widgets_values")
        if not slots or not isinstance(vals, (list, tuple)):
            continue
        vals = list(vals)
        i = 0
        for wn, wt in slots:                               # 老式位置值：照 unzip 的跳法找格
            while i < len(vals) and not _wf_type_ok(wt, vals[i]):
                i += 1
            if i >= len(vals):
                break
            if wn == name:
                vals[i] = v
                n["widgets_values"] = vals
                done.add(t)
                break
            i += 1
    return done


def wf_to_api(doc, texts=None, keep_expand=False):
    """UI 格式 → API 格式。texts 给 {节点标题: 文本} 时先覆盖对应文本格。返回 (graph, report)。

    ⚠️ `keep_expand=True`（2026-10-16 宫格档加）：**别裁掉反推/扩写链**。
    默认行为会把来自 ImageCaptionNode/PromptExpand 的 STRING 入口内联空串，
    可有些工作流的扩写链是**故意要跑的**（宫格档：用户一句话 → LLM 写小故事 →
    LLM 编成英文宫格漫画提示词 → CLIP），裁了就只剩干巴巴的文生图。
    """
    # API 格式直通（2026-10-02）：主人在界面跑通后导出的 {节点: {class_type, inputs}} 图，
    # 参数原样可信（时长/帧率等都是主人调好的），bot 只替换提示词/参考图，不再转 UI。
    if isinstance(doc, dict) and not isinstance(doc.get("nodes"), list):
        vals = [v for v in doc.values() if isinstance(v, dict)]
        if vals and all("class_type" in v for v in vals):
            kinds = {v.get("class_type") for v in vals}
            kind = "video" if "VHS_VideoCombine" in kinds else (
                "image" if "SaveImage" in kinds else "none")
            return (doc, {"nodes": len(vals), "kind": kind, "output": None,
                          "image_outs": [], "video_outs": [], "in_types": {},
                          "api_format": True})
    nodes = [n for n in (doc.get("nodes") or [])
             if isinstance(n, dict) and n.get("id") is not None]
    by_id = {n["id"]: n for n in nodes}
    _wf_apply_texts(nodes, texts)
    lk = _wf_links(doc)
    drop, alias = set(), {}

    for n in nodes:
        if _wf_front_only(n.get("type")) or n.get("mode") == 2:
            drop.add(n["id"])

    # SetNode 定义变量，GetNode 取回来 —— 换成同一条连线
    def_var = {}
    for n in nodes:
        if n.get("type") != "SetNode":
            continue
        key = _wf_const(n)
        cands = [(e.get("name"), e.get("type"), e.get("link")) for e in (n.get("inputs") or [])]
        # pysssss 的 SetNode 输入名跟着类型走（新版本叫 "value"，机甲工作流写的是 "STRING"）
        # —— 只认 "value" 会让配对的 Get 解析成空串（2026-09-16 实测：提示词全丢）。
        pick = next((l for nm, ty, l in cands if nm == "value" and l in lk), None)
        if pick is None:
            pick = next((l for nm, ty, l in cands
                         if l in lk and ty and str(nm or "") != "anything"), None)
        if pick is not None:
            def_var[key] = tuple(lk[pick])
    for n in nodes:
        if n.get("type") == "GetNode":
            src = def_var.get(_wf_const(n))
            if src:
                alias[(n["id"], 0)] = src

    # 旁路（mode=4）：每个输出槽改接「同类型的第 i 个输入」
    for n in nodes:
        if n.get("mode") != 4:
            continue
        outs, ins = n.get("outputs") or [], n.get("inputs") or []
        for oslot, o in enumerate(outs):
            t = o.get("type")
            i = len([x for x in outs[:oslot + 1] if x.get("type") == t]) - 1
            cands = [e for e in ins if e.get("type") == t and e.get("link") in lk]
            if i < len(cands):
                alias[(n["id"], oslot)] = tuple(lk[cands[i]["link"]])

    def resolve(src, seen=None):
        """沿旁路/虚拟节点一路解开，拿到真正的产出节点；解不开返回 None。"""
        seen = seen if seen is not None else set()
        if not src or len(src) < 2:
            return None
        key = (src[0], int(src[1] or 0))
        if key in seen:
            return None
        if src[0] in drop:
            seen.add(key)
            nxt = alias.get(key)
            return resolve(nxt, seen) if nxt else None
        return key if src[0] in by_id else None

    # Anything Everywhere（cg-use-everywhere）：前端在提交前会把它的值广播到所有
    # 「同类型、没接线、又不是它自己上游」的输入上 —— 机甲工作流的 VAEDecode.vae
    # 就是这么来的。这里照做，不然必填输入会缺，服务端直接判错。
    fill = {}
    ae = [n for n in nodes
          if str(n.get("type") or "").startswith(WF_FRONT_PREFIX) and n.get("mode") == 0]
    if ae:
        up = {}
        for nid in by_id:
            cur, stack = set(), [nid]
            while stack:
                c = stack.pop()
                for e in (by_id.get(c, {}).get("inputs") or []):
                    lid = e.get("link")
                    if lid in lk and lk[lid] and lk[lid][0] not in cur:
                        cur.add(lk[lid][0])
                        stack.append(lk[lid][0])
            up[nid] = cur
        for a in ae:
            for e in (a.get("inputs") or []):
                lid = e.get("link")
                t = e.get("type")
                if lid is None or lid not in lk or not t or t == "*":
                    continue
                src = tuple(lk[lid])
                for m in nodes:
                    if m["id"] in drop or m["id"] == src[0] or src[0] in up.get(m["id"], ()):
                        continue
                    for me in (m.get("inputs") or []):
                        if me.get("link") is None and not me.get("widget") \
                                and me.get("type") == t and me.get("name"):
                            fill[(m["id"], me["name"])] = src

    g = {}
    in_types = {}
    # 文本容器：字面值登记进来，下面接线时内联，节点本身不进图
    holders = {}
    for n2 in nodes:
        if str(n2.get("type") or "") in WF_TEXT_HOLDER and n2.get("mode") in (0, None):
            v = _wf_text_value(n2)
            if v is None:                       # 老兜底：位置值第一格
                wv = n2.get("widgets_values") or []
                if wv and isinstance(wv[0], str):
                    v = wv[0]
            if v is not None:
                holders[n2["id"]] = v
    for n in nodes:
        if n["id"] in drop or n["id"] in holders or not n.get("type"):
            continue
        inp = _wf_base_inputs(n)
        for e in (n.get("inputs") or []):
            lid = e.get("link")
            src = resolve(tuple(lk[lid])) if (lid is not None and lid in lk) else None
            if src is None and (n["id"], e.get("name")) in fill:
                src = resolve(fill[(n["id"], e.get("name"))])
            if src:
                if e.get("type") == "STRING" and src[0] in holders:
                    inp[e["name"]] = holders[src[0]]     # 文本容器：内联字面值
                else:
                    inp[e["name"]] = [str(src[0]), int(src[1])]
        g[str(n["id"])] = {"class_type": n["type"], "inputs": inp}
        in_types[str(n["id"])] = {e.get("name"): e.get("type")
                                  for e in (n.get("inputs") or []) if e.get("name")}

    # 输出节点 = 没人接的终端支路。不认死 SaveImage 这个名字：主人的机甲工作流存图
    # 走的是 ZML_SaveImageV2 / SaveToGallery，按类型认才认得出来。
    # ⚠️ 但**真存图节点不参与「有下游就排除」**（见 `wf_is_store_out`）——
    #    主人常把 SaveImage 再接给 Image Comparer 做对比，一刀切会把唯一正常的
    #    存图节点排除掉，反而选中旁路的 PreviewImage（那条链上没有采样器）。
    has_out = {v[0] for v in lk.values() if v and v[0] is not None}
    imgs, vids = [], []
    for idx, n in enumerate(nodes):
        nid = str(n["id"])
        if n.get("mode") in (2, 4) or nid not in g:
            continue
        cls = str(n.get("type") or "")
        if n["id"] in has_out and not wf_is_store_out(cls):
            continue
        # 2026-10-01：新版前端会把下拉输入的 type 序列化成列表（VHS_VideoCombine 的
        # pix_fmt = ["yuv420p","yuv420p10le"]），直接进 set 会 TypeError unhashable——只认字符串。
        types = {e.get("type") for e in (by_id[n["id"]].get("inputs") or [])
                 if isinstance(e.get("type"), str)}
        if cls in WF_VID_OUT or (types & {"VIDEO", "AUDIO"}):
            vids.append((0 if cls in WF_VID_OUT else 1, idx, nid))
            continue
        if not (types & {"IMAGE"}):
            continue
        if cls in WF_IMG_OUT:
            pri = 0
        elif WF_OUT_SAVE.search(cls):
            pri = 1
        elif WF_OUT_GALLERY.search(cls):
            pri = 2
        elif WF_OUT_BAD.search(cls):
            pri = 4
        else:
            pri = 3
        imgs.append((pri, idx, nid))
    imgs.sort()
    vids.sort()
    imgs = [x[2] for x in imgs]
    vids = [x[2] for x in vids]
    kind = "image" if imgs else ("video" if vids else "none")
    if kind == "none":      # 终端支路没认出来也别乱下结论：有视频类节点就按视频报
        if any(v.get("class_type") in WF_VID_OUT for v in g.values()):
            kind = "video"
    # 2026-10-01 修 Amina_VRC「图不对题」：拼接链上其他 string 入口若来自
    # 「图像反推/LLM 扩写」节点（ImageCaptionNode/PromptExpand，吃 LoadImage 里
    # 主人手跑的残留图），残留图的反推描述会整段拼进提示词，把用户描述淹没
    # （实测：描述画猫，出图是残留图里的大毛衣女孩）。bot 出图路径（texts 非 None）
    # 一律把这些 string 入口内联空串 —— 反推链随后不再是输出祖先，被下面剪枝
    # 自然丢弃；主人在 ComfyUI 界面手跑不经过 wf_to_api，不受影响。
    # ⚠️ 档位声明 keep_expand（宫格档）时**整段跳过**：那条扩写链就是提示词本体，不能裁。
    if texts and not keep_expand:
        _CUT_MARKS = {"ImageCaptionNode", "PromptExpand"}

        def _has_cut_up(nid, seen):
            if nid in seen or nid not in g:
                return False
            seen.add(nid)
            if g[nid].get("class_type") in _CUT_MARKS:
                return True
            return any(_has_cut_up(str(v[0]), seen)
                       for v in (g[nid].get("inputs") or {}).values()
                       if isinstance(v, (list, tuple)) and len(v) == 2)

        for _nid in list(g):
            for _k, _v in list((g[_nid].get("inputs") or {}).items()):
                if not (isinstance(_v, (list, tuple)) and len(_v) == 2):
                    continue
                if str(in_types.get(_nid, {}).get(_k, "")).upper() not in ("STRING", "TEXT"):
                    continue
                if _has_cut_up(str(_v[0]), set()):
                    g[_nid]["inputs"][_k] = ""
                    log.info("工作流转换：#%s.%s 来自反推/扩写链 -> 内联空串（防残留图污染提示词）",
                             _nid, _k)
    out_id = imgs[0] if imgs else (vids[0] if vids else None)
    if out_id is not None:
        keep = set([out_id]) | set(_wf_upstream(g, [out_id]))
        g = {k: v for k, v in g.items() if k in keep}
    return g, {"nodes": len(nodes), "kind": kind, "output": out_id,
               "image_outs": imgs, "video_outs": vids, "in_types": in_types,
               "dropped": sorted(str(x) for x in drop)}


def wf_points(g, out_id=None):
    """在图里认注入点：采样器 / 正向 / 负向 / 尺寸 / 种子 / 文件名前缀。"""
    def src_of(nid, key):
        return _wf_link((g.get(nid, {}).get("inputs") or {}).get(key), g)

    def text_of(nid):
        if nid not in g or g[nid].get("class_type") in WF_SAMPLERS:
            return None
        inp = g[nid].get("inputs") or {}
        for k in WF_TEXT_KEYS:
            if k not in inp:
                continue
            if k in ("text", "prompt"):
                return [nid, k]        # 这俩天生是文本格，链着也照改
            if isinstance(inp[k], str):
                return [nid, k]
        # 提示词面板（WeiLinPromptUI 这类）的正向槽叫 `positive`，链着也认（见常量注释）
        if str(g[nid].get("class_type") or "") in WF_PROMPT_PANEL and "positive" in inp:
            return [nid, "positive"]
        return None

    def find_text(seeds, skip):
        for nid in seeds:
            t = None if nid in skip else text_of(nid)
            if t:
                return t
        for nid in seeds:
            if nid not in g:
                continue
            for up in _wf_upstream(g, [nid]):
                if up in skip:
                    continue
                t = text_of(up)
                if t:
                    return t
        return None

    # 采样器：离输出最近的那个（二采工作流里最靠近出图的才是最后一采）
    depth, q = {}, []
    if out_id and out_id in g:
        depth[out_id] = 0
        q = [out_id]
    while q:
        cur = q.pop(0)
        for v in (g.get(cur, {}).get("inputs") or {}).values():
            nid = _wf_link(v, g)
            if nid is not None and nid not in depth:
                depth[nid] = depth[cur] + 1
                q.append(nid)

    def rank(nid):
        inp = g[nid].get("inputs") or {}
        try:
            steps = int(float(inp.get("steps") or inp.get("steps_total") or 0))
        except Exception:
            steps = 0
        return (depth.get(nid, 999), -steps, nid)

    samplers = sorted([k for k, v in g.items() if v.get("class_type") in WF_SAMPLERS], key=rank)
    sid = samplers[0] if samplers else None
    pts = {"sampler": sid, "positive": None, "negative": None, "size": None,
           "size_preset": None, "seed": None, "prefix": [], "warn": []}
    if sid is None:
        pts["warn"].append("找不到采样器（KSampler 这类），提示词和种子都改不了")
        return pts

    cond = {}
    for which in ("positive", "negative"):      # SamplerCustomAdvanced 把 conditioning 藏在 guider 里
        nid = src_of(sid, which)
        if nid is None:
            gd = src_of(sid, "guider")
            nid = src_of(gd, which) if gd else None
        if nid is not None:
            cond[which] = [nid]

    pts["positive"] = find_text(cond.get("positive") or [], set())
    pos_node = pts["positive"][0] if pts["positive"] else None
    skip = set([pos_node]) | set(_wf_upstream(g, [pos_node])) if pos_node else set()
    pts["negative"] = find_text(cond.get("negative") or [], skip)
    if pos_node and pts["negative"] and pts["negative"][0] == pos_node:
        pts["negative"] = None
        pts["warn"].append("正负向共用一个文本编码器，负向没敢动（改了会顶掉提示词）")
    if pos_node is None:
        pts["warn"].append("找不到正向提示词的落点，这段提示词进不去")
    elif pts["negative"] is None:
        # 负向天生为空（ConditioningZeroOut 这类）不算毛病，别拿它吓人
        chain = cond.get("negative") or []
        chain += _wf_upstream(g, chain)
        if not any(g.get(n, {}).get("class_type") in WF_ZERO_COND for n in chain):
            pts["warn"].append("没找到单独的负向编码器，负向沿用工作流里的")

    lat = src_of(sid, "latent_image") or src_of(sid, "latent")
    for nid in ([lat] if lat else []) + _wf_upstream(g, [lat] if lat else [sid]):
        inp = g.get(nid, {}).get("inputs") or {}
        if "width" in inp and "height" in inp:
            pts["size"] = [nid, "width", "height"]
            break
    if pts["size"] is None:
        # 没有 width/height 格：找「预设名里带尺寸」的那种尺寸格（ZML_PresetResolution）。
        # 它只有一个字符串（"SDXL_768x1344"）+ 一个「互换宽高」开关 —— 档位里写
        # wf_preset / swap_wh 就能改（见 graph_workflow 里的覆盖）。
        for nid in ([lat] if lat else []) + _wf_upstream(g, [lat] if lat else [sid]):
            inp = g.get(nid, {}).get("inputs") or {}
            pk = next((k for k in inp if k in WF_PRESET_KEYS), None)
            if pk and isinstance(inp.get(pk), str) and \
                    re.search(r"\d{3,4}\s*[x×*]\s*\d{3,4}", inp[pk]):
                sk = next((k for k in inp if k in WF_SWAP_KEYS), None)
                pts["size_preset"] = [nid, pk, sk]
                break
    if pts["size"] is None and not pts.get("size_preset"):
        pts["warn"].append("没有空潜空间节点，出图尺寸只能用工作流自己写死的")

    for key in ("seed", "noise_seed"):
        if key in (g[sid].get("inputs") or {}):
            pts["seed"] = [sid, key]
            break
    if pts["seed"] is None:
        nz = src_of(sid, "noise")
        for nid in ([nz] if nz else []) + _wf_upstream(g, [nz] if nz else [sid]):
            inp = g.get(nid, {}).get("inputs") or {}
            for key in ("noise_seed", "seed"):
                if key in inp:
                    pts["seed"] = [nid, key]
                    break
            if pts["seed"]:
                break
    if pts["seed"] is None:
        pts["warn"].append("找不到种子格，每张都会一模一样")

    pts["prefix"] = [k for k, v in g.items() if "filename_prefix" in (v.get("inputs") or {})]
    return pts


WF_EMPTY_LATENT = re.compile(r"empty.*latent|latent.*empty|presetresolution", re.I)
# 尺寸格上「互换宽高」这类开关的控件名（机甲档的 ZML_PresetResolution 把它默认开着了）
WF_SWAP_KEYS = ("互换宽高", "交換寬高", "swap_wh", "swap_width_height", "swap")
# 「预设名里带尺寸」的尺寸格控件名（ZML_PresetResolution 的「预设」= "SDXL_768x1344"，
# 只有字符串没有 width/height，档位用 wf_preset / swap_wh 覆盖它 —— 2026-09-16）
WF_PRESET_KEYS = ("预设", "預設", "preset", "resolution", "分辨率", "解析度", "尺寸")
# 采样后的放大节点：出图尺寸要乘上它（机甲档是 LatentUpscaleBy 1.5 → 二采锐化）
WF_LATENT_UP = ("LatentUpscaleBy", "LatentUpscale", "ImageScaleBy")


def _wf_num(v, g, key, d=1.0):
    """取一个「可能是字面值、也可能是连线」的数字（机甲档的 scale_by 接的是 PrimitiveFloat）。"""
    raw = (v.get("inputs") or {}).get(key)
    if isinstance(raw, bool):
        return d
    if isinstance(raw, (int, float)):
        return float(raw)
    nid = _wf_link(raw, g)
    if nid:
        src = g.get(str(nid), {}).get("inputs") or {}
        for kk in ("value", "FLOAT", "float", "number", "INT", "int"):
            x = src.get(kk)
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                return float(x)
    try:
        return float(raw)
    except Exception:
        return d


def wf_out_scale(g, first_ks=None):
    """采样之后的放大倍数（LatentUpscaleBy 的 scale_by）。没有就 1.0。
    ⚠️ 按「谁接了采样器」认，别按「谁接了一采」—— 参考图注入后一采的 latent 来源
    已经不是空画布了，那样会漏判。"""
    samplers = {k for k, v in (g or {}).items() if v.get("class_type") in WF_SAMPLERS}
    for v in (g or {}).values():
        if v.get("class_type") != "LatentUpscaleBy":
            continue
        src = _wf_link((v.get("inputs") or {}).get("samples"), g)
        if src and str(src) in samplers:
            return _wf_num(v, g, "scale_by", 1.0)
    return 1.0


def wf_first_sampler(g, out_id=None):
    """一采 = 「latent 来源自己造 latent」的那个采样器（空画布 / 分辨率预设节点）。
    工作流档要接参考图就得改它的 latent_image，所以得先认准是哪一个。
    ⚠️ `_wf_link` 返回的是**源节点 id 字符串**，别当元组取 [0]。"""
    for nid, v in (g or {}).items():
        if v.get("class_type") not in WF_SAMPLERS:
            continue
        src = _wf_link((v.get("inputs") or {}).get("latent_image"), g)
        if not src:
            continue
        scls = str((g.get(str(src)) or {}).get("class_type") or "")
        if WF_EMPTY_LATENT.search(scls):
            return nid
    return None


def wf_style_i2i_ok(cfg, s):
    """这份工作流档结构上吃不吃参考图（读文件转换一次判）。异常一律当吃不了。"""
    wf = str((s or {}).get("workflow") or "")
    if not wf:
        return False
    try:
        _p, doc = wf_load(cfg, wf)
        g, _rep = wf_to_api(doc)
    except Exception:
        return False
    return wf_i2i_capable(g)


def wf_i2i_capable(g):
    """这张 API 图能不能塞参考图：一采自产 latent，或「编辑流」——LoadImage 直接喂
    扩散链、latent 由编辑器节点自产（Qwen-Edit/Kontext 漫转写实这类），换图即可。"""
    if not g:
        return False
    if not any(str(v.get("class_type") or "") == "VAELoader" for v in g.values()):
        return False
    if wf_first_sampler(g):
        return True
    has_load = any(str(v.get("class_type") or "") in WF_LOAD_IMAGE for v in g.values())
    has_ks = any(v.get("class_type") in WF_SAMPLERS for v in g.values())
    return bool(has_load and has_ks)


def wf_inject_image(g, image, denoise=0.6, w=0, h=0):
    """把参考图塞进工作流图：LoadImage → ImageScale → VAEEncode → 一采的 latent_image。

    跟内置引擎的 `_i2i_nodes` 一个思路，区别是**一采是认出来的**（latent 来源自己造
    latent 的那个采样器），不写死节点号。

    denoise 折算：`KSamplerAdvanced` 没有 denoise 输入，用 `start_at_step` 表达
    （steps=8、denoise=0.6 → 从第 3 步接 ≈ 保留 40% 的噪声步）。有 denoise 输入的
    采样器就直给。

    图里已经启用的 LoadImage（主人把「图像反推提示词」组打开时）也一并喂上同一个文件，
    这样它自己那条「反推 → 重画」的路也能跑。

    返回 (一采节点 id, 新节点 id 列表)；塞不进去抛 ComfyError（调用方回落别的档）。
    """
    ks = wf_first_sampler(g)
    if not ks:
        # 编辑流（latent 由 Qwen-Edit/Kontext 类节点自产）：没有可接线的空画布一采，
        # 参考图本来就靠图里的 LoadImage 喂扩散链 —— 直接换文件，latent/denoise 一概
        # 不动（denoise 语义由流自定，bot 的降噪旋钮对编辑流不生效）。2026-09-29。
        loads = [k for k, v in g.items()
                 if str(v.get("class_type") or "") in WF_LOAD_IMAGE]
        if loads and any(v.get("class_type") in WF_SAMPLERS for v in g.values()):
            for k in loads:
                g[k].setdefault("inputs", {})["image"] = image
            return None, list(loads)
        raise ComfyError("这张工作流里找不到能吃参考图的一采（latent 是别处喂的）")
    vae = next((k for k, v in g.items() if str(v.get("class_type") or "") == "VAELoader"), None)
    if not vae:
        raise ComfyError("这张工作流里没有 VAE 加载节点，接不了参考图")
    inp = g[ks].setdefault("inputs", {})
    mx = max([int(k) for k in g if str(k).isdigit()] or [0])
    n1, n2, n3 = [str(mx + i + 1) for i in range(3)]
    g[n1] = {"class_type": "LoadImage", "inputs": {"image": image}}
    prev, made = [n1, 0], [n1]
    if int(w or 0) and int(h or 0):
        g[n2] = {"class_type": "ImageScale",
                 "inputs": {"image": prev, "upscale_method": "lanczos",
                            "width": int(w), "height": int(h), "crop": "center"}}
        prev = [n2, 0]
        made.append(n2)
    g[n3] = {"class_type": "VAEEncode", "inputs": {"pixels": prev, "vae": [vae, 0]}}
    made.append(n3)
    inp["latent_image"] = [n3, 0]
    if "denoise" in inp:
        inp["denoise"] = float(denoise)
    elif "start_at_step" in inp:
        steps = int(inp.get("steps") or 20)
        inp["start_at_step"] = max(0, min(steps - 1,
                                          int(round((1.0 - float(denoise)) * steps))))
    for v in g.values():                    # 图里已有的 LoadImage 也喂上（反推组那条路）
        if str(v.get("class_type") or "") in WF_LOAD_IMAGE:
            v.setdefault("inputs", {})["image"] = image
    return ks, made


def wf_size_hint(g, pts):
    """工作流自己写死的出图尺寸；拿不到返回 None。
    优先带 width/height 的节点，其次从「768x1344」这种预设名里读（机甲档的尺寸
    写在 ZML_PresetResolution 的预设名里，没有 width/height 格）。
    「互换宽高」开着就换回来（机甲档默认开 → 真实是 1344×768 横幅）。"""
    nid = (pts.get("size") or [None])[0]
    if nid is None:
        for k, v in g.items():
            inp = v.get("inputs") or {}
            if isinstance(inp.get("width"), int) and isinstance(inp.get("height"), int):
                nid = k
                break
    if nid and nid in g:
        inp = g[nid].get("inputs") or {}
        try:
            return [int(inp.get("width")), int(inp.get("height"))]
        except Exception:
            pass
    for k, v in g.items():
        inp = v.get("inputs") or {}
        for val in inp.values():
            m = re.search(r"(\d{3,4})\s*[x×*]\s*(\d{3,4})", val) if isinstance(val, str) else None
            if not m:
                continue
            w, h = int(m.group(1)), int(m.group(2))
            if any(inp.get(kk) is True for kk in WF_SWAP_KEYS):
                w, h = h, w
            return [w, h]
    return None


def _wf_natkey(s):
    """控件名自然排序键：string_2 < string_10（纯字典序会排反）。"""
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", str(s))]


def _wf_prompt_slot(doc, g, pts):
    """用户提示词该写进哪个「文本格」：正向编码器的 text 若由有标题的文本格拼接
    而成（Anima 工作流：质量串 + 画师串 + 简单描述 → JoinStringMulti → 编码器），
    沿「名字排序最大的 STRING 输入」优先下钻（string_3 比.string_2 靠后），
    第一个文本格就是描述格 —— Anima 工作流实测落「简单描述提示词」。
    返回 None = 没有拼接链，提示词照旧整体替换编码器（老行为）。
    档位写 prompt_slot 可以显式指定，写 prompt_slot:"" 强制走整体替换。"""
    pos = pts.get("positive")
    if not pos or pos[0] not in g:
        return None
    txt = (g[pos[0]].get("inputs") or {}).get(pos[1])
    if not (isinstance(txt, (list, tuple)) and len(txt) == 2):
        return None                     # 编码器的 text 是字面量，没有拼接链
    lk = _wf_links(doc)
    by_id = {str(n.get("id")): n for n in (doc.get("nodes") or []) if isinstance(n, dict)}

    def is_slot(n):
        t = str(n.get("title") or "").strip()
        return bool(t) and not WF_SLOT_SKIP.search(str(n.get("type") or "")) \
            and bool(_wf_text_slot_name(n))

    def walk(nid, seen):
        n = by_id.get(nid)
        if n is None or nid in seen:
            return None
        seen.add(nid)
        if is_slot(n):
            return str(n.get("title") or "").strip()
        strs = [(e.get("name"), e.get("link")) for e in (n.get("inputs") or [])
                if str(e.get("type") or "").upper() in ("STRING", "TEXT")
                and e.get("link") in lk and lk[e["link"]] and lk[e["link"]][0] is not None]
        for _name, lid in sorted(strs, key=lambda x: _wf_natkey(x[0]), reverse=True):
            r = walk(str(lk[lid][0]), seen)
            if r:
                return r
        return None

    return walk(str(pos[0]), set())


def _wf_slot_text(doc, title):
    """工作流文件里某个文本格的原始值（没这个格返回 None）。"""
    for n in (doc.get("nodes") or []):
        if isinstance(n, dict) and str(n.get("title") or "").strip() == title:
            wv = n.get("widgets_values")
            if isinstance(wv, (list, tuple)) and wv and isinstance(wv[0], str):
                return wv[0]
    return None


def _artist_slot(s, doc):
    """选配到的画师/角色 tag 该并进哪个文本格：档位 `artist_slot` 指定 > 标题带「质量」的格
    > 标题带「画师」的格。（Anima工作流 里「画师串」节点已脱钩、「质量(不动)」才在链上，
    所以质量优先；换工作流时用 artist_slot 显式指。）"""
    want = str(s.get("artist_slot") or "").strip()
    titles = [str(n.get("title") or "").strip()
              for n in (doc.get("nodes") or []) if isinstance(n, dict)]
    if want and want in titles:
        return want
    for key in ("质量", "画师"):
        for t in titles:
            if t and key in t:
                return t
    return ""


def graph_workflow(cfg, s, text, negative, w, h, seed, prefix, artist="", image=None,
                   denoise=None):
    """跑主人自己的 workflow 文件：读 → 转 API → 注入提示词/尺寸/种子。返回 (图, 宽高)。

    提示词落点分两种（2026-09-13 定）：
    * 拼接模式：正向编码器的 text 由文本格拼出来（Anima 工作流）——用户描述写进
      「描述格」（_wf_prompt_slot 认出来的那个，档位 prompt_slot 可显式指定），
      质量串/画师串由工作流自己的拼接器合成；config wf_texts（/admin 文本槽）的
      覆盖在这一步一起写进控件值。
    * 整体替换：没有拼接链时照老行为，提示词整个写进编码器（此时档位的
      prompt_prefix/prompt_artist 会拼在最前面）。
    artist = 画师/角色选配挑到的 tag（可能两个）：并进「质量」或「画师」格（_artist_slot，
    2026-09-14 加）——因为该工作流的「画师串」节点已被旁路，只有写进接在链上的格才生效。
    """
    if image and s.get("i2i_workflow"):
        s = dict(s)  # 图生图换编辑流（2026-09-29）：文生图仍用 workflow，
        s["workflow"] = str(s["i2i_workflow"])  # 有参考图的任务走 i2i_workflow
    path, doc = wf_load(cfg, s.get("workflow"))
    g0, rep = wf_to_api(doc)
    if not g0:
        raise ComfyError("%s 里没有能跑的东西" % path.name)
    if rep.get("kind") != "image":
        raise ComfyError("%s 不是出图片的工作流（%s）" % (
            path.name, "是视频工作流" if rep.get("kind") == "video" else "没有图片输出节点"))
    pts = wf_points(g0, rep.get("output"))
    texts = wf_texts(cfg)
    slot_title = str(s.get("prompt_slot") if s.get("prompt_slot") is not None
                     else _wf_prompt_slot(doc, g0, pts) or "").strip()
    if artist:                          # 画师/角色 tag：并进「质量/画师」格（2026-09-14）
        texts = dict(texts)
        aslot = _artist_slot(s, doc)
        if aslot and aslot != slot_title:
            base = texts.get(aslot)
            if base is None:
                base = _wf_slot_text(doc, aslot) or ""
            texts[aslot] = ("%s %s" % (str(base).strip(), artist)).strip()
            log.info("工作流档 %s：画师/角色 tag 并入「%s」格 -> %s",
                     s.get("workflow"), aslot, artist)
        elif slot_title:                # 没有独立的质量格，就拼在描述前面（别把 tag 丢了）
            text = ("%s %s" % (artist, text)).strip()
            log.info("工作流档 %s：画师/角色 tag 拼在描述前 -> %s", s.get("workflow"), artist)
        else:
            # 整体替换模式（prompt_slot:""，如 krea2 机甲档）：没有文本格可并，
            # tag 同样拼在描述前 —— 2026-09-16 前是直接丢弃选配结果，白查一场。
            text = ("%s %s" % (artist, text)).strip()
            log.info("工作流档 %s：整体替换模式，画师/角色 tag 拼在描述前 -> %s",
                     s.get("workflow"), artist)
    slot_mode = False
    if slot_title and any(str(n.get("title") or "").strip() == slot_title
                          for n in (doc.get("nodes") or []) if isinstance(n, dict)):
        texts = dict(texts)
        texts[slot_title] = text            # 用户描述进描述格，工作流自己拼
        slot_mode = True
    if texts:
        log.info("工作流档 %s：文本格写入 %s（%s）", path.name,
                 "、".join("%s=%r" % (k, v[:24]) for k, v in sorted(texts.items())),
                 "拼接模式" if slot_mode else "覆盖")
    # 「保留扩写链」的档位（宫格档）：工作流里的 PromptExpand/LLM 编译链是刻意要跑的，
    # 让 wf_to_api 别按防残留图那套把它裁了 —— 2026-10-16
    keep_expand = bool(s.get("keep_expand"))
    g, rep = wf_to_api(doc, texts=texts, keep_expand=keep_expand)
    for k, v in (s.get("wf_points") or {}).items():     # 配置里手写的注入点优先
        if v:
            pts[k] = v
    warn = list(pts.get("warn") or [])
    # 档位覆盖「预设名」尺寸格（ZML_PresetResolution 这类）：必须赶在下面读 own 之前改，
    # 否则读到的还是工作流原来的尺寸（机甲档默认 768x1344 + 互换宽高 = 1344x768 横幅）。
    sp = pts.get("size_preset")
    if sp and sp[0] in g:
        nid, pk, sk = sp
        if str(s.get("wf_preset") or "").strip():
            g[nid]["inputs"][pk] = str(s["wf_preset"]).strip()
        if isinstance(s.get("swap_wh"), bool) and sk:
            g[nid]["inputs"][sk] = bool(s["swap_wh"])
        warn.append("尺寸预设按档位改成 %s（互换宽高=%s）"
                    % (g[nid]["inputs"].get(pk), g[nid]["inputs"].get(sk) if sk else "无此开关"))
    own = wf_size_hint(g, pts)      # 先读工作流自己的尺寸，再改（免得把注入的提示词当尺寸读）
    # 工作流自己的存图节点可能是自定义的（机甲的 ZML_SaveImageV2 直接存到 E:\AI\Picture\...），
    # 存哪儿不归我们管、/view 也拿不到。统一把它换成一个标准 SaveImage 挂到同一条 IMAGE 上：
    # 图一定落在 ComfyUI 的 output 里，前缀也归我们控。
    out_key = str(rep.get("output"))
    src = None
    for name, typ in (rep.get("in_types", {}).get(out_key) or {}).items():
        v = (g.get(out_key, {}).get("inputs") or {}).get(name)
        if str(typ).upper() == "IMAGE" and _wf_link(v, g):
            src = [str(v[0]), int(v[1])]
            break
    if src is None:
        warn.append("出图节点没有 IMAGE 输入，图可能取不回来")
    else:
        if s.get("skip_upscale", False):
            # 档位声明跳过放大：出图源如果是 UltimateSDUpscale 这类分块重采样，
            # 改接它自己的 image 输入（一般是 VAEDecode 的原图）——
            # 裁剪时整条放大链自然消失。
            nid0 = str(src[0])
            if g.get(nid0, {}).get("class_type") in WF_UPSCALER:
                hop = _wf_link((g.get(nid0, {}).get("inputs") or {}).get("image"), g)
                if hop:
                    src = [str(hop), int((g[nid0]["inputs"]["image"])[1])]
                    warn.append("已按档位跳过 %s 放大（8G 卡扛不住）" % g[nid0]["class_type"])
        # 工作流自己的存图节点可能是自定义的（机甲的 ZML_SaveImageV2 直接存到
        # E:\AI\Picture\...），存哪儿不归我们管、/view 也拿不到。统一换成标准 SaveImage。
        g.pop(out_key, None)
        sid = str(max([int(k) for k in g if str(k).isdigit()] or [0]) + 1)
        g[sid] = {"class_type": "SaveImage",
                  "inputs": {"images": src, "filename_prefix": prefix}}
    if pts.get("positive") and pts["positive"][0] in g and not slot_mode:
        nid, key = pts["positive"]
        g[nid]["inputs"][key] = text
    if pts.get("negative") and negative and pts["negative"][0] in g:
        nid, key = pts["negative"]
        g[nid]["inputs"][key] = negative
    if pts.get("size") and w and h and pts["size"][0] in g:
        nid, wk, hk = pts["size"]
        g[nid]["inputs"][wk] = int(w)
        g[nid]["inputs"][hk] = int(h)
        out_wh = [int(w), int(h)]
    else:
        out_wh = own or [int(w or 0), int(h or 0)]
    if pts.get("seed") and pts["seed"][0] in g:
        nid, key = pts["seed"]
        # 种子那一格若是**连线**（工作流里采样器都从同一个 easy seed / Seed 节点取），
        # 改上游那个节点的值，别把连线换成字面量 —— 一采二采共用一颗种子是刻意的
        # （Z-Image 风景档 2026-09-18 踩到：注入把 #86 的连线切断，一采/二采种子就分了家）。
        up = _wf_link(g[nid]["inputs"].get(key), g)   # 注意：返回的是**节点 id**，不是 (id, 槽)
        hit_up = False
        if up and str(up) in g:
            for kk, vv in list((g[str(up)].get("inputs") or {}).items()):
                if kk in ("seed", "noise_seed", "value") and isinstance(vv, int) \
                        and not isinstance(vv, bool):
                    g[str(up)]["inputs"][kk] = int(seed)
                    hit_up = True
                    break
        if not hit_up:
            g[nid]["inputs"][key] = int(seed)
    for nid in (pts.get("prefix") or []):
        if nid in g:
            g[nid]["inputs"]["filename_prefix"] = prefix
    # 画同款：参考图塞进工作流（2026-09-16 加）。参考图按工作流自己的出图尺寸缩，
    # 免得群友的 4000px 原图把 8G 卡撑爆；一采/二采的放大倍数照常生效。
    if image:
        iw, ih = (int(w or 0), int(h or 0)) if (w and h) else (0, 0)
        if not iw or not ih:
            iw, ih = (own or [1024, 1024])[:2]
        ks_i, made = wf_inject_image(g, image, denoise=(0.6 if denoise is None else float(denoise)),
                                     w=iw, h=ih)
        out_wh = [iw, ih]
        log.info("工作流档 %s：参考图已接进 %s（%d×%d，denoise=%.2f，涉及节点 %s）",
                 s.get("workflow"), ("编辑流" if ks_i is None else "一采 #%s" % ks_i),
                 iw, ih, (0.6 if denoise is None else float(denoise)),
                 "、".join(made))
    sc = wf_out_scale(g)
    if sc and abs(sc - 1.0) > 1e-6:
        out_wh = [max(8, int(out_wh[0] * sc) // 8 * 8), max(8, int(out_wh[1] * sc) // 8 * 8)]
    # 最后才裁上游：注入把正向提示词换成字面量后，原来喂它的文本拼接链
    # （JoinStringMulti / 文本容器）就没用了，在这里自然被裁掉 —— 服务端
    # 连见都见不到它们，缺自定义节点也不影响（2026-09-13 Anima 工作流的教训：
    # 先裁后注入会把这些节点留在图里，/prompt 照样 400）。
    if src is not None:
        keep = set([sid]) | set(_wf_upstream(g, [sid]))
        g = {k: v for k, v in g.items() if k in keep}
    log.info("工作流档 %s：%s 出图，%d 个节点（采样器 %s / 正向 %s / 负向 %s / 尺寸 %s / 种子 %s / 提示词%s）",
             s.get("workflow"), path.name, len(g), pts.get("sampler"), pts.get("positive"),
             pts.get("negative"), pts.get("size"), pts.get("seed"),
             "写进「%s」格" % slot_title if slot_mode else "整体替换")
    if warn:
        log.warning("工作流档 %s：%s", path.name, "；".join(warn))
    # ★ 出图前的硬性自检（2026-10-06 主人踩到「一秒发了张不相干的图」之后加的）★
    # 两条中任一条成立，结果必然是错的 ⇒ 直接抛错，**绝不把错图发出去**。
    # ① 没有采样器 = 空壳提交（只有 LoadImage+SaveImage 这类直通支路）
    # ② 有描述但提示词压根没进图（没落点）＝ 画出来的跟你要的无关
    if not wf_has_sampler(g):
        raise ComfyError(
            "工作流「%s」里没有采样器，跑起来只会把输入图原样存出来、不会真的出图。"
            "多半是它真正出图的 SaveImage 在 ComfyUI 界面里被静音了（bot 会跳过静音节点，"
            "于是选中了旁路的直通支路）—— 去界面把出图那个 SaveImage 取消静音，"
            "或者在 /admin「工作流选择」里换一个工作流。" % path.name)
    if str(text or "").strip() and not slot_mode and not (
            pts.get("positive") and str(pts["positive"][0]) in g):
        raise ComfyError(
            "工作流「%s」里找不到能写提示词的地方，这段描述进不去（画出来必然不相干）。"
            "换一个工作流档，或在 /admin「工作流选择」里指定正确的出图工作流。" % path.name)
    # PNG 元数据用：把**原始界面工作流**原封不动地带出来（真节点位置/连线/控件态，
    # 拖回 ComfyUI 直接能开）。只挂引用、不改内容，也不改 g 的任何节点。
    if isinstance(doc, dict) and doc.get("nodes"):
        g[UI_WF_KEY] = doc
    return g, (int(out_wh[0]), int(out_wh[1]))


def wf_class_info(cfg, timeout=8):
    """问服务端：这个节点类认不认、必填输入各是什么类型（问一次记一次）。"""
    b = base(cfg)
    cache = {}

    def info(cls):
        if cls not in cache:
            d = {}
            try:
                d = _req(b + "/object_info/" + urllib.parse.quote(str(cls)),
                         timeout=timeout).get(cls) or {}
            except Exception:
                d = {}
            cache[cls] = {"ok": bool(d), "required": dict((d.get("input") or {}).get("required") or {})}
        return cache[cls]

    return info


def wf_text_slots(cfg, name):
    """工作流里「有标题的文本格」清单（/admin 文本槽编辑器的数据）。
    返回 [{title, file, value, overridden}]：value 已并入 config 覆盖
    （overridden=True 时 value 是覆盖值，file 才是工作流文件里的原值）。
    正/负向编码器不列（归注入点管，编辑了会被顶掉）。"""
    try:
        _path, doc = wf_load(cfg, name)
    except Exception:
        return []
    texts = wf_texts(cfg)
    out = []
    for n in (doc.get("nodes") or []):
        if not isinstance(n, dict) or n.get("mode") not in (0, None):
            continue
        t = str(n.get("title") or "").strip()
        if not t or WF_SLOT_SKIP.search(str(n.get("type") or "")):
            continue
        if not _wf_text_slot_name(n):
            continue
        fv = ""
        wv = n.get("widgets_values")
        if isinstance(wv, (list, tuple)) and wv and isinstance(wv[0], str):
            fv = wv[0]
        out.append({"title": t, "file": fv, "value": texts.get(t, fv),
                    "overridden": t in texts})
    return out


def _wf_check_input(config):
    """这个必填输入缺了算不算问题：控件型（能补值）和标准连线型（必须有人接）都算；
    自定义类型（LORAS / AUTOCOMPLETE_TEXT_LORAS 这类前端自己管的）不算，免得误报。"""
    if not isinstance(config, list) or not config:
        return False
    t = config[0]
    if isinstance(t, list):
        return True                      # COMBO
    return str(t).upper() in WF_CHECK_TYPES


def wf_item(cfg, p, root=None, info=None):
    """一份工作流的体检报告：能不能当档位、缺什么、注入点认出来没有。"""
    p = Path(p)
    st = p.stat()
    it = {"name": p.stem, "file": p.name,
          "rel": p.name if root is None else str(p.relative_to(root)).replace("\\", "/"),
          "path": str(p), "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
          "size_kb": max(1, int(st.st_size / 1024)), "backup": ".backup" in p.name.lower(),
          "nodes": 0, "kind": "none", "usable": False, "reason": "",
          "missing": [], "no_input": [], "hint": "", "needs_image": False,
          "points": {}, "warn": [], "size_wh": None}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        it["reason"] = "读不了：%s" % str(e)[:80]
        return it
    if "nodes" not in doc:
        it["reason"] = "不是 UI 格式的工作流（得是 ComfyUI 里存出来的那种）"
        return it
    try:
        g, rep = wf_to_api(doc)
    except Exception as e:
        it["reason"] = "转换失败：%s" % str(e)[:80]
        return it
    it["nodes"] = rep["nodes"]
    it["kind"] = rep["kind"]
    pts = wf_points(g, rep.get("output"))
    it["points"] = {"sampler": pts.get("sampler"), "positive": bool(pts.get("positive")),
                    "negative": bool(pts.get("negative")), "size": bool(pts.get("size")),
                    "seed": bool(pts.get("seed"))}
    it["warn"] = list(pts.get("warn") or [])
    it["size_wh"] = wf_size_hint(g, pts)
    it["needs_image"] = any(v.get("class_type") in WF_LOAD_IMAGE for v in g.values())
    if info is not None:
        bad_cls, no_in = set(), set()
        for v in g.values():
            cls = str(v.get("class_type"))
            ic = info(cls)
            if not ic.get("ok"):
                bad_cls.add(cls)
                continue
            have = set(v.get("inputs") or {})
            for need, conf in (ic.get("required") or {}).items():
                if need not in have and _wf_check_input(conf):
                    no_in.add("%s 的 %s" % (cls, need))
        it["missing"] = sorted(bad_cls)
        it["no_input"] = sorted(no_in)
    if rep["kind"] != "image":
        it["reason"] = "视频工作流，现画只发图片" if rep["kind"] == "video" else "找不到图片输出节点"
    elif not pts.get("sampler"):
        it["reason"] = "找不到采样器"
    elif not pts.get("positive"):
        it["reason"] = "找不到提示词注入点"
    elif it["missing"]:
        it["reason"] = "本机缺节点：%s" % "、".join(it["missing"][:4])
    elif it["no_input"]:
        it["reason"] = "有必填输入没值：%s" % "、".join(it["no_input"][:3])
    elif it["needs_image"]:
        it["reason"] = "要喂参考图（图生图），现画是文生图"
    else:
        it["usable"] = True
    if it["missing"]:
        it["hint"] = "这些节点本机没装；内置 anima/krea2/fast 是脚本直接拼图，不受影响"
    return it


# ---------------- 出图 ----------------
def _style(cfg, style=None):
    """取档位：(档位名, 档位字典)。写歪了的档位名自动落回 default_style。
    档位两个来源：config 里声明的 styles + 工作流库的文件档（虚拟，不进 config，
    见 style_dict —— 工作流目录本身就是这张声明表）。"""
    name = str(style or cfg.get("default_style") or "krea2").strip()
    s = style_dict(cfg, name)
    if not s:
        name = str(cfg.get("default_style") or "krea2")
        s = style_dict(cfg, name)
    return name, s


def _hires_missing(s, opt):
    """Krea2 那套文件齐不齐，缺谁就说什么（服务端列表为空时不拦，交给它自己校验）。"""
    for key, pool, cn in (("unet", "unets", "扩散模型"), ("clip", "clips", "文本编码器"),
                          ("vae", "vaes", "VAE"), ("lora", "loras", "LoRA"),
                          ("lora2", "loras", "LoRA")):
        want = s.get(key)
        if not want:
            if key == "lora2":
                continue        # 第二个 LoRA 可选
            return "档位里没配 %s" % key
        have = opt.get(pool) or []
        if have and want not in have:
            return "本机没有%s「%s」" % (cn, want)
    return None


# ---------------- PNG 元数据（workflow 块 + 人读摘要）----------------
# 总开关：config `comfyui.png_metadata` = false 可关（主人不想要就关掉，不影响出图）。
# 实际开关读config 的 comfyui.png_metadata（默认开）。
# 保留这个常量只为兼容旧引用/ 探针判断，实际判定走 png_meta_enabled()。
PNG_META_ON = True


def png_meta_enabled(cfg=None):
    """config `comfyui.png_metadata` 是否开启 PNG 元数据（默认开）。

    写法宽松：False/0/"0"/"false"/"off"/"no"/"关"/"关闭" 都算关。
    主人说「不想要就关掉」⇒ 真的能关，不会因为漏读配置就硬塞元数据。
    """
    v = (cfg or {}).get("png_metadata", True)
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "off", "no", "none", "关", "关闭")
    return bool(v)
# graph_workflow 把**原始界面工作流文档**挂在这个私有键上传出来，供 PNG 写
# `workflow` 元数据块（能直接拖回 ComfyUI 打开）。`_build` 提交前会把它摘掉，
# 对 wf_to_api / POST 完全透明（node id 全是数字，不会和这个字符串键冲突）。
# ⚠️ **必须原封不动**地挂 —— 改 node id 不同步改 links 会把工作流弄废
# （links 里存的是 [id, origin_id, origin_slot, target_id, target_slot, type]）。
UI_WF_KEY = "__ui_wf__"

# 主人 2026-10-04 需求：bot 出的图要带完整元数据，能拖回 ComfyUI 打开。
# 机制（读 ComfyUI 源码定案，勿猜）：
#   nodes.py:1701 SaveImage.save_images(prompt, extra_pnginfo) →
#   metadata.add_text("prompt", ...) + 逐个 add_text(x, json.dumps(extra_pnginfo[x]))
#   nodes.py:1686 hidden 里有 extra_pnginfo ← execution.py:203 extra_data.get(...)
#   ← server.py:1121 /prompt 请求体的 extra_data
# ⇒ 在 POST 的 extra_data.extra_pnginfo 里塞 dict，**所有工作流/所有存图节点一次覆盖**，
#   不用改 ComfyUI、不加自定义节点。前端之所以能写 workflow 块，是因为它在
#   extra_data.extra_pnginfo.workflow 里传了界面工作流；API 提交没这一步，故只剩 prompt。
#
# ✅ 覆盖面（2026-10-04 实测，全项目 grep 只有 comfy_gen.py:2800 一个 /prompt 提交点）：
#   出图（文生图）/ 图生图（画同款）/ 视频生成**全部**经过它 ⇒ 只改这一处就全覆盖。
#   实测：wf 档出的 PNG 里 prompt/workflow/parameters/bot 四块齐全，
#   且 workflow 块的 node id/type/控件值 digest 与磁盘原工作流**完全一致**。
# ⚠️ 开关：config `comfyui.png_metadata` = false 即彻底不塞（元数据不是 comfy_gen 硬塞的）。
def _api_to_ui_workflow(g):
    """把 API 图反向包装成一份「最小可读」的界面工作流（内置档/anima/krea2 用）。

    ⚠️ 节点位置是按节点号顺序**排出来的**（不是原工作流布局），
    所以它不能直接拖回 ComfyUI 打开跑 —— 它是一份**快照**：
    记录了「这张图用了什么模型/采样/LoRA/提示词/参考图」，且人类可读。
    wf 档走下面那条路，那边是真的原始布局、能打开。
    """
    nodes, links = [], []
    for i, nid in enumerate(sorted(g, key=lambda x: int(x) if str(x).isdigit() else 0)):
        n = g[nid]
        ins = n.get("inputs") or {}
        nodes.append({
            "id": int(nid) if str(nid).isdigit() else nid,
            "type": n.get("class_type") or "Note",
            # 网格排布：每行 6个，避免全叠在原点
            "pos": [(i % 6) * 320, (i // 6) * 220],
            "size": [300, 120],
            "flags": {}, "order": i, "mode": 0,
            "inputs": [{"name": k, "type": "INPUT",
                        "link": (v[0] if isinstance(v, (list, tuple)) and v else None)}
                       for k, v in ins.items()],
            "outputs": [],
            "properties": {},
            # **控件值原样带上**（模型名/权重/提示词都在这），这是快照的关键
            "widgets_values": {k: v for k, v in ins.items()
                               if not (isinstance(v, (list, tuple)) and len(v) == 2)},
        })
    return {"last_node_id": len(nodes), "last_link_id": 0,
            "nodes": nodes, "links": links, "groups": [],
            "config": {}, "extra": {}}


def png_extra_pnginfo(g, ui_wf=None, text="", negative="", seed=None, size=None,
                      style=None, timeout=None):
    """造extra_pnginfo dict（ComfyUI 会把每个 key 写成一块 PNG 文本）。

    - `workflow`：界面工作流。wf 档传 ui_wf（真布局，可打开），其余传 API 反向包装的快照。
    - `parameters`：**人读**提示词（A1111 习惯），肉眼看图就能知道谁画的、怎么画的。
    - `bot_*`：补几块 bot 侧的上下文（档位/工作流文件/尺寸/种子），省得翻日志。
    """
    ex = {}
    # ⚠️ `workflow` 键只放**真能拖回 ComfyUI 打开**的原始界面工作流（wf 档）。
    # 拿不到就不写这个键 —— 宁可缺，别塞一份打不开的假工作流骗前端/其它工具
    # （前端和A1111 系工具都是直接 loadGraphData，结构不对会报错或静默失败）。
    real_wf = isinstance(ui_wf, dict) and bool(ui_wf.get("nodes"))
    if real_wf:
        ex["workflow"] = ui_wf
    else:
        # 快照另存到自带警告名的键：记录了模型/采样/LoRA/提示词，人能读，
        # 但**不能直接拖回 ComfyUI 打开**（节点位置是排出来的）。
        ex["bot_workflow_snapshot"] = _api_to_ui_workflow(g)
    neg = str(negative or "")
    ex["parameters"] = "\n".join([
        str(text or ""),
        "",
        "Negative prompt: " + neg,
        "",
        "" if real_wf else "(本图 workflow 为 API 快照，见bot_workflow_snapshot，不能直接拖回 ComfyUI 打开)",
        ""
        "Steps: %s, Sampler: %s, CFG scale: %s, Seed: %s, Size: %s" % (
            (g.get(_find_ks(g), {}).get("inputs", {}) or {}).get("steps", "?"),
            (g.get(_find_ks(g), {}).get("inputs", {}) or {}).get("sampler_name", "?"),
            (g.get(_find_ks(g), {}).get("inputs", {}) or {}).get("cfg", "?"),
            seed if seed is not None else (g.get(_find_ks(g), {}).get("inputs", {}) or {}).get("seed", "?"),
            "%sx%s" % tuple(size) if size and len(size) >= 2 else "?"),
    ])
    meta = {"style": style, "generated_by": "qq-bot",
            "workflow_openable": bool(real_wf)}
    if timeout:
        meta["timeout_sec"] = timeout
    ex["bot"] = meta
    return ex


def _find_ks(g):
    """图里的采样节点 id（KSamplerAdvanced / KSampler），取第一个找到的。"""
    for k, v in (g or {}).items():
        if (v or {}).get("class_type") in ("KSampler", "KSamplerAdvanced"):
            return k
    return next(iter(g or {}), None)

def _run(b, g, timeout, tag, extra=None):
    """提交并等结果，返回 (history 条目, 耗时秒)。超时顺手把自己的任务撤掉。

    `extra` = **extra_pnginfo**（PNG 元数据块）。ComfyUI 会把它写进存图 PNG：
      server.py:1121 /prompt 的 extra_data → execution.py:203 hidden →
      nodes.py:1713 SaveImage 逐个 add_text。**所有存图节点都吃这一套**。
      老版本 ComfyUI 不认这个字段就忽略 ⇒ 不会挂。
    """
    body = {"prompt": g, "client_id": "qqbot"}
    if extra:
        body["extra_data"] = {"extra_pnginfo": extra}
    try:
        pid = _req(b + "/prompt", body,
                   timeout=30).get("prompt_id")
    except ComfyError as e:
        raise ComfyError("提交失败：%s" % str(e)[:200])
    if not pid:
        raise ComfyError("ComfyUI 没返回 prompt_id")
    log.info("已提交 ComfyUI %s（%s）：%s", pid[:8], tag, json.dumps(
        [g[k]["class_type"] for k in sorted(g, key=lambda x: int(x))])[:120])
    t0 = time.time()
    entry = None
    while time.time() - t0 < timeout:
        time.sleep(1.0)
        try:
            hist = _req(b + "/history/" + urllib.parse.quote(pid), timeout=15)
        except ComfyError:
            continue
        entry = hist.get(pid)
        if entry:
            break
        try:  # ComfyUI 重启会吞掉队列：队里没东西、历史也查不到，就是任务丢了
            q = _req(b + "/queue", timeout=10)
            busy = len(q.get("queue_running") or []) + len(q.get("queue_pending") or [])
        except ComfyError:
            busy = 1
        if not busy and time.time() - t0 > 25:
            raise ComfyError("任务在队列里消失了（ComfyUI 可能刚重启）")
    if not entry:
        why = _cancel(b, pid)
        raise ComfyError("画了 %.0fs 还没出：%s" % (time.time() - t0, why))
    return entry, time.time() - t0


def _images_of(entry):
    """这次提交产出的所有图（hires 链可能有多个 SaveImage，全都要记下来，发完一起删）。"""
    st = (entry.get("status") or {})
    imgs = []
    for _, out in (entry.get("outputs") or {}).items():
        imgs += out.get("images") or []
    if st.get("status_str") == "error" or not imgs:
        msg = ""
        for _, m in (st.get("messages") or []):
            if isinstance(m, (list, tuple)) and len(m) > 1 and isinstance(m[1], dict):
                msg = str(m[1].get("exception_message") or m[1].get("node_id") or "")
        raise ComfyError("出图失败：%s" % (msg or "没有产出图片")[:160])
    return imgs


def output_paths(cfg, res):
    """这次现画留在 ComfyUI output 里的原图绝对路径（供「发后删图」一并删掉）。
    只认 output 目录及其子目录，subfolder 里带 .. 的一律丢掉，绝不越界删外面。"""
    root = os.path.abspath(os.path.join(str(cfg.get("comfy_dir") or ""), "output"))
    out = []
    for it in ((res or {}).get("comfy_out") or []):
        fn = str(it.get("filename") or "")
        if not fn:
            continue
        p = os.path.abspath(os.path.join(root, str(it.get("subfolder") or ""), fn))
        if p == root or p.startswith(root + os.sep):
            out.append(p)
        else:
            log.warning("跳过越界的 output 路径: %s", p)
    return out


def _save_image(b, im, out_dir, prompt):
    q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                "type": im.get("type", "output")})
    raw = _req(b + "/view?" + q, timeout=90, raw=True)
    if len(raw) < 1024:
        raise ComfyError("拿到的图是空的")
    out_dir = Path(out_dir or ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = "%s_%s" % (time.strftime("%m%d-%H%M%S"),
                      "".join(ch for ch in prompt[:14] if ch.isalnum() or "一-鿿" >= ch >= "〇")[:10])
    f = out_dir / ("%s.png" % stem)
    f.write_bytes(raw)
    return f


def _prune_i2i_dir(cfg, subfolder, keep_hours=6):
    """清 ComfyUI input 里我们上传过的旧参考图（每张一两 MB，不清会攒满主人硬盘）。

    只碰自己这个子目录、只删过期的；ComfyUI 没有删除接口，但这是本机，直接删文件系统就行。
    """
    d = Path(cfg.get("comfy_dir") or "") / "input" / subfolder
    if not d.is_dir():
        return 0
    now, n = time.time(), 0
    for f in d.iterdir():
        try:
            if f.is_file() and now - f.stat().st_mtime > keep_hours * 3600:
                f.unlink()
                n += 1
        except OSError:
            pass
    if n:
        log.info("图生图：清了 %d 张过期参考图（%s）", n, subfolder)
    return n


def upload_image(cfg, path, subfolder="qqbot_i2i", timeout=90):
    """把本地图传到 ComfyUI 的 input/ 下，返回 LoadImage 能直接认的相对名（subfolder/name）。

    图生图要用：/prompt 只能引用 ComfyUI 自己 input 目录里的图，没法吃 bot 侧的绝对路径。
    """
    p = Path(path)
    if not p.is_file():
        raise ComfyError("图生图：本地图找不到了（%s）" % p.name)
    try:
        raw = p.read_bytes()
    except Exception as e:
        raise ComfyError("图生图：图读不出来（%s）" % str(e)[:100])
    if not raw:
        raise ComfyError("图生图：图是空的")
    boundary = "----qqbot" + ("%016x" % random.getrandbits(64))

    def _field(name, val):
        return ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                % (boundary, name, val)).encode("utf-8")

    # 上传名一律换 ASCII：群友的图常见中文名，ComfyUI 侧读中文路径容易翻车
    up_name = ("%016x" % random.getrandbits(64)) + (p.suffix or ".png").lower()
    body = _field("subfolder", subfolder) + _field("overwrite", "true")
    body += ("--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"%s\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n"
             % (boundary, up_name)).encode("utf-8")
    body += raw + b"\r\n" + ("--%s--\r\n" % boundary).encode("utf-8")
    req = urllib.request.Request(base(cfg) + "/upload/image", data=body, method="POST",
                                 headers={"Content-Type":
                                          "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads((r.read() or b"{}").decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        txt = ""
        try:
            txt = (e.read() or b"").decode("utf-8", "replace")
        except Exception:
            pass
        raise ComfyError("上传图片失败 HTTP %s：%s" % (e.code, txt[:200]))
    except Exception as e:
        raise ComfyError("上传图片失败：%s" % str(e)[:120])
    try:
        _prune_i2i_dir(cfg, subfolder)
    except Exception as e:
        log.debug("清参考图失败（不影响出图）：%s", e)
    name = d.get("name") or up_name
    sub = (d.get("subfolder") or subfolder or "").strip("/")
    rel = ("%s/%s" % (sub, name)) if sub else name
    log.info("图生图：已上传 %s（%d 字节）", rel, len(raw))
    return rel


def _i2i_nodes(g, latent_key, ks_key, vae_ref, image, w, h, denoise, ids):
    """把「空画布」换成「参考图编码」：LoadImage → ImageScale → VAEEncode，KSampler 降噪。

    ids 给三个新节点的编号（必须是纯数字字符串，/prompt 的键要能 int() 排序）。
    原图尺寸可能很野（群友发啥都有），统一缩到档位尺寸，别把 8G 卡撑爆。
    """
    a, b, c = ids
    g.pop(latent_key, None)                      # 空画布不要了
    g[a] = {"class_type": "LoadImage", "inputs": {"image": image}}
    g[b] = {"class_type": "ImageScale",
            "inputs": {"image": [a, 0], "upscale_method": "lanczos",
                       "width": int(w), "height": int(h), "crop": "center"}}
    g[c] = {"class_type": "VAEEncode", "inputs": {"pixels": [b, 0], "vae": vae_ref}}
    g[ks_key]["inputs"]["latent_image"] = [c, 0]
    g[ks_key]["inputs"]["denoise"] = float(denoise)


# 暂存最近一次 build 带出的原始界面工作流（按 API 图 id 认领，避免嵌套/重入串味）
_LAST_UI_WORKFLOW = []


def _take_ui_workflow(g):
    """从暂存里取出属于这张 API 图的原始界面工作流（取不到就 None → 退回快照）。"""
    gid = id(g)
    for i, (k, doc) in enumerate(_LAST_UI_WORKFLOW):
        if k == gid:
            _LAST_UI_WORKFLOW.pop(i)
            return doc
    return None


def _drop_ui_workflow(g):
    """构建失败/放弃时清掉暂存，防止内存里攒一堆工作流文档。"""
    gid = id(g)
    for i, (k, _d) in enumerate(_LAST_UI_WORKFLOW):
        if k == gid:
            _LAST_UI_WORKFLOW.pop(i)
            return


def _build(cfg, s, engine, text, w, h, seed, opt, artist="", image=None, denoise=1.0):
    """按引擎拼图，返回 (图, 实际出图宽高)。artist = 选配到的画师/角色 tag（wf 档并入文本格）。"""
    w, h = int(w), int(h)
    negative = s.get("negative") or cfg.get("negative", "")
    prefix = cfg.get("prefix", "qqbot")
    if engine == "wf":
        # 主人自己的 workflow 文件：质量词前缀/@画师 由档位配置拼，跟 anima 档一个规矩
        full = "%s%s%s" % (s.get("prompt_prefix") or "", s.get("prompt_artist") or "", text)
        if not s.get("width") and not s.get("height"):
            w = h = 0          # 档位没写死尺寸 → 用工作流自己那套
        g, wh = graph_workflow(cfg, s, full, negative, w, h, seed, prefix, artist,
                               image=image, denoise=denoise)
        # PNG 元数据：原始界面工作流挂在 __ui_wf__ 上，取出来存到模块级暂存，
        # 并从 API 图里摘掉（绝不能让它进 POST）。
        ui_wf = g.pop(UI_WF_KEY, None)
        if ui_wf:
            _LAST_UI_WORKFLOW.append((id(g), ui_wf))
        return g, int(wh[0]), int(wh[1])
    if engine == "anima":
        g = graph_anima(text, negative, w, h, seed, s, prefix,
                        image=image, denoise=denoise)
        return g, w, h
    if engine == "krea2":
        use_vsr = bool(opt.get("rtx_vsr")) and s.get("vsr", True) is not False
        g = graph_hires(text, negative, w, h, seed, s, prefix,
                        vsr=use_vsr, refine=bool(s.get("refine", True)),
                        image=image, denoise=denoise)
        if use_vsr:
            sc = float(s.get("vsr_scale") or 1.5)
            w, h = max(8, int(w * sc) // 8 * 8), max(8, int(h * sc) // 8 * 8)
        return g, w, h
    ckpt = s.get("ckpt") or cfg.get("ckpt")
    sampler = _pick(s.get("sampler") or cfg.get("sampler", "euler_ancestral"),
                    opt.get("samplers"), ["euler_ancestral", "euler_a", "dpmpp_2m", "euler"])
    scheduler = _pick(s.get("scheduler") or cfg.get("scheduler", "normal"),
                      opt.get("schedulers"), ["normal", "karras", "simple"])
    return graph(text, negative, w, h, s.get("steps") or cfg.get("steps", 22),
                 s.get("cfg") or cfg.get("cfg", 6.0), seed, ckpt, sampler, scheduler, prefix), w, h


ASPECT_DEFAULT = "9:16"


def _aspect_to_wh(aspect, long_edge):
    """'a:b' -> (w, h)：以 long_edge 为长边，两边对齐到 8 的倍数（SD/SDXL 要求）。

    比例非法返回 None（交给调用方回落）。2026-09-18 加：发画图指令可指定长宽比，
    默认 9:16（竖屏）。"""
    try:
        a_s, b_s = str(aspect).split(":")
        a, b = int(a_s), int(b_s)
    except Exception:
        return None
    if a <= 0 or b <= 0:
        return None
    long_edge = max(8, int(round(long_edge / 8.0)) * 8)
    if a >= b:                       # 横 / 方：宽=长边
        w = long_edge
        h = max(8, int(round(long_edge * b / a / 8.0)) * 8)
    else:                            # 竖：高=长边
        h = long_edge
        w = max(8, int(round(long_edge * a / b / 8.0)) * 8)
    return w, h


def generate(cfg, prompt, style=None, llm=None, out_dir=None, timeout=None, landscape=False,
             aspect=None, book=None, web_llm=None, lookup=True, image=None, denoise=None):
    """跑一张图，落到 out_dir（一般是 bot 的图库）。

    image 给了就走图生图（先把图传到 ComfyUI，空画布换成参考图编码）：
    denoise 越小越像原图（默认取 cfg.i2i_denoise，再没有就 0.6）。
    返回 {file, name, seconds, prompt_used, size, style, engine, note, subject}。
    """
    b = base(cfg)
    if not up(cfg, timeout=4):
        raise ComfyError("本地 ComfyUI 没开")
    size = cfg.get("size") or {}
    opt = node_options(cfg)
    name, s = _style(cfg, style)
    engine = (s.get("engine") or "krea2").lower()
    if engine not in ("krea2", "anima", "wf"):
        # 主人明确要求：永远不要 SD1.5。2026-09-13 起 anima 是默认档，wf 走工作流库
        raise ComfyError("「%s」这个档位用不了（%s 引擎已停用）——krea2 / anima / wf 三种才行"
                         % (name, engine))
    note = ""
    skipped = None          # 「接不了参考图」被跳过的档（成功时如实汇报）
    attempts = [(name, s, engine)]
    why = None if engine == "wf" else _hires_missing(s, opt)
    if why:
        raise ComfyError("%s 档不可用：%s" % (name, why))
    fb, fs = _style(cfg, cfg.get("fallback_style"))
    fb_eng = (fs.get("engine") or "krea2").lower()
    if fb != name and fb_eng in ("krea2", "anima", "wf") \
            and (fb_eng == "wf" or not _hires_missing(fs, opt)):
        attempts.append((fb, fs, fb_eng))
    # 先查资料再画：角色名/历史人物这种光靠模型记性画不准的，联网核对设定（一次就够，重试不重查）
    brief, names = "", []
    if book is not None and lookup and cfg.get("enhance", True):
        t0 = time.time()
        brief, names = book.lookup(prompt, probe=llm, web=web_llm)
        if brief:
            log.info("主体查证 %s：拿到设定（%.1fs）", "、".join(names), time.time() - t0)
    # 图生图：图只传一次（重试用同一张）。工作流档能不能吃图**按结构判**
    # （有 VAE + 有空画布一采就行，见 wf_inject_image）；结构上接不了的才跳下一档。
    i2i = None
    dnz = 1.0
    if image:
        dnz = float(denoise if denoise is not None else cfg.get("i2i_denoise") or 0.6)
        dnz = min(1.0, max(0.15, dnz))
        i2i = upload_image(cfg, image)
    err = None
    for i, (nm, st_, eng) in enumerate(attempts):
        if i2i and eng == "wf" and not wf_style_i2i_ok(cfg, st_):
            err = ComfyError("「%s」这份工作流接不了参考图" % nm)
            skipped = nm          # 不在这里定话术：后面档可能照样吃图（18:00 实测）
            log.info("图生图：%s 档结构上接不了图，跳到下一档", nm)
            continue
        bw = int(st_.get("width") or size.get("width") or 640)
        bh = int(st_.get("height") or size.get("height") or 832)
        long_edge = max(bw, bh)
        if aspect:
            # 显式比例优先：以档位长边为基准缩放，保持用户指定的长宽比
            wh = _aspect_to_wh(aspect, long_edge)
            w, h = wh if wh else (bw, bh)
        elif landscape and st_.get("landscape_swap", True) and bh > bw:
            # 老「横图」开关：宽高对调（向后兼容）
            w, h = bh, bw
        else:
            # 默认比例读**配置**（comfyui.default_aspect，网页可改，主人 2026-10-04 要求）；
            # 配「（跟原图）」或配坏了才回落 9:16。画同款路径会传原图比例覆盖这里。
            _dflt = cfg.get("default_aspect") or ASPECT_DEFAULT
            if str(_dflt).strip() in ("（跟原图）", "跟原图", "auto", ""):
                _dflt = ASPECT_DEFAULT
            wh = _aspect_to_wh(_dflt, long_edge)
            w, h = wh if wh else _aspect_to_wh(ASPECT_DEFAULT, long_edge) or (bw, bh)
        artist_tag = ""
        # 成品串（用户自己写好的 tag / @画师）→ 不润色、不自动配画师，原样送工作流
        plain_tagline = not want_enhance(prompt, cfg.get("enhance", True))
        # ⚠️ 写实档图生图必须**强行走润色**（2026-09-19 实测根因，别再删）：
        # 反推串天生就是「成品 tag 串」的样子（逗号堆叠的 danbooru 式 tag），于是被
        # want_enhance() 判成成品串 → 原样送模型。而 krea2 turbo（cfg=1，没有 CFG 放大）
        # 见到这种 tag 串就走平涂动漫画风 —— **跟参考图、denoise、LoRA 全无关**。
        # 对照实测（同图 / 同 seed / 同 LoRA 栈 / krea2 档）：
        #   tags 串            denoise 0.80 / 0.90 / 0.95 / 0.99 → 四张全是动漫平涂
        #   同一串去掉 anime 词 → 还是动漫（所以「剔 anime 词」不是解）
        #   换成自然语言长句     denoise 0.80               → 直接真人照片
        # 故：写实档 i2i 一律先润色成自然语言长句（krea2 口味就是这么定的）。
        force_nl = bool(i2i and eng != "anima" and cfg.get("enhance", True))
        if force_nl:
            log.info("写实档图生图：反推串按「自然语言长句」润色再送模型（tag 串会让 krea2 出动漫，%d 字）",
                     len(prompt))
            plain_tagline = False
        elif plain_tagline:
            log.info("成品 tag 串：跳过润色与画师选配，原样送工作流（%d 字）", len(prompt))
        # force_nl 时故意不选配画师：主人 2026-09-17 定「非 anima 档图生图完全不加 tag」，
        # 而且 @画师 tag 混进写实档反而会把画风拽回二次元。
        if eng in ("anima", "wf") and cfg.get("enhance", True) and not plain_tagline \
                and not force_nl:
            # 画师/角色 tag 按主题选配：内置 anima 档换掉档位里写死的 prompt_artist；
            # wf 档并进工作流的「质量/画师」格（见 graph_workflow 的 artist 参数）
            artist_tag = pick_artist(cfg, st_, prompt, brief, llm, web_llm,
                                     dex_artist=(book.artist_for(names)
                                                 if book is not None else ""),
                                     dex_tags=book.tags_for(names) if book is not None else "")
            if artist_tag and eng == "anima":
                st_ = dict(st_)
                st_["prompt_artist"] = artist_tag if artist_tag.endswith(",") \
                    else artist_tag + ","
        text = prompt
        if not plain_tagline:
            # 润色词有两种口味：krea2=自然语言长句 / anima=模板槽位式 tag 行。
            # wf 档默认跟着 krea2，想换在档位里写 enhance_style。
            etaste = st_.get("enhance_style") or ("anima" if eng == "anima" else "krea2")
            text = enhance(prompt, llm, etaste, brief=brief)
            if force_nl and text == prompt:
                # 润色没生效（LLM 挂了 / 输出太短）→ 退回 tag 串，等于一定出动漫，至少留个痕
                log.warning("写实档图生图：润色没生效，反推串原样送出（这次大概率出动漫）")
        seed = random.randrange(0, MAX_SEED_NUM)
        _built = [None]                    # 让 except 能拿到 g（构建失败时是 None）
        try:
            g, ow, oh = _build(cfg, st_, eng, text, w, h, seed, opt, artist_tag,
                               image=i2i, denoise=dnz)
            _built[0] = g
            lim = float(timeout or st_.get("timeout_sec") or cfg.get("timeout_sec", 300))
            # PNG 元数据：有原始界面工作流就写进 `workflow` 块（能拖回 ComfyUI 打开）；
            # 拿不到（内置档脚本拼图）则写 `bot_workflow_snapshot`——**不冒充workflow**，
            # 因为快照节点位置是排出来的、打不开，塞进 workflow 键会骗前端。
            # 覆盖面：全项目只有这一个 /prompt 提交点 ⇒ 出图 + 图生图 + 视频全部吃到。
            ui_wf = _take_ui_workflow(g)
            _extra = None
            if png_meta_enabled(cfg):
                try:
                    _extra = png_extra_pnginfo(g, ui_wf=ui_wf, text=text,
                                               negative=negative, seed=seed,
                                               size=(ow, oh), style=nm, timeout=lim)
                except Exception:
                    _extra = None
            else:
                # 关掉了也别让原始工作流白暂存/白占内存
                ui_wf = None
            entry, secs = _run(b, g, lim, "%s/%s" % (eng, nm), extra=_extra)
            imgs = _images_of(entry)
            im = imgs[0]
            f = _save_image(b, im, out_dir or cfg.get("save_dir") or ".", prompt)
        except ComfyError as e:
            err = e
            _drop_ui_workflow(_built[0])      # 构建/提交失败：清掉暂存，别攒内存
            if i + 1 < len(attempts) and cfg.get("retry_light", True):
                log.warning("%s 档跑挂了（%s），换轻量档重画", nm, e)
                note = "%s 档这次没跑成，已用轻量档补了一张" % nm
                continue
            _free(b)
            raise
        _free(b)
        if skipped:
            note = "「%s」档接不了参考图，已换「%s」档画（参考图正常生效）" % (skipped, nm)
        log.info("出图完成 %s（%.1fs，%dx%d，%s/%s%s）", f.name, secs, ow, oh, eng, nm,
                 "，图生图 denoise=%.2f" % dnz if i2i else "")
        return {"file": str(f), "name": f.stem, "seconds": round(secs, 1),
                "i2i": bool(i2i), "denoise": round(dnz, 2),
                "prompt_used": text, "size": [ow, oh], "comfy_name": im["filename"],
                "comfy_out": [{"filename": x.get("filename"), "subfolder": x.get("subfolder") or "",
                               "type": x.get("type") or "output"} for x in imgs],
                "style": nm, "engine": eng, "note": note, "subject": names,
                "artist_pick": artist_tag}
    raise err or ComfyError("没画出图")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import argparse
    ap = argparse.ArgumentParser(description="裸测本地 ComfyUI 出图")
    ap.add_argument("prompt", nargs="?", default="一个穿汉服的猫，二次元")
    ap.add_argument("--style", default="")
    ap.add_argument("--landscape", action="store_true", help="横构图（宽高对调）")
    ap.add_argument("--no-enhance", action="store_true", help="不做提示词润色，原样送进模型")
    ap.add_argument("--boot", action="store_true", help="服务没开时先拉起来")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    a = ap.parse_args()
    c = (json.load(open(a.config, encoding="utf-8")) or {}).get("comfyui") or {}
    if a.no_enhance:
        c["enhance"] = False
    print("显存:", vram(c) if up(c) else "服务没开", "\n可用模型目录检查中…")
    if not up(c):
        if not a.boot:
            print("ComfyUI 没在跑；加 --boot 可以让我拉起来")
            raise SystemExit(1)
        print("拉起服务…", boot(c))
    print("档位 %s 预计 %ds（冷机 %ds）" % (a.style or c.get("default_style"),
                                          *est_seconds(c, a.style or None)))
    print(json.dumps(generate(c, a.prompt, a.style, landscape=a.landscape),
                     ensure_ascii=False, indent=1))
