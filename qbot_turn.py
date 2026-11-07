# -*- coding: utf-8 -*-
"""M1 Turn / MediaRef 契约（ADR-001 §4.2/§4.3）。

只做三件事：
  1. 把 OneBot 事件里**已经解析好**的字段归一成不可变 Turn；
  2. 为同一 (adapter, event_id) 生成确定性 turn_id；
  3. 给影子核心/测试一个稳定的 to_dict()。

边界：
  - 不判断业务意图，不拼 Prompt，不发消息，不读写数据库；
  - 不抓媒体字节；MediaRef 只保留引用与状态，M2 再接 MediaStore；
  - 不 import bot，避免和宿主循环依赖。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
ACTIONS = ("reply", "silent", "clarify", "refuse", "tool_call")


def _json_safe(value: Any) -> Any:
    """把事件里的非 JSON 值收成字符串，保证 Turn 能直接落日志。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(x) for x in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


def _digest(*parts: Any, n: int = 24) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.hexdigest()[:n]


def rfc3339(ts: float | int | None = None) -> str:
    """RFC3339 带时区（UTC Z）；缺时间时用当前时间。"""
    t = float(ts) if ts not in (None, "") else datetime.now(timezone.utc).timestamp()
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def turn_id_for(adapter: str, event_id: str) -> str:
    return _digest("turn", adapter, event_id)


def fallback_event_id(session_key: Any, user_id: Any, ts: Any, text: str,
                      media_count: int) -> str:
    """没有 message_id 时的确定性事件键。同一事件字段重复投递 → 同一 turn_id。"""
    return _digest("event", session_key, user_id, ts if ts is not None else "", text,
                   media_count, n=32)


@dataclass(frozen=True)
class MediaRef:
    schema_version: int
    media_id: str
    kind: str
    status: str
    mime: str | None = None
    bytes: int | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    local_uri: str | None = None
    origin: str = "current"
    access_scope: str = "session"
    created_at: str = ""
    expires_at: str | None = None
    error_code: str | None = None
    derived: Mapping[str, Any] | None = None

    def to_dict(self) -> dict:
        d = {
            "schema_version": self.schema_version,
            "media_id": self.media_id,
            "kind": self.kind,
            "status": self.status,
            "mime": self.mime,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
            "duration_ms": self.duration_ms,
            "local_uri": self.local_uri,
            "origin": self.origin,
            "access_scope": self.access_scope,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "error_code": self.error_code,
            "derived": _json_safe(dict(self.derived)) if self.derived else None,
        }
        return d


@dataclass(frozen=True)
class Session:
    kind: str
    key: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "key": str(self.key)}


@dataclass(frozen=True)
class Actor:
    user_id: str
    display_name: str
    role: str

    def to_dict(self) -> dict:
        return {"user_id": str(self.user_id), "display_name": self.display_name,
                "role": self.role}


@dataclass(frozen=True)
class Quote:
    turn_id: str | None
    text: str
    media: tuple[MediaRef, ...] = ()
    unresolved: bool = False

    def to_dict(self) -> dict:
        return {"turn_id": self.turn_id, "text": self.text,
                "media": [m.to_dict() for m in self.media],
                "unresolved": bool(self.unresolved)}


@dataclass(frozen=True)
class Source:
    adapter: str
    event_id: str

    def to_dict(self) -> dict:
        return {"adapter": self.adapter, "event_id": str(self.event_id)}


