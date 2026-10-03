import unittest
from datetime import date

from src.availability import Calendars, OccupancyKind, OverlapError
from src.checkin import CheckInService
from src.compensation import CompensationService
from src.contracts import ContractRegistry, MemberContract
from src.events import EventStore
from src.maintenance import MaintenanceBlock, MaintenanceService
from src.points import PointsLedger
from src.privacy import PartyMember, PrivacyGuard, ServiceDelegation, TravelParty
from src.registry import AuthorizationRegistry, PropertyAuthorization
from src.reservations import (
    AtomicConfirmError,
    ExchangeService,
    ReservationStatus,
    Segment,
)
from src.trace import trace_exchange

T0 = "2026-10-01T10:00:00+08:00"
T1 = "2026-10-02T10:00:00+08:00"
T2 = "2026-10-03T10:00:00+08:00"
T3 = "2026-10-04T10:00:00+08:00"


def make_world():
    store = EventStore()
    points = PointsLedger()
    contracts = ContractRegistry(store, points)
    calendars = Calendars()
    exchange = ExchangeService(store, calendars, points, contracts)
    return store, points, contracts, calendars, exchange


def sign(contracts, member_id="m-1", annual_points=1000):
    return contracts.sign(MemberContract(f"c-{member_id}", member_id, annual_points), T0)


class OccupancyTest(unittest.TestCase):
    def test_kinds_do_not_overlap(self):
        calendars = Calendars()
        calendar = calendars.for_property("p-1")
        calendar.place(OccupancyKind.LONG_TERM, date(2026, 1, 1), date(2026, 6, 1), "lease-1")
        with self.assertRaises(OverlapError):
            calendar.place(OccupancyKind.EXCHANGE, date(2026, 3, 1), date(2026, 3, 8), "r-1")
        with self.assertRaises(OverlapError):
            calendar.place(OccupancyKind.OWNER_SELF, date(2026, 5, 31), date(2026, 6, 2), "owner-1")
        with self.assertRaises(OverlapError):
            calendar.place(OccupancyKind.MAINTENANCE, date(2026, 1, 1), date(2026, 1, 2), "mb-1")
        # 半开区间：退房当天即可被下一段占用
        calendar.place(OccupancyKind.EXCHANGE, date(2026, 6, 1), date(2026, 6, 8), "r-2")
        self.assertEqual(calendar.version, 2)


class AtomicConfirmTest(unittest.TestCase):
    def test_failed_segment_rolls_back_everything(self):
        store, points, contracts, calendars, exchange = make_world()
        sign(contracts)
        calendars.for_property("p-b").place(
            OccupancyKind.OWNER_SELF, date(2026, 11, 3), date(2026, 11, 10), "owner-x")
        segments = [
            Segment("p-a", "村甲", date(2026, 11, 1), date(2026, 11, 5), 100),
            Segment("p-b", "村乙", date(2026, 11, 5), date(2026, 11, 9), 100),
        ]
        with self.assertRaises(AtomicConfirmError):
            exchange.confirm("r-1", "m-1", segments, T1)
        # 任何村落都不落占用、不扣点、不发事件
        self.assertEqual(calendars.for_property("p-a").occupancies(), ())
        self.assertEqual(points.balance("points:m-1"), 1000)
        self.assertEqual(store.of_type("RESERVATION_CONFIRMED"), [])
        self.assertEqual(store.of_type("POINTS_DEBITED"), [])

    def test_insufficient_points_blocks_whole_booking(self):
        store, points, contracts, calendars, exchange = make_world()
        sign(contracts, annual_points=50)
        segments = [Segment("p-a", "村甲", date(2026, 11, 1), date(2026, 11, 5), 100)]
        with self.assertRaises(AtomicConfirmError):
            exchange.confirm("r-1", "m-1", segments, T1)
        self.assertEqual(calendars.for_property("p-a").occupancies(), ())
        self.assertEqual(points.balance("points:m-1"), 50)

    def test_successful_cross_village_confirm(self):
        store, points, contracts, calendars, exchange = make_world()
        sign(contracts)
        segments = [
            Segment("p-a", "村甲", date(2026, 11, 1), date(2026, 11, 5), 100),
            Segment("p-b", "村乙", date(2026, 11, 5), date(2026, 11, 9), 200),
        ]
        reservation = exchange.confirm("r-1", "m-1", segments, T1, idempotency_key="req-1")
        self.assertEqual(reservation.status, ReservationStatus.CONFIRMED)
        self.assertEqual(len(calendars.for_property("p-a").occupancies()), 1)
        self.assertEqual(len(calendars.for_property("p-b").occupancies()), 1)
        self.assertEqual(points.balance("points:m-1"), 700)
        # 幂等键重复提交返回原预约，不重复扣点
        again = exchange.confirm("r-1", "m-1", segments, T1, idempotency_key="req-1")
        self.assertIs(again, reservation)
        self.assertEqual(points.balance("points:m-1"), 700)


