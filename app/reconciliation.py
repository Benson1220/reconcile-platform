"""四種訂單、付款與結算比對規則；不讀取 manifest 的預期答案。"""
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

RULE = "PAID_BUT_ORDER_UNPAID"
AMOUNT_RULE = "PAYMENT_AMOUNT_MISMATCH"
MISSING_SETTLEMENT_RULE = "SETTLEMENT_MISSING"
SETTLEMENT_ARITHMETIC_RULE = "SETTLEMENT_ARITHMETIC_MISMATCH"


@dataclass(frozen=True)
class SourceRow:
    data: dict[str, str]
    file: str
    line: int


def load_rows(path: Path, required: set[str], key: str) -> list[SourceRow]:
    """保留來源與列號，並拒絕缺欄位、重複鍵等不可靠輸入。"""
    return parse_rows(path.read_bytes(), path.name, required, key)


def parse_rows(content: bytes, filename: str, required: set[str], key: str) -> list[SourceRow]:
    """比對與入庫共用相同的記憶體快照，避免檔案在過程中改變。"""
    rows = []
    seen = set()
    with io.StringIO(content.decode("utf-8-sig"), newline="") as file:
        reader = csv.DictReader(file, strict=True)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not required.issubset(fields):
            raise ValueError(f"{filename} 缺少必要欄位或含重複欄名")
        for data in reader:
            if None in data or any(value is None for value in data.values()):
                raise ValueError(f"{filename}:{reader.line_num} 欄位數不符")
            if not data[key].strip() or data[key] in seen:
                raise ValueError(f"{filename}:{reader.line_num} 編號空白或重複")
            seen.add(data[key])
            rows.append(SourceRow(data, filename, reader.line_num))
    return rows


def aware_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError(f"時間缺少時區：{value}")
    return parsed


def find_unpaid_orders(
    orders: list[SourceRow],
    payments: list[SourceRow],
    *,
    as_of: datetime,
    grace_minutes: int = 15,
) -> list[dict]:
    """成功付款後，訂單快照已滿等待時間仍未付款，才建立異常。"""
    if as_of.utcoffset() is None or grace_minutes < 0:
        raise ValueError("基準時間須含時區，等待分鐘數不得為負")
    order_index = {}
    for order in orders:
        order_id = order.data["order_id"]
        if order_id in order_index:
            raise ValueError(f"訂單編號重複：{order_id}")
        if order.data["payment_status"] not in {"PAID", "UNPAID"}:
            raise ValueError(f"訂單狀態不支援：{order_id}")
        aware_time(order.data["snapshot_at"])
        order_index[order_id] = order

    cases = []
    for payment in payments:
        p = payment.data
        if p["status"] not in {"SUCCESS", "FAILED"}:
            raise ValueError(f"付款狀態不支援：{p['payment_id']}")
        if p["order_id"] not in order_index:
            raise ValueError(f"缺少訂單 {p['order_id']}，無法完成本次比對")
        if p["status"] != "SUCCESS":
            continue

        order = order_index[p["order_id"]]
        o = order.data
        paid_at = aware_time(p["paid_at"])
        snapshot_at = aware_time(o["snapshot_at"])
        if paid_at > as_of or snapshot_at > as_of:
            raise ValueError("輸入含基準時間之後的成功付款或相關訂單快照")

        # 判斷訂單是否有時間同步，必須看「快照時間」，不能只看現在時間。
        if snapshot_at < paid_at + timedelta(minutes=grace_minutes):
            continue
        if o["payment_status"] != "UNPAID":
            continue

        cases.append({
            "case_key": f"{RULE}:{p['payment_id']}",
            "rule": RULE,
            "order_id": p["order_id"],
            "payment_id": p["payment_id"],
            "summary": "付款成功，但超過同步等待時間的訂單快照仍顯示未付款",
            "root_cause": "待確認；目前沒有通知日誌，無法判斷根因",
            "evidence": [
                {"file": order.file, "line": order.line, "record": dict(o)},
                {"file": payment.file, "line": payment.line, "record": dict(p)},
            ],
        })
    return cases


def parse_money(value: str) -> Decimal:
    """由原始字串建立精確金額；不四捨五入掩蓋錯誤資料。"""
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"金額格式錯誤：{value!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"金額須為非負有限值：{value!r}")
    if amount.as_tuple().exponent < -2:
        raise ValueError(f"金額最多兩位小數：{value!r}")
    return amount


