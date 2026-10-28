# 標註 R 的操作說明（給 Eden，2026-10-18；10/19 改：題目由本機 Claude 匯入）

標註 R：一張照片配上它的那一頁，你點出描述這張照片的文字，再判斷三級和定錨。規則在手冊 `R_evidence_zh.md`（v1.5），抽題和畫面規格在 `README.md`。

- 用 Label Studio，**只在你自己的電腦上開，不加 `-Lan`**（10/18 決定 12）。
- 題目檔裡只有整頁圖、題號（像 `R3804f50c`，看不出是哪家公司）、第幾批，以及預先畫好的框。框來自 PDF 本身（`page_graph.py` 切出來的文字框和照片位置），不是模型判斷。
- 現在只有**草稿 20 題**，讓你先看畫面。正式題要等兩件事：標註 A 開發集定好照片門檻，以及 page_graph 跑完 443 本（等你說 OK）。

## 0. 先看預覽（不用開 Label Studio，2 分鐘）
雙擊 `D:\accounting\esg_reuse_kit\reports\annotation_R\R_draft_preview.html`（整個路徑貼到檔案總管的網址列也可以）。20 頁，藍框是照片，灰框是文字框，跟 Label Studio 裡看到的一樣。灰框切得不好（漏框、黏在一起、切太碎）的頁，記下題號告訴本機 Claude。

## 1. 開 Label Studio、建帳號（只做一次，約 5 分鐘）
開 PowerShell，先切到 `D:\accounting\esg_reuse_kit`（輸入 `cd D:\accounting\esg_reuse_kit` 按 Enter），再執行：
```
powershell -ExecutionPolicy Bypass -File .\start_labelstudio.ps1 -Annotator eden -AllowSignup
```
1. 用瀏覽器開 `http://localhost:8080`，按 **Sign up**，用你的 email 和自己設的密碼註冊（密碼你自己記，本機 Claude 不碰帳號）。
2. 註冊好就在 PowerShell 視窗按 Ctrl+C 關掉，之後都用不加 `-AllowSignup` 的指令開：
   ```
   powershell -ExecutionPolicy Bypass -File .\start_labelstudio.ps1 -Annotator eden
   ```
- 資料（帳號、題目、答案）存在 `work\labelstudio_eden\`，下次開還在。不進 git，不要刪。

## 2. 建專案、匯入題目：本機 Claude 做（10/19 起）
- 你註冊好帳號、開著 Label Studio，跟本機 Claude 說一聲就好。它用 `src\ls_load.py` 直接把畫面設定和題目放進你的 Label Studio（先備份資料庫，不碰帳號和密碼）。
- 10/19 已經做好：你建的「New Project #1」改名「**R – 草稿**」，裡面是 20 題，每題都有藍框、灰框。瀏覽器開 `http://localhost:8080`，登入，點「R – 草稿」→ **Label All Tasks** 就開始標。
- 看不到圖或灰框，告訴本機 Claude。
- 正式題（每批 50 題）也照這樣由本機 Claude 放進新專案。
- 自己用網頁匯入的做法（備用）：首頁 **Create** → **Labeling Setup** → **Custom template** → **Code** 貼上 `D:\accounting\esg_reuse_kit\configs\labelstudio\R.xml` → **Save**；**Import** 上傳題目檔；**Settings** → **Annotation** 打開 **Use predictions to prelabel tasks**、選 `page_graph`。

## 3. 怎麼標（畫面）
- **藍框**是這一題的照片，固定不能動。
- **灰框**是頁面上的文字框。點一下選取，再按 **d**（或點上方的「描述文字」），框會變成橘色＝描述文字。可以選好幾個。點錯了：選取它，再點「文字框」改回來，或按 Ctrl+Z。
- 不小心拖到灰框：按 Ctrl+Z。**不用自己畫新框**：描述的文字沒有框到，勾「描述文字的框有問題」就好（畫了也會記下來，但要同時勾這一項）。
- 下面的勾選和單選照手冊：「不是照片」、「這頁沒有描述文字」、三級（按 1、2、3）、定錨三項、「描述文字的框有問題」「照片框有問題」「不確定」、備註。
- 送出：**Submit**（Ctrl+Enter）。**不要按 Skip**：不是照片的題目也是勾「不是照片」再送出。
- 改已經送出的答案：開那一題（網址 `http://localhost:8080/projects/專案編號/data?task=題目編號`，或在題目清單點那一列），直接改勾選，按 **Update**。答案記的是最後一次存的時間，程式用它判斷是照哪一版手冊標的。

