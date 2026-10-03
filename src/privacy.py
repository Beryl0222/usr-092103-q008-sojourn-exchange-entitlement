"""同行人与健康偏好的可见范围：仅限本次服务委托，房东看不到会员其他旅程。"""
from __future__ import annotations

from dataclasses import dataclass

from .events import EventStore


@dataclass(frozen=True)
class PartyMember:
    name: str
    relation: str


@dataclass(frozen=True)
class TravelParty:
    party_id: str
    reservation_id: str
    member_id: str
    members: tuple[PartyMember, ...]
    health_preferences: tuple[str, ...]


@dataclass(frozen=True)
class ServiceDelegation:
    delegation_id: str
    reservation_id: str
    property_id: str
    steward: str
    scope: str = "current_stay"  # 数据可见范围仅限本次入住


class PrivacyGuard:
    """服务委托与数据可见范围。房东视图只含被委托的那一次入住。"""

    def __init__(self, store: EventStore) -> None:
        self._store = store
        self._parties: dict[str, TravelParty] = {}       # reservation_id -> party
        self._delegations: dict[str, ServiceDelegation] = {}

    def register_party(self, party: TravelParty, occurred_at: str) -> TravelParty:
        self._parties[party.reservation_id] = party
        self._store.emit(
            "PARTY_REGISTERED", "travel_party", party.party_id, occurred_at,
            f"预约 {party.reservation_id} 登记同行人 {len(party.members)} 人",
            payload={"reservation_id": party.reservation_id,
                     "member_id": party.member_id,
                     "member_count": len(party.members)},
        )
        return party

    def delegate(self, delegation: ServiceDelegation, occurred_at: str) -> ServiceDelegation:
        self._delegations[delegation.delegation_id] = delegation
        self._store.emit(
            "SERVICE_DELEGATED", "service_delegation", delegation.delegation_id, occurred_at,
            f"预约 {delegation.reservation_id} 委托管家 {delegation.steward}",
            payload={"reservation_id": delegation.reservation_id,
                     "property_id": delegation.property_id,
                     "steward": delegation.steward, "scope": delegation.scope},
        )
        return delegation

    def landlord_view(self, delegation_id: str) -> dict:
        """房东/管家视图：仅本次服务范围内的同行人与健康偏好。

        视图按委托定位单次入住，不含会员身份、其他预约或历史旅程。
        """
        try:
            delegation = self._delegations[delegation_id]
        except KeyError:
            raise PermissionError(f"委托 {delegation_id} 不存在，无权查看") from None
        party = self._parties.get(delegation.reservation_id)
        return {
            "property_id": delegation.property_id,
            "reservation_id": delegation.reservation_id,
            "steward": delegation.steward,
            "party": [] if party is None else [
                {"name": m.name, "relation": m.relation} for m in party.members
            ],
            "health_preferences": [] if party is None else list(party.health_preferences),
        }

    def member_view(self, member_id: str) -> list[dict]:
        """会员本人视图：可见自己全部旅程的同行人登记。"""
        return [
            {"reservation_id": p.reservation_id,
             "members": [{"name": m.name, "relation": m.relation} for m in p.members],
             "health_preferences": list(p.health_preferences)}
            for p in self._parties.values() if p.member_id == member_id
        ]
