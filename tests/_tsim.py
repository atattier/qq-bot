# -*- coding: utf-8 -*-
"""「画同款 相似度N%」覆盖功能测试。"""
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import bot

# 1) parse_i2i_dnz_want 用例
CASES = [
    ("相似度60", 60, ""),
    ("相似度 80", 80, ""),
    ("60% 站在沙滩上", 60, "站在沙滩上"),
    ("相似度45 举着伞站在雨里", 45, "举着伞站在雨里"),
    ("90%", 90, ""),
    ("举着伞", None, "举着伞"),
    ("相似度 200", None, "相似度 200"),
    ("保持90%的画面", 90, "保持 的画面"),   # 已知边界：裸 % 会被吃掉，留一个空格
    ("画同款", None, "画同款"),
]
bad = 0
for txt, want_n, want_rest in CASES:
    n, rest = bot.parse_i2i_dnz_want(txt)
    ok = (n == want_n) and (rest.strip() == want_rest.strip())
    if not ok:
        bad += 1
        print("FAIL: %r -> (%r, %r) 期望 (%r, %r)" % (txt, n, rest, want_n, want_rest))
print("parse 用例: %d/%d 通过" % (len(CASES) - bad, len(CASES)))

# 2) 端到端：_i2i_worker 带 dnz_want（用缓存提示词，跳过反推/联网）
SENT, CAP = [], {}
bot.send_group = lambda gid, t: SENT.append(t) or True
def fake_submit(gid, what, style, who, **kw):
    CAP.update(what=what, style=style, kw=kw)
    return True, "queued"
bot.draw_submit = fake_submit
ref_png = sorted(os.listdir(os.path.join(_ROOT, "images")))[0]
src = os.path.join(_ROOT, "images", ref_png)
bot._i2i_worker(123450001, src, "站在夕阳里", {"text": "a girl with long hair", "ident": {}},
                privileged=True, style="krea2", dnz_want=60)
print("--- 消息 ---")
for s in SENT:
    print("  ", s[:110].replace("\n", " / "))
print("denoise =", CAP.get("kw", {}).get("denoise"), "(期望 0.4，相似度60)")
ok = abs((CAP.get("kw", {}).get("denoise") or 0) - 0.40) < 1e-6
# 3) 不指定时维持档位默认
CAP.clear(); SENT.clear()
bot._i2i_worker(123450001, src, "", {"text": "a girl with long hair", "ident": {}},
                privileged=True, style="krea2", dnz_want=None)
print("默认 denoise =", CAP.get("kw", {}).get("denoise"), "(期望 0.8)")
ok2 = abs((CAP.get("kw", {}).get("denoise") or 0) - 0.80) < 1e-6

# 4) 写实档剔画风词后不许留语法残渣（2026-10-29）
#    真实证物：`digital drawing of a chibi illustration-style` 曾剥成 `Digital of a -style`，
#    虚词和 -style 尾巴原样进了正向 CLIP，只占 token 不画东西。
RES_CASES = [
    ("digital drawing of a chibi illustration-style, white background",
     ["white background"], ["-style", " of a", "chibi"]),
    ("anime girl, long black hair, red eyes, cityscape background",
     ["long black hair", "red eyes", "cityscape background"], ["anime girl"]),
    ("1girl, solo, chibi, standing, white dress",
     ["1girl", "solo", "standing", "white dress"], ["chibi"]),
    ("a photo of a girl, upper body", ["photo of a girl", "upper body"], []),
]
bad3 = 0
for src_t, want, deny in RES_CASES:
    out_t, _hits = bot.strip_source_style(src_t)
    if not (all(w in out_t for w in want) and not any(d in out_t for d in deny)):
        bad3 += 1
        print("FAIL strip_source_style: %r -> %r" % (src_t, out_t))
print("画风残渣用例: %d/%d 通过" % (len(RES_CASES) - bad3, len(RES_CASES)))

# 5) 耗时估算要按实测中位数校正（2026-10-29：报 110 秒实际 252 秒）
#    ⚠️ 这段**不许删任何文件**：跑批在沙箱里，「一轮删超过 50 个」会被删除守卫直接拦死
#    （症状：_tsim 明明单跑 PASS，跑批却报 SAFE_DELETE_BULK_CONFIRM_REQUIRED）。
#    所以样本放内存、落盘指向系统 temp 里一个固定名（只覆盖不删），真 state 一个字节都不碰。
import pathlib
import tempfile
_store = {}
_real_times, _real_file = bot._draw_times, bot.DRAW_TIME_FILE
bad4 = 0
_learned = (0, 0)
_cfg_est = (0, 0)
try:
    bot._draw_times = lambda: _store
    bot.DRAW_TIME_FILE = pathlib.Path(tempfile.gettempdir()) / "qbot_draw_time_test.json"
    _cfg_est = bot._draw_est("厚涂")
    bot._est_learn("厚涂", 252)
    _one = bot._draw_est("厚涂")            # 单样本不该改口
    for _s in (240, 260, 248):
        bot._est_learn("厚涂", _s)
    _learned = bot._draw_est("厚涂")        # [252,240,260,248] 中位数 250
    bot._est_learn("厚涂", 1)               # 秒退噪声，必须被拒
    _n = len(bot._draw_times().get("厚涂") or [])
    if _one != _cfg_est:
        bad4 += 1
        print("FAIL 单样本不该改口:", _one, "配置值:", _cfg_est)
    if _learned[0] != 250:
        bad4 += 1
        print("FAIL 中位数应为 250:", _learned)
    if _n != 4:
        bad4 += 1
        print("FAIL 噪声没被拒，样本数=%d" % _n)
finally:
    bot._draw_times, bot.DRAW_TIME_FILE = _real_times, _real_file
print("耗时估算用例: %d/3 通过（学到的 %s，配置值 %s）" % (3 - bad4, _learned, _cfg_est))

print("RESULT:", "PASS" if (ok and ok2 and bad == 0 and bad3 == 0 and bad4 == 0) else "FAIL")
