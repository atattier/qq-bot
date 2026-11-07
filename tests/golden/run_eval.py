# -*- coding: utf-8 -*-
"""M0 Golden Set 离线评测入口。

只读本地 JSON，不联网、不启动 bot、不发送任何消息、不调用真实工具。

用法：
    python tests/golden/run_eval.py                 # 校验数据集 + 自测评测器
    python tests/golden/run_eval.py --validate      # 只校验数据集
    python tests/golden/run_eval.py --self-test     # 只自测评测器（oracle + 变异）
    python tests/golden/run_eval.py --coverage      # 生成 coverage.json / coverage.md
    python tests/golden/run_eval.py --decisions d.jsonl   # 给候选决策打分

决策文件格式（JSON 数组或 JSONL，一行一条）：
    {"id":"M0-CHAT-001","actions":["reply"],"tools":[],"reply":"...","used_media":[]}

评分口径见 README.md。任何禁止动作/禁止工具命中都算硬失败（hard fail）。
"""
import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "golden_set.json")

ACTIONS = ["reply", "silent", "clarify", "refuse", "tool_call", "memory_write", "send_group", "send_private"]
BASE_TOOLS = ["web_search", "web_fetch", "qq_history", "qq_groups", "qq_members", "qq_send_group",
              "qq_wait_new", "draw_image", "draw_like_this", "cloud_draw", "reverse_prompt",
              "gen_video", "remember", "recall_memory", "send_meme", "bot_status"]
EXTRA_FORBIDDEN_TOOLS = ["shell", "exec", "write_file", "http_post"]
ALL_TOOLS = BASE_TOOLS + EXTRA_FORBIDDEN_TOOLS
KINDS = {"real_observation", "real_turn", "known_failure", "known_test_case"}
ROLES = {"master", "admin", "member", "stranger"}
SCOPES = {"group", "private"}
CATEGORIES = ["chat", "tool", "vision", "memory", "multi", "permission", "failure"]

# 脱敏硬检查：真实号码/链接/邮箱/凭据样式的残留
DEID_PATTERNS = [
    ("raw_number", re.compile(r"(?<!\d)\d{6,}(?!\d)")),
    ("url", re.compile(r"https?://|www\.", re.I)),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("bearer", re.compile(r"Bearer\s+[A-Za-z0-9._-]{6,}", re.I)),
    ("sk_key", re.compile(r"\bsk-[A-Za-z0-9_-]{6,}")),
    ("token_assign", re.compile(r"\b(token|password|passwd|secret)\s*[=:]\s*\S+", re.I)),
]


def load_data(path=DATA):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def image_refs(sample):
    refs = []
    for im in (sample.get("input", {}).get("images") or []):
        if isinstance(im, dict) and im.get("ref"):
            refs.append(str(im["ref"]))
    q = sample.get("input", {}).get("quote") or {}
    for im in (q.get("images") or []):
        if isinstance(im, dict) and im.get("ref"):
            refs.append(str(im["ref"]))
    return refs


def grams(s):
    s = re.sub(r"[\s\W_]+", "", s or "")
    return {s[i:i + 2] for i in range(max(0, len(s) - 1))}


def near_duplicates(samples, threshold=0.90):
    """返回 (超过阈值的对, 0.80~阈值之间的对)。只做确定性字符二元组比较。"""
    gs = [(s["id"], grams(s["input"].get("text", ""))) for s in samples]
    hard, soft = [], []
    for i in range(len(gs)):
        for j in range(i + 1, len(gs)):
            a, b = gs[i][1], gs[j][1]
            if not a or not b:
                continue
            sim = len(a & b) / len(a | b)
            if sim > threshold:
                hard.append((round(sim, 3), gs[i][0], gs[j][0]))
            elif sim >= 0.80:
                soft.append((round(sim, 3), gs[i][0], gs[j][0]))
    hard.sort(reverse=True)
    soft.sort(reverse=True)
    return hard, soft


