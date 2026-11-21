import logging
import os
from pathlib import Path

import psycopg
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Path as PathParam, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.access import SESSION_COOKIE, SESSION_SECONDS, authenticate, end_session, session_actor, start_session
from app.api import get_database_dsn, router
from app.database import get_case, get_run, source_hashes
from app.report import render_report
from app.reconciliation import MISSING_SETTLEMENT_RULE
from app.tracking import get_tracking

logger = logging.getLogger(__name__)
NO_CACHE = {"Cache-Control": "no-store"}


def create_app() -> FastAPI:
    instance = FastAPI(title="跨系統資料差異追查平台", version="0.2.0")
    read_only = os.environ.get("DEMO_READ_ONLY", "false").lower() == "true"
    auth_required = not read_only and os.environ.get("AUTH_REQUIRED", "false").lower() == "true"

    @instance.middleware("http")
    async def enforce_access(request: Request, call_next):
        writes = request.method not in {"GET", "HEAD", "OPTIONS"}
        if read_only and writes:
            return JSONResponse(status_code=403, content={"detail": "公開展示版僅供查詢；上傳與案件更新請使用本機版本。"})
        path = request.url.path
        if not auth_required or path in {"/health", "/ready"} or (
            path.startswith("/static/") and path.endswith((".css", ".js"))
        ):
            return await call_next(request)
        if writes:
            origin = request.headers.get("origin")
            same_origin = f"{request.url.scheme}://{request.url.netloc}"
            if (origin is not None and origin != same_origin) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse(status_code=403, content={"detail": "目前權限無法執行此操作。"})
        if path in {"/login", "/logout"}:
            response = await call_next(request)
            response.headers.update(NO_CACHE)
            return response
        try:
            dsn = instance.dependency_overrides.get(get_database_dsn, get_database_dsn)()
            actor = await run_in_threadpool(session_actor, dsn, request.cookies.get(SESSION_COOKIE, ""))
        except (psycopg.Error, HTTPException):
            return JSONResponse(status_code=503, content={"detail": "帳號服務暫時無法使用，請確認資料庫已初始化。"})
        if actor is None:
            if request.method == "GET" and not path.startswith("/api/"):
                return RedirectResponse("/login", status_code=303, headers=NO_CACHE)
            return JSONResponse(status_code=401, headers=NO_CACHE, content={"detail": "請登入工作台。"})
        if writes and actor.role == "viewer":
            return JSONResponse(status_code=403, content={"detail": "目前權限無法執行此操作。"})
        request.state.actor = actor
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @instance.get("/api/deployment", tags=["健康檢查"])
    def deployment(request: Request):
        if auth_required:
            actor = request.state.actor
            return {"read_only": False, "username": actor.username, "role": actor.role}
        return {"read_only": read_only}

    @instance.get("/ready", tags=["健康檢查"])
    def ready():
        with psycopg.connect(get_database_dsn(), connect_timeout=5) as conn:
            row = conn.execute("SELECT 1 FROM schema_migrations WHERE version=6").fetchone()
        if row is None:
            return JSONResponse(status_code=503, content={"detail": "資料庫尚未完成初始化"})
        return {"status": "ready"}

    instance.include_router(router)
    web = Path(__file__).resolve().parent / "web"
    instance.mount("/static", StaticFiles(directory=web), name="static")

    if auth_required:
        login_html = (web / "login.html").read_text(encoding="utf-8")

        @instance.get("/login", include_in_schema=False)
        def login_page():
            return HTMLResponse(login_html)

        @instance.post("/login", include_in_schema=False)
        def login(request: Request, username: Annotated[str, Form()], password: Annotated[str, Form()],
                  dsn: str = Depends(get_database_dsn)):
            actor = authenticate(dsn, username, password)
            if actor is None:
                return HTMLResponse(login_html.replace(
                    "<!-- login-error -->", '<p class="message" role="alert">帳號或密碼不正確。</p>',
                ), status_code=401)
            token = start_session(dsn, actor.username, request.cookies.get(SESSION_COOKIE, ""))
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                samesite="strict", secure=request.url.scheme == "https",
            )
            return response

        @instance.post("/logout", include_in_schema=False)
        def logout(request: Request, dsn: str = Depends(get_database_dsn)):
            end_session(dsn, request.cookies.get(SESSION_COOKIE, ""))
            response = RedirectResponse("/login", status_code=303)
            response.delete_cookie(
                SESSION_COOKIE, httponly=True, samesite="strict", secure=request.url.scheme == "https",
            )
            return response

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
