"""Annotation C against the reuse detector on the test split (cloud 10/27): the detector's precision on the C photos
whose first candidate it verified (LightGlue + MAGSAC `same` in work/reuse/test_candidates.jsonl), and how many of the
annotator's confirmed first choices the detector verified. Needs reuse.py --split test (after configs/frozen.yaml).

  python src/eval_c_verified.py
  -> results/annotation_C_verified_test_{stamp}.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp  # noqa: E402


def rj(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def main():
    if not (ROOT / "configs" / "frozen.yaml").exists():
        sys.exit("測試集要等 configs/frozen.yaml（鐵則 3）")
    cand_path = ROOT / "work" / "reuse" / "test_candidates.jsonl"
    if not cand_path.exists():
        sys.exit("先跑 python src/reuse.py --split test")
    items = {r["item"]: r for r in rj(ROOT / "annotations/test/C_items.jsonl")}
    C = rj(ROOT / "annotations/test/C.jsonl")
    verified = {}
    for r in rj(cand_path):
        verified[(r["query"], r["candidate"])] = r
    rows = []
    for a in C:
        q = items[a["item"]]["query"]
        v = verified.get((q, a["hint"]))
        rows.append({"item": a["item"], "query": q, "hint": a["hint"], "annotator_yes": a["hint_answer"] == "Yes",
                     "in_candidates": v is not None, "detector_same": bool(v and v["same"]),
                     "inliers": v["inliers"] if v else None, "coverage": v["coverage"] if v else None})
    n = len(rows)
    det = [r for r in rows if r["detector_same"]]
    yes = [r for r in rows if r["annotator_yes"]]
    both = [r for r in rows if r["detector_same"] and r["annotator_yes"]]
    res = {"meta": run_meta("configs/default.yaml", [str(cand_path), str(ROOT / "annotations/test/C.jsonl")]), "split": "test",
           "photos": n, "hint_pairs_in_candidates": sum(r["in_candidates"] for r in rows),
           "annotator_first_choice_same": len(yes), "detector_verified_first_choice": len(det), "both": len(both),
           "detector_precision_on_verified": round(len(both) / len(det), 4) if det else None,
           "annotator_yes_verified_by_detector": round(len(both) / len(yes), 4) if yes else None,
           "detector_only": [r["item"] for r in det if not r["annotator_yes"]],
           "annotator_only": [r["item"] for r in yes if not r["detector_same"]],
           "note": "first choice = SSCD top-1 of the previous year (the C hint); detector = frozen LightGlue + MAGSAC thresholds"}
    out = ROOT / "results" / f"annotation_C_verified_test_{run_stamp()}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print({k: res[k] for k in ("photos", "hint_pairs_in_candidates", "annotator_first_choice_same", "detector_verified_first_choice", "both",
                               "detector_precision_on_verified", "annotator_yes_verified_by_detector")}, "| wrote", out)


if __name__ == "__main__":
    main()