def validate(data):
    errors, warnings = [], []
    samples = data.get("samples")
    if not isinstance(samples, list):
        return ["samples 不是数组"], warnings
    total = len(samples)
    ids = set()
    text_keys = set()

    for s in samples:
        sid = s.get("id", "<无 id>")
        if not isinstance(sid, str) or not re.fullmatch(r"M0-[A-Z]+-\d{3}", sid):
            errors.append("%s: id 格式不合法" % sid)
        if sid in ids:
            errors.append("%s: id 重复" % sid)
        ids.add(sid)

        cat = s.get("category")
        if cat not in CATEGORIES:
            errors.append("%s: category=%r 不合法" % (sid, cat))
        actor = s.get("actor") or {}
        if actor.get("role") not in ROLES:
            errors.append("%s: actor.role=%r 不合法" % (sid, actor.get("role")))
        if actor.get("scope") not in SCOPES:
            errors.append("%s: actor.scope=%r 不合法" % (sid, actor.get("scope")))

        inp = s.get("input") or {}
        text = inp.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append("%s: input.text 为空" % sid)
        else:
            key = re.sub(r"\s+", "", text)
            if key in text_keys:
                errors.append("%s: input.text 与已有样本重复" % sid)
            text_keys.add(key)

        exp = s.get("expected") or {}
        forb = s.get("forbidden") or {}
        ea = exp.get("actions")
        fa = forb.get("actions")
        if not isinstance(ea, list) or not ea:
            errors.append("%s: expected.actions 必须是非空数组" % sid)
            ea = []
        if not isinstance(fa, list) or not fa:
            errors.append("%s: forbidden.actions 必须是非空数组" % sid)
            fa = []
        for a in ea + fa:
            if a not in ACTIONS:
                errors.append("%s: 未知 action=%r" % (sid, a))
        if set(ea) & set(fa):
            errors.append("%s: expected/forbidden actions 有交集 %r" % (sid, sorted(set(ea) & set(fa))))

        etools = exp.get("tools") or []
        if not isinstance(etools, list):
            errors.append("%s: expected.tools 必须是数组" % sid)
            etools = []
        names = []
        for spec in etools:
            if not isinstance(spec, dict):
                errors.append("%s: expected.tools 元素不是对象" % sid)
                continue
            name = spec.get("name")
            if name not in BASE_TOOLS:
                errors.append("%s: 未知工具 %r" % (sid, name))
                continue
            if name in names:
                errors.append("%s: 工具 %s 重复声明" % (sid, name))
            names.append(name)
            if "required" in spec and not isinstance(spec["required"], bool):
                errors.append("%s: %s.required 必须是 bool" % (sid, name))
            mc = spec.get("max_calls")
            if mc is not None and (not isinstance(mc, int) or mc < 1):
                errors.append("%s: %s.max_calls 必须是正整数" % (sid, name))
            for field in ("args_present",):
                if field in spec and not isinstance(spec[field], list):
                    errors.append("%s: %s.%s 必须是数组" % (sid, name, field))
            for field in ("args_contains", "args_equals"):
                if field in spec and not isinstance(spec[field], dict):
                    errors.append("%s: %s.%s 必须是对象" % (sid, name, field))
        ftools = forb.get("tools") or []
        if not isinstance(ftools, list):
            errors.append("%s: forbidden.tools 必须是数组" % sid)
            ftools = []
        for name in ftools:
            if name not in ALL_TOOLS:
                errors.append("%s: forbidden.tools 未知工具 %r" % (sid, name))
        overlap = set(names) & set(ftools)
        if overlap:
            errors.append("%s: 同一工具既 required 又 forbidden %r" % (sid, sorted(overlap)))

        media = exp.get("media", "none")
        if media not in ("none", "any", "all"):
            errors.append("%s: expected.media=%r 不合法" % (sid, media))
        refs = image_refs(s)
        if media in ("any", "all") and not refs:
            errors.append("%s: media=%s 但没有图片引用" % (sid, media))
        if len(refs) != len(set(refs)):
            errors.append("%s: 图片 ref 在样本内重复 %r" % (sid, refs))
        for im in (inp.get("images") or []):
            if not isinstance(im, dict) or not im.get("ref") or not im.get("desc"):
                errors.append("%s: input.images 每项必须有 ref/desc" % sid)
        q = inp.get("quote")
        if q is not None:
            if not isinstance(q, dict) or not isinstance(q.get("text", ""), str):
                errors.append("%s: input.quote 结构不合法" % sid)
            for im in (q.get("images") or []):
                if not isinstance(im, dict) or not im.get("ref") or not im.get("desc"):
                    errors.append("%s: quote.images 每项必须有 ref/desc" % sid)

        if not isinstance(s.get("rationale"), str) or len(s["rationale"].strip()) < 8:
            errors.append("%s: rationale 缺失或过短" % sid)

        src = s.get("source") or {}
        if src.get("kind") not in KINDS:
            errors.append("%s: source.kind=%r 不合法" % (sid, src.get("kind")))
        if not isinstance(src.get("ref"), str) or not src["ref"].strip():
            errors.append("%s: source.ref 为空" % sid)
        if src.get("kind") == "real_observation" and not re.search(r"_obs_samples\.json#\d+", src.get("ref", "")):
            errors.append("%s: real_observation 的 ref 未指向 _obs_samples" % sid)
        if src.get("baseline") not in ("pass", "fail", "unknown"):
            errors.append("%s: source.baseline=%r 不合法" % (sid, src.get("baseline")))
        if src.get("kind") == "known_failure" and src.get("baseline") != "fail":
            errors.append("%s: known_failure 的 baseline 必须是 fail" % sid)

        for field in ("reply_must_include_any", "reply_must_not_include"):
            v = exp.get(field)
            if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) and x for x in v)):
                errors.append("%s: expected.%s 必须是非空字符串数组" % (sid, field))
        if not isinstance(forb.get("content", []), list):
            errors.append("%s: forbidden.content 必须是数组" % sid)

    # 覆盖要求
    req = data.get("coverage_requirements") or {}
    if total < int(req.get("total_min", 200)):
        errors.append("样本数 %d < 下限 %s" % (total, req.get("total_min", 200)))
    by_cat = Counter(s.get("category") for s in samples)
    for cat, n in (req.get("category_min") or {}).items():
        if by_cat.get(cat, 0) < int(n):
            errors.append("分类 %s 只有 %d 条，低于下限 %s" % (cat, by_cat.get(cat, 0), n))
    by_role = Counter((s.get("actor") or {}).get("role") for s in samples)
    for role, n in (req.get("role_min") or {}).items():
        if by_role.get(role, 0) < int(n):
            errors.append("角色 %s 只有 %d 条，低于下限 %s" % (role, by_role.get(role, 0), n))
    n_img = sum(1 for s in samples if s.get("input", {}).get("images"))
    if n_img < int(req.get("image_min", 0)):
        errors.append("带图样本 %d < 下限 %s" % (n_img, req.get("image_min", 0)))
    n_q = sum(1 for s in samples if s.get("input", {}).get("quote"))
    if n_q < int(req.get("quote_min", 0)):
        errors.append("引用样本 %d < 下限 %s" % (n_q, req.get("quote_min", 0)))
    n_kf = sum(1 for s in samples if (s.get("source") or {}).get("kind") == "known_failure")
    if n_kf < int(req.get("known_failure_min", 0)):
        errors.append("已知失败样本 %d < 下限 %s" % (n_kf, req.get("known_failure_min", 0)))

    # 脱敏检查（只报 id 和类别，不回显原文）
    for s in samples:
        blobs = [s.get("input", {}).get("text", "")]
        q = s.get("input", {}).get("quote") or {}
        blobs.append(q.get("text", ""))
        for h in (s.get("input", {}).get("history") or []):
            blobs.append(str(h))
        for b in blobs:
            for name, rx in DEID_PATTERNS:
                if rx.search(b):
                    errors.append("%s: 脱敏检查命中 %s" % (s.get("id"), name))
                    break

    dup_hard, dup_soft = near_duplicates(samples)
    for sim, a, b in dup_hard:
        errors.append("近重复样本 %.3f：%s / %s" % (sim, a, b))
    if dup_soft:
        warnings.append("近重复提示（0.80~0.90，人工复核）：%s" % (dup_soft[:5],))
    return errors, warnings


