# -*- coding: utf-8 -*-
"""qbot_meme —— 本地 meme 生成服务的 Bot 侧客户端（阶段 3-C）。

⚠️ 和 qbot_skills / qbot_slang 一样：**本模块不 import bot**，宿主能力（配置、
发图、把图片段落成本地文件、日志）全部靠 bind() 注入。生产 Bot 跑在
Python 3.14.7，meme-generator 0.1.14 只能装在隔离的 3.12 venv 里，所以这里
用标准库 urllib 走 **127.0.0.1 的本地 HTTP 服务**，不在 Bot 进程里 import 任何
meme-generator 的二进制依赖。

职责（9 项，全部封装在本模块，主消息循环只留一个分发旁路）：
  1) 模板查询：/memes/keys + /memes/{key}/info，带缓存
  2) 中文关键词/别名匹配：keyword -> meme key（首期只开放 5 类模板）
  3) 图片收集：接收宿主已落盘的本地图片路径列表
  4) 图片数量校验：按模板 info 的 min/max_images
  5) 参数解析：文字模板从口令剩余文本提取 texts
  6) 生成调用：HTTP POST multipart/form-data 到 /memes/{key}/
  7) 结果处理：bytes + Pillow 校验格式/尺寸/帧数/大小
  8) 返回：写出临时文件，返回 (路径, meta)；调用方负责发送 + 清理
  9) 异常反馈：区分「用户输入错误」(541/542/552...) 与「服务故障」，分别给话术

安全：
  - 目标地址固定来自配置（默认 http://127.0.0.1:2233），用户文本永远不能改它；
  - 输入图片大小/数量、返回图片大小都有硬上限；
  - 连接超时 + 生成超时 + 并发信号量（信号量隔离不同会话，防串线只在 bot 侧收集阶段）。
"""
import io
import json
import logging
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

log = logging.getLogger("qqbot.meme")

CFG = {}
BASE = Path(__file__).resolve().parent
STATE = BASE / "state"

# 宿主注入的回调：fetch_image(seg)->本地路径 / send_image(cid, path, caption)->bool
# avatar_file(qq)->本地路径（QQ 头像落成临时 PNG，2026-11-05 头像模式用）
_FETCH_IMAGE = None
_SEND_IMAGE = None
_AVATAR_FILE = None

# ---- 默认值（写进 config.json 的 meme 段；改这里=改默认，线上可在配置里覆盖）----
_DEFAULTS = {
    "enabled": False,               # 🔴 总闸默认关：接好并验收前不改变任何现有行为
    "api_url": "http://127.0.0.1:2233",
    "health_path": "/health",
    "connect_timeout": 3,           # 健康检查/轻连接的超时（秒）
    "generate_timeout": 60,         # 生成请求超时（秒）
    "max_input_images": 2,          # 单模板最多吃几张图（首期 kiss=2）
    "max_input_image_bytes": 8 * 1024 * 1024,   # 单张输入图上限 8MB
    "max_result_bytes": 8 * 1024 * 1024,        # 返回图片上限 8MB（发送层 b64 兜底也是 8MB）
    "max_concurrency": 2,           # 并发生成上限
    "info_cache_ttl": 600,          # /info 缓存秒数
    # 首期开放的 5 类模板：关键词 -> meme key。别名多写在 value 的第二个字段。
    "templates": {
        "shoot":    {"keywords": ["射", "🐍"], "kind": "gif"},
        "crawl":    {"keywords": ["爬"],       "kind": "static"},
        "kiss":     {"keywords": ["亲", "亲亲"], "kind": "gif"},
        "bad_news": {"keywords": ["悲报"],     "kind": "text"},
        "petpet":   {"keywords": ["摸", "摸摸", "摸头"], "kind": "gif"},
    },
}

# ---- 用户输入错误码（meme-generator 0.1.14 用 5xx 段自定义码表达用户错误）----
# 这些不是「服务挂了」，detail 里是给用户看的中文原因，直接转述即可。
USER_ERR_CODES = {
    531: "没有这个表情模板",
    532: "文字太长了",
    533: "图片打不开（可能已损坏或格式不支持）",
    540: "参数不匹配",
    541: "图片数量不对",
    542: "文字数量不对",
    543: "文字或名字不够",
    550: "参数不对",
    551: "参数解析失败",
    552: "参数格式不对",
    560: "生成被拒绝",
}


