"""Annotation R: does the page have describing text (annotator's boxes) against the depiction level (the two questions
coincided on the development split, review 10/22). Same shape as results/text_level_crosstab_dev_20261022_0910.json so
make_tables.py's macros (textN, textDirect, noTextN, noTextTopical, noTextUnrelated) read either split. The test split
needs configs/frozen.yaml (annotations only, but the number is a test-split finding).

  python src/text_level_crosstab.py --split test
"""
import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True, choices=["dev", "test"])
    args = ap.parse_args()
    if args.split == "test" and not (ROOT / "configs" / "frozen.yaml").exists():
        sys.exit("測試集要等 configs/frozen.yaml（鐵則 3）")
    path = ROOT / "annotations" / args.split / "R.jsonl"
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    rows = [r for r in rows if r.get("status", "done") == "done" and not r.get("not_photo") and r.get("level")]
    ct = collections.Counter()
    for r in rows:
        has_text = bool(r.get("describing_boxes")) and not r.get("no_text")
        ct[("text" if has_text else "no_text") + "/" + r["level"]] += 1
    with_text = {"n": sum(v for k, v in ct.items() if k.startswith("text/")), "direct": ct["text/direct"],
                 "topical": ct["text/topical"], "unrelated": ct["text/unrelated"]}
    no_text = {"n": sum(v for k, v in ct.items() if k.startswith("no_text/")), "direct": ct["no_text/direct"],
               "topical": ct["no_text/topical"], "unrelated": ct["no_text/unrelated"]}
    res = {"meta": run_meta("configs/default.yaml", [str(path)]), "split": args.split, "photos": len(rows),
           "crosstab": dict(sorted(ct.items())), "with_text": with_text, "no_text": no_text,
           "coincidence": {"with_text_all_direct": with_text["direct"] == with_text["n"],
                           "no_text_none_direct": no_text["direct"] == 0},
           "note": f"Annotation R, {args.split} split: whether the page has describing text (annotator's boxes, not flagged 這頁沒有描述文字) "
                   "against the depiction level."}
    out = ROOT / "results" / f"text_level_crosstab_{args.split}_{run_stamp()}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("photos", "crosstab", "coincidence")}, ensure_ascii=False), "| wrote", out)


if __name__ == "__main__":
    main()
