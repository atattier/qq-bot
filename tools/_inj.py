"""注入一条 NapCat 事件，用来验证「不接话/表情包/占位符」这些行为。

🔴 2026-10-30 起**必须走演练态**（主人原话：「测试的发言不要发群里」）。
   以前这个脚本直接 `POST /onebot`，而那个端点不校验来源 ⇒ bot 把假消息当真群消息处理，
   还会**真把回复发进群**。21:32 我就这么把「4K还得看面板色域…」发进了 123450001。

现在的规矩（脚本已经照做，别改回去）：
  · 请求带 `?drill=1` + `X-Bridge-Token` 头；
  · 服务端收下后把该会话标成「演练态」（默认 180 秒）⇒ **这期间该会话出站全静音**，
    假消息只走大脑、绝不进群；
  · 真群消息不受影响（演练态只挡注入这一段）。

用法：
    python tools/_inj.py image            # 纯图片
    python tools/_inj.py face             # QQ 表情段
    python tools/_inj.py text             # 一句陈述（测「激烈讨论不插话」）
    python tools/_inj.py say "介绍这家公司"  # 指定文字
    python tools/_inj.py sticker          # 真·表情包（sub_type=1，测「读情绪」）

可选环境变量：
    INJ_SEC=180    演练态时长（秒）
    INJ_GID=...    目标群
"""
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)  # 这些脚本原来都假定 cwd=项目根（import bot、state/ 相对路径）
import json  # noqa: E402
import time  # noqa: E402
import urllib.request  # noqa: E402
import urllib.error  # noqa: E402
from pathlib import Path  # noqa: E402

_sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(_ROOT)
kind = _sys.argv[1] if len(_sys.argv) > 1 else "image"
uid = int(_sys.argv[2]) if len(_sys.argv) > 2 else 123450003
say = _sys.argv[3] if len(_sys.argv) > 3 else "画：一只戴墨镜的柴犬，赛博朋克"
gid = int(_os.environ.get("INJ_GID") or 123450001)
sec = _os.environ.get("INJ_SEC") or "180"

segs = {
    "image": [{"type": "image", "data": {"url": "http://x/y.jpg", "file": "y.jpg"}}],
    # sub_type=1 才是真·表情包（NapCat 元数据；见 bot.img_is_sticker）
    "sticker": [{"type": "image", "data": {"sub_type": 1, "summary": "[动画表情]",
                                           "url": "http://x/s.gif", "file": "s.gif"}}],
    "face": [{"type": "face", "data": {"face_id": "1"}}],
    "text": [{"type": "text", "data": {"text": "刚跑完图，累死了"}}],
    "say": [{"type": "text", "data": {"text": say}}],
}[kind]

ev = {"time": int(time.time()), "self_id": 123450002,
      "post_type": "message", "message_type": "group", "sub_type": "normal",
      "message_id": int(time.time() * 1000) % 10 ** 9,
      "group_id": gid, "user_id": uid,
      "sender": {"user_id": uid, "nickname": "测试群友", "card": "", "role": "member"},
      "font": 0, "message": segs}


def _token():
    """bridge token（**不是** NapCat 的 token，两者别混）。

    · `state/bridge-token.json` 的 `token` 是 bridge token（首选）；
    · `config.json` 里的 `token` 是 **NapCat** 的，不能拿来当 bridge token 用。
    """
    f = BASE / "state" / "bridge-token.json"
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            t = str((d or {}).get("token") or "").strip()
            if t:
                return t
        except Exception:
            pass
    try:
        d = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
        return str(d.get("bridge_token") or "").strip()
    except Exception:
        return ""


_tk = _token()
if not _tk:
    print("找不到 bridge token（state/bridge-token.json 或 config.json），演练注入会被拒。")
    _sys.exit(2)

url = "http://127.0.0.1:8088/onebot?drill=1&drill_sec=%s" % sec
req = urllib.request.Request(
    url, data=json.dumps(ev, ensure_ascii=False).encode(),
    headers={"Content-Type": "application/json", "X-Bridge-Token": _tk})
try:
    r = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
    print("注入 %s -> %s" % (kind, r))
    print("（该会话已进入演练态 %ss：期间它的出站全静音，不会发进群）" % sec)
except urllib.error.HTTPError as e:
    print("注入被拒 HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:200]))
    print("（403 = token 不对或没带 drill；这是有意的：来路不明的注入一律不收）")
    _sys.exit(1)
