import logging
from pathlib import Path

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    instance = FastAPI(title="跨系統資料差異追查平台", version="0.2.0")
    instance.include_router(router)
    web = Path(__file__).resolve().parent / "web"
    instance.mount("/static", StaticFiles(directory=web), name="static")

    @instance.get("/", include_in_schema=False)
    def home():
        return FileResponse(web / "index.html", headers={"Cache-Control": "no-cache"})

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
