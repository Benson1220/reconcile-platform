import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.api import get_database_dsn
from app.database import database_url, get_case, initialize_database, save_snapshot, update_case
from app.main import create_app
from app.pipeline import prepare_sample
from app.rule_summary import generate_summary


@unittest.skipUnless(os.environ.get("RUN_DATABASE_TESTS") == "1", "需啟用 PostgreSQL 整合測試")
class RuleSummaryDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.base = database_url()
        self.schema = "test_rules_" + uuid.uuid4().hex
        with psycopg.connect(self.base) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        self.dsn = make_conninfo(self.base, options=f"-c search_path={self.schema}")
        initialize_database(self.dsn)
        save_snapshot(prepare_sample(), self.dsn)
        with psycopg.connect(self.dsn) as conn:
            self.case_ids = [row[0] for row in conn.execute("SELECT id FROM cases ORDER BY id")]
        app = create_app()
        app.dependency_overrides[get_database_dsn] = lambda: self.dsn
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        # 任何意外外部模型呼叫都會讓測試失敗，不使用假摘要替代。
        provider = patch("app.ai_summary.openai.OpenAI", side_effect=AssertionError("規則摘要不得呼叫模型"))
        provider.start()
        self.addCleanup(provider.stop)

    def cleanup_schema(self):
        with psycopg.connect(self.base) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_all_four_generate_save_and_read_without_changing_cases(self):
        for case_id in self.case_ids:
            with self.subTest(case_id=case_id):
                before = get_case(self.dsn, case_id)
                path = f"/api/cases/{case_id}/rule-summary"
                self.assertIsNone(self.client.get(path).json()["summary"])
                response = self.client.post(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["mode"], "rules")
                stored = self.client.get(path).json()
                self.assertEqual(stored["summary"]["content"], response.json()["summary"]["content"])
                self.assertEqual(get_case(self.dsn, case_id), before)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM case_rule_summaries").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT count(*) FROM case_ai_summaries").fetchone()[0], 0)

    def test_repeat_and_concurrent_requests_reuse_one_summary(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: generate_summary(self.dsn, self.case_ids[-1]), range(2)))
        self.assertEqual(results[0]["summary"]["id"], results[1]["summary"]["id"])
        self.assertEqual(sorted(item["reused"] for item in results), [False, True])

    def test_updated_case_marks_stale_and_rebuilds(self):
        case_id = self.case_ids[-1]
        path = f"/api/cases/{case_id}/rule-summary"
        self.client.post(path)
        update_case(self.dsn, case_id, "IN_PROGRESS", "查核中", 1)
        self.assertTrue(self.client.get(path).json()["stale"])
        response = self.client.post(path).json()
        self.assertFalse(response["stale"])
        self.assertEqual(response["summary"]["case_version"], 2)
        self.assertEqual(get_case(self.dsn, case_id)["history"][0]["note"], "查核中")

    def test_old_paid_endpoint_is_disabled_without_calling_model(self):
        with patch("app.ai_summary.paid_generation_enabled", return_value=False):
            response = self.client.post(f"/api/cases/{self.case_ids[-1]}/ai-summary")
        self.assertEqual(response.status_code, 403)
        self.assertIn("停用", response.json()["detail"])

    def test_unknown_case_is_404(self):
        path = "/api/cases/999999/rule-summary"
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post(path).status_code, 404)


if __name__ == "__main__":
    unittest.main()
