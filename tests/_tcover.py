# -*- coding: utf-8 -*-
"""离线回归：点播语音路由 + 网易云翻唱取源解析/校验/命令接入。

不联网、不合成、不碰真 config.json：网络与发送函数全部打桩。
"""
import os
import queue
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import bot
import qbot_cover

print("=== 1. 模块加载 ===")
assert bot.qbot_cover is qbot_cover, "bot 没加载 qbot_cover"
assert bot.tts is not None, "tts 模块应可用"
print("  qbot_cover / tts:", "OK")

print()
print("=== 2. 翻唱配置默认值 ===")
cfg = bot._cover_cfg()
print("  ", {k: cfg[k] for k in ("enabled", "max_duration_sec", "steps", "min_free_vram_mb")})
assert cfg["min_duration_sec"] == 60, "试听片段下限 60s"
assert cfg["max_duration_sec"] == 420, "翻唱上限 7 分钟"
assert cfg["queue_max"] >= 1

print()
print("=== 3. 歌曲请求解析 ===")
cases = [
    ("起风了", "query", "起风了"),
    ("网易云 起风了", "query", "起风了"),
    ("一首 起风了", "query", "起风了"),
    ("1330348068", "id", 1330348068),
    ("https://music.163.com/song?id=1330348068&userid=1", "id", 1330348068),
    ("https://y.music.163.com/m/song?id=1330348068", "id", 1330348068),
    ("https://music.163.com/#/song?id=1330348068", "id", 1330348068),
]
for text, kind, value in cases:
    got = qbot_cover.parse_request(text)
    print("  %-52r -> %s" % (text, got))
    assert got == {"kind": kind, "value": value}, "解析错: %r" % text
try:
    qbot_cover.parse_request("   ")
    raise AssertionError("空歌名应报错")
except qbot_cover.CoverError as e:
    print("  空输入 ->", e)

