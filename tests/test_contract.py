import json
import unittest
from pathlib import Path

from src.validator import validate_event

DATA_DIR = Path(__file__).parents[1] / "data"


class ContractTest(unittest.TestCase):
    def test_samples_match_envelope(self) -> None:
        for path in sorted(DATA_DIR.glob("*.json")):
            sample = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(validate_event(sample), [], f"样例 {path.name} 未通过校验")

    def test_event_must_belong_to_aggregate(self) -> None:
        record = {
            "event_id": "evt-x",
            "event_type": "STAY_CHECKED_IN",
            "aggregate_type": "stay_reservation",
            "aggregate_id": "r-x",
            "occurred_at": "2026-10-01T10:00:00+08:00",
            "version": 1,
            "summary": "入住事件挂到了预约聚合上",
        }
        self.assertIn("聚合 stay_reservation 不允许事件 STAY_CHECKED_IN", validate_event(record))

    def test_unknown_aggregate_rejected(self) -> None:
        record = {
            "event_id": "evt-y",
            "event_type": "PROPERTY_AUTHORIZED",
            "aggregate_type": "unknown_aggregate",
            "aggregate_id": "x",
            "occurred_at": "2026-10-01T10:00:00+08:00",
            "version": 1,
            "summary": "未知聚合",
        }
        self.assertIn("未知聚合类型：unknown_aggregate", validate_event(record))


if __name__ == "__main__":
    unittest.main()