@dataclass(frozen=True)
class Turn:
    schema_version: int
    turn_id: str
    session: Session
    actor: Actor
    received_at: str
    text: str
    media: tuple[MediaRef, ...] = ()
    quote: Quote | None = None
    addressed: bool = False
    task_refs: tuple[Mapping[str, Any], ...] = ()
    flags: tuple[tuple[str, bool], ...] = ()
    source: Source = field(default_factory=lambda: Source("napcat", ""))

    def flag(self, name: str, default: bool = False) -> bool:
        for k, v in self.flags:
            if k == name:
                return bool(v)
        return default

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "turn_id": self.turn_id,
            "session": self.session.to_dict(),
            "actor": self.actor.to_dict(),
            "received_at": self.received_at,
            "text": self.text,
            "media": [m.to_dict() for m in self.media],
            "quote": self.quote.to_dict() if self.quote else None,
            "addressed": bool(self.addressed),
            "task_refs": [_json_safe(dict(t)) for t in self.task_refs],
            "flags": {k: bool(v) for k, v in self.flags},
            "source": self.source.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def make_media_refs(turn_id: str, refs: Iterable[Any], *, origin: str = "current",
                    created_at: str | None = None) -> tuple[MediaRef, ...]:
    """把图片段/文件名列表变成 MediaRef。只登记引用，不读字节。"""
    out = []
    for i, raw in enumerate(refs or ()):
        if isinstance(raw, dict):
            ref = raw.get("file") or raw.get("url") or raw.get("file_id") or ""
            kind = str(raw.get("type") or "image")
        else:
            ref = str(raw or "")
            kind = "image"
        mid = "m_" + _digest(turn_id, origin, i, ref, n=20)
        out.append(MediaRef(
            schema_version=SCHEMA_VERSION, media_id=mid, kind=kind if kind in
            ("image", "video", "audio", "file") else "image",
            status="pending", origin=origin, access_scope="session",
            created_at=created_at or rfc3339(),
            # M4：原始引用（本地路径 / URL / file_id）留在 derived 里，选图时才能多图取回。
            # 只登记引用，不读字节；消费方自己决定要不要下载。
            derived=({"ref": ref} if ref else None)))
    return tuple(out)


def make_turn(*, adapter: str = "napcat", event_id: str | None = None,
              session_kind: str, session_key: Any, user_id: Any,
              display_name: str = "", role: str = "member",
              received_at: float | int | None = None, text: str = "",
              media_refs: Iterable[Any] = (), quote_text: str = "",
              quote_turn_id: str | None = None, quote_media_refs: Iterable[Any] = (),
              quote_unresolved: bool = False, addressed: bool = False,
              flags: Mapping[str, bool] | None = None,
              task_refs: Iterable[Mapping[str, Any]] = ()) -> Turn:
    """Adapter 的唯一入口：输入已解析字段，输出不可变 Turn。"""
    kind = "group" if session_kind == "group" else "private"
    media_items = list(media_refs or ())
    qmedia_items = list(quote_media_refs or ())
    eid = str(event_id or fallback_event_id(session_key, user_id, received_at, text,
                                            len(media_items)))
    tid = turn_id_for(adapter, eid)
    ts = rfc3339(received_at)
    media = make_media_refs(tid, media_items, origin="current", created_at=ts)
    qmedia = make_media_refs(tid, qmedia_items, origin="quote", created_at=ts)
    quote = None
    if quote_text or qmedia or quote_unresolved:
        quote = Quote(turn_id=quote_turn_id, text=str(quote_text or ""),
                      media=qmedia, unresolved=bool(quote_unresolved))
    return Turn(
        schema_version=SCHEMA_VERSION, turn_id=tid,
        session=Session(kind=kind, key=str(session_key)),
        actor=Actor(user_id=str(user_id), display_name=str(display_name or ""),
                    role=str(role or "member")),
        received_at=ts, text=str(text or ""), media=media, quote=quote,
        addressed=bool(addressed),
        task_refs=tuple(dict(t) for t in (task_refs or ())),
        flags=tuple(sorted((str(k), bool(v)) for k, v in (flags or {}).items())),
        source=Source(adapter=adapter, event_id=eid),
    )


def turn_from_dict(d: Mapping[str, Any]) -> Turn:
    """测试/回放用：从 to_dict() 还原；字段未知时忽略。"""
    q = d.get("quote")
    quote = None
    if q:
        quote = Quote(turn_id=q.get("turn_id"), text=str(q.get("text") or ""),
                      unresolved=bool(q.get("unresolved")))
    return Turn(
        schema_version=int(d.get("schema_version") or SCHEMA_VERSION),
        turn_id=str(d.get("turn_id") or ""),
        session=Session(kind=(d.get("session") or {}).get("kind", "group"),
                        key=str((d.get("session") or {}).get("key", ""))),
        actor=Actor(user_id=str((d.get("actor") or {}).get("user_id", "")),
                    display_name=str((d.get("actor") or {}).get("display_name", "")),
                    role=str((d.get("actor") or {}).get("role", "member"))),
        received_at=str(d.get("received_at") or ""),
        text=str(d.get("text") or ""),
        quote=quote, addressed=bool(d.get("addressed")),
        flags=tuple(sorted((str(k), bool(v)) for k, v in (d.get("flags") or {}).items())),
        source=Source(adapter=(d.get("source") or {}).get("adapter", "napcat"),
                      event_id=str((d.get("source") or {}).get("event_id", ""))),
    )
