"""Experiment record for Eden (10/24): one document that says what was measured, with which numbers, from which file,
and what is still open. Every number is read from results/ (through paper/numbers.tex, whose macros carry their
source file) or counted from annotations/; nothing is typed in. Writes EXPERIMENT_RECORD.md (git) and
reports/experiment_record_{stamp}.html (for reading). Since configs/frozen.yaml (10/27) the tables stand on the test
split; the record names what is still from the development split.

  python src/make_record.py
"""
import collections
import datetime as dt
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from annotate_export import CSS, md_to_html  # noqa: E402
from meta import run_stamp  # noqa: E402


def macros():
    s = (ROOT / "paper/numbers.tex").read_text(encoding="utf-8")
    out = collections.OrderedDict()
    for line in s.splitlines():
        m = re.match(r"\\newcommand\{\\(\w+)\}\{(.*)\}\s*(?:%\s*(.*))?$", line.strip())
        if not m:
            continue
        name, val, src = m.group(1), m.group(2), (m.group(3) or "").strip()
        val = val.replace("{,}", ",").replace("\\%", "%")
        out[name] = (val, src)
    return out


def count(path):
    p = ROOT / path
    return sum(1 for l in open(p, encoding="utf-8") if l.strip()) if p.exists() else 0


def load(path):
    return json.load(open(ROOT / path, encoding="utf-8"))


def latest(pattern):
    fs = sorted(glob.glob(str(ROOT / pattern)))
    return str(Path(fs[-1]).relative_to(ROOT)).replace("\\", "/") if fs else None


