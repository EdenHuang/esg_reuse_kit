# 標註 A 的操作說明（給 Eden，2026-10-18）

> **備援，A 改用 HTML**（10/18 決定 12）：標註 A 現在用每批一個 HTML 檔，說明見 `Annotation_A_zh.md`。這份留著，HTML 那條路出問題時才用。R 和 D 用 Label Studio 時只在 Eden 的電腦上開，不用 `-Lan`（說明另見 `Annotation_R_zh.md`）。

標註 A 是「這張圖是不是照片」。兩位學生（以下叫 a1、a2）各自標同一批 300 題（開發 100、測試 200），題目順序各自打亂。規則見 `README.md`「品質控管」，學生看的手冊是 `A_photo_zh.md`。

**每位學生用自己的 Label Studio**（各自的資料庫和埠：a1 是 8081、a2 是 8082），所以兩人看不到彼此的專案和答案，κ（兩人一致性）才不會被灌水。這台電腦上的答案檔（下面第 0 步的確認頁、`annotations\qc\`）有檢查題的答案：學生坐你的電腦標的話，請另外開一個 Windows 帳號給學生用。

## 0. 開始前（只做一次，約 20 分鐘）
1. 打開確認頁：`reports\annotation_A\eden\A_confirm_answers.html`（雙擊用瀏覽器開，約 7 MB）。
   - 132 張：開發集公司 100 張（做例子、練習題、開發批的檢查題），測試集公司 32 張（只做測試批的檢查題，學生才不會因為認得公司而看出檢查題）。
   - 分數很高、很低的已經預填答案和理由，**錯的改掉**；接近門檻的 40 張沒有預填，請自己選。
   - 理由會原文顯示給學生看（英文），選最貼切的一項。
   - 用途可以改：例子 10–20 張、練習題至少 40 張、檢查題開發集至少 10 張、測試集至少 20 張；看不準的選「不要用」。
   - 你的選擇會自動存在瀏覽器裡。紅框是還沒填好的。
   - 看完按「下載答案」，存下 `A_confirmed_answers.json`。**這頁有檢查題的答案，不要給學生看。**
2. 把檔案路徑告訴本機 Claude，或自己跑：
   ```
   .venv\Scripts\python.exe src\annotate_export.py a-qc-import 下載的檔案路徑
   .venv\Scripts\python.exe src\annotate_export.py a-student-pages
   .venv\Scripts\python.exe src\annotate_export.py a-tasks
   ```
   做完會有：
   - 學生看的手冊、例子頁、兩組練習題：`reports\annotation_A\students\`
   - 貼進 Label Studio 的說明：`work\annotation\A\A_instructions_for_labelstudio.html`
   - 每位學生每一批的題目檔：`work\annotation\A\tasks\A_a1_batch01.json` … `A_a2_batch06.json`
   - 介面設定：`configs\labelstudio\A.xml`

## 1. 開 Label Studio（每位學生一個）
在 repo 根目錄（`D:\accounting\esg_reuse_kit`）開 PowerShell，a1、a2 各開一個視窗：
```
powershell -ExecutionPolicy Bypass -File .\start_labelstudio.ps1 -Annotator a1 -Lan -ServePractice
powershell -ExecutionPolicy Bypass -File .\start_labelstudio.ps1 -Annotator a2 -Lan
```
- `-Lan`：同一個網路（同一個 Wi-Fi 或系上網路）的學生可以用自己的筆電連進來，視窗會印出網址，像 `http://192.168.x.x:8081`。沒加就只有這台電腦能開。學校網路如果擋電腦之間互連，學生就只能坐你的電腦。
- `-ServePractice`：在 8090 埠放手冊、例子頁、練習頁（只有 `students` 資料夾），學生用 `http://192.168.x.x:8090/` 開。只要在其中一個視窗加。
- Windows 第一次問要不要讓 Python 通過防火牆時，選「私人網路」，允許。
- 關掉：在視窗按 Ctrl+C（有開練習頁的話，也關掉那個最小化的視窗）。
- 資料（帳號、題目、答案）存在 `work\labelstudio_a1\`、`work\labelstudio_a2\`，下次開還在。不進 git，不要刪。

## 2. 帳號（每個 Label Studio 各做一次）
1. 你自己先註冊：第一次加 `-AllowSignup` 開（例如 `.\start_labelstudio.ps1 -Annotator a1 -AllowSignup -Lan`），用 `http://192.168.x.x:8081` 開（**不要用 localhost**，邀請連結會用你開的網址，學生才連得到），按 Sign up，用你的 email 和自己設的密碼註冊。註冊好就按 Ctrl+C 關掉，改用不加 `-AllowSignup` 的指令重開：之後只有拿到邀請連結的人能註冊。
2. 邀請學生：左邊選單 **Organization** → **Add Members** → 跳出的 Invite members 視窗裡複製連結，傳給那位學生（a1 的連結只給 a1）。學生打開連結，用自己的 email 註冊。
3. a2 照做一次（埠 8082）。記下誰是 a1、誰是 a2。之後所有檔名和檢查都用 a1、a2，不用名字。

