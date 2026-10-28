"""Index, sample and download the sustainability reports (PLAN §4) from the TWSE ESG InfoHub.
Run from the repository root:

  python src/download.py probe  --year 2025 --market sii --check-firm 1101
  python src/download.py index  --years 2023 2024 2025 --markets sii otc
  python src/download.py sample --n-firms 150 --n-dev 30
  python src/download.py fetch  [--limit 10] [--firms 1101 1707]
  python src/download.py register        # files saved by hand into data/raw/ -> catalog

Source (checked 2026-10-18 from Eden's PC, in the built-in browser and with Python):
  The old MOPS page (mopsov.twse.com.tw, t100sb11) still lists reports up to report year 2022,
  but for 2023 onward it only opens the ESG InfoHub (esggenplus.twse.com.tw) in a new window.
  The ESG InfoHub page reads a JSON API that needs no login or token (details in
  src/build_population.py): firm lists with industry codes, one query per market and year that
  returns every filed report, and MopsSustainReport/data/FileStream?id=... for the PDF itself.
  The API's year is the report year (1101's 2025 record covers 2025/01/01~2025/12/31), so no
  year offset is needed; --year-offset is accepted only for compatibility and ignored.
  The Chinese version is used; when an amended version exists it replaces the first filing
  (PLAN §4.3).

Sampling uses the frozen frame data/population_2023_2025_20261018.csv (1,069 firms) and the
seeded method in src/sample_firms.py, so it reproduces the 10/18 sample; `index` adds names,
markets and download links. `sample --from-company-list` is the by-hand fallback (PLAN §4.1).

Politeness: one request at a time, at least 3 s apart. Downloaded files are untrusted data:
they are written only under data/raw/ and never run.
"""
import argparse
import csv
import hashlib
import html
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_population as esg  # noqa: E402  (ESG InfoHub API client and inclusion rule)
from meta import run_stamp  # noqa: E402
from sample_firms import draw, order_key  # noqa: E402

MARKET_CODE = {"sii": 0, "otc": 1}
MARKET_NAME = {"sii": "TWSE", "otc": "TPEx"}
INDEX_DIR = Path("data/index")
RAW_DIR = INDEX_DIR / "raw"
INDEX_CSV = INDEX_DIR / "reports_index.csv"
CATALOG = Path("data/catalog.csv")
FRAME = Path("data/population_2023_2025_20261018.csv")
PROBE_DIR = Path("data/raw/_probe")
API = {"base": esg.BASE}  # replaced only by the hidden --base option (offline tests)
INDEX_FIELDS = ["firm_id", "year", "firm_name", "firm_full_name", "market", "market_filed", "industry_code",
                "industry", "reporting_interval", "tw_status", "version", "file_id", "upload_date",
                "first_id", "first_date", "edit_id", "edit_date", "en_first_id", "url"]
CATALOG_FIELDS = ["firm_id", "firm_name", "market", "industry", "year", "url", "source_label", "file_path",
                  "sha256", "pages", "split", "download_date"]


# ---------------------------------------------------------------- helpers
def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def config():
    return yaml.safe_load(Path("configs/default.yaml").read_text(encoding="utf-8"))


def choose(rec):
    """(file id, label, upload date) of the Chinese version to use: amended if it exists."""
    if esg.has_id(rec.get("twEditReportDownloadId")):
        return rec["twEditReportDownloadId"], "中文版修正版", rec.get("twRevisionDate") or ""
    return rec.get("twFirstReportDownloadId") or "", "中文版", rec.get("twCommencementDate") or ""


class Polite:
    """File downloads that share the API's clock: one request at a time, >= interval s apart."""

    def __init__(self, interval=3.0):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = esg.USER_AGENT
        self.interval = interval

    def get(self, url):
        last = ""
        for attempt in range(3):
            wait = self.interval - (time.time() - esg._last_request[0])
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.session.get(url, timeout=600)
                if resp.status_code == 200:
                    return resp
                last = f"HTTP {resp.status_code}"
            except requests.RequestException as e:
                last = repr(e)[:200]
            finally:
                esg._last_request[0] = time.time()
            time.sleep(10 * (attempt + 1))
        raise RuntimeError(f"GET {url} failed: {last}")


