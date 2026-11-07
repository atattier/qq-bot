# -*- coding: utf-8 -*-
"""阶段 3 验收②：**引擎决策矩阵**回归（确定性桩环境，不联网）。

背景：验收方指出引擎分流改造（handle_i2i / draw_like_this 的 moss/comfyui 分流）
超出了阶段 3 原授权边界，要求提供**修改前后的引擎决策矩阵**，并验证三件事：
  a) 用户指定引擎仍然优先；
  b) Moss 优先级没有被意外改变；
  c) ComfyUI 原有行为没有被意外改变。

矩阵（M1~M10）：见下方 CASES。每个格子直接在桩环境里构造会话开关与偏好，
调用决策函数本体（draw_engine / draw_submit / handle_i2i），断言落点引擎。

用法：python tests/_tengine.py
"""
import os as _os, sys as _sys
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
_os.chdir(_ROOT)
import json
import struct
import sys
import zlib

sys.stdout.reconfigure(encoding="utf-8")
import bot  # noqa: E402

bot.save_config = lambda: True
bot.SELF_ID = 123450002
GID = 123450001
UID = 123450005

FAIL = []

# 确定性：comfy 在线（绕过 draw_precheck 的 8188 探活）、反推模块假定可用由桩覆盖
bot.comfy_up = lambda *a, **kw: True
bot._comfy_up = lambda *a, **kw: True

CLAIMS = []
# 🔴 记录口径：**队列项的最终 engine**（draw_submit 内部还会按 raw_prompt/本地档
#    二次定 engine，stub 掉 draw_submit 只能看到调用方传参，看不到决策结果）。
#    所以这里不 stub draw_submit，而是拦 _draw_q.put 拿排队任务里定稿的 engine。
_real_q_put = bot._draw_q.put
def _rec_qput(task):
    CLAIMS.append("engine=%s|style=%s|i2i=%s|edit=%s|raw=%s" % (
        task.get("engine"), task.get("style"), bool(task.get("i2i")),
        str(task.get("edit") or "")[:16], bool(task.get("raw_prompt"))))
bot._draw_q.put = _rec_qput

_orig_mrev = bot.handle_moss_reverse
_orig_medit = bot.handle_moss_edit
_orig_i2i = bot.handle_i2i
_orig_i2iw = bot._i2i_worker
bot.handle_moss_reverse = lambda *a, **kw: CLAIMS.append("MOSS_REVERSE")
bot.handle_moss_edit = lambda *a, **kw: CLAIMS.append("MOSS_EDIT")
bot._i2i_worker = lambda *a, **kw: CLAIMS.append("COMFY_I2I_WORKER")


def _set_engines(moss=None, comfy=None, pref=None, styles=None):
    """构造会话引擎开关：moss/comfy = 群白名单（True=开/False=关/None=不动），
    pref = comfyui.engine 偏好，styles = 本地档表（style_local_engine 会查它）。"""
    cf = bot.CFG.setdefault("comfyui", {})
    dr = bot.CFG.setdefault("draw", {})
    if moss is not None:
        dr.setdefault("moss", {})["groups"] = [GID] if moss else []
    if comfy is not None:
        dr.setdefault("comfyui", {})["groups"] = [GID] if comfy else []
    if pref is not None:
        cf["engine"] = pref
    if styles is not None:
        class _CG:
            @staticmethod
            def style_dict(_cfg, name):
                return dict(styles.get(name) or {})
        bot.comfy_gen = _CG()


def _reset():
    CLAIMS.clear()
    bot.handle_moss_reverse = lambda *a, **kw: CLAIMS.append("MOSS_REVERSE")
    bot.handle_moss_edit = lambda *a, **kw: CLAIMS.append("MOSS_EDIT")
    bot._i2i_worker = lambda *a, **kw: CLAIMS.append("COMFY_I2I_WORKER")
    bot._draw_q.put = _rec_qput
    bot.comfy_gen = _ORIG_COMFY


