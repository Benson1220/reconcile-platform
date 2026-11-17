import unittest
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

from app.api import MAX_FILE_BYTES, get_database_dsn
from app.main import create_app
from app.pipeline import SAMPLE_DIR


def sample_uploads():
    return {name: (f"{name}.csv", (SAMPLE_DIR / f"{name}.csv").read_bytes(), "text/csv")
            for name in ("orders", "payments", "settlements")}


class ApiTests(unittest.TestCase):
    def setUp(self):
        app = create_app()
        app.dependency_overrides[get_database_dsn] = lambda: "unused-by-mocked-repository"
        self.client = TestClient(app)
        self.files = sample_uploads()
        self.save_patch = patch("app.api.save_snapshot", return_value={
            "batch_id": 1, "run_id": 1, "reused": True, "case_count": 4,
        })
        self.save = self.save_patch.start()
        self.addCleanup(self.save_patch.stop)
        self.addCleanup(self.client.close)

    def upload(self, data=None):
        return self.client.post("/api/reconciliations", files=self.files, data=data or {})

    def test_home_serves_workbench_without_database_access(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("資料對帳工作台", response.text)
        self.assertIn('name="orders"', response.text)
        self.assertIn('name="payments"', response.text)
        self.assertIn('name="settlements"', response.text)
        self.save.assert_not_called()

    def test_frontend_assets_and_private_files_not_exposed(self):
        for filename, mime in [("app.js", "javascript"), ("style.css", "text/css")]:
            with self.subTest(filename=filename):
                response = self.client.get(f"/static/{filename}")
                self.assertEqual(response.status_code, 200)
                self.assertIn(mime, response.headers["content-type"])
        self.assertEqual(self.client.get("/static/%2e%2e/.env").status_code, 404)
        self.assertEqual(self.client.get("/.env").status_code, 404)

    def test_health_and_openapi_upload_fields(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 200)
        schema = self.client.get("/openapi.json").json()
        request = schema["paths"]["/api/reconciliations"]["post"]["requestBody"]
        ref = request["content"]["multipart/form-data"]["schema"]["$ref"].split("/")[-1]
        fields = schema["components"]["schemas"][ref]
        self.assertEqual(set(fields["required"]), {"orders", "payments", "settlements"})
        self.assertEqual(fields["properties"]["orders"]["format"], "binary")

    def test_valid_upload_uses_shared_rules_and_returns_links(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cases_url"], "/api/runs/1/cases")
        snapshot = self.save.call_args.args[0]
        self.assertEqual(snapshot.report["case_count"], 4)
        self.assertEqual(snapshot.report["cases"][-1]["difference"], "10.00")

    def test_new_upload_returns_201(self):
        self.save.return_value = {"batch_id": 2, "run_id": 2, "reused": False, "case_count": 4}
        self.assertEqual(self.upload().status_code, 201)

    def test_missing_file_is_422(self):
        del self.files["settlements"]
        self.assertEqual(self.upload().status_code, 422)
        self.save.assert_not_called()

    def test_wrong_extension_is_415(self):
        self.files["orders"] = ("orders.txt", self.files["orders"][1], "text/plain")
        self.assertEqual(self.upload().status_code, 415)
        self.save.assert_not_called()

    def test_empty_file_is_422(self):
        self.files["orders"] = ("orders.csv", b"", "text/csv")
        self.assertEqual(self.upload().status_code, 422)
        self.save.assert_not_called()

    def test_invalid_utf8_is_422(self):
        self.files["orders"] = ("orders.csv", b"\xff\xfe", "text/csv")
        self.assertEqual(self.upload().status_code, 422)
        self.save.assert_not_called()

    def test_missing_column_is_422(self):
        self.files["orders"] = ("orders.csv", b"order_id\nO1\n", "text/csv")
        self.assertEqual(self.upload().status_code, 422)
        self.save.assert_not_called()

    def test_invalid_amount_even_on_unpaid_order_is_rejected(self):
        content = self.files["orders"][1].replace(b"O1008,900.00", b"O1008,-1.00")
        self.files["orders"] = ("orders.csv", content, "text/csv")
        self.assertEqual(self.upload().status_code, 422)
        self.save.assert_not_called()

    def test_currency_mismatch_is_422(self):
        content = self.files["payments"][1].replace(b"TWD", b"USD")
        self.files["payments"] = ("payments.csv", content, "text/csv")
        self.assertEqual(self.upload().status_code, 422)
        self.save.assert_not_called()

    def test_naive_time_and_insufficient_coverage_are_422(self):
        for data in [{"as_of": "2026-11-17T09:00:00"}, {"settlement_complete_through": "2026-11-15"}]:
            with self.subTest(data=data):
                self.assertEqual(self.upload(data).status_code, 422)
        self.save.assert_not_called()

    def test_file_size_limit_is_413(self):
        self.files["orders"] = ("orders.csv", b"x" * (MAX_FILE_BYTES + 1), "text/csv")
        self.assertEqual(self.upload().status_code, 413)
        self.save.assert_not_called()

    def test_row_limit_is_413(self):
        with patch("app.api.MAX_ROWS", 1):
            self.assertEqual(self.upload().status_code, 413)
        self.save.assert_not_called()

    def test_invalid_query_parameters_are_422(self):
        for path in ["/api/runs?limit=0", "/api/runs?offset=-1", "/api/runs/0",
                     "/api/runs/1/cases?rule=unknown", "/api/cases/abc"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 422)

    def test_unknown_run_and_case_are_404(self):
        for name, path in [("get_run", "/api/runs/999"), ("list_cases", "/api/runs/999/cases"), ("get_case", "/api/cases/999")]:
            with self.subTest(path=path), patch(f"app.api.{name}", return_value=None):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_database_unavailable_is_503_without_internal_details(self):
        self.save.side_effect = psycopg.OperationalError("secret-internal-connection-details")
        response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret-internal", response.text)

    def test_original_filename_does_not_choose_server_path(self):
        self.files["orders"] = ("../../elsewhere.csv", self.files["orders"][1], "text/csv")
        self.assertEqual(self.upload().status_code, 200)
        snapshot = self.save.call_args.args[0]
        self.assertEqual(snapshot.rows["orders"][0].file, "orders.csv")

    def test_workflow_validation_happens_before_database_update(self):
        invalid = [
            {"status": "CLOSED", "note": "   ", "expected_version": 1},
            {"status": "UNKNOWN", "note": "測試", "expected_version": 1},
            {"status": "OPEN", "note": "測試", "expected_version": 0},
            {"status": "OPEN", "note": "x" * 2001, "expected_version": 1},
        ]
        with patch("app.api.update_case") as update:
            for body in invalid:
                with self.subTest(body=body):
                    self.assertEqual(self.client.patch("/api/cases/1", json=body).status_code, 422)
            update.assert_not_called()

    def test_workflow_unknown_case_is_404(self):
        with patch("app.api.update_case", return_value=None):
            response = self.client.patch("/api/cases/999", json={"status": "OPEN", "note": "查核", "expected_version": 1})
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
