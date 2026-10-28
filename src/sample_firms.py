"""Stratified sample of firms and the dev/test split (PLAN §4.2, D6, D7) -> data/catalog.csv.

  python src/sample_firms.py --population data/population_2023_2025_20261018.csv --names data/firm_names_20261018.csv
  python src/sample_firms.py --population data/population_2023_2025_20261018.csv --check data/catalog.csv

Allocation: seats proportional to industry size (n * N_h / N), largest-remainder rounding
(ties: smaller industry code first). An industry holding a must-include firm
(configs: data.dev_must_include) gets at least one seat.
Selection: inside each industry the must-include firms come first, then the other firms in
the order of sha256("{seed}:{firm_id}") -- a seeded random order that comes out the same on
every machine, operating system and Python version.
Split (by firm): must-include firms go to dev; the other sampled firms are ordered by
sha256("{seed}:split:{firm_id}"); the first n_dev - len(must_include) go to dev, the rest to test.
Writes data/catalog.csv (one row per firm and report year; url, sha256, pages and
download_date stay empty until download.py fills them), results/sample_{stamp}.json and
reports/sample_{stamp}.html. With --check nothing is written: the sample is derived again
and compared with the given catalog (exit code 3 if they differ).
"""
import argparse
import csv
import hashlib
import html
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meta import run_meta, run_stamp, sha256_file  # noqa: E402

CATALOG_FIELDS = ["firm_id", "firm_name", "market", "industry", "year", "url", "source_label", "file_path",
                  "sha256", "pages", "split", "download_date"]  # PLAN v2.0 §4.3; src/download.py sample fills url


def order_key(seed, *parts):
    return hashlib.sha256(":".join([str(seed), *map(str, parts)]).encode("utf-8")).hexdigest()


def allocate(sizes, n, must_strata):
    total = sum(sizes.values())
    quota = {h: n * s / total for h, s in sizes.items()}
    seats = {h: int(q) for h, q in quota.items()}
    rest = n - sum(seats.values())
    for h in sorted(sizes, key=lambda h: (-(quota[h] - seats[h]), h))[:rest]:
        seats[h] += 1
    for h in sorted(must_strata):
        if seats[h] == 0:  # take the seat from the industry that is most over its quota
            donor = min((g for g in seats if seats[g] > 1 and g not in must_strata),
                        key=lambda g: (quota[g] - seats[g], g))
            seats[donor] -= 1
            seats[h] += 1
    return quota, seats


def draw(pop, n, n_dev, seed, must):
    by_ind = defaultdict(list)
    for r in pop:
        by_ind[r["industry_code"]].append(r["firm_id"])
    missing = [m for m in must if m not in {r["firm_id"] for r in pop}]
    if missing:
        sys.exit(f"must-include firms not in the population: {missing}")
    must_strata = {r["industry_code"] for r in pop if r["firm_id"] in must}
    quota, seats = allocate({h: len(v) for h, v in by_ind.items()}, n, must_strata)
    sampled = []
    for h in sorted(by_ind):
        ranked = sorted(by_ind[h], key=lambda f: (f not in must, order_key(seed, f)))
        sampled += ranked[:seats[h]]
    must_in = [f for f in sampled if f in must]
    others = sorted((f for f in sampled if f not in must), key=lambda f: order_key(seed, "split", f))
    dev = set(must_in) | set(others[:n_dev - len(must_in)])
    split = {f: ("dev" if f in dev else "test") for f in sampled}
    return quota, seats, split


def write_html(path, result, info):
    e = html.escape
    rows = []
    for a in result["allocation"]:
        rows.append(f"<tr><td>{e(a['industry_code'])}</td><td>{e(a['industry'])}</td><td>{a['population']}</td>"
                    f"<td>{a['quota']:.2f}</td><td><b>{a['seats']}</b></td><td>{a['dev']}</td><td>{a['test']}</td></tr>")
    firms = defaultdict(list)
    for f in result["sample"]:
        firms[f["industry"]].append(f)
    lists = []
    for ind in sorted(firms, key=lambda s: firms[s][0]["industry_code"]):
        items = "、".join(
            (f"<b>{e(f['firm_id'])} {e(f['firm_name'])}（dev）</b>" if f["split"] == "dev"
             else f"{e(f['firm_id'])} {e(f['firm_name'])}") for f in firms[ind])
        lists.append(f"<p><b>{e(ind)}</b>：{items}</p>")
    s = result["summary"]
    doc = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>抽樣 150 家</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#666;--line:#ddd}}
