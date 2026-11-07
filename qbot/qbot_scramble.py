# -*- coding: utf-8 -*-
"""图片混淆 / 解混淆业务模块（2026-10-10 从 bot.py 抽出，原 L1024-1273）。

算法核心在 scramble.py（Gilbert 2D 空间填充曲线 + 黄金比例偏移，与网页版逐像素对齐）。
本模块负责：
  1. 混淆/解混淆口令匹配与防误触正则
  2. 消息段/引用图/合并转发图解析与素材回捞
  3. 执行混淆/解混淆与结果私发路由

抽出规约：
  本模块不 import bot。宿主能力由 bot.py 在模块级调用 bind() 注入，
  支持单测隔离与独立导入。
"""
import logging
import os
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / "state"

log = logging.getLogger("qqbot")

try:
    import scramble
except Exception as _e:
    scramble = None
    log.warning("图片混淆模块加载失败（该功能禁用）：%s", _e)

SCR_DIR = STATE / "scramble"

# ---------------- 正则词库与防误触匹配 ----------------
_SCR_WORDS = r"(?:图片混淆|混淆图|混淆|打码图|打码|马赛克掉|打乱)"
_DESCR_WORDS = r"(?:图片解混淆|解混淆|反混淆|还原图|复原图|还原|复原|解密图|解密|解马赛克)"
_SCR_POLITE = (r"(?:帮我|给我|请|麻烦|来(?:一)?张|一?张|一下|下|这?张|那?个|图|吧|哦|呗|它|谢谢"
               r"|私发|私我|发我|偷偷发|悄悄发|私聊发|别发群|不要发群|群里别发)")
_SEP = r"[\s,，、;；:：]*"
_SCR_TAIL2 = (r"(?:%s%s)*(?:%s\d{2,5}\s*[xX×*]\s*\d{2,5})?"
              r"(?:%s%s)*%s[。.!！?？~～]*%s$") % (_SEP, _SCR_POLITE, _SEP,
                                                  _SEP, _SCR_POLITE, _SEP, _SEP)
_SCR_HEAD = r"^%s(?:%s%s)?%s"
_SCR_RE = re.compile(_SCR_HEAD % (_SEP, _SCR_POLITE, _SEP, _SCR_WORDS) + _SCR_TAIL2, re.I)
_DESCR_RE = re.compile(_SCR_HEAD % (_SEP, _SCR_POLITE, _SEP, _DESCR_WORDS) + _SCR_TAIL2, re.I)

# ---------------- 宿主钩子注入 ----------------
_hooks = {
    "cfg": {},
    "scan_media": None,
    "api": None,
    "vg_localize_images": None,
    "quoted_images": None,
    "vg_recall_assets": None,
    "send_group": None,
    "send_segments": None,
    "is_priv": None,
    "priv_key": None,
    "pick_image": None,
}


def bind(cfg=None, scan_media=None, api=None, vg_localize_images=None,
         quoted_images=None, vg_recall_assets=None, send_group=None,
         send_segments=None, is_priv=None, priv_key=None, state_dir=None,
         pick_image=None):
    """由 bot.py 启动时调用注入宿主依赖。"""
    global SCR_DIR
    if cfg is not None:
        _hooks["cfg"] = cfg
    if scan_media is not None:
        _hooks["scan_media"] = scan_media
    if api is not None:
        _hooks["api"] = api
    if vg_localize_images is not None:
        _hooks["vg_localize_images"] = vg_localize_images
    if quoted_images is not None:
        _hooks["quoted_images"] = quoted_images
    if vg_recall_assets is not None:
        _hooks["vg_recall_assets"] = vg_recall_assets
    if send_group is not None:
        _hooks["send_group"] = send_group
    if send_segments is not None:
        _hooks["send_segments"] = send_segments
    if is_priv is not None:
        _hooks["is_priv"] = is_priv
    if priv_key is not None:
        _hooks["priv_key"] = priv_key
    if pick_image is not None:
        _hooks["pick_image"] = pick_image
    if state_dir:
        SCR_DIR = Path(state_dir) / "scramble"


def _scr_cfg():
    return dict((_hooks["cfg"] or {}).get("scramble") or {})


