"""Cross-batch tracking for missing settlements."""
import psycopg
from psycopg.rows import dict_row

from app.database import _case_detail, investigation_case_id
from app.reconciliation import MISSING_SETTLEMENT_RULE


def get_tracking(dsn: str, case: dict) -> dict:
    with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row) as conn:
        # Use a single snapshot for the timeline and the original workflow.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        anchor = investigation_case_id(conn, case["case_id"])
        workflow = _case_detail(conn, anchor)
        observations = conn.execute(
            """SELECT r.id AS run_id, r.as_of, r.created_at, c.id AS case_id,
                      s.settlement_id, sr.line, sr.record
               FROM reconciliation_runs r
               JOIN payments p ON p.batch_id=r.batch_id
               LEFT JOIN cases c ON c.run_id=r.id AND c.payment_id=p.payment_id
                   AND c.order_id=p.order_id AND c.rule=%s
               LEFT JOIN settlements s ON s.batch_id=r.batch_id AND s.payment_id=p.payment_id
               LEFT JOIN source_rows sr ON sr.id=s.source_row_id
               WHERE p.order_id=%s AND p.payment_id=%s AND p.status='SUCCESS'
                 AND (c.id IS NOT NULL OR s.settlement_id IS NOT NULL)
               ORDER BY r.as_of, r.id, s.settlement_id""",
            (MISSING_SETTLEMENT_RULE, case["order_id"], case["payment_id"]),
        ).fetchall()

    timeline = []
    for row in observations:
        if not timeline or timeline[-1]["run_id"] != row["run_id"]:
            timeline.append({
                "run_id": row["run_id"], "as_of": row["as_of"], "created_at": row["created_at"],
                "missing_case_id": row["case_id"], "settlements": [],
            })
        if row["settlement_id"] is not None:
            timeline[-1]["settlements"].append({
                "file": "settlements.csv", "line": row["line"], "record": row["record"],
            })

    first_missing = next(item["as_of"] for item in timeline if item["missing_case_id"] is not None)
    timeline = [item for item in timeline if item["as_of"] >= first_missing]
    latest_time = timeline[-1]["as_of"]
    latest = [item for item in timeline if item["as_of"] == latest_time]
    if any(item["missing_case_id"] is not None for item in latest):
        status = "MISSING"
    elif workflow["closed_at"] and workflow["closed_at"] >= max(item["created_at"] for item in latest):
        status = "REVIEWED"
    else:
        status = "PENDING_REVIEW"
    workflow_fields = ("case_id", "status", "version", "updated_at", "closed_at", "history")
    return {
        "status": status,
        "workflow": {key: workflow[key] for key in workflow_fields},
        "observations": timeline,
    }
