# -*- coding: utf-8 -*-
"""机器状态卡 —— 离线自测（2026-10-23）。

覆盖：绘制（有显卡/没显卡两种数据）/ 口令识别（正例 + 反例 + 必须点名）/
同群冷却 / 开关关掉 / 旋钮注册 / 采集不崩。
**不联网、不发群**：`_skill_send_image` 与 `send_group` 全打桩。

用法：python tests/_tstatuscard.py
"""
import os as _os
import sys as _sys
import tempfile
from pathlib import Path

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
_sys.stdout.reconfigure(encoding="utf-8")

import qbot_statuscard as S    # noqa: E402
import bot                     # noqa: E402
from PIL import Image          # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [!!] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


_tmp = Path(tempfile.mkdtemp(prefix="statuscard_test_"))

GB = 1024 ** 3
FAKE = {
    "cpu": {"pct": 37.0, "name": "Intel Core i5-13500HX", "cores": 20, "phys": 14},
    "mem": {"pct": 62.5, "used": 10 * GB, "total": 16 * GB},
    "disk": {"pct": 81.0, "used": 800 * GB, "total": 954 * GB, "drive": "E:"},
    "gpu": {"name": "NVIDIA GeForce RTX 4060 Laptop GPU", "pct": 13.0,
            "used": 0.9 * GB, "total": 8 * GB, "temp": 41},
    "proc": {"rss": 340 * 1024 ** 2, "uptime": 3 * 3600 + 12 * 60},
    "host": "ATATIER", "py": "3.14.7",
}
INFO = {"name": "小鲸", "napcat": True, "comfy": False, "groups": 1, "skills": 3,
        "role": "小鲸", "brain": "api",
        "sent": {"per_hour": 12, "per_minute": 2, "circuit_left_sec": 0},
        "extra": "记忆：16 个人 / 0 条事实 / 327 轮对话"}

print("== 绘制 ==")
p1 = S.render(FAKE, INFO, out_dir=_tmp)
im = Image.open(p1)
chk("出图成功且是 PNG", p1.exists() and im.format == "PNG", im.format)
chk("尺寸 880x560", im.size == (880, 560), im.size)
chk("文件不是空白图（>15KB）", p1.stat().st_size > 15 * 1024, p1.stat().st_size)

_no_gpu = dict(FAKE)
_no_gpu["gpu"] = None
p2 = S.render(_no_gpu, INFO, out_dir=_tmp)
chk("没有显卡也能出图", p2.exists() and p2.stat().st_size > 15 * 1024)

_bare = {"cpu": {}, "mem": {}, "disk": {}, "gpu": None, "proc": {}, "host": "?", "py": "?"}
p3 = S.render(_bare, {}, out_dir=_tmp)
chk("psutil 全空也不崩（全是 —）", p3.exists() and p3.stat().st_size > 5 * 1024)

print("== 采集 ==")
d = S.collect()
chk("collect 返回必需字段", all(k in d for k in ("cpu", "mem", "disk", "gpu", "proc")))
chk("CPU 使用率是数字", isinstance((d.get("cpu") or {}).get("pct"), (int, float)),
    (d.get("cpu") or {}).get("pct"))
chk("内存总量 > 0", (d.get("mem") or {}).get("total", 0) > 0)

print("== 口令 ==")
_out, _sent = [], []
_real_send, _real_img, _real_build = bot.send_group, bot._skill_send_image, S.build
bot.send_group = lambda gid, text, *a, **k: (_out.append(str(text)), True)[1]
bot._skill_send_image = lambda cid, path, cap="": (_sent.append((cid, str(path))), True)[1]
S.build = lambda info=None, out_dir=None: p1          # 别每条用例都真去跑 nvidia-smi
try:
    for text, want in [("状态", True), ("状态卡", True), ("机器状态", True), ("负载", True),
                       ("看下机器", True), ("看看服务器", True)]:
        _sent.clear()
        bot._STATUS_CARD["last"].clear()
        chk("「%s」出图" % text, bot.handle_status_card(1, text, True) is True and len(_sent) == 1)

    for text in ("这状态不对啊", "我今天状态不错", "看看这个", "设置 状态"):
        _sent.clear()
        bot._STATUS_CARD["last"].clear()
        chk("「%s」不抢话" % text, bot.handle_status_card(1, text, True) is False and not _sent)

    _sent.clear()
    bot._STATUS_CARD["last"].clear()
    chk("没点名不响应", bot.handle_status_card(1, "状态", False) is False and not _sent)

    print("== 冷却与开关 ==")
    _sent.clear()
    bot._STATUS_CARD["last"].clear()
    bot.handle_status_card(2, "状态", True)
    _out.clear()
    h = bot.handle_status_card(2, "状态", True)
    chk("同群 15 秒冷却", h is True and len(_sent) == 1 and not _out, _out[-1:])
    _sent.clear()
    bot.handle_status_card(3, "状态", True)
    chk("换个群照常出图", len(_sent) == 1)

    bot.CFG.setdefault("status_card", {})["enabled"] = False
    _sent.clear()
    chk("开关关掉就不认", bot.handle_status_card(4, "状态", True) is False and not _sent)
    bot.CFG["status_card"]["enabled"] = True

    chk("旋钮「状态卡」在后台（群务组）",
        any(i["label"] == "状态卡" and i["group"] == "群务" for i in bot.knob_items()))
finally:
    bot.send_group, bot._skill_send_image, S.build = _real_send, _real_img, _real_build
    bot._STATUS_CARD["last"].clear()

print()
print("==== ALL PASS ====" if ok else "==== 有失败 ====")
_sys.exit(0 if ok else 1)
