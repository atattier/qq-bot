# -*- coding: utf-8 -*-
"""M1/M2 Agent Core（影子模式）与 Policy。

核心只做判断：reply / silent / clarify / refuse / tool_call。
影子模式没有 Registry 执行器：tool_call 只记录“会调用什么”，绝不执行。
Policy 只做安全/权限/静音的否决，不选择业务动作（ADR-001 §5）。
M2 起 Core 可接 Context Assembler；组装失败自动回退 M1 最小上下文。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping

from qbot.qbot_turn import ACTIONS, Turn

FORBIDDEN_TOOLS = frozenset({"shell", "exec", "write_file", "http_post"})
MASTER_ONLY_TOOLS = frozenset({"qq_send_group", "remember"})
_ACTION_ALIASES = {
    "ask": "clarify", "question": "clarify", "clarify": "clarify",
    "reply": "reply", "text": "reply", "answer": "reply",
    "silent": "silent", "ignore": "silent", "no_reply": "silent",
    "refuse": "refuse", "reject": "refuse", "deny": "refuse",
    "tool": "tool_call", "tool_call": "tool_call", "call_tool": "tool_call",
}
_JSON_RE = re.compile(r"\{.*\}", re.S)


@dataclass
class Decision:
    action: str
    reply: str = ""
    tool: str | None = None
    args: dict = field(default_factory=dict)
    reason: str = ""
    confidence: float | None = None
    raw: str = ""
    steps: list = field(default_factory=list)
    policy_notes: list = field(default_factory=list)
    model: str = ""
    latency_ms: int = 0
    prompt_chars: int = 0
    response_chars: int = 0
    usage: dict = field(default_factory=dict)
    error: str = ""
    tool_executed: bool = False
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action": self.action, "reply": self.reply, "tool": self.tool,
            "args": self.args, "reason": self.reason, "confidence": self.confidence,
            "raw": self.raw[:4000], "steps": self.steps,
            "policy_notes": self.policy_notes, "model": self.model,
            "latency_ms": self.latency_ms, "prompt_chars": self.prompt_chars,
            "response_chars": self.response_chars, "usage": self.usage,
            "error": self.error, "tool_executed": bool(self.tool_executed),
            "context": self.context,
        }


def _strip_json(text: str) -> str:
    s = str(text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s)
    m = _JSON_RE.search(s)
    return m.group(0) if m else s


def parse_decision(text: str) -> Decision:
    """模型输出 → Decision。坏 JSON 不抛异常，返回 error 决策交给上层分类。"""
    raw = str(text or "")
    try:
        d = json.loads(_strip_json(raw))
        if not isinstance(d, dict):
            raise ValueError("顶层不是对象")
    except Exception as e:  # noqa: BLE001
        return Decision(action="invalid", raw=raw, error="JSON 解析失败: %s" % e)
    action = str(d.get("action") or "").strip().lower()
    action = _ACTION_ALIASES.get(action, action)
    if action not in ACTIONS:
        return Decision(action="invalid", raw=raw,
                        error="未知动作 %r" % (d.get("action"),))
    reply = d.get("reply")
    reply = "" if reply is None else str(reply)
    tool = d.get("tool")
    tool = str(tool).strip() if tool not in (None, "") else None
    args = d.get("args") if isinstance(d.get("args"), dict) else {}
    try:
        conf = float(d.get("confidence")) if d.get("confidence") is not None else None
    except (TypeError, ValueError):
        conf = None
    return Decision(action=action, reply=reply, tool=tool, args=args,
                    reason=str(d.get("reason") or ""), confidence=conf, raw=raw)


SYSTEM_CONTRACT = (
    "你是 QQ 群聊机器人的统一智能核心，现在只做影子判断，绝不发送消息、绝不执行工具。\n"
    "根据这一轮输入决定唯一动作：reply / silent / clarify / refuse / tool_call。\n"
    "规则：\n"
    "1. 群聊里没被点名、也不是在接 bot 的话，可以 silent；被明确提问才 reply。\n"
    "2. 需要画图/看图/搜索/记忆/发群等能力时，用 tool_call，并在 tool 写工具名；影子模式只记录，不执行。\n"
    "3. 权限不足、要求密钥/隐私/不可逆操作时 refuse；缺关键信息时 clarify。\n"
    "4. 只输出一个 JSON 对象，不要 Markdown，不要解释。格式：\n"
    '{"action":"reply|silent|clarify|refuse|tool_call","tool":null,"args":{},'
    '"reply":"用户可见文本，silent 时为空","reason":"一句判断依据","confidence":0.0}'
)


def tool_docs(tools: Iterable[Mapping[str, Any]]) -> str:
    lines = []
    for t in tools or ():
        name = str(t.get("name") or "")
        if not name:
            continue
        desc = str(t.get("description") or "").replace("\n", " ")[:90]
        perm = "仅主人" if t.get("master_only") else "当前会话可用"
        lines.append("- %s（%s）：%s" % (name, perm, desc))
    return "\n".join(lines) or "（工具目录为空）"


def build_messages(turn: Turn, history: Iterable[Mapping[str, Any]] = (),
                   tools: Iterable[Mapping[str, Any]] = (), max_history: int = 6) -> list[dict]:
    """M1 的最小上下文。M2 由 Context Assembler 替换；这里是 legacy 回退。"""
    sys_text = SYSTEM_CONTRACT + "\n工具目录：\n" + tool_docs(tools)
    lines = []
    for h in list(history or ())[-max_history:]:
        if not isinstance(h, Mapping):
            continue
        role = str(h.get("role") or h.get("who") or "")
        content = str(h.get("content") or h.get("text") or "")[:300]
        if content:
            lines.append("%s: %s" % (role or "?", content))
    user_text = (
        "会话：%s %s\n"
        "说话人：%s（%s）\n"
        "是否点名：%s\n"
        "文本：%s\n"
        "图片数：%d\n"
        "引用：%s\n"
        "最近消息：\n%s"
    ) % (
        turn.session.kind, turn.session.key, turn.actor.display_name,
        turn.actor.role, "是" if turn.addressed else "否", turn.text,
        len(turn.media), (turn.quote.text if turn.quote else "") or "无",
        "\n".join(lines) or "（无）",
    )
    return [{"role": "system", "content": sys_text},
            {"role": "user", "content": user_text}]


class Policy:
    """安全/权限/静音否决。只返回新 Decision，不改 Turn。"""

    def __init__(self, master_only: Iterable[str] = MASTER_ONLY_TOOLS,
                 forbidden: Iterable[str] = FORBIDDEN_TOOLS):
        self.master_only = frozenset(master_only)
        self.forbidden = frozenset(forbidden)

    def apply(self, turn: Turn, decision: Decision) -> Decision:
        notes = []
        d = decision
        if turn.flag("test_mute") or turn.flag("silent"):
            notes.append("policy:test_mute/silent")
            return replace(d, action="silent", reply="", tool=None, args={},
                           policy_notes=list(d.policy_notes) + notes)
        if d.action == "invalid":
            # 保留 invalid 供影子差异分类；不要在 Policy 里伪装成一次业务拒绝。
            notes.append("policy:invalid_decision")
            return replace(d, reply="", tool=None, args={},
                           policy_notes=list(d.policy_notes) + notes)
        if d.action == "tool_call":
            name = d.tool or ""
            if not name:
                notes.append("policy:tool_missing")
                return replace(d, action="refuse", reply="",
                               policy_notes=list(d.policy_notes) + notes)
            if name in self.forbidden:
                notes.append("policy:forbidden_tool:%s" % name)
                return replace(d, action="refuse", reply="", policy_notes=list(d.policy_notes) + notes)
            if name in self.master_only and not (turn.flag("privileged") or turn.actor.role == "owner"):
                notes.append("policy:master_only:%s" % name)
                return replace(d, action="refuse", reply="", policy_notes=list(d.policy_notes) + notes)
        if d.action == "silent" and d.reply.strip():
            notes.append("policy:silent_dropped_reply")
            d = replace(d, reply="")
        if d.action == "reply" and not d.reply.strip():
            notes.append("policy:empty_reply")
            d = replace(d, action="silent", reply="")
        return replace(d, policy_notes=list(d.policy_notes) + notes)


class Core:
    """一次 Turn 的判断循环。

    decide(messages) -> str | (str, meta)。影子模式 executor=None：
    tool_call 只记录，不执行，循环立即收尾。未来 live 模式可注入 Registry executor。
    """

    def __init__(self, decide: Callable[[list], Any], policy: Policy | None = None,
                 max_steps: int = 2, assembler=None):
        if not callable(decide):
            raise TypeError("decide 必须可调用")
        self.decide = decide
        self.policy = policy or Policy()
        self.max_steps = max(1, int(max_steps))
        self.assembler = assembler

    def run(self, turn: Turn, history: Iterable[Mapping[str, Any]] = (),
            tools: Iterable[Mapping[str, Any]] = (), executor=None,
            recent_turns: Iterable[Turn] = ()) -> Decision:
        mctx = None
        if self.assembler is not None:
            try:
                mctx = self.assembler.build(turn, history=history, tools=tools,
                                            recent_turns=recent_turns)
                messages = mctx.messages
            except Exception as e:  # noqa: BLE001
                # Assembler 出错不能拖垮影子：退回 M1 最小上下文，并留下 error 证据。
                mctx = None
                messages = build_messages(turn, history=history, tools=tools)
                if getattr(self.assembler, "logger", None):
                    self.assembler.logger.warning("Context Assembler 失败，回退 legacy: %s", e)
        else:
            messages = build_messages(turn, history=history, tools=tools)
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
        t0 = time.time()
        text, meta = "", {}
        try:
            out = self.decide(messages)
            if isinstance(out, tuple) and len(out) >= 2:
                text, meta = out[0], (out[1] or {})
            else:
                text = out
        except Exception as e:  # noqa: BLE001
            elapsed = int((time.time() - t0) * 1000)
            return Decision(action="error", error="模型调用失败: %s" % e,
                            model=str((meta or {}).get("backend") or ""),
                            latency_ms=elapsed, prompt_chars=prompt_chars,
                            context=(mctx.to_dict() if mctx else {}))
        elapsed = int((time.time() - t0) * 1000)
        meta = meta if isinstance(meta, dict) else {}
        d = parse_decision(text)
        if d.action == "invalid" and meta.get("error") and not str(text or "").strip():
            return Decision(action="error", error="模型调用失败: %s" % meta.get("error"),
                            model=str(meta.get("backend") or ""),
                            latency_ms=int(meta.get("latency_ms") or elapsed),
                            prompt_chars=prompt_chars,
                            context=(mctx.to_dict() if mctx else {}))
        d.model = str(meta.get("backend") or "")
        d.latency_ms = int(meta.get("latency_ms") or elapsed)
        d.prompt_chars = prompt_chars
        d.response_chars = len(str(text or ""))
        d.usage = meta.get("usage") or {}
        d.context = mctx.to_dict() if mctx else {}
        d.steps = [{"step": 1, "action": d.action, "tool": d.tool,
                    "shadow": executor is None}]
        if d.action == "tool_call" and executor is not None:
            # 预留给 M3 Registry；M1 影子永远走不到这里。
            try:
                result = executor(d.tool, d.args)
                d.steps.append({"step": 2, "tool_result": result})
                d.tool_executed = True
            except Exception as e:  # noqa: BLE001
                d.steps.append({"step": 2, "tool_error": str(e)})
        return self.policy.apply(turn, d)