def plan_decisions():
    s = (ROOT / "PLAN.md").read_text(encoding="utf-8")
    sec = s[s.index("## 13."):]
    rows = []
    for line in sec.splitlines():
        if line.startswith("| 2026/"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows.append((cells[0], cells[1]))
    return rows


def red_marks():
    s = (ROOT / "TODO_EDEN.md").read_text(encoding="utf-8")
    i = s.index("## 6. 紅字對照表")
    j = s.index("\n## ", i + 5)
    return [l for l in s[i:j].splitlines() if l.startswith("| ") and not l.startswith("| 稿子裡的紅字") and not l.startswith("|---")]


def main():
    M = macros()
    v = lambda k: M[k][0] if k in M else "—"
    src = lambda k: M[k][1] if k in M else ""
    stamp = run_stamp()
    today = dt.date.today().isoformat()

    # ---- annotation status (counts only; no label distributions of the test split)
    r_test = load(latest("results/annotation_R_import_test_*.json"))
    r2 = load("results/annotation_R2_check_20261023_2322.json")
    b_agree = load("results/annotation_B_agreement_dev_20261021_1744.json")
    c_chk = load(latest("results/annotation_C_batch_check_a1_*.json"))
    logo_dev = load(latest("results/logo_refs_*_dev30.json"))["totals"]["review"]
    logo_test = load(latest("results/logo_refs_*_test120.json"))["totals"]["review"]
    c_yes = sum(1 for l in open(ROOT / "annotations/test/C.jsonl", encoding="utf-8") if l.strip() and json.loads(l)["hint_answer"] == "Yes")
    ann = [
        ("A 照片／非照片", f"開發集 {count('annotations/dev/A.jsonl')} 列（a1、a2 各 150）；測試集 {count('annotations/test/A.jsonl')} 列（各 200）", f"兩位學生；檢查題各錯 1/35；表 2 照片過濾準確率 {v('filterAcc')}%（P {v('filterP')}、R {v('filterR')}）、表 3（A 的一致性）已算", "annotations/{dev,test}/A.jsonl"),
        ("R 證據四級＋定錨（Eden）", f"開發集 {v('rItemsDev')} 題（非照片 {v('rNotPhotoDev')}，照片 {v('rPhotosDev')} 張）；測試集 {v('rItemsTest')} 題全部送出（非照片 {v('rNotPhotoTest')}，照片 {v('rPhotosTest')} 張），{v('evalPhotos')} 張有三級標籤、{v('evalFirms')} 家進評估，{v('rNoLevelTest')} 張沒有三級被排除", f"手冊 v1.5；開發集曾與 AI 校正（改 {v('devCalibEdits')}／{v('devCalibItems')} 題，只寫在 AI 聲明）；測試集秒數中位數 {r_test['median_seconds']:.1f} 秒；對 R2 的三級加權 κ {v('rTwoKappa')}（表 3）", f"annotations/dev/R.jsonl、annotations/test/R.jsonl；{latest('results/annotation_R_import_test_*.json')}"),
        ("R2 複標（一位學生）", f"測試集 {r2['items']} 張（原訂 150；10/20 抽樣時 Eden 標為照片的測試題 147 張，先抽 100）", f"檢查題 2/2 錯（兩張非照片沒勾「不是照片」；100 題全是照片，不影響 κ）；對 Eden 的三級加權 κ {v('rTwoKappa')} {v('rTwoKappaCI')}、四級 {v('rTwoFourKappa')}、定錨 {v('rTwoAnchorKappa')}，相同 {v('rTwoSame')}／{v('rTwoPhotos')} 張（表 3）", "annotations/test/R2.jsonl；results/annotation_R2_check_20261023_2322.json"),
        ("B 同一張照片（兩位學生）", f"開發集 {v('bPairs')} 對 × 2 人", f"一致 {v('bAgree')}／{v('bPairs')}，κ = {v('bKappa')}；表 7 各方法精確率用它", "annotations/dev/B.jsonl；results/annotation_B_agreement_dev_20261021_1744.json"),
        ("C 跨年重用：驗證程式第一名（Eden）", f"測試集 {count('annotations/test/C.jsonl')} 張；程式第一名被確認是同一張 {c_yes} 張", f"Eden 10/23 決定 Q6：只驗證第一名，14 題「複本沒被提示」的檢查題作廢；剩 6 題檢查題 0 錯。人工驗證的重用比例 {v('cVerifiedShare')}%（只看第一名，下界）；偵測器驗證通過的第一名 {v('cDetectorVerified')} 張裡 Eden 確認 {v('cBothVerified')} 張，精確率 {v('cVerifiedPrecision')}（§6.6）", f"annotations/test/C.jsonl；{latest('results/annotation_C_batch_check_a1_*.json')}；annotations/qc/C_checks_void.jsonl；{src('cVerifiedPrecision')}"),
        ("D 抽取漏框（a2）", f"測試集 {count('annotations/test/D.jsonl')} 頁", f"4 題檢查題作廢（檢查題設計問題）；§4.3：{v('dPages')} 頁裡漏框 {v('dMissed')} 張（{v('dMissedPages')} 頁），框不完整或合併 {v('dWrong')} 個（{v('dWrongPages')} 頁）", "annotations/test/D.jsonl；annotations/qc/D_checks_void.jsonl"),
        ("S 章節歸類抽查（Eden）", f"開發集 {v('sCheckPages')} 頁，相同 {v('sCheckSame')}", "測試集的抽查頁還沒出（待辦）", "annotations/dev/S.jsonl；results/section_check_dev_20261020_1802.json"),
        ("logo 參考圖（Eden 確認）", f"開發集 30 家：{logo_dev['firms_with_confirmed_ref']} 家有 logo、{logo_dev['confirmed_refs']} 張；測試集 120 家：{logo_test['firms_with_confirmed_ref']} 家有 logo、{logo_test['confirmed_refs']} 張", "evidence.py 的 logo 定錨只用確認過的圖", f"{latest('results/logo_refs_*_dev30.json')}；{latest('results/logo_refs_*_test120.json')}"),
    ]

    # ---- key development-split numbers for the paper
    key = [
        ("資料", [
            ("報告", f"{v('nReports')}／{v('nReportsAll')} 本（{v('nNoTextReports')} 本沒有文字層排除）；{v('nSampled')} 家＝開發 {v('nDevFirms')}＋測試 {v('nTestFirms')}；母體 {v('nPopulation')} 家、{v('nIndustries')} 個產業", src("nReports")),
            ("圖與照片", f"抽出的圖 {v('nPlacements')} → 合併重複圖層 {v('nDistinct')} → 給模型判斷 {v('nJudged')} → 照片 {v('nPhotos')}（開發 {v('nPhotosDev')}、測試 {v('nPhotosTest')}）；非照片 {v('nNonPhotos')}（{v('pNonPhoto')}%）", src("nPhotos")),
        ]),
        ("模組 1：找描述文字（表 4，測試集 R 的 {goldBoxes} 個標註框、{linkPhotos} 張）".replace("{goldBoxes}", v("goldBoxes")).replace("{linkPhotos}", v("linkPhotos")), [
            ("規則式（工具）", f"P {v('linkRulesP')}、R {v('linkRulesR')}、F1 {v('linkRulesF')}", src("linkRulesF")),
            ("學習式配對模型（換公司驗證）", f"P {v('linkLrP')}、R {v('linkLrR')}、F1 {v('linkLrF')}", src("linkLrF")),
            ("最近段落 L1／圖說 L3", f"F1 {v('linkNearestF')}／{v('linkCaptionF')}", src("linkNearestF")),
            ("外在評估：同一個工具換上下文的三級 macro-F1（切點各自在開發集凍結）", f"標註者文字 {v('ctxGoldF')}、配對模型 {v('ctxLrF')}（對規則式 {v('ctxLrD')} {v('ctxLrDCI')}）、規則式（工具）{v('ctxRulesF')}、圖說 {v('ctxCaptionF')}、標題鏈 {v('ctxHeadingF')}（對規則式 {v('ctxHeadingD')} {v('ctxHeadingDCI')}）、最近段落 {v('ctxNearestF')}、同欄 {v('ctxColumnF')}；自動連結跨距 {v('ctxSpreadAuto')}，人工文字再加 {v('ctxGoldGain')}；開發集時規則式高於配對模型，測試集反過來、區間含 0，工具維持凍結的規則式", src("ctxGoldF")),
        ]),
        ("拍到：量測工具與比較方法（表 5，測試集 {evalPhotos} 張、{evalFirms} 家，切點在開發集凍結，公司重抽 2,000 次）".replace("{evalPhotos}", v("evalPhotos")).replace("{evalFirms}", v("evalFirms")), [
            ("工具＝Qwen3-VL 校正後少樣本直接判斷", f"三級 macro-F1 {v('toolF')} {v('toolFci')}，acc {v('toolAcc')}，κw {v('toolKw')}；四級 {v('toolFourF')}；定錨 F1 {v('anchorF')}", src("toolF")),
            ("每級", f"直接 F1 {v('directF')}（P {v('directP')}、R {v('directR')}）；主題相關 R {v('topicalR')}；無關 P {v('unrelatedP')}、R {v('unrelatedR')}", src("directF")),
            ("比例偏差（四級，工具減人工）", f"證據 {v('biasEvidence')} 點、無關 {v('biasUnrelated')} 點", src("biasEvidence")),
            ("拆題（TIFA/DSG 式）", f"{v('decompF')}，差 {v('decompDiff')} {v('decompDiffCI')}（McNemar p {v('decompP')}）", src("decompF")),
            ("零樣本校正／未校正少樣本／未校正零樣本／中文 CLIP", f"{v('zeroCalF')}（差 {v('zeroCalDiff')} {v('zeroCalDiffCI')}）／{v('fewUncalF')}／{v('zeroUncalF')}／{v('clipF')}", src("zeroCalF")),
            ("舊模型 Qwen2.5-VL：拆題／零樣本校正", f"{v('decompOldVlmF')}／{v('zeroCalOldVlmF')}（acc {v('zeroCalOldVlmAcc')}、κw {v('zeroCalOldVlmKw')} 反而高：切點較嚴）", src("decompOldVlmF")),
            ("第三個家族 InternVL3-8B：少樣本校正／零樣本校正／少樣本未校正", f"{v('thirdFewCalF')}（差 {v('thirdFewCalDiff')} {v('thirdFewCalDiffCI')}，四級 {v('thirdFewCalFourF')}）／{v('thirdZeroCalF')}／{v('thirdFewUncalF')}", src("thirdFewCalF")),
            ("拆題的消融（差＝方法減拆題）", f"不做主題檢查 {v('ablNoTopicalF')}（{v('ablNoTopicalD')}）；一題問到底 {v('ablOneQF')}（{v('ablOneQD')}）；不做可拍性閘門 {v('ablNoGateF')}（{v('ablNoGateD')}）；不用 DSG {v('ablNoDsgF')}（{v('ablNoDsgD')}）", src("ablNoTopicalF")),
            ("速度（單卡）", f"E3 少樣本 {v('fewShotPhotos')} 張 {v('fewShotSeconds')} 秒含載入（{v('fewShotSecPerPhoto')} 秒／張，開發集）；整套工具（判斷＋OCR＋logo）跑完測試集 {v('measureAllPhotos')} 張 {v('measureAllSeconds')} 秒；重用 {v('reusePhotos')} 張 {v('reuseSeconds')} 秒；圖庫照 {v('stockSeconds')} 秒；page_graph {v('pageGraphSeconds')} 秒／{v('pageGraphWorkers')} 工作", src("fewShotSeconds")),
        ]),
        ("定錨（表 6）", [
            ("工具的定錨 F1", f"{v('anchorF')}；沒有 OCR {v('anchorFNoOcr')}（四級 {v('fourFNoOcr')}）；沒有 logo {v('anchorFNoLogo')}（四級 {v('fourFNoLogo')}）", src("anchorF")),
            ("疊字檢查", f"開發集 {v('overlayPhotosDev')} 張有後製疊字，工具靠疊字誤定錨 {v('overlayFalseAnchorsDev')} 張；測試集 {v('overlayPhotos')} 張被勾疊字；測試集 OCR 定錨 {v('toolOcrAnchors')} 張、標註者文字定錨 {v('annotatorTextAnchors')} 張", src("overlayPhotos")),
            ("有錨點但非直接", f"{v('shareAnchoredNotDirect')}%：包裝 {v('anchoredPackagingN')} 張、無關 {v('anchoredUnrelatedN')} 張（測試集全部 {v('analyzePhotos')} 張）", src("anchoredPackagingN")),
        ]),
        ("視覺填充（表 7）", [
            ("跨年重用（測試集）", f"{v('reuseFound')}／{v('reuseQueries')} 張（{v('reuseShare')}%）在前一年找得到；完全相同的檔 {v('reuseIdentical')}；集中：前三家 {v('reuseTopThreeShare')}%，{v('reuseFirmsZero')}／{v('reuseFirms')} 家為 0；2024 年 {v('reuseShareA')}%、2025 年 {v('reuseShareB')}%（持平，「上升」已改掉）", src("reuseFound")),
            ("合成資料 F1", f"SSCD {v('synthSscdF')}、本文 {v('synthOursF')}、DINOv2 {v('synthDinoF')}、pHash {v('synthPhashF')}（正例 {v('synthPositives')}、負例 {v('synthNegatives')}）；檢索聯集召回 {v('retrievalUnionRecall')}（前 {v('retrievalK')}）", src("synthSscdF")),
            ("跨公司圖庫照（測試集）", f"{v('stockPairs')} 對、{v('stockPhotos')} 張（{v('stockShare')}%，上界：B 開發集的跨公司對精確率只有 {v('bStockPrecOurs')}）", src("stockPairs")),
        ]),
        ("語料（表 8，測試集 {analyzePhotos} 張、{nTestFirms} 家，工具量測）".replace("{analyzePhotos}", v("analyzePhotos")).replace("{nTestFirms}", v("nTestFirms")), [
            (f"人工估計（R 測試集 {v('humanPhotos')} 張、{v('humanFirms')} 家，公司重抽區間）", f"證據 {v('humanEvidence')}% {v('humanEvidenceCI')}、示意 {v('humanIllustration')}% {v('humanIllustrationCI')}、包裝 {v('humanPackaging')}% {v('humanPackagingCI')}、無關 {v('humanUnrelated')}% {v('humanUnrelatedCI')}；有錨點 {v('humanAnchored')}%", src("humanEvidence")),
            ("工具量測的四級比例", f"證據 {v('shareEvidence')}%、示意 {v('shareIllustration')}%、包裝 {v('sharePackaging')}%、無關 {v('shareUnrelated')}%；有錨點 {v('shareAnchored')}%；對人工（標註的 R 照片上）：證據差 {v('biasEvidence')} 點、無關差 {v('biasUnrelated')} 點", src("shareEvidence")),
            ("E／S／G 的證據比例", f"E {v('shareEvidenceE')}%、S {v('shareEvidenceS')}%、G {v('shareEvidenceG')}%（包裝 {v('sharePackagingE')}／{v('sharePackagingS')}／{v('sharePackagingG')}%；G 無關 {v('shareUnrelatedG')}%）；開發集時 E 最低，測試集 G 最低，摘要／§1／§7／§9 改成「E、G 比 S 少」", src("shareEvidenceE")),
            ("描述文字與三級的重合（開發集 R）", f"有描述文字 {v('textN')} 張全部直接對應；沒有 {v('noTextN')} 張：主題相關 {v('noTextTopical')}、無關 {v('noTextUnrelated')}", src("textN")),
            ("描述文字與三級的重合（測試集 R）", f"有描述文字 {v('textNTest')} 張：直接 {v('textDirectTest')}、主題相關 {v('textTopicalTest')}；沒有 {v('noTextNTest')} 張：直接 {v('noTextDirectTest')}（近乎重合，不完全）", src("textNTest")),
        ]),
        ("資料公開", [
            ("release 與重建測試", f"{v('rebuildFirms')} 家 {v('rebuildReports')} 本在乾淨環境重抓重抽，{v('rebuildRecords')} 筆抽取紀錄一致", src("rebuildReports")),
        ]),
    ]

    md = [f"# 實驗紀錄（{today}）", "",
          "給 Eden 改論文用。每個數字都來自 `results/` 的檔案（透過 `paper/numbers.tex` 的巨集，巨集後面註明來源檔）或 `annotations/` 的列數；"
          "`configs/frozen.yaml` 10/27 19:47 寫入後，表 2–8、圖 2–4 和正文的分數都換成測試集（10/28）；還是開發集的只有 §4 對開發集標註的描述、表 7 右欄 B 的精確率（B 只標開發集）、E3 的速度量測、§5.3 的疊字檢查（測試集沒人勾疊字）。改論文時：正文在 `paper/sections/*.tex`，數字一律用巨集（`\\toolF{}` 這種），不要手打；"
          "表格由 `src/make_tables.py` 從 `paper/tables/sources.yaml` 指的結果檔產生；`python src/paper_build.py` 重建 PDF，`python src/audit_numbers.py` 檢查有沒有手打的數字。", "",
          "## 1. 現況一句話", "",
          f"- 稿子：`reports/paper_main.pdf`（Springer sn-jnl，IJDAR 格式），分數是測試集（R {v('evalPhotos')} 張、{v('evalFirms')} 家；語料 {v('analyzePhotos')} 張、{v('nTestFirms')} 家）；紅字只剩資料公開的 repo 網址（11/5），第 5 節是原本的對照表。",
          f"- 人工標註：全部交齊並算進表裡；R 測試集 {v('rItemsTest')} 題、照片 {v('rPhotosTest')} 張，{v('evalPhotos')} 張有三級標籤進評估。",
          "- 還沒做：S 測試集的章節抽查頁；§6.5 四種錯誤在測試集的計數（圖 4 旁的 TODO）；Eden 看圖 2–4 的 11 條英文註解；repo 網址與 DOI（11/5）。", "",
          "## 2. 人工標註", "", "| 任務 | 數量 | 品質／用途 | 檔案 |", "|---|---|---|---|"]
    md += [f"| {a} | {b} | {c} | `{d}` |" for a, b, c, d in ann]
    md += ["", "## 3. 論文現在用的數字（測試集；標明開發集的除外）", ""]
    for title, rows in key:
        md += [f"### {title}", "", "| 項目 | 數字 | 來源 |", "|---|---|---|"]
        md += [f"| {a} | {b} | `{c}` |" for a, b, c in rows]
        md.append("")
    md += ["## 4. 決定（PLAN §13，照時間）", ""]
    md += [f"- **{d}**：{w}" for d, w in plan_decisions()]
    md += ["", "## 5. 紅字對照（TODO_EDEN §6；10/28 已填完，只剩 repo 網址）", "", "| 稿子裡的紅字 | 來源 |", "|---|---|"]
    md += red_marks()
    md += ["", "## 6. 全部巨集（名稱＝值；來源檔）", "",
           "改論文時可直接用這些名稱；值隨 `sources.yaml` 指的結果檔自動更新（10/28 起是測試集）。", ""]
    by_src = collections.OrderedDict()
    for name, (val, s) in M.items():
        by_src.setdefault(s or "（結構性數字／設定）", []).append(f"`\\{name}`＝{val}")
    for s, items in by_src.items():
        md += [f"- `{s}`：" + "；".join(items)]
    md += ["", "## 7. 檔案地圖", "",
           "- 論文：`paper/main.tex`（摘要、作者）、`paper/sections/01_intro … 10_declarations.tex`、`paper/figs/fig1_pipeline.tex`（TikZ）、`fig2–4.tex`（圖說）、`paper/refs.bib`（32 條，全部查證過）。",
           "- 數字：`paper/numbers.tex`（自動產生，不要手改）、`paper/tables/sources.yaml`（每張表用哪個結果檔）、`paper/tables/table*.tex`（自動產生）。",
           "- 圖：`src/make_figs.py select`（挑例子，seed 2026）→ `paper/figs/examples.json`、`glosses.yaml`（英譯）；`draw` 畫成 PDF。",
           "- 凍結後的測試集流程：`src/frozen.py`（寫 frozen.yaml）→ `evidence.py`／`baselines_evidence.py`／`tune_evidence.py`（切點不再調，只套用）→ `evaluate.py --split test`（含 `--second-labels annotations/test/R2.jsonl`）→ `measure.py --split test` → `analyze.py` → `sources.yaml` 改指測試集檔 → `paper_build.py`。",
           "- 紀錄：`LOG.md`（每一步的指令、檔案、決定）、`PLAN.md`（計畫與 §13 變更）、`TODO_EDEN.md`（待辦、紅字對照、投稿前檢查）。",
           f"- 這份紀錄：`src/make_record.py` 產生（`EXPERIMENT_RECORD.md`、`reports/experiment_record_{stamp}.html`）。"]
    text = "\n".join(md) + "\n"
    (ROOT / "EXPERIMENT_RECORD.md").write_text(text, encoding="utf-8", newline="\n")
    out = ROOT / "reports" / "experiment_record.html"   # fixed name: the Markdown in git is the record, this is its rendering
    out.write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>實驗紀錄 {today}</title><style>{CSS} body{{max-width:1100px;margin:16px auto;padding:0 12px;line-height:1.5}} table{{border-collapse:collapse;font-size:14px;margin:8px 0}} td,th{{border:1px solid #ddd;padding:4px 8px;vertical-align:top;text-align:left}} th{{background:#f3f3f3}} code{{font-size:12px;background:#f6f6f6;padding:0 3px}} li{{margin:3px 0}}</style></head><body>{render_md(text)}</body></html>""", encoding="utf-8")
    print("wrote", ROOT / "EXPERIMENT_RECORD.md", "and", out, f"({len(text)} chars, {len(M)} macros)")


def inline(s):
    """Escape, then `code`, **bold** and [text](url)."""
    import html as _h
    s = _h.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def render_md(text):
    """The subset this record uses: #/##/### headings, - bullets, | tables, paragraphs (md_to_html has no tables)."""
    out, para, table, ul = [], [], [], []

    def flush():
        nonlocal para, table, ul
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>"); para = []
        if ul:
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in ul) + "</ul>"); ul = []
        if table:
            rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in table if not re.match(r"^\|?\s*-{2,}", r.strip())]
            if rows:
                head = "".join(f"<th>{inline(c)}</th>" for c in rows[0])
                body = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
                out.append(f"<table><tr>{head}</tr>{body}</table>")
            table = []

    for line in text.splitlines():
        if line.startswith("|"):
            if para or ul:
                flush()
            table.append(line); continue
        if table:
            flush()
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            flush(); out.append(f"<h{len(m.group(1))}>{inline(m.group(2))}</h{len(m.group(1))}>"); continue
        if line.startswith("- "):
            if para:
                flush()
            ul.append(line[2:]); continue
        if not line.strip():
            flush(); continue
        if ul:
            flush()
        para.append(line)
    flush()
    return "\n".join(out)


if __name__ == "__main__":
    main()
