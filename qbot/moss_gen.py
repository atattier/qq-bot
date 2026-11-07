"""MOSI 官方云出图（第二条生图链路，和 comfy_gen.py 并列）。

背景（2026-10-24 主人定）：
  QQ bot 的生图现在有两条路 ——
    ① comfyui 本地流：要管理员发「画图 开」才让用，吃本地显卡（comfy_gen.py）
    ② moss 云端流：走 MOSI 官方 API，不用开「画图 开」、不占显存（本文件）

接口契约照抄 E:\\AI\\moss-prompt-studio\\server\\mosi.py（那边已跑通，别自己发明）：
  POST https://api.mosi.cn/v1/images/generations
    Authorization: Bearer <moss.api_key>
    {"model": "moss-image-sft-1.0", "prompt": ..., "size": "1024x1024", "n": 1,
     "response_format": "url", "output_watermark": {"enabled": false},
     "negative_prompt": ..., "num_inference_steps": ..., "guidance_scale": ..., "seed": ...}
  → {"data": [{"url": "..."}], "usage": {"output_width": .., "output_height": ..}}

⚠️ 两个坑（Studio 那边踩过，直接继承结论）：
  1. MOSI 的图片 CDN **拒绝带 Referer 的请求**（`denied by Referer ACL`）——
     必须由服务端下回来存成本地文件再发给 QQ，不能让 QQ 直连。
  2. 尺寸硬约束：每边 256~2048、**16 的倍数**、总像素 ≤ 2097152。乱传会 400。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

MOSI_URL = "https://api.mosi.cn/v1/images/generations"
DEFAULT_MODEL = "moss-image-sft-1.0"
DEFAULT_SIZE = "1024x1024"
DEFAULT_STEPS = 30

# 比例 → 尺寸。全部满足「16 的倍数、每边 ≤2048、总像素 ≤2097152」。
_ASPECT_SIZE = {
    "1:1": (1024, 1024),
    "9:16": (864, 1536),
    "16:9": (1536, 864),
    "3:4": (864, 1152),
    "4:3": (1152, 864),
    "2:3": (832, 1248),
    "3:2": (1248, 832),
    "4:5": (896, 1120),
    "5:4": (1120, 896),
    "21:9": (1792, 768),
    "9:21": (768, 1792),
    "6:19": (576, 1824),
    "19:6": (1824, 576),
}

MAX_PIXELS = 2097152


class MossError(RuntimeError):
    pass


def pick_size(aspect=None, size=None):
    """定尺寸：显式 size 优先，其次 aspect 查表，最后默认方图。都会夹到合法范围。"""
    if size:
        try:
            w, h = (int(x) for x in str(size).lower().split("x"))
            return _clamp(w, h)
        except Exception:
            pass
    a = str(aspect or "").strip().lower()
    if a in _ASPECT_SIZE:
        return _ASPECT_SIZE[a]
    # 允许 "9/16" "16-9" 这类写法
    a = a.replace("/", ":").replace("-", ":")
    if a in _ASPECT_SIZE:
        return _ASPECT_SIZE[a]
    # 表里没有的比例（比如参考图算出来 3:5）：按面积≈1.3M 现算一个合法尺寸
    try:
        aw, ah = (float(x) for x in a.split(":")[:2])
        if aw > 0 and ah > 0:
            area = 1310720.0            # ≈1.25M，安全落在 2M 上限内
            w = int((area * aw / ah) ** 0.5)
            h = int((area * ah / aw) ** 0.5)
            return _clamp(w, h)
    except Exception:
        pass
    return _ASPECT_SIZE["1:1"]


def _clamp(w, h):
    """夹到官方约束：16 的倍数、每边 256~2048、总像素 ≤ 2097152。"""
    w = max(256, min(2048, int(w) // 16 * 16 or 256))
    h = max(256, min(2048, int(h) // 16 * 16 or 256))
    # 超总像素就等比缩（保持比例）
    while w * h > MAX_PIXELS:
        if w >= h:
            w = max(256, w // 16 * 16 - 16)
        else:
            h = max(256, h // 16 * 16 - 16)
        if w <= 256 and h <= 256:
            break
    return w, h


def available(cfg):
    """moss 这条路现在能不能用（只看有没有 key）。"""
    return bool(((cfg or {}).get("api_key") or "").strip())


def _post(url, payload, key, timeout=300):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 # 不带 Referer：MOSI 的 CDN 对 Referer 敏感，API 这边也保持干净
                 "User-Agent": "qq-bot/moss_gen"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _download(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": "qq-bot/moss_gen"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def generate(cfg, prompt, aspect=None, size=None, negative=None, steps=None,
             cfg_scale=None, seed=None, out_dir=None, timeout=300, safety_retry=None):
    """出一张图，落到 out_dir。返回结构跟 comfy_gen.generate 对齐，调用方不用分叉。

    cfg 是 config.json 的 `moss` 段：
      {"api_key": "...", "model": "moss-image-sft-1.0", "size": "1024x1024",
       "steps": 30, "negative": "...", "prompt_prefix": "best quality, masterpiece"}
    返回 {file, name, seconds, prompt_used, size:(w,h), style, engine:"moss", note, subject}

    `safety_retry`：可选的 `callable(prompt) -> 新 prompt`。
      🔴 2026-10-31：MOSI 对含成人向描述的提示词直接 **403 safety policy error**
         （实测「revealing clothing / suggestive pose」必拦，「三个女孩站公园」正常）。
         主人发「画张群友涩图」当场就是这句 403，群里只看到一行天书。
         上游策略改不了，但**不能就这样把图丢掉** —— 拦了就调 `safety_retry`
         把提示词改写成清水版（主体、构图、配色不动，只去掉成人向描述）再试一次，
         成图后在 caption 里**明说这是清水版**，不糊弄主人。
    """
    cfg = cfg or {}
    key = (cfg.get("api_key") or "").strip()
    if not key:
        raise MossError("没配 MOSI 出图 Key（config.json 的 moss.api_key）")
    text = (prompt or "").strip()
    if not text:
        raise MossError("描述是空的")
    prefix = (cfg.get("prompt_prefix") or "").strip()
    if prefix and prefix.lower() not in text.lower():
        text = prefix + ", " + text


    # 尺寸优先级：显式 size > 显式 aspect > 配置默认 size > 方图。
    # ⚠️ 2026-10-24 踩过：原来写成 `size=size or cfg["size"] or DEFAULT`，
    #    配置里默认 1024x1024 把调用方传的 aspect='9:16' 直接盖掉，竖图全变方图。
    if size:
        w, h = pick_size(size=size)
    elif aspect:
        w, h = pick_size(aspect=aspect)
    else:
        w, h = pick_size(size=cfg.get("size") or DEFAULT_SIZE)
    payload = {
        "model": (cfg.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "prompt": text,
        "size": "%dx%d" % (w, h),
        "n": 1,
        "response_format": "url",
        "output_watermark": {"enabled": bool(cfg.get("watermark"))},
    }
    neg = (negative if negative is not None else cfg.get("negative") or "").strip()
    if neg:
        payload["negative_prompt"] = neg
    st = steps if steps is not None else cfg.get("steps")
    if st:
        payload["num_inference_steps"] = max(1, min(int(st), 100))
    cs = cfg_scale if cfg_scale is not None else cfg.get("cfg_scale")
    if cs is not None:
        try:
            payload["guidance_scale"] = max(0.0, min(float(cs), 10.0))
        except Exception:
            pass
    if seed is not None:
        payload["seed"] = int(seed)

    # ---- 请求：被安全策略拦（403/400 safety）就改写清水版再试一次 ----
    # 🔴 2026-10-31：MOSI 对成人向描述直接 403 safety policy error（实测必拦）。
    #    上游策略改不了，但不能就这样把图丢掉 —— 拦了就把提示词改写成清水版
    #    （主体、构图、配色不动，只去掉成人向描述）再试一次，成图后 caption 明说是清水版。
    _sent = text
    _safe_mode = False
    _safe_err = ""
    t0 = time.time()
    for attempt in (1, 2):
        payload["prompt"] = _sent
        try:
            data = _post(MOSI_URL, payload, key, timeout=timeout)
            break
        except urllib.error.HTTPError as e:
            # 🔴 2026-10-31：判据照官方错误码表补全（`MOSS官方API接入.md` §7）：
            #    403 `safety_guardrail_blocked` = 平台安全审核拦截（实测提示词原文只写
            #    `safety policy error`，没有 code 字段，所以既要认 code 也要认文案）。
            #    403 还有一种是 Key 无效/权限不足（`forbidden`），**那个不能当内容拦截**，
            #    否则会白改写一遍提示词还照样失败。
            try:
                _raw = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                _raw = ""
            _code = ""
            try:
                _j = json.loads(_raw)
                _code = str((_j.get("error") or {}).get("code") or _j.get("code") or "")
                detail = str((_j.get("error") or {}).get("message")
                             or _j.get("message") or _raw)
            except Exception:
                detail = _raw
            _hay = (_code + " " + detail)
            _key_bad = bool(re.search(r"forbidden|unauthor|invalid.{0,6}key|permission",
                                      _hay, re.I)) and \
                not re.search(r"safety|guardrail", _hay, re.I)
            _blocked = (not _key_bad) and bool(
                re.search(r"safety|guardrail|moderat|policy|sensitive|violat",
                          _hay, re.I))
            if not _blocked:
                raise MossError("上游 %s：%s" % (e.code, detail)) from None
            if attempt == 2 or not safety_retry:
                extra = "（清水版改写失败：%s）" % _safe_err if _safe_err else ""
                raise MossError(
                    "上游安全策略拦了这条描述（403）。成人向的词它一定不让过，"
                    "改写清水版也没成%s；换个说法我再画。" % extra) from None
            try:
                safe = (safety_retry(_sent) or "").strip()
            except Exception as ex:
                _safe_err = "%s: %s" % (type(ex).__name__, str(ex)[:60])
                raise MossError(
                    "上游安全策略拦了这条描述（403），改写清水版也失败（%s）；"
                    "换个说法我再画。" % _safe_err) from None
            if not safe or safe.lower() == _sent.lower():
                raise MossError(
                    "上游安全策略拦了这条描述（403），改写不出清水版；换个说法我再画。") from None
            _sent, _safe_mode = safe, True
            t0 = time.time()          # 计时重来，别把被拦那次的时间算进去
        except Exception as e:
            raise MossError("请求失败：%s: %s" % (type(e).__name__, e)) from None

    try:
        remote = data["data"][0]["url"]
    except Exception:
        raise MossError("上游没给图片 URL：%s" % json.dumps(data, ensure_ascii=False)[:200])

    try:
        blob = _download(remote)
    except Exception as e:
        raise MossError("图下不下来（%s: %s）" % (type(e).__name__, e)) from None

    out = Path(out_dir) if out_dir else Path.cwd()
    out.mkdir(parents=True, exist_ok=True)
    name = "moss_" + uuid.uuid4().hex[:12]
    f = out / (name + ".png")
    f.write_bytes(blob)

    usage = data.get("usage") or {}
    rw = int(usage.get("output_width") or w)
    rh = int(usage.get("output_height") or h)
    return {
        "file": str(f),
        "name": name,
        "seconds": round(time.time() - t0, 1),
        "prompt_used": _sent,
        "size": (rw, rh),
        "style": "moss",
        "engine": "moss",
        "note": ("上游拦了成人向描述，这张是清水版重画的" if _safe_mode else ""),
        "safe_mode": _safe_mode,
        "subject": None,
        "remote": remote,
        "model": payload["model"],
    }


# 2026-11-05 U11 转正：bot.py:9504（moss studio）在调，事实公共 API；旧名留别名一个版本周期。
_pick_size = pick_size
