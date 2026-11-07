# -*- coding: utf-8 -*-
"""网易云取源 + 翻唱执行（bot 侧，纯标准库）。

    用户发「翻唱 起风了」/「翻唱 <网易云链接>」/「翻唱 <歌曲ID>」
        ↓
    parse_request → search/detail（匿名接口，绕开系统代理）
        ↓
    fetch 下载原曲 → ffprobe 体检（预览片段 <60s 直接拒）
        ↓
    run_pipeline 调 <GPT-SoVITS 目录>/cover.py（demucs 分离 → Seed-VC 换声 → 混音）
        ↓
    bot 侧上传 mp3

为什么单独一个模块：bot.py 已经 1.1 万行，取源/下载/子进程这些和 QQ 无关的事
放这里能单测，也不会把 bot 的解释器拖进 torch（子进程用 GPT-SoVITS 自己的 venv）。

对外接口：
    parse_request(text)                  → {"kind": "id"|"query", "value": ...}
    search(query, limit=5)               → [Song, ...]
    detail(song_id)                      → Song
    fetch(song_id, dest_dir)             → (path, content_type)
    probe(path, ffprobe=None)            → {duration, ...}
    validate_audio(info, min_sec, max_sec)
    run_pipeline(song, out_mp3, ...)     → {"ok": True, "elapsed": ..., "log": ...}
    work_dir_for(song) / cleanup_work(song) / cleanup_outputs(dir, keep)
"""
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("qqbot")

SOUVITS_HOME = Path(os.environ.get("QBOT_SOVITS_HOME")
                      or (Path.home() / "GPT-SoVITS"))
COVER_PY = SOUVITS_HOME / "cover.py"
VENV_PY = SOUVITS_HOME / ".venv" / "Scripts" / "python.exe"
WORK_ROOT = SOUVITS_HOME / "cover_work"
DEFAULT_TARGET = SOUVITS_HOME / "refs" / "yuki_ref25.wav"

# 网易云匿名接口偶发被系统代理（Clash）拦成 502，本地直连反而稳
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://music.163.com/",
    "Accept": "*/*",
}
_SEARCH_API = "https://music.163.com/api/search/get/web?s=%s&type=1&limit=%d"
_DETAIL_API = "https://music.163.com/api/song/detail?ids=%%5B%d%%5D"
_OUTER_URL = "https://music.163.com/song/media/outer/url?id=%d.mp3"

_LINK_RE = re.compile(r"music\.163\.com/(?:#/)?(?:m/)?song(?:/|\?|#)[^\s]*?(?:id=)?(\d{4,})", re.I)
_BARE_ID_RE = re.compile(r"^\d{5,10}$")
_CIRCLED = {"①": 1, "②": 2, "③": 3, "④": 4}

STAGE_NAMES = {1: "体检音源", 2: "分离人声/伴奏", 3: "音色转换", 4: "混音导出"}


class CoverError(Exception):
    """可直接展示给用户的翻唱错误（文案已说人话）。"""

    def __init__(self, message, stage=0):
        super().__init__(message)
        self.stage = stage


def parse_request(text):
    """把用户输入拆成 id 或搜索词。返回 {"kind","value"}。

    · 网易云链接（含 #/song、y.music、m/song）→ id
    · 纯数字（>=5 位）→ id（歌单号/用户号会查不到，由调用方给失败文案）
    · 其余 → 搜索词（去掉「网易云」「音乐」这类前缀）
    """
    t = (text or "").strip()
    t = re.sub(r"^[\s:：,，]+", "", t)
    m = _LINK_RE.search(t)
    if m:
        return {"kind": "id", "value": int(m.group(1))}
    t = re.sub(r"^(?:网易云音乐|网易云|网抑云)\s*", "", t, flags=re.I).strip()
    t = re.sub(r"^(?:一下|一首歌|一首|这首歌|这首|歌)\s*", "", t).strip()
    if _BARE_ID_RE.match(t):
        return {"kind": "id", "value": int(t)}
    t = re.sub(r"\s+", " ", t).strip(" -—")
    if not t:
        raise CoverError("要翻哪首？发「翻唱 歌名」或网易云链接给我。")
    if len(t) > 60:
        raise CoverError("歌名太长了，直接发网易云链接或歌曲 ID 吧。")
    return {"kind": "query", "value": t}


