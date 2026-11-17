import os
import unittest
from unittest.mock import patch

import psycopg

from fastapi.testclient import TestClient

from app.main import create_app
from scripts.cloud_start import main


class DeploymentTests(unittest.TestCase):
    def test_demo_blocks_writes_before_database_or_model_access(self):
        with patch.dict(os.environ, {"DEMO_READ_ONLY": "true"}), TestClient(create_app()) as client:
            for method, path in [
                ("post", "/api/reconciliations"), ("patch", "/api/cases/1"),
                ("post", "/api/cases/1/rule-summary"), ("post", "/api/cases/1/ai-summary"),
            ]:
                with self.subTest(path=path), patch("app.database.psycopg.connect") as connect:
                    response = getattr(client, method)(path)
                    self.assertEqual(response.status_code, 403)
                    connect.assert_not_called()
            self.assertEqual(client.get("/api/deployment").json(), {"read_only": True})
            for path in ["/", "/static/app.js", "/docs", "/health"]:
                self.assertEqual(client.get(path).status_code, 200)

    def test_local_mode_keeps_upload_endpoint_available(self):
        with patch.dict(os.environ, {"DEMO_READ_ONLY": "false"}), TestClient(create_app()) as client:
            self.assertEqual(client.get("/api/deployment").json(), {"read_only": False})
            # 缺少檔案仍由 FastAPI 驗證，沒有被展示模式的 403 擋住。
            with patch("app.api.database_url", return_value="unused"):
                self.assertEqual(client.post("/api/reconciliations").status_code, 422)

    def test_readiness_checks_database_migrations(self):
        with TestClient(create_app()) as client, patch("app.main.get_database_dsn", return_value="unused"):
            with patch("app.main.psycopg.connect") as connect:
                cursor = connect.return_value.__enter__.return_value.execute.return_value
                cursor.fetchone.return_value = (1,)
                self.assertEqual(client.get("/ready").status_code, 200)
                cursor.fetchone.return_value = None
                self.assertEqual(client.get("/ready").status_code, 503)

    def test_readiness_failure_hides_connection_details(self):
        with TestClient(create_app()) as client, patch("app.main.get_database_dsn", return_value="unused"):
            with patch("app.main.psycopg.connect", side_effect=psycopg.OperationalError("PRIVATE_CONNECTION")):
                response = client.get("/ready")
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("PRIVATE_CONNECTION", response.text)

    def test_cloud_start_requires_explicit_database_and_read_only_mode(self):
        for settings in [{}, {"DATABASE_URL": "unused", "DEMO_READ_ONLY": "false"}]:
            with self.subTest(settings=settings), patch.dict(os.environ, settings, clear=True):
                with patch("scripts.cloud_start.bootstrap") as bootstrap:
                    with self.assertRaises(ValueError):
                        main()
                    bootstrap.assert_not_called()

    def test_cloud_start_prepares_data_before_listening_on_platform_port(self):
        with patch.dict(os.environ, {"DATABASE_URL": "unused", "DEMO_READ_ONLY": "true", "PORT": "10000"}):
            calls = []
            with patch("scripts.cloud_start.bootstrap", side_effect=lambda dsn: calls.append("seed")):
                with patch("scripts.cloud_start.uvicorn.run", side_effect=lambda *a, **kw: calls.append(kw)):
                    main()
            self.assertEqual(calls, ["seed", {"host": "0.0.0.0", "port": 10000}])