def find_amount_mismatches(
    orders: list[SourceRow],
    payments: list[SourceRow],
    *,
    as_of: datetime,
) -> list[dict]:
    """單一 TWD、每張訂單最多一筆成功付款的學習版規則。"""
    if as_of.utcoffset() is None:
        raise ValueError("基準時間須含時區")
    order_index = {}
    for order in orders:
        order_id = order.data["order_id"]
        if order_id in order_index:
            raise ValueError(f"訂單編號重複：{order_id}")
        order_index[order_id] = order

    cases = []
    successful_orders = set()
    payment_ids = set()
    for payment in payments:
        p = payment.data
        if p["payment_id"] in payment_ids:
            raise ValueError(f"付款編號重複：{p['payment_id']}")
        payment_ids.add(p["payment_id"])
        if p["status"] not in {"SUCCESS", "FAILED"}:
            raise ValueError(f"付款狀態不支援：{p['payment_id']}")
        if p["status"] != "SUCCESS":
            continue
        if p["order_id"] not in order_index:
            raise ValueError(f"缺少訂單 {p['order_id']}，無法完成本次比對")
        if p["order_id"] in successful_orders:
            raise ValueError(f"訂單 {p['order_id']} 有多筆成功付款，超出目前規則範圍")
        successful_orders.add(p["order_id"])

        order = order_index[p["order_id"]]
        o = order.data
        if aware_time(p["paid_at"]) > as_of or aware_time(o["snapshot_at"]) > as_of:
            raise ValueError("輸入含基準時間之後的成功付款或相關訂單快照")
        # 第一版沒有匯率換算。不同幣別不能直接相減，也不能算作正常。
        if o["currency"] != p["currency"] or o["currency"] != "TWD":
            raise ValueError(f"訂單 {p['order_id']} 幣別不同或非 TWD，暫不支援比對")

        order_amount = parse_money(o["amount"])
        payment_amount = parse_money(p["amount"])
        if order_amount == payment_amount:
            continue

        # 負數表示少付，正數表示多付；輸出字串以保留十進位精確度。
        difference = payment_amount - order_amount
        cases.append({
            "case_key": f"{AMOUNT_RULE}:{p['payment_id']}",
            "rule": AMOUNT_RULE,
            "order_id": p["order_id"],
            "payment_id": p["payment_id"],
            "summary": "訂單金額與成功付款金額不一致",
            "currency": o["currency"],
            "order_amount": format(order_amount, ".2f"),
            "payment_amount": format(payment_amount, ".2f"),
            "difference": format(difference, ".2f"),
            "difference_definition": "付款金額 - 訂單金額；負數為少付，正數為多付",
            "root_cause": "待確認；金額差異不代表已確認造成差異的原因",
            "evidence": [
                {"file": order.file, "line": order.line, "record": dict(o)},
                {"file": payment.file, "line": payment.line, "record": dict(p)},
            ],
        })
    return cases


def find_missing_settlements(
    payments: list[SourceRow],
    settlements: list[SourceRow],
    *,
    as_of: datetime,
    complete_through: date | None,
    timezone: str = "Asia/Taipei",
    settlement_file: str = "settlements.csv",
    coverage_source: str = "manifest.json:settlement_complete_through",
) -> list[dict]:
    """預期結算日已結束且資料完整，才將查無紀錄視為缺漏。"""
    if as_of.utcoffset() is None:
        raise ValueError("基準時間須含時區")
    local_day = as_of.astimezone(ZoneInfo(timezone)).date()
    if complete_through is None:
        raise ValueError("缺少結算資料完整涵蓋日期，無法判定結算缺漏")
    if complete_through >= local_day:
        raise ValueError("結算資料完整涵蓋日期必須早於基準日，不能宣告尚未結束的日期完整")

    payment_index = {}
    for payment in payments:
        p = payment.data
        if p["payment_id"] in payment_index:
            raise ValueError(f"付款編號重複：{p['payment_id']}")
        if p["status"] not in {"SUCCESS", "FAILED"}:
            raise ValueError(f"付款狀態不支援：{p['payment_id']}")
        payment_index[p["payment_id"]] = payment

    # set 用來快速確認某付款是否至少有一筆結算；不檢查結算金額算式。
    settled_payment_ids = set()
    settlement_ids = set()
    for settlement in settlements:
        s = settlement.data
        if s["settlement_id"] in settlement_ids:
            raise ValueError(f"結算編號重複：{s['settlement_id']}")
        settlement_ids.add(s["settlement_id"])
        if s["payment_id"] not in payment_index:
            raise ValueError(f"結算引用不存在的付款：{s['payment_id']}")
        if date.fromisoformat(s["settlement_date"]) > local_day:
            raise ValueError("結算日期晚於基準日")
        settled_payment_ids.add(s["payment_id"])

    cases = []
    for payment in payments:
        p = payment.data
        if p["status"] != "SUCCESS":
            continue
        paid_at = aware_time(p["paid_at"])
        if paid_at > as_of:
            raise ValueError("成功付款時間晚於基準時間")
        expected_date = date.fromisoformat(p["expected_settlement_date"])
        if expected_date < paid_at.astimezone(ZoneInfo(timezone)).date():
            raise ValueError(f"預期結算日早於付款日：{p['payment_id']}")

        # 當天仍可能結算；第一版等整個預期結算日結束後才判斷逾期。
        if expected_date >= local_day:
            continue
        if p["payment_id"] in settled_payment_ids:
            continue
        # 查無資料但涵蓋範圍不足時中止，不能輸出「沒有異常」。
        if expected_date > complete_through:
            raise ValueError(
                f"資料不足：付款 {p['payment_id']} 預期於 {expected_date} 結算，"
                f"但結算資料僅完整涵蓋至 {complete_through}"
            )

        cases.append({
            "case_key": f"{MISSING_SETTLEMENT_RULE}:{p['payment_id']}",
            "rule": MISSING_SETTLEMENT_RULE,
            "order_id": p["order_id"],
            "payment_id": p["payment_id"],
            "summary": "已過預期結算日，完整涵蓋的結算資料中查無對應付款紀錄",
            "expected_settlement_date": expected_date.isoformat(),
            "as_of": as_of.isoformat(),
            "root_cause": "待確認；查無結算紀錄不代表已確認資金未入帳或結算失敗",
            "evidence": [
                {"file": payment.file, "line": payment.line, "record": dict(p)},
                {
                    "type": "absence_check",
                    "file": settlement_file,
                    "lookup_field": "payment_id",
                    "lookup_value": p["payment_id"],
                    "matching_rows": 0,
                    "rows_scanned": len(settlements),
                    "complete_through": complete_through.isoformat(),
                    "coverage_source": coverage_source,
                    "coverage_note": "由資料提供方宣告；此值來自執行設定，不代表程式已驗證完整性",
                },
            ],
        })
    return cases


