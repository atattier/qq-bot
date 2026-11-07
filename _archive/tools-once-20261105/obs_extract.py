# -*- coding: utf-8 -*-
"""只读观察样本抽取器（阶段：真实群聊观察验证）。

从 bot.log 抽取"用户消息 → 工具调用/回复"配对样本，输出 JSON 供人工标注。
只读 bot.log，不启动 bot、不改代码、不碰 config。

用法：
  python state/_obs_extract.py            # 抽 2026-10-31 之后的样本
  python state/_obs_extract.py 2026-11-01 # 指定起始日期
输出：state/_obs_samples.json（可再人工标注 intent/decision 字段）
"""
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "bot.log")
OUT = os.path.join(ROOT, "state", "_obs_samples.json")

SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-10-31"

# 行格式：2026-11-02 19:35:27,227 INFO 群消息 群 xxx user=昵称(uid): text
RE_MSG = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO (群消息|私聊消息) (.+?) user=(.+?)\((\d+)\): (.*)$")
RE_TOOL = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO 工具调用\[(\w+)\] (\w+) -> (.*)$")
RE_REPLY = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO AI 回复\[([^\]]*)\] group=(-?\d+): (.*)$")
RE_ROUTE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO AI 画图路由([^:]*):(.*)$")
RE_DRAWQ = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO 现画排队 group=(-?\d+) by (.+?): (.*)$")
RE_MOSS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO Moss提示词：([^ ]*)")
RE_SEND = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO .*(发送|send)[^\s]*", re.I)


def _nearest(samples, ts, window=120, want_conv=None):
    """时间最近、120s 内的消息（可选限定会话号）。"""
    from datetime import datetime
    try:
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    best, best_d = None, 1e9
    for smp in reversed(samples[-80:]):
        try:
            st = datetime.strptime(smp["ts"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        d = (t - st).total_seconds()
        if d < 0 or d > window:
            continue
        if want_conv and want_conv not in smp["conv"]:
            continue
        if d < best_d:
            best, best_d = smp, d
    return best


def main():
    lines = io.open(LOG, encoding="utf-8", errors="replace").read().splitlines()
    samples = []
    cur = None
    for ln in lines:
        m = RE_MSG.match(ln)
        if m:
            ts, kind, conv, who, uid, text = m.groups()
            if ts[:10] < SINCE:
                cur = None
                continue
            cur = {"ts": ts, "conv": conv, "who": who, "uid": uid,
                   "text": text, "has_img": "[图片]" in text or "[动画表情]" in text,
                   "tools": [], "replies": [], "routes": [], "draws": [], "moss": []}
            samples.append(cur)
            continue
        if cur is None:
            continue
        m = RE_TOOL.match(ln)
        if m:
            # 工具调用归属：时间上最近的那条消息（120s 内，且是同一会话群的最近消息）
            tgt = _nearest(samples, m.group(1), window=120)
            (tgt or cur)["tools"].append({"tool": m.group(3), "result": m.group(4)[:160]})
            continue
        m = RE_REPLY.match(ln)
        if m:
            tgt = _nearest(samples, m.group(1), window=120, want_conv=m.group(3))
            (tgt or cur)["replies"].append({"provider": m.group(2), "text": m.group(4)[:120]})
            continue
        m = RE_ROUTE.match(ln)
        if m:
            cur["routes"].append((m.group(2) + ":" + m.group(3))[:160])
            continue
        m = RE_DRAWQ.match(ln)
        if m:
            cur["draws"].append(m.group(4)[:80])
            continue
        m = RE_MOSS.match(ln)
        if m:
            cur["moss"].append(m.group(2)[:100])
            continue
    # 过滤：只要"有实际内容"的消息（排除纯占位/空）
    keep = []
    for s in samples:
        t = s["text"].strip()
        if not t and not s["has_img"]:
            continue
        if t in ("[图片]", "[动画表情]", "[视频]"):
            s["text_only_img"] = True
        keep.append(s)
    json.dump(keep, io.open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("样本数:", len(keep), "→", OUT)
    # 粗分类统计
    from collections import Counter
    c = Counter()
    for s in keep:
        if s["tools"]:
            c["有工具调用"] += 1
        elif s["replies"]:
            c["纯聊天回复"] += 1
        elif s["draws"]:
            c["口令画图"] += 1
        elif s["routes"]:
            c["画图路由"] += 1
        else:
            c["无可见动作（沉默/命令/未回复）"] += 1
    print("分类:", dict(c))
    # 有工具的样本里，工具分布
    tc = Counter()
    for s in keep:
        for t in s["tools"]:
            tc[t["tool"]] += 1
    print("工具分布:", dict(tc.most_common()))


if __name__ == "__main__":
    main()
