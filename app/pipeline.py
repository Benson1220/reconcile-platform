"""讀取一次來源快照，供終端機與資料庫匯入共用。"""
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from app.reconciliation import (
    AMOUNT_RULE, MISSING_SETTLEMENT_RULE, RULE, SETTLEMENT_ARITHMETIC_RULE,
    SourceRow, aware_time, find_amount_mismatches, find_missing_settlements,
    find_settlement_arithmetic_mismatches, find_unpaid_orders, parse_money, parse_rows,
)

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data/sample"
RULES_VERSION = "1.0.0"  # 改變業務規則時需更新。
SCHEMAS = {
    "orders": ["order_id", "amount", "currency", "payment_status", "snapshot_at"],
    "payments": ["payment_id", "order_id", "amount", "currency", "status", "paid_at", "expected_settlement_date"],
    "settlements": ["settlement_id", "payment_id", "gross_amount", "fee", "net_amount", "currency", "settlement_date"],
}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_digest(value: dict) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


@dataclass
class Snapshot:
    files: dict[str, bytes]
    rows: dict[str, list[SourceRow]]
    config: dict
    report: dict

    @property
    def fingerprint(self) -> str:
        return json_digest({name: digest(content) for name, content in self.files.items()})


def prepare_sample(directory: Path = SAMPLE_DIR) -> Snapshot:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    # 不將基準答案當作執行條件。
    config = {key: manifest[key] for key in (
        "as_of", "timezone", "currency", "order_sync_grace_minutes", "settlement_complete_through",
    )}
    files = {name: (directory / f"{name}.csv").read_bytes() for name in SCHEMAS}
    return prepare_snapshot(files, config)


def prepare_snapshot(
    files: dict[str, bytes], config: dict, *,
    coverage_source: str = "manifest.json:settlement_complete_through",
) -> Snapshot:
    """上傳與 CLI 共用同一組驗證與規則；呼叫前不寫入資料庫。"""
    if set(files) != set(SCHEMAS):
        raise ValueError("必須一次提供 orders、payments、settlements 三份 CSV")
    rows = {name: parse_rows(files[name], f"{name}.csv", set(schema), schema[0])
            for name, schema in SCHEMAS.items()}
    if config["currency"] != "TWD":
        raise ValueError("目前只支援 TWD")
    if not isinstance(config["order_sync_grace_minutes"], int) or config["order_sync_grace_minutes"] < 0:
        raise ValueError("同步等待時間須為非負整數")
    as_of = aware_time(config["as_of"])
    local_day = as_of.astimezone(ZoneInfo(config["timezone"])).date()
    for source, source_rows in rows.items():
        for row in source_rows:
            data = row.data
            required = set(SCHEMAS[source])
            if source == "payments" and data["status"] == "FAILED":
                required -= {"paid_at", "expected_settlement_date"}
                if data["paid_at"] or data["expected_settlement_date"]:
                    raise ValueError(f"{row.file}:{row.line} 失敗付款不應填成功時間或結算日")
            if any(not data[field].strip() for field in required):
                raise ValueError(f"{row.file}:{row.line} 必要欄位不可空白")
            if data["currency"] != "TWD":
                raise ValueError(f"{row.file}:{row.line} 幣別必須為 TWD")
            for field in {"amount", "gross_amount", "fee", "net_amount"} & data.keys():
                value = parse_money(data[field])
                if value >= 10**16:
                    raise ValueError(f"{row.file}:{row.line} {field} 超出 NUMERIC(18,2) 範圍")
            if source == "orders":
                if aware_time(data["snapshot_at"]) > as_of:
                    raise ValueError(f"{row.file}:{row.line} 訂單快照晚於基準時間")
            elif source == "settlements":
                if date.fromisoformat(data["settlement_date"]) > local_day:
                    raise ValueError(f"{row.file}:{row.line} 結算日期晚於基準日")
    orders, payments, settlements = (rows[name] for name in SCHEMAS)
    cases = find_unpaid_orders(orders, payments, as_of=as_of, grace_minutes=config["order_sync_grace_minutes"])
    cases.extend(find_amount_mismatches(orders, payments, as_of=as_of))
    coverage = config["settlement_complete_through"]
    cases.extend(find_missing_settlements(
        payments, settlements, as_of=as_of,
        complete_through=date.fromisoformat(coverage) if coverage else None,
        timezone=config["timezone"],
        coverage_source=coverage_source,
    ))
    cases.extend(find_settlement_arithmetic_mismatches(
        payments, settlements, as_of=as_of, timezone=config["timezone"],
    ))
    report = {"as_of": config["as_of"],
              "rules_executed": [RULE, AMOUNT_RULE, MISSING_SETTLEMENT_RULE, SETTLEMENT_ARITHMETIC_RULE],
              "case_count": len(cases), "cases": cases}
    return Snapshot(files, rows, config, report)
