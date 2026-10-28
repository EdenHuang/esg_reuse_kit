"""Set the evidence thresholds on annotation R's development labels (PLAN §5 step 5: 門檻全部在開發集調), for module 2,
its ablations and E1, and estimate how well the tuned methods do on firms they were not tuned on.

  module 2        direct when the photo's questions score >= d (the score is the largest p(yes), the mean, or the
                  mean of the two largest), else topical when the topic check >= t, else unrelated (evidence.py)
  ablations (Table 6, the same grid and the same leave-one-firm-out tuning):
    m2_no_dsg       answers without the DSG rule (p(yes) instead of the parent-gated p_eff)
    m2_one_question no decomposition: one yes/no question with the whole context (evidence.py --holistic)
    m2_no_gate      no 可拍性 rule: a photo whose text has no camera-visible referent gets the one-question score
                    instead of being capped at 包裝
    m2_no_topical   no topic check: not depicted -> unrelated
    m2_no_ocr / m2_no_logo  module 2 with anchors from logos only / OCR only (four levels; the three levels are
                    module 2's)
  E1              Chinese CLIP similarity: direct >= t2, topical >= t1, else unrelated (baselines_evidence.py)
  E2, E3          the VLM's own choice, zero-shot / few-shot: nothing to tune
  E2_cal, E3_cal  the same letter probabilities with two cuts tuned like E1's: direct when p(A) >= a, else
                  unrelated when p(C) >= b, else topical. E3_cal is the measurement tool after D15 (10/20).
Every method's predictions also carry four levels from module 2's anchors (OCR or logo found); module2, E2_cal and
E3_cal also get the anchoring ablations ({name}_no_ocr / _no_logo; m2_* for module 2).
Metric for tuning: macro-F1 over 直接對應／主題相關／無關 (PLAN step 8), on photos the annotator gave a level.
The thresholds kept are the best macro-F1 on all development photos (ties: the first in grid order). Because that is
chosen on the same photos, the result file also gives leave-one-firm-out estimates: for each firm, the thresholds
are chosen on the other firms and applied to it; the pooled out-of-fold predictions are scored, and written to
work/eval/dev_{context}/{method}.jsonl for evaluate.py (bootstrap intervals, κ, four levels). Development split only
(CLAUDE.md rule 3). Writes results/tune_evidence_dev_{stamp}.json.

  python src/tune_evidence.py --evidence work/evidence/evidence_dev_ctx_rules.jsonl ^
      --baselines results/baselines_evidence_dev_ctx_rules_<stamp>.json --context rules
"""
import argparse
import collections
import itertools
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp  # noqa: E402

LV = ("direct", "topical", "unrelated")


def macro_f1(pairs):
    f1s = []
    for lv in LV:
        tp = sum(1 for a, b in pairs if a == lv and b == lv)
        fp = sum(1 for a, b in pairs if a != lv and b == lv)
        fn = sum(1 for a, b in pairs if a == lv and b != lv)
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return round(statistics.mean(f1s), 4)


def score(pairs):
    return {"photos": len(pairs), "macro_f1": macro_f1(pairs), "accuracy": round(sum(a == b for a, b in pairs) / len(pairs), 4) if pairs else None,
            "confusion_label_by_model": {a: dict(collections.Counter(b for x, b in pairs if x == a)) for a in LV}}


def tune(rows, grid, predict):
    """Best grid point by macro-F1 on rows (label, features); the first in grid order wins a tie."""
    best, best_f = None, -1.0
    for g in grid:
        f = macro_f1([(r["label"], predict(r, g)) for r in rows])
        if f > best_f:
            best, best_f = g, f
    return best, best_f


