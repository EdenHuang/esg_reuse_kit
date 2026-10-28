"""Report-level statistics (PLAN §5 step 9, Table 8 and the fourth paper's data): for each report, each E / S / G
section, year and industry, the four-level shares of the photos (measure.py), the non-photo share, the cross-year
reuse share (reuse.py) and the cross-firm stock share (stock.py).

Inputs (one split): work/measure/{split}_all.jsonl, work/section/{split}_photos.jsonl (section_map.py),
work/photo_filter.csv, work/reuse/{split}_candidates.jsonl, work/reuse/stock_{split}_candidates.jsonl, data/catalog.csv.
  non-photo share  among the images the photo filter judged (no exclusion other than 'nonphoto': covers, reports
                   without a text layer, vector shadings, layout elements and duplicates are left out)
  reuse share      among the photos of year-t reports whose firm has a t-1 report: verified same photo or shot
  stock share      photos with a verified match in another firm's report
The four levels are the tool's estimates; the bias against annotation R (evaluate.py, test split) is reported next to
them in Table 8. The test split needs configs/frozen.yaml (frozen.check).
Writes results/analyze_{split}_{stamp}.json, work/analyze/{split}_reports.csv and reports/analyze_{split}_{stamp}.html.

  python src/analyze.py --split dev
"""
import argparse
import collections
import csv
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import yaml  # noqa: E402

import frozen  # noqa: E402
from meta import run_meta, run_stamp  # noqa: E402

L4 = ("證據", "示意", "包裝", "無關")
SECTIONS = ("E", "S", "G", "公司概況", "其他")


def jsonl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8")] if Path(p).exists() else []


