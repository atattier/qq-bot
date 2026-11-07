# -*- coding: utf-8 -*-
"""qbot_statuscard —— 把机器状态画成一张图（2026-10-23）。

**为什么自己画、不装 `nonebot-plugin-picstatus`**：那个插件走 HTML 渲染 + playwright
（要装 Chromium、常驻一个无头浏览器进程），为了一张状态图不值。本机已有 psutil，
用 Pillow 直接画一张深色卡片，零新增依赖、50ms 出图。

数据分两半：
  · 主机侧（CPU/内存/磁盘/显卡/进程）—— 本模块自己采，不需要宿主告诉；
  · 机器人侧（名字/群数/发送量/ComfyUI/技能数）—— 宿主（bot.py）通过 `bot_info` 传进来，
    因为这些东西只有它知道。**本模块不 import bot**（和其余 qbot_* 一致）。

配色跟 `/admin` 的 Dark Console 一脉相承：深底 + 蓝主色，三档告警色。
"""
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("qqbot.statuscard")

BASE = Path(__file__).resolve().parent
OUTDIR = BASE / "cache" / "statuscard"

# ── 配色 ────────────────────────────────────────────────────────────────
BG = (15, 17, 21)
CARD = (23, 26, 33)
LINE = (38, 43, 54)
TEXT = (230, 232, 238)
MUTED = (139, 147, 167)
ACCENT = (79, 140, 255)
OK = (61, 220, 132)
WARN = (255, 176, 32)
BAD = (255, 92, 92)

W, H = 880, 560
PAD = 28

FONTS = [
    ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc"),   # 微软雅黑
    ("C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simhei.ttf"),  # 黑体
    ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf"),
]


def _font(size, bold=False):
    for b, r in FONTS:
        p = b if bold else r
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)      # Pillow ≥10 支持带字号
    except TypeError:
        return ImageFont.load_default()


def _gb(n, nd=1):
    try:
        return ("%." + str(nd) + "f GB") % (float(n) / 1024 ** 3)
    except (TypeError, ValueError):
        return "—"


def _mem(n):
    """进程内存用 MB 更好读（0 GB 那种显示没有信息量）。"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n >= 1024 ** 3:
        return "%.1f GB" % (n / 1024 ** 3)
    return "%.0f MB" % (n / 1024 ** 2)


def _pct_color(p):
    if p is None:
        return MUTED
    if p >= 88:
        return BAD
    if p >= 70:
        return WARN
    return ACCENT


def _dur(sec):
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return "—"
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m = sec // 60
    if d:
        return "%d 天 %d 小时" % (d, h)
    if h:
        return "%d 小时 %d 分" % (h, m)
    if m:
        return "%d 分" % m
    return "不到 1 分"


# ── 采集：主机侧 ────────────────────────────────────────────────────────

def _cpu_name():
    """Windows 上 platform.processor() 只给 "Intel64 Family 6…"，没用；读注册表拿商品名。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        v, _ = winreg.QueryValueEx(k, "ProcessorNameString")
        return re.sub(r"\s+", " ", str(v)).strip()
    except Exception:  # noqa: BLE001
        return platform.processor() or platform.machine() or "CPU"


def _gpu():
    """nvidia-smi 拿显卡（3 秒超时）。没有 N 卡/没装驱动就返回 None，卡片上显示「—」。"""
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = (out.stdout or "").strip().splitlines()[0]
        name, util, used, total, temp = [x.strip() for x in line.split(",")[:5]]
        return {"name": name, "pct": float(util), "used": float(used) * 1024 ** 2,
                "total": float(total) * 1024 ** 2, "temp": int(float(temp))}
    except Exception:  # noqa: BLE001
        return None