class CheckInTest(unittest.TestCase):
    def test_offline_replay_is_deduplicated(self):
        store, points, contracts, calendars, exchange = make_world()
        sign(contracts)
        exchange.confirm("r-1", "m-1",
                         [Segment("p-a", "村甲", date(2026, 11, 1), date(2026, 11, 5), 100)], T1)
        checkins = CheckInService(store, exchange)
        # 离线办理，恢复联网后同一条记录重放
        first = checkins.record("r-1", "req-9", "steward-a", T2, offline=True)
        replay = checkins.record("r-1", "req-9", "steward-a", T3, offline=True)
        self.assertIs(first, replay)
        verified = checkins.verify("req-9", T3)
        again = checkins.verify("req-9", T3)
        self.assertIs(verified, again)
        self.assertEqual(len(store.of_type("CHECK_IN_RECORDED")), 1)
        self.assertEqual(len(store.of_type("STAY_CHECKED_IN")), 1)
        self.assertEqual(exchange.get("r-1").status, ReservationStatus.CHECKED_IN)


class CompensationTest(unittest.TestCase):
    def setUp(self):
        self.store, self.points, self.contracts, self.calendars, self.exchange = make_world()
        sign(self.contracts)
        self.exchange.confirm(
            "r-1", "m-1",
            [Segment("p-a", "村甲", date(2026, 11, 1), date(2026, 11, 5), 100)], T1)
        self.exchange.confirm(
            "r-2", "m-1",
            [Segment("p-b", "村乙", date(2026, 11, 1), date(2026, 11, 5), 200)], T1)
        self.maintenance = MaintenanceService(self.store, self.calendars)
        self.compensation = CompensationService(
            self.store, self.exchange, self.points, self.contracts)

    def test_maintenance_only_affects_its_property_and_dates(self):
        block = MaintenanceBlock("mb-1", "p-a", date(2026, 11, 2), date(2026, 11, 4), "maintenance")
        affected = self.maintenance.affected_reservations(block.property_id, block.start, block.end)
        self.assertEqual(affected, ["r-1"])  # 其他房源的 r-2 不受影响
        # 未补偿前直接封房会与在住占用冲突
        with self.assertRaises(OverlapError):
            self.maintenance.block(block, T2)
        for reservation_id in affected:
            self.compensation.disrupt(reservation_id, "maintenance", T2,
                                      new_dates=(date(2026, 12, 1), date(2026, 12, 5)))
        self.maintenance.block(block, T2)
        calendar = self.calendars.for_property("p-a")
        kinds = sorted(o.kind for o in calendar.occupancies())
        self.assertEqual(kinds, [OccupancyKind.EXCHANGE, OccupancyKind.MAINTENANCE])
        # r-2 的占用原样保留
        self.assertEqual(self.exchange.get("r-2").status, ReservationStatus.CONFIRMED)
        self.assertEqual(len(self.calendars.for_property("p-b").occupancies()), 1)

    def test_compensation_does_not_overwrite_original(self):
        entry = self.compensation.disrupt(
            "r-1", "maintenance", T2, new_dates=(date(2026, 12, 1), date(2026, 12, 5)))
        self.assertEqual(entry.kind, "reschedule")
        original = self.exchange.get("r-1")
        self.assertEqual(original.status, ReservationStatus.DISRUPTED)
        # 原预约的房源与日期保持不变
        segment = original.segments[0]
        self.assertEqual((segment.property_id, segment.check_in, segment.check_out),
                         ("p-a", date(2026, 11, 1), date(2026, 11, 5)))
        # 改期生成新的关联预约
        rebooked = self.exchange.get("r-1-re")
        self.assertEqual(rebooked.segments[0].check_in, date(2026, 12, 1))
        self.assertEqual(rebooked.links["original_reservation"], "r-1")
        self.assertEqual(rebooked.links["compensation_id"], entry.compensation_id)
        # 点数先返还再扣减，总额不多扣
        self.assertEqual(self.points.balance("points:m-1"), 700)
        reasons = [e.reason_code for e in self.points.statement("points:m-1")]
        self.assertIn("disruption_transfer", reasons)

    def test_points_refund_when_no_option_available(self):
        entry = self.compensation.disrupt("r-1", "natural_disaster", T2)
        self.assertEqual(entry.kind, "points_refund")
        self.assertEqual(self.points.balance("points:m-1"), 800)
        last = self.points.statement("points:m-1")[-1]
        self.assertEqual(last.reason_code, "disruption_refund")
        self.assertEqual(last.refs["reservation_id"], "r-1")


