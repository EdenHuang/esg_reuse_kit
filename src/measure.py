"""The four-level evidence measurement (PLAN §5 step 5 after D15, 10/20; Tables 5 and 8): for each photo,
  拍到   the vision-language model's direct choice with examples (E3, baselines_evidence.run_e2), its p(A) / p(C)
         cut as in configs `measure` (cut_p_direct, cut_p_unrelated; set on the development split);
  定錨   PaddleOCR text matched against the firm's own names (catalog, reports_index, ESG InfoHub list) and the
         report year in AD and ROC (evidence.anchor_match), or the firm's confirmed logo found by evidence.logo_matches.
         10/20: the language model's anchor strings (event names, other years) changed no development photo
         (OCR anchor F1 0.684 with and without them), so the tool does not call it.
  四級   拍到 + 定錨 = 證據, 拍到 = 示意, topical = 包裝, else 無關.
Context: extract_pairs' rule-based context (`measure.context`, default rules), as in the development comparison.
E3 examples: development photos. On the development split each photo gets examples from other firms
(baselines_evidence.e3_examples); on the test split the fixed ones in configs/frozen.yaml (`measure.e3_examples`).

Photos: --photos r-items (annotations/{split}/R_items.jsonl, for evaluate.py) or all (every photo the photo filter
keeps in the split, for analyze.py). The test split needs configs/frozen.yaml with the same settings (frozen.check).
On the development split the cuts are the ones set on the same photos, so it is for building analyze.py, not for
scoring (tune_evidence.py gives the out-of-fold scores).
Writes work/measure/{split}_{photos}.jsonl (one row per photo), work/eval/{split}_measure/tool.jsonl (r-items: the
evaluate.py format) and results/measure_{split}_{photos}_{stamp}.json.

  python src/measure.py --split dev --photos all
"""
import argparse
import collections
import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "work" / "models" / "hf"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(ROOT / "src"))
import yaml  # noqa: E402

import frozen  # noqa: E402
from meta import run_meta, run_stamp, sha256_file  # noqa: E402

LEVEL4 = ("證據", "示意", "包裝", "無關")
OCR_CHUNK = 1500


