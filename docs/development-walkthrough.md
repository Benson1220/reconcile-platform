# 跨系統資料差異追查與證據整理平台

以合成的「訂單 → 付款 → 結算」資料，練習跨系統整合、異常追查與可追溯的證據整理。

## 目前使用：本機規則式摘要

首頁已改用本機規則式摘要，不需 API 金鑰或額度，不呼叫外部模型。
請先閱讀 [規則式摘要逐步操作說明](rule-summary-step-by-step.md)。
既有 AI 章節保留為歷史參考，外部 AI 產生預設停用。

## 第一階段：開發環境與模擬資料

目前完成：Python 專案骨架、FastAPI 健康檢查、三份 CSV、資料格式驗證程式。
第二階段已完成四條業務規則，第三階段已完成 PostgreSQL 來源與結果儲存。
請接著閱讀 [PostgreSQL 逐步操作說明](postgresql-step-by-step.md)。
第四階段已完成 CSV 上傳與結果查詢 API，請閱讀 [API 逐步操作說明](api-step-by-step.md)。
第五階段已完成網頁工作台，開啟 http://127.0.0.1:8000/ 即可操作。
請閱讀 [網頁逐步操作說明](web-step-by-step.md)。
第六階段已完成案件狀態、處理備註與結案，請閱讀 [案件處理逐步說明](case-workflow-step-by-step.md)。
第七階段已完成 AI 摘要串接、介面與模擬測試，請閱讀 [AI 摘要逐步操作說明](ai-summary-step-by-step.md)。
目前已改用規則式摘要，外部 AI 產生預設停用；登入尚未實作。
此階段不用 Docker、PostgreSQL 或模型 API 金鑰，也不會產生模型使用費。

### 1. 進入資料夾與啟用環境

在 macOS 終端機執行：

```bash
cd /Users/benson/Documents/ChatGPT/future/reconcile-platform
source .venv/bin/activate
python --version
```

應顯示 Python 3.11.x。虛擬環境將本專案套件與系統 Python 隔離。
每開一個新的終端機，都需要重新進入資料夾並啟用環境。