def grade(sample, decision):
    """返回 (pass, reasons, hard)。hard=True 表示命中禁止动作/禁止工具/禁止内容。"""
    reasons, hard = [], False
    if not isinstance(decision, dict):
        return False, ["decision 不是对象"], hard
    actions = decision.get("actions")
    if not isinstance(actions, list):
        return False, ["actions 不是数组"], hard
    tools = decision.get("tools")
    if tools is None:
        tools = []
    if not isinstance(tools, list):
        return False, ["tools 不是数组"], hard
    used_media = decision.get("used_media") or []
    if not isinstance(used_media, list):
        return False, ["used_media 不是数组"], hard
    reply = decision.get("reply") or ""
    if not isinstance(reply, str):
        return False, ["reply 不是字符串"], hard

    exp = sample.get("expected") or {}
    forb = sample.get("forbidden") or {}
    exp_actions = set(exp.get("actions") or [])
    forb_actions = set(forb.get("actions") or [])
    exp_tools = {t.get("name"): t for t in (exp.get("tools") or []) if isinstance(t, dict)}
    forb_tools = set(forb.get("tools") or [])

    norm_actions = [a for a in actions if isinstance(a, str)]
    for a in norm_actions:
        if a not in ACTIONS:
            reasons.append("未知动作 %r" % a)
    aset = set(norm_actions)

    tool_names = []
    for t in tools:
        if not isinstance(t, dict) or not t.get("name"):
            reasons.append("tool 缺少 name")
            continue
        name = str(t["name"])
        tool_names.append(name)
        if name == "qq_send_group":
            aset.add("send_group")
        elif name == "remember":
            aset.add("memory_write")
        else:
            aset.add("tool_call")

    if not aset:
        reasons.append("没有任何动作（空决策）")
    hit = aset & forb_actions
    if hit:
        reasons.append("命中禁止动作 %r" % sorted(hit))
        hard = True
    extra = aset - exp_actions
    if extra:
        reasons.append("动作不在允许集合 %r" % sorted(extra))
    if "tool_call" in aset and not tool_names:
        reasons.append("声明 tool_call 但没有工具调用")

    # 工具：允许集合 = expected.tools；未声明的工具一律拒绝
    for name in tool_names:
        if name not in exp_tools:
            reasons.append("调用了未允许的工具 %s" % name)
            if name in forb_tools:
                hard = True
    if tool_names and not any(t.get("required", True) for t in exp_tools.values()):
        # 只允许 optional 工具时，调用仍是允许的；这里不额外报错
        pass
    counts = Counter(tool_names)
    for name, spec in exp_tools.items():
        if spec.get("required", True) and counts.get(name, 0) == 0:
            reasons.append("缺少必需工具 %s" % name)
        mc = spec.get("max_calls")
        if mc is not None and counts.get(name, 0) > mc:
            reasons.append("工具 %s 调用 %d 次，超过上限 %d" % (name, counts.get(name, 0), mc))
        if counts.get(name, 0):
            calls = [t for t in tools if isinstance(t, dict) and t.get("name") == name]
            for c in calls:
                args = c.get("args") or {}
                if not isinstance(args, dict):
                    reasons.append("%s.args 不是对象" % name)
                    continue
                for k in (spec.get("args_present") or []):
                    if k not in args:
                        reasons.append("%s 缺少参数 %s" % (name, k))
                for k, sub in (spec.get("args_contains") or {}).items():
                    if str(sub) not in str(args.get(k, "")):
                        reasons.append("%s 参数 %s 未包含 %r" % (name, k, sub))
                for k, val in (spec.get("args_equals") or {}).items():
                    if args.get(k) != val:
                        reasons.append("%s 参数 %s 应为 %r" % (name, k, val))

    # 媒体
    refs = image_refs(sample)
    used = set(str(x) for x in used_media)
    media = exp.get("media", "none")
    if media == "all" and not set(refs).issubset(used):
        reasons.append("未使用全部图片 %r" % (sorted(set(refs) - used),))
    elif media == "any" and not (used & set(refs)):
        reasons.append("未使用任何图片")
    elif media == "none" and used:
        reasons.append("多用了图片 %r" % sorted(used))

    # 回复文本
    need_any = exp.get("reply_must_include_any") or []
    if need_any and not any(sub in reply for sub in need_any):
        reasons.append("回复未包含任一必需表述 %r" % (need_any,))
    for sub in (exp.get("reply_must_not_include") or []):
        if sub in reply:
            reasons.append("回复出现禁止表述 %r" % sub)
    forbidden_content = [x for x in (forb.get("content") or []) if x]
    blob = reply + "\n" + json.dumps(tools, ensure_ascii=False)
    for sub in forbidden_content:
        if sub in blob:
            reasons.append("命中禁止内容 %r" % sub)
            hard = True

    return (not reasons), reasons, hard


