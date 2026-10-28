"""Per-report extraction statistics (the Table 1 columns) and a check against known numbers.

  python src/report_extract.py --catalog data/catalog_pilot.csv --extract work/extract --tag local --expect pilot

For every catalog row that has {extract}/{firm_id}_{year}_pairs.jsonl, counts
  pages, placements (圖片位置), pictures (合併圖層後), distinct (不同的圖),
  layout_groups / layout_places (版面元素 組/處), same_page_groups (同頁畫兩次),
  reused_2pages (跨兩頁重用), analysed (列入分析 = distinct - layout_groups).
--expect pilot compares the two pilot reports with the numbers in CLAUDE.md / LOG.md and,
record by record, with pilot/*_pairs.jsonl on every field the pilot run also wrote
(all fields except the report name and the crop file name).
Writes results/extract_check_{stamp}_{tag}.json and reports/extract_check_{stamp}_{tag}.html.
Exit code 3 when an expected number or a pilot record differs.
"""
import argparse
import base64
import csv
import html
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meta import run_meta, run_stamp  # noqa: E402

EXPECTED_BY_VERSION = {  # pilot PDFs (data/raw_pilot); CLAUDE.md「第一次開工」3, PLAN §5 step 1 table
    # v1: the cloud pilot and 10/18 local runs; vector gradient fills were counted as pictures
    "v1": {
        "1707_2025": {"pages": 155, "placements": 130, "pictures": 123, "distinct": 107,
                      "layout_groups": 2, "layout_places": 14, "same_page_groups": 4, "analysed": 105},
        "1101_2025": {"pages": 235, "placements": 355, "pictures": 327, "distinct": 318,
                      "layout_groups": 2, "layout_places": 9, "reused_2pages": 1, "analysed": 316},
    },
    # v2 (10/18 evening): extract_pairs.py skips vector gradient fills. The change from v1 is
    # exactly the gradient pictures, 7 and 10 (results/extract_compare_20261018_1808_pilot.json)
    "v2": {
        "1707_2025": {"pages": 155, "placements": 118, "pictures": 116, "distinct": 100,
                      "layout_groups": 2, "layout_places": 14, "same_page_groups": 4, "analysed": 98},
        "1101_2025": {"pages": 235, "placements": 333, "pictures": 317, "distinct": 308,
                      "layout_groups": 2, "layout_places": 9, "reused_2pages": 1, "analysed": 306},
    },
}
CURRENT = "v2"
EXPECTED = EXPECTED_BY_VERSION[CURRENT]
PILOT_FILES = {"1707_2025": "grapeking_pairs.jsonl", "1101_2025": "tcc_pairs.jsonl"}
SKIP_FIELDS = {"report", "crop"}
COLUMNS = [("pages", "頁數"), ("placements", "圖片位置"), ("pictures", "合併圖層後"),
           ("distinct", "不同的圖"), ("layout_groups", "版面元素（組）"), ("layout_places", "版面元素（處）"),
           ("same_page_groups", "同頁畫兩次（組）"), ("reused_2pages", "跨兩頁重用（組）"),
           ("analysed", "列入分析")]
