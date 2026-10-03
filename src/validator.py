"""校验领域事件公共信封与各事件负载。

约定见 contracts/domain.md；信封的 JSON Schema 见 contracts/domain.schema.json。
本模块在信封之上补充：事件类型与聚合的一致性、各事件必填负载、关键取值规则。
"""

from __future__ import annotations

from datetime import datetime

ENVELOPE_REQUIRED = (
    "event_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "occurred_at",
    "version",
    "summary",
)

# 兼容既有引用
REQUIRED = ENVELOPE_REQUIRED

EVENT_AGGREGATE = {
    "PROPERTY_AUTHORIZED": "property_authorization",
    "PROPERTY_AUTHORIZATION_UPDATED": "property_authorization",
    "PROPERTY_AUTHORIZATION_REVOKED": "property_authorization",
    "CALENDAR_BLOCK_REGISTERED": "property_calendar",
    "CALENDAR_BLOCK_RELEASED": "property_calendar",
    "PROPERTY_BLOCKED": "property_calendar",
    "PROPERTY_BLOCK_LIFTED": "property_calendar",
    "MEMBER_CONTRACT_SIGNED": "member_contract",
    "MEMBER_CONTRACT_TERMINATED": "member_contract",
    "ENTITLEMENT_GRANTED": "member_entitlement",
    "POINTS_DEBITED": "member_entitlement",
    "POINTS_REFUNDED": "member_entitlement",
    "POINTS_ADJUSTED": "member_entitlement",
    "RESERVATION_HELD": "stay_reservation",
    "RESERVATION_CONFIRMED": "stay_reservation",
    "RESERVATION_HOLD_RELEASED": "stay_reservation",
    "RESERVATION_CANCELLED": "stay_reservation",
    "RESERVATION_RESCHEDULED": "stay_reservation",
    "RESERVATION_SUBSTITUTED": "stay_reservation",
    "STAY_CHECKED_IN": "stay_reservation",
    "STAY_CHECKED_OUT": "stay_reservation",
    "SERVICE_DELEGATED": "service_delegation",
    "SERVICE_DELEGATION_REVOKED": "service_delegation",
    "COMPENSATION_ISSUED": "compensation_entry",
    "COMPENSATION_FULFILLED": "compensation_entry",
}

PAYLOAD_REQUIRED = {
    "PROPERTY_AUTHORIZED": (
        "property_id", "owner_id", "village_id", "capacity", "season_quotas", "housekeeper_service",
    ),
    "CALENDAR_BLOCK_REGISTERED": ("property_id", "block_id", "block_type", "start_date", "end_date"),
    "CALENDAR_BLOCK_RELEASED": ("property_id", "block_id"),
    "PROPERTY_BLOCKED": ("property_id", "block_id", "reason", "start_date", "end_date"),
    "PROPERTY_BLOCK_LIFTED": ("property_id", "block_id"),
    "MEMBER_CONTRACT_SIGNED": ("contract_id", "member_id", "tier", "cancel_policy", "valid_from", "valid_to"),
    "MEMBER_CONTRACT_TERMINATED": ("contract_id", "member_id"),
    "ENTITLEMENT_GRANTED": ("entitlement_id", "member_id", "contract_id", "annual_points", "period"),
    "POINTS_DEBITED": ("entitlement_id", "amount", "reason_code", "ref_aggregate_id", "balance_after"),
    "POINTS_REFUNDED": ("entitlement_id", "amount", "reason_code", "ref_aggregate_id", "balance_after"),
    "POINTS_ADJUSTED": ("entitlement_id", "amount", "reason_code", "ref_aggregate_id", "balance_after"),
    "RESERVATION_HELD": (
        "reservation_id", "journey_id", "property_id", "member_id",
        "start_date", "end_date", "points_amount", "hold_expires_at", "calendar_version",
    ),
    "RESERVATION_CONFIRMED": (
        "reservation_id", "journey_id", "property_id", "member_id", "start_date", "end_date", "points_amount",
    ),
    "RESERVATION_HOLD_RELEASED": ("reservation_id", "journey_id", "reason"),
    "RESERVATION_CANCELLED": ("reservation_id", "cancel_reason", "liability"),
    "RESERVATION_RESCHEDULED": ("reservation_id", "supersedes_reservation_id", "start_date", "end_date"),
    "RESERVATION_SUBSTITUTED": ("reservation_id", "substitutes_reservation_id", "property_id", "start_date", "end_date"),
    "STAY_CHECKED_IN": (
        "reservation_id", "idempotency_key", "verified_by", "verification_method", "recorded_offline", "captured_at",
    ),
    "STAY_CHECKED_OUT": ("reservation_id", "early_departure", "actual_checkout_date"),
    "SERVICE_DELEGATED": ("delegation_id", "reservation_id", "delegate_id", "scope_start", "scope_end", "visible_fields"),
    "SERVICE_DELEGATION_REVOKED": ("delegation_id",),
    "COMPENSATION_ISSUED": ("compensation_id", "origin_reservation_id", "kind"),
    "COMPENSATION_FULFILLED": ("compensation_id",),
}

