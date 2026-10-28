"""Assemble the public release folder (NEXT 10/22, D13; PLAN D8: no report images, nothing that cannot be rebuilt).

  python src/make_release.py            # -> release/ (gitignored) + release/MANIFEST.json + results/release_{stamp}.json

Contents: the files of release_meta/ (README.md, LICENSE, LICENSE-DATA, CITATION.cff, .zenodo.json, .gitignore),
data/ (catalog.csv with URLs, sha256, pages, split; the sampling frame, firm names, reports_index.csv and the ESG
InfoHub company list that the sampling and the name matching read), configs/ (default.yaml, prompts, frozen.yaml when
it exists), src/, requirements.txt, annotations/ (dev, test, qc, guidelines, logo review CSVs), results/,
photos/photo_filter.csv, extraction/{report}_pairs.jsonl (geometry and hashes; caption and overlay text removed),
page_structure/{report}.jsonl (box geometry, roles, containers, reading order; box text removed). The text is rebuilt
locally from the PDFs with the same code.

10/28 (public repository): release/ is the working tree of the public git repository, so a rebuild clears everything
in it except .git; the data files moved from the root to data/ so the code runs in place (rebuild_test.py reads either).
"""
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp  # noqa: E402

OUT = ROOT / "release"
META = ROOT / "release_meta"
PAIR_TEXT_KEYS = {"caption", "overlay_text", "local_heading", "heading", "paragraphs", "body", "context", "text"}
DATA_FILES = ["data/catalog.csv", "data/population_2023_2025_20261018.csv", "data/firm_names_20261018.csv",
              "data/index/reports_index.csv", "data/index/raw/company_lists.json"]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def strip_pairs(src, dst):
    n = 0
    with open(src, encoding="utf-8") as f, open(dst, "w", encoding="utf-8", newline="\n") as g:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            r = {k: v for k, v in r.items() if k not in PAIR_TEXT_KEYS and not (isinstance(v, str) and len(v) > 64)}
            g.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def strip_page(src, dst):
    n = 0
    with open(src, encoding="utf-8") as f, open(dst, "w", encoding="utf-8", newline="\n") as g:
        for line in f:
            if not line.strip():
                continue
            p = json.loads(line)
            for b in p.get("boxes", []):
                b.pop("text", None)
            p = {k: v for k, v in p.items() if not (isinstance(v, str) and len(v) > 64)}
            g.write(json.dumps(p, ensure_ascii=False) + "\n")
            n += 1
    return n



def main():
    meta_files = sorted(p for p in META.iterdir() if p.is_file()) if META.is_dir() else []
    if not meta_files:
        sys.exit(f"{META} is empty: README.md, LICENSE, LICENSE-DATA, CITATION.cff, .zenodo.json, .gitignore go there")
    if OUT.exists():
        for p in OUT.iterdir():  # keep release/.git: the public repository's history
            if p.name == ".git":
                continue
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    (OUT / "configs").mkdir(parents=True, exist_ok=True)
    for rel in DATA_FILES:
        (OUT / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, OUT / rel)
    for p in meta_files:
        shutil.copy(p, OUT / p.name)
    shutil.copy(ROOT / "configs" / "default.yaml", OUT / "configs" / "default.yaml")
    shutil.copytree(ROOT / "configs" / "prompts", OUT / "configs" / "prompts")
    if (ROOT / "configs" / "frozen.yaml").exists():
        shutil.copy(ROOT / "configs" / "frozen.yaml", OUT / "configs" / "frozen.yaml")
    shutil.copytree(ROOT / "src", OUT / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "requirements.txt", OUT / "requirements.txt")
    shutil.copytree(ROOT / "annotations", OUT / "annotations", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "results", OUT / "results", ignore=shutil.ignore_patterns("*.html"))
    (OUT / "photos").mkdir()
    shutil.copy(ROOT / "work" / "photo_filter.csv", OUT / "photos" / "photo_filter.csv")
    (OUT / "extraction").mkdir()
    (OUT / "page_structure").mkdir()
    counts = {"extraction_records": 0, "extraction_files": 0, "page_structure_pages": 0, "page_structure_files": 0}
    for p in sorted((ROOT / "work" / "extract").glob("*_pairs.jsonl")):
        counts["extraction_records"] += strip_pairs(p, OUT / "extraction" / p.name)
        counts["extraction_files"] += 1
    for p in sorted((ROOT / "work" / "page_graph").glob("*.jsonl")):
        counts["page_structure_pages"] += strip_page(p, OUT / "page_structure" / p.name)
        counts["page_structure_files"] += 1
    manifest = []
    total = 0
    for p in sorted(OUT.rglob("*")):
        if ".git" in p.relative_to(OUT).parts:
            continue
        if p.is_file() and p.name != "MANIFEST.json":
            size = p.stat().st_size
            total += size
            manifest.append({"path": str(p.relative_to(OUT)).replace("\\", "/"), "bytes": size, "sha256": sha256(p)})
    (OUT / "MANIFEST.json").write_text(json.dumps({"files": manifest, "total_bytes": total}, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {"release_dir": str(OUT.relative_to(ROOT)), "files": len(manifest), "total_mb": round(total / 1e6, 1), **counts,
               "text_removed": sorted(PAIR_TEXT_KEYS), "data_files": DATA_FILES, "meta_files": [p.name for p in meta_files]}
    stamp = run_stamp()
    out = ROOT / "results" / f"release_{stamp}.json"
    out.write_text(json.dumps({"meta": run_meta("configs/default.yaml", [str(ROOT / "data" / "catalog.csv")]), **summary},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print("wrote", out)


if __name__ == "__main__":
    main()
