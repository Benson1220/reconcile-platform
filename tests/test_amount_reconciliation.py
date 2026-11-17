import json
import subprocess
import sys
import unittest
from pathlib import Path

from app.reconciliation import SourceRow, aware_time, find_amount_mismatches, parse_money

PROJECT_DIR = Path(__file__).resolve().parents[1]
AS_OF = aware_time("2026-11-17T09:00:00+08:00")


class AmountTests(unittest.TestCase):
    def rows(self, order_amount="1500.00", payment_amount="1400.00", status="SUCCESS", currency="TWD"):
        order = SourceRow({
            "order_id": "O1", "amount": order_amount, "currency": "TWD",
            "snapshot_at": "2026-11-17T09:00:00+08:00",
        }, "orders.csv", 3)
        payment = SourceRow({
            "payment_id": "P1", "order_id": "O1", "amount": payment_amount,
            "currency": currency, "status": status,
            "paid_at": "2026-11-13T10:10:00+08:00",
        }, "payments.csv", 3)
        return [order], [payment]

    def run_rule(self, **kwargs):
        orders, payments = self.rows(**kwargs)
        return find_amount_mismatches(orders, payments, as_of=AS_OF)

    def test_underpayment_and_evidence(self):
        case = self.run_rule()[0]
        self.assertEqual(case["difference"], "-100.00")
        self.assertEqual(case["order_amount"], "1500.00")
        self.assertEqual(case["payment_amount"], "1400.00")
        self.assertEqual([(e["file"], e["line"]) for e in case["evidence"]], [("orders.csv", 3), ("payments.csv", 3)])

    def test_overpayment(self):
        self.assertEqual(self.run_rule(payment_amount="1600.00")[0]["difference"], "100.00")

    def test_equivalent_amount_formats(self):
        self.assertEqual(self.run_rule(order_amount="1500", payment_amount="1500.0"), [])

    def test_cent_difference_is_exact(self):
        self.assertEqual(self.run_rule(order_amount="0.30", payment_amount="0.29")[0]["difference"], "-0.01")

    def test_failed_payment_is_ignored(self):
        self.assertEqual(self.run_rule(status="FAILED"), [])

    def test_currency_mismatch_is_not_reported_as_amount_difference(self):
        with self.assertRaisesRegex(ValueError, "幣別"):
            self.run_rule(currency="USD")

    def test_invalid_amounts_are_rejected(self):
        for value in ["", "abc", "NaN", "Infinity", "-0.01", "1.001"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_money(value)

    def test_missing_order_is_rejected(self):
        _, payments = self.rows()
        with self.assertRaisesRegex(ValueError, "缺少訂單"):
            find_amount_mismatches([], payments, as_of=AS_OF)

    def test_multiple_successful_payments_are_rejected(self):
        orders, payments = self.rows()
        second = SourceRow({**payments[0].data, "payment_id": "P2"}, "payments.csv", 4)
        with self.assertRaisesRegex(ValueError, "多筆成功付款"):
            find_amount_mismatches(orders, payments + [second], as_of=AS_OF)

    def test_duplicate_payment_id_is_rejected(self):
        orders, payments = self.rows()
        with self.assertRaisesRegex(ValueError, "付款編號重複"):
            find_amount_mismatches(orders, payments * 2, as_of=AS_OF)

    def test_future_payment_is_rejected(self):
        orders, payments = self.rows()
        payments[0].data["paid_at"] = "2026-11-18T10:00:00+08:00"
        with self.assertRaisesRegex(ValueError, "基準時間"):
            find_amount_mismatches(orders, payments, as_of=AS_OF)

    def test_cli_matches_expected_answers_for_enabled_rules(self):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.reconcile"], cwd=PROJECT_DIR,
            text=True, capture_output=True, check=True,
        )
        output = json.loads(result.stdout)
        manifest = json.loads((PROJECT_DIR / "data/sample/manifest.json").read_text())
        expected = [case for case in manifest["expected_cases"] if case["rule"] in output["rules_executed"]]
        self.assertEqual(output["rules_executed"], ["PAID_BUT_ORDER_UNPAID", "PAYMENT_AMOUNT_MISMATCH", "SETTLEMENT_MISSING", "SETTLEMENT_ARITHMETIC_MISMATCH"])
        self.assertEqual(output["case_count"], 4)
        self.assertEqual([{k: case[k] for k in ("rule", "order_id", "payment_id")} for case in output["cases"]], expected)
        self.assertEqual(output["cases"][1]["difference"], "-100.00")
        self.assertEqual(output["cases"][3]["expected_net_amount"], "1960.00")
        self.assertEqual(output["cases"][3]["difference"], "10.00")


if __name__ == "__main__":
    unittest.main()
