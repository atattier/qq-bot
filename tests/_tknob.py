# -*- coding: utf-8 -*-
"""/admin 档位降噪旋钮：读写 + 热生效测试（save_config 打桩，不动磁盘）。"""
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)
sys.stdout.reconfigure(encoding="utf-8")
import bot

bot.save_config = lambda: True          # 打桩：别把测试值写进 config.json

def g(label):
    e = bot.knob_find(label)
    return e, bot.knob_get(e)

bad = 0
# 1) 初始值：写实=80（档位覆盖），动画/厚涂=60（兜底回落，如实显示生效值）——2026-09-30 三档
for lab, want in (("写实档降噪", 80), ("动画档降噪", 60), ("厚涂档降噪", 60)):
    e, v = g(lab)
    if v != want:
        print("FAIL 初始 %s = %r 期望 %r" % (lab, v, want)); bad += 1
    else:
        print("OK 初始 %s = %s" % (lab, v))

# 2) 设置 → 热生效
ok, msg = bot.knob_set("写实档降噪", "55", privileged=True)
if not ok or abs(bot.i2i_denoise("", "krea2") - 0.55) > 1e-9:
    print("FAIL 写实设置:", msg, bot.i2i_denoise("", "krea2")); bad += 1
else:
    print("OK 写实 55 -> i2i_denoise=%.2f | %s" % (bot.i2i_denoise("", "krea2"), msg))

ok, msg = bot.knob_set("动画档降噪", "70", privileged=True)
if not ok or abs(bot.i2i_denoise("", "anima") - 0.70) > 1e-9:
    print("FAIL 动画设置:", msg); bad += 1
else:
    print("OK 动画 70 -> %.2f" % bot.i2i_denoise("", "anima"))

# 3) 边界：95 被拒（max 90）、非数字被拒
ok1, m1 = bot.knob_set("写实档降噪", "95", privileged=True)
ok2, m2 = bot.knob_set("写实档降噪", "abc", privileged=True)
if ok1 or ok2:
    print("FAIL 边界: %r %r" % (ok1, ok2)); bad += 1
else:
    print("OK 边界拒绝:", m1, "|", m2)

# 4) 越权：非管理员拒绝
ok, msg = bot.knob_set("写实档降噪", "65", privileged=False)
if ok:
    print("FAIL 越权"); bad += 1
else:
    print("OK 越权拒绝:", msg)

# 5) knob_items 快照里有这三项（/admin 页面数据源）
items = {i["key"] for i in bot.knob_items("反推")}
for k in ("comfyui.styles.krea2.i2i_denoise", "comfyui.styles.anima.i2i_denoise",
          "comfyui.styles.厚涂.i2i_denoise"):
    if k not in items:
        print("FAIL 快照缺", k); bad += 1
print("快照: 3/3 在" if bad == 0 else "快照有缺")

# 6) 兜底说明：reverse.i2i_denoise 仍在
e = bot.knob_find("同款相似度")
print("兜底旋钮在:", bool(e), "| 现值:", bot.knob_get(e))

# 7) 重名标签消歧（2026-10-02 主人报：视频生成卡改冷却秒/每小时上限不生效）
dupes = dict(bot.knob_dupes())
print("重名标签:", "、".join("%s×%d" % (k, len(v)) for k, v in dupes.items()))
for lab in ("冷却秒", "每小时上限", "群友可用", "冷却"):
    cnt = len(dupes.get(lab, []))
    if cnt < 2:
        print("FAIL 期望 %s 是重名项" % lab); bad += 1
    else:
        print("OK %s 重名 %d 处: %s" % (lab, cnt, dupes[lab]))

CASE = [("视频生成 冷却秒", "comfyui.video_gen.cooldown_sec"),
        ("视频 冷却秒", "video.cooldown_sec"),
        ("现画 冷却", "comfyui.cooldown_sec"),
        ("反推 冷却", "reverse.cooldown_sec"),
        ("视频生成 每小时上限", "comfyui.video_gen.max_per_hour"),
        ("现画 每小时上限", "comfyui.max_per_hour"),
        ("视频生成 群友可用", "comfyui.video_gen.members_allowed"),
        ("现画 群友可用", "comfyui.members_allowed"),
        ("反推 群友可用", "reverse.members_allowed"),
        ("视频生成/冷却秒", "comfyui.video_gen.cooldown_sec"),
        ("comfyui.video_gen.cooldown_sec", "comfyui.video_gen.cooldown_sec"),
        ("画师/角色选配", "comfyui.artist_pick"),      # 标签自带斜杠，不能被误切
        ("现画", "comfyui.enabled"),                   # 与分组同名，按标签走老路
        ("查证后端", "comfyui.subject_backend"),
        ("冷却秒", "video.cooldown_sec")]              # 不给分组 = 注册表第一个（老行为）
for name, want in CASE:
    e = bot.knob_find(name)
    got = e["key"] if e else None
    if got != want:
        print("FAIL knob_find(%r) -> %r 期望 %r" % (name, got, want)); bad += 1
    else:
        print("OK knob_find(%r) -> %s" % (name, got))

# 8) 口令切词：「设置 视频生成 冷却秒 300」两段式项名
SPLIT = [("视频生成 冷却秒 300", ("视频生成 冷却秒", "300")),
         ("视频生成 冷却秒", ("视频生成 冷却秒", "")),
         ("冷却秒 300", ("冷却秒", "300")),
         ("联网 开", ("联网", "开")),
         ("画图档 anima", ("画图档", "anima")),
         ("超时秒 9000", ("超时秒", "9000")),
         ("", ("", ""))]
for body, want in SPLIT:
    got = bot._split_setting(body)
    if got != want:
        print("FAIL _split_setting(%r) -> %r 期望 %r" % (body, got, want)); bad += 1
    else:
        print("OK _split_setting(%r) -> %r" % (body, got))

# 9) 端到端：带分组改名只动目标键，别串到同名项
_old_vg = bot._dot_get("comfyui.video_gen.cooldown_sec")
_old_v = bot._dot_get("video.cooldown_sec")
ok, msg = bot.knob_set("视频生成 冷却秒", "1800", privileged=True)
after_vg = bot._dot_get("comfyui.video_gen.cooldown_sec")
after_v = bot._dot_get("video.cooldown_sec")
if not ok or after_vg != 1800 or after_v != _old_v:
    print("FAIL 分组改名: ok=%r vg=%r(期望1800) video=%r(期望%r) | %s"
          % (ok, after_vg, after_v, _old_v, msg)); bad += 1
else:
    print("OK 分组改名 -> 只改 comfyui.video_gen.cooldown_sec=1800，video.cooldown_sec 未动")
bot._dot_set("comfyui.video_gen.cooldown_sec", _old_vg)      # 内存复原
bot._dot_set("video.cooldown_sec", _old_v)

# 10) 帮助文案里重名项带分组前缀
help_txt = bot.knob_help_text()
for want in ("视频生成 冷却秒", "现画 冷却", "反推 群友可用"):
    if want not in help_txt:
        print("FAIL 帮助文案缺 %r" % want); bad += 1
print("OK 帮助文案给重名项加了分组前缀" if bad == 0 else "帮助文案有缺")

print("RESULT:", "PASS" if bad == 0 else "FAIL")