def shares(rows):
    n = len(rows)
    return {"photos": n, **{lv: round(sum(r["level4"] == lv for r in rows) / n, 4) if n else None for lv in L4},
            "anchored": round(sum(r["anchored"] for r in rows) / n, 4) if n else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    args = ap.parse_args()
    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))
    frozen.check(args.split, cfg)
    meas = jsonl(ROOT / "work" / "measure" / f"{args.split}_all.jsonl")
    if not meas:
        sys.exit(f"沒有 work/measure/{args.split}_all.jsonl：先跑 measure.py --split {args.split} --photos all")
    sec = {r["crop"]: r["class"] for r in jsonl(ROOT / "work" / "section" / f"{args.split}_photos.jsonl")}
    cat = {}
    with open(ROOT / cfg["paths"]["catalog"], encoding="utf-8-sig", newline="") as f:
        for d in csv.DictReader(f):
            cat[f"{d['firm_id'].strip()}_{d['year'].strip()}"] = d
    judged, photos_f = collections.Counter(), collections.Counter()
    with open(ROOT / "work" / "photo_filter.csv", encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if d["split"] != args.split:
                continue
            reasons = set(filter(None, d["exclude"].split("|")))
            if reasons <= {"nonphoto"}:
                judged[d["report"]] += 1
                photos_f[d["report"]] += d["keep"] == "1"
    reuse_q = collections.defaultdict(bool)
    for r in jsonl(ROOT / "work" / "reuse" / f"{args.split}_candidates.jsonl"):
        reuse_q[r["query"]] |= bool(r["same"])
    stock = set()
    for r in jsonl(ROOT / "work" / "reuse" / f"stock_{args.split}_candidates.jsonl"):
        if r["same"]:
            stock |= {r["a"], r["b"]}
    for r in meas:
        r["section"] = sec.get(r["crop"], "其他")
        r["industry"] = (cat.get(r["report"]) or {}).get("industry", "")
        r["reuse_query"] = r["crop"] in reuse_q
        r["reused"] = reuse_q.get(r["crop"], False)
        r["stock"] = r["crop"] in stock

    def block(rows):
        q = [r for r in rows if r["reuse_query"]]
        return {**shares(rows), "reuse_queries": len(q), "reuse_share": round(sum(r["reused"] for r in q) / len(q), 4) if q else None,
                "stock_share": round(sum(r["stock"] for r in rows) / len(rows), 4) if rows else None}
    by = lambda key: {k: block([r for r in meas if r[key] == k]) for k in sorted({r[key] for r in meas}, key=str)}  # noqa: E731
    reports = []
    for rep in sorted({r["report"] for r in meas}):
        rows = [r for r in meas if r["report"] == rep]
        b = block(rows)
        reports.append({"report": rep, "firm": rep.split("_")[0], "year": int(rep.split("_")[1]), "industry": rows[0]["industry"],
                        "images_judged": judged[rep], "photos": len(rows),
                        "nonphoto_share": round(1 - photos_f[rep] / judged[rep], 4) if judged[rep] else None,
                        **{k: b[k] for k in L4}, "anchored": b["anchored"], "reuse_queries": b["reuse_queries"],
                        "reuse_share": b["reuse_share"], "stock_share": b["stock_share"],
                        **{f"photos_{s}": sum(r["section"] == s for r in rows) for s in SECTIONS}})
    tot_j, tot_p = sum(judged.values()), sum(photos_f.values())
    res = {"split": args.split, "reports": len(reports), "firms": len({r["firm"] for r in reports}), "photos": len(meas),
           "images_judged": tot_j, "nonphoto_share": round(1 - tot_p / tot_j, 4) if tot_j else None,
           "overall": block(meas), "by_section": by("section"), "by_year": by("year"), "by_industry": by("industry"),
           "section_x_level4": {s: dict(collections.Counter(r["level4"] for r in meas if r["section"] == s)) for s in SECTIONS},
           "report_level": {"mean_evidence_share": round(sum(r["證據"] for r in reports) / len(reports), 4) if reports else None,
                            "mean_nonphoto_share": round(sum(r["nonphoto_share"] or 0 for r in reports) / len(reports), 4) if reports else None},
           "note": "four levels are the measurement tool's estimates (measure.py); on the development split its cuts were set on the same R photos",
           "sources": {"measure": f"work/measure/{args.split}_all.jsonl", "section": f"work/section/{args.split}_photos.jsonl",
                       "reuse": f"work/reuse/{args.split}_candidates.jsonl", "stock": f"work/reuse/stock_{args.split}_candidates.jsonl"}}
    out_dir = ROOT / "work" / "analyze"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / f"{args.split}_reports.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(reports[0]))
        w.writeheader()
        w.writerows(reports)
    stamp = run_stamp()
    rp = ROOT / "results" / f"analyze_{args.split}_{stamp}.json"
    k = 2
    while rp.exists():
        rp = ROOT / "results" / f"analyze_{args.split}_{stamp}_{k}.json"
        k += 1
    inputs = [ROOT / "work" / "photo_filter.csv"] + [ROOT / v for v in res["sources"].values() if (ROOT / v).exists()]
    rp.write_text(json.dumps({"meta": run_meta("configs/default.yaml", inputs), **res}, ensure_ascii=False, indent=1), encoding="utf-8")

    def tr(name, b):
        f = lambda x: "—" if x is None else f"{100 * x:.1f}%"  # noqa: E731
        return (f"<tr><td>{html.escape(str(name))}</td><td>{b['photos']}</td>" + "".join(f"<td>{f(b[lv])}</td>" for lv in L4)
                + f"<td>{f(b['anchored'])}</td><td>{f(b['reuse_share'])}</td><td>{f(b['stock_share'])}</td></tr>")
    head = "<tr><th></th><th>照片</th>" + "".join(f"<th>{lv}</th>" for lv in L4) + "<th>有定錨</th><th>跨年重用</th><th>跨公司</th></tr>"
    sect = "".join(tr(k2, v) for k2, v in res["by_section"].items())
    yrs = "".join(tr(k2, v) for k2, v in res["by_year"].items())
    ind = "".join(tr(k2 or "（不明）", v) for k2, v in res["by_industry"].items())
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>報告層級統計</title><style>
:root{{--bg:#fafafa;--fg:#222;--mut:#777;--line:#ddd}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1b1b;--fg:#eee;--mut:#999;--line:#444}}}}
body{{font-family:system-ui,"Microsoft JhengHei",sans-serif;margin:0 auto;padding:16px;background:var(--bg);color:var(--fg);max-width:1000px;line-height:1.6}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;font-size:14px;margin:8px 0}} td,th{{border:1px solid var(--line);padding:4px 8px;text-align:right}} td:first-child,th:first-child{{text-align:left}}
.mut{{color:var(--mut);font-size:13px}}</style></head><body>
<h1 style="font-size:20px">報告層級統計（{'開發集' if args.split == 'dev' else '測試集'}）</h1>
<p>{res['reports']} 本報告、{res['firms']} 家、照片 {res['photos']:,} 張；給模型判斷的圖 {tot_j:,} 張，非照片 {100 * (res['nonphoto_share'] or 0):.1f}%。
四級是量測工具估的（開發集上它的切點是用同一批 R 照片調的，這裡只是先把程式寫好）。</p>
<h2 style="font-size:17px">全部</h2><div class=wrap><table>{head}{tr('全部', res['overall'])}</table></div>
<h2 style="font-size:17px">E／S／G</h2><div class=wrap><table>{head}{sect}</table></div>
<h2 style="font-size:17px">年度</h2><div class=wrap><table>{head}{yrs}</table></div>
<h2 style="font-size:17px">產業</h2><div class=wrap><table>{head}{ind}</table></div>
<p class=mut>跨年重用：t 年的照片在 t−1 年報告找得到同一張或同一畫面（2023 年沒有前一年，不算）。每本報告的數字在 work/analyze/{args.split}_reports.csv。</p></body></html>"""
    hp = ROOT / "reports" / f"{rp.stem}.html"
    hp.write_text(page, encoding="utf-8")
    print(json.dumps({k2: res[k2] for k2 in ("reports", "photos", "nonphoto_share")} | {"overall": res["overall"]}, ensure_ascii=False))
    print(f"wrote {rp} and {hp}")


if __name__ == "__main__":
    main()
