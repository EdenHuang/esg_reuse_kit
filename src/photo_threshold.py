"""The photo threshold from annotation A's development items (PLAN §5 step 3: prompts and threshold set on the 100
development images; accuracy reported on the 200 test images, target >= 90%, otherwise a vision-language model).

The 100 development items were drawn as 10 per photo-score band of 0.1 (README 怎麼抽題 A), so a plain average over
them over-weights the uncertain middle. Every metric is given twice: on the 100 items as drawn, and weighted to the
development population (the images given to the photo model in the development firms: each band's items count
N_band / n_band). The threshold kept is the one with the highest weighted accuracy (ties: the one closest to the
current value).

Labels: annotations/dev/A.jsonl. With one annotator that annotator's answers; with two, the items they agree on plus
Eden's decisions on the others (annotations/dev/A_final.jsonl when it exists). Never reads annotations/test/
(CLAUDE.md rule 3). Writes results/photo_threshold_dev_{stamp}.json and reports/photo_threshold_dev_{stamp}.html
(the images on the wrong side of the chosen threshold).

  python src/photo_threshold.py
"""
import argparse
import base64
import collections
import csv
import html
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp  # noqa: E402

BEFORE_MODEL = {"image_only_report", "cover_page", "vector_shading", "layout_repeat", "duplicate"}


def read_jsonl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()] if Path(p).exists() else []


