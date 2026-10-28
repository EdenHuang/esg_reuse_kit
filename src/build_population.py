"""Population for sampling (PLAN §4.2): listed (上市) and OTC (上櫃) firms that filed a Chinese
sustainability report for every report year in configs (2023-2025), from the TWSE ESG InfoHub.

  python src/build_population.py                      # query the API, write work/esg_api/{stamp}/
  python src/build_population.py --compare data/population_2023_2025_20261018.csv

API (checked 2026-10-18 in a browser; no login or token needed):
  GET  {BASE}MopsSustainReport/industryAndCompanyCode?marketType=0|1   firms and industry codes (0 上市, 1 上櫃)
  POST {BASE}MopsSustainReport/data   {"marketType":0,"year":2025,"industryNameList":[],
        "industryName":null,"companyCodeList":[...],"companyCode":"all"}   one record per filed report
  GET  {BASE}MopsSustainReport/data/FileStream?id={uuid}                  the PDF itself
Each report record carries twFirstReportDownloadId (first filing), twEditReportDownloadId
(amended version, all zeros if none), the upload dates and reportingInterval.
Years before 2023 live in the older MOPS system (MopsSustainReport/data/old) and are not used.

Inclusion: twStatus == 2, a Chinese file id, a record in every year, and a declared reporting
period that starts or ends in the report year. Typos in one end (2301: 2024/01/01~2025/03/31,
2345: 2025/01/01~2026/12/31) and two-year reports are kept; firms whose period lies wholly in
another year (2505, 3011, 4119, 5475 on 10/18) are dropped, since the file may belong to the
wrong year. This is the rule behind data/population_2023_2025_20261018.csv (1,069 firms).
Industry and market come from the current firm list. Requests run one at a time, at least
3 s apart (CLAUDE.md rule 6). Outputs (work/ is not in git, results/ is):
  work/esg_api/{stamp}/raw_*.json, population.csv, report_index.csv (every firm-year record:
  file ids and dates, for download.py), results/population_{stamp}.json (counts and meta).
"""
import argparse
import csv
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meta import run_meta, run_stamp  # noqa: E402

BASE = "https://esggenplus.twse.com.tw/api/api/"
MARKETS = {0: "TWSE", 1: "TPEx"}
ZERO_ID = "00000000-0000-0000-0000-000000000000"
USER_AGENT = "esg-reuse-kit/0.1 (academic research, National Dong Hwa University)"
_last_request = [0.0]


def _call(req, gap):
    wait = gap - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    finally:
        _last_request[0] = time.time()


def get_json(path, gap=3.0, base=BASE):
    req = urllib.request.Request(base + path, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    return _call(req, gap)


def post_json(path, body, gap=3.0, base=BASE):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                          "Content-Type": "application/json"})
    return _call(req, gap)


def company_list(market, base=BASE):
    return get_json(f"MopsSustainReport/industryAndCompanyCode?marketType={market}", base=base)["data"]


def report_records(market, year, codes, base=BASE):
    j = post_json("MopsSustainReport/data", {"marketType": market, "year": year, "industryNameList": [],
                                             "industryName": None, "companyCodeList": codes,
                                             "companyCode": "all"}, base=base)
    if not j.get("success"):
        raise RuntimeError(f"ESG InfoHub answered: {j.get('message')}")
    return j.get("data") or []


def file_url(file_id, base=BASE):
    return f"{base}MopsSustainReport/data/FileStream?id={file_id}"


def has_id(x):
    return bool(x) and x != ZERO_ID


def period_years(text):
    """(start year, end year) of a declared reporting period such as 2025/01/01~2025/12/31."""
    parts = str(text or "").split("~")
    out = []
    for p in (parts[0], parts[-1]):
        try:
            out.append(int(p.strip()[:4]))
        except ValueError:
            out.append(None)
    return tuple(out)


def period_ok(text, year):
    return year in period_years(text)


