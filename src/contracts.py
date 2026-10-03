"""会员合同：年度点数、取消与中断责任规则，以及签约登记。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .events import EventStore
from .points import PointsLedger


@dataclass(frozen=True)
class CancelPolicy:
    free_cancel_days: int = 7         # 入住前多少天取消全额返还
    late_refund_ratio: float = 0.5    # 超过免费取消期后的返还比例
    early_departure_ratio: float = 0.5  # 提前离开时未住晚数的返还比例


@dataclass(frozen=True)
class MemberContract:
    contract_id: str
    member_id: str
    annual_points: int
    # 维修/自然灾害中断时的补偿偏好顺序
    disruption_preferences: tuple[str, ...] = ("alternative", "reschedule", "points_refund")
    cancel_policy: CancelPolicy = field(default_factory=CancelPolicy)


class ContractRegistry:
    def __init__(self, store: EventStore, points: PointsLedger) -> None:
        self._store = store
        self._points = points
        self._by_member: dict[str, MemberContract] = {}

    @staticmethod
    def account_of(member_id: str) -> str:
        return f"points:{member_id}"

    def sign(self, contract: MemberContract, occurred_at: str) -> MemberContract:
        self._by_member[contract.member_id] = contract
        self._store.emit(
            "CONTRACT_SIGNED", "member_contract", contract.contract_id, occurred_at,
            f"会员 {contract.member_id} 签约，年度点数 {contract.annual_points}",
            payload={"member_id": contract.member_id, "annual_points": contract.annual_points},
        )
        self._store.emit(
            "ENTITLEMENT_GRANTED", "member_entitlement", contract.member_id, occurred_at,
            "换住权益开通",
            payload={"contract_id": contract.contract_id},
        )
        self._points.grant(
            self.account_of(contract.member_id), contract.annual_points, occurred_at,
            refs={"contract_id": contract.contract_id},
        )
        return contract

    def of_member(self, member_id: str) -> MemberContract:
        try:
            return self._by_member[member_id]
        except KeyError:
            raise KeyError(f"会员 {member_id} 未签约") from None
