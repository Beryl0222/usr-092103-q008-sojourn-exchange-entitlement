"""房源授权登记：房屋归属、可住人数、淡旺季额度与管家服务。"""
from __future__ import annotations

from dataclasses import dataclass

from .events import EventStore


@dataclass(frozen=True)
class PropertyAuthorization:
    authorization_id: str
    property_id: str
    village: str
    owner_id: str                 # 房屋归属：当前房源由谁提供
    capacity: int                 # 可住人数
    season_quota: dict            # 淡旺季额度，如 {"peak": 30, "off_peak": 60}
    steward_services: tuple[str, ...]  # 管家服务项目


class AuthorizationRegistry:
    def __init__(self, store: EventStore) -> None:
        self._store = store
        self._by_property: dict[str, PropertyAuthorization] = {}

    def authorize(self, auth: PropertyAuthorization, occurred_at: str) -> PropertyAuthorization:
        self._by_property[auth.property_id] = auth
        self._store.emit(
            "PROPERTY_AUTHORIZED", "property_authorization", auth.authorization_id, occurred_at,
            f"房源 {auth.property_id} 授权入会，归属 {auth.owner_id}",
            payload={"property_id": auth.property_id, "village": auth.village,
                     "owner_id": auth.owner_id, "capacity": auth.capacity,
                     "season_quota": dict(auth.season_quota),
                     "steward_services": list(auth.steward_services)},
        )
        return auth

    def provider_of(self, property_id: str) -> str:
        """会员可核对：当前房源由谁提供。"""
        return self._by_property[property_id].owner_id