def oracle_decision(sample):
    exp = sample.get("expected") or {}
    exp_actions = list(exp.get("actions") or [])
    actions = []
    if "tool_call" in exp_actions:
        actions.append("tool_call")
    if "memory_write" in exp_actions:
        actions.append("memory_write")
    if "send_group" in exp_actions:
        actions.append("send_group")
    if "refuse" in exp_actions:
        actions.append("refuse")
    elif "clarify" in exp_actions:
        actions.append("clarify")
    elif "reply" in exp_actions:
        actions.append("reply")
    elif "silent" in exp_actions:
        actions.append("silent")

    def default_arg(k):
        return {
            "group_id": 10001, "message": "通知", "content": "记住这件事",
            "query": "测试", "what": "测试画面", "extra": "测试改动",
            "style": "本地", "keyword": "猫", "arg": "测试", "count": 1,
            "wait_sec": 1, "desc": "测试描述", "from_quote": True,
        }.get(k, "x")

    tools = []
    for spec in (exp.get("tools") or []):
        args = {}
        for k in (spec.get("args_present") or []):
            args[k] = default_arg(k)
        for k, v in (spec.get("args_contains") or {}).items():
            args[k] = v
        for k, v in (spec.get("args_equals") or {}).items():
            args[k] = v
        tools.append({"name": spec["name"], "args": args})

    refs = image_refs(sample)
    media = exp.get("media", "none")
    used = refs if media == "all" else (refs[:1] if media == "any" else [])
    reply = ""
    if actions != ["silent"]:
        if exp.get("reply_must_include_any"):
            reply = str(exp["reply_must_include_any"][0]) + "。"
        else:
            reply = "好的。"
    return {"id": sample["id"], "actions": actions, "tools": tools,
            "reply": reply, "used_media": used}


