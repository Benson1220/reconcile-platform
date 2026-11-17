"""Validate fixture structure; deliberately preserve business discrepancies."""
import csv
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample"
SCHEMAS = {
    "orders": ["order_id", "amount", "currency", "payment_status", "snapshot_at"],
    "payments": ["payment_id", "order_id", "amount", "currency", "status", "paid_at", "expected_settlement_date"],
    "settlements": ["settlement_id", "payment_id", "gross_amount", "fee", "net_amount", "currency", "settlement_date"],
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def timestamp(value: str) -> None:
    require(datetime.fromisoformat(value).utcoffset() is not None, "時間必須含時區")


def money(value: str) -> None:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"金額格式錯誤：{value}") from exc
    require(amount.is_finite() and amount >= 0, f"金額必須為非負有限值：{value}")
    require(amount.as_tuple().exponent >= -2, f"金額最多兩位小數：{value}")


def validate() -> None:
    manifest = json.loads((SAMPLE_DIR / "manifest.json").read_text(encoding="utf-8"))
    tables = {}
    for name, schema in SCHEMAS.items():
        with (SAMPLE_DIR / f"{name}.csv").open(encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            require(reader.fieldnames == schema, f"{name} 欄位不符：{reader.fieldnames}")
            rows = list(reader)
        require(len(rows) == manifest["expected_counts"][name], f"{name} 筆數不符")
        seen = set()
        for row_number, row in enumerate(rows, start=2):
            prefix = f"{name}.csv 第 {row_number} 列"
            require(None not in row and all(v is not None for v in row.values()), f"{prefix} 欄位數不符")
            required = set(schema) - {"paid_at", "expected_settlement_date"}
            require(all(row[k].strip() for k in required), f"{prefix} 缺少必要值")
            key = row[schema[0]]
            require(key not in seen, f"{prefix} ID 重複：{key}")
            seen.add(key)
            require(row["currency"] == manifest["currency"], f"{prefix} 幣別不符")
            for field in ("amount", "gross_amount", "fee", "net_amount"):
                if field in row:
                    money(row[field])
            if name == "orders":
                require(row["payment_status"] in {"PAID", "UNPAID"}, f"{prefix} 狀態錯誤")
                timestamp(row["snapshot_at"])
            elif name == "payments":
                require(row["status"] in {"SUCCESS", "FAILED"}, f"{prefix} 狀態錯誤")
                if row["status"] == "SUCCESS":
                    timestamp(row["paid_at"])
                    date.fromisoformat(row["expected_settlement_date"])
                else:
                    require(not row["paid_at"] and not row["expected_settlement_date"], f"{prefix} 失敗付款不應有成功時間／結算日")
            else:
                date.fromisoformat(row["settlement_date"])
        tables[name] = rows
        print(f"OK {name}.csv: {len(rows)} 筆")

    order_ids = {r["order_id"] for r in tables["orders"]}
    payment_ids = {r["payment_id"] for r in tables["payments"]}
    require(all(r["order_id"] in order_ids for r in tables["payments"]), "付款引用不存在的訂單")
    require(all(r["payment_id"] in payment_ids for r in tables["settlements"]), "結算引用不存在的付款")
    print("OK 格式、必要值、唯一 ID、金額型別、時區與資料關聯")
    print("注意：這是資料格式驗證，尚未執行四種業務異常的比對規則。")


if __name__ == "__main__":
    try:
        validate()
    except (ValueError, KeyError, OSError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
