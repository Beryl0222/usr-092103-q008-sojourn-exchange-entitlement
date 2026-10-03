import json
import unittest
from pathlib import Path

from src.validator import validate_event

ROOT = Path(__file__).parents[1]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ContractTest(unittest.TestCase):
    def test_sample_matches_envelope(self) -> None:
        self.assertEqual(validate_event(load(ROOT / "data" / "sample.json")), [])

    def test_journey_samples_are_valid(self) -> None:
        for path in sorted((ROOT / "data" / "samples").glob("*.json")):
            with self.subTest(sample=path.name):
                self.assertEqual(validate_event(load(path)), [])

    def test_rejects_unknown_event_type(self) -> None:
        record = load(ROOT / "data" / "sample.json")
        record["event_type"] = "POINTS_MELTED"
        self.assertIn("未知事件类型：POINTS_MELTED", validate_event(record))

    def test_rejects_aggregate_mismatch(self) -> None:
        record = load(ROOT / "data" / "sample.json")
        record["aggregate_type"] = "stay_reservation"
        self.assertTrue(any("aggregate_type" in e for e in validate_event(record)))

    def test_rejects_bad_version(self) -> None:
        record = load(ROOT / "data" / "sample.json")
        record["version"] = 0
        self.assertIn("version 必须是正整数", validate_event(record))

    def test_check_in_requires_idempotency_key(self) -> None:
        record = load(ROOT / "data" / "samples" / "11-stay-checked-in.json")
        del record["idempotency_key"]
        self.assertIn("缺少字段：idempotency_key", validate_event(record))

    def test_rejects_inverted_dates(self) -> None:
        record = load(ROOT / "data" / "samples" / "02-calendar-block-registered.json")
        record["start_date"], record["end_date"] = record["end_date"], record["start_date"]
        self.assertIn("start_date 必须早于 end_date", validate_event(record))

    def test_rejects_non_positive_points(self) -> None:
        record = load(ROOT / "data" / "samples" / "13-points-refunded.json")
        record["amount"] = -300
        self.assertIn("amount 必须是正整数", validate_event(record))


if __name__ == "__main__":
    unittest.main()