class MemeError(Exception):
    """带「给用户的话」和「是否用户输入问题」的生成异常。"""

    def __init__(self, user_msg, detail="", user_fault=False):
        super().__init__(detail or user_msg)
        self.user_msg = user_msg
        self.detail = detail or user_msg
        self.user_fault = user_fault


# ----------------------------------------------------------------------------
# bind / 配置
# ----------------------------------------------------------------------------

def bind(cfg, base=None, logger=None, fetch_image=None, send_image=None, state_dir=None,
         avatar_file=None):
    global CFG, BASE, STATE, _FETCH_IMAGE, _SEND_IMAGE, _AVATAR_FILE
    CFG = cfg
    if base:
        BASE = Path(base)
    if state_dir:
        STATE = Path(state_dir)
    if logger:
        globals()["log"] = logger
    if fetch_image:
        _FETCH_IMAGE = fetch_image
    if send_image:
        _SEND_IMAGE = send_image
    if avatar_file:
        _AVATAR_FILE = avatar_file


def cfg():
    """把 meme 段默认值落进 CFG（跟 qbot_skills.cfg 一致的写法，免得 save_config 丢段）。"""
    d = CFG.setdefault("meme", {})
    for k, v in _DEFAULTS.items():
        if isinstance(v, dict):
            dd = d.setdefault(k, {})
            for kk, vv in v.items():
                dd.setdefault(kk, dict(vv) if isinstance(vv, dict) else vv)
        else:
            d.setdefault(k, v)
    return d


def _conf(key, default=None):
    c = CFG.get("meme") or {}
    return c.get(key, _DEFAULTS.get(key, default))


def _api_base():
    """目标地址固定取配置，且强制只认本机回环地址，杜绝被用户输入或误配置带偏到公网。"""
    url = str(_conf("api_url") or "").rstrip("/")
    # 硬约束：只允许 127.0.0.1 / localhost，且不带 userinfo / 路径跳转
    from urllib.parse import urlparse
    p = urlparse(url)
    if p.scheme not in ("http",) or p.hostname not in ("127.0.0.1", "localhost") \
            or p.username or p.password or p.path not in ("", "/"):
        raise MemeError("表情服务地址不合法（只允许本机 http://127.0.0.1:2233）",
                        "非法 api_url=%r" % url, user_fault=False)
    return url


# ----------------------------------------------------------------------------
# 1) 模板查询 + 2) 关键词匹配
# ----------------------------------------------------------------------------

_info_cache = {}          # key -> (ts, info)
_info_lock = threading.Lock()
_keys_cache = {"ts": 0.0, "keys": []}
_keys_lock = threading.Lock()


def _http_get(path, timeout):
    req = urllib.request.Request(_api_base() + path)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def available_keys(force=False):
    """GET /memes/keys（带短 TTL 缓存）。服务不可达抛 MemeError。"""
    now = time.time()
    ttl = float(_conf("info_cache_ttl") or 600)
    with _keys_lock:
        if not force and _keys_cache["keys"] and now - _keys_cache["ts"] < ttl:
            return list(_keys_cache["keys"])
    try:
        st, raw = _http_get("/memes/keys", float(_conf("connect_timeout") or 3))
        keys = json.loads(raw)
        if not isinstance(keys, list):
            raise ValueError("keys 不是数组")
    except MemeError:
        raise
    except Exception as e:
        raise MemeError("表情服务连不上，稍后再试～", "keys: %s: %s" % (type(e).__name__, e))
    with _keys_lock:
        _keys_cache["ts"] = now
        _keys_cache["keys"] = list(keys)
    return list(keys)


