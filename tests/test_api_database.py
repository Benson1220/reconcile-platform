import os
import unittest
import uuid
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.api import get_database_dsn
from app.access import SESSION_COOKIE, create_user
from app.database import database_url, initialize_database, read_summary, save_snapshot
from app.main import create_app
from app.pipeline import SAMPLE_DIR
from scripts.tracking_demo import demo_snapshot


@unittest.skipUnless(os.environ.get("RUN_DATABASE_TESTS") == "1", "需啟用 PostgreSQL 整合測試")
class ApiDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.base_dsn = database_url()
        self.schema = "test_api_" + uuid.uuid4().hex
        with psycopg.connect(self.base_dsn) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        self.dsn = make_conninfo(self.base_dsn, options=f"-c search_path={self.schema}")
        initialize_database(self.dsn)
        app = create_app()
        app.dependency_overrides[get_database_dsn] = lambda: self.dsn
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.files = {name: (f"{name}.csv", (SAMPLE_DIR / f"{name}.csv").read_bytes(), "text/csv")
                      for name in ("orders", "payments", "settlements")}

    def cleanup_schema(self):
        with psycopg.connect(self.base_dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def test_roles_enforce_case_changes_and_record_authenticated_actors(self):
        run = save_snapshot(demo_snapshot("first"), self.dsn)
        cases = self.client.get(f"/api/runs/{run['run_id']}/cases").json()["items"]
        case_id = next(case["case_id"] for case in cases if case["order_id"] == "O1004")
        url = f"/api/cases/{case_id}"
        report = f"/cases/{case_id}/report"
        password = "synthetic-password-for-test"
        for username, role in [("alice", "viewer"), ("bob", "investigator"), ("carol", "supervisor")]:
            create_user(self.dsn, username, role, password)
        with patch.dict(os.environ, {"DEMO_READ_ONLY": "false", "AUTH_REQUIRED": "true"}):
            app = create_app()
            app.dependency_overrides[get_database_dsn] = lambda: self.dsn
            with TestClient(app) as client:
                self.assertEqual(client.get(url).status_code, 401)
                self.assertEqual(client.post("/login", data={"username": "alice", "password": "wrong"}).status_code, 401)
                active = None

                def sign_in(username):
                    nonlocal active
                    if username != active:
                        response = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
                        self.assertEqual(response.status_code, 303)
                        active = username

                sign_in("alice")
                self.assertEqual(client.get(report).status_code, 200)

                def update(username, status, version, headers=None):
                    sign_in(username)
                    return client.patch(url, headers=headers, json={
                        "status": status, "note": "測試查核", "expected_version": version,
                        "actor_username": "spoofed",
                    })

                self.assertEqual(update("alice", "IN_PROGRESS", 1).status_code, 403)
                self.assertEqual(update("bob", "CLOSED", 1).status_code, 403)
                self.assertEqual(update("bob", "IN_PROGRESS", 1, {"Origin": "https://other.example"}).status_code, 403)
                started = update("bob", "IN_PROGRESS", 1)
                self.assertEqual(started.status_code, 200)
                self.assertEqual(started.json()["history"][0]["actor_username"], "bob")
                self.assertEqual(update("carol", "CLOSED", 1).status_code, 409)
                self.assertEqual(update("carol", "CLOSED", 2).status_code, 200)
                self.assertEqual(update("bob", "IN_PROGRESS", 3).status_code, 403)
                sign_in("alice")
                finished = client.get(url).json()
                self.assertEqual(finished["status"], "CLOSED")
                self.assertEqual(
                    [(event["actor_username"], event["actor_role"]) for event in finished["history"]],
                    [("carol", "supervisor"), ("bob", "investigator")],
                )
                self.assertIn("carol", client.get(report).text)
                self.assertEqual(update("carol", "IN_PROGRESS", 3).status_code, 200)

    def test_logout_revokes_session_and_allows_switching_accounts(self):
        password = "synthetic-password-for-test"
        create_user(self.dsn, "alice", "viewer", password)
        create_user(self.dsn, "bob", "investigator", password)
        with patch.dict(os.environ, {"DEMO_READ_ONLY": "false", "AUTH_REQUIRED": "true"}):
            app = create_app()
            app.dependency_overrides[get_database_dsn] = lambda: self.dsn
            with TestClient(app) as client:
                self.assertEqual(client.get("/", follow_redirects=False).headers["location"], "/login")
                signed_in = client.post("/login", data={"username": "alice", "password": password})
                self.assertIn("登出", signed_in.text)
                token = client.cookies[SESSION_COOKIE]
                self.assertEqual(client.get("/api/deployment").json()["username"], "alice")
                self.assertEqual(client.post("/logout", headers={"Origin": "https://other.example"}).status_code, 403)
                signed_out = client.post("/logout", follow_redirects=False)
                self.assertEqual(signed_out.headers["location"], "/login")
                self.assertNotIn(SESSION_COOKIE, client.cookies)
                self.assertEqual(client.get("/api/deployment", headers={"Cookie": f"{SESSION_COOKIE}={token}"}).status_code, 401)
                self.assertEqual(client.get("/", follow_redirects=False).headers["location"], "/login")
                client.post("/login", data={"username": "bob", "password": password})
                self.assertEqual(client.get("/api/deployment").json()["username"], "bob")

    def test_missing_settlement_tracks_shared_workflow_and_requires_human_review(self):
        def missing_case(stage):
            run_id = save_snapshot(demo_snapshot(stage), self.dsn)["run_id"]
            rows = self.client.get(f"/api/runs/{run_id}/cases").json()["items"]
            return run_id, next((row for row in rows if row["payment_id"] == "P9003"), None)

        _, first = missing_case("first")
        first_url = f"/api/cases/{first['case_id']}"
        self.assertEqual(self.client.patch(first_url, json={
            "status": "IN_PROGRESS", "note": "等候結算資料", "expected_version": 1,
        }).status_code, 200)
        _, repeat = missing_case("repeat")
        self.assertNotEqual(first["case_id"], repeat["case_id"])
        self.assertEqual(repeat["status"], "IN_PROGRESS")
        repeat_url = f"/api/cases/{repeat['case_id']}"
        self.assertEqual(self.client.get(repeat_url).json()["history"][0]["note"], "等候結算資料")
        self.assertEqual(self.client.patch(repeat_url, json={
            "status": "IN_PROGRESS", "note": "第二批仍缺漏", "expected_version": 2,
        }).status_code, 200)
        self.assertEqual(self.client.get(first_url).json()["history"][0]["note"], "第二批仍缺漏")

        settled_run, missing = missing_case("settled")
        self.assertIsNone(missing)
        detail = self.client.get(first_url).json()
        follow_up = detail["tracking"]
        self.assertEqual(follow_up["status"], "PENDING_REVIEW")
        self.assertEqual(detail["status"], "IN_PROGRESS")
        self.assertEqual(detail["evidence"][1]["type"], "absence_check")
        self.assertEqual(len(follow_up["observations"]), 3)
        latest = follow_up["observations"][-1]
        self.assertEqual(latest["run_id"], settled_run)
        self.assertEqual(latest["settlements"][0]["record"]["settlement_id"], "S8003")
        response = self.client.patch(first_url, json={
            "status": "CLOSED", "note": "已核對 S8003", "expected_version": 3,
        })
        self.assertEqual(response.json()["tracking"]["status"], "REVIEWED")

    def test_printable_report_uses_evidence_and_escapes_case_notes(self):
        uploaded = self.client.post("/api/reconciliations", files=self.files).json()
        cases = self.client.get(uploaded["cases_url"]).json()["items"]
        case_id = next(case["case_id"] for case in cases if case["order_id"] == "O1004")
        path = f"/api/cases/{case_id}"
        note = "<script>alert(1)</script>"
        self.assertEqual(self.client.patch(path, json={
            "status": "CLOSED", "note": note, "expected_version": 1,
        }).status_code, 200)
        report = self.client.get(f"/cases/{case_id}/report")
        self.assertEqual(report.status_code, 200)
        for value in ("O1004", "S8004", "1960.00", "1970.00", "10.00", "已結案",
                      "E1 · settlements.csv · 第 4 列", "E2 · payments.csv · 第 5 列",
                      "SHA-256", "建議查核步驟", "&lt;script&gt;alert(1)&lt;/script&gt;"):
            self.assertIn(value, report.text)
        self.assertNotIn(note, report.text)
        self.assertEqual(report.headers["cache-control"], "no-store")
        for case in cases:
            self.assertEqual(self.client.get(f"/cases/{case['case_id']}/report").status_code, 200)
        self.assertEqual(self.client.get("/cases/999999/report").status_code, 404)

    def test_upload_query_filter_and_detail_round_trip(self):
        response = self.client.post("/api/reconciliations", files=self.files)
        self.assertEqual(response.status_code, 201, response.text)
        uploaded = response.json()
        runs = self.client.get("/api/runs").json()
        self.assertEqual(runs["total"], 1)
        self.assertEqual(runs["items"][0]["case_count"], 4)
        detail = self.client.get(uploaded["run_url"]).json()
        self.assertEqual(detail["config"]["settlement_complete_through"], "2026-11-16")
        cases = self.client.get(uploaded["cases_url"]).json()
        self.assertEqual(cases["total"], 4)
        missing_id = next(item["case_id"] for item in cases["items"] if item["rule"] == "SETTLEMENT_MISSING")
        missing = self.client.get(f"/api/cases/{missing_id}").json()
        self.assertEqual(missing["evidence"][1]["coverage_source"], "upload_form:settlement_complete_through")
        filtered = self.client.get(uploaded["cases_url"], params={"rule": "SETTLEMENT_ARITHMETIC_MISMATCH"}).json()
        self.assertEqual(filtered["total"], 1)
        case = self.client.get(f"/api/cases/{filtered['items'][0]['case_id']}").json()
        self.assertEqual(case["settlement_id"], "S8004")
        self.assertEqual(case["difference"], "10.00")
        self.assertEqual(case["evidence"][0]["file"], "settlements.csv")
        self.assertEqual(case["evidence"][0]["line"], 4)

    def test_reupload_returns_200_without_duplicates(self):
        first = self.client.post("/api/reconciliations", files=self.files)
        second = self.client.post("/api/reconciliations", files=self.files)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["reused"])
        self.assertEqual(first.json()["run_id"], second.json()["run_id"])
        self.assertEqual(read_summary(self.dsn)["table_counts"]["cases"], 4)

    def test_invalid_upload_does_not_write_partial_sources(self):
        self.files["settlements"] = ("settlements.csv", b"settlement_id\nS1\n", "text/csv")
        response = self.client.post("/api/reconciliations", files=self.files)
        self.assertEqual(response.status_code, 422)
        self.assertTrue(all(count == 0 for count in read_summary(self.dsn)["table_counts"].values()))

    def test_pagination_and_empty_filter(self):
        uploaded = self.client.post("/api/reconciliations", files=self.files).json()
        cases = self.client.get(uploaded["cases_url"], params={"limit": 1, "offset": 1}).json()
        self.assertEqual(cases["total"], 4)
        self.assertEqual(len(cases["items"]), 1)
        self.assertEqual(cases["items"][0]["order_id"], "O1002")
        empty = self.client.get(uploaded["cases_url"], params={"offset": 100}).json()
        self.assertEqual(empty["items"], [])
        self.assertEqual(empty["total"], 4)

    def seed_case(self):
        uploaded = self.client.post("/api/reconciliations", files=self.files).json()
        items = self.client.get(uploaded["cases_url"]).json()["items"]
        return uploaded, next(item for item in items if item["order_id"] == "O1004")

    def change(self, case_id, status, note, version):
        return self.client.patch(f"/api/cases/{case_id}", json={"status": status, "note": note, "expected_version": version})

    def test_default_workflow_and_note_keep_original_evidence(self):
        _, case = self.seed_case()
        before = self.client.get(f"/api/cases/{case['case_id']}").json()
        self.assertEqual((before["status"], before["version"], before["history"]), ("OPEN", 1, []))
        response = self.change(case["case_id"], "IN_PROGRESS", "  已開始核對原始報表  ", 1)
        self.assertEqual(response.status_code, 200, response.text)
        after = response.json()
        self.assertEqual((after["status"], after["version"]), ("IN_PROGRESS", 2))
        self.assertEqual(after["history"][0]["note"], "已開始核對原始報表")
        self.assertEqual(after["history"][0]["from_status"], "OPEN")
        self.assertEqual(after["evidence"], before["evidence"])
        self.assertEqual(after["difference"], "10.00")

    def test_close_requires_note_and_keeps_closure_time_on_extra_note(self):
        _, case = self.seed_case()
        case_id = case["case_id"]
        self.assertEqual(self.change(case_id, "CLOSED", "   ", 1).status_code, 422)
        closed = self.change(case_id, "CLOSED", "已確認資料欄位錯誤，完成處理", 1).json()
        self.assertIsNotNone(closed["closed_at"])
        extra = self.change(case_id, "CLOSED", "補充驗證結果", 2).json()
        self.assertEqual(extra["closed_at"], closed["closed_at"])
        self.assertEqual(len(extra["history"]), 2)

    def test_reopen_keeps_history_and_clears_closure_time(self):
        _, case = self.seed_case()
        case_id = case["case_id"]
        self.change(case_id, "CLOSED", "完成查核", 1)
        reopened = self.change(case_id, "IN_PROGRESS", "發現新證據，重新調查", 2).json()
        self.assertEqual(reopened["status"], "IN_PROGRESS")
        self.assertIsNone(reopened["closed_at"])
        self.assertEqual([event["version"] for event in reopened["history"]], [3, 2])
        self.assertEqual(reopened["history"][0]["from_status"], "CLOSED")

    def test_stale_update_is_409_and_does_not_append_note(self):
        _, case = self.seed_case()
        case_id = case["case_id"]
        self.assertEqual(self.change(case_id, "IN_PROGRESS", "第一個分頁儲存", 1).status_code, 200)
        stale = self.change(case_id, "CLOSED", "舊分頁嘗試結案", 1)
        self.assertEqual(stale.status_code, 409)
        stored = self.client.get(f"/api/cases/{case_id}").json()
        self.assertEqual(stored["status"], "IN_PROGRESS")
        self.assertEqual(len(stored["history"]), 1)
        self.assertEqual(stored["version"], 2)

    def test_reupload_retains_progress_and_notes(self):
        _, case = self.seed_case()
        self.change(case["case_id"], "IN_PROGRESS", "持續追查", 1)
        response = self.client.post("/api/reconciliations", files=self.files).json()
        self.assertTrue(response["reused"])
        stored = self.client.get(f"/api/cases/{case['case_id']}").json()
        self.assertEqual(stored["status"], "IN_PROGRESS")
        self.assertEqual(stored["history"][0]["note"], "持續追查")
        self.assertEqual(read_summary(self.dsn)["table_counts"]["cases"], 4)

    def test_status_filter_combines_with_rule_filter(self):
        uploaded, case = self.seed_case()
        self.change(case["case_id"], "CLOSED", "完成查核", 1)
        result = self.client.get(uploaded["cases_url"], params={"status": "CLOSED", "rule": "SETTLEMENT_ARITHMETIC_MISMATCH"}).json()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["status"], "CLOSED")
        open_cases = self.client.get(uploaded["cases_url"], params={"status": "OPEN"}).json()
        self.assertEqual(open_cases["total"], 3)

    def test_concurrent_changes_only_one_wins(self):
        from concurrent.futures import ThreadPoolExecutor
        from app.database import CaseVersionConflict, update_case
        _, case = self.seed_case()
        def attempt(number):
            try:
                update_case(self.dsn, case["case_id"], "IN_PROGRESS", f"並行備註 {number}", 1)
                return "saved"
            except CaseVersionConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(results), ["conflict", "saved"])
        stored = self.client.get(f"/api/cases/{case['case_id']}").json()
        self.assertEqual(len(stored["history"]), 1)


if __name__ == "__main__":
    unittest.main()
