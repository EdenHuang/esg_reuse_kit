"""Evaluation against annotation R (PLAN §5 step 8; Tables 4-6 and the measurement bias of Table 8).

Predictions: JSONL files with one photo per line {"item", "crop", "firm", "level3"[, "level4", "anchored"]}
(tune_evidence.py writes the development ones, out of fold, to work/eval/dev_{context}/{method}.jsonl; the test ones
come from the frozen thresholds). Named on the command line as NAME=FILE, or a folder (--pred-dir: every *.jsonl,
named "{context}/{method}").

Metrics, on the photos the annotator gave a level (not 'not a photo') that the method has a prediction for:
  - three levels (直接對應／主題相關／無關): macro-F1 over the levels present in the labels or the predictions, accuracy,
    precision / recall / F1 per level, the confusion matrix;
  - 直接對應 vs the rest: F1;
  - weighted κ (linear weights, the levels ordered direct > topical > unrelated), model against the annotator;
  - four levels (證據／示意／包裝／無關) and anchoring, when the method gives them: the annotator's four levels are
    direct + (text or logo anchor) = 證據, direct = 示意, topical = 包裝, unrelated = 無關; macro-F1, accuracy, anchor F1
    (anchored = 照片裡有能認出公司或活動的字 or 這家公司的 logo), and the bias of each level's share (model - annotator).
Uncertainty: the firms are resampled with replacement (--boot, 2,000; seed 2026) and the photos of the drawn firms
scored again; 95% percentile intervals. Against --ref (default module2): the paired difference of macro-F1 in each
resample (interval, and the share of resamples where the method is not better), and McNemar's exact test on the
photos one method gets right and the other wrong (直接對應 vs the rest).
--second-labels (R2 on the test split): weighted κ annotator against annotator, the same way.

The test split needs configs/frozen.yaml (CLAUDE.md rule 3): without it this refuses to run.
Writes results/eval_{split}_{tag}_{stamp}.json and reports/eval_{split}_{tag}_{stamp}.html.

  python src/evaluate.py --split dev --pred-dir work/eval/dev_rules --tag rules
"""
import argparse
import collections
import html
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp  # noqa: E402

L3 = ("direct", "topical", "unrelated")
L4 = ("證據", "示意", "包裝", "無關")
ORD = {"direct": 2, "topical": 1, "unrelated": 0}
ZH3 = {"direct": "直接對應", "topical": "主題相關", "unrelated": "無關"}
SEED = 2026


def gold_of(r):
    a = bool(r.get("anchor_text") or r.get("anchor_logo"))
    lv4 = ("證據" if a else "示意") if r["level"] == "direct" else ("包裝" if r["level"] == "topical" else "無關")
    return {"level3": r["level"], "level4": lv4, "anchored": a}


def prf(pairs, pos):
    tp = sum(1 for a, b in pairs if a == pos and b == pos)
    fp = sum(1 for a, b in pairs if a != pos and b == pos)
    fn = sum(1 for a, b in pairs if a == pos and b != pos)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def macro(pairs, levels):
    present = [lv for lv in levels if any(a == lv or b == lv for a, b in pairs)]
    return sum(prf(pairs, lv)[2] for lv in present) / len(present) if present else float("nan")


def kappa_w(pairs):
    """Linear-weighted κ on the ordered three levels."""
    n = len(pairs)
    if not n:
        return float("nan")
    k = 3
    w = lambda i, j: 1 - abs(i - j) / (k - 1)  # noqa: E731
    obs = sum(w(ORD[a], ORD[b]) for a, b in pairs) / n
    pa = collections.Counter(ORD[a] for a, _ in pairs)
    pb = collections.Counter(ORD[b] for _, b in pairs)
    exp = sum(w(i, j) * pa[i] * pb[j] for i in range(k) for j in range(k)) / (n * n)
    return (obs - exp) / (1 - exp) if exp < 1 else float("nan")


