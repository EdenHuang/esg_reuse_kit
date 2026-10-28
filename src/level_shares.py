"""The annotators' estimate of the four evidence levels (and the three depiction levels) on a split's annotated R
photos: shares with firm-level bootstrap intervals (2,000 resamples, seed 2026). On the test split this is the human
estimate of Section 7 (the tool's corpus-wide shares come from analyze.py). Needs configs/frozen.yaml for test.

  python src/level_shares.py --split test
  -> results/level_shares_{split}_{stamp}.json
"""
import argparse
import collections
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from evaluate import SEED, ci, gold_of  # noqa: E402
from meta import run_meta, run_stamp  # noqa: E402

LEVELS4 = ("證據", "示意", "包裝", "無關")
LEVELS3 = ("direct", "topical", "unrelated")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True, choices=["dev", "test"])
    ap.add_argument("--boot", type=int, default=2000)
    args = ap.parse_args()
    if args.split == "test" and not (ROOT / "configs" / "frozen.yaml").exists():
        sys.exit("測試集要等 configs/frozen.yaml（鐵則 3）")
    path = ROOT / "annotations" / args.split / "R.jsonl"
    items = {json.loads(l)["item"]: json.loads(l) for l in open(ROOT / "annotations" / args.split / "R_items.jsonl", encoding="utf-8") if l.strip()}
    rows = {}
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("status", "done") == "done" and not r.get("not_photo") and r.get("level"):
            rows[r["item"]] = gold_of(r)
    firm_of = {i: items[i]["firm"] for i in rows}
    firms = sorted(set(firm_of.values()))
    by_firm = collections.defaultdict(list)
    for i, g in rows.items():
        by_firm[firm_of[i]].append(g)

    def shares(gs):
        n = len(gs)
        c4 = collections.Counter(g["level4"] for g in gs)
        c3 = collections.Counter(g["level3"] for g in gs)
        return {**{lv: c4[lv] / n for lv in LEVELS4}, **{lv: c3[lv] / n for lv in LEVELS3}, "anchored": sum(g["anchored"] for g in gs) / n}

    point = shares(list(rows.values()))
    rng = random.Random(SEED)
    boots = []
    for _ in range(args.boot):
        draw = [rng.choice(firms) for _ in firms]
        boots.append(shares([g for f in draw for g in by_firm[f]]))
    res = {"meta": run_meta("configs/default.yaml", [str(path)]), "split": args.split, "photos": len(rows), "firms": len(firms),
           "counts4": dict(collections.Counter(g["level4"] for g in rows.values())),
           "counts3": dict(collections.Counter(g["level3"] for g in rows.values())),
           "anchored": sum(g["anchored"] for g in rows.values()),
           "shares_percent": {k: round(100 * v, 1) for k, v in point.items()},
           "ci_percent": {k: [round(100 * x, 1) for x in ci([b[k] for b in boots])] for k in point},
           "bootstrap": {"resamples": args.boot, "unit": "firm", "seed": SEED, "interval": "95% percentile"},
           "note": "annotators' labels only (R.jsonl via evaluate.gold_of); the sample is the simple random sample of filtered photos behind annotation R"}
    out = ROOT / "results" / f"level_shares_{args.split}_{run_stamp()}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(args.split, len(rows), "photos |", {k: f"{res['shares_percent'][k]} {res['ci_percent'][k]}" for k in LEVELS4}, "| anchored", res["shares_percent"]["anchored"], "| wrote", out)


if __name__ == "__main__":
    main()
