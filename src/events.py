"""领域事件信封与仅追加事件存储。

所有事件携带统一身份与版本：
- event_id 全局唯一，用于投递去重（含离线重放）；
- version 是聚合内的连续序号，从 1 开始，用于乐观并发控制。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    occurred_at: str
    version: int
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        record = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "occurred_at": self.occurred_at,
            "version": self.version,
            "summary": self.summary,
        }
        record.update(self.payload)
        return record


class VersionConflict(Exception):
    """聚合版本与预期不一致。"""


class EventConflict(Exception):
    """同一 event_id 携带了不同内容。"""


class EventStore:
    """内存事件存储：按 event_id 去重，按聚合校验连续版本。"""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._by_id: dict[str, Event] = {}
        self._versions: dict[tuple[str, str], int] = {}

    def next_version(self, aggregate_type: str, aggregate_id: str) -> int:
        return self._versions.get((aggregate_type, aggregate_id), 0) + 1

    def append(self, event: Event) -> Event:
        known = self._by_id.get(event.event_id)
        if known is not None:
            if known != event:
                raise EventConflict(f"event_id {event.event_id} 已存在且内容不同")
            return known  # 重复投递/离线重放：同一事件只生效一次
        key = (event.aggregate_type, event.aggregate_id)
        expected = self._versions.get(key, 0) + 1
        if event.version != expected:
            raise VersionConflict(f"{key} 期望版本 {expected}，收到 {event.version}")
        self._events.append(event)
        self._by_id[event.event_id] = event
        self._versions[key] = event.version
        return event

    def emit(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        occurred_at: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> Event:
        version = self.next_version(aggregate_type, aggregate_id)
        event = Event(
            event_id=event_id or f"{aggregate_type}:{aggregate_id}:{version}",
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            occurred_at=occurred_at,
            version=version,
            summary=summary,
            payload=dict(payload or {}),
        )
        return self.append(event)

    def of_aggregate(self, aggregate_type: str, aggregate_id: str) -> list[Event]:
        return [e for e in self._events
                if e.aggregate_type == aggregate_type and e.aggregate_id == aggregate_id]

    def of_type(self, event_type: str) -> list[Event]:
        return [e for e in self._events if e.event_type == event_type]

    def all(self) -> tuple[Event, ...]:
        return tuple(self._events)