def wants_scramble(text):
    return bool(text) and bool(_SCR_RE.match(text.strip()))


def wants_descamble(text):
    return bool(text) and bool(_DESCR_RE.match(text.strip()))


def _scr_size_text(size):
    return "%d×%d" % (int(size[0]), int(size[1]))


def forward_images(msg, limit=4):
    """从转发消息里抠图片段（合并转发 = forward 段）。"""
    fid, inline = None, []
    scan_fn = _hooks["scan_media"]
    if scan_fn is None:
        # 降级默认实现
        def scan_fn(m):
            imgs, qmid = [], 0
            if not isinstance(m, list):
                return imgs, qmid
            for s in m:
                if isinstance(s, dict) and s.get("type") == "image":
                    d = s.get("data", {}) or {}
                    if d.get("url") or d.get("file"):
                        imgs.append(d)
            return imgs, qmid

    for seg in msg or []:
        if isinstance(seg, dict) and seg.get("type") == "forward":
            d = seg.get("data") or {}
            fid = d.get("id")
            c = d.get("content")
            if isinstance(c, list):
                inline = c
            break

    def _dig(entries, depth=0):
        out = []
        for m in entries or []:
            if not isinstance(m, dict):
                continue
            body = m.get("message") or m.get("content") or []
            if not isinstance(body, list):
                continue
            imgs, _ = scan_fn(body)
            out += imgs
            if len(out) >= limit:
                return out
            if depth < 3:
                sub = []
                for s in body:
                    if isinstance(s, dict) and s.get("type") == "forward":
                        c = (s.get("data") or {}).get("content")
                        if isinstance(c, list):
                            sub += c
                if sub:
                    out += _dig(sub, depth + 1)
                    if len(out) >= limit:
                        return out
        return out

    out = _dig(inline)[:limit]
    if out:
        log.info("从转发记录里取到 %d 张图（内联）", len(out))
        return out
    if not fid:
        return []
    api_fn = _hooks["api"]
    if not api_fn:
        return []
    try:
        arg = int(fid) if str(fid).isdigit() else fid
        d = (api_fn("get_forward_msg", message_id=arg).get("data") or {})
    except Exception as e:
        log.info("混淆取转发记录失败: %s", e)
        return []
    out = _dig(d.get("messages") or d.get("node") or [])[:limit]
    if out:
        log.info("从转发记录里取到 %d 张图（get_forward_msg）", len(out))
    return out


def _scr_pick_image(imgs, qmid, uid=None, gid=None, raw_msg=None):
    """找要处理的图。顺序：本条消息的图 → 引用的图 → 转发记录里的图 → 回捞这人最近发的图。"""
    loc_fn = _hooks["vg_localize_images"] or (lambda x: [d.get("file") or d.get("url") for d in x if d])
    pool = loc_fn(imgs or [])
    if not pool and qmid:
        quote_fn = _hooks["quoted_images"]
        if quote_fn:
            pool = loc_fn(quote_fn(qmid)[:1])
    if not pool and raw_msg:
        pool = loc_fn(forward_images(raw_msg)[:1])
    if not pool and uid is not None:
        try:
            recall_fn = _hooks["vg_recall_assets"]
            if recall_fn:
                m_imgs, _ = recall_fn(gid, uid)
                pool = loc_fn((m_imgs or [])[:1])
        except Exception as e:
            log.debug("混淆素材回捞失败: %s", e)
    return pool[0] if pool else None