BLOCK_TYPES = ("long_rental", "swap_stay", "owner_use")
BLOCK_REASONS = ("maintenance", "natural_disaster")
COMPENSATION_KINDS = ("reschedule", "substitute", "points_refund")
LIABILITIES = ("member", "operator", "force_majeure")
PRIVACY_SCOPES = ("service_only", "member_only", "operations")

POINTS_EVENTS = ("POINTS_DEBITED", "POINTS_REFUNDED", "POINTS_ADJUSTED")


def _is_int(value: object, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _is_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_non_empty_str(value: object) -> bool:
    return isinstance(value, str) and len(value) > 0


def validate_event(record: dict) -> list[str]:
    errors: list[str] = [f"缺少字段：{name}" for name in ENVELOPE_REQUIRED if name not in record]

    event_type = record.get("event_type")
    if event_type is not None:
        expected = EVENT_AGGREGATE.get(event_type)
        if expected is None:
            errors.append(f"未知事件类型：{event_type}")
        elif record.get("aggregate_type") not in (None, expected):
            errors.append(f"事件 {event_type} 的 aggregate_type 应为 {expected}")

    if "version" in record and not _is_int(record["version"], 1):
        errors.append("version 必须是正整数")
    if "occurred_at" in record and not _is_datetime(record["occurred_at"]):
        errors.append("occurred_at 必须是 ISO 8601 日期时间")

    for name in ("event_id", "aggregate_id", "summary", "trace_id", "causation_id"):
        if name in record and not _is_non_empty_str(record[name]):
            errors.append(f"{name} 必须是非空字符串")

    if event_type in PAYLOAD_REQUIRED:
        errors += [f"缺少字段：{name}" for name in PAYLOAD_REQUIRED[event_type] if name not in record]

    errors += _validate_values(event_type, record)
    return errors


def _validate_values(event_type: str | None, record: dict) -> list[str]:
    errors: list[str] = []

    if "start_date" in record or "end_date" in record:
        start, end = record.get("start_date"), record.get("end_date")
        if not _is_date(start) or not _is_date(end):
            errors.append("start_date/end_date 必须是 YYYY-MM-DD 日期")
        elif start >= end:
            errors.append("start_date 必须早于 end_date")

    enum_checks = (
        ("block_type", BLOCK_TYPES),
        ("reason", BLOCK_REASONS if event_type == "PROPERTY_BLOCKED" else None),
        ("kind", COMPENSATION_KINDS),
        ("liability", LIABILITIES),
        ("privacy_scope", PRIVACY_SCOPES),
    )
    for key, allowed in enum_checks:
        if allowed and key in record and record[key] not in allowed:
            errors.append(f"{key} 必须是 {'/'.join(allowed)} 之一")

    for key in ("capacity", "points_amount", "annual_points", "calendar_version"):
        if key in record and not _is_int(record[key], 1):
            errors.append(f"{key} 必须是正整数")
    if event_type in POINTS_EVENTS:
        if "amount" in record and not _is_int(record["amount"], 1):
            errors.append("amount 必须是正整数")
        if "balance_after" in record and not _is_int(record["balance_after"], 0):
            errors.append("balance_after 必须是非负整数")

    for key in ("recorded_offline", "early_departure"):
        if key in record and not isinstance(record[key], bool):
            errors.append(f"{key} 必须是布尔值")
    if "captured_at" in record and not _is_datetime(record["captured_at"]):
        errors.append("captured_at 必须是 ISO 8601 日期时间")
    if "hold_expires_at" in record and not _is_datetime(record["hold_expires_at"]):
        errors.append("hold_expires_at 必须是 ISO 8601 日期时间")

    return errors
