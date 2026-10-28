"""Run extract_pairs on every report listed in the catalog, several reports at a time.

Usage (from the repository root):
  python src/run_extract_all.py --catalog data/catalog.csv --out work/extract --workers 8

The catalog needs the columns firm_id, year, file_path. Each report is named
{firm_id}_{year} (e.g. 1101_2025). A report whose {name}_pairs.jsonl already exists is
skipped, so an interrupted run can simply be started again.

One row per report is written to {out}/_runs/extract_{timestamp}.csv:
name, pages, placements, pictures (after merging layers), distinct, layout_repeat
groups, analysed (distinct minus layout repeats), seconds, error.
"""
import argparse
import csv
import multiprocessing as mp
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_pairs as ep  # noqa: E402

FIELDS = ["name", "pages", "placements", "pictures", "distinct", "layout_groups",
          "analysed", "seconds", "error"]


def run_one(pdf_path, name, out_dir, crop_long_side, jpg_quality):
    t0 = time.time()
    row = {"name": name}
    try:
        records, n_pages, _ = ep.extract_report(pdf_path, name, out_dir, crop_long_side, jpg_quality)
        reps = [r for r in records if r["representative"]]
        layout = [r for r in reps if r["doc_role"] == "layout_repeat"]
        row.update(pages=n_pages, placements=sum(len(r["layers"]) for r in records),
                   pictures=len(records), distinct=len(reps), layout_groups=len(layout),
                   analysed=len(reps) - len(layout), error="")
    except Exception as e:  # keep going; the error is listed in the run log
        row["error"] = repr(e)[:300]
    row["seconds"] = round(time.time() - t0, 1)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=max(1, (mp.cpu_count() or 2) - 1))
    ap.add_argument("--crop-long-side", type=int, default=ep.CROP_LONG_SIDE)
    ap.add_argument("--jpg-quality", type=int, default=ep.JPG_QUALITY)
    ap.add_argument("--only", nargs="*", help="run only these names, e.g. 1101_2025")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    todo, missing, done = [], [], 0
    with open(args.catalog, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = f"{row['firm_id'].strip()}_{row['year'].strip()}"
            if args.only and name not in args.only:
                continue
            pdf = Path(row.get("file_path", "").strip())
            if not row.get("file_path", "").strip() or not pdf.is_file():
                missing.append(name)
                continue
            if (out_dir / f"{name}_pairs.jsonl").exists():
                done += 1
                continue
            todo.append((str(pdf), name))
    print(f"{len(todo)} to run, {done} already done, {len(missing)} without a PDF file")
    if missing:
        print("  missing:", " ".join(missing[:20]) + (" ..." if len(missing) > 20 else ""))

    run_dir = out_dir / "_runs"
    run_dir.mkdir(exist_ok=True)
    log_path = run_dir / f"extract_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    n_err = 0
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8", newline="") as logf:
        writer = csv.DictWriter(logf, fieldnames=FIELDS)
        writer.writeheader()
        ctx = mp.get_context("spawn")  # same behaviour on Linux, macOS and Windows
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as pool:
            futures = [pool.submit(run_one, pdf, name, str(out_dir), args.crop_long_side,
                                   args.jpg_quality) for pdf, name in todo]
            for i, fut in enumerate(as_completed(futures), 1):
                row = fut.result()
                writer.writerow(row)
                logf.flush()
                n_err += bool(row["error"])
                status = f"ERROR {row['error']}" if row["error"] else \
                    f"analysed={row['analysed']} ({row['seconds']} s)"
                print(f"[{i}/{len(todo)}] {row['name']}: {status}", flush=True)
    print(f"finished in {time.time() - t0:.0f} s, {n_err} errors; log: {log_path}")


if __name__ == "__main__":
    main()