def metrics(rows, full=True):
    """rows: [(gold dict, pred dict)]"""
    p3 = [(g["level3"], p["level3"]) for g, p in rows]
    out = {"photos": len(rows), "macro_f1": macro(p3, L3), "accuracy": sum(a == b for a, b in p3) / len(p3) if p3 else float("nan"),
           "direct_f1": prf([("d" if a == "direct" else "o", "d" if b == "direct" else "o") for a, b in p3], "d")[2],
           "kappa_w": kappa_w(p3)}
    four = [(g, p) for g, p in rows if "level4" in p]
    if four and len(four) == len(rows):
        p4 = [(g["level4"], p["level4"]) for g, p in four]
        out["macro_f1_4"] = macro(p4, L4)
        out["accuracy_4"] = sum(a == b for a, b in p4) / len(p4)
        out["anchor_f1"] = prf([(g["anchored"], p["anchored"]) for g, p in four], True)[2]
        if full:
            n = len(p4)
            out["share_bias_4"] = {lv: (sum(b == lv for _, b in p4) - sum(a == lv for a, _ in p4)) / n for lv in L4}
    if full:
        out["per_level"] = {lv: dict(zip(("precision", "recall", "f1"), prf(p3, lv))) for lv in L3}
        out["confusion_label_by_model"] = {a: dict(collections.Counter(b for x, b in p3 if x == a)) for a in L3}
        if four and len(four) == len(rows):
            out["confusion4_label_by_model"] = {a: dict(collections.Counter(b for x, b in p4 if x == a)) for a in L4}
    return out


def mcnemar(rows_a, rows_b):
    """Exact two-sided McNemar on 直接對應 vs the rest: b = only A right, c = only B right."""
    pa = {g["item"]: (g["level3"] == "direct") == (p["level3"] == "direct") for g, p in rows_a}
    pb = {g["item"]: (g["level3"] == "direct") == (p["level3"] == "direct") for g, p in rows_b}
    common = pa.keys() & pb.keys()
    b = sum(1 for i in common if pa[i] and not pb[i])
    c = sum(1 for i in common if pb[i] and not pa[i])
    n = b + c
    if n == 0:
        return {"only_ref_right": b, "only_method_right": c, "p": 1.0}
    tail = sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / 2 ** n
    return {"only_ref_right": b, "only_method_right": c, "p": round(min(1.0, 2 * tail), 4)}


def ci(vals):
    v = sorted(x for x in vals if x == x)
    if not v:
        return None
    return [round(v[int(0.025 * (len(v) - 1))], 4), round(v[int(0.975 * (len(v) - 1))], 4)]