@media (prefers-color-scheme: dark){{:root{{--bg:#161616;--fg:#eee;--mut:#aaa;--line:#333}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.7 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;margin:0 auto;max-width:1000px;padding:16px}}
table{{border-collapse:collapse;font-variant-numeric:tabular-nums}} th,td{{border-bottom:1px solid var(--line);padding:4px 10px;text-align:right}}
th:nth-child(-n+2),td:nth-child(-n+2){{text-align:left}} .mut{{color:var(--mut)}} .wrap{{overflow-x:auto}}
</style></head><body>
<h1>母體與抽樣（PLAN §4.2）</h1>
<p class="mut">{e(result['meta']['date'])}｜母體檔 {e(info['population'])}（sha256 {e(result['population_sha256'][:12])}…）｜seed {result['seed']}</p>
<p>母體 <b>{s['population']}</b> 家（2023–2025 三個年度都有中文永續報告書）→ 依產業比例抽 <b>{s['sampled']}</b> 家
（上市 {s['sample_market'].get('TWSE', 0)}、上櫃 {s['sample_market'].get('TPEx', 0)}），涵蓋 {s['industries_sampled']}／{s['industries']} 個產業。
開發集 <b>{s['dev']}</b> 家、測試集 <b>{s['test']}</b> 家；catalog 共 {s['catalog_rows']} 列（家數 × 3 年）。</p>
<p class="mut">「應分配」= 150 × 該產業家數 ÷ 母體家數；先取整數，剩下的名額給小數最大的產業。產業內的順序由 sha256(seed:代號) 決定，任何機器重跑都一樣。</p>
<div class="wrap"><table><tr><th>代碼</th><th>產業</th><th>母體家數</th><th>應分配</th><th>抽中</th><th>dev</th><th>test</th></tr>{''.join(rows)}</table></div>
<h2>抽中的公司（粗體是開發集）</h2>{''.join(lists)}
</body></html>"""
    Path(path).write_text(doc, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--population", required=True)
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--names", help="CSV with firm_id, firm_name (fills the catalog firm_name column)")
    ap.add_argument("--catalog", default="data/catalog.csv")
    ap.add_argument("--check", help="derive the sample again and compare with this catalog; write nothing")
    ap.add_argument("--results", default="results")
    ap.add_argument("--reports", default="reports")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    seed, d = cfg["seed"], cfg["data"]
    must = [str(x) for x in d["dev_must_include"]]
    with open(args.population, encoding="utf-8-sig", newline="") as f:
        pop = list(csv.DictReader(f))
    quota, seats, split = draw(pop, d["n_firms"], d["n_dev"], seed, must)

    if args.check:
        with open(args.check, encoding="utf-8-sig", newline="") as f:
            old = {(r["firm_id"], r["split"]) for r in csv.DictReader(f)}
        new = set(split.items())
        same = old == new
        print(f"重新抽樣 {len(split)} 家，和 {args.check} {'完全一樣' if same else '不一樣'}")
        if not same:
            print("  只在新抽樣：", sorted(new - old)[:20])
            print("  只在 catalog：", sorted(old - new)[:20])
        sys.exit(0 if same else 3)

    names = {}
    if args.names:
        with open(args.names, encoding="utf-8-sig", newline="") as f:
            names = {r["firm_id"]: r["firm_name"] for r in csv.DictReader(f)}
    info = {r["firm_id"]: r for r in pop}
    ind_name = {r["industry_code"]: r["industry"] for r in pop}
    sizes = Counter(r["industry_code"] for r in pop)

    Path(args.catalog).parent.mkdir(parents=True, exist_ok=True)
    with open(args.catalog, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        w.writeheader()
        for fid in sorted(split):
            for year in d["years"]:
                w.writerow({"firm_id": fid, "firm_name": names.get(fid, ""), "market": info[fid]["market"],
                            "industry": info[fid]["industry"], "year": year, "url": "", "source_label": "", "file_path": f"data/raw/{fid}_{year}.pdf", "sha256": "",
                            "pages": "", "split": split[fid], "download_date": ""})

    alloc = []
    for h in sorted(sizes):
        members = [f for f in split if info[f]["industry_code"] == h]
        alloc.append({"industry_code": h, "industry": ind_name[h], "population": sizes[h],
                      "quota": round(quota[h], 4), "seats": seats[h],
                      "dev": sum(split[f] == "dev" for f in members),
                      "test": sum(split[f] == "test" for f in members)})
    sample = [{"firm_id": f, "firm_name": names.get(f, ""), "market": info[f]["market"],
               "industry_code": info[f]["industry_code"], "industry": info[f]["industry"],
               "split": split[f]} for f in sorted(split)]
    summary = {"population": len(pop), "population_market": dict(Counter(r["market"] for r in pop)),
               "sampled": len(split), "sample_market": dict(Counter(s["market"] for s in sample)),
               "dev": sum(v == "dev" for v in split.values()), "test": sum(v == "test" for v in split.values()),
               "industries": len(sizes), "industries_sampled": sum(a["seats"] > 0 for a in alloc),
               "catalog_rows": len(split) * len(d["years"])}
    stamp = run_stamp()
    result = {"meta": run_meta(args.config, [args.population] + ([args.names] if args.names else [])),
              "seed": seed, "method": __doc__.split("Allocation:")[1].split("Writes")[0].strip(),
              "population_file": args.population, "population_sha256": sha256_file(args.population),
              "catalog_file": args.catalog, "catalog_sha256": sha256_file(args.catalog),
              "summary": summary, "allocation": alloc, "sample": sample}
    Path(args.results).mkdir(parents=True, exist_ok=True)
    Path(args.reports).mkdir(parents=True, exist_ok=True)
    out_json = Path(args.results) / f"sample_{stamp}.json"
    out_html = Path(args.reports) / f"sample_{stamp}.html"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    write_html(out_html, result, {"population": args.population})
    print(json.dumps(summary, ensure_ascii=False))
    print(f"wrote {args.catalog}, {out_json}, {out_html}")


if __name__ == "__main__":
    main()
