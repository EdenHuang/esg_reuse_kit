"""results/ -> paper/tables/*.tex (PLAN §3; CLAUDE.md rule 1: every number in the paper comes from a result file).

paper/tables/sources.yaml names, for each table, the result files it is built from (explicit paths, so a rerun with a
new result is a visible change to that file). Each .tex starts with a comment listing its sources and their sha256.
A table whose sources do not exist yet (the test split waits for configs/frozen.yaml) is written as a marked
placeholder, so the paper compiles. Tables built from development results say so in the caption ("development
split"); those are drafting aids and are replaced by the test-split files before submission.

  python src/make_tables.py            # all tables
  python src/make_tables.py --only 1 5
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "paper" / "tables"
L3 = ("direct", "topical", "unrelated")


def load(p):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


def sha(p):
    return hashlib.sha256((ROOT / p).read_bytes()).hexdigest()[:16]


def num(x, d=3):
    return "--" if x is None else f"{x:.{d}f}"


def pct(x, d=1):
    return "--" if x is None else f"{100 * x:.{d}f}"


def intn(x):
    return "--" if x is None else f"{x:,}".replace(",", "{,}")


def ci(c):
    return f"\\,{{\\scriptsize[{c[0]:.2f}, {c[1]:.2f}]}}" if c else ""


def esc(s):
    return str(s).replace("_", "\\_").replace("%", "\\%").replace("&", "\\&")


def table(label, caption, colspec, header, rows, note="", wide=False, colsep=None):
    """IJDAR two-column layout (10/20): wide=True spans both columns (table*); all tables in \\footnotesize;
    colsep (e.g. "3pt") narrows the column padding of a single-column table."""
    body = "\n".join(" & ".join(r) + r" \\" for r in rows)
    env = "table*" if wide else "table"
    sep = f"\\setlength{{\\tabcolsep}}{{{colsep}}}\n" if colsep else ""
    return (f"\\begin{{{env}}}[t]\n\\centering\n\\caption{{{caption}}}\\label{{{label}}}\n\\footnotesize\n{sep}"
            f"\\begin{{tabular}}{{{colspec}}}\n\\toprule\n{header} \\\\\n\\midrule\n{body}\n\\bottomrule\n\\end{{tabular}}\n"
            + (f"\\par\\smallskip{{\\footnotesize {note}}}\n" if note else "") + f"\\end{{{env}}}\n")


def split_note(src):
    return "development split" if any("_dev_" in s or "dev" in Path(s).stem.split("_") for s in src) else "test split"


# ------------------------------------------------------------------ tables
def t1(src):
    import csv
    ex, pf = load(src["extract"]), load(src["photo_filter"])
    sub, tot, ps = ex["subtotals"]["without_no_text_layer"], pf["totals"], pf["per_split"]
    firms = {}
    with open(ROOT / "data" / "catalog.csv", encoding="utf-8-sig", newline="") as f:
        for d in csv.DictReader(f):
            firms[d["firm_id"].strip()] = d["split"].strip()
    nd = sum(v == "dev" for v in firms.values())
    assert tot["crops"] - tot["image_only_report"] - tot["cover_page"] - tot["layout_repeat"] - tot["duplicate"] == tot["candidates"]
    assert sub["pictures"] + ex["subtotals"]["no_text_layer"]["pictures"] == tot["crops"]   # the filter starts from the merged images
    rows = [["Firms (development / test)", f"{len(firms)} ({nd} / {len(firms) - nd})"],
            ["Reports downloaded / with a text layer", f"{intn(ex['totals']['reports'])} / {intn(sub['reports'])}"],
            ["\\multicolumn{2}{l}{\\emph{Extraction, the 443 reports with a text layer}}"],
            ["\\quad pages", intn(sub["pages"])],
            ["\\quad image placements", intn(sub["placements"])],
            ["\\quad images after merging repeated layers", intn(sub["pictures"])],
            ["\\quad distinct images", intn(sub["distinct"])],
            ["\\multicolumn{2}{l}{\\emph{Photo filter, all 450 reports}}"],   # 10/22 review: a waterfall that adds up
            ["\\quad images after merging repeated layers", intn(tot["crops"])],
            ["\\quad $-$ in reports without a text layer", intn(tot["image_only_report"])],
            ["\\quad $-$ on cover pages", intn(tot["cover_page"])],
            ["\\quad $-$ layout elements (3+ pages)", intn(tot["layout_repeat"])],
            ["\\quad $-$ duplicates within a report", intn(tot["duplicate"])],
            ["\\quad $=$ given to the photo filter", intn(tot["candidates"])],
            ["\\qquad photos", intn(tot["photos"])],   # 10/24: split in two rows, the single-column table was 15 pt too wide
            ["\\qquad\\quad development / test", f"{intn(ps['dev']['photos'])} / {intn(ps['test']['photos'])}"],
            ["\\qquad non-photos", intn(tot["nonphoto"])]]
    return table("tab:data", "Dataset statistics (reporting years 2023--2025).", "lr", "& Count", rows, colsep="3pt",
                 note="Distinct images merge images repeated within a report. The filter's exclusions subtract in order.")


def t4(src):
    cl, ev = load(src["context_link"]), load(src["extrinsic"])
    sc = cl["vs_labels"]["scores"]
    names = [("L1_nearest", "L1 nearest paragraph", "L1_nearest"), ("L2_column", "L2 same column", "L2_column"),
             ("L3_caption", "L3 caption only", "L3_caption"), ("L4_heading", "L4 heading chain", "L4_heading"),
             ("L5_rules", "Rule-based context (the tool)", "rules"), ("M_lr", "Learned pairing model", "M_lr")]
    rows = []
    for key, label, ctx in names:
        s = sc.get(key, {})
        m = ev["methods"].get(f"tool@{ctx}") or {}
        rows.append([label, num(s.get("precision")), num(s.get("recall")), num(s.get("f1")),
                     num(m.get("macro_f1")) + ci((m.get("ci") or {}).get("macro_f1")), num(m.get("macro_f1_4"))])
    g = ev["methods"].get("tool@gold") or {}
    rows.append(["Annotator's describing text (not comparable)", "--", "--", "--", num(g.get("macro_f1")) + ci((g.get("ci") or {}).get("macro_f1")),
                 num(g.get("macro_f1_4"))])
    return table("tab:context", f"Context linking: box-level precision, recall and F1 (intrinsic), and the evidence "
                 f"measurement with each context (extrinsic, macro-F1; {split_note(src.values())}).", "lrrrrr",
                 "Context & P & R & F1 & 3-level & 4-level", rows,
                 note="Brackets: 95\\% interval from 2,000 firm-level bootstrap resamples. Extrinsic: the measurement tool (calibrated "
                      "few-shot direct judgement + anchoring). The last row is not a linking method: the annotator's text is empty "
                      "for almost exactly the photos judged non-direct (Section~\\ref{sec:exp-link}), so the context carries part of "
                      "the label.", wide=True)


def method_rows(ev, spec, delta=True):
    rows = []
    for name, label in spec:
        m = ev["methods"].get(name)
        if not m:
            continue
        vs = m.get("vs_ref")
        rows.append([label, num(m["macro_f1"]) + ci(m["ci"].get("macro_f1")), num(m["accuracy"]), num(m["kappa_w"]),
                     num(m.get("macro_f1_4")), num(m.get("anchor_f1")),
                     (f"{vs['macro_f1_diff']:+.3f}" + ci(vs["diff_ci"])) if (vs and delta) else "--"])
    return rows


def t5(src):
    rows = []
    for key, spec in (("tool", [("tool", "Direct, few-shot, calibrated (ours; Qwen3-VL)"),
                                ("E2_cal", "Direct, zero-shot, calibrated (Qwen3-VL)"),
                                ("E3_uncal", "Direct, few-shot, uncalibrated (Qwen3-VL)"),
                                ("module2", "Decomposed questions (Qwen3-VL)"),
                                ("E1", "Chinese CLIP similarity, two cuts"),
                                ("q25_module2", "Decomposed questions (Qwen2.5-VL)"),   # 10/21: same run, so paired to the tool
                                ("q25_E2_cal", "Direct, zero-shot, calibrated (Qwen2.5-VL)")]),
                      ("third", [("ivl:E3_cal", "Direct, few-shot, calibrated (InternVL3)"),   # 10/22 NEXT C12
                                 ("ivl:E2_cal", "Direct, zero-shot, calibrated (InternVL3)"),
                                 ("ivl:E3", "Direct, few-shot, uncalibrated (InternVL3)")]),
                      ("prev", [("q25:module2", "Decomposed questions (Qwen2.5-VL)"),
                                ("q25:E2_cal_v1", "Direct, zero-shot, calibrated (Qwen2.5-VL)")])):
        if key in src:   # 'prev' (an earlier file with another reference method) is only used when 'tool' lacks the q25 rows
            rows += method_rows(load(src[key]), spec, delta=(key in ("tool", "third")))
    return table("tab:methods", f"Depiction methods, all with the same anchoring ({split_note(src.values())}; "
                 "three-level macro-F1 against annotation R).", "lrrrrrr",
                 "Method & Macro-F1 & Acc. & $\\kappa_w$ & 4-level & Anchor F1 & $\\Delta$ vs ours", rows,
                 note="Direct: the direct judgement, and decomposed questions: the TIFA/DSG-style approach, both of Section~\\ref{sec:method-evidence}. Brackets: 95\\% firm-level bootstrap "
                      "intervals; $\\Delta$: paired difference in macro-F1.", wide=True, colsep="3pt")   # 10/24: was 60 pt too wide


def t5b(src):
    """Per-level precision, recall and F1 of the measurement tool (NEXT 10/22, A4)."""
    ev = load(src["tool"])
    pl = ev["methods"]["tool"]["per_level"]
    names = {"direct": "Direct", "topical": "Topical", "unrelated": "Unrelated"}
    rows = [[names[k], num(pl[k]["precision"]), num(pl[k]["recall"]), num(pl[k]["f1"])] for k in ("direct", "topical", "unrelated")]
    cm = ev["methods"]["tool"]["confusion_label_by_model"]
    rows.append(["\\multicolumn{4}{l}{\\emph{Confusion (annotation $\\rightarrow$ tool), counts}}"])
    for g in ("direct", "topical", "unrelated"):
        rows.append([f"{names[g]} ({sum(cm[g].values())})"] + [str(cm[g].get(p, 0)) for p in ("direct", "topical", "unrelated")])
    return table("tab:perlevel", f"Per-level scores of the measurement tool ({split_note([src['tool']])}).", "lrrr",
                 "Level (n) & P / direct & R / topical & F1 / unrelated", rows, colsep="4pt",
                 note="Upper rows: precision, recall and F1 of each level. Lower rows: how the photos of each annotated level "
                      "were labelled by the tool, columns direct / topical / unrelated.")


def t6(src):
    tool, m2 = load(src["tool"]), load(src["module2"])
    rows = method_rows(tool, [("tool", "Measurement tool (full)"), ("tool_no_ocr", "\\quad without OCR anchors"),
                              ("tool_no_logo", "\\quad without logo anchors")])
    rows += method_rows(m2, [("q3:module2", "Decomposed questions (full)"), ("q3:m2_one_question", "\\quad one question instead of several"),
                             ("q3:m2_no_gate", "\\quad without the camera-visibility gate"), ("q3:m2_no_dsg", "\\quad without DSG dependencies"),
                             ("q3:m2_no_topical", "\\quad without the topic check")])
    for r, key in zip(rows[1:3], ("tool_no_ocr", "tool_no_logo")):   # 10/21 review: the anchoring rows leave depiction
        vs = tool["methods"][key]["vs_ref"]                             # unchanged, so their delta is the four-level difference
        r[1:4] = ["=", "=", "="]
        r[-1] = f"{vs['macro_f1_4_diff']:+.3f}" + ci(vs.get("diff4_ci"))
    return table("tab:ablation", f"Ablations ({split_note(src.values())}).", "lrrrrrr",
                 "Variant & Macro-F1 & Acc. & $\\kappa_w$ & 4-level & Anchor F1 & $\\Delta$", rows, wide=True,
                 note="$\\Delta$: paired difference in three-level macro-F1 with its 95\\% interval; for the two anchoring rows, "
                      "whose depiction scores equal the full tool's by construction (=), the difference in four-level macro-F1.")


def t7(src):
    pf, re_, st = load(src["photo_filter"]), load(src["reuse"]), load(src["stock"])
    real = re_["real"]
    rows = [["Non-photo share of filtered images", pct(1 - pf["totals"]["photos"] / pf["totals"]["candidates"]) + "\\%"],   # 10/28: shorter label (the test counts widened the value column by 5.5 pt)
            ["Reused from the previous year", f"{intn(real['queries_with_a_reuse'])} / {intn(real['queries'])} ({pct(real['share_of_queries'])}\\%)"],   # 10/28: shorter label, same reason
            ["\\quad of which byte-identical files", intn(real["verified_md5_identical"])],
            ["Photo pairs verified across firms", intn(st["verified_pairs"])]]
    syn = re_["synthetic"]["methods"]
    bp = load(src["b_precision"])["by_kind"]["reuse"]["methods"] if src.get("b_precision") else {}   # 10/27: annotation B
    rows2 = [[esc(k), num(v.get("precision")), num(v.get("recall")), num(v.get("f1")),
              (num(bp[k]["precision"]) + f" ({bp[k]['pairs']})") if k in bp else "--"] for k, v in syn.items()]
    t_a = table("tab:fill", f"Visual filler ({split_note([src['reuse']])}; reuse and stock counts before annotation B).", "lr",
                "Indicator & Value", rows, colsep="3pt")   # 10/24: shorter labels and padding, the table was 34 pt too wide
    t_b = table("tab:reuse-methods", f"Reuse detection: synthetic pairs ({split_note([src['reuse']])}) and the precision of each method's "
                "own picks on the annotated pairs of annotation B (development split; pairs in brackets).", "lrrrr",
                "Method & P & R & F1 & Prec.\ on B pairs", rows2, colsep="3pt")
    return t_a + "\n" + t_b


def t2(src):
    s = load(src["summary"])
    a = s["A"]; f = a["filter"]
    thr = f["thresholds"]

    def row(label, m):
        return [label, intn(m["images"]), pct(m["accuracy"]) + "\\%", num(m["precision_photo"]), num(m["recall_photo"])]
    rows = [row("Test, both annotators agree", f["on_agreed_images"]),
            row("Test, vs.\\ annotator a1", f["vs_a1"]), row("Test, vs.\\ annotator a2", f["vs_a2"])]
    if src.get("dev"):
        d = load(src["dev"])["chosen"]["weighted"]
        rows.append(["Development (weighted)", "--", pct(d["accuracy"]) + "\\%", num(d["precision"]), num(d["recall"])])
    return table("tab:photo-filter", f"Photo filter accuracy against annotation A (200 random test images, two annotators). Thresholds fixed before "
                 f"the test annotations: OpenCLIP photo score $\\ge$ {thr['photo_threshold']} or VLM $\\ge$ {thr['vlm_threshold']}. "
                 "The development row is weighted to the development population of judged images.",
                 "lrrrr", "Images & $n$ & Acc. & P & R", rows, note="P and R for the photo class.", colsep="3pt")


def t3(src):
    a = load(src["A"])["A"]; r2 = load(src["R2"]); b = load(src["B"])
    t3_ = r2["three_levels"]
    rows = [["A: photo or not (two students, test)", intn(a["images"]), intn(a["same"]), num(a["kappa"], 2), "--"],
            ["R vs R2: three levels (author vs student, test)", intn(r2["photos"]), intn(t3_["same"]), num(t3_["kappa_w"], 2) + "$^{w}$",
             f"[{t3_['kappa_w_ci'][0]:.2f}, {t3_['kappa_w_ci'][1]:.2f}]"],
            ["R vs R2: four levels", intn(r2["photos"]), intn(r2["four_levels"]["same"]), num(r2["four_levels"]["kappa"], 2), "--"],
            ["R vs R2: anchored or not", intn(r2["photos"]), intn(r2["anchoring"]["same"]), num(r2["anchoring"]["kappa"], 2), "--"],
            ["B: same photo (two students, dev.)", intn(b["pairs"]), intn(b["agree"]), num(b["cohen_kappa"], 2), "--"]]
    return table("tab:agreement", "Annotation agreement (Cohen's $\\kappa$; $w$: linear-weighted $\\kappa$ on the ordered levels, with a "
                 "firm-level bootstrap interval).", "lrrrr", "Annotation & Items & Same & $\\kappa$ & 95\\% CI", rows, colsep="3pt", wide=True)


def t8(src):
    an = load(src["analyze"])
    rows = []
    for s, b in an["by_section"].items():
        name = {"公司概況": "Company overview", "其他": "Other"}.get(s, s)
        rows.append([esc(name), intn(b["photos"]), *(pct(b[lv]) for lv in ("證據", "示意", "包裝", "無關")), pct(b["reuse_share"]), pct(b["stock_share"])])
    return table("tab:reports", f"Evidence levels by section ({split_note([src['analyze']])}; shares in \\%, the measurement tool's estimates).",
                 "lrrrrrrr", "Section & Photos & Evidence & Illustration & Packaging & Unrelated & Reused & Stock", rows, wide=True)


def numbers(man):
    """paper/numbers.tex: the numbers the text quotes, as macros (\\nPhotos ...), from the same result files.
    Macros with 'Dev' in the name come from the development split and are replaced before submission."""
    out = ["% generated by src/make_tables.py; do not edit by hand"]

    def mac(name, val, src):
        out.append(f"\\newcommand{{\\{name}}}{{{val}}}  % {src}")
    s1 = man["table1_data"]
    ex, pf = load(s1["extract"]), load(s1["photo_filter"])
    sub, tot, ps = ex["subtotals"]["without_no_text_layer"], pf["totals"], pf["per_split"]
    for name, val in (("nReportsAll", ex["totals"]["reports"]), ("nReports", sub["reports"]), ("nPages", sub["pages"]),
                      ("nPlacements", sub["placements"]), ("nDistinct", sub["distinct"]), ("nJudged", tot["candidates"]),
                      ("nPhotos", tot["photos"]), ("nPhotosDev", ps["dev"]["photos"]), ("nPhotosTest", ps["test"]["photos"]),
                      ("nNonPhotos", tot["nonphoto"])):
        mac(name, intn(val), s1["photo_filter"] if name.startswith(("nJudged", "nPhotos", "nNon")) else s1["extract"])
    mac("pNonPhoto", pct(tot["nonphoto"] / tot["candidates"]), s1["photo_filter"])
    mac("nNoTextReports", intn(ex["totals"]["reports"] - sub["reports"]), s1["extract"])
    t5 = man.get("table5_methods") or {}
    if t5.get("tool") and (ROOT / t5["tool"]).exists():
        ev = load(t5["tool"])
        m = ev["methods"]
        mac("devPhotos", ev["photos_labelled"], t5["tool"])
        mac("devFirms", ev["firms"], t5["tool"])
        for key, name in (("tool", "Tool"), ("module2", "Decomp"), ("E2_cal", "ZeroCal"), ("E1", "Clip")):
            if key in m:
                mac(f"dev{name}F", num(m[key]["macro_f1"]), t5["tool"])
        if "module2" in m and m["module2"].get("vs_ref"):
            v = m["module2"]["vs_ref"]
            mac("devToolOverDecomp", f"{-v['macro_f1_diff']:.3f}", t5["tool"])
            mac("devToolOverDecompCI", f"[{-v['diff_ci'][1]:.3f}, {-v['diff_ci'][0]:.3f}]", t5["tool"])
        mac("devToolFourF", num(m["tool"].get("macro_f1_4")), t5["tool"])
        mac("devAnchorF", num(m["tool"].get("anchor_f1")), t5["tool"])
        if "tool_no_ocr" in m:
            mac("devAnchorFNoOcr", num(m["tool_no_ocr"].get("anchor_f1")), t5["tool"])
            mac("devFourFNoOcr", num(m["tool_no_ocr"].get("macro_f1_4")), t5["tool"])
    smp = man.get("sampling") or {}
    if smp.get("sample") and (ROOT / smp["sample"]).exists():   # 04_dataset.tex
        sm = load(smp["sample"])["summary"]
        for name, val in (("nPopulation", sm["population"]), ("nSampled", sm["sampled"]), ("nDevFirms", sm["dev"]),
                          ("nTestFirms", sm["test"]), ("nIndustries", sm["industries"]), ("nTWSE", sm["sample_market"]["TWSE"]),
                          ("nTPEx", sm["sample_market"]["TPEx"])):
            mac(name, intn(val), smp["sample"])
    qc = ROOT / "annotations" / "qc"
    if (qc / "R_dev_claude_edits.jsonl").exists():   # the development calibration (03_task.tex)
        edits = [l for l in (qc / "R_dev_claude_edits.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        meta = json.loads((qc / "R_dev_claude_reading.jsonl").read_text(encoding="utf-8").splitlines()[0])["meta"]
        mac("devCalibEdits", len(edits), "annotations/qc/R_dev_claude_edits.jsonl")
        mac("devCalibItems", meta["items_read"], "annotations/qc/R_dev_claude_reading.jsonl")
    # ---- 10/21: split-neutral macros for the results text (Eden: write the paper now, fill the test numbers later).
    # The same names are regenerated from the test files once sources.yaml points at them; \evalSplit names the split.
    if t5.get("tool") and (ROOT / t5["tool"]).exists():
        ev = load(t5["tool"]); m = ev["methods"]; src = t5["tool"]
        mac("evalSplit", "development" if ev["split"] == "dev" else "test", src)
        mac("evalPhotos", ev["photos_labelled"], src); mac("evalFirms", ev["firms"], src)
        T = m["tool"]
        mac("toolF", num(T["macro_f1"]), src); mac("toolFci", f"[{T['ci']['macro_f1'][0]:.2f}, {T['ci']['macro_f1'][1]:.2f}]", src)
        mac("toolAcc", num(T["accuracy"]), src); mac("toolKw", num(T["kappa_w"]), src)
        mac("toolFourF", num(T["macro_f1_4"]), src); mac("anchorF", num(T["anchor_f1"]), src)
        pl = T["per_level"]
        mac("directF", num(pl["direct"]["f1"]), src); mac("directP", num(pl["direct"]["precision"]), src)
        mac("directR", num(pl["direct"]["recall"]), src); mac("topicalR", num(pl["topical"]["recall"]), src)
        mac("unrelatedP", num(pl["unrelated"]["precision"]), src); mac("unrelatedR", num(pl["unrelated"]["recall"]), src)
        sb = T["share_bias_4"]
        mac("biasEvidence", f"{100 * sb['證據']:+.1f}", src); mac("biasUnrelated", f"{100 * sb['無關']:+.1f}", src)
        # 10/28: 6.5 error counts (annotator level -> tool level) from the confusion matrices of the tool
        c3, c4 = T["confusion_label_by_model"], T["confusion4_label_by_model"]
        for lv, name in (("direct", "Direct"), ("topical", "Topical"), ("unrelated", "Unrelated")):
            mac(f"label{name}N", intn(sum(c3[lv].values())), src)
        for lv, name in (("證據", "Evidence"), ("示意", "Illustration"), ("包裝", "Packaging"), ("無關", "UnrelatedFour")):   # no digits in LaTeX macro names
            mac(f"label{name}N", intn(sum(c4[lv].values())), src)
        mac("errDirectAsTopical", intn(c3["direct"].get("topical", 0)), src); mac("errDirectAsUnrelated", intn(c3["direct"].get("unrelated", 0)), src)
        mac("errDirectMissed", intn(c3["direct"].get("topical", 0) + c3["direct"].get("unrelated", 0)), src)
        mac("errTopicalAsDirect", intn(c3["topical"].get("direct", 0)), src)
        mac("errEvidenceAsIllustration", intn(c4["證據"].get("示意", 0)), src)   # depicted thing seen, anchor missed
        mac("errIllustrationAsEvidence", intn(c4["示意"].get("證據", 0)), src)   # anchor found where the annotator ticked none
        mac("errPackagingAsUnrelated", intn(c4["包裝"].get("無關", 0)), src)
        for key, name in (("module2", "decomp"), ("E2_cal", "zeroCal"), ("E1", "clip"), ("E3_uncal", "fewUncal"),
                          ("q25_module2", "decompOldVlm"), ("q25_E2_cal", "zeroCalOldVlm")):
            if key in m:
                mac(f"{name}F", num(m[key]["macro_f1"]), src)
                mac(f"{name}Acc", num(m[key]["accuracy"]), src); mac(f"{name}Kw", num(m[key]["kappa_w"]), src)
                if m[key].get("vs_ref"):   # 10/22 review: the text uses the table's convention, method minus tool, 2-decimal intervals
                    v = m[key]["vs_ref"]
                    mac(f"{name}Diff", f"{v['macro_f1_diff']:+.3f}", src)
                    mac(f"{name}DiffCI", f"[{v['diff_ci'][0]:.2f}, {v['diff_ci'][1]:.2f}]", src)
                    if v.get("mcnemar_direct_vs_rest"):
                        mac(f"{name}P", num(v["mcnemar_direct_vs_rest"]["p"], 2), src)
        for key, name in (("tool_no_ocr", "NoOcr"), ("tool_no_logo", "NoLogo")):
            if key in m:
                mac(f"anchorF{name}", num(m[key]["anchor_f1"]), src); mac(f"fourF{name}", num(m[key]["macro_f1_4"]), src)
    if t5.get("third") and (ROOT / t5["third"]).exists():   # 10/22 NEXT C12: third VLM family, same run pairs it with the tool
        m = load(t5["third"])["methods"]; src = t5["third"]
        for key, name in (("ivl:E3_cal", "thirdFewCal"), ("ivl:E2_cal", "thirdZeroCal"), ("ivl:E3", "thirdFewUncal")):
            if key in m:
                mac(f"{name}F", num(m[key]["macro_f1"]), src); mac(f"{name}FourF", num(m[key].get("macro_f1_4")), src)
                if m[key].get("vs_ref"):
                    v = m[key]["vs_ref"]
                    mac(f"{name}Diff", f"{v['macro_f1_diff']:+.3f}", src)
                    mac(f"{name}DiffCI", f"[{v['diff_ci'][0]:.2f}, {v['diff_ci'][1]:.2f}]", src)
    t4 = man.get("table4_context") or {}
    if t4.get("extrinsic") and (ROOT / t4["extrinsic"]).exists():
        cx = load(t4["extrinsic"])["methods"]; src = t4["extrinsic"]
        for key, name in (("tool@gold", "Gold"), ("tool@rules", "Rules"), ("tool@M_lr", "Lr"), ("tool@L1_nearest", "Nearest"),
                          ("tool@L2_column", "Column"), ("tool@L4_heading", "Heading"), ("tool@L3_caption", "Caption")):
            if key in cx:
                mac(f"ctx{name}F", num(cx[key]["macro_f1"]), src)
                if cx[key].get("vs_ref"):   # 10/28: paired difference against the rule-based context (context minus rules)
                    v = cx[key]["vs_ref"]
                    mac(f"ctx{name}D", f"{v['macro_f1_diff']:+.3f}", src)
                    mac(f"ctx{name}DCI", f"[{v['diff_ci'][0]:.3f}, {v['diff_ci'][1]:.3f}]", src)   # 3 decimals: one bound is -0.001
                cf = cx[key]["confusion_label_by_model"].get("direct", {})   # where the direct photos go under this context
                mac(f"ctx{name}DirectAsTopical", intn(cf.get("topical", 0)), src)
                mac(f"ctx{name}DirectAsUnrelated", intn(cf.get("unrelated", 0)), src)
                mac(f"ctx{name}BiasUnrelated", f"{100 * cx[key]['share_bias_4']['無關']:+.1f}", src)
        mac("ctxHeadingFourF", num(cx["tool@L4_heading"]["macro_f1_4"]), src)
        mac("ctxDirectN", intn(load(t4["extrinsic"])["labels"]["direct"]), src)
    if t4.get("context_link") and (ROOT / t4["context_link"]).exists():
        cl = load(t4["context_link"])["vs_labels"]; src = t4["context_link"]
        mac("goldBoxes", intn(cl["gold_boxes"]), src); mac("linkPhotos", intn(cl["gold_photos"]), src)
        mac("noTextPhotosTest" if "test" in Path(src).stem else "noTextPhotos", intn(cl["no_text_photos"]), src)
        for key, name in (("L5_rules", "Rules"), ("M_lr", "Lr"), ("L1_nearest", "Nearest"), ("L3_caption", "Caption"),
                          ("L4_heading", "Heading"), ("L2_column", "Column")):
            s = cl["scores"][key]
            mac(f"link{name}P", num(s["precision"]), src); mac(f"link{name}R", num(s["recall"]), src); mac(f"link{name}F", num(s["f1"]), src)
    cld = man.get("context_link_dev")   # 10/28: §4 describes the development annotation (its no-text count)
    if cld and (ROOT / cld).exists() and "test" in Path(t4.get("context_link", "")).stem:
        mac("noTextPhotos", intn(load(cld)["vs_labels"]["no_text_photos"]), cld)
    t6 = man.get("table6_ablation") or {}
    if t6.get("module2") and (ROOT / t6["module2"]).exists():
        mm = load(t6["module2"])["methods"]; src = t6["module2"]
        for key, name in (("q3:m2_no_topical", "NoTopical"), ("q3:m2_one_question", "OneQ"), ("q3:m2_no_gate", "NoGate"),
                          ("q3:m2_no_dsg", "NoDsg"), ("q3:E2", "zeroUncal")):
            if key in mm:
                mac(f"abl{name}F" if name[0].isupper() else f"{name}F", num(mm[key]["macro_f1"]), src)
                if mm[key].get("vs_ref") and name[0].isupper():
                    mac(f"abl{name}D", f"{mm[key]['vs_ref']['macro_f1_diff']:+.3f}", src)
    t7 = man.get("table7_fill") or {}
    if t7.get("reuse") and (ROOT / t7["reuse"]).exists():
        ru = load(t7["reuse"]); src = t7["reuse"]; re_ = ru["real"]; sy = ru["synthetic"]
        mac("reuseQueries", intn(re_["queries"]), src); mac("reuseFound", intn(re_["queries_with_a_reuse"]), src)
        mac("reuseShare", pct(re_["share_of_queries"]), src); mac("reuseIdentical", intn(re_["verified_md5_identical"]), src)
        for key, name in (("sscd", "Sscd"), ("ours", "Ours"), ("dinov2", "Dino"), ("phash", "Phash")):
            mac(f"synth{name}F", num(sy["methods"][key]["f1"]), src)
        mac("synthPositives", intn(sy["positives"]), src); mac("synthNegatives", intn(sy["negatives"]), src)
        mac("retrievalUnionRecall", num(sy["retrieval_recall_at_k"]["union"]), src); mac("retrievalK", sy["retrieval_recall_at_k"]["k"], src)
    if t7.get("stock") and (ROOT / t7["stock"]).exists():
        st = load(t7["stock"]); src = t7["stock"]
        mac("stockPairs", intn(st["verified_pairs"]), src); mac("stockPhotos", intn(st["photos_with_a_cross_firm_match"]), src)
        mac("stockShare", pct(st["share_of_photos"]), src)
    t8 = man.get("table8_reports") or {}
    if t8.get("analyze") and (ROOT / t8["analyze"]).exists():
        an = load(t8["analyze"]); src = t8["analyze"]; ov = an["overall"]
        mac("analyzeSplit", "development" if an["split"] == "dev" else "test", src)
        mac("analyzePhotos", intn(ov["photos"]), src)
        for lv, name in (("證據", "Evidence"), ("示意", "Illustration"), ("包裝", "Packaging"), ("無關", "Unrelated")):
            mac(f"share{name}", pct(ov[lv]), src)
        mac("shareAnchored", pct(ov["anchored"]), src)
        for sec, name in (("E", "E"), ("S", "S"), ("G", "G")):
            if sec in an["by_section"]:
                mac(f"shareEvidence{name}", pct(an["by_section"][sec]["證據"]), src)
                mac(f"sharePackaging{name}", pct(an["by_section"][sec]["包裝"]), src)
                mac(f"shareUnrelated{name}", pct(an["by_section"][sec]["無關"]), src)   # 10/28: 7 on the test split
        for yr, name in (("2024", "A"), ("2025", "B")):   # LaTeX macro names cannot hold digits
            if yr in an["by_year"] and an["by_year"][yr]["reuse_share"] is not None:
                mac(f"reuseShare{name}", pct(an["by_year"][yr]["reuse_share"]), src)
    if t4.get("extrinsic") and (ROOT / t4["extrinsic"]).exists():   # 10/21 review: automatic-linker spread vs the annotator's text
        cx = load(t4["extrinsic"])["methods"]; src = t4["extrinsic"]
        auto = [cx[k]["macro_f1"] for k in cx if k != "tool@gold"]
        mac("ctxSpreadAuto", num(max(auto) - min(auto)), src)
        mac("ctxGoldGain", num(cx["tool@gold"]["macro_f1"] - max(auto)), src)
    asp = man.get("anchored_split")
    if asp and (ROOT / asp).exists():   # 10/22 review: an anchor on a non-direct photo leaves it packaging or unrelated
        a = load(asp); sh = a["anchored_by_level4_share"]; n_ = a["anchored_by_level4"]
        mac("shareAnchoredNotDirect", pct(sh.get("包裝", 0) + sh.get("無關", 0)), asp)
        mac("anchoredPackagingN", intn(n_.get("包裝", 0)), asp); mac("anchoredUnrelatedN", intn(n_.get("無關", 0)), asp)
    if t7.get("reuse") and (ROOT / t7["reuse"]).exists():
        bf = sorted(load(t7["reuse"])["real"]["by_firm"].values(), reverse=True)
        mac("reuseTopThreeShare", pct(sum(bf[:3]) / sum(bf)), t7["reuse"]); mac("reuseFirms", intn(len(bf)), t7["reuse"])
        mac("reuseFirmsZero", intn(sum(v == 0 for v in bf)), t7["reuse"])
    ovl = man.get("overlay")
    if ovl and (ROOT / ovl).exists():
        o = load(ovl)
        mac("overlayPhotos", intn(o["overlay_ticked"]), ovl); mac("overlayFalseAnchors", intn(o["false_anchor_through_overlay"]), ovl)
        mac("toolOcrAnchors", intn(o["tool_ocr_anchors"]), ovl); mac("annotatorTextAnchors", intn(o["annotator_text_anchors"]), ovl)
    ovd = man.get("overlay_dev")   # 10/28: the test annotator ticked no overlay, so 5.3 quotes the development check
    if ovd and (ROOT / ovd).exists():
        o = load(ovd)
        mac("overlayPhotosDev", intn(o["overlay_ticked"]), ovd); mac("overlayFalseAnchorsDev", intn(o["false_anchor_through_overlay"]), ovd)
    rdev = ROOT / "annotations" / "dev" / "R.jsonl"
    if rdev.exists():   # counts only
        rows_ = [json.loads(l) for l in rdev.read_text(encoding="utf-8").splitlines() if l.strip()]
        mac("rItemsDev", intn(len(rows_)), "annotations/dev/R.jsonl"); mac("rNotPhotoDev", intn(sum(bool(r.get("not_photo")) for r in rows_)), "annotations/dev/R.jsonl")
        mac("rPhotosDev", intn(len(rows_) - sum(bool(r.get("not_photo")) for r in rows_)), "annotations/dev/R.jsonl")
    rtest = ROOT / "annotations" / "test" / "R.jsonl"
    if rtest.exists():   # 10/28: counts only (items, non-photos, photos, photos without a level); no label distribution
        rows_ = [json.loads(l) for l in rtest.read_text(encoding="utf-8").splitlines() if l.strip()]
        ph_ = [r for r in rows_ if not r.get("not_photo")]
        mac("rItemsTest", intn(len(rows_)), "annotations/test/R.jsonl"); mac("rNotPhotoTest", intn(len(rows_) - len(ph_)), "annotations/test/R.jsonl")
        mac("rPhotosTest", intn(len(ph_)), "annotations/test/R.jsonl")
        mac("rNoLevelTest", intn(sum(1 for r in ph_ if not r.get("level"))), "annotations/test/R.jsonl")
    if (qc / "R_dev_claude_edits.jsonl").exists():
        kinds = {"anchor": 0, "level": 0, "describes": 0}
        for l in (qc / "R_dev_claude_edits.jsonl").read_text(encoding="utf-8").splitlines():
            if l.strip():
                e = json.loads(l)
                for k in kinds:
                    kinds[k] += e["before"].get(k) != e["after"].get(k)
        mac("editsAnchor", kinds["anchor"], "annotations/qc/R_dev_claude_edits.jsonl"); mac("editsLevel", kinds["level"], "annotations/qc/R_dev_claude_edits.jsonl")
        mac("editsBoxes", kinds["describes"], "annotations/qc/R_dev_claude_edits.jsonl")
    r2 = man.get("r2_check")   # 10/23: annotation R2 returned; the count of photos labelled twice
    if r2 and (ROOT / r2).exists():
        mac("rTwoItems", load(r2)["items"], r2)
    rb = man.get("rebuild")   # NEXT 10/22 D14: rebuild test(s); a report counts once, from the run in which it passed
    if rb:
        files = [rb] if isinstance(rb, str) else list(rb)
        passed = {}
        for f in files:
            if (ROOT / f).exists():
                for x in load(f)["reports"]:
                    if x["pass"]:
                        passed[x["report"]] = x
        if passed:
            src = ", ".join(files)
            mac("rebuildReports", len(passed), src)
            mac("rebuildFirms", len({k.split("_")[0] for k in passed}), src)
            mac("rebuildRecords", f"{sum(x['records_release'] for x in passed.values()):,}", src)
    tp = man.get("throughput") or {}   # NEXT 10/22 A7: wall-clock figures recorded in result files
    if tp.get("e3") and (ROOT / tp["e3"]).exists():
        e = load(tp["e3"]); mac("fewShotSeconds", intn(round(e["wall_seconds"])), tp["e3"]); mac("fewShotPhotos", intn(e["photos_reported"]), tp["e3"])
        mac("fewShotSecPerPhoto", num(e["wall_seconds"] / e["photos_reported"], 1), tp["e3"])
    if tp.get("reuse") and (ROOT / tp["reuse"]).exists():
        r_ = load(tp["reuse"]); mac("reuseSeconds", intn(round(r_["seconds"])), tp["reuse"]); mac("reusePhotos", intn(r_["photos"]), tp["reuse"])
    if tp.get("stock") and (ROOT / tp["stock"]).exists():
        s_ = load(tp["stock"]); mac("stockSeconds", intn(round(s_["seconds"])), tp["stock"])
    if tp.get("measure_all") and (ROOT / tp["measure_all"]).exists():   # 10/28: the whole tool over every test photo
        m_ = load(tp["measure_all"]); mac("measureAllSeconds", intn(round(m_["seconds"])), tp["measure_all"])
        mac("measureAllPhotos", intn(m_["photos"]), tp["measure_all"])
    if tp.get("page_graph") and (ROOT / tp["page_graph"]).exists():
        g = load(tp["page_graph"]); mac("pageGraphSeconds", intn(round(g["seconds"])), tp["page_graph"])
        mac("pageGraphWorkers", intn(int(g["meta"]["command"].split("--workers")[1].split()[0])), tp["page_graph"])
    xt = man.get("crosstab")
    if xt and (ROOT / xt).exists():   # 10/22 review: describing text and depiction coincide on the development split
        x = load(xt)
        mac("textN", intn(x["with_text"]["n"]), xt); mac("textDirect", intn(x["with_text"]["direct"]), xt)
        mac("noTextN", intn(x["no_text"]["n"]), xt); mac("noTextTopical", intn(x["no_text"]["topical"]), xt)
        mac("noTextUnrelated", intn(x["no_text"]["unrelated"]), xt)
    xt2 = man.get("crosstab_test")   # 10/27: the same crosstab on the test split (the development one stays for 4.4 / 8)
    if xt2 and (ROOT / xt2).exists():
        x = load(xt2)
        mac("textNTest", intn(x["with_text"]["n"]), xt2); mac("textDirectTest", intn(x["with_text"]["direct"]), xt2)
        mac("textTopicalTest", intn(x["with_text"]["topical"]), xt2); mac("noTextNTest", intn(x["no_text"]["n"]), xt2)
        mac("noTextDirectTest", intn(x["no_text"]["direct"]), xt2); mac("noTextTopicalTest", intn(x["no_text"]["topical"]), xt2)
        mac("noTextUnrelatedTest", intn(x["no_text"]["unrelated"]), xt2); mac("crosstabPhotosTest", intn(x["photos"]), xt2)
    ls = man.get("level_shares")   # 10/27: the annotators' estimate of the four levels (test split, Section 7 and the abstract)
    if ls and (ROOT / ls).exists():
        h = load(ls)
        names = {"證據": "Evidence", "示意": "Illustration", "包裝": "Packaging", "無關": "Unrelated"}
        mac("humanPhotos", intn(h["photos"]), ls); mac("humanFirms", intn(h["firms"]), ls)
        for lv, nm in names.items():
            mac(f"human{nm}", f"{h['shares_percent'][lv]:.1f}", ls)
            mac(f"human{nm}CI", f"[{h['ci_percent'][lv][0]:.1f}, {h['ci_percent'][lv][1]:.1f}]", ls)
            mac(f"human{nm}N", intn(h["counts4"].get(lv, 0)), ls)
        mac("humanAnchored", f"{h['shares_percent']['anchored']:.1f}", ls)
    sm = man.get("annotation_summary")   # 10/27: D, C and A from the test annotations
    if sm and (ROOT / sm).exists():
        z = load(sm); d, c, a = z["D"], z["C"], z["A"]
        mac("dPages", intn(d["pages"]), sm); mac("dPagesNoPhoto", intn(d["pages_without_photos"]), sm)
        mac("dMissed", intn(d["missed_photos_drawn"]), sm); mac("dMissedPages", intn(d["pages_with_a_missed_photo"]), sm)
        mac("dWrong", intn(d["wrong_boxes_total"]), sm); mac("dWrongPages", intn(d["pages_with_a_wrong_box"]), sm)
        mac("dWrongPartial", intn(d["wrong_boxes"].get("partial", 0)), sm); mac("dWrongMerged", intn(d["wrong_boxes"].get("merged", 0)), sm)
        mac("cPhotos", intn(c["photos"]), sm); mac("cFirstSame", intn(c["first_choice_same_photo"]), sm)
        mac("cFirstPrecision", num(c["first_choice_precision"], 2), sm); mac("cVerifiedShare", f"{c['verified_reuse_share_percent']:.1f}", sm)
        mac("aImages", intn(a["images"]), sm); mac("aSame", intn(a["same"]), sm); mac("aKappa", num(a["kappa"], 2), sm)
        fa = a["filter"]["on_agreed_images"]
        mac("filterImages", intn(fa["images"]), sm); mac("filterAcc", f"{100 * fa['accuracy']:.1f}", sm)
        mac("filterP", num(fa["precision_photo"], 2), sm); mac("filterR", num(fa["recall_photo"], 2), sm)
    r2 = man.get("r2_agreement")
    if r2 and (ROOT / r2).exists():
        q = load(r2); t3_ = q["three_levels"]
        mac("rTwoPhotos", intn(q["photos"]), r2); mac("rTwoSame", intn(t3_["same"]), r2); mac("rTwoKappa", num(t3_["kappa_w"], 2), r2)
        mac("rTwoKappaCI", f"[{t3_['kappa_w_ci'][0]:.2f}, {t3_['kappa_w_ci'][1]:.2f}]", r2)
        mac("rTwoAnchorKappa", num(q["anchoring"]["kappa"], 2), r2); mac("rTwoFourKappa", num(q["four_levels"]["kappa"], 2), r2)
    bp = man.get("b_precision")
    if bp and (ROOT / bp).exists():
        q = load(bp); ru = q["by_kind"]["reuse"]["methods"]; st = q["by_kind"]["stock"]["methods"]
        for key, nm in (("ours", "Ours"), ("sscd", "Sscd"), ("dinov2", "Dino"), ("phash", "Phash"), ("md5", "Md")):
            if key in ru:
                mac(f"bPrec{nm}", num(ru[key]["precision"], 2), bp); mac(f"bPairs{nm}", intn(ru[key]["pairs"]), bp)
        if "ours" in st:
            mac("bStockPrecOurs", num(st["ours"]["precision"], 2), bp); mac("bStockPairsOurs", intn(st["ours"]["pairs"]), bp)
        mac("bDisagree", intn(q["disagreements"]), bp)
    cv = man.get("c_verified")   # 10/28 (cloud 10/27): annotation C against the frozen reuse detector on the test split
    if cv and (ROOT / cv).exists():
        q = load(cv)
        mac("cDetectorVerified", intn(q["detector_verified_first_choice"]), cv); mac("cBothVerified", intn(q["both"]), cv)
        mac("cVerifiedPrecision", num(q["detector_precision_on_verified"], 2), cv)
        mac("cYesVerified", f"{100 * q['annotator_yes_verified_by_detector']:.0f}", cv)
    ag = man.get("agreement") or {}
    if ag.get("B") and (ROOT / ag["B"]).exists():
        b = load(ag["B"]); src = ag["B"]
        mac("bPairs", intn(b["pairs"]), src); mac("bAgree", intn(b["agree"]), src); mac("bKappa", num(b["cohen_kappa"], 2), src)
    if ag.get("S") and (ROOT / ag["S"]).exists():
        s = load(ag["S"]); src = ag["S"]
        mac("sCheckPages", intn(s["pages"]), src); mac("sCheckSame", intn(s["same"]), src)
    (ROOT / "paper" / "numbers.tex").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("numbers.tex ok")


GEN = {"1": ("table1_data", t1), "2": ("table2_photo_filter", t2), "3": ("table3_agreement", t3), "4": ("table4_context", t4),
       "5": ("table5_methods", t5), "5b": ("table5b_perlevel", t5b), "6": ("table6_ablation", t6),
       "7": ("table7_fill", t7), "8": ("table8_reports", t8)}
PENDING = {}   # 10/27: Tables 2 and 3 are built from the test annotations


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    man = yaml.safe_load((OUT / "sources.yaml").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    for num_, (name, fn) in GEN.items():
        if args.only and num_ not in args.only:
            continue
        src = man.get(name) or {}
        missing = [p for p in src.values() if not (ROOT / p).exists()]
        if not src or missing:
            tex = f"% {name}: waiting for {missing or 'sources'}\n\\begin{{table}}[t]\\centering\\caption{{{name} (pending)}}\\end{{table}}\n"
        else:
            head = "".join(f"% {k}: {v} (sha256 {sha(v)})\n" for k, v in src.items())
            tex = f"% generated by src/make_tables.py; do not edit by hand\n{head}{fn(src)}"
        (OUT / f"{name}.tex").write_text(tex, encoding="utf-8")
        print(f"{name}.tex", "pending" if (not src or missing) else "ok")
    if not args.only:
        numbers(man)
    for num_, (name, cap) in PENDING.items():
        if args.only and num_ not in args.only:
            continue
        label = {"table2_photo_filter": "tab:photo-filter", "table3_agreement": "tab:agreement"}.get(name, "tab:" + name)
        (OUT / f"{name}.tex").write_text(f"% {name}: waits for the test-split annotations\n\\begin{{table}}[t]\\centering\\caption{{{cap} (pending)}}\\label{{{label}}}\n"
                                    "\\begin{tabular}{l}\\toprule Waits for the test split.\\\\ \\bottomrule\\end{tabular}\\end{table}\n", encoding="utf-8")   # sn-jnl writes the label only with a body
        print(f"{name}.tex pending")


if __name__ == "__main__":
    main()
