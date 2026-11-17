import json
import unittest
from pathlib import Path

from app.reconciliation import SourceRow, aware_time, find_unpaid_orders, load_rows

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample"


class UnpaidOrderTests(unittest.TestCase):
    def check_case(self, *, snapshot="2026-11-17T08:30:00+08:00", status="UNPAID", payment_status="SUCCESS", paid="2026-11-17T08:00:00+08:00"):
        orders = [SourceRow({"order_id": "O1", "payment_status": status, "snapshot_at": snapshot}, "orders.csv", 2)]
        payments = [SourceRow({"order_id": "O1", "payment_id": "P1", "status": payment_status, "paid_at": paid}, "payments.csv", 2)]
        return find_unpaid_orders(orders, payments, as_of=aware_time("2026-11-17T09:00:00+08:00"))

    def test_sample_matches_independent_expected_answer(self):
        manifest = json.loads((SAMPLE_DIR / "manifest.json").read_text())
        orders = load_rows(SAMPLE_DIR / "orders.csv", {"order_id", "payment_status", "snapshot_at"}, "order_id")
        payments = load_rows(SAMPLE_DIR / "payments.csv", {"payment_id", "order_id", "status", "paid_at"}, "payment_id")
        cases = find_unpaid_orders(orders, payments, as_of=aware_time(manifest["as_of"]))
        expected = [c for c in manifest["expected_cases"] if c["rule"] == "PAID_BUT_ORDER_UNPAID"]
        self.assertEqual([{k: c[k] for k in ("rule", "order_id", "payment_id")} for c in cases], expected)
        self.assertEqual([(e["file"], e["line"]) for e in cases[0]["evidence"]], [("orders.csv", 2), ("payments.csv", 2)])

    def test_paid_order_is_not_reported(self):
        self.assertEqual(self.check_case(status="PAID"), [])

    def test_failed_payment_is_not_reported(self):
        self.assertEqual(self.check_case(payment_status="FAILED", paid=""), [])

    def test_old_snapshot_is_not_reported_even_if_now_is_later(self):
        self.assertEqual(self.check_case(snapshot="2026-11-17T07:50:00+08:00"), [])

    def test_within_grace_period_is_not_reported(self):
        self.assertEqual(self.check_case(snapshot="2026-11-17T08:14:59+08:00"), [])

    def test_exact_grace_boundary_is_reported(self):
        self.assertEqual(len(self.check_case(snapshot="2026-11-17T08:15:00+08:00")), 1)

    def test_equivalent_utc_time(self):
        self.assertEqual(len(self.check_case(paid="2026-11-17T00:00:00+00:00")), 1)

    def test_future_snapshot_is_rejected(self):
        with self.assertRaises(ValueError):
            self.check_case(snapshot="2026-11-17T10:00:00+08:00")

    def test_naive_time_is_rejected(self):
        with self.assertRaises(ValueError):
            self.check_case(paid="2026-11-17T08:00:00")


if __name__ == "__main__":
    unittest.main()
