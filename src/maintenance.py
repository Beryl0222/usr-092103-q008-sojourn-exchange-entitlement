"""维修封房与自然灾害：只影响相应日期与房源，受影响的换住预约转入补偿流程。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .availability import Calendars, OccupancyKind
from .events import EventStore


@dataclass(frozen=True)
class MaintenanceBlock:
    block_id: str
    property_id: str
    start: date
    end: date
    cause: str  # maintenance / natural_disaster


class MaintenanceService:
    def __init__(self, store: EventStore, calendars: Calendars) -> None:
        self._store = store
        self._calendars = calendars

    def affected_reservations(self, property_id: str, start: date, end: date) -> list[str]:
        """只查出该房源在该日期段内的换住预约，其他房源与其他日期不受影响。"""
        calendar = self._calendars.for_property(property_id)
        return sorted({
            o.ref_id
            for o in calendar.overlapping(start, end, kinds={OccupancyKind.EXCHANGE})
        })

    def block(self, block: MaintenanceBlock, occurred_at: str) -> MaintenanceBlock:
        """封房登记。须先对受影响预约完成补偿（释放占用），否则与在住占用冲突。"""
        calendar = self._calendars.for_property(block.property_id)
        calendar.place(OccupancyKind.MAINTENANCE, block.start, block.end, block.block_id)
        self._store.emit(
            "MAINTENANCE_BLOCKED", "maintenance_block", block.block_id, occurred_at,
            f"房源 {block.property_id} 封房 {block.start}~{block.end}（{block.cause}）",
            payload={"property_id": block.property_id, "cause": block.cause,
                     "start": block.start.isoformat(), "end": block.end.isoformat(),
                     "calendar_version": calendar.version},
        )
        return block