def handle_scramble(gid, raw, privileged, imgs, qmid, uid=None, decrypt=False, raw_msg=None):
    """混淆 / 解混淆主流程。decrypt=True 走解混淆（群里私发）。"""
    send_group_fn = _hooks["send_group"] or (lambda *a, **kw: None)
    send_seg_fn = _hooks["send_segments"] or (lambda *a, **kw: False)
    is_priv_fn = _hooks["is_priv"] or (lambda g: False)
    priv_key_fn = _hooks["priv_key"] or (lambda u: -int(u))

    cfg = _scr_cfg()
    if scramble is None:
        send_group_fn(gid, "图片混淆功能这会儿用不了（本机缺 Pillow）。")
        return True
    if not cfg.get("enabled", True):
        send_group_fn(gid, "图片混淆现在是关的（管理员发「设置 图片混淆 开」能开）。")
        return True
    if not is_priv_fn(gid) and not privileged and not cfg.get("members_allowed", True):
        send_group_fn(gid, "混淆功能先只对管理员开放。")
        return True

    tail = (raw or "").strip()
    verb = "解混淆" if decrypt else "混淆"
    pick_fn = _hooks.get("pick_image") or _scr_pick_image
    src = pick_fn(imgs, qmid, uid, gid, raw_msg)
    if not src:
        send_group_fn(gid, "先把要%s的图片发出来（发张图、或者回复/转发那张图，再发「%s」都行）。"
                           % (verb, verb))
        return True

    size = scramble.parse_size(tail) if decrypt else None

    out_dir = (cfg.get("out_dir") or "").strip() or str(SCR_DIR)
    q = int(cfg.get("quality") or 95)
    keep = int(cfg.get("keep", 20))
    try:
        if decrypt:
            path, size, dt = scramble.descamble(
                src, out_dir, size=size, max_pixels=int(cfg.get("max_pixels") or 4000000),
                quality=q, keep=keep)
        else:
            path, size, dt = scramble.scramble(
                src, out_dir, max_side=int(cfg.get("max_side", 1536)),
                max_pixels=int(cfg.get("max_pixels") or 4000000),
                quality=q, keep=keep)
    except scramble.ScrambleError as e:
        log.info("图片%s失败：%s", verb, e)
        send_group_fn(gid, "%s失败：%s" % (verb, e))
        return True
    except Exception as e:
        log.error("图片%s炸了: %s", verb, e, exc_info=True)
        send_group_fn(gid, "%s的时候内部报错（已记日志），换张图试试。" % verb)
        return True

    log.info("图片%s %s -> %s %s (%.2fs, by uid=%s)",
             verb, os.path.basename(src), os.path.basename(path), _scr_size_text(size), dt, uid)
    cap = "%s好了（%s，%.1f 秒）" % (verb, _scr_size_text(size), dt)
    if decrypt:
        if is_priv_fn(gid):
            target, priv, hint = gid, True, None
        elif uid:
            target, priv, hint = priv_key_fn(int(uid)), False, gid
        else:
            send_group_fn(gid, "我不知道该私发给谁（这条消息里没拿到你的 QQ 号），"
                               "所以直接把解混淆结果发群里了。")
            target, priv, hint = gid, True, None

        ok = send_seg_fn(target, [
            {"type": "text", "data": {"text": "解混淆后的图（%s）：" % _scr_size_text(size)}},
            {"type": "image", "data": {"file": path}},
        ], group_hint=hint)
        if priv:
            return True
        if ok:
            send_group_fn(gid, "解混淆好了，私发给你了（走的群临时会话，不用加好友）。"
                               "⚠️ 这算法没有密钥，谁拿到原图都能还原，别指望它能挡住谁。")
        else:
            send_group_fn(gid, "解混淆算好了，但私发没成功（对方可能关了「接收临时会话」，"
                               "或者 QQ 这边卡了）。你在群里回我一声我再试，加我好友就一定能发。")
        return True
    ok = send_seg_fn(gid, [
        {"type": "text", "data": {"text": cap + "，原尺寸 " + _scr_size_text(size) + "。"
                                        "要还原就发「解混淆」（我私发给你）。"}},
        {"type": "image", "data": {"file": path}},
    ])
    if not ok:
        send_group_fn(gid, "图算好了但发不出去，文件在 %s" % Path(path).name)
    return True


def handle_scramble_cmd(gid, text, privileged, imgs, qmid, uid=None, raw_msg=None):
    """口令分派：先试解混淆再试混淆（长词优先）。返回 True = 已处理。"""
    t = (text or "").strip()
    if wants_descamble(t):
        return handle_scramble(gid, t, privileged, imgs, qmid, uid=uid, decrypt=True,
                               raw_msg=raw_msg)
    if wants_scramble(t):
        return handle_scramble(gid, t, privileged, imgs, qmid, uid=uid, decrypt=False,
                               raw_msg=raw_msg)
    return False