def meme_info(key, force=False):
    """GET /memes/{key}/info（带 TTL 缓存），返回 params_type。"""
    now = time.time()
    ttl = float(_conf("info_cache_ttl") or 600)
    with _info_lock:
        hit = _info_cache.get(key)
        if not force and hit and now - hit[0] < ttl:
            return hit[1]
    # user 输入不能拼进 URL 之外的地方：key 只允许本模块模板表里的白名单
    safe = re.sub(r"[^a-z0-9_]", "", str(key))
    try:
        st, raw = _http_get("/memes/%s/info" % safe, float(_conf("connect_timeout") or 3))
        info = json.loads(raw)
    except MemeError:
        raise
    except urllib.error.HTTPError as e:
        raise MemeError("这个表情查不到参数", "info %s HTTP %d" % (safe, e.code))
    except Exception as e:
        raise MemeError("表情服务连不上，稍后再试～", "info: %s: %s" % (type(e).__name__, e))
    with _info_lock:
        _info_cache[key] = (now, info)
    return info


def _template_table():
    """keyword -> (key, spec)。只认配置里首期开放的 5 类模板。"""
    out = {}
    tmpl = _conf("templates") or {}
    for key, spec in tmpl.items():
        for kw in spec.get("keywords", []):
            out[str(kw)] = (str(key), spec)
    return out


# parse_message 把非文字段转成占位文本（图片=[图片]、动画表情=[动画表情]…）。
# 真实「带图 + 口令」消息里，文字段与图片占位会拼在一起，例如「射[图片]」「[图片]摸头」
# 「悲报 今天摸鱼[图片]」。匹配前先剥掉这些占位，否则口令永远认不全（阶段 3-D 实测）。
_PLACEHOLDER_RE = re.compile(r"\[(?:图片|图|动画表情|表情|语音|视频|QQ表情|文件|卡片消息|"
                             r"富文本|礼物|骰子|戳一戳|群回答|音乐|小程序|转发消息)\]")


def _clean_text(text):
    """剥掉消息段占位符、@占位、压缩空白，得到可用于口令匹配的纯文字。

    @占位：parse_message 会把 at 段转成 `@昵称` 文本，「@某人 亲」若不剥掉开头
    的 @ 永远匹配不上口令（2026-11-05 头像模式上线时踩的）。只剥**开头**的 @ 串：
    「悲报 @张三 挂科了」这种文字模板的正文里 @ 该留着还留着。
    """
    t = _PLACEHOLDER_RE.sub(" ", str(text or ""))
    t = re.sub(r"^(?:@\S+\s*)+", "", t)
    return " ".join(t.split()).strip()


def match(text):
    """口令识别：返回 (key, spec, kw, rest) 或 None。

    - 先剥离 `[图片]` 等消息段占位符（parse_message 的产物），再匹配；
    - 精确取「消息 = 关键词」或「关键词 + 空白 + 文字」（给文字模板用）；
    - 关键词表里按长度倒序匹配，避免「摸」抢了「摸头」；
    - 只有命中且总闸 enabled 才返回；未命中/关闸一律 None（不改变现有行为）。
    """
    if not _conf("enabled"):
        return None
    t = _clean_text(text)
    if not t:
        return None
    table = _template_table()
    for kw in sorted(table, key=len, reverse=True):
        if t == kw:
            key, spec = table[kw]
            return key, spec, kw, ""
        if t.startswith(kw):
            tail = t[len(kw):]
            # 关键词后必须跟空白，避免「射雕英雄」被当成口令
            if tail and tail[0] in (" ", "\t", "\n", "　"):
                key, spec = table[kw]
                return key, spec, kw, tail.strip()
    return None


# ----------------------------------------------------------------------------
# 3) 图片收集（宿主落盘）+ 4) 数量校验 + 5) 参数解析
# ----------------------------------------------------------------------------

def _collect_images(seg_list, maxn=None):
    """图片段 data 列表 -> 本地路径列表。用宿主注入的 fetch_image（bot._fetch_raw_image）。
    超限/落盘失败的单张跳过并记日志；返回的路径由调用方在 finally 里统一清理。
    maxn 由模板的 max_images 决定，避免把回捞累积的多张都喂进去造成「图片过多」。"""
    if _FETCH_IMAGE is None:
        raise MemeError("表情模块没接好图片下载", "fetch_image 未 bind")
    hard_cap = int(_conf("max_input_images") or 2)
    cap = min(hard_cap, int(maxn)) if maxn is not None else hard_cap
    maxb = int(_conf("max_input_image_bytes") or (8 * 1024 * 1024))
    out = []
    for seg in (seg_list or [])[:cap]:
        try:
            p = _FETCH_IMAGE(seg)
        except Exception as e:
            log.warning("meme 取图失败: %s", e)
            continue
        if not p:
            continue
        try:
            if Path(p).stat().st_size > maxb:
                log.warning("meme 输入图超 %d 字节，跳过: %s", maxb, p)
                try:
                    Path(p).unlink()
                except OSError:
                    pass
                continue
        except OSError:
            continue
        out.append(str(p))
    return out


