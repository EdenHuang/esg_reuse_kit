"""Check the downloaded reports (PLAN §4, table 1): right firm and year, and which ones are images only.

Usage (from the repository root):
  python src/check_downloads.py --catalog data/catalog.csv --cover-check results/download_cover_check_20261018.json

For every downloaded report in the catalog:
  text layer  - pages with more than 20 characters of text; a report with none is a scanned/image-only
                report (CLAUDE.md: detect, exclude, report the count)
  period      - the year written after 報告期間 / 揭露期間 / 報導期間 / reporting period on pages 1-12
                (ROC years converted): match / mixed (catalog year and another year) / mismatch / none
  cover check - optional: a hand (AI-assisted) check of page 1-3 images and text, one entry per report
                with firm_ok, year_ok, evidence (a JSON list, or {"meta": ..., "checks": [...]})
Writes results/download_check_{stamp}.json and reports/download_check_{stamp}.html.
"""
import argparse
import base64
import csv
import html
import json
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meta import run_meta, run_stamp  # noqa: E402

PERIOD = re.compile(r"(報告期間|揭露期間|報導期間|資訊揭露期間|報告書期間|涵蓋期間|報告書揭露期間|資料期間|報告範疇期間|reporting period)"
                    r"[^0-9]{0,20}((?:20[12]\d)|(?:1[01]\d))\s*年?", re.I)
PERIOD_PAGES = 12


def scan(path):
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)
    with pymupdf.open(path) as doc:
        text_pages, years, snippets = 0, [], []
        for page in doc:
            t = re.sub(r"\s+", "", page.get_text())
            text_pages += len(t) > 20
            if page.number < PERIOD_PAGES:
                for m in PERIOD.finditer(t):
                    y = int(m.group(2))
                    years.append(y + 1911 if y < 200 else y)
                    snippets.append(f"p{page.number + 1}: {t[max(0, m.start() - 15):m.end() + 35]}")
        thumb = ""
        if text_pages == 0:  # keep a small picture of page 1 for the HTML
            thumb = base64.b64encode(doc[0].get_pixmap(dpi=30).tobytes("jpg", jpg_quality=70)).decode()
        return {"pages": doc.page_count, "text_pages": text_pages, "period_years": years,
                "period_snippets": snippets[:4], "thumb": thumb}


def period_status(years, want):
    if not years:
        return "none"
    c = Counter(years)
    if c.most_common(1)[0][0] == want:
        return "match"
    return "mixed" if want in c else "mismatch"