ROLE_LABEL = {"layout_repeat": "版面元素（3 頁以上，排除）", "same_page_repeat": "同一頁畫兩次（只算一次）",
              "reused": "報告內跨兩頁重用（保留）"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def stats(records, pages):
    reps = [r for r in records if r["representative"]]
    layout = [r for r in reps if r["doc_role"] == "layout_repeat"]
    return {"pages": pages,
            "placements": sum(len(r["layers"]) for r in records),
            "pictures": len(records),
            "distinct": len(reps),
            "layout_groups": len(layout),
            "layout_places": sum(r["dup_count"] for r in layout),
            "same_page_groups": sum(r["doc_role"] == "same_page_repeat" for r in reps),
            "reused_2pages": sum(r["doc_role"] == "reused" for r in reps),
            "analysed": len(reps) - len(layout)}


def page_count(row):
    if (row.get("pages") or "").strip():
        return int(row["pages"])
    pdf = Path((row.get("file_path") or "").strip())
    if pdf.is_file():
        import pymupdf
        with pymupdf.open(pdf) as doc:
            return len(doc)
    return None


def compare_pilot(records, pilot_path):
    old = load(pilot_path)
    fields = sorted((set(old[0]) & set(records[0])) - SKIP_FIELDS) if old and records else []
    out = {"pilot_file": str(pilot_path), "records_new": len(records), "records_pilot": len(old),
           "fields_compared": fields, "differences": abs(len(old) - len(records)), "examples": []}
    for i, (a, b) in enumerate(zip(records, old)):
        for k in fields:
            if a[k] != b[k]:
                out["differences"] += 1
                if len(out["examples"]) < 20:
                    out["examples"].append({"record": i, "page": a["page"], "field": k,
                                            "new": a[k], "pilot": b[k]})
    return out


def groups(records):
    """Repeated pictures inside the report: one entry per group that is not 'unique'."""
    out = []
    for r in records:
        if r["representative"] and r["doc_role"] != "unique":
            out.append({"role": r["doc_role"], "pages": r["dup_pages"], "count": r["dup_count"],
                        "crop": r["crop"], "first_page": r["page"]})
    return out


def thumb(path, size=200):
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def mark(item, key):
    got = item["stats"][key]
    ref = item.get("reference", {}).get(key)
    if ref is not None:  # --vs-pilot / --vs-extract: show the other number, no verdict
        return f'{got} <span class="mut">（{item.get("reference_label", "試跑")} {ref}）</span>'
    exp = item.get("expected", {}).get(key)
    if exp is None:
        return f"{got}"
    if got == exp:
        return f'{got} <span class="ok">✓</span>'
    return f'{got} <span class="bad">✗ 應為 {exp}</span>'


SUBTOTAL_LABEL = {"without_no_text_layer": "不含沒有文字層的報告（表 1）", "no_text_layer": "沒有文字層的報告（排除）",
                  "without_no_text_layer_dev": "　其中開發集", "without_no_text_layer_test": "　其中測試集"}


def subtotal_table(result):
    sub = result.get("subtotals") or {}
    if not sub:
        return ""
    e = html.escape
    head = "<tr><th>小計</th><th>報告</th>" + "".join(f"<th>{e(lab)}</th>" for _, lab in COLUMNS) + "</tr>"
    rows = ""
    for g, t in sub.items():
        cell = {"stats": t}
        if "vs_extract" in t:
            cell.update(reference=t["vs_extract"], reference_label="舊版")
        rows += (f"<tr><td>{e(SUBTOTAL_LABEL.get(g, g))}</td><td>{t['reports']}</td>"
                 + "".join(f"<td>{mark(cell, k)}</td>" for k, _ in COLUMNS) + "</tr>")
    return f"<h2>小計</h2><div class='wrap'><table>{head}{rows}</table></div><h2>每本報告</h2>"


def write_html(path, result, crop_dir):
    e = html.escape
    meta = result["meta"]
    if result["all_match"] is None:
        if result.get("vs_extract"):
            verdict = f"括號裡是舊版抽取（{result['vs_extract']}）的數字，只供對照。"
        elif any("reference" in it for it in result["reports"]):
            verdict = "括號裡是試跑版（公司官網的檔）的數字，只供對照。"
        else:
            verdict = "抽取統計。"
    else:
        verdict = ("結論：全部數字和試跑一致。✓ 表示等於 CLAUDE.md／LOG.md 記的試跑數字。" if result["all_match"]
                   else "結論：有數字跟試跑不一樣（紅色），先查原因再往下做。")
    rows = "".join(
        "<tr><td>" + e(it["name"]) + "</td>" + "".join(f"<td>{mark(it, k)}</td>" for k, _ in COLUMNS) + "</tr>"
        for it in result["reports"])
    if len(result["reports"]) > 2:
        tot = {"stats": result["totals"]}
        if result.get("totals_vs_extract"):
            tot.update(reference=result["totals_vs_extract"], reference_label="舊版")
        rows = ("<tr><td><b>合計（" + str(result["totals"]["reports"]) + " 本）</b></td>"
                + "".join(f"<td><b>{mark(tot, k)}</b></td>" for k, _ in COLUMNS) + "</tr>") + rows
    head = "<tr><th>報告</th>" + "".join(f"<th>{e(lab)}</th>" for _, lab in COLUMNS) + "</tr>"
    parts = [f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>抽取檢查 {e(result['tag'])}</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#666;--line:#ddd;--ok:#137333;--bad:#b3261e;--card:#f6f6f6}}
@media (prefers-color-scheme: dark){{:root{{--bg:#161616;--fg:#eee;--mut:#aaa;--line:#333;--ok:#6dd58c;--bad:#ff8a80;--card:#222}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;margin:0 auto;max-width:1100px;padding:16px}}
table{{border-collapse:collapse;margin:8px 0 20px;font-variant-numeric:tabular-nums}}
th,td{{border-bottom:1px solid var(--line);padding:6px 10px;text-align:right;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}} .wrap{{overflow-x:auto}}
.ok{{color:var(--ok);font-weight:600}} .bad{{color:var(--bad);font-weight:600}} .mut{{color:var(--mut)}}
.grid{{display:flex;flex-wrap:wrap;gap:12px}} .card{{background:var(--card);border-radius:8px;padding:8px;width:216px}}
.card img{{max-width:200px;max-height:200px;display:block;margin:auto}} .card p{{margin:4px 0 0;font-size:13px}}
</style></head><body>
<h1>抽取檢查：{e(result['tag'])}</h1>
<p class="mut">{e(meta['date'])}｜Python {e(meta['python'])}｜PyMuPDF {e(str(meta['packages'].get('pymupdf')))}｜Pillow {e(str(meta['packages'].get('pillow')))}｜{e(meta['platform'])}｜git {e(str(meta['git'].get('commit')))}</p>
<p><b>{verdict}</b></p>
{subtotal_table(result)}
<div class="wrap"><table>{head}{rows}</table></div>"""]
    for it in result["reports"]:
        pr = it.get("pilot_records")
        if pr:
            verdict = "完全相同" if pr["differences"] == 0 else f"有 {pr['differences']} 處不同"
            parts.append(f"<p><b>{e(it['name'])}</b> 逐筆比對試跑紀錄（{pr['records_new']} 筆對 {pr['records_pilot']} 筆，"
                         f"比 {len(pr['fields_compared'])} 個欄位：位置、圖說、小標題、段落、分組等）：<b>{verdict}</b></p>")
            if pr["examples"]:
                parts.append("<pre>" + e(json.dumps(pr["examples"], ensure_ascii=False, indent=1)) + "</pre>")
    parts.append("<h2>報告內重複的圖（被排除或只算一次的）</h2>")
    for it in result["reports"]:
        cards = []
        for g in it["groups"]:
            src = thumb(Path(crop_dir) / g["crop"])
            img = f'<img src="{src}" alt="">' if src else '<p class="mut">（沒有裁切圖）</p>'
            cards.append(f'<div class="card">{img}<p>{e(ROLE_LABEL.get(g["role"], g["role"]))}</p>'
                         f'<p class="mut">{g["count"]} 處，頁 {e(", ".join(map(str, g["pages"])))}</p></div>')
        parts.append(f"<h3>{e(it['name'])}</h3><div class='grid'>{''.join(cards) or '<p>無</p>'}</div>")
    parts.append("</body></html>")
    Path(path).write_text("\n".join(parts), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--extract", default="work/extract")
    ap.add_argument("--tag", required=True, help="short label, e.g. local or cloud")
    ap.add_argument("--expect", choices=["pilot", "pilot_v1"],
                    help=f"pilot = the {CURRENT} pilot numbers; pilot_v1 = the v1 numbers plus a record-by-record "
                         "check against pilot/*_pairs.jsonl (those files are v1)")
    ap.add_argument("--vs-pilot", action="store_true",
                    help="show the pilot numbers next to these (e.g. the official ESG InfoHub files); no pass/fail")
    ap.add_argument("--vs-extract", help="another extraction directory (e.g. work/extract_v1): show its numbers "
                                         "next to these, plus totals; no pass/fail")
    ap.add_argument("--download-check", help="results/download_check_*.json: add subtotals without the reports that "
                                             "have no text layer (image_only), overall and per split (Table 1)")
    ap.add_argument("--pilot-dir", default="pilot")
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--results", default="results")
    ap.add_argument("--reports", default="reports")
    args = ap.parse_args()

    ext = Path(args.extract)
    with open(args.catalog, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    items, all_match, inputs = [], True, [args.catalog]
    for row in rows:
        name = f"{row['firm_id'].strip()}_{row['year'].strip()}"
        pairs = ext / f"{name}_pairs.jsonl"
        if not pairs.exists():
            continue
        records = load(pairs)
        inputs += [pairs, (row.get("file_path") or "").strip()]
        item = {"name": name, "stats": stats(records, page_count(row)), "groups": groups(records)}
        if args.vs_pilot and name in EXPECTED:
            item["reference"] = EXPECTED[name]
        if args.vs_extract:
            other = Path(args.vs_extract) / f"{name}_pairs.jsonl"
            if other.exists():
                item["reference"] = stats(load(other), item["stats"]["pages"])
                item["reference_label"] = "舊版"
                inputs.append(other)
        version = {"pilot": CURRENT, "pilot_v1": "v1"}.get(args.expect)
        if version and name in EXPECTED_BY_VERSION[version]:
            exp = EXPECTED_BY_VERSION[version][name]
            item["expected"] = exp
            item["expected_version"] = version
            item["mismatch"] = {k: {"expected": v, "got": item["stats"][k]}
                                for k, v in exp.items() if item["stats"][k] != v}
            all_match &= not item["mismatch"]
            pilot = Path(args.pilot_dir) / PILOT_FILES[name]
            if version == "v1" and pilot.exists():  # the pilot record files are v1
                item["pilot_records"] = compare_pilot(records, pilot)
                all_match &= item["pilot_records"]["differences"] == 0
        items.append(item)
    if not items:
        sys.exit(f"no *_pairs.jsonl in {ext} for the reports in {args.catalog}")

    stamp = run_stamp()
    keys = [k for k, _ in COLUMNS]
    totals = {k: sum(it["stats"][k] or 0 for it in items) for k in keys}
    totals["reports"] = len(items)
    if args.vs_extract:
        with_ref = [it for it in items if "reference" in it]
        totals_ref = {k: sum(it["reference"][k] or 0 for it in with_ref) for k in keys}
        totals_ref["reports"] = len(with_ref)
    subtotals = {}
    if args.download_check:
        inputs.append(args.download_check)
        image_only = {r["name"] for r in json.loads(Path(args.download_check).read_text(encoding="utf-8"))["reports"]
                      if r["image_only"]}
        split = {f"{r['firm_id'].strip()}_{r['year'].strip()}": (r.get("split") or "").strip() for r in rows}
        groups_ = {"without_no_text_layer": [it for it in items if it["name"] not in image_only],
                   "no_text_layer": [it for it in items if it["name"] in image_only]}
        for s in sorted({v for v in split.values() if v}):
            groups_[f"without_no_text_layer_{s}"] = [it for it in groups_["without_no_text_layer"] if split.get(it["name"]) == s]
        for g, its in groups_.items():
            subtotals[g] = {"reports": len(its), **{k: sum(it["stats"][k] or 0 for it in its) for k in keys}}
            if args.vs_extract:
                subtotals[g]["vs_extract"] = {k: sum(it["reference"][k] or 0 for it in its if "reference" in it) for k in keys}
    result = {"meta": run_meta(args.config, [p for p in inputs if p]), "tag": args.tag, "subtotals": subtotals,
              "extract": str(ext), "vs_extract": args.vs_extract,
              "expected_version": {"pilot": CURRENT, "pilot_v1": "v1"}.get(args.expect),
              "all_match": all_match if args.expect else None, "totals": totals,
              **({"totals_vs_extract": totals_ref} if args.vs_extract else {}), "reports": items}
    Path(args.results).mkdir(parents=True, exist_ok=True)
    Path(args.reports).mkdir(parents=True, exist_ok=True)
    out_json = Path(args.results) / f"extract_check_{stamp}_{args.tag}.json"
    out_html = Path(args.reports) / f"extract_check_{stamp}_{args.tag}.html"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    write_html(out_html, result, ext / "crops")

    for it in items:
        s = it["stats"]
        print(f"{it['name']}: 頁 {s['pages']}｜圖片位置 {s['placements']} → 合併後 {s['pictures']} → "
              f"不同的圖 {s['distinct']}｜版面元素 {s['layout_groups']} 組 {s['layout_places']} 處｜"
              f"同頁畫兩次 {s['same_page_groups']}｜跨兩頁 {s['reused_2pages']}｜列入分析 {s['analysed']}")
        if it.get("mismatch"):
            print("  與試跑不同：", json.dumps(it["mismatch"], ensure_ascii=False))
        if it.get("pilot_records"):
            print(f"  逐筆比對試跑紀錄：{it['pilot_records']['differences']} 處不同")
    if len(items) > 2:
        t = totals
        print(f"合計 {t['reports']} 本：頁 {t['pages']}｜圖片位置 {t['placements']} → 合併後 {t['pictures']} → "
              f"不同的圖 {t['distinct']}｜版面元素 {t['layout_groups']} 組 {t['layout_places']} 處｜列入分析 {t['analysed']}")
        if args.vs_extract:
            t = totals_ref
            print(f"舊版 {t['reports']} 本：頁 {t['pages']}｜圖片位置 {t['placements']} → 合併後 {t['pictures']} → "
                  f"不同的圖 {t['distinct']}｜版面元素 {t['layout_groups']} 組 {t['layout_places']} 處｜列入分析 {t['analysed']}")
    for g, t in subtotals.items():
        old = f"（舊版列入分析 {t['vs_extract']['analysed']}）" if "vs_extract" in t else ""
        print(f"小計 {g}：{t['reports']} 本｜頁 {t['pages']}｜圖片位置 {t['placements']} → 合併後 {t['pictures']} → "
              f"不同的圖 {t['distinct']}｜列入分析 {t['analysed']}{old}")
    if args.expect:
        print(f"比對 {result['expected_version']} 的試跑數字" + ("" if args.expect == "pilot_v1" else
              "（v2 沒有逐筆試跑紀錄；和 v1 的逐筆差異見 results/extract_compare_*_pilot.json）"))
        print("結論：" + ("全部和試跑一致" if all_match else "有差異，先查原因（多半是 PyMuPDF 版本）"))
    print(f"wrote {out_json} and {out_html}")
    sys.exit(0 if (all_match or not args.expect) else 3)  # 3 = numbers differ (1 = the script crashed)


if __name__ == "__main__":
    main()