若在另一台電腦重新建置（需先備妥 Python 3.11 以上）：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install --no-deps -e .
```

`pyproject.toml` 宣告直接依賴，`requirements-lock.txt` 記錄此階段實際安裝的版本。
鎖定檔適用於此 Python 3.11 開發環境；跨平台重新安裝時仍需驗證。

### 2. 檢查三份資料

```bash
python scripts/validate_sample_data.py
```

預期顯示 orders 8 筆、payments 7 筆、settlements 4 筆，以及格式驗證通過。
這個程式只檢查欄位、型別、必要值、唯一 ID、時區與資料關聯。
故意放入的業務異常會保留，下一階段才撰寫偵測規則。

### 3. 啟動後端

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

保持終端機開啟，在瀏覽器查看：

- 健康檢查：http://127.0.0.1:8000/health
- API 互動文件：http://127.0.0.1:8000/docs

健康檢查會回傳 `{"status":"ok","stage":"csv-upload-and-query-api"}`。
`/docs` 頁面可對 `GET /health` 點選 Try it out → Execute。
此處只證明後端程序正常，尚未檢查資料庫。根路徑 `/` 現已提供網頁工作台，可上傳 CSV 與查看證據。
在終端機按 Control+C 停止服務；輸入 `deactivate` 離開虛擬環境。

## 模擬資料的規則

全部使用虛構資料、TWD、Asia/Taipei 時區；一張訂單最多一筆成功付款，不處理退款、分期與拆單。
訂單金額及結算總額均以付款金額為比較基準；結算淨額另扣除手續費。

- 訂單快照：2026-11-17 09:00 +08:00。
- 比對基準時間固定為同一時間，避免隔天執行時結果改變。
- 訂單同步等待時間：15 分鐘。
- 結算資料宣告完整涵蓋至 2026-11-16（含）；這是合成資料的設定，不是系統自行證明的事實。
- 預期結算日由付款資料直接提供，第一版不推算銀行工作日或假日。
- 只有預期結算日已完整涵蓋、且已經過期的成功付款，才判定結算缺漏。

### 檔案與欄位

`data/sample/orders.csv`：`order_id` 是訂單鍵；`amount` 是應付金額；`payment_status` 是快照當下的狀態；`snapshot_at` 是快照時間。

`data/sample/payments.csv`：`payment_id` 是交易鍵；`order_id` 關聯訂單；`amount` 是本次付款金額；`status` 為 SUCCESS 或 FAILED；失敗交易不填 `paid_at` 與 `expected_settlement_date`。

`data/sample/settlements.csv`：`settlement_id` 是結算鍵；`payment_id` 關聯付款；`gross_amount` 是總額；`fee` 是手續費；`net_amount` 是淨額；`settlement_date` 是結算日期。

CSV 使用 UTF-8、逗號分隔、第一列為欄位名稱。原始檔案列號將作為後續證據來源。

### 每筆資料的設計目的

| 訂單 | 付款 | 結算 | 預期結果 |
|---|---|---|---|
| O1001：1000、未付款 | P9001：1000、成功 | 總額 1000、費用 20、淨額 980 | 付款成功但訂單未更新 |
| O1002：1500、已付款 | P9002：1400、成功 | 總額 1400、費用 28、淨額 1372 | 訂單與付款金額不同 |
| O1003：800、已付款 | P9003：800、成功，11/16 預期結算 | 無 | 結算缺漏 |
| O1004：2000、已付款 | P9004：2000、成功 | 總額 2000、費用 40、淨額 1970 | 結算算式錯誤，應為 1960 |
| O1005：500、已付款 | P9005：500、成功 | 總額 500、費用 10、淨額 490 | 正常，不應誤報 |
| O1006：600、已付款 | P9006：600、成功，11/18 預期結算 | 無 | 尚未到期，不應誤報 |
| O1007：700、未付款 | P9007：失敗 | 無 | 付款未成功，不應誤報 |
| O1008：900、未付款 | 無 | 無 | 未付款訂單，不應誤報 |

預期共四個異常；機器可讀的基準答案放在 `data/sample/manifest.json`。
未來比對引擎只讀取 manifest 的執行條件，不得使用 `expected_cases` 產生結果；基準答案僅用來驗證。
目前沒有通知日誌，因此 O1001 的根因仍未知，不能認定為通知失敗。

## 專案結構

```text
reconcile-platform/
├── app/main.py                  # 後端入口與健康檢查
├── data/sample/
│   ├── orders.csv
│   ├── payments.csv
│   ├── settlements.csv
│   └── manifest.json            # 測試資料條件與預期結果
├── scripts/validate_sample_data.py
├── pyproject.toml
└── requirements-lock.txt
```

## 第二階段：第一條比對規則

在專案根目錄、已啟用虛擬環境的終端機執行：

```bash
python -m scripts.reconcile
```

目前執行 `PAID_BUT_ORDER_UNPAID`、`PAYMENT_AMOUNT_MISMATCH`、`SETTLEMENT_MISSING` 與 `SETTLEMENT_ARITHMETIC_MISMATCH` 四條規則。
預期輸出 `case_count: 4`。第一筆是訂單 `O1001`、付款 `P9001`，
證據是 `orders.csv` 第 2 列及 `payments.csv` 第 2 列。
第二筆是訂單 `O1002`、付款 `P9002`，證據在兩份檔案的第 3 列。
第三筆是訂單 `O1003`、付款 `P9003`，付款證據在 `payments.csv` 第 4 列，附結算查無紀錄的搜尋條件。
第四筆是訂單 `O1004`、付款 `P9004`、結算 `S8004`，結算原始證據在 `settlements.csv` 第 4 列。
這個指令只顯示結果，不寫入資料庫；要儲存請使用第三階段的 `python -m scripts.db import-sample`。

閱讀順序：先看 `scripts/reconcile.py` 入口，再看 `app/pipeline.py` 的共用主流程，最後看 `app/reconciliation.py`。

1. `load_rows()` 讀取 CSV，保留原始列內容、檔名與列號，檢查必要欄位與唯一編號。
2. `order_index` 用訂單編號建立字典，讓每筆付款直接找到對應訂單。
3. `find_unpaid_orders()` 檢查付款成功、訂單未付款，以及訂單快照是否已經過同步等待時間。
4. 組合異常摘要與來源證據；根因標記待確認。

時間判斷使用 `snapshot_at >= paid_at + 15 分鐘`，恰好滿 15 分鐘即納入檢查。
不能因為現在已過很久，就把付款前取得的舊訂單快照視為異常。
缺少對應訂單、無時區時間、未支援狀態或相關資料晚於固定基準時間時，會中止並顯示錯誤，
目前尚未做個別錯誤列隔離與資料不足案件管理。

執行測試：

```bash
python -m unittest discover -s tests -v
```

第一條規則有 9 項測試，包含模擬資料的預期結果、來源列號、已付款訂單、失敗付款、過舊快照、
等待期間、恰好滿 15 分鐘、時區換算與無效時間。加上其餘規則及入口測試，規則測試共 46 項。
第三階段另加入 6 項 PostgreSQL 整合測試，需設定 `RUN_DATABASE_TESTS=1` 並啟動資料庫；資料庫階段合計 52 項；加入網頁後總計 75 項，詳見 API 操作說明。
`expected_cases` 只由測試讀取，比對程式不讀答案。

## 第二條規則：訂單與付款金額不一致

不用安裝新套件，仍從專案根目錄執行 `python -m scripts.reconcile`。
只需執行這個入口，`app/reconciliation.py` 會自動被載入，不必另外執行。

閱讀 `app/reconciliation.py` 的 `parse_money()` 與 `find_amount_mismatches()`：

1. 將訂單依 `order_id` 建立字典。
2. 逐筆讀取付款，只處理 `SUCCESS`。
3. 檢查對應訂單、成功付款筆數及固定基準時間。
4. 確認兩邊幣別皆為 TWD。
5. 使用標準函式庫 `Decimal`，從 CSV 原始字串轉換金額，不經過 float。
6. 金額相同就略過；不同就輸出兩邊金額、差額及原始資料來源。

關鍵比較邏輯：

```python
order_amount = parse_money(o["amount"])
payment_amount = parse_money(p["amount"])
if order_amount == payment_amount:
    continue