class EarlyDepartureTest(unittest.TestCase):
    def test_unused_nights_released_and_refunded(self):
        store, points, contracts, calendars, exchange = make_world()
        sign(contracts)
        exchange.confirm("r-1", "m-1",
                         [Segment("p-a", "村甲", date(2026, 11, 1), date(2026, 11, 5), 100)], T1)
        exchange.early_departure("r-1", date(2026, 11, 3), T2)
        occupancy = calendars.for_property("p-a").occupancies()[0]
        self.assertEqual(occupancy.end, date(2026, 11, 3))  # 尾部日期已释放
        # 未住 2 晚 × 每晚 25 点 × 合同返还比例 0.5 = 25 点
        self.assertEqual(points.balance("points:m-1"), 925)
        last = points.statement("points:m-1")[-1]
        self.assertEqual(last.reason_code, "early_departure_refund")
        self.assertEqual(last.refs["reservation_id"], "r-1")


class PrivacyTest(unittest.TestCase):
    def test_landlord_sees_only_delegated_stay(self):
        store = EventStore()
        guard = PrivacyGuard(store)
        guard.register_party(TravelParty(
            "party-1", "r-1", "m-1",
            (PartyMember("张三", "配偶"),), ("低糖饮食",)), T1)
        guard.register_party(TravelParty(
            "party-2", "r-2", "m-1",
            (PartyMember("张母", "母亲"),), ("无障碍通道",)), T1)
        guard.delegate(ServiceDelegation("d-1", "r-1", "p-a", "steward-a"), T1)
        view = guard.landlord_view("d-1")
        self.assertEqual([m["name"] for m in view["party"]], ["张三"])
        self.assertEqual(view["health_preferences"], ["低糖饮食"])
        # 房东看不到会员其他旅程
        self.assertNotIn("张母", str(view))
        self.assertNotIn("r-2", str(view))
        with self.assertRaises(PermissionError):
            guard.landlord_view("d-404")
        # 会员本人可见全部旅程
        self.assertEqual(len(guard.member_view("m-1")), 2)


class PointsStatementTest(unittest.TestCase):
    def test_statement_explains_every_change(self):
        store, points, contracts, calendars, exchange = make_world()
        sign(contracts)
        exchange.confirm("r-1", "m-1",
                         [Segment("p-a", "村甲", date(2026, 12, 1), date(2026, 12, 5), 100)], T1)
        exchange.cancel("r-1", date(2026, 11, 1), T2)  # 提前 30 天，全额返还
        statement = points.statement("points:m-1")
        self.assertEqual([e.reason_code for e in statement],
                         ["annual_grant", "reservation_confirm", "cancel_refund"])
        self.assertEqual([e.delta for e in statement], [1000, -100, 100])
        self.assertEqual(points.balance("points:m-1"), 1000)
        for entry in statement[1:]:
            self.assertEqual(entry.refs["reservation_id"], "r-1")


class TraceTest(unittest.TestCase):
    def test_full_chain_traceable(self):
        store, points, contracts, calendars, exchange = make_world()
        authorizations = AuthorizationRegistry(store)
        authorizations.authorize(PropertyAuthorization(
            "auth-1", "p-a", "村甲", "owner-1", 4,
            {"peak": 30, "off_peak": 60}, ("接送", "保洁")), T0)
        sign(contracts)
        exchange.confirm("r-1", "m-1",
                         [Segment("p-a", "村甲", date(2026, 11, 1), date(2026, 11, 5), 100)], T1)
        CheckInService(store, exchange).record("r-1", "req-9", "steward-a", T2)
        compensation = CompensationService(store, exchange, points, contracts)
        compensation.disrupt("r-1", "maintenance", T3)
        # 会员可核对当前房源由谁提供
        self.assertEqual(authorizations.provider_of("p-a"), "owner-1")
        # 运营方从一次换房追到授权、占用、实际入住与补偿
        types = [event.event_type for event in trace_exchange(store, "r-1")]
        for expected in ("PROPERTY_AUTHORIZED", "RESERVATION_CONFIRMED", "OCCUPANCY_RESERVED",
                         "STAY_CHECKED_IN", "POINTS_DEBITED", "POINTS_CREDITED",
                         "COMPENSATION_ISSUED"):
            self.assertIn(expected, types)


if __name__ == "__main__":
    unittest.main()