def _validate_inputs(info, image_paths, texts):
    """按 info.params_type 校验图片/文字数量，不足/过多给出可读话术。"""
    pt = (info or {}).get("params_type") or {}
    mi, ma = int(pt.get("min_images", 0)), int(pt.get("max_images", 0))
    mt, xt = int(pt.get("min_texts", 0)), int(pt.get("max_texts", 0))
    n_img, n_txt = len(image_paths), len([t for t in texts if str(t).strip()])
    if n_img < mi:
        raise MemeError("还差 %d 张图～（这个表情要 %s 张）" % (mi - n_img, mi),
                        "图片不足 %d<%d" % (n_img, mi), user_fault=True)
    if ma and n_img > ma:
        raise MemeError("图太多啦，这个表情最多 %d 张" % ma,
                        "图片过多 %d>%d" % (n_img, ma), user_fault=True)
    if n_txt < mt and not (pt.get("default_texts") or []):
        raise MemeError("还差文字内容～（这个表情要填字）",
                        "文字不足 %d<%d" % (n_txt, mt), user_fault=True)
    if xt and n_txt > xt:
        raise MemeError("文字太多啦，这个表情最多 %d 条" % xt,
                        "文字过多 %d>%d" % (n_txt, xt), user_fault=True)


def _parse_texts(spec, rest):
    """文字模板：口令后面的内容按空白切成多条（capoo_say 支持多句，首期 bad_news 1 条）。"""
    if spec.get("kind") != "text":
        return []
    if not rest:
        return []
    # 用空白分句，但保留中文短语整体；首期只 1 条时 info 校验会挡住多余
    return [rest]


# ----------------------------------------------------------------------------
# 6) 生成调用 + 7) 结果处理 + 8) 返回
# ----------------------------------------------------------------------------

def _multipart(image_paths, texts):
    """拼 multipart/form-data；返回 (body_bytes, content_type)。"""
    boundary = "----qbotmeme" + uuid.uuid4().hex
    buf = io.BytesIO()

    def field(name, value):
        buf.write(("--%s\r\n" % boundary).encode())
        buf.write(('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode("utf-8"))
        buf.write(str(value).encode("utf-8"))
        buf.write(b"\r\n")

    def filefield(name, filename, content, ctype):
        buf.write(("--%s\r\n" % boundary).encode())
        buf.write(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                   % (name, filename)).encode("utf-8"))
        buf.write(("Content-Type: %s\r\n\r\n" % ctype).encode())
        buf.write(content)
        buf.write(b"\r\n")

    for i, p in enumerate(image_paths):
        ctype = mimetypes.guess_type(p)[0] or "image/png"
        filefield("images", "image_%d%s" % (i, Path(p).suffix or ".png"),
                  Path(p).read_bytes(), ctype)
    for t in texts:
        field("texts", t)
    field("args", "{}")
    buf.write(("--%s--\r\n" % boundary).encode())
    return buf.getvalue(), "multipart/form-data; boundary=%s" % boundary


def _post_generate(key, image_paths, texts):
    body, ctype = _multipart(image_paths, texts)
    req = urllib.request.Request(
        _api_base() + "/memes/%s/" % re.sub(r"[^a-z0-9_]", "", key),
        data=body, headers={"Content-Type": ctype}, method="POST")
    timeout = float(_conf("generate_timeout") or 60)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        # HTTPError 也是类文件对象，能读 body
        try:
            raw = e.read()
        except Exception:
            raw = b""
        return e.code, raw, e.headers.get("Content-Type", "") if e.headers else ""


def _parse_error_detail(raw, code):
    try:
        d = json.loads(raw)
        return str(d.get("detail") or d.get("message") or "")
    except Exception:
        return (raw[:200].decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)[:200])


