# -*- coding: utf-8 -*-
"""语音合成（bot 侧）。

架构：**GPT-SoVITS 作为独立常驻服务**跑在 <GPT-SoVITS 目录>（自带 venv，
监听 127.0.0.1:9880），bot 只通过 HTTP 调它 —— 所以 bot 的解释器不需要 torch/CUDA。
edge-tts 已装在 bot 解释器内，作「服务没起 / 合成失败」时的兜底。

对外接口：
    up(cfg)                  GPT-SoVITS 服务是否在线
    synth(text, cfg, tag)    合成语音，返回音频文件绝对路径；失败返回 None
    engine_of(cfg)           当前生效引擎名（sovits / edge）
"""
import asyncio
import json
import logging
import re
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("qqbot")

BASE = Path(__file__).resolve().parent.parent
# 本地服务一律绕开系统代理（否则 127.0.0.1 会被 Clash 拦成 502）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 消息里的占位标记（[图:xxx] 之类）念不出来，合成前剔掉
_MARK_RE = re.compile(r"\[(?:图|img|image|表情|语音|视频|卡片消息|文件)[:：]?\s*[^\]]{0,30}?\]")
_URL_RE = re.compile(r"https?://\S+")
_CODE_RE = re.compile(r"```.*?```", re.S)

DEFAULT_EDGE_VOICE = "zh-CN-XiaoxiaoNeural"


def vcfg(cfg):
    """取 config.voice 段（缺省给一份安全默认）。"""
    return (cfg or {}).get("voice") or {}


def out_dir(cfg):
    """语音落盘目录。**一定返回绝对路径**。

    🔴 2026-10-18 实测：config 里写的是相对路径 `state/tts`，于是产出的 mp3 路径也是
    相对的（`state\\tts\\grp_xxx.mp3`），发给 NapCat 直接被拒：
    `文件处理失败 识别URL失败, uri= state\\tts\\grp_1792321755491.mp3`
    （NapCat 的 handleOb11FileLikeMessage 只认 file:// / http(s):// / base64:// 和
    绝对本地路径，**相对路径当成 URL 解析，必然失败**）⇒ 语音根本发不出去。
    所以这里统一把相对路径按项目根（BASE）解析成绝对路径。
    """
    raw = vcfg(cfg).get("out_dir") or ""
    d = Path(raw) if raw else (BASE / "state" / "tts")
    if not d.is_absolute():
        d = BASE / d
    d.mkdir(parents=True, exist_ok=True)
    return d


def engine_of(cfg):
    v = vcfg(cfg)
    return (v.get("engine") or "sovits").lower()


def clean(text):
    """把消息文本洗成「适合念出来」的样子。"""
    t = text or ""
    t = _CODE_RE.sub(" ", t)
    t = _URL_RE.sub(" ", t)
    t = _MARK_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    # 去掉常见 markdown 装饰
    t = t.strip("*_~`#> ")
    return t


def _sovits_cfg(cfg):
    return vcfg(cfg).get("sovits") or {}


def up(cfg, timeout=3):
    """GPT-SoVITS 服务在不在（问 /docs，FastAPI 默认有）。"""
    url = (_sovits_cfg(cfg).get("url") or "http://127.0.0.1:9880").rstrip("/")
    try:
        req = urllib.request.Request(url + "/docs", headers={"User-Agent": "qqbot"})
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status < 500
    except Exception:
        return False


def _gain_wav(path, gain, target_peak=0.95):
    """把 16bit PCM WAV 整体放大，并做峰值保护。

    (2026-10-19) 参考音频换成「唱歌片段」后，合成结果普遍偏轻——实测 RMS 0.023
    vs 现役 yuki 的 0.066，约 -9dB，直接发到 QQ 会听不清。这里按 config 的
    voice.sovits.gain 补回来。只用标准库，不往 bot 解释器里塞 numpy。
    """
    try:
        gain = float(gain or 1.0)
    except Exception:
        return
    if abs(gain - 1.0) < 1e-3:
        return
    import array
    import wave

    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2:      # 只处理 16bit
                return
            nch, sr = w.getnchannels(), w.getframerate()
            raw = w.readframes(w.getnframes())
        a = array.array("h")
        a.frombytes(raw)
        if not len(a):
            return
        peak = max(max(a), -min(a)) or 1
        lim = int(32767 * target_peak)
        scale = gain if int(peak * gain) <= lim else lim / peak
        if abs(scale - 1.0) < 1e-3:
            return
        out = array.array(
            "h", (max(-32768, min(32767, int(v * scale))) for v in a))
        with wave.open(str(path), "wb") as w:
            w.setnchannels(nch)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(out.tobytes())
    except Exception as e:  # noqa: BLE001  # 音量是锦上添花，失败不能连累发声
        log.debug("音量调整跳过: %s", e)


