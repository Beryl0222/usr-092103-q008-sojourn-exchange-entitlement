"""退改责任：维修或自然灾害中断预约时，按合同生成改期、替代房或点数返还。

原预约的房源与日期保持不变（状态记为 disrupted）；改期与替代房都生成
新的关联预约，点数在流水中先返还再扣减，全程可核对，绝不覆盖原预约。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .contracts import ContractRegistry
from .events import EventStore
from .points import PointsLedger
from .reservations import ExchangeService, ReservationStatus, Segment


@dataclass(frozen=True)
class CompensationEntry:
    compensation_id: str
    reservation_id: str
    kind: str        # alternative / reschedule / points_refund
    cause: str       # maintenance / natural_disaster
    outcome_id: str  # 新预约号；点数返还时为原预约号
    summary: str


class CompensationService:
    def __init__(self, store: EventStore, exchange: ExchangeService,
                 points: PointsLedger, contracts: ContractRegistry) -> None:
        self._store = store
        self._exchange = exchange
        self._points = points
        self._contracts = contracts
        self._seq = 0

    def disrupt(self, reservation_id: str, cause: str, occurred_at: str,
                alternative: Segment | None = None,
                new_dates: tuple[date, date] | None = None) -> CompensationEntry:
        """中断预约：释放原占用，按合同偏好顺序补偿，原预约不被覆盖。"""
        reservation = self._exchange.get(reservation_id)
        contract = self._contracts.of_member(reservation.member_id)
        self._seq += 1
        compensation_id = f"comp-{self._seq:04d}"
        affected_points = sum(s.points for s in reservation.segments)

        self._exchange.release(reservation_id, occurred_at, reason=cause)
        reservation.status = ReservationStatus.DISRUPTED

        kind = "points_refund"
        outcome_id = reservation_id
        summary = ""
        handled = False
        for preference in contract.disruption_preferences:
            if preference == "alternative" and alternative is not None:
                outcome_id = f"{reservation_id}-alt"
                self._transfer_and_rebook(reservation, compensation_id, cause,
                                          [alternative], outcome_id, occurred_at)
                kind, summary = "alternative", f"替代房 {alternative.property_id}（{outcome_id}）"
                handled = True
                break
            if preference == "reschedule" and new_dates is not None:
                start, end = new_dates
                outcome_id = f"{reservation_id}-re"
                new_segments = [Segment(s.property_id, s.village, start, end, s.points)
                                for s in reservation.segments]
                self._transfer_and_rebook(reservation, compensation_id, cause,
                                          new_segments, outcome_id, occurred_at)
                kind, summary = "reschedule", f"改期 {start}~{end}（{outcome_id}）"
                handled = True
                break
        if not handled:
            self._exchange.refund(reservation, affected_points, "disruption_refund",
                                  occurred_at, f"{cause} 中断，返还 {affected_points} 点",
                                  refs={"compensation_id": compensation_id})
            summary = f"返还 {affected_points} 点"
        elif kind == "reschedule":
            self._store.emit(
                "RESERVATION_RESCHEDULED", "stay_reservation", reservation_id, occurred_at,
                f"原预约改期至 {outcome_id}",
                payload={"new_reservation_id": outcome_id,
                         "compensation_id": compensation_id},
            )
        self._store.emit(
            "COMPENSATION_ISSUED", "compensation_entry", compensation_id, occurred_at,
            f"预约 {reservation_id} 因{cause}补偿：{kind}",
            payload={"reservation_id": reservation_id, "kind": kind, "cause": cause,
                     "outcome_id": outcome_id, "member_id": reservation.member_id},
        )
        return CompensationEntry(compensation_id, reservation_id, kind, cause,
                                 outcome_id, summary)

    def _transfer_and_rebook(self, reservation, compensation_id: str, cause: str,
                             segments: list[Segment], new_id: str, occurred_at: str) -> None:
        """先返还原预约点数，再以新预约扣点：流水上可见点数转移，不重复扣费。"""
        affected_points = sum(s.points for s in reservation.segments)
        self._exchange.refund(reservation, affected_points, "disruption_transfer",
                              occurred_at, f"{cause} 中断，点数转入 {new_id}",
                              refs={"compensation_id": compensation_id,
                                    "new_reservation_id": new_id})
        self._exchange.confirm(new_id, reservation.member_id, segments, occurred_at,
                               links={"original_reservation": reservation.reservation_id,
                                      "compensation_id": compensation_id, "cause": cause})
