"""Rebuild test (NEXT 10/22, D14): in a fresh virtual environment and an empty folder, follow release/README.md for a
few firms---download their reports from the catalog URLs (one request every 3 s), re-run the extraction---and compare
with the release: PDF sha256, extraction record count per report, and every record's geometry, crop name and hashes.

  python src/rebuild_test.py --firms 6499 1455 2102 2739 5201 --dir work/rebuild_test
  -> results/rebuild_test_{stamp}.json (pass/fail per report)
"""
import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp  # noqa: E402

COMPARE = ("page", "idx", "crop", "bbox", "layer_md5", "px_w", "px_h", "xref", "layers", "area_ratio", "full_page")


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def run(cmd, cwd, log):
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n$ {' '.join(map(str, cmd))}\n")
        r = subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        f.write(f"[exit {r.returncode}]\n")
    return r.returncode


def norm(rec):
    out = {}
    for k in COMPARE:
        v = rec.get(k)
        if k == "bbox" and v:
            v = [round(float(x), 1) for x in v]
        if k == "area_ratio" and v is not None:
            v = round(float(v), 3)
        out[k] = v
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--firms", nargs="+", required=True)
    ap.add_argument("--dir", default="work/rebuild_test")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--keep-venv", action="store_true", help="reuse an existing venv in --dir (saves the install)")
    args = ap.parse_args()
    release = ROOT / "release"
    if not (release / "README.md").exists():
        sys.exit("先跑 python src/make_release.py")
    # 10/28: the release keeps the catalog under data/ (the public repository runs in place); older releases had it at the root
    catalog = release / "data" / "catalog.csv" if (release / "data" / "catalog.csv").exists() else release / "catalog.csv"
    d = ROOT / args.dir
    venv = d / "venv"
    repo = d / "repo"
    if d.exists():
        for child in d.iterdir():
            if child == venv and args.keep_venv:
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    d.mkdir(parents=True, exist_ok=True)
    log = d / "rebuild_test.log"
    log.write_text("", encoding="utf-8")
    t0 = time.time()
    steps = {}
    # 1. a fresh environment with only what download and extraction import
    if not (venv / "Scripts" / "python.exe").exists():
        steps["venv"] = run([sys.executable, "-m", "venv", str(venv)], d, log)
        py = venv / "Scripts" / "python.exe"
        steps["pip"] = run([str(py), "-m", "pip", "install", "-q", "pymupdf==1.28.2", "pillow==13.0.0", "numpy", "pyyaml", "requests"], d, log)
    py = venv / "Scripts" / "python.exe"
    # 2. an empty folder with the release's code, configs and catalog
    shutil.copytree(release / "src", repo / "src")
    shutil.copytree(release / "configs", repo / "configs")
    (repo / "data").mkdir()
    shutil.copy(catalog, repo / "data" / "catalog.csv")
    cat = {f"{r['firm_id']}_{r['year']}": r for r in csv.DictReader(open(catalog, encoding="utf-8-sig", newline=""))}
    names = sorted(n for n, r in cat.items() if r["firm_id"] in args.firms)
    # 3. download (download.py keeps 3 s between requests) and 4. extract
    steps["download"] = run([str(py), "src/download.py", "fetch", "--firms", *args.firms], repo, log)
    steps["extract"] = run([str(py), "src/run_extract_all.py", "--catalog", "data/catalog.csv", "--out", "work/extract",
                            "--workers", str(args.workers), "--only", *names], repo, log)
    # 5. compare
    reports = []
    for n in names:
        r = cat[n]
        pdf = repo / "data" / "raw" / f"{n}.pdf"
        item = {"report": n, "pdf_downloaded": pdf.exists(), "sha256_matches": pdf.exists() and sha256(pdf) == r["sha256"]}
        rel = release / "extraction" / f"{n}_pairs.jsonl"
        new = repo / "work" / "extract" / f"{n}_pairs.jsonl"
        if rel.exists() and new.exists():
            A = [json.loads(l) for l in open(rel, encoding="utf-8") if l.strip()]
            B = [json.loads(l) for l in open(new, encoding="utf-8") if l.strip()]
            item.update(records_release=len(A), records_rebuilt=len(B))
            ka = {(a["page"], a["idx"]): norm(a) for a in A}
            kb = {(b["page"], b["idx"]): norm(b) for b in B}
            diff = [k for k in ka if ka[k] != kb.get(k)] + [k for k in kb if k not in ka]
            item.update(records_differ=len(diff), first_differences=[list(k) for k in diff[:5]])
            crops = sum(1 for p in (repo / "work" / "extract" / "crops").glob(f"{n}_*.jpg")) if (repo / "work" / "extract" / "crops").exists() else 0
            item.update(crops_rebuilt=crops)
            item["pass"] = bool(item["sha256_matches"] and len(A) == len(B) and not diff)
        else:
            item.update(records_release=rel.exists(), records_rebuilt=new.exists(), **{"pass": False})
        reports.append(item)
    out = {"meta": run_meta("configs/default.yaml", [str(catalog)]), "firms": args.firms, "reports": reports, "steps": steps,
           "all_pass": all(x["pass"] for x in reports), "seconds": round(time.time() - t0),
           "note": "PDFs re-downloaded from the catalog URLs into an empty folder with a fresh venv; extraction re-run with the "
                   "release's code; records compared on " + ", ".join(COMPARE) + "."}
    path = ROOT / "results" / f"rebuild_test_{run_stamp()}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for x in reports:
        print(x["report"], "PASS" if x["pass"] else "FAIL", {k: x[k] for k in x if k not in ("report", "pass", "first_differences")})
    print("all_pass", out["all_pass"], "| wrote", path)


if __name__ == "__main__":
    main()
