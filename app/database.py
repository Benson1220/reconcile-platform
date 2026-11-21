"""來源與結果以單一交易保存；相同資料、規則版本及設定可重複執行。"""
import os
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.pipeline import RULES_VERSION, SCHEMAS, Snapshot, digest, json_digest
from app.reconciliation import parse_money

ROOT = Path(__file__).resolve().parents[1]


def database_url() -> str:
    value = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not value:
        raise ValueError("尚未設定 DATABASE_URL，請先執行本機資料庫 setup")
    return value


def initialize_database(dsn: str | None = None) -> list[int]:
    migrations = sorted((ROOT / "sql").glob("[0-9][0-9][0-9]_*.sql"))
    with psycopg.connect(dsn or database_url(), connect_timeout=5) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(7160117)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, sha256 TEXT NOT NULL)")
        versions = []
        for path in migrations:
            version = int(path.name.split("_", 1)[0])
            migration = path.read_bytes()
            existing = conn.execute("SELECT sha256 FROM schema_migrations WHERE version=%s", (version,)).fetchone()
            if existing:
                if existing[0] != digest(migration):
                    raise ValueError("已套用 migration 被修改，請新增 migration")
            else:
                conn.execute(migration.decode("utf-8"))
                conn.execute("INSERT INTO schema_migrations VALUES (%s,%s)", (version, digest(migration)))
            versions.append(version)
        return versions