def pdf_pages(path):
    try:
        import pymupdf
        with pymupdf.open(path) as doc:
            return doc.page_count
    except Exception:
        return ""


def norm(text):
    return re.sub(r"\s+", "", str(text or "")).replace("臺", "台")


def sanity(path, year, names):
    """Does the start of the PDF mention the firm and the report year? A hint, not a verdict."""
    try:
        import pymupdf
        with pymupdf.open(path) as doc:
            text = norm("".join(doc[i].get_text() for i in range(min(3, doc.page_count))))
    except Exception as e:
        return {"name": "?", "year": "?", "note": f"讀不到：{e!r}"[:80]}
    if not text:
        return {"name": "?", "year": "?", "note": "前三頁沒有文字層"}
    name_ok = any(n and norm(n) in text for n in names)
    year_ok = str(year) in text or f"{int(year) - 1911}年" in text
    return {"name": "✓" if name_ok else "✗", "year": "✓" if year_ok else "✗", "note": ""}


# ---------------------------------------------------------------- commands
def cmd_probe(args):
    market = MARKET_CODE[args.market]
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    lists = esg.company_list(market, API["base"])
    codes = [c["companyCode"] for c in lists["companyNameList"]]
    print(f"[公司清單] {args.market}：{len(codes)} 家、{len(lists['industryNameList'])} 個產業")
    data = esg.report_records(market, args.year, codes, API["base"])
    (RAW_DIR / f"probe_{args.market}_{args.year}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"[報告清單] {args.year} 年度：{len(data)} 筆")
    rec = next((x for x in data if x.get("code") == args.check_firm), None)
    if rec is None:
        print(f"結論：清單裡沒有 {args.check_firm}，先檢查市場別和年度。")
        return 1
    print(f"  {rec['code']} {rec.get('shortName')}｜期間 {rec.get('reportingInterval')}｜"
          f"第一版上傳 {rec.get('twCommencementDate')}｜修正版上傳 {rec.get('twRevisionDate') or '無'}")
    if esg.period_ok(rec.get("reportingInterval"), args.year):
        print("  年度 = 報告涵蓋的年度，不用 --year-offset。")
    else:
        print("  注意：申報的期間和年度對不上，先人工看一下這筆。")
    file_id, label, _ = choose(rec)
    data = Polite(args.interval).get(esg.file_url(file_id, API["base"])).content
    if data[:5] != b"%PDF-":
        print(f"結論：下載回來的不是 PDF（{len(data)} bytes），先看 {esg.file_url(file_id, API['base'])}")
        return 1
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    out = PROBE_DIR / f"{args.check_firm}_{args.year}.pdf"
    out.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    print(f"  下載{label}：{len(data):,} bytes、{pdf_pages(out)} 頁 → {out}")
    for local in (Path("data/raw_pilot") / out.name, Path("data/raw") / out.name):
        if local.is_file():
            same = hashlib.sha256(local.read_bytes()).hexdigest() == digest
            print(f"  和手上的 {local}（{local.stat().st_size:,} bytes）{'是同一個檔' if same else '不是同一個檔'}")
    print("結論：可以用股票代號加年度批次抓。下一步：python src/download.py index --years 2023 2024 2025 --markets sii otc")
    return 0


def cmd_index(args):
    if args.year_offset:
        print("--year-offset 在 ESG InfoHub 用不到（年度就是報告涵蓋的年度），忽略。")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    lists = {mk: esg.company_list(MARKET_CODE[mk], API["base"]) for mk in args.markets}
    (RAW_DIR / "company_lists.json").write_text(json.dumps(lists, ensure_ascii=False), encoding="utf-8")
    current, ind_name = {}, {}
    for mk in sorted(args.markets, key=lambda m: m != "otc"):  # listed-market names win
        ind_name.update({i["industryCode"]: i["industryName"] for i in lists[mk]["industryNameList"]})
    for mk in args.markets:
        for c in lists[mk]["companyNameList"]:
            current[c["companyCode"]] = (MARKET_NAME[mk], c["industryCode"], c["companyName"].split(" ", 1)[-1])
    codes = sorted(current)
    rows, counts = [], {}
    for year in args.years:
        for mk in args.markets:
            data = esg.report_records(MARKET_CODE[mk], year, codes, API["base"])
            (RAW_DIR / f"{mk}_{year}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            counts[f"{year} {mk}"] = len(data)
            for x in data:
                file_id, label, uploaded = choose(x)
                market, ind, short = current.get(x["code"], (MARKET_NAME[mk], "", x.get("shortName", "")))
                rows.append({"firm_id": x["code"], "year": year, "firm_name": short, "firm_full_name": x.get("name", ""),
                             "market": market, "market_filed": MARKET_NAME[mk], "industry_code": ind,
                             "industry": ind_name.get(ind, ""), "reporting_interval": x.get("reportingInterval", ""),
                             "tw_status": x.get("twStatus"), "version": label, "file_id": file_id,
                             "upload_date": uploaded, "first_id": x.get("twFirstReportDownloadId", ""),
                             "first_date": x.get("twCommencementDate", ""),
                             "edit_id": x.get("twEditReportDownloadId") if esg.has_id(x.get("twEditReportDownloadId")) else "",
                             "edit_date": x.get("twRevisionDate", ""),
                             "en_first_id": x.get("enFirstReportDownloadId") if esg.has_id(x.get("enFirstReportDownloadId")) else "",
                             "url": esg.file_url(file_id, API["base"]) if esg.has_id(file_id) else ""})
    write_csv(INDEX_CSV, rows, INDEX_FIELDS)
    print("每個年度、市場的報告筆數：", "，".join(f"{k} {v}" for k, v in counts.items()))
    print(f"寫入 {INDEX_CSV}（{len(rows)} 筆）。下一步：python src/download.py sample --n-firms 150 --n-dev 30")
    return 0


def cmd_sample(args):
    cfg = config()
    seed, years = cfg["seed"], args.years
    must = [str(x) for x in args.include]
    if args.from_company_list:  # by-hand fallback: no check of which years exist
        lists = {mk: esg.company_list(MARKET_CODE[mk], API["base"]) for mk in ("sii", "otc")}
        frame, names = [], {}
        for mk in ("sii", "otc"):
            inds = {i["industryCode"]: i["industryName"] for i in lists[mk]["industryNameList"]}
            for c in lists[mk]["companyNameList"]:
                frame.append({"firm_id": c["companyCode"], "market": MARKET_NAME[mk],
                              "industry_code": c["industryCode"], "industry": inds.get(c["industryCode"], "")})
                names[c["companyCode"]] = c["companyName"].split(" ", 1)[-1]
        index = {}
        print(f"公司清單共 {len(frame)} 家；沒有先確認三個年度都有報告，缺年度的公司要跳過。")
    else:
        frame = read_csv(args.frame)
        if not INDEX_CSV.is_file():
            raise SystemExit(f"沒有 {INDEX_CSV}：先跑 python src/download.py index")
        index = {(r["firm_id"], int(r["year"])): r for r in read_csv(INDEX_CSV)}
        names = {r["firm_id"]: r["firm_name"] for r in index.values()}
        print(f"抽樣母體：{args.frame}（{len(frame)} 家）")
    _, seats, split = draw(frame, args.n_firms, args.n_dev, seed, must)
    info = {r["firm_id"]: r for r in frame}
    order = sorted(split) if not args.from_company_list else \
        sorted(split, key=lambda f: (f not in must, order_key(seed, "manual", f)))
    rows, missing = [], []
    for f in order:
        for y in years:
            rec = index.get((f, y))
            if rec is None and not args.from_company_list:
                missing.append(f"{f}_{y}")
            rows.append({"firm_id": f, "firm_name": names.get(f, ""), "market": info[f]["market"],
                         "industry": info[f]["industry"], "year": y, "url": rec["url"] if rec else "",
                         "source_label": (f"ESG InfoHub {rec['version']}（上傳 {rec['upload_date']}）" if rec
                                          else "manual: save by hand as file_path"),
                         "file_path": f"data/raw/{f}_{y}.pdf", "sha256": "", "pages": "",
                         "split": split[f], "download_date": ""})
    if CATALOG.is_file():
        old = read_csv(CATALOG)
        if {(r["firm_id"], r["split"]) for r in old} != {(r["firm_id"], r["split"]) for r in rows} and not args.force:
            raise SystemExit(f"{CATALOG} 裡的樣本和這次抽的不同；確定要換掉樣本請加 --force。")
        kept = {(r["firm_id"], r["year"]): r for r in old}
        for r in rows:  # keep downloads whose link did not change
            o = kept.get((r["firm_id"], str(r["year"])))
            if o and o.get("sha256") and o.get("url") == r["url"]:
                r.update(sha256=o["sha256"], pages=o.get("pages", ""), download_date=o.get("download_date", ""))
    write_csv(CATALOG, rows, CATALOG_FIELDS)
    print(f"抽出 {len(split)} 家（開發集 {sum(v == 'dev' for v in split.values())}、"
          f"測試集 {sum(v == 'test' for v in split.values())}），{sum(1 for v in seats.values() if v)} 個產業；"
          f"寫入 {CATALOG}（{len(rows)} 列）")
    if missing:
        print(f"警告：{len(missing)} 本在索引裡找不到下載連結：{missing[:10]}")
    return 0


def duplicate_years(rows):
    by_firm = defaultdict(dict)
    for r in rows:
        if r["sha256"]:
            by_firm[r["firm_id"]][r["year"]] = r["sha256"]
    out = []
    for f, ys in by_firm.items():
        seen = defaultdict(list)
        for y, h in ys.items():
            seen[h].append(y)
        out += [f"{f}：{'、'.join(sorted(v))} 是同一個檔" for v in seen.values() if len(v) > 1]
    return out


def write_fetch_report(path, checks, dups):
    e = html.escape
    body = "".join(
        f"<tr><td>{e(c['name'])}</td><td>{e(c['firm_name'])}</td><td>{e(c['split'])}</td><td>{c['mb']:.1f}</td>"
        f"<td>{c['pages']}</td><td>{e(c['label'])}</td><td>{c['check']['name']}</td><td>{c['check']['year']}</td>"
        f"<td>{e(c['check']['note'])}</td></tr>" for c in checks)
    doc = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>下載檢查</title>
<style>:root{{--bg:#fff;--fg:#1d1d1f;--line:#ddd}}@media (prefers-color-scheme: dark){{:root{{--bg:#161616;--fg:#eee;--line:#333}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;max-width:1000px;margin:0 auto;padding:16px}}
table{{border-collapse:collapse}}th,td{{border-bottom:1px solid var(--line);padding:4px 10px;text-align:left}}.wrap{{overflow-x:auto}}</style></head><body>
<h1>下載檢查（{len(checks)} 本）</h1>
<p>「公司名」「年份」：前三頁的文字有沒有出現公司簡稱或全名、報告年度（西元或民國）。只是提示，✗ 的要打開看一眼。</p>
<div class="wrap"><table><tr><th>報告</th><th>公司</th><th>分組</th><th>MB</th><th>頁數</th><th>版本</th><th>公司名</th><th>年份</th><th>備註</th></tr>{body}</table></div>
<h2>同一家公司不同年度是同一個檔</h2><p>{e('；'.join(dups)) if dups else '沒有'}</p></body></html>"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(doc, encoding="utf-8")


def cmd_fetch(args):
    must = [str(x) for x in config()["data"]["dev_must_include"]]
    rows = read_csv(CATALOG)
    full_names = {}
    if INDEX_CSV.is_file():
        full_names = {(r["firm_id"], r["year"]): r["firm_full_name"] for r in read_csv(INDEX_CSV)}
    order = sorted(range(len(rows)), key=lambda i: (rows[i]["split"] != "dev", rows[i]["firm_id"] not in must,
                                                    rows[i]["firm_id"], rows[i]["year"]))
    todo = [i for i in order if rows[i]["url"] and not (rows[i]["sha256"] and Path(rows[i]["file_path"]).is_file())]
    if args.firms:
        todo = [i for i in todo if rows[i]["firm_id"] in args.firms]
    print(f"要下載 {len(todo)} 本（全部 {len(rows)} 本；開發集先抓）")
    client, done, fails, checks = Polite(args.interval), 0, 0, []
    for i in todo:
        if args.limit and done >= args.limit:
            break
        r = rows[i]
        name = f"{r['firm_id']}_{r['year']}"
        try:
            data = client.get(r["url"]).content
            if data[:5] != b"%PDF-":
                raise RuntimeError(f"不是 PDF（{len(data)} bytes）")
        except Exception as e:
            fails += 1
            print(f"{name}: 失敗 {e}")
            if fails >= 5:
                print("連續 5 本失敗，先停：可能被擋或網址失效，先跑 probe。")
                break
            continue
        fails = 0
        path = Path(r["file_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        r.update(sha256=hashlib.sha256(data).hexdigest(), pages=pdf_pages(path),
                 download_date=date.today().isoformat())
        write_csv(CATALOG, rows, CATALOG_FIELDS)  # save progress after every file
        chk = sanity(path, r["year"], [r["firm_name"], full_names.get((r["firm_id"], r["year"]), "")])
        checks.append({"name": name, "firm_name": r["firm_name"], "split": r["split"], "mb": len(data) / 1e6,
                       "pages": r["pages"], "label": r["source_label"], "check": chk})
        done += 1
        print(f"{name}: {len(data) / 1e6:.1f} MB、{r['pages']} 頁｜公司名 {chk['name']}｜年份 {chk['year']} {chk['note']}")
    dups = duplicate_years(rows)
    if dups:
        print("警告：同一家公司不同年度是同一個檔（可能申報放錯年度）：", "；".join(dups))
    if checks:
        report = Path("reports") / f"fetch_{run_stamp()}.html"
        write_fetch_report(report, checks, dups)
        print(f"檢查表：{report}")
    have = Counter(r["firm_id"] for r in rows if r["sha256"])
    print(f"這次下載 {done} 本；三年都到手的公司 {sum(1 for v in have.values() if v >= 3)} 家；目錄已更新：{CATALOG}")
    return 0


def cmd_register(args):
    """Record PDFs that were saved by hand as file_path, and count complete firms."""
    rows = read_csv(CATALOG)
    added = 0
    for r in rows:
        path = Path(r["file_path"])
        if r["sha256"] or not path.is_file():
            continue
        data = path.read_bytes()
        if data[:5] != b"%PDF-":
            print(f"{path}: 不是 PDF，略過")
            continue
        r.update(sha256=hashlib.sha256(data).hexdigest(), pages=pdf_pages(path),
                 download_date=date.today().isoformat())
        added += 1
    write_csv(CATALOG, rows, CATALOG_FIELDS)
    years = {r["year"] for r in rows}
    have = defaultdict(set)
    for r in rows:
        if r["sha256"]:
            have[r["firm_id"]].add(r["year"])
    complete = [f for f in dict.fromkeys(r["firm_id"] for r in rows) if have[f] >= years]
    print(f"這次登記 {added} 本；{len(years)} 個年度都齊的公司：{len(complete)} 家")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=3.0, help="seconds between requests (at least 3)")
    ap.add_argument("--base", default=esg.BASE, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="one list query and one download")
    p.add_argument("--year", type=int, default=2025)
    p.add_argument("--market", choices=list(MARKET_CODE), default="sii")
    p.add_argument("--check-firm", default="1101")
    p = sub.add_parser("index", help="every firm's report record and download link")
    p.add_argument("--years", type=int, nargs="+", default=[2023, 2024, 2025])
    p.add_argument("--markets", nargs="+", choices=list(MARKET_CODE), default=["sii", "otc"])
    p.add_argument("--year-offset", type=int, default=0, help="ignored (kept for compatibility)")
    p = sub.add_parser("sample", help="stratified sample and dev/test split -> data/catalog.csv")
    p.add_argument("--n-firms", type=int, default=150)
    p.add_argument("--n-dev", type=int, default=30)
    p.add_argument("--years", type=int, nargs="+", default=[2023, 2024, 2025])
    p.add_argument("--include", nargs="+", default=["1101", "1707"])
    p.add_argument("--frame", default=str(FRAME), help="frozen population CSV")
    p.add_argument("--from-company-list", action="store_true", help="by-hand fallback (PLAN §4.1)")
    p.add_argument("--force", action="store_true", help="replace an existing, different sample")
    p = sub.add_parser("fetch", help="download the catalog's PDFs (dev firms first)")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--firms", nargs="+")
    sub.add_parser("register", help="record PDFs saved by hand")
    args = ap.parse_args()
    args.interval = max(3.0, args.interval)
    API["base"] = args.base
    sys.exit({"probe": cmd_probe, "index": cmd_index, "sample": cmd_sample, "fetch": cmd_fetch,
              "register": cmd_register}[args.cmd](args))


if __name__ == "__main__":
    main()