difference = payment_amount - order_amount
```

O1002 的結果是 `1500.00` 與 `1400.00`，`difference` 為 `-100.00`。
負數表示少付，正數表示多付。輸出金額使用 JSON 字串保留十進位精確度。
`1500`、`1500.0`、`1500.00` 都視為相同金額。
非數字、非有限值、負金額或超過兩位小數會報錯，不自行四捨五入。

第一版只支援單一 TWD、每張訂單最多一筆成功付款。
不同幣別、非 TWD、重複付款 ID、同訂單多筆成功付款或缺少訂單會讓整次比對失敗，
不會把這些情況視為「沒有異常」；目前尚未做逐筆隔離。
此規則不扣除結算手續費，也不使用第一條規則的 15 分鐘等待條件。
它假設這份練習資料的訂單金額代表付款時應付金額；日後若支援改單，需取得歷史版本再比較。

可選練習：先備份 `payments.csv`，把 P9002 金額暫改為 `1500.00`，再次執行。
預期只剩 O1001、O1003 與 O1004 三筆異常。將金額還原為 `1400.00` 後，再跑全部測試。
修改練習期間，依固定基準答案驗證的測試會失敗，這是預期行為。

## 第三條規則：應結算但查無紀錄

仍執行 `python -m scripts.reconcile`，不用另外執行規則檔案，也不用安裝套件。
新增的函式是 `app/reconciliation.py` 中的 `find_missing_settlements()`。

第一版採用「日期」判定：預期結算日必須已完整結束，當天不報逾期。
先把 `as_of` 轉成設定的 Asia/Taipei 當地日期，再進行比較，不依賴電腦當下日期。
模擬資料的固定基準為 2026-11-17 09:00 +08:00，結算資料完整涵蓋至 2026-11-16（含）。
涵蓋宣告代表這組資料所有截至該日的相關結算已提供，不是只有當天的單日檔。
目前宣告來自 `manifest.json`；真實整合時要由來源批次清單等機制確認完整性。

判斷順序：

1. 從結算資料建立 `settled_payment_ids` 集合，以付款編號查找。
2. 只處理成功付款，驗證付款時間與預期結算日。
3. 預期結算日為今天或未來時略過，仍可能正常結算。
4. 已找到對應結算時略過；這條規則不檢查金額算式。
5. 若查無結算，檢查資料完整涵蓋日期。範圍不足時整次比對中止，顯示「資料不足」。
6. 過期、範圍足夠且查無紀錄時，建立 `SETTLEMENT_MISSING`。

O1003 / P9003 預期 11/16 結算，11/17 比對時資料完整涵蓋至 11/16，卻沒有對應紀錄，因此建立異常。
O1006 / P9006 預期 11/18 結算，因此不報錯。失敗付款 P9007 也不報錯。
O1004 雖然淨額有算式錯誤，但已存在結算紀錄，不會被當成「結算缺漏」。

這種異常沒有可引用的結算列號：證據會保存付款原始列，以及 `absence_check`，包含搜尋檔案、
付款編號、符合筆數 0、掃描筆數 4、資料完整涵蓋日期及涵蓋宣告來源。
查無紀錄的根因仍待確認，不直接認定為未入帳或結算失敗。

自我練習：備份 `manifest.json`，暫把 `settlement_complete_through` 改成 `2026-11-15` 後執行入口。
預期出現 `FAIL: 資料不足`，不會輸出完整的案件清單。因為資料不含完整的 11/16，不能斷言 P9003 缺漏。
將日期還原為 `2026-11-16`，再執行全部測試，請依 API 操作說明執行目前版本的全部測試。

## 第四條規則：結算算式不一致

**終端機執行：** 仍使用 `python -m scripts.reconcile`，不用安裝新套件。
預期共四筆異常，第四筆包含：

```json
{
  "rule": "SETTLEMENT_ARITHMETIC_MISMATCH",
  "order_id": "O1004",
  "payment_id": "P9004",
  "settlement_id": "S8004",
  "gross_amount": "2000.00",
  "fee": "40.00",
  "net_amount": "1970.00",
  "expected_net_amount": "1960.00",
  "difference": "10.00"
}
```

**閱讀說明，不需貼上或另行執行：** 打開 `app/reconciliation.py` 的
`find_settlement_arithmetic_mismatches()`。入口已自動呼叫這個函式。

1. 用 `payment_id` 關聯付款，取得訂單編號，並確認付款成功。
2. 驗證日期、唯一編號及 TWD 幣別。
3. 使用既有 `parse_money()` 將總額、手續費、淨額轉成 Decimal。
4. 計算 `expected_net_amount = gross_amount - fee`。
5. 相等就略過；不同就記錄 `difference = net_amount - expected_net_amount`。
6. 附上結算原始列與付款原始列，根因保留待確認。

差額正數表示紀錄淨額偏高，負數表示偏低，與第二條規則的「少付／多付」含義不同。
`expected_net_amount` 是依提供的總額與手續費計算，並不證明這兩個欄位正確。
目前只確認三個欄位的算式矛盾，不判定實際資金多入帳或哪個欄位應修正。

這條規則檢查每筆已提供的結算，不要求完整涵蓋日期；但入口的第三條規則仍會檢查完整性。
它不會建立缺漏案件、不推算手續費率，也不檢查結算總額是否等於付款金額（該項尚未實作）。
錯誤資料如負金額、非有限值、不同幣別或結算對應失敗付款，會讓整次比對中止。

證據位置：`settlements.csv` 第 4 列、`payments.csv` 第 5 列。
O1003 沒有結算紀錄，因此只由第三條規則報缺漏，不會產生算式異常。

**終端機執行測試：**

```bash
python -m unittest discover -s tests -v
```

四條規則原有 46 項測試；加入 API 後一般執行會顯示 73 項、略過 10 項資料庫測試。第四條規則測試涵蓋正確算式、淨額偏高／偏低、精確小數、
零手續費、零淨額、無效金額、缺少付款、失敗付款、重複結算與日期問題。

## 下一階段

四條規則、PostgreSQL 儲存與 CSV 上傳／結果查詢 API 已完成。請先閱讀 [API 操作說明](api-step-by-step.md)。
網頁工作台已完成，請依 [網頁操作說明](web-step-by-step.md) 練習上傳、篩選與證據查詢。
案件處理與 AI 摘要程式已完成；請依 [AI 操作說明](ai-summary-step-by-step.md) 了解設定與實際呼叫的下一步。
AI 摘要放在已有證據與案件結構之後。
