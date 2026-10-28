"""Annotation B (two annotators, development split): the precision of each reuse / stock method on the pairs it
picked (annotations/dev/B_items.jsonl `picked_by`; the pairs were sampled per method). Truth = the pairs both
annotators labelled the same way (disagreements are counted and left out). Table 7's [B].

  python src/eval_b.py
  -> results/annotation_B_precision_dev_{stamp}.json
"""
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp  # noqa: E402


def rj(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def main():
    items = {r["item"]: r for r in rj(ROOT / "annotations/dev/B_items.jsonl")}
    labels = collections.defaultdict(dict)
    for r in rj(ROOT / "annotations/dev/B.jsonl"):
        labels[r["item"]][r["annotator"]] = r["answer"] == "Yes"
    both = {i: v for i, v in labels.items() if "a1" in v and "a2" in v}
    agreed = {i: v["a1"] for i, v in both.items() if v["a1"] == v["a2"]}
    out = {"pairs_annotated": len(both), "pairs_agreed": len(agreed), "disagreements": len(both) - len(agreed), "by_kind": {}}
    for kind in ("reuse", "stock"):
        per = {}
        methods = sorted({m for i in agreed for m in items[i]["picked_by"] if items[i]["kind"] == kind})
        for m in methods:
            picked = [i for i in agreed if items[i]["kind"] == kind and m in items[i]["picked_by"]]
            yes = sum(agreed[i] for i in picked)
            per[m] = {"pairs": len(picked), "same_photo": yes, "precision": round(yes / len(picked), 4) if picked else None}
        n_kind = [i for i in agreed if items[i]["kind"] == kind]
        out["by_kind"][kind] = {"pairs_agreed": len(n_kind), "same_photo": sum(agreed[i] for i in n_kind), "methods": per,
                                "sampled_for": dict(collections.Counter(s for i in n_kind for s in items[i]["sampled_for"]))}
    out["note"] = ("precision = share of a method's picked pairs (picked_by) that both annotators called the same photo; "
                   "md5 = byte-identical file, phash = hash distance, dinov2 / sscd = descriptor top-k, ours = LightGlue + MAGSAC verified")
    out["meta"] = run_meta("configs/default.yaml", [str(ROOT / "annotations/dev/B.jsonl"), str(ROOT / "annotations/dev/B_items.jsonl")])
    path = ROOT / "results" / f"annotation_B_precision_dev_{run_stamp()}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for kind, v in out["by_kind"].items():
        print(kind, {m: (x["precision"], x["pairs"]) for m, x in v["methods"].items()})
    print("wrote", path)


if __name__ == "__main__":
    main()