def port_up(port, host="127.0.0.1", timeout=0.35):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def collect():
    """采主机侧数据。psutil 缺失时降级成 None，不抛。"""
    d = {"cpu": {}, "mem": {}, "disk": {}, "gpu": None, "proc": {},
         "host": platform.node() or "?", "py": platform.python_version()}
    try:
        import psutil
    except Exception as e:  # noqa: BLE001
        log.warning("psutil 不可用，状态卡只剩服务信息: %s", e)
        return d
    try:
        d["cpu"] = {"pct": psutil.cpu_percent(interval=0.25),
                    "name": _cpu_name(),
                    "cores": psutil.cpu_count(logical=True) or 0,
                    "phys": psutil.cpu_count(logical=False) or 0}
        vm = psutil.virtual_memory()
        d["mem"] = {"pct": vm.percent, "used": vm.used, "total": vm.total}
        drive = Path(BASE).drive or "C:"
        du = psutil.disk_usage(drive + "\\")
        d["disk"] = {"pct": du.percent, "used": du.used, "total": du.total, "drive": drive}
        p = psutil.Process(os.getpid())
        d["proc"] = {"rss": p.memory_info().rss,
                     "uptime": max(0, time.time() - p.create_time())}
    except Exception as e:  # noqa: BLE001
        log.warning("状态卡采集出错: %s", e)
    d["gpu"] = _gpu()
    return d


# ── 绘制 ────────────────────────────────────────────────────────────────