_ORIG_COMFY = bot.comfy_gen


def chk(cond, label, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(label)


def _mk_png(path):
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)
    raw = (bytes([0]) + bytes([0xcc, 0x88, 0x99]) * 8) * 8
    blob = (bytes([0x89]) + b"PNG" + bytes([0x0d, 0x0a, 0x1a, 0x0a])
            + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    open(path, "wb").write(blob)


IMG = _os.path.join(bot.STATE, "_tengine_img.png")
_mk_png(IMG)

MOSS_JSON = json.dumps({
    "summary": {"caption": "A girl in white dress"},
    "canvas": {"width": 1280, "height": 1280}, "items": []}, ensure_ascii=False)

STYLES = {  # style_local_engine 的桩表：anima/厚涂=本地档
    "anima": {"engine": "anima"},
    "厚涂": {"engine": "krea2"},
}

print("=== 验收②：引擎决策矩阵（改前 vs 改后）===")
print()

# ── A 组：draw_engine(gid) 决策本身（本函数两个版本逐行一致，行为不变）──
print("-- A. draw_engine：会话开关 → 生效引擎（未改动函数，回归确认）--")
_set_engines(moss=True, comfy=True, pref="moss", styles=STYLES)
chk(bot.draw_engine(GID) == "moss", "M1 双开+偏好moss → moss", bot.draw_engine(GID))
_set_engines(pref="comfyui")
chk(bot.draw_engine(GID) == "comfyui", "M2 双开+偏好comfyui → comfyui", bot.draw_engine(GID))
_set_engines(moss=True, comfy=False)
chk(bot.draw_engine(GID) == "moss", "M3 只开moss → moss（单开即用单开）", bot.draw_engine(GID))
_set_engines(moss=False, comfy=True)
chk(bot.draw_engine(GID) == "comfyui", "M4 只开comfyui → comfyui", bot.draw_engine(GID))
_set_engines(moss=False, comfy=False)
chk(bot.draw_engine(GID) is None, "M9 都没开 → None（画不了）", bot.draw_engine(GID))
_set_engines(moss=True, comfy=True, pref="moss", styles=STYLES)
print()

# ── B 组：用户指定引擎仍然优先（验收点 a）──
print("-- B. 用户指定引擎优先（draw_submit / handle_i2i）--")
_set_engines(moss=True, comfy=True, pref="moss", styles=STYLES)
bot.draw_submit(GID, "一只猫", "anima", "测试", privileged=True)
chk(any("style=anima" in c and "engine=comfyui" in c for c in CLAIMS),
    "M5 偏好moss + 显式本地档「anima」→ 最终 engine=comfyui（用户指定优先于偏好）",
    CLAIMS)
_reset()
_set_engines(moss=True, comfy=False, pref="moss", styles=STYLES)
_r = bot.draw_submit(GID, "一只猫", "厚涂", "测试", privileged=True)
chk((not _r[0]) or any("style=厚涂" in c and "engine=comfyui" in c for c in CLAIMS),
    "M7 只开moss + 显式本地档 → 拒绝或按本地档走 comfy，绝不静默改云端",
    (_r, CLAIMS))
_reset()
_set_engines(moss=True, comfy=True, pref="moss", styles=STYLES)
bot.draw_submit(GID, MOSS_JSON, None, "测试", privileged=True, raw_prompt=True)
chk(any("engine=moss" in c and "raw=True" in c for c in CLAIMS),
    "M6 编译 Moss JSON + 无显式引擎 → 强制 moss（raw_prompt 只能云端）", CLAIMS)
_reset()
_set_engines(moss=True, comfy=True, pref="comfyui", styles=STYLES)
bot.draw_submit(GID, MOSS_JSON, None, "测试", privileged=True, raw_prompt=True, engine="moss")
chk(any("engine=moss" in c and "raw=True" in c for c in CLAIMS),
    "M6b 显式 engine=moss 调用方指定 → 尊重调用方（不被偏好覆盖）", CLAIMS)
_reset()
_set_engines(moss=True, comfy=True, pref="comfyui", styles=STYLES)
bot.draw_submit(GID, "一只猫", None, "测试", privileged=True)
# 无指定时 draw_submit 把 engine=None 原样入队（决策延迟到 do_draw 消费时），
# 落点 = do_draw 的 `job["engine"] or draw_engine(gid)` → 偏好 comfyui
chk(any(c == "engine=None|style=None|i2i=False|edit=|raw=False" for c in CLAIMS)
    and bot.draw_engine(GID) == "comfyui",
    "M11 双开+偏好comfyui+无指定 → 队列 engine=None，do_draw 按 draw_engine 落 comfyui（Moss 未被反向强加）",
    (CLAIMS, bot.draw_engine(GID)))
_reset()
print()

# ── C 组：handle_i2i 的分流（改造点本体；验「用户指定档优先」+「comfyui 原行为」）──
print("-- C. handle_i2i 分流：eng=moss → 云端链路；指定本地档 → 仍走 comfy --")
_set_engines(moss=True, comfy=True, pref="moss", styles=STYLES)
bot.handle_i2i(GID, "", True, imgs=(IMG,))
chk("MOSS_REVERSE" in CLAIMS, "M8a eng=moss + 无补充 → handle_moss_reverse（不查本地8188）", CLAIMS)
_reset()
_set_engines(moss=True, comfy=True, pref="moss", styles=STYLES)
bot.handle_i2i(GID, "把帽子低一点", True, imgs=(IMG,))
chk("MOSS_EDIT" in CLAIMS, "M8b eng=moss + 改稿补充 → handle_moss_edit", CLAIMS)
_reset()
_set_engines(moss=False, comfy=True, pref="comfyui", styles=STYLES)
bot.handle_i2i(GID, "", True, imgs=(IMG,))
chk("COMFY_I2I_WORKER" in CLAIMS, "M8c eng=comfyui → 原有 _i2i_worker 链路原样", CLAIMS)
_reset()
_set_engines(moss=True, comfy=True, pref="moss", styles=STYLES)
bot.handle_i2i(GID, "", True, imgs=(IMG,), style="anima")
chk("COMFY_I2I_WORKER" in CLAIMS, "M8d eng=moss 但用户指定本地档 anima → 仍走 comfy（用户指定优先）",
    CLAIMS)
_reset()
_set_engines(moss=True, comfy=False, pref="moss", styles=STYLES)
bot.handle_i2i(GID, "", True, imgs=(IMG,), style="anima")
chk("COMFY_I2I_WORKER" in CLAIMS, "M8e 只开moss+指定本地档 → 也按用户指定走 comfy（直发不静默改云端）",
    CLAIMS)
_reset()
print()

# ── D 组：brain 工具面 draw_like_this 的动态分流（_exec_bot_tool）──
print("-- D. draw_like_this 工具 → 按 draw_engine 动态分流 --")
_set_engines(moss=True, comfy=False, pref="moss", styles=STYLES)
bot._exec_bot_tool("draw_like_this", {"what": "照着这张画"}, GID, uid=str(UID),
                   priv=True, imgs=(IMG,), qmid=0)
chk("MOSS_REVERSE" in CLAIMS or "MOSS_EDIT" in CLAIMS,
    "M10a moss 引擎会话里大脑选 draw_like_this → 云端链路（原缺陷已修）", CLAIMS)
_reset()
_set_engines(moss=False, comfy=True, pref="comfyui", styles=STYLES)
bot._exec_bot_tool("draw_like_this", {"what": "照着这张画"}, GID, uid=str(UID),
                   priv=True, imgs=(IMG,), qmid=0)
chk("COMFY_I2I_WORKER" in CLAIMS,
    "M10b comfyui 引擎会话里 draw_like_this → 本地图生图（原行为）", CLAIMS)
_reset()
print()

print("=== 汇总 ===")
print("失败 %d 项" % len(FAIL))
if FAIL:
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)
