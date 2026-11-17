"""本機固定規則摘要：不建立模型客戶端、不讀取金鑰、不發出外部請求。"""
from datetime import date, datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.ai_summary import SummaryContent, make_input
from app.database import _case_detail
from app.pipeline import json_digest
from app.reconciliation import (
    AMOUNT_RULE, MISSING_SETTLEMENT_RULE, RULE, SETTLEMENT_ARITHMETIC_RULE, parse_money,
)

RULE_VERSION = "1.0.0"


class RuleSummaryError(ValueError):
    pass


def build_summary(case: dict) -> tuple[SummaryContent, dict]:
    payload = make_input(case)
    catalog = payload["evidence"]

    def record(source: str, key: str, value: str):
        matches = [(evidence_id, item["record"]) for evidence_id, item in catalog.items()
                   if item.get("file") == f"{source}.csv" and item.get("record", {}).get(key) == value]
        if len(matches) != 1:
            raise RuleSummaryError(f"缺少或無法唯一確認 {source} 的原始證據")
        return matches[0]

    def statement(text, refs):
        return {"text": text, "evidence_ids": refs}

    rule = case["rule"]
    order_id, payment_id = case["order_id"], case["payment_id"]
    payment_ref, payment = record("payments", "payment_id", payment_id)
    if payment["status"] != "SUCCESS" or payment["order_id"] != order_id:
        raise RuleSummaryError("付款證據與案件資料不符")

    if rule in {RULE, AMOUNT_RULE}:
        order_ref, order = record("orders", "order_id", order_id)
        both = [order_ref, payment_ref]
        if rule == RULE:
            if order["payment_status"] != "UNPAID":
                raise RuleSummaryError("訂單證據不符合狀態未更新規則")
            overview = f"訂單 {order_id} 的付款 {payment_id} 已成功，但提供的訂單快照仍顯示未付款。"
            facts = [
                statement(f"付款 {payment_id} 狀態為 SUCCESS，成功時間為 {payment['paid_at']}。", [payment_ref]),
                statement(f"訂單 {order_id} 在 {order['snapshot_at']} 的快照狀態為 UNPAID。", [order_ref]),
            ]
            steps = [
                statement("先確認訂單快照與付款時間是否屬於同一查核範圍，並核對訂單、付款編號。", both),
                statement("取得付款通知接收、重試與訂單狀態更新日誌，按付款時間追查是否有失敗紀錄。", [payment_ref]),
                statement("依原始交易結果確認正確狀態，經確認與既有作業流程處理後，再記錄驗證結果。", both),
            ]
            missing = ["付款通知與重試日誌。", "訂單狀態更新日誌及資料同步紀錄。"]
        else:
            if order["currency"] != payment["currency"] or order["currency"] != "TWD":
                raise RuleSummaryError("幣別不符，無法直接比較金額")
            order_amount, payment_amount = parse_money(order["amount"]), parse_money(payment["amount"])
            difference = payment_amount - order_amount
            if not difference:
                raise RuleSummaryError("原始金額相同，不符合金額差異規則")
            direction = "少付" if difference < 0 else "多付"
            overview = f"訂單 {order_id} 金額 {order_amount:.2f} TWD，成功付款 {payment_amount:.2f} TWD，依兩份紀錄比較為{direction} {abs(difference):.2f} TWD。"
            facts = [
                statement(f"訂單應付金額為 {order_amount:.2f} TWD。", [order_ref]),
                statement(f"成功付款金額為 {payment_amount:.2f} TWD。", [payment_ref]),
                statement(f"付款金額 − 訂單金額 = {difference:.2f} TWD；負數為少付，正數為多付。", both),
            ]
            steps = [
                statement("核對付款當時的訂單版本、價格異動與原始付款明細。", both),
                statement("向業務確認折扣、改單或分次付款等情境是否影響本次比對基準。", both),
                statement("確認差異原因與正確金額後，依既有流程處理，再重新比對並留下備註。", both),
            ]
            missing = ["付款當時的訂單版本與價格異動紀錄。", "原始付款明細與業務確認結果。"]
    elif rule == MISSING_SETTLEMENT_RULE:
        matches = [(evidence_id, item) for evidence_id, item in catalog.items()
                   if item.get("type") == "absence_check" and item.get("lookup_field") == "payment_id"
                   and item.get("lookup_value") == payment_id and item.get("matching_rows") == 0]
        if len(matches) != 1:
            raise RuleSummaryError("缺少可確認的查無結算證據")
        absence_ref, absence = matches[0]
        due = date.fromisoformat(payment["expected_settlement_date"])
        coverage = date.fromisoformat(absence["complete_through"])
        as_of = datetime.fromisoformat(case["as_of"])
        if as_of.utcoffset() is None or due >= as_of.astimezone(ZoneInfo("Asia/Taipei")).date() or coverage < due:
            raise RuleSummaryError("時間或涵蓋範圍不足，不能判定結算缺漏")
        payload["anomaly"]["as_of"] = case["as_of"]
        both = [payment_ref, absence_ref]
        overview = f"成功付款 {payment_id} 預期於 {due} 結算；在本次提供的結算資料內查無對應紀錄。這不等於已證明資金未入帳。"
        facts = [
            statement(f"付款狀態為 SUCCESS，預期結算日為 {due}。", [payment_ref]),
            statement(f"以付款編號 {payment_id} 查詢 {absence['file']}，符合筆數為 0，掃描 {absence['rows_scanned']} 筆。", [absence_ref]),
            statement(f"本次比對基準時間為 {case['as_of']}；資料來源宣告完整涵蓋至 {coverage}（含），已涵蓋預期結算日。", both),
        ]
        steps = [
            statement("先向資料提供方確認結算批次是否完整、是否有延遲或漏匯入的檔案。", [absence_ref]),
            statement("以付款編號與預期結算日期查詢付款平台的原始結算明細，確認是否延後或使用其他對應編號。", both),
            statement("取得補充資料後重新匯入與比對，再記錄實際查核結果；不要僅憑查無紀錄認定未入帳。", both),
        ]
        missing = ["來源結算批次清單與完整性確認。", "付款平台原始結算明細或延後結算的說明。"]
    elif rule == SETTLEMENT_ARITHMETIC_RULE:
        settlement_ref, settlement = record("settlements", "settlement_id", case["settlement_id"])
        if settlement["payment_id"] != payment_id or settlement["currency"] != payment["currency"]:
            raise RuleSummaryError("結算證據與付款不符")
        gross, fee, net = (parse_money(settlement[key]) for key in ("gross_amount", "fee", "net_amount"))
        expected = gross - fee
        difference = net - expected
        if not difference:
            raise RuleSummaryError("算式一致，不符合結算算式差異規則")
        direction = "偏高" if difference > 0 else "偏低"
        overview = f"結算 {settlement['settlement_id']}：{gross:.2f} − {fee:.2f} 應為 {expected:.2f}，紀錄淨額為 {net:.2f}，{direction} {abs(difference):.2f} {settlement['currency']}。"
        both = [settlement_ref, payment_ref]
        facts = [
            statement(f"原始結算總額 {gross:.2f}、手續費 {fee:.2f}、紀錄淨額 {net:.2f}。", [settlement_ref]),
            statement(f"依總額減手續費計算的淨額為 {expected:.2f}，紀錄淨額減應有淨額的差額為 {difference:.2f}。", [settlement_ref]),
            statement(f"結算對應成功付款 {payment_id}，關聯訂單 {order_id}。", both),
        ]
        steps = [
            statement("核對原始結算檔的總額、手續費及淨額欄位，確認匯入欄位對應與金額單位。", [settlement_ref]),
            statement("向資料提供方確認費用與淨額定義，是否另含調整項目；先確認哪個欄位有誤。", [settlement_ref]),
            statement("取得確認結果後依既有流程處理，重新比對並附上驗證紀錄，再決定是否結案。", both),
        ]
        missing = ["原始結算報表與匯入欄位對應。", "費用、淨額定義與資料提供方的確認結果。"]
    else:
        raise RuleSummaryError("此異常類型尚未建立規則式摘要範本")
    content = SummaryContent(overview=overview, confirmed_facts=facts, possible_causes=[],
                             recommended_steps=steps, missing_information=missing)
    known = set(catalog)
    if any(not set(item.evidence_ids).issubset(known) for item in [*content.confirmed_facts, *content.recommended_steps]):
        raise RuleSummaryError("摘要引用不存在的證據")
    return content, payload