def _bar(dr, x, y, w, h, pct, color):
    dr.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=(40, 45, 57))
    if pct:
        ww = max(h, int(w * min(100.0, max(0.0, pct)) / 100.0))
        dr.rounded_rectangle((x, y, x + ww, y + h), radius=h // 2, fill=color)


def _tile(dr, x, y, w, h, label, value, sub, pct, color):
    dr.rounded_rectangle((x, y, x + w, y + h), radius=14, fill=CARD)
    dr.text((x + 18, y + 16), label, font=_font(15), fill=MUTED)
    dr.text((x + 18, y + 40), value, font=_font(32, True), fill=TEXT)
    if sub:
        dr.text((x + 18, y + 82), sub, font=_font(13), fill=MUTED)
    if pct is not None:
        _bar(dr, x + 18, y + h - 20, w - 36, 6, pct, color)


def render(data, bot_info=None, out_dir=None):
    """画卡片，返回文件路径。bot_info 见 bot.py 的 status_card()。"""
    bot_info = dict(bot_info or {})
    out_dir = Path(out_dir or OUTDIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)

    # 标题
    name = str(bot_info.get("name") or "机器人")
    dr.text((PAD, 24), name, font=_font(30, True), fill=TEXT)
    tw = dr.textlength(name, font=_font(30, True))
    dr.text((PAD + tw + 12, 36), "· 机器状态", font=_font(17), fill=MUTED)
    ts = time.strftime("%m-%d %H:%M:%S")
    dr.text((W - PAD - dr.textlength(ts, font=_font(15)), 38), ts, font=_font(15), fill=MUTED)

    cpu, mem, disk, gpu = data.get("cpu") or {}, data.get("mem") or {}, \
        data.get("disk") or {}, data.get("gpu")

    # 四个方块
    y0 = 84
    tw4 = (W - PAD * 2 - 3 * 16) // 4
    th = 118
    tiles = [
        ("CPU", "%.0f%%" % cpu["pct"] if cpu.get("pct") is not None else "—",
         "%d 核" % cpu.get("cores", 0) if cpu.get("cores") else "",
         cpu.get("pct"), _pct_color(cpu.get("pct"))),
        ("内存", _gb(mem.get("used")), "共 %s" % _gb(mem.get("total")),
         mem.get("pct"), _pct_color(mem.get("pct"))),
        ("磁盘 %s" % disk.get("drive", ""), _gb(disk.get("used")), "共 %s" % _gb(disk.get("total")),
         disk.get("pct"), _pct_color(disk.get("pct"))),
    ]
    if gpu:
        tiles.append(("显存", _gb(gpu["used"]), "共 %s · %d%% · %d°C"
                      % (_gb(gpu["total"]), gpu["pct"], gpu["temp"]),
                      (gpu["used"] / gpu["total"] * 100) if gpu.get("total") else None,
                      _pct_color((gpu["used"] / gpu["total"] * 100) if gpu.get("total") else None)))
    else:
        tiles.append(("显卡", "—", "nvidia-smi 取不到", None, MUTED))
    for i, (lab, val, sub, pct, col) in enumerate(tiles):
        _tile(dr, PAD + i * (tw4 + 16), y0, tw4, th, lab, val, sub, pct, col)

    # 硬件详情
    y = y0 + th + 22
    dr.text((PAD, y), "硬件", font=_font(14, True), fill=MUTED)
    y += 26
    rows = [("处理器", "%s（%s 核 / %s 线程）" % (
        (cpu.get("name") or "—")[:46], cpu.get("phys") or "?", cpu.get("cores") or "?"))]
    if gpu:
        rows.append(("显卡", "%s · 占用 %d%% · %d°C" % (gpu["name"][:40], gpu["pct"], gpu["temp"])))
    rows.append(("主机", "%s · Python %s" % (data.get("host", "?"), data.get("py", "?"))))
    for lab, val in rows:
        dr.text((PAD, y), lab, font=_font(14), fill=MUTED)
        dr.text((PAD + 74, y), val, font=_font(14), fill=TEXT)
        y += 26

    # 服务与运行
    y += 8
    dr.rectangle((PAD, y, W - PAD, y + 1), fill=LINE)
    y += 18
    dr.text((PAD, y), "服务", font=_font(14, True), fill=MUTED)
    y += 26

    proc = data.get("proc") or {}

    def _dot(x, ok):
        dr.ellipse((x, y + 6, x + 8, y + 14), fill=OK if ok else BAD)

    napcat = bot_info.get("napcat")
    comfy = bot_info.get("comfy")
    items = [
        ("NapCat", napcat, "在线" if napcat else "离线"),
        ("ComfyUI", comfy, "在线" if comfy else "离线"),
    ]
    x = PAD
    for lab, ok, txt in items:
        dr.text((x, y), lab, font=_font(14), fill=MUTED)
        lw = dr.textlength(lab, font=_font(14))
        _dot(x + lw + 8, ok)
        dr.text((x + lw + 22, y), txt, font=_font(14), fill=TEXT if ok else BAD)
        x += 190
    dr.text((x, y), "进程 %s · 已运行 %s" % (_mem(proc.get("rss")), _dur(proc.get("uptime"))),
            font=_font(14), fill=TEXT)

    # 今日/概况
    y += 34
    dr.text((PAD, y), "概况", font=_font(14, True), fill=MUTED)
    y += 26
    sent = bot_info.get("sent") or {}
    lines = [
        "群 %s 个 · 技能 %s 个 · 人格「%s」· 大脑 %s" % (
            bot_info.get("groups", "?"), bot_info.get("skills", "?"),
            bot_info.get("role", "?"), bot_info.get("brain", "?")),
        "发送：近 1 小时 %s 条 · 近 1 分钟 %s 条%s" % (
            sent.get("per_hour", "?"), sent.get("per_minute", "?"),
            ("　熔断中（剩 %ss）" % sent["circuit_left_sec"]) if sent.get("circuit_left_sec") else ""),
    ]
    if bot_info.get("extra"):
        lines.append(str(bot_info["extra"])[:90])
    for ln in lines:
        dr.text((PAD, y), ln, font=_font(14), fill=TEXT)
        y += 26

    # 页脚
    dr.text((PAD, H - 34), "qbot 状态卡 · 数据来自 psutil / nvidia-smi · %s"
            % time.strftime("%Y-%m-%d %H:%M"), font=_font(12), fill=(96, 103, 120))

    path = out_dir / ("status_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
    img.save(path, "PNG", optimize=True)
    return path


def build(bot_info=None, out_dir=None):
    """采集 + 绘制一步到位。"""
    return render(collect(), bot_info, out_dir)
