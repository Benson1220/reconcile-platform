import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app import ai_summary
from app.api import get_database_dsn
from app.database import database_url, get_case, initialize_database, save_snapshot, update_case
from app.main import create_app
from app.pipeline import prepare_sample
from ai_fixture import fake_summary


@unittest.skipUnless(os.environ.get("RUN_DATABASE_TESTS") == "1", "需啟用 PostgreSQL 整合測試；模型回應全數模擬")
class AiDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.base = database_url()
        self.schema = "test_ai_" + uuid.uuid4().hex
        with psycopg.connect(self.base) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        self.dsn = make_conninfo(self.base, options=f"-c search_path={self.schema}")
        initialize_database(self.dsn)
        save_snapshot(prepare_sample(), self.dsn)
        with psycopg.connect(self.dsn) as conn:
            self.case_id = conn.execute("SELECT id FROM cases WHERE order_id='O1004'").fetchone()[0]
        app = create_app()
        app.dependency_overrides[get_database_dsn] = lambda: self.dsn
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        settings = patch("app.ai_summary.settings", return_value={"key": "test-fixture-key", "model": "gpt-5-mini"})
        settings.start()
        self.addCleanup(settings.stop)
        enabled = patch("app.ai_summary.paid_generation_enabled", return_value=True)
        enabled.start()
        self.addCleanup(enabled.stop)
        provider = patch("app.ai_summary.call_model", side_effect=lambda payload, config: fake_summary(payload))
        self.provider = provider.start()
        self.addCleanup(provider.stop)
        self.path = f"/api/cases/{self.case_id}/ai-summary"

    def cleanup_schema(self):
        with psycopg.connect(self.base) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_generate_persist_read_and_reuse_without_changing_case(self):
        before = get_case(self.dsn, self.case_id)
        initial = self.client.get(self.path).json()
        self.assertIsNone(initial["summary"])
        first = self.client.post(self.path)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(first.json()["reused"])
        self.assertFalse(first.json()["stale"])
        self.assertIn("測試回應", first.json()["summary"]["content"]["overview"])
        self.assertEqual(get_case(self.dsn, self.case_id), before)
        saved = self.client.get(self.path).json()
        second = self.client.post(self.path).json()
        self.assertTrue(second["reused"])
        self.assertEqual(saved["summary"]["id"], second["summary"]["id"])
        self.provider.assert_called_once()

    def test_missing_key_returns_503_and_writes_nothing(self):
        with patch("app.ai_summary.settings", return_value={"key": "", "model": "gpt-5-mini"}):
            self.assertEqual(self.client.post(self.path).status_code, 503)
        self.provider.assert_not_called()
        self.assertIsNone(self.client.get(self.path).json()["summary"])

    def test_invalid_citation_is_rejected_and_not_saved(self):
        def invalid(payload, config):
            content, usage = fake_summary(payload)
            content.confirmed_facts[0].evidence_ids = ["E999"]
            return content, usage
        self.provider.side_effect = invalid
        self.assertEqual(self.client.post(self.path).status_code, 502)
        self.assertIsNone(self.client.get(self.path).json()["summary"])

    def test_changed_case_marks_old_summary_stale_then_creates_new_one(self):
        self.client.post(self.path)
        update_case(self.dsn, self.case_id, "IN_PROGRESS", "開始查核", 1)
        self.assertTrue(self.client.get(self.path).json()["stale"])
        fresh = self.client.post(self.path).json()
        self.assertFalse(fresh["stale"])
        self.assertEqual(fresh["summary"]["case_version"], 2)
        self.assertEqual(self.provider.call_count, 2)

    def test_update_during_generation_does_not_overwrite_case(self):
        def update_during_call(payload, config):
            update_case(self.dsn, self.case_id, "IN_PROGRESS", "產生期間人工更新", 1)
            return fake_summary(payload)
        self.provider.side_effect = update_during_call
        response = self.client.post(self.path).json()
        self.assertTrue(response["stale"])
        self.assertEqual(get_case(self.dsn, self.case_id)["status"], "IN_PROGRESS")

    def test_parallel_generation_returns_busy_without_duplicate_model_call(self):
        started, release = Event(), Event()
        def slow(payload, config):
            started.set()
            release.wait(5)
            return fake_summary(payload)
        self.provider.side_effect = slow
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(ai_summary.generate_summary, self.dsn, self.case_id)
            try:
                self.assertTrue(started.wait(3))
                with self.assertRaises(ai_summary.SummaryError) as caught:
                    ai_summary.generate_summary(self.dsn, self.case_id)
                self.assertEqual(caught.exception.status, 409)
            finally:
                release.set()
            self.assertFalse(future.result()["reused"])
        self.provider.assert_called_once()

    def test_unknown_case_is_404(self):
        self.assertEqual(self.client.get("/api/cases/999999/ai-summary").status_code, 404)
        self.assertEqual(self.client.post("/api/cases/999999/ai-summary").status_code, 404)


if __name__ == "__main__":
    unittest.main()