def generate(key, image_paths, texts):
    """核心生成：POST -> 错误码分流 -> Pillow 校验 -> 写临时文件。

    返回 (tmp_path, meta)。meta: {format, size, n_frames, animated, bytes}。
    任何失败抛 MemeError（带给用户的话 + 是否用户问题 + 详细日志）。
    """
    # 并发闸门：拿不到就快速拒绝，不让生成请求堆死主流程
    sem = _gen_sem_for()
    if not sem.acquire(blocking=False):
        raise MemeError("表情生成正忙，稍等一下再发～", "并发已满", user_fault=False)
    try:
        st, raw, ctype = _post_generate(key, image_paths, texts)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        raise MemeError("表情服务连不上，稍后再试～", "POST 连接失败 %s: %s" % (type(e).__name__, e))
    except Exception as e:
        raise MemeError("表情生成出了点问题", "POST 异常 %s: %s" % (type(e).__name__, e))
    finally:
        sem.release()

    if st != 200:
        detail = _parse_error_detail(raw, st)
        # 标准网关/服务器故障码：当服务故障，不当用户错误
        GATEWAY_FAULT = (500, 501, 502, 503, 504, 505)
        if st in GATEWAY_FAULT:
            raise MemeError("表情服务开小差了，稍后再试～", "HTTP %d body=%s" % (st, detail[:200]))
        # meme-generator 自定义码（520 基类 / 531-560）：用户输入或参数问题，转述中文 detail
        if st in USER_ERR_CODES or (520 <= st < 600 and detail):
            umsg = detail or USER_ERR_CODES.get(st, "这个表情参数不对")
            raise MemeError(umsg, "HTTP %d detail=%s" % (st, detail), user_fault=True)
        raise MemeError("这个表情生成失败（HTTP %d）" % st, "HTTP %d body=%s" % (st, detail[:200]))

    # 结果校验
    maxb = int(_conf("max_result_bytes") or (8 * 1024 * 1024))
    if not raw or len(raw) < 64:
        raise MemeError("表情服务返回了空图", "结果为空 len=%d" % len(raw))
    if len(raw) > maxb:
        raise MemeError("表情图太大了发不出去", "结果 %d > %d" % (len(raw), maxb))
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        fmt, size = im.format, im.size
        n_frames = getattr(im, "n_frames", 1)
        animated = bool(getattr(im, "is_animated", False))
        # 逐帧 seek/load，确认不是半截损坏的 GIF
        for i in range(n_frames):
            im.seek(i)
            im.load()
        im.seek(0)
        im.close()   # Windows 上不关会占住 BytesIO / 文件句柄，影响后续清理
    except MemeError:
        raise
    except Exception as e:
        raise MemeError("表情图损坏了，稍后再试～", "Pillow 校验失败 %s: %s" % (type(e).__name__, e))

    ext = ".gif" if fmt == "GIF" else (".png" if fmt == "PNG" else ".jpg")
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / ("meme_%s%s" % (uuid.uuid4().hex, ext))
    tmp.write_bytes(raw)     # 用 bytes 直接写，避开 BytesIO 指针位置问题
    meta = {"format": fmt, "size": size, "n_frames": n_frames,
            "animated": animated, "bytes": len(raw), "ext": ext}
    return str(tmp), meta


_sem_cache = {"n": 0, "obj": None}
_sem_lock = threading.Lock()


def _gen_sem_for():
    """按配置生成并发信号量（配置热改也可生效）。"""
    n = int(_conf("max_concurrency") or 2)
    with _sem_lock:
        if _sem_cache["n"] != n or _sem_cache["obj"] is None:
            _sem_cache["n"] = n
            _sem_cache["obj"] = threading.BoundedSemaphore(n)
        return _sem_cache["obj"]


# ----------------------------------------------------------------------------
# 9) 高层入口：供 bot.py 一行调用（图片收集/回捞/校验/生成/发送/清理/反馈）
# ----------------------------------------------------------------------------

def _avatar_tmp(qq):
    """要一张 QQ 头像的本地临时文件（宿主注入的 avatar_file 回调）。失败返回 None。"""
    if _AVATAR_FILE is None:
        return None
    try:
        p = _AVATAR_FILE(str(qq or "").strip())
        return str(p) if p else None
    except Exception as e:
        log.warning("meme 头像获取失败 qq=%r: %s", qq, e)
        return None