def write_html(path, summary, recs):
    e = html.escape
    img_only = "".join(
        f"<figure><img src='data:image/jpeg;base64,{r['thumb']}' alt='{e(r['name'])}'>"
        f"<figcaption>{e(r['name'])} {e(r['firm_name'])}（{e(r['split'])}，{r['pages']} 頁）</figcaption></figure>"
        for r in recs if r["image_only"])
    rows = "".join(
        f"<tr><td>{e(r['name'])}</td><td>{e(r['firm_name'])}</td><td>{e(r['split'])}</td><td>{r['pages']}</td>"
        f"<td>{r['text_pages']}</td><td>{e(r['period'])}</td><td>{e(','.join(map(str, sorted(set(r['period_years'])))))}</td>"
        f"<td>{e(r.get('firm_ok', ''))}</td><td>{e(r.get('year_ok', ''))}</td><td>{e(r.get('evidence', ''))}</td></tr>"
        for r in recs)
    odd = "".join(f"<li>{e(r['name'])}（{e(r['period'])}）：{e(' ｜ '.join(r['period_snippets']))}</li>"
                  for r in recs if r["period"] in ("mixed", "mismatch"))
    summ = "".join(f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>" for k, v in summary.items())
    doc = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>下載檢查</title>
<style>:root{{--bg:#fff;--fg:#1d1d1f;--line:#ddd;--mute:#666}}
@media (prefers-color-scheme: dark){{:root{{--bg:#161616;--fg:#eee;--line:#333;--mute:#aaa}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;max-width:1200px;margin:0 auto;padding:16px}}
table{{border-collapse:collapse}}th,td{{border-bottom:1px solid var(--line);padding:3px 8px;text-align:left;vertical-align:top}}
.wrap{{overflow-x:auto}}figure{{display:inline-block;margin:6px;text-align:center}}figcaption{{font-size:13px;color:var(--mute)}}
img{{max-width:100%;border:1px solid var(--line)}}li{{font-size:13px}}</style></head><body>
<h1>下載檢查（{len(recs)} 本）</h1>
<div class="wrap"><table>{summ}</table></div>
<h2>沒有文字層的報告（沒有任何一頁超過 20 個字）</h2><div>{img_only or '沒有'}</div>
<h2>報告期間年份不是目錄年度或混了別的年份（原文前後文）</h2><ul>{odd or '<li>沒有</li>'}</ul>
<h2>每本報告</h2><p>「期間」：第 1–{PERIOD_PAGES} 頁「報告期間／揭露期間…」後面的年份。「公司、年度」：看第 1–3 頁圖和文字的檢查。</p>
<div class="wrap"><table><tr><th>報告</th><th>公司</th><th>分組</th><th>頁數</th><th>有文字的頁</th><th>期間</th><th>期間年份</th><th>公司</th><th>年度</th><th>依據</th></tr>{rows}</table></div>
</body></html>"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(doc, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="data/catalog.csv")
    ap.add_argument("--cover-check", help="JSON list: name, firm_ok, year_ok, covered_year_seen, evidence")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    rows = [r for r in csv.DictReader(open(args.catalog, encoding="utf-8-sig")) if r["sha256"]]
    rows = [r for r in rows if Path(r["file_path"]).is_file()]
    with ProcessPoolExecutor(args.workers) as pool:
        scans = list(pool.map(scan, [r["file_path"] for r in rows]))
    cover = {}
    if args.cover_check:
        data = json.load(open(args.cover_check, encoding="utf-8"))
        cover = {x["name"]: x for x in (data["checks"] if isinstance(data, dict) else data)}
    recs = []
    for r, s in zip(rows, scans):
        name = f"{r['firm_id']}_{r['year']}"
        c = cover.get(name, {})
        recs.append({"name": name, "firm_id": r["firm_id"], "firm_name": r["firm_name"], "year": r["year"],
                     "split": r["split"], **s, "image_only": s["text_pages"] == 0,
                     "period": period_status(s["period_years"], int(r["year"])),
                     **{k: c[k] for k in ("firm_ok", "year_ok", "covered_year_seen", "evidence") if k in c}})
    years = Counter(r["firm_id"] for r in recs)
    img = [r["name"] for r in recs if r["image_only"]]
    summary = {
        "下載的報告": len(recs),
        "三年都到手的公司": sum(v == 3 for v in years.values()),
        "沒有文字層的報告": f"{len(img)} 本（{len({n.split('_')[0] for n in img})} 家）：{'、'.join(img)}",
        "排除這些報告後三年都可用的公司": sum(v == 3 for v in Counter(r["firm_id"] for r in recs if not r["image_only"]).values()),
        "報告期間年份": dict(Counter(r["period"] for r in recs)),
    }
    if cover:
        summary["公司、年度檢查（第 1–3 頁）"] = dict(Counter(f"公司 {r.get('firm_ok', '沒檢查')}／年度 {r.get('year_ok', '沒檢查')}" for r in recs))
    stamp = run_stamp()
    data_files = [args.catalog] + ([args.cover_check] if args.cover_check else [])
    out = Path("results") / f"download_check_{stamp}.json"
    out.write_text(json.dumps({"meta": run_meta("configs/default.yaml", data_files), "summary": summary,
                               "reports": [{k: v for k, v in r.items() if k != "thumb"} for r in recs]},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    page = Path("reports") / f"download_check_{stamp}.html"
    write_html(page, summary, recs)
    for k, v in summary.items():
        print(f"{k}：{v}")
    print(f"wrote {out} and {page}")


if __name__ == "__main__":
    main()