## 3. 建專案（每個 Label Studio 一個專案）
以 a1 為例：
1. 首頁 **Create** → Project Name 填 `A – a1`。
2. **Labeling Setup** → 左邊選 **Custom template** → 切到 **Code**，把 `configs\labelstudio\A.xml` 的內容整個貼上 → **Save**。
3. 專案 **Settings**：
   - **General** → **Task Sampling** 選 **Sequential**（照題目檔的順序，順序已經打亂過）。
   - **Annotation** → **Labeling Instructions**：用記事本打開 `work\annotation\A\A_instructions_for_labelstudio.html`，全選複製貼上（這是 HTML，貼上後顯示成排好的手冊）；勾 **Show before labeling**。
4. **Import** → 上傳 `work\annotation\A\tasks\A_a1_batch01.json`。一批 55 題：正式題 50 題，混 5 題檢查題，看起來完全一樣。
- **一次只匯入一批**。這批檢查過了（第 5 步）再匯入下一批。
- **第一次先試**：題目裡的圖是直接放在題目檔裡（base64），這種匯入方式還沒在 Label Studio 實際試過。先建一個「試用」專案匯入第 1 批，你自己標 2–3 題、匯出，跑第 5 步的檢查**加 `--dry-run`**（只檢查、不寫任何檔，你試標的答案不會被當成學生的）。看得到圖、檢查跑得動，再刪掉試用專案、建正式專案。試用時檢查會說「還沒標」，那是正常的。

## 4. 學生標註
1. 第一次標之前：
   - 看手冊（`A_guidelines.html`，在練習頁網址的第一個連結）；
   - 看例子頁；
   - 做練習題 1，20 題要答對 18 題。沒過就再看一次例子，做練習題 2。
   - 練習頁最後可以按「Save my result」，輸入 a1 或 a2（不要輸入名字），存下來交給你，放到 `annotations\qc\`。
2. 開專案 → **Label All Tasks**。
   - 按鍵：1 = Photo、2 = Not a photo、3 = Not sure（猶豫時另外勾，還是要選 1 或 2）。
   - 送出：**Submit**（Ctrl+Enter）。
   - **絕對不要按 Skip**（快捷鍵 Ctrl+Space）。跳過的題目在檢查時算錯；被跳過的題目在 Data Manager 裡顯示為 skipped，請學生回去補。
3. 每批 55 題，約 5–10 分鐘。

## 5. 每批標完：匯出、檢查
1. 開那位學生的專案 → **Export** → 格式選 **JSON** → 存到 `work\annotation\A\exports\`，**檔名加日期時間**，像 `A_a1_export_20261022_1530.json`（不要和題目檔同名）。匯出檔會包含到目前為止的所有批，沒關係。
2. 跑檢查（預設檢查匯出檔裡最新的那一批）：
   ```
   .venv\Scripts\python.exe src\annotate_export.py a-check --annotator a1 --export work\annotation\A\exports\A_a1_export_20261022_1530.json
   ```
   - 每一批印出：完成幾題、跳過幾題、沒標幾題、檢查題錯幾題、每題秒數中位數、連續同一答案最長幾題。
   - 結果存在 `results\annotation_A_batch_check_a1_*.json`；正式題的答案寫進 `annotations\dev\A.jsonl`、`annotations\test\A.jsonl`（只有題號和答案）。
   - 匯出檔不是這位學生的、裡面沒有任何標註、或比之前的少，檢查會直接停下來，不會蓋掉已經存的答案。
3. 最後列出「提醒 Eden」的話：
   - 有題目被跳過或還沒標：請學生補完再匯出一次。
   - 檢查題錯超過 2 成（跳過的算錯）：這一批退回重標。**先把這次的匯出檔留著**，再到 Data Manager 用 batch 欄篩出那一批，全選 → Delete Annotations，請學生重標。
   - 每題秒數中位數不到 1 秒，或連續 30 題以上同一個答案：先停，跟學生確認。
   - 有題目被標了兩次：只算最新的一次；重標時要先刪舊的。
4. 沒問題就匯入下一批（第 3 步第 4 點）。

順序：每位學生 6 批。前 2 批是開發集 100 題（10/22 前要標完，用來定照片門檻），後 4 批是測試集 200 題。

## 6. 全部標完
- 告訴本機 Claude。接著做：
  - 兩人答案不一樣的題目，做成一頁給你決定（看不到模型答案）；
  - 算 A 的 κ。開發集現在就能算；測試集要 11/2 凍結後才算。
- 匯出檔和 `work\labelstudio_a1\`、`work\labelstudio_a2\` 都留著。

## 你會被問到的
- **學生看得到模型分數嗎？** 看不到。題目檔裡只有圖、題號（像 `A1b2c3d4e`，用只存在這台電腦的密鑰算出來，看不出是哪家公司、哪張圖）和第幾批。`annotate_export.py` 產生題目時會檢查，多了任何欄位就停。
- **檢查題看得出來嗎？** 看不出來。檢查題和正式題用同一套程式處理圖、編題號，每 10 題左右隨機放 1 題；測試批的檢查題也來自測試集公司。
- **圖會外流嗎？** 圖只存在這台電腦（`work\labelstudio_*` 的資料庫和 `reports\annotation_A\`）。學生用 `-Lan` 時，是從你的電腦即時讀圖。練習頁的答案寫在網頁裡面，懂網頁的學生可以偷看：練習題只是教學，真正把關的是混在正式題裡的檢查題。