## 4. 標完草稿：匯出、檢查
1. 專案 → **Export** → 格式選 **JSON** → 存到 `work\annotation\R\exports\`，檔名加日期時間，像 `R_draft_export_20261020_2130.json`。
2. 跑：
   ```
   .venv\Scripts\python.exe src\annotate_export.py r-import --draft --export work\annotation\R\exports\R_draft_export_20261020_2130.json
   ```
   會列出每題的問題（沒選三級、勾了「這頁沒有描述文字」卻選直接對應、畫了新框卻沒勾框有問題），以及三級、定錨的分布和每題秒數中位數。**草稿不會寫進 `annotations\`**。
3. 告訴本機 Claude 畫面哪裡不好用（框太多、字太小、快捷鍵、選項文字），改好再抽正式題。手冊說「前 20 題標完先停一下」，就是這一次：拿不準的規則補進手冊。

## 5. 正式題（之後）
開發集 100 對，照抽出的順序多抽 40 題備用；測試集 300 對同樣做法。
1. 本機 Claude 跑（要先有照片門檻和 443 本的 page_graph）：
   ```
   .venv\Scripts\python.exe src\annotate_export.py r-sample --split dev --n 100
   ```
   會產生 `work\annotation\R\R_dev_batch01_tasks.json`、`batch02`、`batch03`（每批 50 題，第 3 批是備用），題目表在 `annotations\dev\R_items.jsonl`。
2. 建新專案 `R – 開發集`（照第 2 步，用同一個 `R.xml`），**一次只匯入一批**。
3. 每批標完匯出，跑（不加 `--draft`）：
   ```
   .venv\Scripts\python.exe src\annotate_export.py r-import --split dev --export work\annotation\R\exports\R_dev_export_日期時間.json
   ```
   答案寫進 `annotations\dev\R.jsonl`（只有題號、框的編號和標籤）。它也會說「已標幾張照片、還差幾張、接著標第幾批」：勾「不是照片」的題目不算，由備用題往下補，直到湊滿 100 張照片。
4. 測試集：`--split test --n 300`，另建專案 `R – 測試集`。

## 你會被問到的
- **灰框是模型判斷嗎？** 不是。灰框是 PDF 裡每段文字的位置，藍框是照片在頁面上的位置，都是從 PDF 直接讀出來的；導覽列、頁首頁尾不畫。哪個框在描述照片，是你要標的。
- **答案會跟模型比嗎？** 會，但題目裡看不到任何模型結果。你點的框是圖文配對的標準答案；「描述文字的框有問題」用來算程式切文字框切錯的比例。
- **R2（學生複標）呢？** 10/27 開始，做法之後補在這份說明。

## 重看開發集「主題相關」8 張（手冊 v1.4，10/19）
「直接對應」看照片拍的是不是文字講的**那一類事物**，是不是那一場交給定錨（手冊第 2 步的判斷順序）。開發集標成「主題相關」的 8 張，照 v1.4 再看一次；要改就改，按 **Update**；不改就不用動。

| # | 題目（點開） | 頁 | 文字在講 |
|---|---|---|---|
| 1 | http://localhost:8080/projects/2/data?task=26 | 2102_2023 第 67 頁 | 社區參與：環保活動、尾牙聚餐、中秋烤肉等 |
| 2 | http://localhost:8080/projects/2/data?task=29 | 3015_2024 第 115 頁 | 幾場健康講座、「辦公室運動達人」（你勾了「這頁沒有描述文字」；如果那串講座就是在講這張照片，取消這個勾、點那個文字框） |
| 3 | http://localhost:8080/projects/2/data?task=40 | 2610_2025 第 43 頁 | 「小小空服員體驗營」 |
| 4 | http://localhost:8080/projects/2/data?task=54 | 3015_2023 第 121 頁 | 「雲科電機實驗室交流」「雲科儲能系統書報講堂」 |
| 5 | http://localhost:8080/projects/2/data?task=56 | 1101_2023 第 105 頁 | 能源展展示儲能櫃、同仁在現場與客戶溝通 |
| 6 | http://localhost:8080/projects/2/data?task=60 | 2610_2025 第 47 頁 | 圖說「待命室、更衣室」：客艙組員待命區整修工程 |
| 7 | http://localhost:8080/projects/2/data?task=68 | 3015_2023 第 121 頁 | 同第 4 題，另一張照片 |
| 8 | http://localhost:8080/projects/2/data?task=99 | 2645_2025 第 92 頁 | 員工人數統計（你勾了「這頁沒有描述文字」） |

改完跟本機 Claude 說「改好了」：它會唯讀讀出答案、重新匯入開發集，再重跑模組 2 的門檻和比較。測試集還沒有標任何一題（10/19 22:30 查過，只看了題數），所以不用回頭改。
