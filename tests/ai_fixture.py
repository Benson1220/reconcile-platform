"""Only used by isolated tests: this is not a model response or production fallback."""
from app.ai_summary import SummaryContent


def fake_summary(payload):
    ids = list(payload["evidence"])
    anomaly = payload["anomaly"]
    arithmetic = anomaly["rule"] == "SETTLEMENT_ARITHMETIC_MISMATCH"
    fact = ("總額 2000.00 減手續費 40.00，依算式應為 1960.00；紀錄淨額為 1970.00，差額 10.00。"
            if arithmetic else "提供的比對結果標示此案件存在資料差異，需要人工核對。")
    return SummaryContent(
        overview="【測試回應，非實際 AI 產生】以下內容僅用來驗證摘要顯示、證據引用與儲存流程。",
        confirmed_facts=[{"text": fact, "evidence_ids": [ids[0]]}],
        possible_causes=[{"text": "可能為資料欄位轉換問題，尚待驗證。", "evidence_ids": [ids[0]], "how_to_verify": "核對原始檔案與匯入欄位對應。"}],
        recommended_steps=[{"text": "先核對引用的原始檔案列，再向資料提供方確認欄位意義與處理方式。", "evidence_ids": ids}],
        missing_information=["原始結算報表與資料提供方的確認結果。"],
    ), {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200}
