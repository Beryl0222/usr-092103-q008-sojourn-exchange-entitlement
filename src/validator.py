"""校验领域事件公共字段，以及聚合类型与事件类型的归属关系。"""

REQUIRED = ("event_id", "event_type", "aggregate_type", "aggregate_id", "occurred_at", "version", "summary")

EVENTS_BY_AGGREGATE = {
    "property_authorization": {"PROPERTY_AUTHORIZED"},
    "room_calendar": {"OCCUPANCY_RESERVED", "OCCUPANCY_RELEASED"},
    "member_contract": {"CONTRACT_SIGNED"},
    "member_entitlement": {"ENTITLEMENT_GRANTED"},
    "points_account": {"POINTS_CREDITED", "POINTS_DEBITED"},
    "travel_party": {"PARTY_REGISTERED"},
    "stay_reservation": {"RESERVATION_CONFIRMED", "RESERVATION_CANCELLED", "RESERVATION_RESCHEDULED"},
    "check_in_record": {"CHECK_IN_RECORDED", "STAY_CHECKED_IN"},
    "service_delegation": {"SERVICE_DELEGATED"},
    "maintenance_block": {"MAINTENANCE_BLOCKED"},
    "compensation_entry": {"COMPENSATION_ISSUED"},
}


def validate_event(record: dict) -> list[str]:
    errors = [f"缺少字段：{name}" for name in REQUIRED if name not in record]
    if "version" in record and (not isinstance(record["version"], int) or record["version"] < 1):
        errors.append("version 必须是正整数")
    aggregate_type = record.get("aggregate_type")
    event_type = record.get("event_type")
    if aggregate_type is not None:
        allowed = EVENTS_BY_AGGREGATE.get(aggregate_type)
        if allowed is None:
            errors.append(f"未知聚合类型：{aggregate_type}")
        elif event_type is not None and event_type not in allowed:
            errors.append(f"聚合 {aggregate_type} 不允许事件 {event_type}")
    return errors
