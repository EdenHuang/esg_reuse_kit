"""configs/frozen.yaml (PLAN §7, 11/2): what is frozen, and the check every script runs before it touches the test
split (CLAUDE.md rule 3).

The frozen file holds a snapshot of the settings that decide a test result: the evidence models and revisions, the
measurement tool (configs/default.yaml `measure`), the photo filter and reuse thresholds, the hash of every prompt
file, the E3 examples (development photos, fixed), and the thresholds each compared method chose on the whole
development set (tune_evidence.py result files). `check(split, cfg)` stops a test run when the file is missing or
when the current settings differ from it; development runs are never stopped.

  python src/frozen.py show                     # what would be frozen now (nothing is written)
  python src/frozen.py write --tune results/tune_evidence_dev_....json [...]   # 11/2, with Eden's go-ahead
"""
import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FROZEN = ROOT / "configs" / "frozen.yaml"
KEYS = {"evidence": ("llm", "llm_revision", "vlm", "vlm_revision", "vlm_max_pixels", "cnclip", "cnclip_revision",
                     "context_max_chars", "ocr_match_threshold", "logo_min_inliers"),
        "measure": None,   # all of it
        "photo_filter": ("photo_threshold", "vlm_threshold"),
        "reuse": ("min_inliers", "min_coverage", "topk", "stock_topk_other_firms"),
        "logo_refs": ("ocr_min_score", "ocr_lang")}


def sha_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def snapshot(cfg):
    out = {}
    for sec, keys in KEYS.items():
        d = cfg.get(sec) or {}
        out[sec] = dict(d) if keys is None else {k: d.get(k) for k in keys}
    out["prompts_sha256"] = {p.stem: sha_file(p) for p in sorted((ROOT / "configs" / "prompts").glob("*.txt"))}
    return out


def load():
    return yaml.safe_load(FROZEN.read_text(encoding="utf-8")) if FROZEN.exists() else None


def diff(frozen, cur):
    out = []
    for sec, d in cur.items():
        f = frozen.get(sec) or {}
        for k, v in d.items():
            if f.get(k) != v:
                out.append(f"{sec}.{k}: 凍結 {f.get(k)!r}，現在 {v!r}")
    return out


def check(split, cfg):
    """Stop unless the test split may be used with these settings (CLAUDE.md rule 3)."""
    if split != "test":
        return None
    fr = load()
    if fr is None:
        sys.exit("測試集要等 configs/frozen.yaml（鐵則 3）")
    d = diff(fr, snapshot(cfg))
    if d:
        sys.exit("設定跟 configs/frozen.yaml 不一樣，不能跑測試集：\n  " + "\n  ".join(d[:20]))
    return fr


def chosen_thresholds(tune_files):
    """{context: {method: chosen}} from tune_evidence.py result files (development split, all photos)."""
    out = {}
    for f in tune_files:
        r = json.loads(Path(f).read_text(encoding="utf-8"))
        ctx = out.setdefault(r["context"], {})
        for name, m in r["methods"].items():
            if "chosen" in m:
                ctx[name] = m["chosen"]
        ctx["_source"] = str(Path(f).relative_to(ROOT)) if Path(f).is_absolute() else str(f)
    return out


def e3_fixed_examples(cfg):
    """The E3 examples for the test split: per level, the first photo of baselines_evidence.e3_examples' pools
    (development labels, the measurement tool's context), whatever the query's firm."""
    sys.path.insert(0, str(ROOT / "src"))
    import baselines_evidence as be
    import csv
    items = [json.loads(l) for l in open(ROOT / "annotations" / "dev" / "R_items.jsonl", encoding="utf-8")]
    lab = {json.loads(l)["item"]: json.loads(l) for l in open(ROOT / "annotations" / "dev" / "R.jsonl", encoding="utf-8")}
    items = [it for it in items if it["item"] in lab and not lab[it["item"]].get("not_photo") and lab[it["item"]].get("level")]
    crops = [it["crop"] for it in items]
    filt = {}
    with open(ROOT / "work" / "photo_filter.csv", encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if d["crop"] in set(crops):
                filt[d["crop"]] = d
    recs = {}
    for rep in sorted({filt[c]["report"] for c in crops}):
        for line in open(ROOT / "work" / "extract" / f"{rep}_pairs.jsonl", encoding="utf-8"):
            d = json.loads(line)
            if d["crop"] in filt:
                recs[d["crop"]] = d
    ctx = be.load_contexts(cfg["measure"].get("context", "rules"), crops, recs, cfg["evidence"]["context_max_chars"])
    rows = [{"crop": c, "firm": filt[c]["firm_id"], "parts": ctx[c][2]} for c in crops]
    by = {it["crop"]: lab[it["item"]]["level"] for it in items}
    pick = be.e3_examples(rows, by, int(cfg["measure"].get("shots_per_level", 1)))
    return [r["crop"] for r in pick({"crop": "", "firm": ""})]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    w = sub.add_parser("write")
    w.add_argument("--tune", nargs="+", required=True, help="tune_evidence.py result files whose chosen thresholds are frozen")
    w.add_argument("--note", default="")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))
    snap = snapshot(cfg)
    snap["measure"]["e3_examples"] = e3_fixed_examples(cfg)
    if args.cmd == "show":
        print(yaml.safe_dump(snap, allow_unicode=True, sort_keys=False))
        return
    if FROZEN.exists():
        sys.exit(f"{FROZEN} 已經存在，不覆蓋")
    snap = {"frozen_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "note": args.note, **snap,
            "thresholds": chosen_thresholds(args.tune)}
    FROZEN.write_text(yaml.safe_dump(snap, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"wrote {FROZEN}")


if __name__ == "__main__":
    main()