def metrics(rows, thr, weight):
    """rows: (score, is_photo, weight key) → accuracy, precision, recall, F1 of 'photo', weighted by weight[key]."""
    tp = fp = fn = tn = 0.0
    for s, y, k in rows:
        w = weight(k)
        pred = s >= thr
        if pred and y:
            tp += w
        elif pred:
            fp += w
        elif y:
            fn += w
        else:
            tn += w
    tot = tp + fp + fn + tn
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else None
    r = lambda x: None if x is None else round(x, 4)  # noqa: E731
    return {"accuracy": r((tp + tn) / tot), "precision": r(prec), "recall": r(rec), "f1": r(f1),
            "tp": r(tp), "fp": r(fp), "fn": r(fn), "tn": r(tn)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", type=float, default=0.05)
    args = ap.parse_args()
    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))
    current = float(cfg["photo_filter"]["photo_threshold"])
    items = {r["item"]: r for r in read_jsonl(ROOT / "annotations" / "dev" / "A_items.jsonl")}
    final = read_jsonl(ROOT / "annotations" / "dev" / "A_final.jsonl")
    raw = read_jsonl(ROOT / "annotations" / "dev" / "A.jsonl")
    annotators = sorted({r["annotator"] for r in raw})
    if final:
        labels = {r["item"]: r["answer"] for r in final}
        source = "annotations/dev/A_final.jsonl"
    elif len(annotators) == 1:
        labels = {r["item"]: r["answer"] for r in raw}
        source = f"annotations/dev/A.jsonl ({annotators[0]} only)"
    else:
        by = collections.defaultdict(dict)
        for r in raw:
            by[r["item"]][r["annotator"]] = r["answer"]
        labels = {i: next(iter(a.values())) for i, a in by.items() if len(set(a.values())) == 1}
        source = f"annotations/dev/A.jsonl (items {', '.join(annotators)} agree on; Eden decides the rest)"
    missing = sorted(set(items) - set(labels))

    filt = {}
    with open(ROOT / "work" / "photo_filter.csv", encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if d["split"] == "dev":
                filt[d["crop"]] = d
    judged = [d for d in filt.values() if not (set(filter(None, d["exclude"].split("|"))) & BEFORE_MODEL)]
    band = lambda s: f"bin{min(9, int(float(s) * 10))}"  # noqa: E731
    pop = collections.Counter(band(d["photo_score"]) for d in judged)
    rows = []
    for i, lab in labels.items():
        it = items[i]
        s = float(filt[it["crop"]]["photo_score"])
        rows.append((s, lab == "Photo", it["stratum"], it["crop"], i))
    drawn = collections.Counter(r[2] for r in rows)
    bad_strata = [k for k in drawn if band(filt[items[next(r[4] for r in rows if r[2] == k)]["crop"]]["photo_score"]) != k]
    weight = lambda k: pop[k] / drawn[k] if drawn[k] else 0.0  # noqa: E731
    one = lambda k: 1.0  # noqa: E731
    triples = [(s, y, k) for s, y, k, _, _ in rows]

    grid = sorted({round(args.step * k, 4) for k in range(1, int(1 / args.step))} | {current})
    table = [{"threshold": t, "weighted": metrics(triples, t, weight), "as_drawn": metrics(triples, t, one)} for t in grid]
    best = max(table, key=lambda r: (r["weighted"]["accuracy"], -abs(r["threshold"] - current)))
    at_current = next(r for r in table if r["threshold"] == current)
    by_band = {k: {"population": pop[k], "drawn": drawn[k], "photos": sum(1 for s, y, kk in triples if kk == k and y)}
               for k in sorted(pop)}

    # the vision-language model's answer (photo_vlm.py, cached) as the filter, alone or with OpenCLIP (10/19:
    # PLAN step 3's fallback). The weights stay those of the OpenCLIP score bands the items were drawn from.
    import hashlib
    ev = cfg["evidence"]
    ph = hashlib.sha256((ROOT / "configs" / "prompts" / "is_photo.txt").read_bytes()).hexdigest()
    vlm = {}
    cache = ROOT / "work" / "evidence" / "vlm_is_photo.jsonl"
    if cache.exists():
        for line in open(cache, encoding="utf-8"):
            r = json.loads(line)
            if r.get("revision") == ev["vlm_revision"] and r.get("prompt_sha256") == ph:
                vlm[r["crop"]] = r["p_yes"]
    comparison = None
    if all(c in vlm for _, _, _, c, _ in rows):
        def fam(pred):
            tr = [(1.0 if pred(s, vlm[c]) else 0.0, y, k) for s, y, k, c, _ in rows]
            return {"weighted": metrics(tr, 0.5, weight), "as_drawn": metrics(tr, 0.5, one)}
        vgrid = [0.5, 0.7, 0.8, 0.9, 0.95, 0.98]
        cands = {"openclip": [({"openclip": t}, fam(lambda s, v, t=t: s >= t)) for t in grid],
                 "vlm": [({"vlm": t}, fam(lambda s, v, t=t: v >= t)) for t in vgrid],
                 "either": [({"openclip": t, "vlm": u}, fam(lambda s, v, t=t, u=u: s >= t or v >= u)) for t in grid for u in vgrid],
                 "both": [({"openclip": t, "vlm": u}, fam(lambda s, v, t=t, u=u: s >= t and v >= u)) for t in grid for u in vgrid]}
        comparison = {name: dict(zip(("thresholds", "metrics"), max(opts, key=lambda o: o[1]["weighted"]["accuracy"])))
                      for name, opts in cands.items()}
    wrong = sorted(((s, y, c, i) for s, y, k, c, i in rows if (s >= best["threshold"]) != y), key=lambda x: x[0])
    res = {"labels": source, "annotators": annotators, "items": len(rows), "missing_labels": missing,
           "photos_in_labels": sum(y for _, y, _ in triples), "dev_judged_images": len(judged),
           "bands": by_band, "strata_mismatch": bad_strata,
           "current_threshold": current, "at_current": at_current, "chosen": best,
           "rule": "highest population-weighted accuracy on the development items; ties: closest to the current value",
           "target_test_accuracy": 0.90, "table": table,
           "comparison": comparison, "vlm_answers": len(vlm),
           "wrong_at_chosen": [{"crop": c, "item": i, "score": round(s, 4), "label": "Photo" if y else "Not a photo"} for s, y, c, i in wrong]}
    stamp = run_stamp()
    rpath = ROOT / "results" / f"photo_threshold_dev_{stamp}.json"
    k = 2
    while rpath.exists():
        rpath = ROOT / "results" / f"photo_threshold_dev_{stamp}_{k}.json"
        k += 1
    rpath.write_text(json.dumps({"meta": run_meta("configs/default.yaml", [ROOT / "annotations" / "dev" / "A.jsonl",
                                                                         ROOT / "annotations" / "dev" / "A_items.jsonl",
                                                                         ROOT / "work" / "photo_filter.csv"]), **res},
                                ensure_ascii=False, indent=1), encoding="utf-8")

    def img(c):
        return base64.b64encode((ROOT / "work" / "extract" / "crops" / c).read_bytes()).decode()
    pct = lambda x: "—" if x is None else f"{100 * x:.1f}%"  # noqa: E731
    trs = "".join(f'<tr{" class=on" if r["threshold"] == best["threshold"] else ""}><td>{r["threshold"]:g}</td>'
                  f'<td>{pct(r["weighted"]["accuracy"])}</td><td>{pct(r["weighted"]["precision"])}</td><td>{pct(r["weighted"]["recall"])}</td>'
                  f'<td>{pct(r["as_drawn"]["accuracy"])}</td></tr>' for r in table)
    names = {"openclip": "OpenCLIP 分數", "vlm": "視覺語言模型", "either": "兩者任一說是照片", "both": "兩者都說是照片"}
    crows = "".join(f'<tr><td>{names[n]}</td><td>{", ".join(f"{k} ≥ {v:g}" for k, v in x["thresholds"].items())}</td>'
                    f'<td>{pct(x["metrics"]["weighted"]["accuracy"])}</td><td>{pct(x["metrics"]["weighted"]["precision"])}</td>'
                    f'<td>{pct(x["metrics"]["weighted"]["recall"])}</td></tr>' for n, x in (comparison or {}).items())
    ctable = (f'<h2 style="font-size:16px;margin-top:24px">換一種照片過濾（每種取加權準確率最高的門檻）</h2><div class="wrap"><table>'
              f'<tr><th>做法</th><th>門檻</th><th>加權準確率</th><th>加權精確率</th><th>加權召回率</th></tr>{crows}</table></div>') if comparison else ""
    cards = "".join(f'<figure><img src="data:image/jpeg;base64,{img(c)}" alt=""><figcaption>{"照片" if y else "不是照片"}（學生）｜模型分數 {s:.2f}</figcaption></figure>'
                    for s, y, c, i in wrong)
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>照片門檻</title><style>
:root{{--bg:#fafafa;--fg:#222;--mut:#666;--line:#ddd;--on:#fff4cc}}
body{{font-family:system-ui,"Microsoft JhengHei",sans-serif;margin:16px;background:var(--bg);color:var(--fg);max-width:1000px}}
table{{border-collapse:collapse;font-size:14px}} td,th{{border:1px solid var(--line);padding:4px 8px;text-align:right}} tr.on td{{background:var(--on);font-weight:600}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}} figure{{margin:0}} figure img{{width:100%;height:180px;object-fit:contain;background:#0001}}
figcaption{{font-size:13px;color:var(--mut)}} .wrap{{overflow-x:auto}}
</style></head><body><h1 style="font-size:20px">照片門檻：開發集 {len(rows)} 張</h1>
<p>答案來源：{html.escape(source)}。照片 {res["photos_in_labels"]} 張。這 {len(rows)} 張是照分數每 0.1 一層、每層抽 10 張（最低那層 10/19 另外加抽 50 張），所以「照開發集比例加權」那欄才代表開發集全部 {len(judged):,} 張給模型判斷的圖。</p>
<p><b>選的門檻：{best["threshold"]:g}</b>（加權準確率最高）：加權準確率 {pct(best["weighted"]["accuracy"])}、照片的精確率 {pct(best["weighted"]["precision"])}、召回率 {pct(best["weighted"]["recall"])}。
現在的初值 {current:g}：加權準確率 {pct(at_current["weighted"]["accuracy"])}。正式的準確率在測試集 200 張上報（目標 90%），11/2 凍結前不算。</p>
<div class="wrap"><table><tr><th>門檻</th><th>加權準確率</th><th>加權精確率</th><th>加權召回率</th><th>抽到的原樣準確率</th></tr>{trs}</table></div>
{ctable}
<h2 style="font-size:16px;margin-top:24px">門檻 {best["threshold"]:g} 判錯的 {len(wrong)} 張</h2><div class="grid">{cards}</div></body></html>"""
    hpath = ROOT / "reports" / f"photo_threshold_dev_{stamp}.html"
    hpath.write_text(page, encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("labels", "items", "photos_in_labels", "dev_judged_images", "bands", "strata_mismatch",
                                          "at_current")} | {"chosen": best, "comparison": comparison}, ensure_ascii=False))
    print(f"wrong at chosen: {len(wrong)}; wrote {rpath} and {hpath}")


if __name__ == "__main__":
    main()
