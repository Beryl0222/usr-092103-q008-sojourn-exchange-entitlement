"""全链路追溯：从一次换房追到授权、占用、实际入住与补偿全过程。"""
from __future__ import annotations

from .events import Event, EventStore

_CHAIN_TYPES = (
    "OCCUPANCY_RESERVED", "OCCUPANCY_RELEASED",
    "CHECK_IN_RECORDED", "STAY_CHECKED_IN",
    "POINTS_DEBITED", "POINTS_CREDITED",
    "COMPENSATION_ISSUED",
)


def trace_exchange(store: EventStore, reservation_id: str) -> list[Event]:
    """按时间顺序还原一次换房的全过程事件。"""
    chain = list(store.of_aggregate("stay_reservation", reservation_id))
    property_ids = {
        segment["property_id"]
        for event in chain
        for segment in event.payload.get("segments", [])
    }
    seen = {id(event) for event in chain}
    for event in store.all():
        if id(event) in seen:
            continue
        payload = event.payload
        if event.event_type == "PROPERTY_AUTHORIZED" and payload.get("property_id") in property_ids:
            chain.append(event)
        elif event.event_type in _CHAIN_TYPES and payload.get("reservation_id") == reservation_id:
            chain.append(event)
    return sorted(chain, key=lambda e: (e.occurred_at, e.aggregate_type, e.version))