def find_settlement_arithmetic_mismatches(
    payments: list[SourceRow],
    settlements: list[SourceRow],
    *,
    as_of: datetime,
    timezone: str = "Asia/Taipei",
) -> list[dict]:
    """檢查已提供結算的總額減手續費是否等於淨額；不推算費率。"""
    if as_of.utcoffset() is None:
        raise ValueError("基準時間須含時區")
    local_day = as_of.astimezone(ZoneInfo(timezone)).date()
    payment_index = {}
    for payment in payments:
        payment_id = payment.data["payment_id"]
        if payment_id in payment_index:
            raise ValueError(f"付款編號重複：{payment_id}")
        payment_index[payment_id] = payment

    cases = []
    settlement_ids = set()
    for settlement in settlements:
        s = settlement.data
        if s["settlement_id"] in settlement_ids:
            raise ValueError(f"結算編號重複：{s['settlement_id']}")
        settlement_ids.add(s["settlement_id"])
        if s["payment_id"] not in payment_index:
            raise ValueError(f"結算引用不存在的付款：{s['payment_id']}")
        payment = payment_index[s["payment_id"]]
        p = payment.data
        if p["status"] != "SUCCESS":
            raise ValueError(f"結算對應的付款不是 SUCCESS：{s['payment_id']}")
        paid_at = aware_time(p["paid_at"])
        settlement_day = date.fromisoformat(s["settlement_date"])
        if paid_at > as_of or settlement_day > local_day:
            raise ValueError("付款或結算晚於基準時間／日期")
        if settlement_day < paid_at.astimezone(ZoneInfo(timezone)).date():
            raise ValueError(f"結算日期早於付款日：{s['settlement_id']}")
        if s["currency"] != p["currency"] or s["currency"] != "TWD":
            raise ValueError(f"結算 {s['settlement_id']} 幣別不同或非 TWD，暫不支援比對")

        gross_amount = parse_money(s["gross_amount"])
        fee = parse_money(s["fee"])
        net_amount = parse_money(s["net_amount"])
        expected_net_amount = gross_amount - fee
        if expected_net_amount == net_amount:
            continue

        # 正數表示紀錄中的淨額偏高，負數表示偏低。
        difference = net_amount - expected_net_amount
        cases.append({
            "case_key": f"{SETTLEMENT_ARITHMETIC_RULE}:{s['settlement_id']}",
            "rule": SETTLEMENT_ARITHMETIC_RULE,
            "order_id": p["order_id"],
            "payment_id": s["payment_id"],
            "settlement_id": s["settlement_id"],
            "summary": "結算總額減手續費，不等於紀錄中的淨額",
            "currency": s["currency"],
            "gross_amount": format(gross_amount, ".2f"),
            "fee": format(fee, ".2f"),
            "net_amount": format(net_amount, ".2f"),
            "expected_net_amount": format(expected_net_amount, ".2f"),
            "difference": format(difference, ".2f"),
            "difference_definition": "紀錄淨額 - 應有淨額；正數為偏高，負數為偏低",
            "root_cause": "待確認；僅確認算式不一致，尚未確認哪個欄位錯誤或實際入帳金額",
            "evidence": [
                {"file": settlement.file, "line": settlement.line, "record": dict(s)},
                {"file": payment.file, "line": payment.line, "record": dict(p)},
            ],
        })
    return cases