def save_snapshot(snapshot: Snapshot, dsn: str | None = None) -> dict:
    with psycopg.connect(dsn or database_url(), connect_timeout=5) as conn:
        inserted = conn.execute(
            "INSERT INTO import_batches (fingerprint) VALUES (%s) ON CONFLICT DO NOTHING RETURNING id",
            (snapshot.fingerprint,),
        ).fetchone()
        if inserted:
            batch_id = inserted[0]
            source_ids = {}
            for source, rows in snapshot.rows.items():
                content = snapshot.files[source]
                conn.execute("INSERT INTO source_files VALUES (%s,%s,%s,%s,%s,%s)",
                             (batch_id, source, f"{source}.csv", digest(content), len(rows), content))
                for row in rows:
                    row_id = conn.execute(
                        "INSERT INTO source_rows (batch_id, source, line, record) VALUES (%s,%s,%s,%s) RETURNING id",
                        (batch_id, source, row.line, Jsonb(row.data)),
                    ).fetchone()[0]
                    source_ids[(row.file, row.line)] = row_id
                    values = []
                    for field in SCHEMAS[source]:
                        value = row.data[field]
                        if field in {"amount", "gross_amount", "fee", "net_amount"}:
                            value = parse_money(value)
                        values.append(value if value != "" else None)
                    columns = ["batch_id", *SCHEMAS[source], "source_row_id"]
                    query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(source), sql.SQL(",").join(map(sql.Identifier, columns)),
                        sql.SQL(",").join(sql.Placeholder() for _ in columns),
                    )
                    conn.execute(query, [batch_id, *values, row_id])
        else:
            batch_id = conn.execute("SELECT id FROM import_batches WHERE fingerprint=%s", (snapshot.fingerprint,)).fetchone()[0]
            source_ids = {(f"{source}.csv", line): row_id for row_id, source, line in conn.execute(
                "SELECT id, source, line FROM source_rows WHERE batch_id=%s", (batch_id,),
            )}

        inserted_run = conn.execute(
            """INSERT INTO reconciliation_runs (batch_id, rules_version, config_hash, config, rules_executed, as_of)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
            (batch_id, RULES_VERSION, json_digest(snapshot.config), Jsonb(snapshot.config),
             Jsonb(snapshot.report["rules_executed"]), snapshot.config["as_of"]),
        ).fetchone()
        if inserted_run:
            run_id = inserted_run[0]
            for case in snapshot.report["cases"]:
                case_id = conn.execute(
                    "INSERT INTO cases (run_id, case_key, rule, order_id, payment_id, payload) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                    (run_id, case["case_key"], case["rule"], case["order_id"], case["payment_id"], Jsonb(case)),
                ).fetchone()[0]
                for position, evidence in enumerate(case["evidence"]):
                    row_id = source_ids[(evidence["file"], evidence["line"])] if "line" in evidence else None
                    conn.execute("INSERT INTO case_evidence VALUES (%s,%s,%s,%s)",
                                 (case_id, position, row_id, Jsonb(evidence)))
        else:
            run_id = conn.execute(
                "SELECT id FROM reconciliation_runs WHERE batch_id=%s AND rules_version=%s AND config_hash=%s",
                (batch_id, RULES_VERSION, json_digest(snapshot.config)),
            ).fetchone()[0]
        count = conn.execute("SELECT count(*) FROM cases WHERE run_id=%s", (run_id,)).fetchone()[0]
        return {"batch_id": batch_id, "run_id": run_id, "reused": inserted_run is None, "case_count": count}


def read_summary(dsn: str | None = None) -> dict:
    with psycopg.connect(dsn or database_url(), connect_timeout=5) as conn:
        counts = {}
        for name in ["import_batches", "source_files", "source_rows", "orders", "payments", "settlements", "reconciliation_runs", "cases", "case_evidence"]:
            counts[name] = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name))).fetchone()[0]
        cases = conn.execute("SELECT run_id, rule, order_id, payment_id, payload->>'difference' FROM cases ORDER BY run_id, id").fetchall()
        return {"table_counts": counts, "cases": [dict(zip(["run_id", "rule", "order_id", "payment_id", "difference"], row)) for row in cases]}


def list_runs(dsn: str, limit: int, offset: int) -> dict:
    with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row) as conn:
        total = conn.execute("SELECT count(*) AS total FROM reconciliation_runs").fetchone()["total"]
        items = conn.execute(
            """SELECT r.id AS run_id, r.batch_id, r.rules_version, r.as_of, r.created_at,
                      (SELECT count(*) FROM cases c WHERE c.run_id=r.id) AS case_count
               FROM reconciliation_runs r ORDER BY r.id DESC LIMIT %s OFFSET %s""",
            (limit, offset),
        ).fetchall()
        return {"total": total, "limit": limit, "offset": offset, "items": items}


def get_run(dsn: str, run_id: int) -> dict | None:
    with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row) as conn:
        return conn.execute(
            """SELECT r.id AS run_id, r.batch_id, r.rules_version, r.as_of, r.created_at,
                      r.config, r.rules_executed,
                      (SELECT count(*) FROM cases c WHERE c.run_id=r.id) AS case_count
               FROM reconciliation_runs r WHERE r.id=%s""", (run_id,),
        ).fetchone()


def source_hashes(dsn: str, batch_id: int) -> dict[str, str]:
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        return dict(conn.execute(
            "SELECT filename, sha256 FROM source_files WHERE batch_id=%s", (batch_id,),
        ).fetchall())


def list_cases(dsn: str, run_id: int, rule: str | None, limit: int, offset: int, status: str | None = None) -> dict | None:
    with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row) as conn:
        if not conn.execute("SELECT 1 FROM reconciliation_runs WHERE id=%s", (run_id,)).fetchone():
            return None
        # 所有使用者輸入都透過參數傳入，不拼接 SQL。
        condition = "run_id=%s"
        params = [run_id]
        if rule is not None:
            condition += " AND rule=%s"
            params.append(rule)
        if status is not None:
            condition += " AND status=%s"
            params.append(status)
        total = conn.execute(f"SELECT count(*) AS total FROM cases WHERE {condition}", params).fetchone()["total"]
        items = conn.execute(
            f"""SELECT id AS case_id, run_id, rule, order_id, payment_id, status, version, updated_at, closed_at,
                       payload->>'summary' AS summary, payload->>'difference' AS difference
                FROM cases WHERE {condition} ORDER BY id LIMIT %s OFFSET %s""",
            [*params, limit, offset],
        ).fetchall()
        return {"run_id": run_id, "total": total, "limit": limit, "offset": offset, "items": items}


def get_case(dsn: str, case_id: int) -> dict | None:
    with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row) as conn:
        return _case_detail(conn, case_id)


def _case_detail(conn, case_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id AS case_id, run_id, payload, status, version, updated_at, closed_at FROM cases WHERE id=%s",
        (case_id,),
    ).fetchone()
    if row is None:
        return None
    # 即使期間有其他更新，也只回傳讀取到的案件版本所涵蓋的紀錄。
    history = conn.execute(
        "SELECT id AS event_id, from_status, to_status, note, version, created_at FROM case_events WHERE case_id=%s AND version<=%s ORDER BY version DESC",
        (case_id, row["version"]),
    ).fetchall()
    payload = row.pop("payload")
    return {**payload, **row, "history": history}


class CaseVersionConflict(Exception):
    """使用者看到的是舊版案件，避免覆蓋其他人的進度。"""


def update_case(dsn: str, case_id: int, status: str, note: str, expected_version: int) -> dict | None:
    note = note.strip()
    if status not in {"OPEN", "IN_PROGRESS", "CLOSED"} or not 1 <= len(note) <= 2000:
        raise ValueError("請選擇有效狀態，並填寫 1～2000 字的處理備註")
    with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row) as conn:
        current = conn.execute("SELECT status, version, closed_at FROM cases WHERE id=%s FOR UPDATE", (case_id,)).fetchone()
        if current is None:
            return None
        if current["version"] != expected_version:
            raise CaseVersionConflict("案件已被更新，請重新載入案件後再儲存；備註尚未送出")
        conn.execute(
            """UPDATE cases SET status=%s, version=version+1, updated_at=clock_timestamp(),
                closed_at=CASE WHEN %s='CLOSED' THEN COALESCE(closed_at, clock_timestamp()) ELSE NULL END
                WHERE id=%s""", (status, status, case_id),
        )
        conn.execute(
            "INSERT INTO case_events (case_id, from_status, to_status, note, version) VALUES (%s,%s,%s,%s,%s)",
            (case_id, current["status"], status, note, current["version"] + 1),
        )
        return _case_detail(conn, case_id)
