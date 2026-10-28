"""Test-split numbers that come from the annotations alone (after configs/frozen.yaml): the photo filter's accuracy on
annotation A (Table 2), the two A annotators' agreement (Table 3), annotation D's missed and wrong photo boxes (4.3),
and annotation C's verified first choices (6.6). Nothing here reads a model score chosen on this split; the photo
filter's decisions were fixed before the test annotations existed (configs/frozen.yaml photo_filter).

  python src/eval_annotations_test.py
  -> results/annotation_test_summary_{stamp}.json
"""
import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import frozen  # noqa: E402
import yaml  # noqa: E402
from meta import run_meta, run_stamp  # noqa: E402


def rj(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def kappa(pairs):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    ca, cb = collections.Counter(a for a, _ in pairs), collections.Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else float("nan"), po


def main():
    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))
    fr = frozen.check("test", cfg)
    if not fr:
        sys.exit("沒有 configs/frozen.yaml（鐵則 3）")
    out = {"split": "test"}

    # ---- A: two annotators on 200 random test images; the filter's decision from work/photo_filter.csv
    A = rj(ROOT / "annotations/test/A.jsonl")
    items = {r["item"]: r for r in rj(ROOT / "annotations/test/A_items.jsonl")}
    by = collections.defaultdict(dict)
    for a in A:
        by[a["item"]][a["annotator"]] = a["answer"] == "Photo"
    both = {i: v for i, v in by.items() if "a1" in v and "a2" in v}
    pairs = [(v["a1"], v["a2"]) for v in both.values()]
    k, po = kappa(pairs)
    agree = {i: v["a1"] for i, v in both.items() if v["a1"] == v["a2"]}
    pf = {}
    crops = {items[i]["crop"] for i in both}
    with open(ROOT / "work" / "photo_filter.csv", encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if d["crop"] in crops:
                pf[d["crop"]] = d
    def acc(label_of):
        tp = fp = fn = tn = 0
        for i, lab in label_of.items():
            pred = pf[items[i]["crop"]]["is_photo"] == "1"
            tp += pred and lab; fp += pred and not lab; fn += (not pred) and lab; tn += (not pred) and not lab
        n = tp + fp + fn + tn
        return {"images": n, "accuracy": round((tp + tn) / n, 4), "precision_photo": round(tp / (tp + fp), 4) if tp + fp else None,
                "recall_photo": round(tp / (tp + fn), 4) if tp + fn else None, "tp": tp, "fp": fp, "fn": fn, "tn": tn}
    out["A"] = {"images": len(both), "annotators": ["a1", "a2"], "same": sum(a == b for a, b in pairs), "percent_agreement": round(po, 4),
                "kappa": round(k, 4), "photos_a1": sum(v["a1"] for v in both.values()), "photos_a2": sum(v["a2"] for v in both.values()),
                "filter": {"thresholds": fr["photo_filter"], "rule": "OpenCLIP photo score >= photo_threshold or VLM p >= vlm_threshold (work/photo_filter.csv is_photo)",
                           "on_agreed_images": acc(agree), "vs_a1": acc({i: v["a1"] for i, v in both.items()}), "vs_a2": acc({i: v["a2"] for i, v in both.items()})},
                "sampling": dict(collections.Counter(items[i]["stratum"] for i in both))}

    # ---- D: 200 test pages; drawn = a missed photo; flags on existing boxes = wrong boxes
    D = rj(ROOT / "annotations/test/D.jsonl")
    flags = collections.Counter(v for r in D for v in r["boxes"].values())
    out["D"] = {"pages": len(D), "pages_without_photos": sum(1 for r in D if r["no_photos"]),
                "missed_photos_drawn": sum(len(r["drawn"]) for r in D), "pages_with_a_missed_photo": sum(1 for r in D if r["drawn"]),
                "wrong_boxes": dict(flags), "wrong_boxes_total": sum(flags.values()), "pages_with_a_wrong_box": sum(1 for r in D if r["boxes"]),
                "checks_void": len(rj(ROOT / "annotations/qc/D_checks_void.jsonl"))}

    # ---- C: 200 test photos, the retrieval's first choice verified
    C = rj(ROOT / "annotations/test/C.jsonl")
    yes = sum(1 for r in C if r["hint_answer"] == "Yes")
    out["C"] = {"photos": len(C), "first_choice_same_photo": yes, "first_choice_precision": round(yes / len(C), 4),
                "verified_reuse_share_percent": round(100 * yes / len(C), 1), "other_candidates_clicked": sum(len(r["selected"]) for r in C),
                "hint": "SSCD top-1 of the previous year's report (annotate_bcd.py c-pool)", "checks_void": len(rj(ROOT / "annotations/qc/C_checks_void.jsonl"))}

    out["meta"] = run_meta("configs/default.yaml", [str(ROOT / p) for p in ("annotations/test/A.jsonl", "annotations/test/D.jsonl", "annotations/test/C.jsonl", "work/photo_filter.csv")])
    path = ROOT / "results" / f"annotation_test_summary_{run_stamp()}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    a = out["A"]
    print(f"A: {a['images']} images, same {a['same']}, κ {a['kappa']}; filter accuracy on agreed {a['filter']['on_agreed_images']['accuracy']} "
          f"({a['filter']['on_agreed_images']['images']} images; P {a['filter']['on_agreed_images']['precision_photo']}, R {a['filter']['on_agreed_images']['recall_photo']})")
    print("D:", {k: v for k, v in out["D"].items() if k != "wrong_boxes"}, out["D"]["wrong_boxes"])
    print("C:", out["C"])
    print("wrote", path)


if __name__ == "__main__":
    main()