def r4(x):
    return round(x, 4) if isinstance(x, float) and x == x else x


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--labels", help="default annotations/{split}/R.jsonl")
    ap.add_argument("--pred", nargs="*", default=[], help="NAME=FILE")
    ap.add_argument("--pred-dir", nargs="*", default=[], help="folders of NAME.jsonl (named {folder suffix}/{NAME})")
    ap.add_argument("--ref", default="module2", help="the method the others are compared with (a name, or its suffix)")
    ap.add_argument("--second-labels", help="a second annotator's R.jsonl (R2): κ between the annotators")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()
    if args.split == "test" and not (ROOT / "configs" / "frozen.yaml").exists():
        sys.exit("測試集要等 configs/frozen.yaml（鐵則 3）")
    lab_path = Path(args.labels) if args.labels else ROOT / "annotations" / args.split / "R.jsonl"
    labels = {}
    for line in open(lab_path, encoding="utf-8"):
        r = json.loads(line)
        if r.get("status", "done") == "done" and not r.get("not_photo") and r.get("level"):
            labels[r["item"]] = dict(gold_of(r), item=r["item"])
    items = {json.loads(l)["item"]: json.loads(l) for l in open(ROOT / "annotations" / args.split / "R_items.jsonl", encoding="utf-8")}
    files = dict(p.split("=", 1) for p in args.pred)
    for d in args.pred_dir:
        d = Path(d)
        for f in sorted(d.glob("*.jsonl")):
            files[f"{d.name.split('_', 1)[-1]}/{f.stem}"] = str(f)
    if not files:
        sys.exit("沒有預測檔（--pred 或 --pred-dir）")
    preds = {}
    for name, f in files.items():
        rows = []
        for line in open(f, encoding="utf-8"):
            p = json.loads(line)
            if p["item"] in labels:
                rows.append((labels[p["item"]], p))
        preds[name] = rows
    refs = [n for n in preds if n == args.ref or n.endswith("/" + args.ref)]
    firm_of = {i: items[i]["firm"] for i in labels}
    firms = sorted({firm_of[i] for i in labels})
    rng = random.Random(SEED)
    draws = [[rng.choice(firms) for _ in firms] for _ in range(args.boot)]
    by_firm = {n: collections.defaultdict(list) for n in preds}
    for n, rows in preds.items():
        for g, p in rows:
            by_firm[n][firm_of[g["item"]]].append((g, p))

    res = {"split": args.split, "labels_file": str(lab_path), "photos_labelled": len(labels), "firms": len(firms),
           "labels": dict(collections.Counter(g["level3"] for g in labels.values())),
           "labels4": dict(collections.Counter(g["level4"] for g in labels.values())),
           "bootstrap": {"resamples": args.boot, "unit": "firm", "seed": SEED, "interval": "95% percentile"},
           "methods": {}}
    boot = {n: [metrics([x for f in draw for x in by_firm[n][f]], full=False) for draw in draws] for n in preds}
    for n, rows in preds.items():
        m = {k: (r4(v) if not isinstance(v, dict) else {kk: (r4(vv) if not isinstance(vv, dict) else {k3: r4(v3) for k3, v3 in vv.items()})
                                                         for kk, vv in v.items()}) for k, v in metrics(rows).items()}
        m["file"] = files[n]
        m["ci"] = {k: ci([b[k] for b in boot[n] if k in b]) for k in ("macro_f1", "accuracy", "direct_f1", "kappa_w", "macro_f1_4", "anchor_f1")
                   if k in m}
        ref = next((r for r in refs if r.split("/")[0] == n.split("/")[0]), refs[0] if refs else None)
        if ref and ref != n:
            diffs = [b["macro_f1"] - a["macro_f1"] for a, b in zip(boot[ref], boot[n])]
            m["vs_ref"] = {"ref": ref, "macro_f1_diff": r4(m["macro_f1"] - metrics(preds[ref])["macro_f1"]), "diff_ci": ci(diffs),
                           "share_not_better": round(sum(d <= 0 for d in diffs if d == d) / max(1, sum(d == d for d in diffs)), 4),
                           "mcnemar_direct_vs_rest": mcnemar(preds[ref], rows)}
            if "macro_f1_4" in m and "macro_f1_4" in metrics(preds[ref]):   # 10/21: the anchoring ablations change only the four levels
                diffs4 = [b["macro_f1_4"] - a["macro_f1_4"] for a, b in zip(boot[ref], boot[n]) if "macro_f1_4" in a and "macro_f1_4" in b]
                m["vs_ref"]["macro_f1_4_diff"] = r4(m["macro_f1_4"] - metrics(preds[ref])["macro_f1_4"])
                m["vs_ref"]["diff4_ci"] = ci(diffs4)
        res["methods"][n] = m
    if args.second_labels:
        two = {}
        for line in open(args.second_labels, encoding="utf-8"):
            r = json.loads(line)
            if r["item"] in labels and not r.get("not_photo") and r.get("level"):
                two[r["item"]] = gold_of(r)
        pairs = [(labels[i]["level3"], two[i]["level3"]) for i in two]
        anc = [(labels[i]["anchored"], two[i]["anchored"]) for i in two]
        kb = []
        for draw in draws:
            pp = [(labels[i]["level3"], two[i]["level3"]) for f in draw for i in two if firm_of[i] == f]
            kb.append(kappa_w(pp))
        po = sum(a == b for a, b in anc) / len(anc) if anc else float("nan")
        pe = ((sum(a for a, _ in anc) / len(anc)) * (sum(b for _, b in anc) / len(anc)) +
              (1 - sum(a for a, _ in anc) / len(anc)) * (1 - sum(b for _, b in anc) / len(anc))) if anc else float("nan")
        res["annotators"] = {"file": args.second_labels, "photos": len(pairs), "kappa_w": r4(kappa_w(pairs)), "kappa_w_ci": ci(kb),
                             "same_level": sum(a == b for a, b in pairs), "anchor_kappa": r4((po - pe) / (1 - pe)) if pe == pe and pe < 1 else None}

    stamp = run_stamp()
    rpath = ROOT / "results" / f"eval_{args.split}_{args.tag}_{stamp}.json"
    k = 2
    while rpath.exists():
        rpath = ROOT / "results" / f"eval_{args.split}_{args.tag}_{stamp}_{k}.json"
        k += 1
    inputs = [lab_path] + [Path(f) for f in files.values()] + ([Path(args.second_labels)] if args.second_labels else [])
    rpath.write_text(json.dumps({"meta": run_meta("configs/default.yaml", inputs), **res}, ensure_ascii=False, indent=1), encoding="utf-8")
    write_html(ROOT / "reports" / f"{rpath.stem}.html", res)
    w = max(len(n) for n in res["methods"])
    for n, m in res["methods"].items():
        vs = m.get("vs_ref")
        print(f"{n:{w}s}  macroF1 {m['macro_f1']:.3f} {m['ci'].get('macro_f1')}  acc {m['accuracy']:.3f}  κw {m['kappa_w']:.3f}"
              + (f"  4級 {m['macro_f1_4']:.3f}  定錨F1 {m['anchor_f1']:.3f}" if "macro_f1_4" in m else "")
              + (f"  vs {vs['ref']}: {vs['macro_f1_diff']:+.3f} {vs['diff_ci']}" if vs else ""))
    print(f"wrote {rpath} and reports/{rpath.stem}.html")


