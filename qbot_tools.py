# -*- coding: utf-8 -*-
"""M3 Tool Registry：三套工具面（总控大脑 / MCP / HTTP 兼容层）的统一契约。

边界（ADR-001 §8.3 当前默认）：
  - Registry 只**描述与影子校验**，不执行工具、不发消息、不写业务状态；
  - 现有 handler 内的权限判断继续有效，Registry 不取代；
  - 默认关闭（bot 侧 `arch.tools=legacy`）；shadow 只写审计，不阻断 dispatch；
  - MCP 侧可用 `QQBOT_ARCH_TOOLS=registry` 让 tools/list 由 Registry 生成。

契约：
  - `ToolSpec`：名字/说明/参数 schema/scope/副作用/幂等/超时/约束/HTTP 映射；
  - `ToolResult`：ok/status/data/error/latency/replayed，状态
    `ok|error|denied|timeout|invalid|unsupported`；
  - 权限矩阵：public < admin < owner < system。system 只给本机桥接（MCP/HTTP）
    与内部执行器，聊天里的任何角色都不能冒充。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

SCHEMA_VERSION = 1
PUBLIC, ADMIN, OWNER, SYSTEM = "public", "admin", "owner", "system"
SCOPES = (PUBLIC, ADMIN, OWNER, SYSTEM)
RANK = {PUBLIC: 0, ADMIN: 1, OWNER: 2, SYSTEM: 3}
SIDE_EFFECTS = frozenset({"none", "read", "write", "external", "irreversible"})
RESULT_STATUSES = frozenset({"ok", "error", "denied", "timeout", "invalid",
                            "unsupported"})
SURFACES = ("omni", "mcp", "http")

BASE = Path(__file__).resolve().parent
SHADOW_DIR = BASE / "state" / "tools"
SHADOW_FILE = SHADOW_DIR / "shadow.jsonl"
_FS_LOCK = threading.Lock()


def bind_shadow_dir(path) -> None:
    """宿主注入审计目录（测试态由 bot 指到临时目录）。"""
    global SHADOW_DIR, SHADOW_FILE
    SHADOW_DIR = Path(path)
    SHADOW_FILE = SHADOW_DIR / "shadow.jsonl"


# ---------------------------------------------------------------- Principal / Spec

@dataclass(frozen=True)
class Principal:
    """一次调用的身份。聊天身份与 system（桥接/本机执行器）严格分开。"""
    source: str = "chat"          # chat | system
    role: str = "member"          # member | stranger | admin | owner
    is_master: bool = False
    is_privileged: bool = False
    is_group: bool = True

    @classmethod
    def chat(cls, role: str = "member", *, is_master: bool = False,
             is_privileged: bool = False, is_group: bool = True) -> "Principal":
        return cls("chat", str(role or "member"), bool(is_master),
                   bool(is_privileged), bool(is_group))

    @classmethod
    def system(cls, source: str = "system") -> "Principal":
        return cls(str(source or "system"), "system", True, True, True)

    @property
    def rank(self) -> int:
        if self.source != "chat":
            return RANK[SYSTEM]
        if self.is_master:
            return RANK[OWNER]
        if self.is_privileged:
            return RANK[ADMIN]
        return RANK[PUBLIC]

    def allows(self, scope: str) -> bool:
        if scope == SYSTEM:
            return self.source != "chat"
        if scope == OWNER:
            return self.source != "chat" or self.is_master
        if scope == ADMIN:
            return self.source != "chat" or self.is_privileged or self.is_master
        return True

    def to_dict(self) -> dict:
        return {"source": self.source, "role": self.role,
                "is_master": bool(self.is_master),
                "is_privileged": bool(self.is_privileged),
                "is_group": bool(self.is_group), "rank": self.rank}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    scope: str = PUBLIC
    surface: str = "omni"
    side_effect: str = "none"
    idempotent: bool = True
    timeout_sec: float = 30.0
    group_only: bool = False
    private_only: bool = False
    constraints: tuple = ()
    aliases: tuple = ()
    http: tuple = ()              # ((method, path), ...)
    source: str = ""

    def __post_init__(self):
        if self.scope not in SCOPES:
            raise ValueError("未知 scope: %r" % (self.scope,))
        if self.side_effect not in SIDE_EFFECTS:
            raise ValueError("未知 side_effect: %r" % (self.side_effect,))
        if self.surface not in SURFACES:
            raise ValueError("未知 surface: %r" % (self.surface,))

    def to_openai(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": dict(self.input_schema or {"type": "object",
                                                     "properties": {}})}}

    def to_mcp(self) -> dict:
        return {"name": self.name, "description": self.description,
                "inputSchema": dict(self.input_schema or {"type": "object",
                                                          "properties": {},
                                                          "required": []})}

    def to_dict(self) -> dict:
        return {"name": self.name, "scope": self.scope, "surface": self.surface,
                "side_effect": self.side_effect, "idempotent": bool(self.idempotent),
                "timeout_sec": self.timeout_sec, "group_only": bool(self.group_only),
                "private_only": bool(self.private_only),
                "constraints": list(self.constraints), "aliases": list(self.aliases),
                "http": [list(x) for x in self.http], "source": self.source}


@dataclass(frozen=True)
class Permission:
    allowed: bool
    tool: str
    scope: str
    reason: str

    def to_dict(self) -> dict:
        return {"allowed": bool(self.allowed), "tool": self.tool,
                "scope": self.scope, "reason": self.reason}


@dataclass(frozen=True)
class ToolResult:
    tool: str
    ok: bool
    status: str = "ok"
    data: dict = field(default_factory=dict)
    error: str = ""
    latency_ms: int = 0
    idempotent: bool = True
    replayed: bool = False
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if self.status not in RESULT_STATUSES:
            raise ValueError("未知 ToolResult.status: %r" % (self.status,))

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "tool": self.tool,
                "ok": bool(self.ok), "status": self.status, "data": self.data,
                "error": self.error, "latency_ms": int(self.latency_ms),
                "idempotent": bool(self.idempotent),
                "replayed": bool(self.replayed)}

    @classmethod
    def ok_result(cls, tool: str, data: Mapping[str, Any] | None = None,
                  latency_ms: int = 0, idempotent: bool = True) -> "ToolResult":
        return cls(tool, True, "ok", dict(data or {}), "", latency_ms, idempotent)

    @classmethod
    def fail(cls, tool: str, status: str, error: str,
             latency_ms: int = 0, idempotent: bool = True,
             data: Mapping[str, Any] | None = None) -> "ToolResult":
        if status not in RESULT_STATUSES or status == "ok":
            status = "error"
        return cls(tool, False, status, dict(data or {}), str(error or ""),
                   latency_ms, idempotent)

    @classmethod
    def from_legacy(cls, tool: str, result: Any, latency_ms: int = 0,
                    idempotent: bool = True) -> "ToolResult":
        """现有 dispatch 的 {"ok": bool, ...} → ToolResult。"""
        if isinstance(result, ToolResult):
            return result
        if not isinstance(result, Mapping):
            return cls.fail(tool, "error", "返回值不是对象: %s" % type(result).__name__,
                            latency_ms, idempotent)
        # 旧协议两种形状：bot 工具带 "ok"；qq_* 查询成功时只有数据键、没有 "ok"。
        if "ok" in result:
            ok = bool(result.get("ok"))
        else:
            ok = "error" not in result
        return cls(tool, ok, "ok" if ok else "error", dict(result),
                   "" if ok else str(result.get("error") or ""),
                   latency_ms, idempotent)


def validate_args(spec: ToolSpec, args: Any) -> list[str]:
    """轻量参数校验：只查 object/required/基础类型；未知参数保留兼容。"""
    if not isinstance(args, Mapping):
        return ["args 必须是对象"]
    schema = spec.input_schema or {}
    issues = []
    for key in schema.get("required") or ():
        if key not in args or args.get(key) is None:
            issues.append("缺少必填参数 %s" % key)
    props = schema.get("properties") or {}
    for key, value in args.items():
        rule = props.get(key)
        if not isinstance(rule, Mapping) or value is None:
            continue
        typ = rule.get("type")
        if typ == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            issues.append("%s 应为 integer" % key)
        elif typ == "number" and (isinstance(value, bool)
                                  or not isinstance(value, (int, float))):
            issues.append("%s 应为 number" % key)
        elif typ == "string" and not isinstance(value, str):
            issues.append("%s 应为 string" % key)
        elif typ == "boolean" and not isinstance(value, bool):
            issues.append("%s 应为 boolean" % key)
        elif typ == "array" and not isinstance(value, (list, tuple)):
            issues.append("%s 应为 array" % key)
        elif typ == "object" and not isinstance(value, Mapping):
            issues.append("%s 应为 object" % key)
    return issues


# ---------------------------------------------------------------- Registry

class ToolRegistry:
    """一份契约，多套工具面。只读、线程安全（注册在启动期完成）。"""

    def __init__(self, specs: Iterable[ToolSpec] = ()):
        self._specs: dict[str, ToolSpec] = {}
        self._aliases: dict[str, str] = {}
        self._lock = threading.Lock()
        for s in specs or ():
            self.register(s)

    def register(self, spec: ToolSpec) -> None:
        with self._lock:
            if spec.name in self._specs or spec.name in self._aliases:
                raise ValueError("工具重复注册: %s" % spec.name)
            self._specs[spec.name] = spec
            for a in spec.aliases:
                if a in self._specs or a in self._aliases:
                    raise ValueError("工具别名冲突: %s" % a)
                self._aliases[str(a)] = spec.name

    def resolve(self, name: str) -> str | None:
        n = str(name or "")
        if n in self._specs:
            return n
        return self._aliases.get(n)

    def get(self, name: str) -> ToolSpec | None:
        n = self.resolve(name)
        return self._specs.get(n) if n else None

    def names(self, surface: str | None = None) -> list[str]:
        return [n for n, s in self._specs.items()
                if surface is None or s.surface == surface]

    def all(self, surface: str | None = None) -> list[ToolSpec]:
        return [s for s in self._specs.values()
                if surface is None or s.surface == surface]

    def check(self, principal: Principal, name: str,
              allow_draw: bool = True) -> Permission:
        spec = self.get(name)
        if spec is None:
            return Permission(False, str(name), "unknown", "unknown_tool")
        if not principal.allows(spec.scope):
            return Permission(False, spec.name, spec.scope,
                              "scope:%s" % spec.scope)
        if principal.source == "chat":
            if spec.group_only and not principal.is_group:
                return Permission(False, spec.name, spec.scope, "group_only")
            if spec.private_only and principal.is_group:
                return Permission(False, spec.name, spec.scope, "private_only")
        if "allow_draw" in spec.constraints and not allow_draw:
            return Permission(False, spec.name, spec.scope,
                              "constraint:allow_draw")
        return Permission(True, spec.name, spec.scope, "ok")

    def visible(self, principal: Principal, surface: str = "omni",
                allow_draw: bool = True) -> list[ToolSpec]:
        return [s for s in self.all(surface)
                if self.check(principal, s.name, allow_draw).allowed]

    def shadow_offer(self, principal: Principal, surface: str,
                     offered: Iterable[str], allow_draw: bool = True) -> dict:
        """当前工具面 vs Registry 预期：差异分类，供影子审计。"""
        offered = [str(x) for x in (offered or ()) if str(x)]
        resolved = [self.resolve(n) or n for n in offered]
        expected = [s.name for s in self.visible(principal, surface, allow_draw)]
        missing_spec = sorted({n for n in offered
                               if self.get(n) is None})
        denied = sorted({self.resolve(n) or n for n in offered
                         if not self.check(principal, n, allow_draw).allowed})
        absent = sorted(set(expected) - set(resolved))
        extra = sorted(set(resolved) - set(expected) - set(denied))
        report = {
            "surface": surface, "principal": principal.to_dict(),
            "offered": offered, "missing_spec": missing_spec,
            "denied_but_offered": denied, "expected_but_absent": absent,
            "allowed_but_not_expected": extra,
        }
        report["clean"] = not (missing_spec or denied or absent or extra)
        return report

    def shadow_call(self, principal: Principal, name: str,
                    caller_allowed: bool, allow_draw: bool = True) -> dict:
        """实际执行时的影子对账：Registry 结论 vs 执行层结论。"""
        perm = self.check(principal, name, allow_draw)
        denied_called = bool(caller_allowed and not perm.allowed)
        allowed_blocked = bool((not caller_allowed) and perm.allowed)
        return {"tool": str(name), "principal": principal.to_dict(),
                "registry_allowed": perm.allowed, "registry_reason": perm.reason,
                "caller_allowed": bool(caller_allowed),
                "denied_but_called": denied_called,
                "allowed_but_blocked": allowed_blocked,
                "clean": not (denied_called or allowed_blocked)}

    def coverage(self) -> dict:
        rows = []
        by_scope = {s: 0 for s in SCOPES}
        by_surface = {s: 0 for s in SURFACES}
        for spec in self._specs.values():
            issues = []
            if not spec.description:
                issues.append("missing_description")
            if not isinstance(spec.input_schema, Mapping):
                issues.append("missing_schema")
            if spec.scope not in SCOPES:
                issues.append("missing_scope")
            if spec.surface not in SURFACES:
                issues.append("missing_surface")
            if spec.side_effect not in SIDE_EFFECTS:
                issues.append("missing_side_effect")
            if issues:
                rows.append({"name": spec.name, "issues": issues})
            by_scope[spec.scope] += 1
            by_surface[spec.surface] += 1
        return {"total": len(self._specs), "by_scope": by_scope,
                "by_surface": by_surface, "invalid": rows,
                "clean": not rows}

    def openai_tools(self, names: Iterable[str] | None = None) -> list[dict]:
        wanted = list(names) if names is not None else self.names("omni")
        out = []
        for n in wanted:
            spec = self.get(n)
            if spec is not None:
                out.append(spec.to_openai())
        return out

    def mcp_tools(self, names: Iterable[str] | None = None) -> list[dict]:
        wanted = list(names) if names is not None else self.names("mcp")
        out = []
        for n in wanted:
            spec = self.get(n)
            if spec is not None:
                out.append(spec.to_mcp())
        return out


# ---------------------------------------------------------------- 工具面元数据

def _name(tool: Mapping[str, Any]) -> str:
    fn = (tool or {}).get("function") if isinstance(tool, Mapping) else None
    if isinstance(fn, Mapping):
        return str(fn.get("name") or "")
    return str((tool or {}).get("name") or "")


# 总控大脑 / QQ 工具（OpenAI function 格式）：权限 + 执行约束只在这里声明一次。
# scope 口径 = **工具面准入线**（bot._omni_tools 真正摘下/留下的那一条），
# 不是 handler 里的资源闸（冷却/白名单/仅管理员仍在 handler，Registry 不取代）。
OMNI_META = {
    # —— QQ 工具：_omni_tools 只给 privileged（主人 / 可信群管）——
    "qq_groups":      dict(scope=ADMIN, side_effect="read", idempotent=True, timeout_sec=15),
    "qq_members":     dict(scope=ADMIN, side_effect="read", idempotent=True, timeout_sec=15),
    "qq_history":     dict(scope=ADMIN, side_effect="read", idempotent=True, timeout_sec=15),
    "qq_send_group":  dict(scope=ADMIN, side_effect="irreversible", idempotent=False, timeout_sec=45),
    "qq_wait_new":    dict(scope=ADMIN, side_effect="read", idempotent=True, timeout_sec=45),
    # —— bot 能力：群友也可用；画图/视频那 3 个还受 allow_draw 资源闸 ——
    "draw_image":     dict(scope=PUBLIC, side_effect="external", idempotent=False,
                           timeout_sec=60, constraints=("allow_draw",), aliases=("cloud_draw",)),
    "draw_like_this": dict(scope=PUBLIC, side_effect="external", idempotent=False,
                           timeout_sec=60, constraints=("allow_draw",)),
    "reverse_prompt": dict(scope=PUBLIC, side_effect="external", idempotent=False,
                           timeout_sec=60),
    "gen_video":      dict(scope=PUBLIC, side_effect="external", idempotent=False,
                           timeout_sec=30, constraints=("allow_draw",)),
    "web_search":     dict(scope=PUBLIC, side_effect="external", idempotent=True, timeout_sec=25),
    "recall_memory":  dict(scope=PUBLIC, side_effect="read", idempotent=True, timeout_sec=10),
    "remember":       dict(scope=ADMIN, side_effect="write", idempotent=False, timeout_sec=10),
    "send_meme":      dict(scope=PUBLIC, side_effect="write", idempotent=False, timeout_sec=20),
    "bot_status":     dict(scope=PUBLIC, side_effect="read", idempotent=True, timeout_sec=10),
}


def build_omni_registry(qq_tools: Iterable[Mapping[str, Any]],
                        bot_tools: Iterable[Mapping[str, Any]]) -> ToolRegistry:
    reg = ToolRegistry()
    for tool in list(qq_tools or ()) + list(bot_tools or ()):
        name = _name(tool)
        fn = tool.get("function") if isinstance(tool, Mapping) else None
        if not name or not isinstance(fn, Mapping):
            continue
        meta = dict(OMNI_META.get(name) or {})
        if not meta:
            # 没登记的新工具不能悄悄变成 public：默认按最严处理并进 coverage 报告。
            meta = dict(scope=OWNER, side_effect="write", idempotent=False,
                        timeout_sec=15)
        reg.register(ToolSpec(
            name=name, description=str(fn.get("description") or ""),
            input_schema=fn.get("parameters") or {"type": "object", "properties": {}},
            scope=meta.get("scope", OWNER), surface="omni",
            side_effect=meta.get("side_effect", "write"),
            idempotent=bool(meta.get("idempotent", False)),
            timeout_sec=float(meta.get("timeout_sec", 15)),
            constraints=tuple(meta.get("constraints") or ()),
            aliases=tuple(meta.get("aliases") or ()),
            source="bot._QQ_TOOLS/_BOT_TOOLS",
        ))
    return reg


# MCP 工具：全部只由本机桥接 token 调用（system）；HTTP 映射是兼容层的唯一登记。
MCP_META = {
    "qq_status":            dict(side_effect="read", idempotent=True, timeout_sec=45,
                                 http=(("GET", "/api/status"),)),
    "qq_groups":            dict(side_effect="read", idempotent=True, timeout_sec=45,
                                 http=(("GET", "/api/groups"),)),
    "qq_members":           dict(side_effect="read", idempotent=True, timeout_sec=45,
                                 http=(("GET", "/api/members"),)),
    "qq_history":           dict(side_effect="read", idempotent=True, timeout_sec=45,
                                 http=(("GET", "/api/history"),)),
    "qq_wait_for_messages": dict(side_effect="read", idempotent=True, timeout_sec=55,
                                 http=(("GET", "/api/wait_messages"),)),
    "qq_send":              dict(side_effect="irreversible", idempotent=False,
                                 timeout_sec=45, http=(("POST", "/api/send"),)),
    "qq_generate":          dict(side_effect="external", idempotent=False,
                                 timeout_sec=60, http=(("POST", "/api/generate"),)),
    "qq_slang_list":        dict(side_effect="read", idempotent=True, timeout_sec=45,
                                 http=(("GET", "/api/slang"),)),
    "qq_slang_propose":     dict(side_effect="write", idempotent=False, timeout_sec=45,
                                 http=(("POST", "/api/slang"),)),
    "qq_meme_lookup":       dict(side_effect="read", idempotent=True, timeout_sec=45,
                                 http=(("POST", "/api/skill"),)),
    "qq_meme_catalog":      dict(side_effect="read", idempotent=True, timeout_sec=45,
                                 http=(("POST", "/api/skill"),)),
    "qq_meme_teach":        dict(side_effect="write", idempotent=False, timeout_sec=45,
                                 http=(("POST", "/api/skill"),)),
    "qq_meme_pick":         dict(side_effect="read", idempotent=True, timeout_sec=45,
                                 http=(("POST", "/api/skill"),)),
    "qq_meme_send":         dict(side_effect="write", idempotent=False, timeout_sec=90,
                                 http=(("POST", "/api/skill"),)),
    "qq_fibi_pick":         dict(side_effect="read", idempotent=True, timeout_sec=45,
                                 http=(("POST", "/api/skill"),)),
    "qq_fibi_send":         dict(side_effect="write", idempotent=False, timeout_sec=60,
                                 http=(("POST", "/api/skill"),)),
    "qq_petpet":            dict(side_effect="write", idempotent=False, timeout_sec=120,
                                 http=(("POST", "/api/skill"),)),
    "qq_recall":            dict(side_effect="read", idempotent=True, timeout_sec=45,
                                 http=(("GET", "/api/memory"),)),
    "qq_remember":          dict(side_effect="write", idempotent=False, timeout_sec=45,
                                 http=(("POST", "/api/memory"),)),
    "qq_config":            dict(side_effect="irreversible", idempotent=False,
                                 timeout_sec=45,
                                 http=(("GET", "/api/config"), ("POST", "/api/config"))),
}


def build_mcp_registry(mcp_tools: Iterable[Mapping[str, Any]]) -> ToolRegistry:
    reg = ToolRegistry()
    for tool in mcp_tools or ():
        name = _name(tool)
        if not name:
            continue
        meta = dict(MCP_META.get(name) or {})
        reg.register(ToolSpec(
            name=name, description=str(tool.get("description") or ""),
            input_schema=tool.get("inputSchema") or {"type": "object",
                                                     "properties": {}, "required": []},
            scope=SYSTEM, surface="mcp",
            side_effect=meta.get("side_effect", "write"),
            idempotent=bool(meta.get("idempotent", False)),
            timeout_sec=float(meta.get("timeout_sec", 45)),
            http=tuple(meta.get("http") or ()),
            source="qq-mcp-server.TOOLS",
        ))
    return reg


# ---------------------------------------------------------------- 执行契约（影子）

class ToolRuntime:
    """按 ToolSpec 执行一次调用：参数校验 → 幂等复用 → 超时护栏 → ToolResult。

    只给影子/测试用：executor 由调用方传入，模块本身不 import 任何工具实现。
    """

    def __init__(self, cache_enabled: bool = True):
        self.cache_enabled = bool(cache_enabled)
        self._cache: dict = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()

    @staticmethod
    def _key(spec: ToolSpec, args: Mapping[str, Any]) -> tuple:
        try:
            body = json.dumps(dict(args or {}), ensure_ascii=False, sort_keys=True)
        except Exception:  # noqa: BLE001
            body = str(args)
        return (spec.name, body)

    def execute(self, spec: ToolSpec, args: Mapping[str, Any],
                executor: Callable[[], Any], timeout: float | None = None) -> ToolResult:
        issues = validate_args(spec, args)
        if issues:
            return ToolResult.fail(spec.name, "invalid", "；".join(issues),
                                   idempotent=spec.idempotent)
        key = self._key(spec, args)
        if self.cache_enabled and spec.idempotent:
            with self._lock:
                hit = self._cache.get(key)
            if hit is not None:
                return ToolResult(**{**hit.__dict__, "replayed": True})
        limit = float(timeout if timeout is not None else spec.timeout_sec)
        box: dict = {}

        def _run():
            try:
                box["v"] = executor()
            except Exception as e:  # noqa: BLE001
                box["e"] = e

        t0 = time.time()
        th = threading.Thread(target=_run, daemon=True)
        th.start()
        th.join(max(0.0, limit))
        elapsed = int((time.time() - t0) * 1000)
        if th.is_alive():
            return ToolResult.fail(spec.name, "timeout",
                                   "执行超时 %ss" % limit, elapsed, spec.idempotent)
        if "e" in box:
            return ToolResult.fail(spec.name, "error", str(box["e"]), elapsed,
                                   spec.idempotent)
        tr = ToolResult.from_legacy(spec.name, box.get("v"), elapsed, spec.idempotent)
        if self.cache_enabled and spec.idempotent and tr.ok:
            with self._lock:
                self._cache[key] = tr
        return tr


def record_shadow(kind: str, payload: Mapping[str, Any]) -> bool:
    """追加一条工具影子审计。永不抛异常，不改变执行结果。"""
    rec = {"schema_version": SCHEMA_VERSION, "kind": str(kind),
           "written_at": time.time()}
    rec.update(dict(payload or {}))
    try:
        with _FS_LOCK:
            SHADOW_DIR.mkdir(parents=True, exist_ok=True)
            with open(SHADOW_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False
