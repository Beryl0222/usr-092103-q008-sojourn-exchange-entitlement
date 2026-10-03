"""预约占用：跨村预约原子确认，取消与提前离开按合同责任处理。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from .availability import Calendars, OccupancyKind
from .contracts import ContractRegistry
from .events import EventStore
from .points import PointsLedger


class ReservationStatus(str, Enum):
    CONFIRMED = "confirmed"
    CHECKED_IN = "checked_in"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DISRUPTED = "disrupted"  # 因维修/灾害被中断，等待补偿；原预约内容不变


@dataclass(frozen=True)
class Segment:
    property_id: str
    village: str
    check_in: date
    check_out: date
    points: int


@dataclass
class Reservation:
    reservation_id: str
    member_id: str
    contract_id: str
    segments: tuple[Segment, ...]
    status: ReservationStatus
    links: dict = field(default_factory=dict)  # original_reservation / compensation_id / cause


class AtomicConfirmError(Exception):
    """跨村预约未整体确认：任何村落都不落占用、不扣点。"""


class ExchangeService:
    def __init__(self, store: EventStore, calendars: Calendars,
                 points: PointsLedger, contracts: ContractRegistry) -> None:
        self._store = store
        self._calendars = calendars
        self._points = points
        self._contracts = contracts
        self._reservations: dict[str, Reservation] = {}
        self._keys: dict[str, str] = {}

    def get(self, reservation_id: str) -> Reservation:
        return self._reservations[reservation_id]

    def confirm(self, reservation_id: str, member_id: str, segments: list[Segment],
                occurred_at: str, idempotency_key: str | None = None,
                links: dict | None = None) -> Reservation:
        """跨村原子确认：先全量校验，再统一提交；任一段失败则整体不生效。"""
        if idempotency_key is not None and idempotency_key in self._keys:
            return self._reservations[self._keys[idempotency_key]]
        contract = self._contracts.of_member(member_id)
        account = self._contracts.account_of(member_id)
        total = sum(s.points for s in segments)
        # 第一阶段：全部校验，不做任何变更
        if self._points.balance(account) < total:
            raise AtomicConfirmError(f"会员 {member_id} 点数不足，跨村预约整体未确认")
        for seg in segments:
            calendar = self._calendars.for_property(seg.property_id)
            if not calendar.can_place(seg.check_in, seg.check_out):
                raise AtomicConfirmError(
                    f"{seg.village}/{seg.property_id} {seg.check_in}~{seg.check_out} "
                    "不可占用，跨村预约整体未确认"
                )
        # 第二阶段：统一提交占用、扣点与确认事件
        for seg in segments:
            calendar = self._calendars.for_property(seg.property_id)
            calendar.place(OccupancyKind.EXCHANGE, seg.check_in, seg.check_out, reservation_id)
            self._store.emit(
                "OCCUPANCY_RESERVED", "room_calendar", seg.property_id, occurred_at,
                f"{seg.village} 换住占用 {seg.check_in}~{seg.check_out}",
                payload={"kind": OccupancyKind.EXCHANGE.value, "reservation_id": reservation_id,
                         "village": seg.village, "start": seg.check_in.isoformat(),
                         "end": seg.check_out.isoformat(), "calendar_version": calendar.version},
            )
        self._points.debit(account, total, "reservation_confirm", occurred_at,
                           refs={"reservation_id": reservation_id},
                           summary=f"预约 {reservation_id} 扣点 {total}")
        self._store.emit(
            "POINTS_DEBITED", "points_account", account, occurred_at,
            f"预约 {reservation_id} 扣减 {total} 点",
            payload={"amount": total, "reason_code": "reservation_confirm",
                     "reservation_id": reservation_id},
        )
        reservation = Reservation(
            reservation_id=reservation_id, member_id=member_id,
            contract_id=contract.contract_id, segments=tuple(segments),
            status=ReservationStatus.CONFIRMED, links=dict(links or {}),
        )
        self._reservations[reservation_id] = reservation
        if idempotency_key is not None:
            self._keys[idempotency_key] = reservation_id
        self._store.emit(
            "RESERVATION_CONFIRMED", "stay_reservation", reservation_id, occurred_at,
            f"跨村预约确认，共 {len(segments)} 段",
            payload={"member_id": member_id, "contract_id": contract.contract_id,
                     "segments": [{"property_id": s.property_id, "village": s.village,
                                   "check_in": s.check_in.isoformat(),
                                   "check_out": s.check_out.isoformat(),
                                   "points": s.points} for s in segments],
                     "links": dict(links or {})},
        )
        return reservation

    def cancel(self, reservation_id: str, as_of: date, occurred_at: str) -> Reservation:
        """取消预约：按合同取消责任计算返还比例。"""
        reservation = self.get(reservation_id)
        policy = self._contracts.of_member(reservation.member_id).cancel_policy
        refund = 0
        for seg in reservation.segments:
            days_before = (seg.check_in - as_of).days
            ratio = 1.0 if days_before >= policy.free_cancel_days else policy.late_refund_ratio
            refund += int(seg.points * ratio)
        self.release(reservation_id, occurred_at, reason="cancel")
        if refund:
            self.refund(reservation, refund, "cancel_refund", occurred_at,
                        f"取消预约 {reservation_id} 返还 {refund} 点")
        reservation.status = ReservationStatus.CANCELLED
        self._store.emit(
            "RESERVATION_CANCELLED", "stay_reservation", reservation_id, occurred_at,
            f"预约取消，返还 {refund} 点", payload={"refund_points": refund},
        )
        return reservation

    def early_departure(self, reservation_id: str, actual_checkout: date,
                        occurred_at: str) -> Reservation:
        """住客提前离开：释放未住晚数，按合同比例返还，不多扣。"""
        reservation = self.get(reservation_id)
        policy = self._contracts.of_member(reservation.member_id).cancel_policy
        refund = 0
        for seg in reservation.segments:
            calendar = self._calendars.for_property(seg.property_id)
            if not calendar.shorten(reservation_id, actual_checkout):
                continue
            total_nights = (seg.check_out - seg.check_in).days
            unused = (seg.check_out - actual_checkout).days
            if total_nights > 0 and unused > 0:
                refund += int(seg.points / total_nights * unused * policy.early_departure_ratio)
            self._store.emit(
                "OCCUPANCY_RELEASED", "room_calendar", seg.property_id, occurred_at,
                f"预约 {reservation_id} 提前离开，释放 {actual_checkout}~{seg.check_out}",
                payload={"reservation_id": reservation_id, "reason": "early_departure",
                         "kind": OccupancyKind.EXCHANGE.value,
                         "calendar_version": calendar.version},
            )
        if refund:
            self.refund(reservation, refund, "early_departure_refund", occurred_at,
                        f"提前离开返还 {refund} 点")
        return reservation

    def release(self, reservation_id: str, occurred_at: str, reason: str = "released") -> None:
        """释放预约的全部占用（取消、维修中断等）。"""
        reservation = self.get(reservation_id)
        for seg in reservation.segments:
            calendar = self._calendars.for_property(seg.property_id)
            if calendar.release(reservation_id):
                self._store.emit(
                    "OCCUPANCY_RELEASED", "room_calendar", seg.property_id, occurred_at,
                    f"释放预约 {reservation_id} 占用（{reason}）",
                    payload={"reservation_id": reservation_id, "reason": reason,
                             "kind": OccupancyKind.EXCHANGE.value,
                             "calendar_version": calendar.version},
                )

    def refund(self, reservation: Reservation, amount: int, reason_code: str,
               occurred_at: str, summary: str, refs: dict | None = None) -> None:
        """点数返还：流水与事件都带来源引用，会员可核对。"""
        account = self._contracts.account_of(reservation.member_id)
        merged = {"reservation_id": reservation.reservation_id, **(refs or {})}
        self._points.credit(account, amount, reason_code, occurred_at,
                            refs=merged, summary=summary)
        self._store.emit(
            "POINTS_CREDITED", "points_account", account, occurred_at, summary,
            payload={"amount": amount, "reason_code": reason_code, **merged},
        )

    def mark_checked_in(self, reservation_id: str) -> None:
        self.get(reservation_id).status = ReservationStatus.CHECKED_IN

    def complete(self, reservation_id: str) -> None:
        self.get(reservation_id).status = ReservationStatus.COMPLETED
