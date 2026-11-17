import json
import unittest
from datetime import date
from pathlib import Path

from app.reconciliation import (
    MISSING_SETTLEMENT_RULE, SourceRow, aware_time, find_missing_settlements, load_rows,
)

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data/sample"
AS_OF = aware_time("2026-11-17T09:00:00+08:00")


class MissingSettlementTests(unittest.TestCase):
    def payment(self, expected="2026-11-16", status="SUCCESS"):
        return SourceRow({
            "payment_id": "P1", "order_id": "O1", "status": status,
            "paid_at": "2026-11-13T10:00:00+08:00" if status == "SUCCESS" else "",
            "expected_settlement_date": expected,
        }, "payments.csv", 4)

    def run_rule(self, expected="2026-11-16", status="SUCCESS", settlements=None,
                 coverage=date(2026, 11, 16), as_of=AS_OF):
        return find_missing_settlements(
            [self.payment(expected, status)], settlements or [],
            as_of=as_of, complete_through=coverage,
        )

    def test_overdue_missing_payment_and_absence_evidence(self):
        case = self.run_rule()[0]
        self.assertEqual(case["payment_id"], "P1")
        self.assertEqual(case["evidence"][0]["line"], 4)
        absence = case["evidence"][1]
        self.assertEqual(absence["lookup_value"], "P1")
        self.assertEqual(absence["matching_rows"], 0)
        self.assertEqual(absence["complete_through"], "2026-11-16")

    def test_existing_settlement_is_not_missing(self):
        settlement = SourceRow({"settlement_id": "S1", "payment_id": "P1", "settlement_date": "2026-11-16"}, "settlements.csv", 2)
        self.assertEqual(self.run_rule(settlements=[settlement]), [])

    def test_future_due_date_is_not_reported(self):
        self.assertEqual(self.run_rule(expected="2026-11-18"), [])

    def test_due_today_is_not_reported(self):
        self.assertEqual(self.run_rule(expected="2026-11-17"), [])

    def test_failed_payment_is_not_reported(self):
        self.assertEqual(self.run_rule(expected="", status="FAILED"), [])

    def test_insufficient_coverage_does_not_claim_no_anomalies(self):
        with self.assertRaisesRegex(ValueError, "資料不足"):
            self.run_rule(coverage=date(2026, 11, 15))

    def test_missing_coverage_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "缺少結算資料完整"):
            self.run_rule(coverage=None)

    def test_today_or_future_coverage_is_rejected(self):
        for coverage in [date(2026, 11, 17), date(2026, 11, 18)]:
            with self.subTest(coverage=coverage), self.assertRaisesRegex(ValueError, "尚未結束"):
                self.run_rule(coverage=coverage)

    def test_timezone_uses_taipei_day(self):
        # UTC 尚為 11/16，但台北已是 11/17；11/16 已完整結束。
        self.assertEqual(len(self.run_rule(as_of=aware_time("2026-11-16T16:00:00+00:00"))), 1)

    def test_due_date_before_payment_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "早於付款日"):
            self.run_rule(expected="2026-11-12")

    def test_invalid_due_date_is_rejected(self):
        with self.assertRaises(ValueError):
            self.run_rule(expected="not-a-date")

    def test_naive_baseline_is_rejected(self):
        from datetime import datetime
        with self.assertRaisesRegex(ValueError, "須含時區"):
            self.run_rule(as_of=datetime(2026, 11, 17, 9))

    def test_sample_matches_independent_expected_answer(self):
        manifest = json.loads((SAMPLE_DIR / "manifest.json").read_text())
        payments = load_rows(SAMPLE_DIR / "payments.csv", {"payment_id", "order_id", "status", "paid_at", "expected_settlement_date"}, "payment_id")
        settlements = load_rows(SAMPLE_DIR / "settlements.csv", {"settlement_id", "payment_id", "settlement_date"}, "settlement_id")
        cases = find_missing_settlements(payments, settlements, as_of=AS_OF, complete_through=date(2026, 11, 16))
        expected = [c for c in manifest["expected_cases"] if c["rule"] == MISSING_SETTLEMENT_RULE]
        self.assertEqual([{k: c[k] for k in ("rule", "order_id", "payment_id")} for c in cases], expected)
        self.assertEqual(cases[0]["evidence"][1]["rows_scanned"], 4)


if __name__ == "__main__":
    unittest.main()
