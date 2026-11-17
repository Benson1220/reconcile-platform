"""RUN_DATABASE_TESTS=1 時才連線；全部操作隔離在隨機測試 schema。"""
import copy
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.database import database_url, initialize_database, read_summary, save_snapshot
from app.pipeline import prepare_sample


@unittest.skipUnless(os.environ.get("RUN_DATABASE_TESTS") == "1", "設定 RUN_DATABASE_TESTS=1 才執行 PostgreSQL 整合測試")
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.base_dsn = database_url()
        self.schema = "test_" + uuid.uuid4().hex
        with psycopg.connect(self.base_dsn) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        self.dsn = make_conninfo(self.base_dsn, options=f"-c search_path={self.schema}")
        initialize_database(self.dsn)

    def cleanup_schema(self):
        # 只清除本測試建立的 UUID schema，絕不清除 public 或應用資料。
        with psycopg.connect(self.base_dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_round_trip_sources_results_and_evidence(self):
        snapshot = prepare_sample()
        result = save_snapshot(snapshot, self.dsn)
        self.assertFalse(result["reused"])
        self.assertEqual(result["case_count"], 4)
        summary = read_summary(self.dsn)
        self.assertEqual(summary["table_counts"], {
            "import_batches": 1, "source_files": 3, "source_rows": 19,
            "orders": 8, "payments": 7, "settlements": 4,
            "reconciliation_runs": 1, "cases": 4, "case_evidence": 8,
        })
        with psycopg.connect(self.dsn) as conn:
            amount = conn.execute("SELECT amount FROM orders WHERE order_id='O1002'").fetchone()[0]
            self.assertEqual(amount, Decimal("1500.00"))
            raw = conn.execute("SELECT original_content FROM source_files WHERE source='settlements'").fetchone()[0]
            self.assertEqual(bytes(raw), snapshot.files["settlements"])
            stored = conn.execute("SELECT payload FROM cases ORDER BY id").fetchall()
            self.assertEqual([row[0] for row in stored], snapshot.report["cases"])
            linked = conn.execute("SELECT r.source, r.line FROM case_evidence e JOIN source_rows r ON r.id=e.source_row_id JOIN cases c ON c.id=e.case_id WHERE c.rule='SETTLEMENT_ARITHMETIC_MISMATCH' ORDER BY e.position").fetchall()
            self.assertEqual(linked, [("settlements", 4), ("payments", 5)])
            self.assertEqual(conn.execute("SELECT count(*) FROM case_evidence WHERE source_row_id IS NULL").fetchone()[0], 1)

    def test_reimport_is_idempotent(self):
        snapshot = prepare_sample()
        first = save_snapshot(snapshot, self.dsn)
        second = save_snapshot(snapshot, self.dsn)
        self.assertTrue(second["reused"])
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(read_summary(self.dsn)["table_counts"]["cases"], 4)

    def test_entire_import_rolls_back_on_evidence_failure(self):
        snapshot = prepare_sample()
        snapshot.report["cases"][-1]["evidence"][0]["line"] = 999
        with self.assertRaises(KeyError):
            save_snapshot(snapshot, self.dsn)
        self.assertTrue(all(count == 0 for count in read_summary(self.dsn)["table_counts"].values()))

    def test_new_config_creates_run_but_reuses_source_batch(self):
        snapshot = prepare_sample()
        first = save_snapshot(snapshot, self.dsn)
        changed = copy.deepcopy(snapshot)
        changed.config["order_sync_grace_minutes"] = 20
        second = save_snapshot(changed, self.dsn)
        self.assertEqual(first["batch_id"], second["batch_id"])
        self.assertNotEqual(first["run_id"], second["run_id"])
        counts = read_summary(self.dsn)["table_counts"]
        self.assertEqual(counts["source_rows"], 19)
        self.assertEqual(counts["cases"], 8)

    def test_concurrent_reimports_do_not_duplicate_cases(self):
        snapshot = prepare_sample()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: save_snapshot(snapshot, self.dsn), range(2)))
        self.assertEqual(results[0]["run_id"], results[1]["run_id"])
        self.assertEqual(sorted(r["reused"] for r in results), [False, True])
        self.assertEqual(read_summary(self.dsn)["table_counts"]["cases"], 4)

    def test_migration_can_be_repeated(self):
        initialize_database(self.dsn)
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], 4)

    def test_cloud_bootstrap_reuses_samples_and_prepares_read_only_summaries(self):
        from scripts.cloud_start import bootstrap
        from app.main import create_app
        from app.api import get_database_dsn
        from fastapi.testclient import TestClient
        from unittest.mock import patch

        first = bootstrap(self.dsn)
        second = bootstrap(self.dsn)
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertTrue(second["reused"])
        with psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM cases").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT count(*) FROM case_rule_summaries").fetchone()[0], 4)
            case_ids = [row[0] for row in conn.execute("SELECT id FROM cases ORDER BY id")]
        with patch.dict(os.environ, {"DEMO_READ_ONLY": "true"}):
            app = create_app()
            app.dependency_overrides[get_database_dsn] = lambda: self.dsn
            with TestClient(app) as client:
                self.assertEqual(client.get(f"/api/runs/{first['run_id']}/cases").json()["total"], 4)
                for case_id in case_ids:
                    self.assertIsNotNone(client.get(f"/api/cases/{case_id}/rule-summary").json()["summary"])
                    self.assertEqual(client.post(f"/api/cases/{case_id}/rule-summary").status_code, 403)

    def test_cloud_bootstrap_refuses_database_with_other_source_data(self):
        from scripts.cloud_start import bootstrap
        snapshot = prepare_sample()
        snapshot.files["orders"] += b"\n"
        save_snapshot(snapshot, self.dsn)
        with self.assertRaises(ValueError):
            bootstrap(self.dsn)


if __name__ == "__main__":
    unittest.main()
