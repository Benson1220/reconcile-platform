import json
import unittest
from pathlib import Path

from app.reconciliation import (
    SETTLEMENT_ARITHMETIC_RULE, SourceRow, aware_time,
    find_settlement_arithmetic_mismatches, load_rows,
)

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data/sample"
AS_OF = aware_time("2026-11-17T09:00:00+08:00")


class SettlementArithmeticTests(unittest.TestCase):
    def rows(self, gross="2000.00", fee="40.00", net="1970.00"):
        payment = SourceRow({
            "payment_id": "P1", "order_id": "O1", "status": "SUCCESS",
            "currency": "TWD", "paid_at": "2026-11-13T10:00:00+08:00",
        }, "payments.csv", 5)
        settlement = SourceRow({
            "settlement_id": "S1", "payment_id": "P1", "gross_amount": gross,
            "fee": fee, "net_amount": net, "currency": "TWD",
            "settlement_date": "2026-11-16",
        }, "settlements.csv", 4)
        return [payment], [settlement]

    def run_rule(self, **kwargs):
        payments, settlements = self.rows(**kwargs)
        return find_settlement_arithmetic_mismatches(payments, settlements, as_of=AS_OF)

    def test_high_net_amount_and_evidence(self):
        case = self.run_rule()[0]
        self.assertEqual(case["expected_net_amount"], "1960.00")
        self.assertEqual(case["difference"], "10.00")
        self.assertEqual(case["settlement_id"], "S1")
        self.assertEqual([(e["file"], e["line"]) for e in case["evidence"]], [("settlements.csv", 4), ("payments.csv", 5)])

    def test_correct_arithmetic_is_not_reported(self):
        self.assertEqual(self.run_rule(net="1960.00"), [])

    def test_low_net_amount(self):
        self.assertEqual(self.run_rule(net="1950.00")[0]["difference"], "-10.00")

    def test_decimal_arithmetic_is_exact(self):
        self.assertEqual(self.run_rule(gross="0.30", fee="0.10", net="0.20"), [])
        self.assertEqual(self.run_rule(gross="0.30", fee="0.10", net="0.21")[0]["difference"], "0.01")

    def test_zero_fee_and_zero_net(self):
        self.assertEqual(self.run_rule(gross="10", fee="0", net="10.00"), [])
        self.assertEqual(self.run_rule(gross="10", fee="10", net="0"), [])

    def test_invalid_money_in_each_field(self):
        for field in ["gross", "fee", "net"]:
            for value in ["", "NaN", "Infinity", "-1", "1.001"]:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.run_rule(**{field: value})

    def test_no_settlement_means_no_arithmetic_case(self):
        payments, _ = self.rows()
        self.assertEqual(find_settlement_arithmetic_mismatches(payments, [], as_of=AS_OF), [])

    def test_currency_mismatch_is_rejected(self):
        payments, settlements = self.rows()
        settlements[0].data["currency"] = "USD"
        with self.assertRaisesRegex(ValueError, "幣別"):
            find_settlement_arithmetic_mismatches(payments, settlements, as_of=AS_OF)

    def test_missing_or_failed_payment_is_rejected(self):
        payments, settlements = self.rows()
        with self.assertRaisesRegex(ValueError, "不存在的付款"):
            find_settlement_arithmetic_mismatches([], settlements, as_of=AS_OF)
        payments[0].data["status"] = "FAILED"
        with self.assertRaisesRegex(ValueError, "不是 SUCCESS"):
            find_settlement_arithmetic_mismatches(payments, settlements, as_of=AS_OF)

    def test_duplicate_settlement_is_rejected(self):
        payments, settlements = self.rows()
        with self.assertRaisesRegex(ValueError, "結算編號重複"):
            find_settlement_arithmetic_mismatches(payments, settlements * 2, as_of=AS_OF)

    def test_future_or_pre_payment_settlement_is_rejected(self):
        for day in ["2026-11-18", "2026-11-12"]:
            payments, settlements = self.rows()
            settlements[0].data["settlement_date"] = day
            with self.subTest(day=day), self.assertRaises(ValueError):
                find_settlement_arithmetic_mismatches(payments, settlements, as_of=AS_OF)

    def test_sample_matches_independent_expected_answer(self):
        manifest = json.loads((SAMPLE_DIR / "manifest.json").read_text())
        payments = load_rows(SAMPLE_DIR / "payments.csv", {"payment_id", "order_id", "status", "currency", "paid_at"}, "payment_id")
        settlements = load_rows(SAMPLE_DIR / "settlements.csv", {"settlement_id", "payment_id", "gross_amount", "fee", "net_amount", "currency", "settlement_date"}, "settlement_id")
        cases = find_settlement_arithmetic_mismatches(payments, settlements, as_of=AS_OF)
        expected = [c for c in manifest["expected_cases"] if c["rule"] == SETTLEMENT_ARITHMETIC_RULE]
        self.assertEqual([{k: c[k] for k in ("rule", "order_id", "payment_id")} for c in cases], expected)
        self.assertEqual(cases[0]["evidence"][0]["line"], 4)


if __name__ == "__main__":
    unittest.main()
