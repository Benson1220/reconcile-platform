# CSV 上傳與結果查詢 API

目前已完成一次上傳三份 CSV、執行四條規則、儲存資料庫，以及查詢批次與案件證據。
這是本機學習版，尚未加入登入與權限控管；啟動指令只綁定 127.0.0.1。

## 第 1 步：終端機執行

```bash
cd /Users/benson/Documents/ChatGPT/future/reconcile-platform
source .venv/bin/activate
python -m scripts.local_postgres start
python -m scripts.db init
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

最後一行啟動後端後，請保持終端機開啟。
如果 8000 已有舊版專案服務，先在原本啟動它的終端機按 Control+C，再重新啟動。
目前所需套件已安裝，這台電腦不必重裝。

## 第 2 步：瀏覽器操作，打開互動文件

開啟 http://127.0.0.1:8000/docs 。這是 Swagger UI，可直接操作 API，不必手寫 HTTP 請求。

| API | 用途 |
|---|---|
| POST /api/reconciliations | 上傳三份 CSV，比對並儲存 |
| GET /api/runs | 列出比對批次 |
| GET /api/runs/{run_id} | 查看批次設定、規則版本與異常筆數 |
| GET /api/runs/{run_id}/cases | 列出批次異常，可分頁或按規則篩選 |
| GET /api/cases/{case_id} | 查看單筆異常的完整結果與證據 |

POST 表示提交資料，GET 表示查詢。查詢 API 讀取已儲存的結果，不重新執行規則。
檔案上傳使用 multipart/form-data；FastAPI 文件：https://fastapi.tiangolo.com/tutorial/request-files/

## 第 3 步：瀏覽器操作，上傳三份檔案

1. 展開 `POST /api/reconciliations`。
2. 點選 **Try it out**。
3. 對三個檔案欄位分別點選 **Choose File／選擇檔案**。

| 欄位 | 選擇檔案 |
|---|---|
| orders | orders.csv |
| payments | payments.csv |
| settlements | settlements.csv |

三份檔案皆在 `/Users/benson/Documents/ChatGPT/future/reconcile-platform/data/sample`。
在 Mac 檔案選擇視窗按 Command+Shift+G，貼上這個資料夾路徑，即可前往。

下面的設定沿用畫面預設值：

| 欄位 | 範例資料使用的值 |
|---|---|
| as_of | 2026-11-17T09:00:00+08:00 |
| settlement_complete_through | 2026-11-16 |
| order_sync_grace_minutes | 15 |

日期預設值專供這組範例，不是系統自動推算的現在時間。
未來上傳其他資料時需明確設定基準與涵蓋日期；API 不會自行證明來源完整性。

4. 點選 **Execute**。
5. 查看下方 **Server response → Response body**，不是文件中的 Example Value。

目前原資料已由 CLI 匯入，所以預期 HTTP **200**：

```json
{
  "batch_id": 1,
  "run_id": 1,
  "reused": true,
  "case_count": 4,
  "run_url": "/api/runs/1",
  "cases_url": "/api/runs/1/cases"
}
```

新來源或新設定第一次成功建立時回傳 HTTP **201**；重複相同資料則 HTTP **200**。
ID 以實際回傳為準，未來可能不是 1；重複上傳相同資料不會新增四筆案件。
原始上傳檔名不會用來建立伺服器路徑，資料按表單欄位對應到固定來源名稱。
不會覆寫 `data/sample`。原始位元組保存在資料庫 `source_files`。

## 第 4 步：瀏覽器操作，查詢批次

展開 `GET /api/runs` → Try it out → Execute。
`total` 目前應為 1，`items` 中有一筆 `case_count: 4` 的批次。

再展開 `GET /api/runs/{run_id}`，輸入剛取得的 `run_id` 後 Execute。
這裡能查看當時的比對設定與 `rules_version`。

## 第 5 步：瀏覽器操作，列出四筆異常

展開 `GET /api/runs/{run_id}/cases` → Try it out。
輸入剛取得的 `run_id`；`rule` 先不選，`limit`、`offset` 使用預設值，再 Execute。
回應應有 `total: 4`，`items` 中列出 O1001～O1004，每筆會有數字 `case_id`。

若要只看第四條規則，在 `rule` 選 `SETTLEMENT_ARITHMETIC_MISMATCH`，再 Execute。
結果應有 `total: 1`、`order_id: O1004`、`difference: "10.00"`。

分頁方式：`limit=1`、`offset=1` 代表略過第一筆，取下一筆。
`total` 是符合條件的總筆數，不是這一頁的筆數。

## 第 6 步：瀏覽器操作，查看證據

記下 O1004 那筆的 **case_id**，再展開 `GET /api/cases/{case_id}`。
點 Try it out，輸入這個數字後 Execute。

注意這裡使用資料庫數字案件編號，不是 `O1004`、`P9004` 或 `S8004`。
回應應包含 `settlement_id: S8004`、`expected_net_amount: "1960.00"`、
`difference: "10.00"`，以及 `evidence` 原始資料。
證據中會有 `settlements.csv` 第 4 列，以及 `payments.csv` 第 5 列。

新上傳批次的缺漏證據涵蓋來源標示 `upload_form:settlement_complete_through`；
沿用過去 CLI 建立的批次時，會保留首次建立時的 `manifest.json` 來源標記。

## 錯誤回應

| HTTP 狀態 | 意義 |
|---|---|
| 200 | 查詢成功，或沿用既有上傳結果 |
| 201 | 新比對批次建立成功 |
| 404 | 找不到指定批次或案件 |
| 409 | 資料關聯或唯一性衝突，交易已回復 |
| 413 | 檔案超過每份 2 MiB，或資料超過每份 10,000 筆 |
| 415 | 上傳檔案副檔名不是 .csv |
| 422 | 缺少檔案、欄位／型別錯誤、無效金額、資料涵蓋不足等 |
| 503 | 資料庫設定、連線或建表尚未就緒 |

CSV 須為 UTF-8（可含 BOM）。不同幣別、超過兩位小數、非有限值或超出金額範圍會被拒絕。
三份資料先一起驗證，通過後才寫入；資料庫交易中途失敗時全部回復。
上傳 API 的大小限制是在 multipart 解析後檢查每份檔案，不是整個 HTTP 請求的入口限制。

## 終端機執行測試

另開終端機並進入專案、啟用環境後：

```bash
RUN_DATABASE_TESTS=1 python -m unittest discover -s tests -v
```

目前共 73 項全部通過，其中 10 項需要 PostgreSQL。
不設定 `RUN_DATABASE_TESTS=1` 時，通過 63 項、略過 10 項，不需資料庫連線。
整合測試使用隨機名稱 schema，不修改現有 public 的來源與四筆案件。

## 閱讀說明：不需貼上或單獨執行

- `app/api.py`：上傳欄位、參數驗證、回應格式與五個 API。
- `app/pipeline.py`：CLI 與 API 共用 CSV 驗證及四條規則。
- `app/database.py`：批次與結果保存，以及參數化 SQL 查詢。
- `app/main.py`：註冊 API 與資料庫錯誤回應。

目前上傳採同步處理，小批量完成後才回傳；尚未實作背景任務或進度查詢。
資料庫 schema 沒有變更，不需建立第二版 migration。

操作結束後在啟動 Uvicorn 的終端機按 Control+C，即可停止 API；資料仍保留。
