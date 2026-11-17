# PostgreSQL：儲存來源資料與比對結果

## 目前已完成

這台 Mac 已安裝 PostgreSQL 17，專案使用獨立資料庫叢集。
資料庫名稱 `reconcile`，應用程式帳號 `reconcile_app`，本機位址 `127.0.0.1:55432`。
應用程式帳號不是資料庫超級使用者。
叢集的資料檔在 `.local/postgres`，連線設定與隨機密碼在 `.env`。
`.local` 與 `.env` 已加入 `.gitignore`。
目前 PostgreSQL 保持執行中；未設定開機自動啟動。

我們已實際執行建表、匯入、重複匯入與 PostgreSQL 整合測試。
因此你再次匯入時應看到 `reused: true`，這是成功沿用既有資料，不是錯誤。

## 第 1 步：終端機執行，啟用環境

```bash
cd /Users/benson/Documents/ChatGPT/future/reconcile-platform
source .venv/bin/activate
```

不需要另外進入 Python 的 `>>>` 互動介面。

## 第 2 步：終端機執行，確認 PostgreSQL 已啟動

```bash
python -m scripts.local_postgres start
```

預期顯示 `OK PostgreSQL 已啟動，僅監聽 127.0.0.1:55432`。
已在執行時，這個指令會直接沿用服務；重開機後則用它重新啟動。
不需要執行 `brew services start`，因為本專案使用自己的叢集目錄。

## 第 3 步：終端機執行，確認資料表

```bash
python -m scripts.db init
```

預期顯示 `OK 資料庫 schema 已就緒（migration 1）`。
可重複執行，不會清除資料。migration 是資料庫結構的版本紀錄；
第一版 SQL 是 `sql/001_initial.sql`。已套用的 SQL 若被修改，會拒絕繼續執行，避免版本與實際結構不一致。
目前只實作 migration 1；日後新增 migration 時也需要擴充執行器。

## 第 4 步：終端機執行，匯入與儲存結果

```bash
python -m scripts.db import-sample
```

這個指令讀取一次三份 CSV 快照，在記憶體執行四條規則，最後將來源與結果一起存進資料庫。
不會把基準答案 `expected_cases` 當作結果。

第一次成功新增的輸出：

```json
{"batch_id": 1, "run_id": 1, "reused": false, "case_count": 4}
```

這台電腦已完成第一次匯入，因此你現在執行應看到 `reused: true`。
`batch_id` 是來源批次編號，`run_id` 是比對批次編號。
這些 ID 是資料庫生成的；未來有其他批次時，數字可能不同或跳號，這是正常現象。

重跑相同 CSV、相同設定與相同規則版本時，沿用同一個 run，案件維持四筆。
CSV 內容改變時建立新來源批次；設定改變時同一來源可建立新的比對批次。
修改規則程式時，需同步更新 `app/pipeline.py` 的 `RULES_VERSION`，保存不同版本的結果。

## 第 5 步：終端機執行，從資料庫查回結果

```bash
python -m scripts.db show
```

此指令只查詢資料庫，不重新讀取 CSV 或執行規則。
未修改模擬資料的初始狀態下，預期有：

| 資料表 | 筆數 | 用途 |
|---|---:|---|
| import_batches | 1 | 三份來源檔案組成的快照批次 |
| source_files | 3 | 原始 CSV 位元組、檔名與 SHA-256 |
| source_rows | 19 | 原始列內容、來源與列號 |
| orders | 8 | 可直接用 SQL 查詢的訂單資料 |
| payments | 7 | 可直接用 SQL 查詢的付款資料 |
| settlements | 4 | 可直接用 SQL 查詢的結算資料 |
| reconciliation_runs | 1 | 比對設定、基準時間與規則版本 |
| cases | 4 | 四筆異常及完整結果 JSON |
| case_evidence | 8 | 原始列證據連結或查無紀錄的搜尋條件 |

`cases` 清單應有 O1001～O1004。差額為 `null` 的案件沒有金額差額欄位，並非資料遺失。
異常不會因為存入資料庫而被修正，S8004 的淨額仍保留為來源提供的 1970.00。
資料庫刻意不限制「總額 − 手續費 = 淨額」，否則會拒絕保存我們要追查的異常。

## 第 6 步：終端機執行，驗證儲存功能

```bash
RUN_DATABASE_TESTS=1 python -m unittest discover -s tests -v
```

目前加入 API 後預期 `Ran 73 tests` 與 `OK`；新增 API 測試詳見 [API 操作說明](api-step-by-step.md)。
原有規則有 46 項，新增 6 項 PostgreSQL 整合測試：

- 原始 CSV、精確金額、比對結果與證據可完整查回。
- 重複匯入不新增重複案件。
- 匯入中途失敗時，整次交易回復，沒有半套資料。
- 設定不同時新增比對批次，沿用來源批次。
- 同時匯入相同資料，不重複建立批次或案件。
- migration 可重複執行。

測試會在隨機名稱的獨立 schema 中執行，完成後僅清除自己建立的 schema，不改動 public 的專案資料。
不設定 `RUN_DATABASE_TESTS=1` 時，通過 63 項、略過 10 項整合測試，適用未啟動資料庫的情況。

## 閱讀說明：不需要貼上或單獨執行

閱讀順序：

1. `scripts/db.py`：分派建表、匯入與查詢指令。
2. `app/pipeline.py`：一次讀取原始 CSV、呼叫四條規則，供兩種入口共用。
3. `sql/001_initial.sql`：資料表、型別、唯一鍵及外鍵。
4. `app/database.py`：用 Psycopg 3 將資料與結果存進 PostgreSQL。

`with psycopg.connect(...) as conn` 的交易在正常結束時提交，有例外時回復。
所有動態資料值使用參數化 SQL；金額欄位使用 `NUMERIC(18,2)`。
Psycopg 交易說明：https://www.psycopg.org/psycopg3/docs/basic/transactions.html

目前 CLI 仍先在記憶體比對，再用單一交易持久化；尚未實作「從資料庫重載來源後重跑規則」；上傳與查詢 API 已於下一階段完成，網頁操作介面尚未實作。
案件是每次比對的快照，目前沒有跨批次案件合併、簽派與結案狀態。

## 使用完畢後：終端機執行

```bash
python -m scripts.local_postgres stop
```

停止服務不會刪除資料。下次執行 `python -m scripts.local_postgres start` 即可繼續查詢。
不要刪除 `.local/postgres`，那裡是實際資料庫檔案。

## 在另一台 Mac 重建環境

需先備妥 Python 3.11 與 Homebrew，複製專案程式碼但不複製 `.venv`、`.env` 或 `.local`。
使用專案 README 的方式建立虛擬環境並安裝鎖定依賴後：

```bash
brew install postgresql@17
python -m scripts.local_postgres setup
python -m scripts.db init
python -m scripts.db import-sample
python -m scripts.db show
```

`setup` 會生成這台電腦專用的隨機密碼，不必自行填寫 `.env.example`。
資料庫連線只使用本機 55432 連接埠；若連接埠被占用，會報錯而不替換其他服務。