def _synth_sovits(text, cfg, out_path):
    s = _sovits_cfg(cfg)
    ref = s.get("ref_audio") or ""
    if not ref:
        log.error("GPT-SoVITS 未配置参考音频（voice.sovits.ref_audio）")
        return None
    url = (s.get("url") or "http://127.0.0.1:9880").rstrip("/")
    payload = {
        "text": text,
        "text_lang": s.get("text_lang") or "zh",
        "ref_audio_path": ref,
        "prompt_text": s.get("prompt_text") or "",
        "prompt_lang": s.get("prompt_lang") or "zh",
        "media_type": "wav",
        "streaming_mode": False,
        "text_split_method": s.get("text_split_method") or "cut5",
        "batch_size": 1,
        "speed_factor": float(s.get("speed") or 1.0),
    }
    if s.get("seed") is not None:
        payload["seed"] = int(s["seed"])
    timeout = float(s.get("timeout_sec") or 60)
    try:
        req = urllib.request.Request(
            url + "/tts",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with _OPENER.open(req, timeout=timeout) as r:
            data = r.read()
        if len(data) < 1024:
            log.warning("GPT-SoVITS 返回过短（%d 字节），判为失败", len(data))
            return None
        out_path.write_bytes(data)
        _gain_wav(out_path, s.get("gain") or 1.0)
        return str(out_path)
    except Exception as e:
        log.warning("GPT-SoVITS 合成失败: %s", e)
        return None


def _synth_edge(text, cfg, out_path):
    e = vcfg(cfg).get("edge") or {}
    try:
        import edge_tts
    except Exception as ex:
        log.error("edge_tts 不可用: %s", ex)
        return None
    voice = e.get("voice") or DEFAULT_EDGE_VOICE
    rate = e.get("rate") or "+0%"
    pitch = e.get("pitch") or "+0Hz"

    async def _run():
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await comm.save(str(out_path))

    try:
        asyncio.run(_run())
    except Exception as ex:
        log.error("edge-tts 合成失败: %s", ex)
        return None
    return str(out_path) if out_path.exists() and out_path.stat().st_size > 512 else None


def synth(text, cfg, tag="tts", strict_sovits=False):
    """合成语音，返回文件绝对路径；失败返回 None。

    引擎按 config.voice.engine 走；sovits 失败自动回落 edge-tts（保证「有声音」比「声线对」优先）。
    `strict_sovits=True` 时不回落 —— 用户点播要的是 Yuki 的声音，换成微软小晓
    会让人以为声线坏了，宁可回一句「没合成出来」。
    """
    v = vcfg(cfg)
    if not v.get("enabled", False):
        return None
    t = clean(text)
    if not t:
        return None
    maxc = int(v.get("max_chars") or 60)
    if maxc > 0 and len(t) > maxc:
        t = t[:maxc]
    d = out_dir(cfg)
    stamp = "%s_%d" % (tag, int(time.time() * 1000))
    res = None
    if engine_of(cfg) == "sovits":
        p = _synth_sovits(t, cfg, d / (stamp + ".wav"))
        if p:
            res = p
        elif strict_sovits:
            log.info("点播语音要求原声线，不回落 edge-tts")
            return None
        else:
            log.info("回落 edge-tts")
    if not res:
        res = _synth_edge(t, cfg, d / (stamp + ".mp3"))
    if res:
        cleanup(cfg)
    return res


def cleanup(cfg, keep=None):
    """只留最近 keep 个音频（默认 config.voice.keep 或 20）。"""
    try:
        k = int(keep if keep is not None else (vcfg(cfg).get("keep") or 20))
        files = sorted(out_dir(cfg).glob("*.wav")) + sorted(out_dir(cfg).glob("*.mp3"))
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[max(k, 1):]:
            try:
                p.unlink()
            except OSError:
                pass
    except Exception as e:
        log.debug("tts cleanup 失败: %s", e)


if __name__ == "__main__":  # 手工自测：python tts.py "要念的话"
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg_path = BASE / "config.json"
    conf = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    txt = sys.argv[1] if len(sys.argv) > 1 else "哥哥，我在这里呢。"
    print("服务在线:", up(conf))
    print("引擎:", engine_of(conf))
    t0 = time.time()
    out = synth(txt, conf, tag="selftest")
    print("产物:", out, "耗时 %.2fs" % (time.time() - t0))