def result(conn, case_id: int, row: dict | None, reused=False) -> dict:
    version = conn.execute("SELECT version FROM cases WHERE id=%s", (case_id,)).fetchone()["version"]
    return {"mode": "rules", "case_id": case_id, "current_case_version": version,
            "reused": reused, "stale": row is not None and row["case_version"] != version, "summary": row}


def read_summary(dsn: str, case_id: int) -> dict | None:
    with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row) as conn:
        if not conn.execute("SELECT 1 FROM cases WHERE id=%s", (case_id,)).fetchone():
            return None
        row = conn.execute("SELECT * FROM case_rule_summaries WHERE case_id=%s ORDER BY id DESC LIMIT 1", (case_id,)).fetchone()
        return result(conn, case_id, row)


def generate_summary(dsn: str, case_id: int) -> dict | None:
    with psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row) as conn:
        lock_key = int.from_bytes(sha256(f"rule-summary:{case_id}".encode()).digest()[:8], signed=True)
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
        case = _case_detail(conn, case_id)
        if case is None:
            return None
        content, payload = build_summary(case)
        fingerprint = json_digest(payload)
        row = conn.execute(
            "SELECT * FROM case_rule_summaries WHERE case_id=%s AND case_version=%s AND rule_version=%s AND input_hash=%s",
            (case_id, case["version"], RULE_VERSION, fingerprint),
        ).fetchone()
        if row:
            return result(conn, case_id, row, reused=True)
        row = conn.execute(
            """INSERT INTO case_rule_summaries (case_id,case_version,rule_version,input_hash,input_snapshot,content)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
            (case_id, case["version"], RULE_VERSION, fingerprint, Jsonb(payload), Jsonb(content.model_dump())),
        ).fetchone()
        return result(conn, case_id, row)
