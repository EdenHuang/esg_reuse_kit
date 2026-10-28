"""Annotator agreement R (Eden) vs R2 (a second annotator) on the test photos, from the two annotation files only
(PLAN §7 checkpoint 11/2: weighted κ on the three levels >= 0.6 before configs/frozen.yaml is written). No model
output is read, no label distribution is printed (CLAUDE.md rule 3): κ, its firm-level bootstrap interval, the number
of photos that agree, and the anchoring κ.

  python src/agreement_r2.py                 # annotations/test/R.jsonl vs annotations/test/R2.jsonl
  -> results/annotation_R2_agreement_test_{stamp}.json
"""
import argparse
import collections
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from evaluate import SEED, ci, gold_of, kappa_w, r4  # noqa: E402
from meta import run_meta, run_stamp  # noqa: E402


def load(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("status", "done") == "done" and not r.get("not_photo") and r.get("level"):
            out[r["item"]] = gold_of(r)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="test", choices=["dev", "test"])
    ap.add_argument("--second", default=None, help="default annotations/{split}/R2.jsonl")
    ap.add_argument("--boot", type=int, default=2000)
    args = ap.parse_args()
    first = ROOT / "annotations" / args.split / "R.jsonl"
    second = Path(args.second) if args.second else ROOT / "annotations" / args.split / "R2.jsonl"
    a, b = load(first), load(second)
    items = {json.loads(l)["item"]: json.loads(l) for l in open(ROOT / "annotations" / args.split / "R_items.jsonl", encoding="utf-8") if l.strip()}
    both = sorted(i for i in b if i in a)
    skipped = {"second_only_or_not_photo_in_first": len([i for i in b if i not in a]),
               "second_file_rows_not_usable": sum(1 for l in open(second, encoding="utf-8") if l.strip()) - len(b)}
    pairs3 = [(a[i]["level3"], b[i]["level3"]) for i in both]
    anc = [(a[i]["anchored"], b[i]["anchored"]) for i in both]
    firm_of = {i: items[i]["firm"] for i in both}
    firms = sorted(set(firm_of.values()))
    rng = random.Random(SEED)
    kb = []
    for _ in range(args.boot):
        draw = [rng.choice(firms) for _ in firms]
        pp = [(a[i]["level3"], b[i]["level3"]) for f in draw for i in both if firm_of[i] == f]
        kb.append(kappa_w(pp))
    po = sum(x == y for x, y in anc) / len(anc)
    pa, pb = sum(x for x, _ in anc) / len(anc), sum(y for _, y in anc) / len(anc)
    pe = pa * pb + (1 - pa) * (1 - pb)
    # four levels: plain (unweighted) agreement and Cohen's κ, the levels are not ordered the same way
    p4 = [(a[i]["level4"], b[i]["level4"]) for i in both]
    po4 = sum(x == y for x, y in p4) / len(p4)
    ca, cb = collections.Counter(x for x, _ in p4), collections.Counter(y for _, y in p4)
    pe4 = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (len(p4) ** 2)
    res = {"meta": run_meta("configs/default.yaml", [str(first), str(second)]), "split": args.split,
           "files": {"first": str(first), "second": str(second)}, "photos": len(both), "firms": len(firms), "skipped": skipped,
           "three_levels": {"kappa_w": r4(kappa_w(pairs3)), "kappa_w_ci": ci(kb), "same": sum(x == y for x, y in pairs3),
                            "bootstrap": {"resamples": args.boot, "unit": "firm", "seed": SEED}},
           "anchoring": {"same": sum(x == y for x, y in anc), "kappa": r4((po - pe) / (1 - pe)) if pe < 1 else None},
           "four_levels": {"same": sum(x == y for x, y in p4), "kappa": r4((po4 - pe4) / (1 - pe4)) if pe4 < 1 else None},
           "checkpoint": {"rule": "PLAN §7 11/2: weighted κ on the three levels >= 0.6", "passed": kappa_w(pairs3) >= 0.6},
           "note": "annotation files only; no model output; label distributions deliberately not written (rule 3)"}
    out = ROOT / "results" / f"annotation_R2_agreement_{args.split}_{run_stamp()}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    t = res["three_levels"]
    print(f"{len(both)} photos, {len(firms)} firms | three-level weighted κ {t['kappa_w']} {t['kappa_w_ci']} (same {t['same']}) | "
          f"anchoring κ {res['anchoring']['kappa']} (same {res['anchoring']['same']}) | four-level κ {res['four_levels']['kappa']} (same {res['four_levels']['same']}) | "
          f"checkpoint passed: {res['checkpoint']['passed']} | wrote {out}")


if __name__ == "__main__":
    main()
