import logging
import os
from pathlib import Path

import psycopg
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import get_database_dsn, router
from app.database import get_case, get_run, source_hashes
from app.report import render_report
from app.reconciliation import MISSING_SETTLEMENT_RULE
from app.tracking import get_tracking

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    instance = FastAPI(title="跨系統資料差異追查平台", version="0.2.0")
    read_only = os.environ.get("DEMO_READ_ONLY", "false").lower() == "true"

    @instance.middleware("http")
    async def demo_access(request: Request, call_next):
        if read_only and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return JSONResponse(status_code=403, content={"detail": "公開展示版僅供查詢；上傳與案件更新請使用本機版本。"})
        return await call_next(request)

    @instance.get("/api/deployment", tags=["健康檢查"])
    def deployment():
        return {"read_only": read_only}

    @instance.get("/ready", tags=["健康檢查"])
    def ready():
        with psycopg.connect(get_database_dsn(), connect_timeout=5) as conn:
            row = conn.execute("SELECT 1 FROM schema_migrations WHERE version=4").fetchone()
        if row is None:
            return JSONResponse(status_code=503, content={"detail": "資料庫尚未完成初始化"})
        return {"status": "ready"}

    instance.include_router(router)
    web = Path(__file__).resolve().parent / "web"
    instance.mount("/static", StaticFiles(directory=web), name="static")

    @instance.get("/", include_in_schema=False)
    def home():
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})

    @instance.get("/cases/{case_id}/report", response_class=HTMLResponse, include_in_schema=False)
    def case_report(case_id: Annotated[int, PathParam(ge=1)], dsn: str = Depends(get_database_dsn)):
        case = get_case(dsn, case_id)
        if case is None:
            raise HTTPException(404, "找不到此異常案件")
        run = get_run(dsn, case["run_id"])
        if run is None:
            raise HTTPException(404, "找不到此案件的比對批次")
        hashes = source_hashes(dsn, run["batch_id"])
        if case["rule"] == MISSING_SETTLEMENT_RULE:
            workflow = get_tracking(dsn, case)["workflow"]
            case.update({key: value for key, value in workflow.items() if key != "case_id"})
        return HTMLResponse(render_report(case, run, hashes), headers={"Cache-Control": "no-store"})

    @instance.exception_handler(psycopg.Error)
    async def database_error(request: Request, exc: psycopg.Error):
        # 不向 API 使用者顯示連線字串、SQL 或資料庫內部錯誤。
        logger.error("Database request failed: %s", type(exc).__name__)
        if isinstance(exc, (psycopg.OperationalError, psycopg.errors.UndefinedTable)):
            status, detail = 503, "資料庫無法使用，請確認已啟動服務並執行 scripts.db init"
        elif isinstance(exc, psycopg.DataError):
            status, detail = 422, "資料不符合資料庫型別或範圍，整次匯入已回復"
        elif isinstance(exc, psycopg.IntegrityError):
            status, detail = 409, "資料關聯或唯一性衝突，整次匯入已回復"
        else:
            status, detail = 500, "資料庫操作失敗，請查看服務端紀錄"
        return JSONResponse(status_code=status, content={"detail": detail})

    @instance.get("/health", tags=["健康檢查"])
    def health() -> dict[str, str]:
        """程序健康檢查；不代表資料庫可連線。"""
        return {"status": "ok", "stage": "csv-upload-and-query-api"}

    return instance


app = create_app()
