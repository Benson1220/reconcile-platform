# 部署面試用公開展示：Render + Neon

目標：面試官使用 HTTPS 網址即可查看四筆合成異常、原始證據及規則式摘要。
公開展示為唯讀，不接受上傳、案件更新或模型呼叫。本機啟動仍保留完整功能。
網站執行於 Render，PostgreSQL 保存在 Neon；GitHub 保存原始碼。

## 費用與限制

截至 2026-11-17 查核官方文件：

- [Neon Free](https://neon.com/pricing)：提供免費額度，沒有固定試用期限，無須信用卡；有運算、儲存等用量限制。
- [Render Free](https://render.com/docs/free)：免費網站閒置 15 分鐘後休眠，重新喚醒約需一分鐘；每月有免費用量限制。
- 本專案不建立 Render Postgres，避免其免費資料庫的到期限制；也不需要 OpenAI API 金鑰。

選擇 Free 方案。面試前先開啟網站並確認資料；正式長期服務需另行評估方案。

## 第一步：建立專供展示的 Neon 資料庫

1. 開啟 https://console.neon.tech/ ，註冊或使用 GitHub 登入。
2. 選擇 Free 方案，建立新專案 `reconcile-demo`。
3. 若有版本選項，選 PostgreSQL 17。區域可選 AWS US West / Oregon，配合本作品的 Render Oregon 區域。
4. 使用新建立的空資料庫，不要接入現有公司或個人業務資料庫。
5. 在專案的 Connect 視窗複製 PostgreSQL connection string；保留 `sslmode=require` 等原有參數。
6. 複製的是以 `postgresql://` 開頭的連線字串，不是整段 `psql '...'` 指令。

這個字串含資料庫密碼。只貼入 Render 的 DATABASE_URL 欄位，不要放進聊天、GitHub 或公開截圖。

## 第二步：用 Blueprint 建立 Render 網站

1. 開啟 https://dashboard.render.com/ ，註冊或使用 GitHub 登入。
2. 點 New → Blueprint，連接 `Benson1220/reconcile-platform`。
3. 如果找不到儲存庫，檢查 Render 的 GitHub 應用程式是否已授權存取這個儲存庫。
4. 分支選 `main`；Blueprint 路徑使用根目錄的 `render.yaml`。
5. Blueprint 名稱可用 `reconcile-demo`。
6. 將 Neon 的連線字串貼入提示的 `DATABASE_URL`。
7. 確認預覽只有一個 Free Web Service，沒有建立付費資源或額外資料庫，再按 Deploy Blueprint。

`render.yaml` 已包含：

| 設定 | 值 |
|---|---|
| Runtime | Python，版本由 .python-version 指定為 3.11 系列 |
| Build Command | `pip install -r requirements-lock.txt && pip install --no-deps -e .` |
| Start Command | `python -m scripts.cloud_start` |
| Health Check Path | `/ready` |
| DEMO_READ_ONLY | `true` |
| OPENAI_SUMMARY_ENABLED | `false` |

不要上傳本機 .env，也不用執行 scripts.local_postgres。後者是 Mac 本機資料庫管理工具。

## 第三步：檢查部署紀錄

啟動程式會依序：

1. 在 Neon 執行四份 migration。
2. 檢查資料庫沒有其他來源批次。
3. 匯入 data/sample 中的合成資料，建立四筆異常。
4. 預先保存四份規則式摘要。
5. 在 Render 提供的 PORT 啟動 FastAPI。

第一次及後續啟動都使用相同初始化流程；相同資料會沿用既有批次、案件與摘要。
啟動成功會看到 `Demo ready: 4 cases and local rule summaries` 及 Uvicorn 啟動紀錄。
資料庫初始化失敗會停止啟動，不會把空白工作台當成正常服務。

## 第四步：驗收網站

以 Render 顯示的實際網址為準，不要自行猜測子網域。

1. 開啟首頁，看到「公開作品展示」提示與四筆異常。
2. 依序篩選異常，確認 O1001～O1004。
3. 打開 O1004，確認差額 10.00、S8004 原始證據，以及含 E1／E2 引用的摘要。
4. 打開 O1003，確認缺少結算的 absence_check 證據。
5. 確認上傳區隱藏，案件更新欄位停用，摘要可直接閱讀。
6. 開啟 `/ready`，看到 `{"status":"ready"}`。
7. 開啟 `/api/deployment`，看到 `{"read_only":true}`。
8. 從另一個裝置或無痕視窗開啟，確認不需登入。

成功後將網址加入 GitHub 儲存庫右側 About → Website，以及履歷的作品連結。

## 常見問題

- 首次開啟較慢：Render Free 可能正在喚醒；稍候再試。
- `Cloud startup failed`：檢查 Render 環境變數中的 DATABASE_URL 與 Neon 狀態。啟動程式不會在 log 印出密碼。
- 資料庫含其他來源：建立專供作品的新空資料庫，不要刪除或清空原資料庫。
- `/health` 正常但 `/ready` 失敗：前者只檢查程序，後者會確認資料庫連線與 migration。
- 寫入 API 回傳 403：這是公開展示的預期行為；本機版本使用 `DEMO_READ_ONLY=false`。
- 雲端服務已存在：不要重複建立 Blueprint；修改既有服務或檢查其部署紀錄。

## 已驗證與待驗證

本機已測試初始化冪等性、四份摘要、唯讀寫入阻擋及資料庫健康檢查。
實際雲端建置、Neon 連線與公開網址需在帳號建立及設定後驗收，不能以本機測試代替。

官方參考：[Render FastAPI](https://render.com/docs/deploy-fastapi)、[Blueprint](https://render.com/docs/infrastructure-as-code)、[Neon 連線](https://neon.com/docs/connect/connect-from-any-app)。