def handle(gid, uid, text, imgs, qmid=0, recall_assets=None, send=None,
           quoted_images=None, ats=None, quoted_sender=None):
    """尝试处理一条 meme 口令。返回 True=已处理（无论成败，主循环应 return），False=不是口令。

    图片来源按优先级（2026-11-05 加头像模式，2026-11-06 主人定稿三条语义）：
      ① 当前消息 imgs 非空 -> 只用当前图（按模板 max_images 截断）；
      ② @了人（头像模式）-> 双人模板（min_images>=2）= [发话人头像, 被@的人头像]
         （发话人是主动方排第 1，「亲 @某人」= 我亲 TA）；单人模板 = 被@的人头像；
      ③ 当前无图且模板 min_images>0 -> 回捞 recall_assets(gid, uid)（会话隔离由宿主保证）；
      ④ 仍无图且 qmid 有效 -> 取引用里的图/表情；
      ⑤ 引用没图 -> 「被引用的人」的头像当主角（quoted_sender，主人定：引用谁就是谁）；
      ⑥ 全都没有且模板只要 1 张图 -> 发话人自己的头像（跟 petpet 上游行为一致）；
         要 2 张及以上则报错提示「@一个人」。
      ⑦ min_images=0 的纯文字模板（悲报等）-> 完全不取图。

    所有落盘临时图（输入图 + 头像 + 结果图）在 finally 里可靠清理。
    用户错误给简短可读话术；服务故障给简短话术、细节只进 log。
    """
    hit = match(text)
    if not hit:
        return False
    key, spec, kw, rest = hit
    paths = []
    result_path = None
    avatar_paths = []          # 头像临时文件单独记账（可能比 cap 多 fetch 了一张，全要清）
    try:
        info = meme_info(key)
        pt = (info or {}).get("params_type") or {}
        min_images = int(pt.get("min_images", 0))
        max_images = int(pt.get("max_images", 0))

        # 会话归属：图片只来自「当前消息」/「@的人的头像」/「本 (gid,uid) 回捞」/「本条引用」。
        sender = str(uid or "")
        targets = []
        for a in (ats or []):
            a = str(a)
            if a and a != sender and a not in targets:
                targets.append(a)
        cur = [s for s in (imgs or []) if isinstance(s, dict)]
        cap = max_images if max_images > 0 else None
        if cur:
            paths = _collect_images(cur, maxn=cap) if min_images > 0 else []
            if min_images > 0 and len(paths) < min_images:
                # 本条图不够数：用头像补齐（先被@的人，再发话人）——
                # 「发自己的照片 + 亲 @某人」= 这张图亲 TA 的头像（2026-11-05）
                for qq in (targets + ([sender] if sender and sender not in targets else [])):
                    if len(paths) >= min_images:
                        break
                    p = _avatar_tmp(qq)
                    if p:
                        avatar_paths.append(p)
                        paths.append(p)
        elif min_images > 0 and targets:
            # 头像模式：明确 @ 了人就不再回捞旧图（点谁就是谁，别拿历史图凑）
            if min_images >= 2:
                order = [sender] + [t for t in targets if t != sender]
            else:
                order = targets[:1]
            for qq in order[:max(1, min_images)]:
                p = _avatar_tmp(qq)
                if p:
                    avatar_paths.append(p)
            paths = avatar_paths[:cap] if cap else list(avatar_paths)
        elif min_images > 0 and recall_assets:
            try:
                recalled, _ = recall_assets(gid, uid)
                segs = [s for s in (recalled or []) if isinstance(s, dict)]
            except Exception as e:
                log.warning("meme 回捞失败: %s", e)
                segs = []
            paths = _collect_images(segs, maxn=cap)
            # 引用图：仅当回捞也没图、且宿主提供了 quoted_images 回调时才取
            if not paths and qmid and quoted_images:
                try:
                    segs = [s for s in (quoted_images(qmid) or []) if isinstance(s, dict)]
                except Exception as e:
                    log.warning("meme 引用图失败: %s", e)
                paths = _collect_images(segs, maxn=cap)
            # 引用没图（纯文字引用）：用「被引用的人」的头像（2026-11-06 主人定）
            if not paths and qmid and callable(quoted_sender):
                qsender = str(quoted_sender(qmid) or "")
                if qsender and qsender != sender and re.fullmatch(r"\d{4,12}", qsender):
                    p = _avatar_tmp(qsender)
                    if p:
                        avatar_paths.append(p)
                        paths = list(avatar_paths)
            if not paths and min_images == 1 and sender:
                p = _avatar_tmp(sender)      # 最后兜底：拿发话人自己的头像
                if p:
                    avatar_paths.append(p)
                    paths = list(avatar_paths)
        elif min_images == 1 and sender:
            p = _avatar_tmp(sender)          # 没接回捞/引用回调时的兜底
            if p:
                avatar_paths.append(p)
                paths = list(avatar_paths)
        if min_images >= 2 and len(paths) < min_images and not cur:
            raise MemeError("这个表情要 @ 一个人才能生成～（「%s @某人」）" % kw,
                            "双人模板图不足 %d<%d（无 @）" % (len(paths), min_images),
                            user_fault=True)

        texts = _parse_texts(spec, rest)
        _validate_inputs(info, paths, texts)

        result_path, meta = generate(key, paths, texts)
        send_fn = send or _SEND_IMAGE
        if send_fn is None:
            raise MemeError("表情模块没接好发送", "send_image 未 bind")
        ok = send_fn(gid, result_path)
        if ok is False:
            raise MemeError("表情图没发出去", "send_image 返回 False")
        log.info("meme 生成成功 key=%s kw=%s fmt=%s size=%s frames=%d bytes=%d src=%s",
                 key, kw, meta["format"], meta["size"], meta["n_frames"], meta["bytes"],
                 "本条图" if cur else ("头像" if avatar_paths else "回捞/引用"))
        return True
    except MemeError as e:
        if e.user_fault:
            log.info("meme 用户输入问题: %s", e.detail)
        else:
            log.warning("meme 服务问题: %s", e.detail)
        # 文字反馈由宿主通过 feedback 回调发出（不 import bot，避免误用发图通道）
        _feedback(gid, e.user_msg)
        return True
    except Exception as e:
        log.exception("meme 未知异常")
        _feedback(gid, "表情生成出了点问题，稍后再试～")
        return True
    finally:
        for p in paths:
            _unlink(p)
        for p in avatar_paths:
            _unlink(p)
        if result_path:
            _unlink(result_path)


