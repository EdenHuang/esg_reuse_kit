"""Two anchoring checks from the measurement tool's output and annotation R (replaces the 10/22 one-off scripts):
  overlay   on the labelled photos: how often a post-production overlay (annotation R, 後製疊字) could have given the
            tool a false OCR anchor (overlay ticked, tool anchored by OCR, annotator did not anchor by text);
  anchored  on every photo of the split: the anchored share and its split over the four levels (an anchor on a
            non-direct photo leaves it packaging or unrelated).
Needs work/measure/{split}_r-items.jsonl and {split}_all.jsonl (measure.py); the test split needs configs/frozen.yaml.

  python src/anchor_checks.py --split test
  -> results/overlay_check_{split}_{stamp}.json, results/anchored_split_{split}_{stamp}.json
"""
import argparse
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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True, choices=["dev", "test"])
    args = ap.parse_args()
    if args.split == "test" and not (ROOT / "configs" / "frozen.yaml").exists():
        sys.exit("測試集要等 configs/frozen.yaml（鐵則 3）")
    r_path = ROOT / "annotations" / args.split / "R.jsonl"
    m_items = ROOT / "work" / "measure" / f"{args.split}_r-items.jsonl"
    m_all = ROOT / "work" / "measure" / f"{args.split}_all.jsonl"
    R = {r["item"]: r for r in rj(r_path) if r.get("status", "done") == "done" and not r.get("not_photo") and r.get("level")}
    M = {r["item"]: r for r in rj(m_items) if r.get("item") in R}
    both = sorted(set(R) & set(M))
    ov = [i for i in both if R[i].get("overlay")]
    ov_tool = [i for i in ov if M[i].get("anchored_ocr")]
    false_anchor = [i for i in ov_tool if not R[i].get("anchor_text")]
    stamp = run_stamp()
    overlay = {"meta": run_meta("configs/default.yaml", [str(r_path), str(m_items)]), "split": args.split, "photos": len(both),
               "overlay_ticked": len(ov), "overlay_and_tool_ocr_anchored": len(ov_tool), "false_anchor_through_overlay": len(false_anchor),
               "tool_ocr_anchors": sum(1 for i in both if M[i].get("anchored_ocr")),
               "annotator_text_anchors": sum(1 for i in both if R[i].get("anchor_text")),
               "note": "overlay = annotation R 後製疊字; a false anchor = overlay ticked, tool anchored by OCR, annotator did not anchor by text"}
    p1 = ROOT / "results" / f"overlay_check_{args.split}_{stamp}.json"
    p1.write_text(json.dumps(overlay, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = rj(m_all)
    anchored = [r for r in rows if r.get("anchored_ocr") or r.get("logo_found")]
    by4 = collections.Counter(r["level4"] for r in anchored)
    split = {"meta": run_meta("configs/default.yaml", [str(m_all)]), "split": args.split, "photos": len(rows), "anchored": len(anchored),
             "anchored_share": round(len(anchored) / len(rows), 4) if rows else None,
             "anchored_by_level4": dict(by4), "anchored_by_level4_share": {k: round(v / len(rows), 4) for k, v in by4.items()},
             "note": "an anchor on a non-direct photo leaves it packaging or unrelated"}
    p2 = ROOT / "results" / f"anchored_split_{args.split}_{stamp}.json"
    p2.write_text(json.dumps(split, ensure_ascii=False, indent=1), encoding="utf-8")
    print({k: overlay[k] for k in ("photos", "overlay_ticked", "overlay_and_tool_ocr_anchored", "false_anchor_through_overlay", "tool_ocr_anchors", "annotator_text_anchors")})
    print({k: split[k] for k in ("photos", "anchored", "anchored_share", "anchored_by_level4")}, "| wrote", p1, p2)


if __name__ == "__main__":
    main()