def lofo(rows, grid, predict):
    """Out-of-fold predictions {crop: (level, thresholds)}: each firm with thresholds tuned on the other firms."""
    out = {}
    for firm in sorted({r["firm"] for r in rows}):
        g, _ = tune([r for r in rows if r["firm"] != firm], grid, predict)
        out.update({r["crop"]: (predict(r, g), g) for r in rows if r["firm"] == firm})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--baselines", help="baselines_evidence.py result for the same photos and context (E1, E2, E3)")
    ap.add_argument("--context", default="rules", help="name of the context source (for the prediction folder)")
    ap.add_argument("--labels", default=str(ROOT / "annotations" / "dev" / "R.jsonl"))
    ap.add_argument("--split", choices=["dev", "test"], default="dev")
    ap.add_argument("--apply", help="no tuning: 'frozen' (configs/frozen.yaml thresholds) or a tune_evidence result file; "
                                    "predictions for every photo of --evidence, labels unused")
    args = ap.parse_args()
    if args.split == "test" and args.apply != "frozen":
        sys.exit("測試集只能 --apply frozen（鐵則 3）")
    if args.apply == "frozen":
        import frozen
        import yaml
        fr = frozen.check(args.split, yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8")))
        fr = fr or frozen.load()
        if not fr:
            sys.exit("沒有 configs/frozen.yaml")
        fixed = (fr.get("thresholds") or {}).get(args.context)
        if fixed is None:
            sys.exit(f"frozen.yaml 沒有 {args.context} 的門檻")
    elif args.apply:
        fixed = {n: m["chosen"] for n, m in json.load(open(args.apply, encoding="utf-8"))["methods"].items() if "chosen" in m}
    items = {json.loads(l)["item"]: json.loads(l) for l in open(ROOT / "annotations" / args.split / "R_items.jsonl", encoding="utf-8")}
    ev = {json.loads(l)["crop"]: json.loads(l) for l in open(args.evidence, encoding="utf-8")}
    if args.apply:   # every photo of the evidence file; no labels are read
        labels = {ph["item"]: {"level": None} for ph in ev.values() if ph.get("item") in items}
    else:
        labels = {r["item"]: r for r in (json.loads(l) for l in open(args.labels, encoding="utf-8"))}
    base = {d["crop"]: d for d in json.load(open(args.baselines, encoding="utf-8"))["per_photo"]} if args.baselines else {}
    rows = []
    for it, lab in labels.items():
        if not args.apply and (lab.get("not_photo") or not lab.get("level")):
            continue
        c = items[it]["crop"]
        ph = ev.get(c)
        if ph is None or (args.baselines and c not in base):
            continue
        ps = sorted((q["p_eff"] for q in ph["questions"]), reverse=True)
        raw = sorted((q["p_yes"] for q in ph["questions"]), reverse=True)
        agg = lambda v: {"max": v[0] if v else 0.0, "mean": statistics.mean(v) if v else 0.0,  # noqa: E731
                         "top2": statistics.mean(v[:2]) if v else 0.0}
        hol = ph.get("holistic_p")
        b = base.get(c, {})
        rows.append({"item": it, "crop": c, "firm": items[it]["firm"], "label": lab["level"],
                     "agg": agg(ps), "agg_raw": agg(raw), "has_q": bool(ps), "holistic": hol,
                     "topical": ph["topical_p"] if ph["topical_p"] is not None else 0.0,
                     "anchored_ocr": bool(ph.get("anchored_text")), "anchored_logo": bool((ph.get("logo") or {}).get("found")),
                     "e1": b.get("e1_sim"), "e2": b.get("e2_choice"), "e3": b.get("e3_choice"),
                     "e2_p": b.get("e2_p"), "e3_p": b.get("e3_p")})
    if not rows:
        sys.exit("沒有可以比的照片")

    # 10/20: the first grid (depiction 0.5-0.98, topic 0.2-0.9) put every choice on its lower edge; widened
    D = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.98)
    T = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
    grid3 = list(itertools.product(("max", "top2", "mean"), D, T))
    grid_hol = [("one", d, t) for d, t in itertools.product(D, T)]
    grid_not = [(a, d, None) for a, d in itertools.product(("max", "top2", "mean"), D)]

    def three(depicted, r, t):
        return "direct" if depicted else ("topical" if t is not None and r["topical"] >= t else "unrelated")
    methods = {
        "module2": (grid3, lambda r, g: three(r["has_q"] and r["agg"][g[0]] >= g[1], r, g[2]), ("max", 0.5, 0.6)),
        "m2_no_dsg": (grid3, lambda r, g: three(r["has_q"] and r["agg_raw"][g[0]] >= g[1], r, g[2]), None),
        "m2_no_topical": (grid_not, lambda r, g: three(r["has_q"] and r["agg"][g[0]] >= g[1], r, None), None),
    }
    if any(r["holistic"] is not None for r in rows):   # evidence.py --holistic (None: no text, scored 0)
        methods["m2_one_question"] = (grid_hol, lambda r, g: three((r["holistic"] or 0.0) >= g[1], r, g[2]), None)
        methods["m2_no_gate"] = (grid3, lambda r, g: three((r["agg"][g[0]] if r["has_q"] else (r["holistic"] or 0.0)) >= g[1], r, g[2]), None)
    if args.baselines and all(r["e1"] is not None for r in rows):
        sims = sorted({round(r["e1"], 3) for r in rows})
        cuts = sorted({round(x, 3) for x in [sims[int(len(sims) * q / 20)] for q in range(1, 20)]})
        methods["E1"] = ([(a, b) for a in cuts for b in cuts if a < b],
                         lambda r, g: "direct" if r["e1"] >= g[1] else ("topical" if r["e1"] >= g[0] else "unrelated"), None)
    for name in ("E2", "E3"):   # calibrated: the VLM's own letter probabilities with cuts tuned like E1's (10/20: the
        key = name.lower() + "_p"   # 7B model almost never answers A, so its plain choice is a weak baseline)
        if args.baselines and all(r[key] for r in rows):
            qd = sorted({round(r[key]["direct"], 4) for r in rows})
            qu = sorted({round(r[key]["unrelated"], 4) for r in rows})
            cd = sorted({qd[int(len(qd) * q / 20)] for q in range(1, 20)})
            cu = sorted({qu[int(len(qu) * q / 20)] for q in range(1, 20)})
            methods[f"{name}_cal"] = ([(a, b) for a in cd for b in cu],
                                      lambda r, g, k=key: "direct" if r[k]["direct"] >= g[0] else
                                      ("unrelated" if r[k]["unrelated"] >= g[1] else "topical"), None)

    res = {"context": args.context, "photos": len(rows), "firms": len({r["firm"] for r in rows}),
           "labels": dict(collections.Counter(r["label"] for r in rows)),
           "labels_file": args.labels, "evidence_file": args.evidence, "baselines_file": args.baselines, "methods": {}}
    out_dir = ROOT / "work" / "eval" / f"{args.split}_{args.context}"
    out_dir.mkdir(parents=True, exist_ok=True)

    def write(name, pred, extra=None):
        with open(out_dir / f"{name}.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                d = {"item": r["item"], "crop": r["crop"], "firm": r["firm"], "level3": pred[r["crop"]]}
                if extra:
                    d.update(extra(r, pred[r["crop"]]))
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        return str((out_dir / f"{name}.jsonl").relative_to(ROOT))

    def four(anchored):
        return lambda r, lv: {"anchored": anchored(r), "level4": ("證據" if anchored(r) else "示意") if lv == "direct"
                              else ("包裝" if lv == "topical" else "無關")}
    for name, (grid, pred, initial) in methods.items():
        if args.apply:   # the given thresholds, no tuning, no scores
            if name not in fixed:
                continue
            g = tuple(fixed[name])
            m = {"chosen": list(g), "applied_from": args.apply}
            lv = {r["crop"]: pred(r, g) for r in rows}
        else:
            g, _ = tune(rows, grid, pred)
            oof = lofo(rows, grid, pred)
            m = {"chosen": list(g), "in_sample": score([(r["label"], pred(r, g)) for r in rows]),
                 "leave_one_firm_out": score([(r["label"], oof[r["crop"]][0]) for r in rows]), "grid_points": len(grid),
                 "thresholds_per_fold": dict(collections.Counter(str(list(v[1])) for v in oof.values()))}
            if initial:
                m["at_initial"] = {"thresholds": list(initial), **score([(r["label"], pred(r, initial)) for r in rows])}
            lv = {c: v[0] for c, v in oof.items()}
        # four levels for every method from the same anchors (OCR or logo, evidence.py): D15 (10/20) pairs the
        # direct judgement with module 2's anchoring
        m["predictions"] = write(name, lv, four(lambda r: r["anchored_ocr"] or r["anchored_logo"]))
        if name in ("module2", "E2_cal", "E3_cal"):   # anchoring ablations: the same three levels, one source only
            pre = "m2" if name == "module2" else name
            for an, fn in ((f"{pre}_no_ocr", lambda r: r["anchored_logo"]), (f"{pre}_no_logo", lambda r: r["anchored_ocr"])):
                res["methods"][an] = {"chosen": list(g), "same_three_levels_as": name,
                                      "predictions": write(an, lv, four(fn))}
        res["methods"][name] = m
    for name in ("E2", "E3"):
        key = name.lower()
        if args.baselines and all(r[key] for r in rows):
            pairs = [(r["label"], r[key]) for r in rows]
            if args.apply:
                res["methods"][name] = {"chosen": None, "predictions": write(name, {r["crop"]: r[key] for r in rows},
                                                                             four(lambda r: r["anchored_ocr"] or r["anchored_logo"]))}
                continue
            res["methods"][name] = {"chosen": None, "in_sample": score(pairs), "leave_one_firm_out": score(pairs),
                                    "note": "nothing tuned", "predictions": write(name, {r["crop"]: r[key] for r in rows},
                                                                                 four(lambda r: r["anchored_ocr"] or r["anchored_logo"]))}
    stamp = run_stamp()
    kind = f"apply_{args.split}" if args.apply else "tune_evidence_dev"
    rpath = ROOT / "results" / f"{kind}_{stamp}.json"
    k = 2
    while rpath.exists():
        rpath = ROOT / "results" / f"{kind}_{stamp}_{k}.json"
        k += 1
    rpath.write_text(json.dumps({"meta": run_meta("configs/default.yaml", [Path(args.labels), Path(args.evidence)]
                                                  + ([Path(args.baselines)] if args.baselines else [])), **res},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    for n, m in res["methods"].items():
        if args.apply and "chosen" in m:
            print(f"{args.context:11s} {n:16s} applied {m['chosen']} -> {m.get('predictions')}")
        elif "in_sample" in m:
            print(f"{args.context:11s} {n:16s} chosen {m['chosen']} in-sample {m['in_sample']['macro_f1']} "
                  f"LOFO {m['leave_one_firm_out']['macro_f1']} acc {m['leave_one_firm_out']['accuracy']}")
    print(f"wrote {rpath} and {out_dir.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