def level_of(p, m):
    if p["direct"] >= float(m["cut_p_direct"]):
        return "direct"
    return "unrelated" if p["unrelated"] >= float(m["cut_p_unrelated"]) else "topical"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--photos", choices=["r-items", "all"], required=True)
    ap.add_argument("--dev-labels", default=str(ROOT / "annotations" / "dev" / "R.jsonl"), help="the E3 examples' answers")
    ap.add_argument("--limit", type=int, help="first N photos only (a trial run)")
    ap.add_argument("--tag", help="suffix for the output files (a variant run that must not replace the main one)")
    ap.add_argument("--split-punct", type=int, choices=[0, 1], default=None,
                    help="1: short English names also match as punctuation-bounded OCR tokens (NEXT C11 trial); default: configs")
    args = ap.parse_args()
    import baselines_evidence as be
    import evidence as E
    import logo_refs as lr

    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))
    fr = frozen.check(args.split, cfg)
    ev, m = cfg["evidence"], dict(cfg["measure"])
    if m.get("depiction") != "E3_cal":
        sys.exit(f"measure.depiction = {m.get('depiction')}：這支程式只做 E3_cal")
    t0 = time.time()
    filt = {}
    with open(ROOT / "work" / "photo_filter.csv", encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            filt[d["crop"]] = d
    item_of = {}
    if args.photos == "r-items":
        rows = [json.loads(l) for l in open(ROOT / "annotations" / args.split / "R_items.jsonl", encoding="utf-8")]
        crops = [r["crop"] for r in rows]
        item_of = {r["crop"]: r["item"] for r in rows}
    else:
        crops = sorted(c for c, d in filt.items() if d["split"] == args.split and d["keep"] == "1")
    if any(filt[c]["split"] != args.split for c in crops):
        sys.exit("照片的 split 對不上")
    crops = crops[:args.limit] if args.limit else crops

    # E3 examples: development photos with Eden's levels
    dev_items = [json.loads(l) for l in open(ROOT / "annotations" / "dev" / "R_items.jsonl", encoding="utf-8")]
    dev_lab = {json.loads(l)["item"]: json.loads(l) for l in open(args.dev_labels, encoding="utf-8")}
    by_crop = {it["crop"]: dev_lab[it["item"]]["level"] for it in dev_items
               if it["item"] in dev_lab and not dev_lab[it["item"]].get("not_photo") and dev_lab[it["item"]].get("level")}
    ex_crops = fr["measure"]["e3_examples"] if fr else sorted(by_crop)
    need = set(crops) | set(ex_crops)
    recs = {}
    for rep in sorted({filt[c]["report"] for c in need}):
        for line in open(ROOT / "work" / "extract" / f"{rep}_pairs.jsonl", encoding="utf-8"):
            d = json.loads(line)
            if d["crop"] in need:
                recs[d["crop"]] = d
    ctx_arg = m.get("context", "rules")
    ctx = be.load_contexts(ctx_arg, sorted(need), recs, ev["context_max_chars"])

    def row(c, with_image=False):
        r = {"crop": c, "firm": filt[c]["firm_id"], "labelled": ctx[c][0], "plain": ctx[c][1], "parts": ctx[c][2]}
        if with_image:
            r["image"] = be.image_of({"crop": c})
        return r
    ex_rows = [row(c, True) for c in ex_crops]
    if fr:
        pick = lambda q: ex_rows  # noqa: E731
    else:
        pick = be.e3_examples(ex_rows, by_crop, int(m.get("shots_per_level", 1)))
    rows = [row(c) for c in crops]
    prompt_path = ROOT / "configs" / "prompts" / f"{m.get('prompt', 'direct_zeroshot')}.txt"
    work = ROOT / cfg["paths"]["evidence"]
    e3, e3_info = be.run_e2(rows, ev, prompt_path.read_text(encoding="utf-8"), sha256_file(prompt_path),
                            work / "e3_fewshot.jsonl", examples=pick, labels_by_crop=by_crop)
    print(f"[E3] {e3_info['new']} 新，{time.time() - t0:.0f} 秒", flush=True)

    # anchors: OCR (in chunks: one PaddleOCR process per chunk) and logos
    with open(ROOT / cfg["paths"]["catalog"], encoding="utf-8-sig", newline="") as f:
        names, _ = lr.load_names(list(csv.DictReader(f)), ROOT / "data" / "index")
    lp = {"ocr_min_score": cfg["logo_refs"].get("ocr_min_score", 0.5), "name_bigram_min": float(ev.get("ocr_match_threshold", 0.7)),
          "short_name_split_punct": bool(args.split_punct if args.split_punct is not None else ev.get("short_name_split_punct", False))}
    ocr, ocr_new, ocr_err = {}, 0, []
    for i in range(0, len(crops), OCR_CHUNK):
        got, info = E.ocr_photos(crops[i:i + OCR_CHUNK], work / "ocr_photo", cfg["logo_refs"].get("ocr_lang", "chinese_cht"),
                                 workers=int(cfg["logo_refs"].get("ocr_workers", 1)))
        ocr.update(got)
        ocr_new += info["new"]
        if info.get("error"):
            ocr_err.append(info["error"])
        if info["new"]:
            print(f"[ocr] {min(i + OCR_CHUNK, len(crops))}/{len(crops)}，{time.time() - t0:.0f} 秒", flush=True)
    if ocr_err:   # 10/20: a run whose OCR failed (shapely blocked) wrote anchors for 162 of 3,437 photos; never again
        sys.exit("OCR 失敗，沒有寫任何結果：" + " | ".join(e[:400] for e in ocr_err[:3]))
    photos = [{"crop": c, "firm": filt[c]["firm_id"]} for c in crops]
    logo_info = E.logo_matches(photos, ev, work)
    print(f"[logo] 新比對 {logo_info['new']}，{time.time() - t0:.0f} 秒", flush=True)

    out = []
    for ph in photos:
        c = ph["crop"]
        d = filt[c]
        a = E.anchor_match(ocr.get(c), names.get(d["firm_id"], []), [], [str(d["year"])], lp)
        a_ocr = bool(a["org"] or a["event"] or a["year"])
        anchored = a_ocr or ph["logo"]["found"]
        p = e3[c]["p"]
        lv = level_of(p, m)
        out.append({"crop": c, "item": item_of.get(c), "report": d["report"], "firm": d["firm_id"], "year": int(d["year"]),
                    "page": int(d["page"]), "p": p, "level3": lv,
                    "level4": ("證據" if anchored else "示意") if lv == "direct" else ("包裝" if lv == "topical" else "無關"),
                    "anchored": anchored, "anchored_ocr": a_ocr, "anchor_org": (a["org"] or {}).get("name") if a["org"] else None,
                    "anchor_year": bool(a["year"]), "logo_available": ph["logo"]["available"], "logo_found": ph["logo"]["found"],
                    "context_empty": not ctx[c][2]})
    mdir = ROOT / "work" / "measure"
    mdir.mkdir(parents=True, exist_ok=True)
    tag = f"{args.split}_{args.photos}" + (f"_first{args.limit}" if args.limit else "") + (f"_{args.tag}" if args.tag else "")
    with open(mdir / f"{tag}.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if args.photos == "r-items" and not args.limit:
        edir = ROOT / "work" / "eval" / (f"{args.split}_measure" + (f"_{args.tag}" if args.tag else ""))
        edir.mkdir(parents=True, exist_ok=True)
        with open(edir / "tool.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for r in out:
                f.write(json.dumps({k: r[k] for k in ("item", "crop", "firm", "level3", "level4", "anchored")}, ensure_ascii=False) + "\n")
    n = len(out)
    res = {"split": args.split, "photos_set": args.photos, "photos": n, "firms": len({r["firm"] for r in out}),
           "reports": len({r["report"] for r in out}),
           "settings": {"measure": m, "e3_examples": "fixed (frozen.yaml)" if fr else "development photos of other firms",
                        "vlm": ev["vlm"], "vlm_revision": ev["vlm_revision"], "context": ctx_arg, "name_match": lp,
                        "cuts_from": "development split (in sample on dev)" if not fr else "configs/frozen.yaml"},
           "levels4": {lv: sum(r["level4"] == lv for r in out) for lv in LEVEL4},
           "shares4": {lv: round(sum(r["level4"] == lv for r in out) / n, 4) for lv in LEVEL4} if n else {},
           "anchored": sum(r["anchored"] for r in out), "anchored_ocr": sum(r["anchored_ocr"] for r in out),
           "anchor_kinds": {"org": sum(1 for r in out if r["anchor_org"]), "year": sum(r["anchor_year"] for r in out),
                            "logo": sum(r["logo_found"] for r in out)},
           "logo_coverage": {"photos_with_refs": sum(r["logo_available"] for r in out),
                             "firms_with_refs": len({r["firm"] for r in out if r["logo_available"]})},
           "context_empty": sum(r["context_empty"] for r in out),
           "new_outputs": {"e3": e3_info["new"], "ocr": ocr_new, "logo": logo_info["new"]}, "ocr_errors": ocr_err,
           "seconds": round(time.time() - t0, 1), "file": str((mdir / f"{tag}.jsonl").relative_to(ROOT))}
    stamp = run_stamp()
    rp = ROOT / "results" / f"measure_{tag}_{stamp}.json"
    k = 2
    while rp.exists():
        rp = ROOT / "results" / f"measure_{tag}_{stamp}_{k}.json"
        k += 1
    rp.write_text(json.dumps({"meta": run_meta("configs/default.yaml", [ROOT / "work" / "photo_filter.csv", prompt_path, Path(args.dev_labels)]
                                               + ([frozen.FROZEN] if fr else []), packages=("torch", "transformers", "pillow")),
                              **res}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k2: res[k2] for k2 in ("photos", "levels4", "anchored", "anchor_kinds", "logo_coverage", "context_empty", "seconds")}, ensure_ascii=False))
    print(f"wrote {rp}")


if __name__ == "__main__":
    main()
