import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
from dotenv import dotenv_values
from fastapi.testclient import TestClient

from app.ai_summary import SummaryError, call_model, make_input, public_settings, validate_summary
from app.main import create_app
from app.pipeline import prepare_sample
from scripts.configure_ai import write_settings
from ai_fixture import fake_summary


class AiSummaryTests(unittest.TestCase):
    def case(self):
        case = prepare_sample().report["cases"][-1]
        return {**case, "case_id": 4, "version": 1, "status": "OPEN", "history": [{"note": "不應傳送的處理備註"}]}

    def test_prompt_input_excludes_notes_and_unknown_csv_columns(self):
        case = self.case()
        case["evidence"][0]["record"]["customer_email"] = "private@example.invalid"
        payload = make_input(case)
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("不應傳送", text)
        self.assertNotIn("customer_email", text)
        self.assertNotIn("private@example", text)
        self.assertEqual(payload["evidence"]["E1"]["line"], 4)

    def test_unknown_evidence_reference_is_rejected(self):
        payload = make_input(self.case())
        content, _ = fake_summary(payload)
        content.confirmed_facts[0].evidence_ids = ["E999"]
        with self.assertRaises(SummaryError) as caught:
            validate_summary(content, payload)
        self.assertEqual(caught.exception.status, 502)

    def test_sdk_uses_structured_output_and_does_not_store_response(self):
        payload = make_input(self.case())
        content, usage = fake_summary(payload)
        client = MagicMock()
        client.responses.parse.return_value = SimpleNamespace(status="completed", output_parsed=content, usage=SimpleNamespace(model_dump=lambda: usage))
        with patch("app.ai_summary.openai.OpenAI") as sdk:
            sdk.return_value.__enter__.return_value = client
            generated, _ = call_model(payload, {"key": "test-fixture-key", "model": "gpt-5-mini"})
            self.assertEqual(generated, content)
            self.assertEqual(sdk.call_args.kwargs["max_retries"], 0)
            request = client.responses.parse.call_args.kwargs
            self.assertFalse(request["store"])
            self.assertEqual(request["model"], "gpt-5-mini")
            self.assertNotIn("test-fixture-key", request["input"])

    def test_timeout_is_explicit_without_fake_fallback(self):
        client = MagicMock()
        client.responses.parse.side_effect = openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        with patch("app.ai_summary.openai.OpenAI") as sdk:
            sdk.return_value.__enter__.return_value = client
            with self.assertRaises(SummaryError) as caught:
                call_model(make_input(self.case()), {"key": "test-fixture-key", "model": "gpt-5-mini"})
        self.assertEqual(caught.exception.status, 504)

    def test_refusal_or_incomplete_response_is_not_saved_as_summary(self):
        for status, parsed in [("completed", None), ("incomplete", None)]:
            client = MagicMock()
            client.responses.parse.return_value = SimpleNamespace(status=status, output_parsed=parsed)
            with self.subTest(status=status), patch("app.ai_summary.openai.OpenAI") as sdk:
                sdk.return_value.__enter__.return_value = client
                with self.assertRaises(SummaryError):
                    call_model(make_input(self.case()), {"key": "test-fixture-key", "model": "gpt-5-mini"})

    def test_quota_spend_and_rate_errors_have_distinct_safe_messages(self):
        for code, expected in [
            ("insufficient_quota", "可用額度不足"),
            ("credit_balance_exhausted", "可用額度不足"),
            ("project_spend_limit_exceeded", "支出上限已達"),
            ("organization_usage_limit_exceeded", "月用量上限"),
            ("rate_limit_exceeded", "短時間請求"),
            ("slow_down", "短時間請求"),
            ("unknown", "無法由回應判定"),
        ]:
            response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
            client = MagicMock()
            client.responses.parse.side_effect = openai.RateLimitError(
                "internal-message-with-test-secret", response=response, body={"code": code},
            )
            with self.subTest(code=code), patch("app.ai_summary.openai.OpenAI") as sdk:
                sdk.return_value.__enter__.return_value = client
                with self.assertRaises(SummaryError) as caught:
                    call_model(make_input(self.case()), {"key": "test-fixture-key", "model": "gpt-5-mini"})
                self.assertEqual(caught.exception.status, 429)
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn("internal-message", str(caught.exception))

    def test_public_settings_do_not_expose_key(self):
        with patch("app.ai_summary.settings", return_value={"key": "test-fixture-secret", "model": "gpt-5-mini"}):
            data = public_settings()
            self.assertEqual(data, {"configured": True, "model": "gpt-5-mini"})
            with TestClient(create_app()) as client:
                response = client.get("/api/ai/status")
                self.assertNotIn("test-fixture-secret", response.text)

    def test_configuration_preserves_database_url_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("DATABASE_URL=postgresql://example.invalid/reconcile\n")
            write_settings(path, "sk-test-fixture-secret")
            values = dotenv_values(path)
            self.assertEqual(values["DATABASE_URL"], "postgresql://example.invalid/reconcile")
            self.assertEqual(values["OPENAI_API_KEY"], "sk-test-fixture-secret")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_overlong_input_is_rejected_before_api(self):
        case = self.case()
        case["evidence"][0]["record"]["settlement_id"] = "x" * 25_000
        with self.assertRaises(SummaryError) as caught:
            make_input(case)
        self.assertEqual(caught.exception.status, 422)


if __name__ == "__main__":
    unittest.main()
