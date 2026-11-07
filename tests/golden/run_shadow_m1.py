# -*- coding: utf-8 -*-
"""M1 影子核心回放：Golden Set → Turn → Core → 决策/差异/延迟证据。

两种模式：
  python tests/golden/run_shadow_m1.py --fake          # 全 240 条，假模型，只验队列/覆盖率
  python tests/golden/run_shadow_m1.py --limit 72      # 分层抽样，真本地 Ollama，测延迟
边界：只读 golden_set.json；不发 QQ 消息、不执行工具、不写生产 state。
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)
os.environ["QBOT_TEST"] = "1"
os.environ.pop("QBOT_ARCH_SHADOW", None)

import bot  # noqa: E402
from qbot import qbot_core as C  # noqa: E402
from qbot.qbot_turn import make_turn  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "golden_set.json"
OUT = HERE / "m1_shadow"
ROLE_MAP = {"master": "owner", "admin": "admin", "member": "member", "stranger": "stranger"}


def pick_samples(samples, limit):
    """按 category 轮转取样，保证每个类别都有。"""
    buckets = defaultdict(list)
    for s in samples:
        buckets[s.get("category") or "?"].append(s)
    order = sorted(buckets)
    out = []
    i = 0
    while len(out) < limit and any(buckets[c] for c in order):
        c = order[i % len(order)]
        if buckets[c]:
            out.append(buckets[c].pop(0))
        i += 1
    return out


def build_sample_turn(s):
    actor = s.get("actor") or {}
    inp = s.get("input") or {}
    scope = "group" if actor.get("scope") == "group" else "private"
    role = ROLE_MAP.get(actor.get("role"), "member")
    uid = {"owner": 10001, "admin": 10002, "member": 20001, "stranger": 30001}.get(role, 20001)
    quote = inp.get("quote") or {}
    imgs = [x.get("ref") or "" for x in (inp.get("images") or []) if isinstance(x, dict)]
    qimgs = [x.get("ref") or "" for x in (quote.get("images") or []) if isinstance(x, dict)]
    hist = []
    for h in (inp.get("history") or []):
        hist.append({"role": "user", "content": str(h)})
    for c in (inp.get("context") or []):
        hist.append({"role": "system", "content": "上下文: " + str(c)})
    text = str(inp.get("text") or "")
    if quote.get("text"):
        text = "引用: " + str(quote["text"]) + "\n" + text
    return make_turn(
        event_id="golden:" + str(s.get("id")),
        session_kind=scope,
        session_key=8123 if scope == "group" else -uid,
        user_id=uid,
        display_name={"owner": "主人", "admin": "管理员", "member": "群友",
                      "stranger": "陌生人"}.get(role, "群友"),
        role=role,
        received_at=1793878000,
        text=text,
        media_refs=[{"file": x} for x in imgs],
        quote_text=str(quote.get("text") or ""),
        quote_media_refs=[{"file": x} for x in qimgs],
        quote_unresolved=bool(quote and not quote.get("text") and not qimgs),
        addressed=bool(actor.get("addressed")),
        flags={"privileged": role == "owner"},
    ), hist


def fake_decide(messages):
    """确定性假模型：只证明管线能对每条样本产出决策。"""
    return json.dumps({"action": "silent", "reply": "", "reason": "fake replay",
                       "confidence": 0.0}, ensure_ascii=False), {"backend": "fake"}


def to_eval_decision(sid, d):
    actions = [d.action]
    tools = []
    if d.action == "tool_call" and d.tool:
        tools.append(d.tool)
        if d.tool == "remember":
            actions.append("memory_write")
        elif d.tool == "qq_send_group":
            actions.append("send_group")
    elif d.action == "tool_call":
        actions.append("tool_call")
    return {"id": sid, "actions": actions, "tools": tools,
            "reply": d.reply, "used_media": []}


def decision_diff(sample, d):
    """影子决策 vs Golden 期望：每一条都给出可复核分类。"""
    if d.action in ("invalid", "error") or "policy:invalid_decision" in (d.policy_notes or []):
        return "invalid"
    actions = set(to_eval_decision(sample["id"], d)["actions"])
    forb = set((sample.get("forbidden") or {}).get("actions") or [])
    forb_tools = set((sample.get("forbidden") or {}).get("tools") or [])
    exp = set((sample.get("expected") or {}).get("actions") or [])
    exp_tools = {t.get("name") for t in ((sample.get("expected") or {}).get("tools") or [])
                 if isinstance(t, dict)}
    if actions & forb or (d.action == "tool_call" and d.tool in forb_tools):
        return "forbidden_hit"
    if d.action == "tool_call" and d.tool and exp_tools and d.tool not in exp_tools:
        return "tool_mismatch"
    if actions <= exp and actions:
        return "match_expected"
    return "action_mismatch"


def run_one(sample, core, tools_wire):
    turn, hist = build_sample_turn(sample)
    t0 = time.time()
    d = core.run(turn, history=hist, tools=tools_wire)
    wall = int((time.time() - t0) * 1000)
    return {
        "id": sample["id"],
        "category": sample.get("category"),
        "expected_actions": (sample.get("expected") or {}).get("actions") or [],
        "forbidden_actions": (sample.get("forbidden") or {}).get("actions") or [],
        "turn_id": turn.turn_id,
        "decision": d.to_dict(),
        "eval_decision": to_eval_decision(sample["id"], d),
        "diff": decision_diff(sample, d),
        "wall_ms": wall,
        "model_latency_ms": d.latency_ms,
    }


def pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    k = min(len(vals) - 1, max(0, int(round((p / 100.0) * (len(vals) - 1)))))
    return vals[k]


def write_outputs(results, scope_size, mode, provider, elapsed, scope_rule,
                  cost_note, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "decisions.jsonl").write_text(
        "".join(json.dumps(r["eval_decision"], ensure_ascii=False) + "\n" for r in results),
        encoding="utf-8")
    (outdir / "result.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in results),
        encoding="utf-8")

    lat = [r["model_latency_ms"] for r in results if r["decision"]["action"] not in ("error", "invalid")]
    actions = Counter(r["decision"]["action"] for r in results)
    diffs = Counter(r["diff"] for r in results)
    cats = Counter(r["category"] for r in results)
    by_cat = defaultdict(lambda: Counter())
    for r in results:
        by_cat[r["category"]][r["diff"]] += 1
    usage = Counter()
    for r in results:
        u = r["decision"].get("usage") or {}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage[k] += int(u.get(k) or 0)
    errors = [r for r in results if r["decision"]["action"] in ("error", "invalid")
              or "policy:invalid_decision" in (r["decision"].get("policy_notes") or [])]
    review = [r for r in results if r["diff"] not in ("match_expected",)][:20]

    summary = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": mode, "provider": provider,
        "scope": scope_size, "scope_rule": scope_rule,
        "coverage": round(len(results) / scope_size, 4) if scope_size else 0.0,
        "completed": len(results), "errors": len(errors),
        "elapsed_sec": round(elapsed, 1),
        "latency_ms": {
            "p50": pct(lat, 50), "p95": pct(lat, 95),
            "min": min(lat) if lat else None, "max": max(lat) if lat else None,
            "mean": int(statistics.mean(lat)) if lat else None,
        },
        "actions": dict(actions), "diffs": dict(diffs),
        "categories": dict(cats),
        "by_category": {k: dict(v) for k, v in by_cat.items()},
        "usage": dict(usage), "cost_note": cost_note,
        "review_sample_ids": [r["id"] for r in review],
    }
    (outdir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# M1 影子核心回放结果", "",
        "- 模式：`%s`（%s）" % (mode, provider),
        "- 范围：%s（%d 条）" % (scope_rule, scope_size),
        "- 覆盖率：**%.1f%%**（%d/%d），错误 %d" % (
            summary["coverage"] * 100, len(results), scope_size, len(errors)),
        "- 总耗时：%.1fs；模型延迟 p50=%sms p95=%sms（min=%s max=%s）" % (
            summary["elapsed_sec"], summary["latency_ms"]["p50"],
            summary["latency_ms"]["p95"], summary["latency_ms"]["min"],
            summary["latency_ms"]["max"]),
        "- 成本：%s；token 统计 %s" % (cost_note, dict(usage) or "未提供"),
        "", "## 动作分布", "",
        "| 动作 | 数量 |", "|---|---|",
    ]
    for k, v in sorted(actions.items(), key=lambda x: -x[1]):
        lines.append("| `%s` | %d |" % (k, v))
    lines += ["", "## 差异分类（影子 vs Golden 期望）", "",
              "| 分类 | 数量 | 含义 |", "|---|---|---|",
              "| `match_expected` | %d | 动作/工具落在允许集合且未触禁 |" % diffs.get("match_expected", 0),
              "| `action_mismatch` | %d | 动作不在允许集合 |" % diffs.get("action_mismatch", 0),
              "| `tool_mismatch` | %d | 调用了期望集合外的工具 |" % diffs.get("tool_mismatch", 0),
              "| `forbidden_hit` | %d | 命中禁止动作/工具（硬失败） |" % diffs.get("forbidden_hit", 0),
              "| `invalid` | %d | 模型输出无法解析 |" % diffs.get("invalid", 0),
              "| `error` | %d | 模型调用失败 |" % diffs.get("error", 0),
              "", "## 分类别", "", "| 类别 | 条数 | 差异分布 |", "|---|---|---|"]
    for c in sorted(by_cat):
        lines.append("| %s | %d | %s |" % (
            c, cats[c], ", ".join("%s=%d" % (k, v) for k, v in sorted(by_cat[c].items()))))
    if mode == "fake":
        lines += ["", "> 假模型一律返回 silent；本表差异分类只证明管线覆盖，不能用于评价决策质量。"]
    if review:
        lines += ["", "## 抽样复核（前 20 条非 match_expected）", "",
                  "| ID | 类别 | 期望 | 实际 | 差异 | 模型理由 |", "|---|---|---|---|---|---|"]
        for r in review:
            d = r["decision"]
            lines.append("| %s | %s | %s | %s%s | `%s` | %s |" % (
                r["id"], r["category"], "/".join(r["expected_actions"]),
                d["action"], ("/" + d["tool"]) if d.get("tool") else "",
                r["diff"], str(d.get("reason") or "")[:70].replace("|", "/")))
    (outdir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--limit", type=int, default=72)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", default=None)
    ap.add_argument("--reanalyze", action="store_true",
                    help="不调模型，只按当前分类规则重算已有 result.jsonl")
    args = ap.parse_args(argv)

    data = json.loads(DATA.read_text(encoding="utf-8"))
    samples = data["samples"]
    by_id = {s["id"]: s for s in samples}
    if args.reanalyze:
        outdir = Path(args.out) if args.out else (OUT / ("fake" if args.fake else "local"))
        results = [json.loads(x) for x in
                   (outdir / "result.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        for r in results:
            s = by_id.get(r["id"])
            d = SimpleNamespace(**r["decision"])
            r["diff"] = decision_diff(s, d)
            r["eval_decision"] = to_eval_decision(r["id"], d)
        old = json.loads((outdir / "summary.json").read_text(encoding="utf-8"))
        summary = write_outputs(results, old.get("scope") or len(results),
                                old.get("mode") or "reanalyzed",
                                old.get("provider") or "?",
                                old.get("elapsed_sec") or 0,
                                old.get("scope_rule") or "已回放样本",
                                old.get("cost_note") or "未记录", outdir)
        print(json.dumps({k: summary[k] for k in (
            "mode", "scope", "coverage", "errors", "actions", "diffs")},
            ensure_ascii=False, indent=2))
        print("out:", outdir)
        return 0

    scope = samples if args.fake else pick_samples(samples, max(1, args.limit))
    tools = bot._shadow_tools()
    decide = fake_decide if args.fake else bot._shadow_model
    core = C.Core(decide, C.Policy())

    outdir = Path(args.out) if args.out else (OUT / ("fake" if args.fake else "local"))
    outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    results = []
    if args.fake or args.workers <= 1:
        for i, s in enumerate(scope, 1):
            results.append(run_one(s, core, tools))
            if i % 20 == 0:
                print("  ... %d/%d" % (i, len(scope)), flush=True)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(run_one, s, core, tools) for s in scope]
            for i, f in enumerate(futs, 1):
                results.append(f.result())
                if i % 10 == 0:
                    print("  ... %d/%d" % (i, len(scope)), flush=True)
    elapsed = time.time() - t0

    summary = write_outputs(
        results, len(scope),
        "fake" if args.fake else "local_ollama",
        "fake" if args.fake else bot.provider_cfg("ollama_fast").get("model"),
        elapsed,
        "全部 Golden Set" if args.fake else "按 category 分层抽样 %d 条" % len(scope),
        "假模型，零费用" if args.fake else "本地 Ollama，零 API 费用",
        outdir)
    print(json.dumps({k: summary[k] for k in (
        "mode", "scope", "coverage", "errors", "elapsed_sec", "latency_ms",
        "actions", "diffs", "usage")}, ensure_ascii=False, indent=2))
    print("out:", outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
