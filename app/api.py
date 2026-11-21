"""CSV 上傳、比對結果查詢；透過 /docs 逐步操作。"""
import csv
from datetime import date, datetime
from enum import Enum
from pathlib import Path as FilePath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.database import CaseVersionConflict, database_url, get_case, get_run, list_cases, list_runs, save_snapshot, update_case
from app.pipeline import prepare_snapshot
from app.reconciliation import AMOUNT_RULE, MISSING_SETTLEMENT_RULE, RULE, SETTLEMENT_ARITHMETIC_RULE
from app import ai_summary, rule_summary, tracking

router = APIRouter(prefix="/api")
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 10_000


def get_database_dsn() -> str:
    try:
        return database_url()
    except ValueError as exc:
        raise HTTPException(503, "資料庫尚未設定，請先執行本機資料庫 setup") from exc


class RuleName(str, Enum):
    unpaid = RULE
    amount = AMOUNT_RULE
    missing = MISSING_SETTLEMENT_RULE
    arithmetic = SETTLEMENT_ARITHMETIC_RULE


class CaseStatus(str, Enum):
    open = "OPEN"
    in_progress = "IN_PROGRESS"
    closed = "CLOSED"


class CaseUpdate(BaseModel):
    status: CaseStatus
    note: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=1)

    @field_validator("note")
    @classmethod
    def trim_note(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("請填寫處理備註；結案時請說明處理結果")
        return value


class ImportResult(BaseModel):
    batch_id: int
    run_id: int
    reused: bool
    case_count: int
    run_url: str
    cases_url: str


class RunSummary(BaseModel):
    run_id: int
    batch_id: int
    rules_version: str
    as_of: datetime
    created_at: datetime
    case_count: int


class RunDetail(RunSummary):
    config: dict[str, Any]
    rules_executed: list[str]


class RunPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[RunSummary]


class CaseSummary(BaseModel):
    case_id: int
    run_id: int
    rule: str
    order_id: str
    payment_id: str
    summary: str
    difference: str | None
    status: CaseStatus
    version: int
    updated_at: datetime
    closed_at: datetime | None


class CasePage(BaseModel):
    run_id: int
    total: int
    limit: int
    offset: int
    items: list[CaseSummary]


@router.post(
    "/reconciliations", response_model=ImportResult, status_code=201,
    responses={200: {"model": ImportResult, "description": "相同資料與設定，沿用既有結果"}},
    tags=["1. 上傳與比對"], summary="上傳三份 CSV，執行比對並儲存",
    description="一次提供訂單、付款、結算 CSV。日期預設值只供範例資料操作，實際資料請自行設定。每份檔案最多 2 MiB、10,000 筆。",
)
def upload_reconciliation(
    orders: Annotated[UploadFile, File(description="選擇 orders.csv", json_schema_extra={"format": "binary"})],
    payments: Annotated[UploadFile, File(description="選擇 payments.csv", json_schema_extra={"format": "binary"})],
    settlements: Annotated[UploadFile, File(description="選擇 settlements.csv", json_schema_extra={"format": "binary"})],
    as_of: Annotated[datetime, Form(description="比對時間，必須包含時區；範例使用固定時間")]
        = datetime.fromisoformat("2026-11-17T09:00:00+08:00"),
    settlement_complete_through: Annotated[date, Form(description="來源宣告完整涵蓋的結算日期，含當日")]
        = date(2026, 11, 16),
    order_sync_grace_minutes: Annotated[int, Form(ge=0, le=1440)] = 15,
    dsn: str = Depends(get_database_dsn),
):
    files = {}
    for source, upload in [("orders", orders), ("payments", payments), ("settlements", settlements)]:
        if FilePath(upload.filename or "").suffix.lower() != ".csv":
            raise HTTPException(415, f"{source} 必須上傳 .csv 檔案")
        content = upload.file.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{source}.csv 超過 2 MiB 限制")
        if not content.strip():
            raise HTTPException(422, f"{source}.csv 不可為空檔案")
        # 不用使用者的檔名拼接路徑，也不覆寫專案 sample 檔案。
        files[source] = content

    config = {
        "as_of": as_of.isoformat(), "timezone": "Asia/Taipei", "currency": "TWD",
        "order_sync_grace_minutes": order_sync_grace_minutes,
        "settlement_complete_through": settlement_complete_through.isoformat(),
    }
    try:
        snapshot = prepare_snapshot(
            files, config, coverage_source="upload_form:settlement_complete_through",
        )
    except (ValueError, KeyError, csv.Error) as exc:
        raise HTTPException(422, f"資料驗證失敗：{exc}") from exc
    if any(len(rows) > MAX_ROWS for rows in snapshot.rows.values()):
        raise HTTPException(413, "每份 CSV 最多 10,000 筆資料")

    result = save_snapshot(snapshot, dsn)
    result.update(run_url=f"/api/runs/{result['run_id']}", cases_url=f"/api/runs/{result['run_id']}/cases")
    return JSONResponse(status_code=200 if result["reused"] else 201, content=result)


@router.get("/runs", response_model=RunPage, tags=["2. 查詢比對批次"], summary="列出比對批次")
def query_runs(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    dsn: str = Depends(get_database_dsn),
):
    return list_runs(dsn, limit, offset)


@router.get("/runs/{run_id}", response_model=RunDetail, tags=["2. 查詢比對批次"], summary="查詢單一批次與執行設定")
def query_run(run_id: Annotated[int, Path(ge=1)], dsn: str = Depends(get_database_dsn)):
    result = get_run(dsn, run_id)
    if result is None:
        raise HTTPException(404, "找不到此比對批次")
    return result


@router.get("/runs/{run_id}/cases", response_model=CasePage, tags=["3. 查詢異常與證據"], summary="列出批次異常，可依規則篩選")
def query_cases(
    run_id: Annotated[int, Path(ge=1)],
    rule: RuleName | None = None,
    status: CaseStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    dsn: str = Depends(get_database_dsn),
):
    result = list_cases(dsn, run_id, rule.value if rule else None, limit, offset, status.value if status else None)
    if result is None:
        raise HTTPException(404, "找不到此比對批次")
    return result


@router.get("/cases/{case_id}", tags=["3. 查詢異常與證據"], summary="查詢單筆異常的完整內容與證據")
def query_case(case_id: Annotated[int, Path(ge=1)], dsn: str = Depends(get_database_dsn)) -> dict[str, Any]:
    result = get_case(dsn, case_id)
    if result is None:
        raise HTTPException(404, "找不到此異常案件")
    if result["rule"] == MISSING_SETTLEMENT_RULE:
        follow_up = tracking.get_tracking(dsn, result)
        result.update({key: value for key, value in follow_up["workflow"].items() if key != "case_id"})
        result["tracking"] = follow_up
    return result


@router.patch("/cases/{case_id}", tags=["4. 處理案件"], summary="更新狀態並新增處理備註")
def patch_case(
    case_id: Annotated[int, Path(ge=1)], update: CaseUpdate,
    request: Request,
    dsn: str = Depends(get_database_dsn),
) -> dict[str, Any]:
    try:
        result = update_case(
            dsn, case_id, update.status.value, update.note, update.expected_version,
            actor=getattr(request.state, "actor", None),
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except CaseVersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "找不到此異常案件")
    if result["rule"] == MISSING_SETTLEMENT_RULE:
        return query_case(case_id, dsn)
    return result


@router.get("/ai/status", tags=["5. AI 摘要"], summary="查詢 AI 是否設定完成，不顯示金鑰")
def ai_status() -> dict:
    return {**ai_summary.public_settings(), "enabled": ai_summary.paid_generation_enabled()}


@router.get("/cases/{case_id}/ai-summary", tags=["5. AI 摘要"], summary="讀取最近一次已儲存摘要，不呼叫模型")
def get_ai_summary(case_id: Annotated[int, Path(ge=1)], dsn: str = Depends(get_database_dsn)) -> dict:
    result = ai_summary.read_summary(dsn, case_id)
    if result is None:
        raise HTTPException(404, "找不到此異常案件")
    return result


@router.post("/cases/{case_id}/ai-summary", tags=["5. AI 摘要"], summary="產生或沿用案件摘要，不變更案件狀態")
def create_ai_summary(case_id: Annotated[int, Path(ge=1)], dsn: str = Depends(get_database_dsn)) -> dict:
    try:
        result = ai_summary.generate_summary(dsn, case_id)
    except ai_summary.SummaryError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "找不到此異常案件")
    return result


@router.get("/summary/status", tags=["6. 本機規則式摘要"], summary="確認本機摘要模式，不需金鑰")
def summary_status() -> dict:
    return {"mode": "rules", "requires_api_key": False, "external_requests": False, "rule_version": rule_summary.RULE_VERSION}


@router.get("/cases/{case_id}/rule-summary", tags=["6. 本機規則式摘要"], summary="讀取保存的規則式摘要")
def get_rule_summary(case_id: Annotated[int, Path(ge=1)], dsn: str = Depends(get_database_dsn)) -> dict:
    result = rule_summary.read_summary(dsn, case_id)
    if result is None:
        raise HTTPException(404, "找不到此異常案件")
    return result


@router.post("/cases/{case_id}/rule-summary", tags=["6. 本機規則式摘要"], summary="在本機整理異常、證據與查核步驟")
def create_rule_summary(case_id: Annotated[int, Path(ge=1)], dsn: str = Depends(get_database_dsn)) -> dict:
    try:
        result = rule_summary.generate_summary(dsn, case_id)
    except (ValueError, ai_summary.SummaryError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "找不到此異常案件")
    return result
