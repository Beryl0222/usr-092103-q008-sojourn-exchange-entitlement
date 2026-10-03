"""入住验真：离线可受理，恢复联网后按 client_request_id 去重。"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .events import EventStore
from .reservations import ExchangeService, ReservationStatus


@dataclass(frozen=True)
class CheckInRecord:
    check_in_id: str
    reservation_id: str
    client_request_id: str  # 办理设备生成的幂等键
    steward: str
    occurred_at: str
    offline: bool
    verified: bool


class CheckInService:
    def __init__(self, store: EventStore, exchange: ExchangeService) -> None:
        self._store = store
        self._exchange = exchange
        self._by_request: dict[str, CheckInRecord] = {}

    def record(self, reservation_id: str, client_request_id: str, steward: str,
               occurred_at: str, offline: bool = False) -> CheckInRecord:
        """办理入住。同一 client_request_id 重复提交（含离线重放）只生效一次。"""
        known = self._by_request.get(client_request_id)
        if known is not None:
            return known
        reservation = self._exchange.get(reservation_id)
        if reservation.status not in (ReservationStatus.CONFIRMED, ReservationStatus.CHECKED_IN):
            raise ValueError(f"预约 {reservation_id} 当前状态不可办理入住")
        record = CheckInRecord(
            check_in_id=f"ci-{client_request_id}",
            reservation_id=reservation_id,
            client_request_id=client_request_id,
            steward=steward,
            occurred_at=occurred_at,
            offline=offline,
            verified=False,
        )
        self._by_request[client_request_id] = record
        self._store.emit(
            "CHECK_IN_RECORDED", "check_in_record", record.check_in_id, occurred_at,
            f"入住受理（{'离线' if offline else '在线'}）",
            payload={"reservation_id": reservation_id,
                     "client_request_id": client_request_id,
                     "steward": steward, "offline": offline},
        )
        if not offline:
            return self.verify(client_request_id, occurred_at)
        return record

    def verify(self, client_request_id: str, occurred_at: str) -> CheckInRecord:
        """联网验真：已验真的记录重复验真直接返回，不重复发事件。"""
        record = self._by_request[client_request_id]
        if record.verified:
            return record
        record = replace(record, verified=True)
        self._by_request[client_request_id] = record
        self._exchange.mark_checked_in(record.reservation_id)
        self._store.emit(
            "STAY_CHECKED_IN", "check_in_record", record.check_in_id, occurred_at,
            "入住验真完成",
            payload={"reservation_id": record.reservation_id,
                     "client_request_id": client_request_id},
        )
        return record