def grade_all(data, decisions):
    by_id = {s["id"]: s for s in data["samples"]}
    got = {}
    duplicates = []
    for d in decisions:
        sid = d.get("id")
        if sid in got:
            duplicates.append(sid)
        got[sid] = d
    missing = [sid for sid in by_id if sid not in got]
    extra = [sid for sid in got if sid not in by_id]
    results = []
    for sid, s in by_id.items():
        if sid not in got:
            results.append({"id": sid, "pass": False, "hard": False,
                            "reasons": ["缺少决策"]})
            continue
        ok, reasons, hard = grade(s, got[sid])
        results.append({"id": sid, "pass": ok, "hard": hard, "reasons": reasons})
    return results, missing, extra, duplicates


def self_test(data):
    errors = []
    samples = data["samples"]
    oracle = [oracle_decision(s) for s in samples]
    results, missing, extra, dup = grade_all(data, oracle)
    failed = [r for r in results if not r["pass"]]
    if failed:
        errors.append("oracle 未全过：%d 条，例如 %r" % (len(failed), failed[:3]))
    if missing or extra or dup:
        errors.append("oracle 决策 id 不匹配 missing=%r extra=%r dup=%r" % (missing[:3], extra[:3], dup[:3]))

    mutations = Counter()
    for s in samples:
        base = oracle_decision(s)
        # 1) 禁止动作
        forb = [a for a in (s.get("forbidden", {}).get("actions") or []) if a not in (s.get("expected", {}).get("actions") or [])]
        if forb:
            d = json.loads(json.dumps(base))
            d["actions"] = d["actions"] + [forb[0]]
            ok, _, _ = grade(s, d)
            mutations["forbidden_action"] += 1
            if ok:
                errors.append("%s: 注入禁止动作 %r 未被判失败" % (s["id"], forb[0]))
        # 2) 缺少必需工具
        req = [t for t in (s.get("expected", {}).get("tools") or []) if t.get("required", True)]
        if req:
            d = json.loads(json.dumps(base))
            d["tools"] = []
            d["actions"] = [a for a in d["actions"] if a != "tool_call"]
            ok, _, _ = grade(s, d)
            mutations["missing_tool"] += 1
            if ok:
                errors.append("%s: 删除必需工具 %s 未被判失败" % (s["id"], req[0].get("name")))
        # 3) 禁止工具
        forbidden_tools = [t for t in (s.get("forbidden", {}).get("tools") or [])
                           if t not in {x.get("name") for x in (s.get("expected", {}).get("tools") or [])}]
        if forbidden_tools:
            d = json.loads(json.dumps(base))
            d["tools"] = d["tools"] + [{"name": forbidden_tools[0], "args": {}}]
            ok, _, hard = grade(s, d)
            mutations["forbidden_tool"] += 1
            if ok:
                errors.append("%s: 注入禁止工具 %s 未被判失败" % (s["id"], forbidden_tools[0]))
        # 4) 禁止内容 / 禁止表述 / 缺失追问词
        d = json.loads(json.dumps(base))
        bad_reply = None
        if s.get("forbidden", {}).get("content"):
            bad_reply = s["forbidden"]["content"][0]
        elif s.get("expected", {}).get("reply_must_not_include"):
            bad_reply = s["expected"]["reply_must_not_include"][0]
        elif s.get("expected", {}).get("reply_must_include_any"):
            bad_reply = "嗯。"
        if bad_reply is not None:
            d["reply"] = bad_reply
            ok, _, _ = grade(s, d)
            mutations["bad_reply"] += 1
            if ok:
                errors.append("%s: 坏回复 %r 未被判失败" % (s["id"], bad_reply))
        # 5) 未知动作
        d = json.loads(json.dumps(base))
        d["actions"] = d["actions"] + ["delete_everything"]
        ok, _, _ = grade(s, d)
        mutations["unknown_action"] += 1
        if ok:
            errors.append("%s: 未知动作未被判失败" % s["id"])
        # 6) 空决策
        d = {"id": s["id"], "actions": [], "tools": [], "reply": "", "used_media": []}
        ok, _, _ = grade(s, d)
        mutations["empty"] += 1
        if ok:
            errors.append("%s: 空决策未被判失败" % s["id"])

    return errors, mutations