def _get_json(url, timeout=15):
    req = urllib.request.Request(url, headers=_HEADERS)
    with _OPENER.open(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8", "replace") or "{}")


def _song_from(item):
    artists = [a.get("name") for a in (item.get("artists") or item.get("ar") or [])
               if isinstance(a, dict) and a.get("name")]
    album = item.get("album") or item.get("al") or {}
    dur = item.get("duration") or item.get("dt") or 0
    return {
        "id": int(item.get("id") or 0),
        "name": str(item.get("name") or "").strip(),
        "artist": " / ".join(artists),
        "album": str(album.get("name") or "").strip(),
        "duration_sec": float(dur) / 1000.0 if dur else 0.0,
        "fee": int(item.get("fee") or 0),
    }


def search(query, limit=5):
    url = _SEARCH_API % (urllib.parse.quote(query), max(1, min(int(limit), 10)))
    d = _get_json(url)
    if int(d.get("code") or 0) not in (200, 0):
        raise CoverError("网易云搜索失败了（code=%s）" % d.get("code"))
    songs = ((d.get("result") or {}).get("songs") or [])
    return [s for s in (_song_from(x) for x in songs) if s["id"] and s["name"]]


_PUNCT_RE = re.compile(r"[\s\-—_·・,，。.、:：;；!！?？'\"“”‘’()（）\[\]【】《》<>/\\|+&]+")


def _norm(s):
    return _PUNCT_RE.sub("", (s or "").lower())


def _coverage(query, text):
    qn = set(_norm(query))
    if not qn:
        return 0
    return len(qn & set(_norm(text)))


def search_candidates(query, limit=5, min_sec=60.0, max_sec=420.0):
    """搜索候选版本，按与输入的字面重合度排序，交给用户挑。

    网易云对「雨爱司凤版」这种把版本名粘在歌名后面的输入很笨：原样搜只回
    一堆 DJ 版；把尾部「版」去掉再搜，才会出现《雨爱·司凤》。所以额外搜
    一遍去掉尾部「版」的写法，再按字面覆盖度合并排序。
    会员歌（fee=1，匿名拿不到音源）和试听片段直接过滤，选中也做不了。
    """
    q = (query or "").strip()
    variants = [q]
    if q.endswith("版") and len(q) > 1:
        variants.append(q[:-1])
    found = {}
    order = 0
    for v in variants:
        try:
            hits = search(v, limit=10)
        except CoverError:
            if len(variants) == 1:
                raise
            continue
        for s in hits:
            if s["id"] in found or s.get("fee") == 1:
                continue
            d = float(s.get("duration_sec") or 0)
            if d and (d < min_sec or d > max_sec):
                continue
            s = dict(s)
            s["_cov"] = _coverage(query, s["name"] + s["artist"])
            s["_order"] = order
            order += 1
            found[s["id"]] = s
    out = sorted(found.values(), key=lambda s: (-s["_cov"], s["_order"]))
    for s in out:
        s.pop("_cov", None)
        s.pop("_order", None)
    return out[:max(1, min(int(limit), 10))]


def detail(song_id):
    d = _get_json(_DETAIL_API % int(song_id))
    songs = d.get("songs") or []
    if not songs:
        raise CoverError("网易云里找不到这首歌（ID %s）" % song_id)
    return _song_from(songs[0])


def _audio_request(song_id):
    return urllib.request.Request(_OUTER_URL % int(song_id), headers=_HEADERS)


_LYRIC_URL = ("https://music.163.com/api/song/lyric"
              "?id=%d&lv=1&kv=1&tv=-1")


def fetch_lyrics(song_id, dest_dir):
    """拉 LRC 歌词（行级时间戳），返回 Path 或 None。

    2026-10-25：翻唱要「只唱歌词」。人声轨里除了主唱还有前奏垫音、英文背景人声、
    气声、ad-lib，它们落在歌词的空隙里，换声后就成了第二个人在唱。
    歌词时间轴是唯一可靠的判据 —— cover.py 拿它做门控。
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / ("%d.lrc" % int(song_id))
    try:
        req = urllib.request.Request(_LYRIC_URL % int(song_id), headers=_HEADERS)
        with _OPENER.open(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        lrc = ((d.get("lrc") or {}).get("lyric") or "").strip()
        if "[" not in lrc:
            log.info("歌词为空 id=%s（纯音乐或没上传歌词）", song_id)
            return None
        dest.write_text(lrc, encoding="utf-8")
        return dest
    except Exception as e:  # noqa: BLE001  歌词是增强项，拿不到就跳过门控
        log.warning("拉歌词失败 id=%s: %s", song_id, e)
        return None


def fetch(song_id, dest_dir, timeout=90, max_mb=40):
    """下载原曲到 dest_dir，返回 (Path, content_type)。只收真音频。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / ("%d.mp3" % int(song_id))
    tmp = dest.with_suffix(".part")
    t0 = time.time()
    try:
        with _OPENER.open(_audio_request(song_id), timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype or "application/json" in ctype:
                raise CoverError("这首歌没有可直接播放的音频（要会员/无版权），换一首吧。")
            total = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_mb * 1024 * 1024:
                        raise CoverError("文件超过 %dMB，不下了。" % int(max_mb))
                    f.write(chunk)
        if total < 100 * 1024:
            raise CoverError("下到的音频只有 %dKB，多半是网易云的试听/占位文件。" % (total // 1024))
        os.replace(str(tmp), str(dest))
        log.info("网易云下载完成 id=%s %.1fMB %.1fs", song_id, total / 1048576.0, time.time() - t0)
        return dest, ctype
    except CoverError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    except Exception as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise CoverError("下载原曲失败：%s" % str(e)[:120])


def _ffprobe_exe(ffprobe=None):
    if ffprobe:
        return ffprobe
    return shutil.which("ffprobe") or "ffprobe"


def probe(path, ffprobe=None):
    """ffprobe 体检。取不到信息返回 {}（调用方当未知处理，不硬拦）。"""
    try:
        out = subprocess.run(
            [_ffprobe_exe(ffprobe), "-v", "error", "-show_entries",
             "format=duration,bit_rate:stream=codec_name,sample_rate,channels",
             "-of", "json", str(path)],
            capture_output=True, text=True, errors="replace", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        d = json.loads(out.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        fmt = d.get("format") or {}
        return {
            "codec": st.get("codec_name"),
            "sample_rate": int(st.get("sample_rate") or 0),
            "channels": int(st.get("channels") or 0),
            "duration": float(fmt.get("duration") or 0),
            "kbps": int(fmt.get("bit_rate") or 0) // 1000,
        }
    except Exception as e:
        log.warning("ffprobe 体检失败 %s: %s", path, e)
        return {}


def validate_audio(info, min_sec=60, max_sec=420):
    """拒绝试听片段和超长歌。info 为空（ffprobe 缺）时放行。"""
    dur = float((info or {}).get("duration") or 0)
    if dur <= 0:
        return
    if dur < float(min_sec):
        raise CoverError("这段音频只有 %.0f 秒，是试听片段，翻唱出来不完整。" % dur)
    if dur > float(max_sec):
        raise CoverError("这首歌 %.0f 分钟，太长了（上限 %d 分钟），换首短点的吧。"
                         % (dur / 60.0, int(float(max_sec) / 60)))


def safe_stem(name):
    """和 cover.py 的 safe_stem 保持一致（用它建的工作目录才找得到）。"""
    ascii_only = re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_")[:32]
    h = hashlib.md5((name or "").encode("utf-8", "replace")).hexdigest()[:8]
    return "%s_%s" % (ascii_only, h) if ascii_only else "track_%s" % h


def work_dir_for(song):
    return WORK_ROOT / safe_stem(Path(song).stem)


def cleanup_work(song):
    try:
        shutil.rmtree(str(work_dir_for(song)), ignore_errors=True)
    except Exception as e:
        log.debug("清理 cover_work 失败: %s", e)


def cleanup_outputs(out_dir, keep=10, max_age_sec=7 * 86400):
    """只留最近 keep 个成品，顺带删 7 天前的。"""
    try:
        d = Path(out_dir)
        files = [p for p in d.glob("*.mp3") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        now = time.time()
        for i, p in enumerate(files):
            if i >= max(int(keep), 1) or now - p.stat().st_mtime > max_age_sec:
                p.unlink(missing_ok=True)
        for p in d.glob("*.log"):
            if now - p.stat().st_mtime > max_age_sec:
                p.unlink(missing_ok=True)
        # 成品 wav 只用于混音中间件，成功上传后 bot 会立即删；这里兜底清旧的
        for p in d.glob("*.wav"):
            if now - p.stat().st_mtime > max_age_sec:
                p.unlink(missing_ok=True)
    except Exception as e:
        log.debug("清理翻唱产物失败: %s", e)


def cleanup_sources(src_dir, max_age_sec=86400):
    try:
        now = time.time()
        for p in Path(src_dir).glob("*"):
            if p.is_file() and now - p.stat().st_mtime > max_age_sec:
                p.unlink(missing_ok=True)
    except Exception as e:
        log.debug("清理翻唱源文件失败: %s", e)


def cleanup_works(max_age_sec=2 * 86400):
    """失败留下的工作目录（几个 wav 就上百 MB）最多留两天。"""
    try:
        now = time.time()
        for p in WORK_ROOT.glob("*"):
            if p.is_dir() and now - p.stat().st_mtime > max_age_sec:
                shutil.rmtree(str(p), ignore_errors=True)
    except Exception as e:
        log.debug("清理 cover_work 失败: %s", e)


def _read_new(f, off):
    try:
        f.seek(off)
        data = f.read()
        return data, f.tell()
    except Exception:
        return "", off


def run_pipeline(song, out_mp3, target=None, steps=25, boost=3.0, timeout_sec=1800,
                 on_stage=None, python=None, cover_py=None, lrc=None):
    """跑 cover.py，边跑边把阶段进度回调出去。

    cover.py 内部每个阶段都是独立子进程，成功/失败都会把日志写进工作目录；
    这里只负责拉起、转发阶段、超时杀掉、把失败原因压成一句人话。

    lrc: 歌词文件路径。给了就让 cover.py 做歌词门控（只唱歌词、切掉垫音）；
         不给时 cover.py 会自己按数字文件名去网易云拉。
    """
    if os.name == "nt" and not python:
        python = str(VENV_PY)
    py = python or "python"
    if not Path(py).exists() and not shutil.which(py):
        raise CoverError("GPT-SoVITS 的 venv 解释器不见了：%s" % py, stage=0)
    cover = Path(cover_py or COVER_PY)
    if not cover.exists():
        raise CoverError("翻唱脚本不存在：%s" % cover, stage=0)
    out_mp3 = Path(out_mp3).resolve()
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    target = str(target or DEFAULT_TARGET)
    if not Path(target).exists():
        raise CoverError("目标音色参考音频不存在：%s" % target, stage=0)

    cmd = [py, "-u", str(cover), str(Path(song).resolve()),
           "--target", target, "--out", str(out_mp3),
           "--steps", str(int(steps)), "--boost", str(float(boost))]
    if lrc:
        cmd += ["--lrc", str(lrc)]
    log_path = out_mp3.with_suffix(".log")
    t0 = time.time()
    stage = 0
    log.info("翻唱启动：%s -> %s", song, out_mp3)
    with open(log_path, "w", encoding="utf-8", errors="replace") as lf:
        p = subprocess.Popen(cmd, cwd=str(SOUVITS_HOME), stdout=lf, stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        off = 0
        try:
            while True:
                rc = p.poll()
                time.sleep(1.0)
                with open(log_path, "r", encoding="utf-8", errors="replace") as rf:
                    text, off = _read_new(rf, off)
                for line in (text or "").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    m = re.search(r"阶段\s*([①②③④1-4])", line)
                    if m:
                        n = _CIRCLED.get(m.group(1)) or int(m.group(1))
                        if n != stage:
                            stage = n
                            if on_stage:
                                try:
                                    on_stage(n, STAGE_NAMES.get(n, "处理中"))
                                except Exception as e:
                                    log.debug("阶段回调失败: %s", e)
                if rc is not None:
                    break
                if timeout_sec and time.time() - t0 > float(timeout_sec):
                    raise CoverError("翻唱超时了（超过 %d 分钟），已经停下。"
                                     % int(float(timeout_sec) / 60), stage=stage)
        finally:
            if p.poll() is None:
                try:
                    p.terminate()
                    p.wait(timeout=8)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass

    elapsed = time.time() - t0
    if p.returncode != 0:
        tail = []
        try:
            tail = [x for x in Path(log_path).read_text(
                encoding="utf-8", errors="replace").splitlines() if x.strip()][-6:]
        except Exception:
            pass
        where = ("阶段%s（%s）" % (stage, STAGE_NAMES.get(stage, ""))) if stage else "启动阶段"
        raise CoverError("%s失败：%s" % (where, " / ".join(x.strip()[:90] for x in tail[-2:])
                                        or ("退出码 %s" % p.returncode)), stage=stage)
    if not out_mp3.exists() or out_mp3.stat().st_size < 100 * 1024:
        raise CoverError("混音跑完了但没有成品文件（或文件过小）。", stage=4)
    log.info("翻唱完成：%s %.1fs %.1fMB", out_mp3, elapsed,
             out_mp3.stat().st_size / 1048576.0)
    return {"ok": True, "out": str(out_mp3), "elapsed": elapsed,
            "log": str(log_path), "stage": stage}
