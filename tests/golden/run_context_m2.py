# -*- coding: utf-8 -*-
"""M2 Context Assembler 回放：Golden Set 240 条 → 18 段上下文审计。

只组装、不调模型、不发消息、不执行工具、不联网；证据落
`tests/golden/m2_context/summary.json|md`。用于验收「18 段有来源与优先级、
超预算有日志且关键段不丢」在真实样本上的覆盖率。
"""
import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
os.environ["QBOT_TEST"] = "1"
os.environ.pop("QBOT_ARCH_SHADOW", None)
os.environ["QBOT_ARCH_CONTEXT"] = "assembler"

import bot  # noqa: E402
import qbot_context as X  # noqa: E402
import run_shadow_m1 as M1R  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "golden_set.json"
OUT = HERE / "m2_context"


class Capture:
    def __init__(self):
        self.warnings = Counter()

    def warning(self, fmt, *a):
        try:
            msg = fmt % a
        except Exception:
            msg = str(fmt)
        for key in ("丢弃可选段", "关键段仍超预算", "超过单段上限", "生成失败"):
            if key in msg:
                self.warnings[key] += 1
                break
        else:
            self.warnings["其它"] += 1

    def debug(self, *a):
        pass

    info = debug


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    samples = json.loads(DATA.read_text(encoding="utf-8"))["samples"]
    if args.limit:
        samples = samples[:args.limit]
    bot.CFG.setdefault("arch", {})["context"] = "assembler"
    bot.CFG["arch"]["context_disabled"] = []
    cap = Capture()
    asm = X.ContextAssembler(providers=bot._shadow_context_providers(),
                             logger=cap, budget_chars=24000)
    tools = bot._shadow_tools()

    status = defaultdict(Counter)
    used, over, errors = [], 0, []
    media_hit = media_total = 0
    t0 = time.time()
    for s in samples:
        turn, hist = M1R.build_sample_turn(s)
        try:
            m = asm.build(turn, history=hist, tools=tools)
        except Exception as e:  # noqa: BLE001
            errors.append({"id": s.get("id"), "error": str(e)})
            continue
        used.append(m.used_chars)
        over += 1 if m.over_budget else 0
        for seg in m.segments:
            status[seg.id][seg.status] += 1
            if seg.status == "error":
                errors.append({"id": s.get("id"), "segment": seg.id,
                               "error": seg.reason})
        if turn.media or (turn.quote and turn.quote.media):
            media_total += 1
            if m.segment("media").status == "included":
                media_hit += 1

    elapsed = time.time() - t0
    seg_rows = []
    for spec in X.SPECS:
        c = status[spec.id]
        included = c.get("included", 0) + c.get("truncated", 0)
        seg_rows.append({
            "id": spec.id, "title": spec.title, "source": spec.source,
            "priority": spec.priority, "critical": spec.critical,
            "included": included, "coverage": round(included / max(1, len(used)), 4),
            "statuses": dict(c),
        })
    summary = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "samples": len(samples), "assembled": len(used), "errors": len(errors),
        "elapsed_sec": round(elapsed, 1),
        "budget_chars": asm.budget_chars,
        "used_chars": {
            "p50": int(statistics.median(used)) if used else 0,
            "max": max(used) if used else 0,
            "mean": int(statistics.mean(used)) if used else 0,
        },
        "over_budget": over,
        "warnings": dict(cap.warnings),
        "media_cases": media_total, "media_included": media_hit,
        "segments": seg_rows,
        "critical_dropped": [
            r["id"] for r in seg_rows
            if r["critical"] and r["statuses"].get("dropped_budget", 0)],
        "error_samples": errors[:20],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# M2 Context Assembler 回放结果", "",
        "- 样本：Golden Set %d 条（只组装，不调模型）" % len(samples),
        "- 完成：%d；异常：%d；耗时 %.1fs" % (len(used), len(errors), elapsed),
        "- 上下文预算：%d 字；实际 p50=%s / mean=%s / max=%s" % (
            asm.budget_chars, summary["used_chars"]["p50"],
            summary["used_chars"]["mean"], summary["used_chars"]["max"]),
        "- 超预算：%d 条（关键段被丢：%s）" % (
            over, summary["critical_dropped"] or "无"),
        "- 告警：%s" % (dict(cap.warnings) or "无"),
        "- 带图样本：%d；媒体段命中：%d" % (media_total, media_hit),
        "", "## 18 段覆盖与来源", "",
        "| 段 | 优先级 | 关键 | 来源 | 出现 | 覆盖率 | 状态分布 |",
        "|---|---:|---|---|---:|---:|---|",
    ]
    for r in seg_rows:
        lines.append("| `%s` %s | %d | %s | %s | %d | %.1f%% | %s |" % (
            r["id"], r["title"], r["priority"], "是" if r["critical"] else "否",
            r["source"], r["included"], r["coverage"] * 100,
            ", ".join("%s=%d" % (k, v) for k, v in sorted(r["statuses"].items()))))
    if errors:
        lines += ["", "## 异常样本（前 20）", "", "| 样本 | 段 | 原因 |", "|---|---|---|"]
        for e in errors[:20]:
            lines.append("| %s | %s | %s |" % (
                e.get("id"), e.get("segment", "-"), str(e.get("error"))[:80]))
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in (
        "samples", "assembled", "errors", "elapsed_sec", "used_chars",
        "over_budget", "warnings", "media_cases", "media_included",
        "critical_dropped")}, ensure_ascii=False, indent=2))
    print("out:", OUT)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