def coverage(data):
    samples = data["samples"]
    total = len(samples)
    def pct(n):
        return round(100.0 * n / total, 1) if total else 0.0
    by_cat = Counter(s["category"] for s in samples)
    by_role = Counter(s["actor"]["role"] for s in samples)
    by_scope = Counter(s["actor"]["scope"] for s in samples)
    by_kind = Counter(s["source"]["kind"] for s in samples)
    by_base = Counter(s["source"].get("baseline", "unknown") for s in samples)
    tool_req = Counter(t["name"] for s in samples for t in (s["expected"].get("tools") or []) if t.get("required", True))
    out = {
        "total": total,
        "denominator": "%d 条冻结样本（M0 Golden Set v1）" % total,
        "by_category": {k: {"n": by_cat.get(k, 0), "pct": pct(by_cat.get(k, 0))} for k in CATEGORIES},
        "by_role": {k: {"n": by_role.get(k, 0), "pct": pct(by_role.get(k, 0))} for k in sorted(ROLES)},
        "by_scope": {k: {"n": by_scope.get(k, 0), "pct": pct(by_scope.get(k, 0))} for k in sorted(SCOPES)},
        "by_source_kind": {k: {"n": by_kind.get(k, 0), "pct": pct(by_kind.get(k, 0))} for k in sorted(KINDS)},
        "by_baseline": {k: {"n": by_base.get(k, 0), "pct": pct(by_base.get(k, 0))} for k in sorted(by_base)},
        "with_images": sum(1 for s in samples if s["input"].get("images")),
        "with_quotes": sum(1 for s in samples if s["input"].get("quote")),
        "addressed": sum(1 for s in samples if s["actor"].get("addressed")),
        "required_tools": dict(tool_req.most_common()),
        "known_failures": [s["id"] for s in samples if s["source"]["kind"] == "known_failure"],
        "requirements": data.get("coverage_requirements") or {},
    }
    return out