print()
print("=== 4. 音频体检（本地 1s wav 必须被拒） ===")
tmp = tempfile.mkdtemp(prefix="qbot_cov_")
wav = os.path.join(tmp, "short.wav")
with wave.open(wav, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(8000)
    w.writeframes(b"\x00\x00" * 8000)
info = qbot_cover.probe(wav)
print("  probe:", info)
assert 0.9 < info["duration"] < 1.1, "ffprobe 应读到 1s，实际 %r" % info
try:
    qbot_cover.validate_audio(info, 60, 420)
    raise AssertionError("1s 试听片段应被拒")
except qbot_cover.CoverError as e:
    print("  拒绝 ->", e)
qbot_cover.validate_audio({"duration": 180}, 60, 420)
qbot_cover.validate_audio({}, 60, 420)      # ffprobe 缺信息时不硬拦
print("  180s / 未知 -> 放行")

print()
print("=== 5. 点播语音命令 ===")
real_send = bot.send_group
real_gate = bot._voice_gate
real_up = bot._tts_service_up
sent = []
bot.send_group = lambda cid, text, *a, **k: sent.append(text) or True
bot._voice_gate = lambda: (True, "")
bot._tts_service_up = lambda timeout=2: True
bot._voice_q = queue.Queue()
try:
    assert bot.handle_voice_cmd(-123, "说一句 今天也要开心", addressed=True,
                                is_group=False) is True
    assert bot.handle_voice_cmd(-123, "闲聊一句话", addressed=True, is_group=False) is False
    assert bot.handle_voice_cmd(999, "说一句 你好", addressed=False, is_group=True) is False
    items = []
    while not bot._voice_q.empty():
        items.append(bot._voice_q.get())
    print("  入队:", items)
    assert items and items[0][2] == "manual", "点播应标 manual"
    assert len(bot.split_speech("第一句。" * 40)) <= 4
finally:
    bot.send_group = real_send
    bot._voice_gate = real_gate
    bot._tts_service_up = real_up

print()
print("=== 6. 翻唱命令：候选 → 回编号直接开唱（全打桩） ===")
from pathlib import Path


class _FakeCover:
    DEFAULT_TARGET = qbot_cover.DEFAULT_TARGET
    CoverError = qbot_cover.CoverError
    parse_request = staticmethod(qbot_cover.parse_request)

    @staticmethod
    def search_candidates(q, limit=5, min_sec=60, max_sec=420):
        return [
            {"id": 1330348068, "name": "起风了", "artist": "冯沁苑",
             "album": "", "duration_sec": 325.9, "fee": 8},
            {"id": 222, "name": "起风了 (伴奏)", "artist": "X",
             "album": "", "duration_sec": 326.0, "fee": 8},
        ]

    @staticmethod
    def fetch(song_id, dest_dir, timeout=90, max_mb=40):
        p = Path(dest_dir) / ("%d.mp3" % song_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(b"fake-mp3")
        return p, "audio/mpeg"


real_cover = bot.qbot_cover
real_cfg = bot.CFG.get("cover")
work = tempfile.mkdtemp(prefix="qbot_cov_flow_")
cover_cfg = dict(cfg)
cover_cfg.update({"src_dir": os.path.join(work, "src"), "out_dir": os.path.join(work, "out"),
                  "confirm": True, "cooldown_sec": 0, "max_per_hour": 99})
sent.clear()
bot.send_group = lambda cid, text, *a, **k: sent.append(text) or True
bot.qbot_cover = _FakeCover
bot.CFG["cover"] = cover_cfg
bot._cover_q = queue.Queue()
bot._cover_user = {}
bot._cover_pend.clear()
key = (str(999), str(111))

try:
    assert bot.handle_cover_reply(999, True, 111, "1") is False, "没待办时数字不该被接管"
    assert bot.handle_cover_cmd(999, "翻唱 起风了", addressed=True, is_group=True,
                                uid=111, who="tester") is True
    assert bot.handle_cover_cmd(999, "翻唱 起风了", addressed=False, is_group=True,
                                uid=111, who="tester") is False
    assert bot.handle_cover_cmd(999, "普通聊天", addressed=True, is_group=True,
                                uid=111) is False
    p = bot._cover_pend.get(key)
    print("  待选:", p["stage"], [c["name"] for c in p["cands"]])
    assert p and p["stage"] == "pick" and len(p["cands"]) == 2
    assert bot._cover_q.empty(), "选版本之前不该开跑"
    assert "回编号" in sent[-1], sent[-1]

    assert bot.handle_cover_reply(999, True, 999, "1") is False, "别人回编号不该被接管"
    assert bot.handle_cover_reply(999, True, 111, "9") is True
    print("  越界文案:", sent[-1])
    assert bot._cover_pend[key]["stage"] == "pick"

    # 回编号 = 直接开唱，不再要「确认」
    assert bot.handle_cover_reply(999, True, 111, "1") is True
    job = bot._cover_q.get_nowait()
    print("  选版即入队:", job["song"]["name"], "|", sent[-1][:24], "…")
    assert job["song"]["id"] == 1330348068
    assert job["req"] == {"kind": "query", "value": "起风了"}
    assert key not in bot._cover_pend
    assert "越界" not in sent[-1] and "确认" not in sent[-1]

    # 取消：清状态、不入队
    bot._cover_user = {}
    bot.handle_cover_cmd(999, "翻唱 稻香", addressed=True, is_group=True, uid=111)
    assert bot.handle_cover_reply(999, True, 111, "取消") is True
    assert key not in bot._cover_pend
    assert bot._cover_q.empty(), "取消后不该入队"
    print("  取消清理:", "OK")

    # 链接/ID 是明确指向：跳过候选直接入队
    bot._cover_user = {}
    assert bot.handle_cover_cmd(999, "翻唱 1330348068", addressed=True, is_group=True,
                                uid=111) is True
    job = bot._cover_q.get_nowait()
    assert job["req"] == {"kind": "id", "value": 1330348068} and key not in bot._cover_pend
    print("  ID 直通:", job["req"])
finally:
    bot.send_group = real_send
    bot.qbot_cover = real_cover
    bot._cover_pend.clear()
    if real_cfg is None:
        bot.CFG.pop("cover", None)
    else:
        bot.CFG["cover"] = real_cfg

print()
print("=== 7. 成品按 QQ 语音分段 ===")
import shutil
import subprocess

ff = shutil.which("ffmpeg")
if not ff:
    print("  ffmpeg 不在 PATH，跳过")
else:
    src = os.path.join(tmp, "long70.mp3")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "anullsrc=r=8000:cl=mono", "-t", "70", "-q:a", "9", src],
                   check=True, timeout=60)
    segs, seg_dir = bot._cover_voice_segments(src, 30)
    durs = [round(bot.qbot_cover.probe(str(s)).get("duration") or 0, 1) for s in segs]
    print("  70s 按 30s 切 ->", len(segs), "段", durs)
    assert len(segs) == 3 and sum(durs) >= 68, (len(segs), durs)
    assert seg_dir is not None and Path(seg_dir).is_dir()

    sent.clear()
    real_send = bot.send_group
    real_voice = bot.send_voice
    bot.send_group = lambda cid, text, *a, **k: sent.append(text) or True
    bot.send_voice = lambda cid, p: sent.append(p) or True
    try:
        ok = bot._cover_send_voice(-1, src, {"name": "测试曲", "artist": "t"}, 70,
                                   {"voice_segment_sec": 30, "voice_gap_sec": 0,
                                    "voice_max_segments": 10})
    finally:
        bot.send_group = real_send
        bot.send_voice = real_voice
    assert ok is True
    assert len(sent) == 4, sent          # 1 条说明 + 3 段语音
    print("  发送:", sent[0][:36], "…", "段数", len(sent) - 1)
    assert "分成 3 段语音" in sent[0]
    shutil.rmtree(seg_dir, ignore_errors=True)

print()
print("PASS _tcover")