def build(years, base=BASE, raw_dir=None):
    lists = {m: company_list(m, base) for m in MARKETS}
    current = {}
    for m in MARKETS:
        for c in lists[m]["companyNameList"]:
            current[c["companyCode"]] = {"market": MARKETS[m], "industry_code": c["industryCode"],
                                         "firm_name": c["companyName"].split(" ", 1)[-1]}
    ind_name = {}
    for m in (1, 0):  # the listed-market name wins when both markets use a code
        ind_name.update({i["industryCode"]: i["industryName"] for i in lists[m]["industryNameList"]})
    codes = sorted(current)
    index, ok, counts, dropped = [], {}, {}, Counter()
    for y in years:
        for m in MARKETS:
            data = report_records(m, y, codes, base)
            counts[f"{y}_{MARKETS[m]}"] = len(data)
            if raw_dir:
                (raw_dir / f"raw_data_{y}_{m}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            for x in data:
                index.append({"firm_id": x["code"], "year": y, "market_filed": MARKETS[m], "sector": x.get("sector"),
                              "reporting_interval": x.get("reportingInterval"), "tw_status": x.get("twStatus"),
                              "tw_first_id": x.get("twFirstReportDownloadId"), "tw_first_date": x.get("twCommencementDate"),
                              "tw_edit_id": x.get("twEditReportDownloadId") if has_id(x.get("twEditReportDownloadId")) else "",
                              "tw_edit_date": x.get("twRevisionDate"),
                              "en_first_id": x.get("enFirstReportDownloadId") if has_id(x.get("enFirstReportDownloadId")) else "",
                              "en_edit_id": x.get("enEditReportDownloadId") if has_id(x.get("enEditReportDownloadId")) else ""})
                if x.get("twStatus") != 2:
                    dropped["status_not_2"] += 1
                elif not has_id(x.get("twFirstReportDownloadId")):
                    dropped["no_chinese_file"] += 1
                else:
                    ok[(y, x["code"])] = x
    if raw_dir:
        (raw_dir / "raw_company_lists.json").write_text(json.dumps(lists, ensure_ascii=False), encoding="utf-8")
    all_years = [c for c in codes if all((y, c) in ok for y in years)]
    wrong_period = sorted(c for c in all_years
                          if any(not period_ok(ok[(y, c)].get("reportingInterval"), y) for y in years))
    pop = []
    for c in all_years:
        if c in wrong_period:
            continue
        cur = current[c]
        pop.append({"firm_id": c, "firm_name": cur["firm_name"], "market": cur["market"],
                    "industry_code": cur["industry_code"], "industry": ind_name.get(cur["industry_code"], ""),
                    "en_years": ";".join(str(y) for y in years if has_id(ok[(y, c)].get("enFirstReportDownloadId"))),
                    "amended_years": ";".join(str(y) for y in years if has_id(ok[(y, c)].get("twEditReportDownloadId")))})
    summary = {"records_per_year_market": counts, "dropped_records": dict(dropped),
               "firms_with_report_each_year": {str(y): sum(1 for (yy, _) in ok if yy == y) for y in years},
               "firms_all_years": len(all_years), "dropped_wrong_period": wrong_period,
               "population": len(pop), "population_market": dict(Counter(p["market"] for p in pop))}
    return pop, index, summary


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--compare", help="earlier population CSV (firm_id, market, industry_code) to compare with")
    ap.add_argument("--base", default=BASE, help=argparse.SUPPRESS)  # for offline tests
    ap.add_argument("--results", default="results")
    args = ap.parse_args()

    years = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))["data"]["years"]
    stamp = run_stamp()
    out_dir = Path("work/esg_api") / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    pop, index, summary = build(years, args.base, out_dir)
    write_csv(out_dir / "population.csv", pop)
    write_csv(out_dir / "report_index.csv", index)

    comparison = None
    if args.compare:
        with open(args.compare, encoding="utf-8-sig", newline="") as f:
            old = {r["firm_id"]: (r["market"], r["industry_code"]) for r in csv.DictReader(f)}
        new = {p["firm_id"]: (p["market"], p["industry_code"]) for p in pop}
        comparison = {"file": args.compare, "same": old == new,
                      "only_new": sorted(set(new) - set(old)), "only_old": sorted(set(old) - set(new)),
                      "changed": sorted(k for k in set(old) & set(new) if old[k] != new[k])}
    result = {"meta": run_meta(args.config, [args.compare] if args.compare else []),
              "source": BASE, "years": years, "summary": summary, "comparison": comparison,
              "outputs": [str(out_dir / "population.csv"), str(out_dir / "report_index.csv")]}
    Path(args.results).mkdir(parents=True, exist_ok=True)
    out_json = Path(args.results) / f"population_{stamp}.json"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"母體 {summary['population']} 家 {summary['population_market']}；"
          f"三年都有 {summary['firms_all_years']} 家，報告期間年份不符排除 {summary['dropped_wrong_period']}")
    if comparison:
        print("和 " + args.compare + (" 完全一樣" if comparison["same"] else
              f" 不一樣：新增 {comparison['only_new'][:10]}，少了 {comparison['only_old'][:10]}，"
              f"產業或市場變了 {comparison['changed'][:10]}"))
    print(f"wrote {out_dir}/ and {out_json}")
    sys.exit(0 if (comparison is None or comparison["same"]) else 3)  # 3 = differs from the snapshot


if __name__ == "__main__":
    main()