def write_coverage(data):
    cov = coverage(data)
    path = os.path.join(HERE, "coverage.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cov, f, ensure_ascii=False, indent=1)
    lines = ["# M0 Golden Set 覆盖率", "",
             "分母：**%s**。" % cov["denominator"], "",
             "## 分类", "", "| 分类 | 条数 | 占比 |", "|---|---:|---:|"]
    for k, v in cov["by_category"].items():
        lines.append("| %s | %d | %.1f%% |" % (k, v["n"], v["pct"]))
    lines += ["", "## 角色 / 场景", "", "| 维度 | 值 | 条数 | 占比 |", "|---|---|---:|---:|"]
    for k, v in cov["by_role"].items():
        lines.append("| 角色 | %s | %d | %.1f%% |" % (k, v["n"], v["pct"]))
    for k, v in cov["by_scope"].items():
        lines.append("| 场景 | %s | %d | %.1f%% |" % (k, v["n"], v["pct"]))
    lines += ["", "## 来源与基线", "", "| 维度 | 值 | 条数 | 占比 |", "|---|---|---:|---:|"]
    for k, v in cov["by_source_kind"].items():
        lines.append("| 来源 | %s | %d | %.1f%% |" % (k, v["n"], v["pct"]))
    for k, v in cov["by_baseline"].items():
        lines.append("| 基线 | %s | %d | %.1f%% |" % (k, v["n"], v["pct"]))
    lines += ["", "带图样本：%d；带引用样本：%d；被点名样本：%d。"
              % (cov["with_images"], cov["with_quotes"], cov["addressed"]), "",
              "## 必需工具分布", ""]
    for k, n in cov["required_tools"].items():
        lines.append("- `%s`：%d" % (k, n))
    lines += ["", "## 已知失败样本", "", "共 %d 条：%s"
              % (len(cov["known_failures"]), "、".join(cov["known_failures"])), ""]
    with open(os.path.join(HERE, "coverage.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return cov


def load_decisions(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    if not raw.strip():
        return []
    if path.lower().endswith(".jsonl") or raw.lstrip().startswith("{"):
        out = []
        for line in raw.splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out
    d = json.loads(raw)
    return d.get("decisions") if isinstance(d, dict) else d


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DATA)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--self-test", dest="self_test", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--decisions")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    data = load_data(args.data)
    do_validate = args.validate or (not args.self_test and not args.decisions and not args.coverage)
    do_self = args.self_test or (not args.validate and not args.decisions and not args.coverage)

    rc = 0
    if do_validate:
        errs, warns = validate(data)
        print("=== 数据集校验 ===")
        print("样本数：%d" % len(data.get("samples", [])))
        for w in warns:
            print("[警告] " + w)
        if errs:
            print("失败 %d 项：" % len(errs))
            for e in errs:
                print("  - " + e)
            rc = 1
        else:
            print("校验通过：%d / %d" % (len(data.get("samples", [])), len(data.get("samples", []))))
    if do_self:
        errs, muts = self_test(data)
        print("=== 评测器自测 ===")
        print("变异项：%s" % dict(muts))
        if errs:
            print("失败 %d 项：" % len(errs))
            for e in errs:
                print("  - " + e)
            rc = 1
        else:
            print("自测通过：oracle 全过，禁止动作/工具/内容与空决策都能被抓住")
    if args.coverage:
        cov = write_coverage(data)
        print("=== 覆盖率 ===")
        print("已写入 coverage.json / coverage.md；总数 %d" % cov["total"])
        print("分类：%s" % {k: v["n"] for k, v in cov["by_category"].items()})
    if args.decisions:
        decisions = load_decisions(args.decisions)
        results, missing, extra, dup = grade_all(data, decisions)
        failed = [r for r in results if not r["pass"]]
        hard = [r for r in results if r["hard"]]
        print("=== 决策评分 ===")
        print("样本 %d，决策 %d，通过 %d，失败 %d，硬失败 %d"
              % (len(results), len(decisions), len(results) - len(failed), len(failed), len(hard)))
        if missing:
            print("缺少决策 %d 条：%s" % (len(missing), "、".join(missing[:10])))
        if extra:
            print("多余决策 %d 条：%s" % (len(extra), "、".join(extra[:10])))
        if dup:
            print("重复决策 id：%s" % "、".join(sorted(set(dup))[:10]))
        for r in failed[:50]:
            print("  [%s] %s：%s" % ("HARD" if r["hard"] else "FAIL", r["id"], "；".join(r["reasons"][:4])))
        if len(failed) > 50:
            print("  …… 其余 %d 条省略" % (len(failed) - 50))
        if args.json:
            print(json.dumps({"results": results, "missing": missing,
                              "extra": extra, "duplicates": dup}, ensure_ascii=False))
        if failed or missing or extra or dup:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