_FEEDBACK = None


def bind_feedback(fn):
    """单独注入「文字反馈」回调（send_group(gid, text)），与发图解耦。"""
    global _FEEDBACK
    _FEEDBACK = fn


def _feedback(gid, text):
    if _FEEDBACK:
        try:
            _FEEDBACK(gid, text)
        except Exception as e:
            log.warning("meme 反馈发送失败: %s", e)
    else:
        log.info("meme 反馈(无回调): %s", text)


def _unlink(p):
    try:
        Path(p).unlink()
    except OSError:
        pass


def health(timeout=None):
    """供 bot 侧/运维探活：返回 (ok, info dict)。确认服务可访问 + 路由已注册。"""
    try:
        st, raw = _http_get(str(_conf("health_path") or "/health"),
                            float(timeout or _conf("connect_timeout") or 3))
        d = json.loads(raw)
        return bool(d.get("ok")) and int(d.get("routes", 0)) > 4, d
    except Exception as e:
        return False, {"err": "%s: %s" % (type(e).__name__, e)}


def preview(key, timeout=25):
    """GET /memes/{key}/preview 的字节（服务端用示例素材渲好的预览图）。

    给 bot 的 /api/meme_preview 代理用：<img> 拿它直接当 src。
    key 已在本函数再做一次白名单净化，宿主路由不用重复防注入。
    """
    safe = re.sub(r"[^a-z0-9_-]", "", str(key or ""))
    if not safe:
        raise MemeError("没这个模板", "preview: 空 key")
    st, raw = _http_get("/memes/%s/preview" % safe, float(timeout))
    if st != 200 or not raw:
        raise MemeError("这个模板预览取不到", "preview %s HTTP %s" % (safe, st))
    return raw
