import copy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import ai_summary
from app.main import create_app
from app.pipeline import prepare_sample
from app.rule_summary import RuleSummaryError, build_summary


class RuleSummaryTests(unittest.TestCase):
    def cases(self):
        return [{**case, "case_id": index, "version": 1, "status": "OPEN"}
                for index, case in enumerate(prepare_sample().report["cases"], 1)]

    def test_all_four_templates_cite_real_evidence_without_model_calls(self):
        with patch("app.ai_summary.openai.OpenAI", side_effect=AssertionError("不得建立模型客戶端")):
            for case in self.cases():
                with self.subTest(rule=case["rule"]):
                    content, payload = build_summary(case)
                    self.assertEqual(len(content.recommended_steps), 3)
                    self.assertEqual(content.possible_causes, [])
                    self.assertNotIn("測試回應", content.overview)
                    for item in [*content.confirmed_facts, *content.recommended_steps]:
                        self.assertTrue(set(item.evidence_ids).issubset(payload["evidence"]))

    def test_status_summary_uses_success_and_unpaid_source_records(self):
        content, _ = build_summary(self.cases()[0])
        self.assertIn("O1001", content.overview)
        self.assertIn("未付款", content.overview)
        self.assertIn("通知", " ".join(content.missing_information))

    def test_amount_direction_and_decimal_difference(self):
        case = self.cases()[1]
        content, _ = build_summary(case)
        self.assertIn("少付 100.00", content.overview)
        # 改用另一組數字，驗證不是把範例金額寫死在模板中。
        case["evidence"][0]["record"]["amount"] = "0.30"
        case["evidence"][1]["record"]["amount"] = "0.31"
        content, _ = build_summary(case)
        self.assertIn("多付 0.01", content.overview)

    def test_missing_settlement_mentions_coverage_and_no_payment_conclusion(self):
        content, _ = build_summary(self.cases()[2])
        text = " ".join(item.text for item in content.confirmed_facts)
        self.assertIn("2026-11-16", text)
        self.assertIn("符合筆數為 0", text)
        self.assertIn("不等於已證明資金未入帳", content.overview)

    def test_incomplete_coverage_is_rejected(self):
        case = self.cases()[2]
        case["evidence"][1]["complete_through"] = "2026-11-15"
        with self.assertRaises(RuleSummaryError):
            build_summary(case)

    def test_arithmetic_uses_actual_amounts(self):
        case = self.cases()[3]
        content, _ = build_summary(case)
        self.assertIn("1960.00", content.overview)
        self.assertIn("偏高 10.00", content.overview)
        case["evidence"][0]["record"].update(gross_amount="100.00", fee="2.00", net_amount="97.00")
        content, _ = build_summary(case)
        self.assertIn("偏低 1.00", content.overview)
        self.assertIn("應為 98.00", content.overview)

    def test_reordered_evidence_keeps_correct_citations(self):
        case = copy.deepcopy(self.cases()[0])
        case["evidence"].reverse()
        content, payload = build_summary(case)
        payment_ref = content.confirmed_facts[0].evidence_ids[0]
        self.assertEqual(payload["evidence"][payment_ref]["file"], "payments.csv")

    def test_missing_evidence_is_not_invented(self):
        case = self.cases()[3]
        case["evidence"] = case["evidence"][1:]
        with self.assertRaises(RuleSummaryError):
            build_summary(case)

    def test_summary_mode_is_local_and_requires_no_key(self):
        with TestClient(create_app()) as client:
            response = client.get("/api/summary/status")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["mode"], "rules")
            self.assertFalse(response.json()["requires_api_key"])
            self.assertFalse(response.json()["external_requests"])

    def test_paid_generation_is_blocked_before_database_or_model_access(self):
        with patch("app.ai_summary.paid_generation_enabled", return_value=False), patch("app.ai_summary.call_model") as provider:
            with self.assertRaises(ai_summary.SummaryError) as caught:
                ai_summary.generate_summary("unused", 1)
            self.assertEqual(caught.exception.status, 403)
            provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
