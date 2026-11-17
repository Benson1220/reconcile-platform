"""雲端唯讀作品展示：建表、匯入合成資料、預先整理摘要，再啟動服務。"""
import logging
import os

import psycopg
import uvicorn

from app.database import initialize_database, save_snapshot
from app.pipeline import prepare_sample
from app.rule_summary import generate_summary

logger = logging.getLogger(__name__)


def bootstrap(dsn: str) -> dict:
    snapshot = prepare_sample()
    initialize_database(dsn)
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        # 公開展示只接受內附的合成資料，避免誤接既有業務資料庫。
        unexpected = conn.execute(
            "SELECT 1 FROM import_batches WHERE fingerprint <> %s LIMIT 1",
            (snapshot.fingerprint,),
        ).fetchone()
        if unexpected:
            raise ValueError("請使用專供展示的新資料庫；目前資料庫含其他來源資料")
    result = save_snapshot(snapshot, dsn)
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        case_ids = [row[0] for row in conn.execute(
            "SELECT id FROM cases WHERE run_id=%s ORDER BY id", (result["run_id"],),
        )]
    for case_id in case_ids:
        generate_summary(dsn, case_id)
    logger.info("Demo ready: %s cases and local rule summaries", len(case_ids))
    return result


def main():
    logging.basicConfig(level=logging.INFO)
    # 雲端啟動不讀本機 .env，也不輸出含密碼的連線字串。
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise ValueError("請在託管平台設定 DATABASE_URL")
    if os.environ.get("DEMO_READ_ONLY", "").lower() != "true":
        raise ValueError("公開展示必須設定 DEMO_READ_ONLY=true")
    bootstrap(dsn)
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, psycopg.Error) as exc:
        logger.error("Cloud startup failed (%s); check deployment settings and database access.", type(exc).__name__)
        raise SystemExit(1) from None