def write_html(path, res):
    def f(x):
        return "—" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x))

    def iv(c):
        return f"<span class=mut>[{c[0]:.3f}, {c[1]:.3f}]</span>" if c else ""
    rows = []
    for n, m in res["methods"].items():
        vs = m.get("vs_ref") or {}
        rows.append(f"<tr><td>{html.escape(n)}</td><td>{m['photos']}</td><td>{f(m['macro_f1'])} {iv(m['ci'].get('macro_f1'))}</td>"
                    f"<td>{f(m['accuracy'])}</td><td>{f(m['direct_f1'])}</td><td>{f(m['kappa_w'])} {iv(m['ci'].get('kappa_w'))}</td>"
                    f"<td>{f(m.get('macro_f1_4'))} {iv(m['ci'].get('macro_f1_4'))}</td><td>{f(m.get('anchor_f1'))}</td>"
                    f"<td>{(f(vs['macro_f1_diff']) + ' ' + iv(vs['diff_ci'])) if vs else ''}</td>"
                    f"<td>{vs['mcnemar_direct_vs_rest']['p'] if vs else ''}</td></tr>")
    lab = "、".join(f"{ZH3[k]} {v}" for k, v in res["labels"].items())
    lab4 = "、".join(f"{k} {v}" for k, v in res["labels4"].items())
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>證據分級評估</title><style>
:root{{--bg:#fafafa;--fg:#222;--mut:#777;--line:#ddd}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1b1b;--fg:#eee;--mut:#999;--line:#444}}}}
body{{font-family:system-ui,"Microsoft JhengHei",sans-serif;margin:0 auto;padding:16px;background:var(--bg);color:var(--fg);max-width:1200px}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;font-size:13px}} td,th{{border:1px solid var(--line);padding:4px 6px;text-align:left;white-space:nowrap}}
.mut{{color:var(--mut);font-size:12px}}</style></head><body>
<h1 style="font-size:20px">證據分級評估（{'開發集' if res['split'] == 'dev' else '測試集'}）</h1>
<p>{res['photos_labelled']} 張照片、{res['firms']} 家公司。標註：{lab}；四級：{lab4}。括號是以公司為單位重抽 {res['bootstrap']['resamples']} 次的 95% 區間。
開發集的預測都是「換公司」的：門檻在其他公司上調好再套到這家（tune_evidence.py）。</p>
<div class=wrap><table><tr><th>方法</th><th>張數</th><th>三級 macro-F1</th><th>準確率</th><th>直接對應 F1</th><th>加權 κ</th><th>四級 macro-F1</th><th>定錨 F1</th><th>比參考方法多</th><th>McNemar p</th></tr>
{''.join(rows)}</table></div></body></html>"""
    path.write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
