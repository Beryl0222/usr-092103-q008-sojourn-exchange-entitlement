"""房态版本：同一房源上长期租住、短期换住、房东自用、维修封房互不重叠。

日期一律使用半开区间 [start, end)：退房当天即可被下一段占用。
每次占用变更（登记、释放、缩短）都会提升日历版本。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class OccupancyKind(str, Enum):
    LONG_TERM = "long_term"      # 长期租住
    EXCHANGE = "exchange"        # 短期换住
    OWNER_SELF = "owner_self"    # 房东自用
    MAINTENANCE = "maintenance"  # 维修封房


@dataclass(frozen=True)
class Occupancy:
    property_id: str
    kind: OccupancyKind
    start: date
    end: date  # 半开区间 [start, end)
    ref_id: str  # 占用来源：预约号、长租合同号、维修单号等


class OverlapError(Exception):
    """同一房源的占用时间段互相重叠。"""


class RoomCalendar:
    """单个房源的房态日历，version 随每次占用变更递增。"""

    def __init__(self, property_id: str) -> None:
        self.property_id = property_id
        self.version = 0
        self._active: list[Occupancy] = []

    def overlapping(self, start: date, end: date, kinds: set[OccupancyKind] | None = None) -> list[Occupancy]:
        return [
            o for o in self._active
            if o.start < end and start < o.end and (kinds is None or o.kind in kinds)
        ]

    def can_place(self, start: date, end: date) -> bool:
        return not self.overlapping(start, end)

    def place(self, kind: OccupancyKind, start: date, end: date, ref_id: str) -> Occupancy:
        if end <= start:
            raise ValueError("结束日期必须晚于开始日期")
        conflicts = self.overlapping(start, end)
        if conflicts:
            c = conflicts[0]
            raise OverlapError(
                f"房源 {self.property_id} 在 {c.start}~{c.end} 已被 {c.kind.value} 占用（{c.ref_id}）"
            )
        occupancy = Occupancy(self.property_id, kind, start, end, ref_id)
        self._active.append(occupancy)
        self.version += 1
        return occupancy

    def release(self, ref_id: str) -> list[Occupancy]:
        released = [o for o in self._active if o.ref_id == ref_id]
        for occupancy in released:
            self._active.remove(occupancy)
        if released:
            self.version += 1
        return released

    def shorten(self, ref_id: str, new_end: date) -> list[Occupancy]:
        """提前离开：把占用结束日提前到 new_end，释放尾部日期。"""
        shortened: list[Occupancy] = []
        kept: list[Occupancy] = []
        for occupancy in self._active:
            if occupancy.ref_id == ref_id and occupancy.start < new_end < occupancy.end:
                kept.append(Occupancy(occupancy.property_id, occupancy.kind,
                                      occupancy.start, new_end, occupancy.ref_id))
                shortened.append(occupancy)
            else:
                kept.append(occupancy)
        if shortened:
            self._active = kept
            self.version += 1
        return shortened

    def occupancies(self) -> tuple[Occupancy, ...]:
        return tuple(self._active)


class Calendars:
    """按房源管理的房态日历集合。"""

    def __init__(self) -> None:
        self._by_property: dict[str, RoomCalendar] = {}

    def for_property(self, property_id: str) -> RoomCalendar:
        return self._by_property.setdefault(property_id, RoomCalendar(property_id))
