"""Module 1: which text on the page describes the photo (PLAN §5 step 4, RQ1).

Candidates are the page's text boxes that are not menus, headers or footers (page_graph.py, is_running false);
the photo is the picture record of the crop (visible box). The comparison methods (PLAN L1-L5):
  L1 nearest   the body-text box closest to the photo (edge-to-edge distance)
  L2 column    the closest body-text box that shares at least 30% of the narrower width with the photo
               (same column); L1 when there is none
  L3 caption   the closest caption-role box (page_graph role 'caption'); nothing when the page has none
  L4 heading   the heading chain before the photo in reading order (h1, h2, h3: the last of each level), carried
               over from earlier pages with pictures (Pharos-ESG's section level; page_graph keeps only pages with
               pictures, so a heading on a page without pictures is not seen)
  L5 rules     the current extract_pairs.py context (overlay text, caption, local heading, heading, two paragraphs),
               mapped back to the boxes whose text it contains
The learned model (logistic regression on photo x box features, trained on annotation R development boxes) is
added once those labels exist; its features are written now (--features) so they can be checked.

Scoring against annotation R (--labels: a result of `annotate_export.py r-import`, or annotations/dev/R.jsonl):
box-level precision, recall and F1 over (photo, box) pairs, micro-averaged; photos the annotator marked as not a
photo are left out; '這頁沒有描述文字' photos have no gold box (any box chosen is a false positive); photos with
'描述文字的框有問題' are scored and also counted separately. Also: the share of photos where at least one chosen box is
a gold box. Development split only (CLAUDE.md rule 3).
Writes work/context/{method}_{tag}.jsonl (boxes and the assembled text per photo), results/context_link_{tag}_{stamp}.json.

  python src/context_link.py --crops annotations/qc/R_draft_crops.txt --tag draft20 --labels results/annotation_R_draft_import_20261019_1350.json
"""
import argparse
import collections
import csv
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp  # noqa: E402

METHODS = ["L1_nearest", "L2_column", "L3_caption", "L4_heading", "L5_rules"]
HEADINGS = ("h1", "h2", "h3")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").replace("臺", "台")
    return "".join(ch for ch in s if ch.isalnum())


def gap(a, b):
    dx = max(0.0, max(a[0] - b[2], b[0] - a[2]))
    dy = max(0.0, max(a[1] - b[3], b[1] - a[3]))
    return math.hypot(dx, dy)


def x_share(a, b):
    ov = min(a[2], b[2]) - max(a[0], b[0])
    w = min(a[2] - a[0], b[2] - b[0])
    return ov / w if w > 0 and ov > 0 else 0.0


def features(pic, box, page):
    """Photo x box features of PLAN step 4 (for the learned model)."""
    p, b = pic["visible_bbox"] or pic["bbox"], box["bbox"]
    H = page["height"] or 1.0
    inside = (b[0] >= p[0] - 1 and b[2] <= p[2] + 1 and b[1] >= p[1] - 1 and b[3] <= p[3] + 1)
    side = ("inside" if inside else "below" if b[1] >= p[3] - 1 else "above" if b[3] <= p[1] + 1
            else "right" if b[0] >= p[2] - 1 else "left" if b[2] <= p[0] + 1 else "overlap")
    return {"same_container": bool(set(pic.get("containers") or []) & set(box.get("containers") or [])),
            "on_photo": pic["idx"] in (box.get("on_pictures") or []), "side": side, "gap_over_height": round(gap(p, b) / H, 4),
            "x_share": round(x_share(p, b), 3),
            "edge_aligned": min(abs(p[0] - b[0]), abs(p[2] - b[2])) <= 2.0, "role": box["role"],
            "numbered": bool(re.match(r"^\s*[\(（]?\d+(\.\d+)*[\)）.、]?\s*\S", box.get("text") or "")),
            "order_gap": abs((box.get("order") or 0) - (pic.get("order") or 0)), "chars": box.get("chars") or 0}


def load_pages(report, cache={}):
    if report not in cache:
        cache[report] = [json.loads(l) for l in open(ROOT / "work" / "page_graph" / f"{report}.jsonl", encoding="utf-8")]
    return cache[report]


def heading_chain(report, page_no, pic_order):
    """The last h1, h2, h3 before the photo in reading order, carried over from earlier pages with pictures."""
    last = {}
    for pg in load_pages(report):
        if pg["page"] > page_no:
            break
        for b in sorted((b for b in pg["boxes"] if not b["is_running"] and b["role"] in HEADINGS), key=lambda b: b["order"]):
            if pg["page"] == page_no and b["order"] > pic_order:
                break
            last[b["role"]] = b
            for lower in HEADINGS[HEADINGS.index(b["role"]) + 1:]:   # a new h1 ends the h2 and h3 before it
                last.pop(lower, None)
    return [last[h] for h in HEADINGS if h in last]


def rules_boxes(rec, cands):
    """Boxes whose text the extract_pairs context contains (or that contain a field of it)."""
    fields = [rec.get("overlay_text", ""), rec.get("caption", ""), rec.get("local_heading", ""), rec.get("heading", "")]
    fields += [p.get("text", "") if isinstance(p, dict) else str(p) for p in (rec.get("paragraphs") or [])[:2]]
    fields = [norm(f) for f in fields if norm(f)]
    out = []
    for b in cands:
        t = norm(b.get("text"))
        if len(t) < 2:
            continue
        for f in fields:
            short, long_ = (t, f) if len(t) <= len(f) else (f, t)
            if t == f or (len(short) >= 4 and short in long_):   # 2-3 characters (「台泥」) match too much
                out.append(b)
                break
            if len(short) >= 4:
                bg = {short[i:i + 2] for i in range(len(short) - 1)}
                if sum(1 for x in bg if x in long_) / len(bg) >= 0.8:
                    out.append(b)
                    break
    return out


def predict(method, pic, page, cands, rec):
    p = pic["visible_bbox"] or pic["bbox"]
    body = [b for b in cands if b["role"] == "body"]
    if method == "L1_nearest":
        return sorted(body, key=lambda b: gap(p, b["bbox"]))[:1]
    if method == "L2_column":
        col = [b for b in body if x_share(p, b["bbox"]) >= 0.3]
        return sorted(col or body, key=lambda b: gap(p, b["bbox"]))[:1]
    if method == "L3_caption":
        return sorted((b for b in cands if b["role"] == "caption"), key=lambda b: gap(p, b["bbox"]))[:1]
    if method == "L4_heading":   # may include headings of earlier pages: they go into the text, not the box ids
        return heading_chain(page["report"], page["page"], pic.get("order") or 0)
    if method == "L5_rules":
        return rules_boxes(rec, cands)
    raise ValueError(method)


SIDES = ("inside", "below", "above", "right", "left", "overlap")
ROLES = ("body", "caption", "h1", "h2", "h3", "note", "garbled")
PART_LABEL = {"caption": "圖說", "h1": "標題", "h2": "標題", "h3": "標題", "body": "內文", "note": "註"}
PART_ORDER = ("圖上的字", "圖說", "標題", "內文", "註")


def parts_of(boxes, pic, here=None):
    """[(label, text)] for module 2's context, in extract_pairs' order (text on the photo, caption, headings, body,
    notes; reading order within each); garbled boxes are left out. `here`: the photo's page boxes (L4 also brings
    headings of earlier pages, whose on_pictures refer to other pictures)."""
    out = []
    for b in sorted(boxes, key=lambda b: b.get("order") or 0):
        if b.get("role") == "garbled":
            continue
        on = pic["idx"] in (b.get("on_pictures") or []) and (here is None or b in here)
        lab = "圖上的字" if on else PART_LABEL.get(b.get("role"), "內文")
        t = " ".join((b.get("text") or "").split())
        if t:
            out.append((lab, t))
    return sorted(out, key=lambda p: PART_ORDER.index(p[0]))


def lr_rows(feats, gold, item_of):
    """Photo x box rows for the logistic regression (the same recipe for training on dev and applying on test)."""
    by_photo = collections.defaultdict(list)
    for f in feats:
        by_photo[f["crop"]].append(f)
    rows = []
    for crop, fs in by_photo.items():
        g = gold.get(item_of.get(crop)) if gold is not None else {}
        if g is None:
            continue
        G = set(g.get("describing_boxes") or [])
        rank = {f["box"]: i for i, f in enumerate(sorted(fs, key=lambda f: f["gap_over_height"]))}
        for f in fs:
            v = [float(f["same_container"]), float(f["on_photo"]), f["gap_over_height"], min(f["gap_over_height"], 0.5),
                 f["x_share"], float(f["edge_aligned"]), float(f["numbered"]), math.log1p(f["order_gap"]),
                 math.log1p(f["chars"]), 1.0 / (1 + rank[f["box"]])]
            v += [float(f["side"] == s) for s in SIDES] + [float(f["role"] == r) for r in ROLES]
            rows.append({"crop": crop, "firm": crop.split("_")[0], "box": f["box"], "x": v, "y": int(f["box"] in G)})
    return rows


def apply_lr(feats, dev_result, dev_features, dev_labels, dev_items):
    """10/27 (test split): fit the final model on the development features and labels as train_lr does, check it
    against the coefficients recorded in `dev_result`, and apply it with the recorded cut to `feats`."""
    import numpy as np

    def fit_logreg(X, y, C=1.0, iters=100):
        """sklearn's LogisticRegression(C=1, class_weight='balanced') objective, Newton's method (convex: one optimum).
        10/27: sklearn's compiled parts are blocked on this machine; the coefficients are checked against its record."""
        n, d = X.shape
        w_pos, w_neg = n / (2 * y.sum()), n / (2 * (n - y.sum()))
        sw = np.where(y == 1, w_pos, w_neg)
        Xb = np.hstack([X, np.ones((n, 1))])
        beta = np.zeros(d + 1)
        reg = np.ones(d + 1) / C
        reg[-1] = 0.0   # the intercept is not penalised
        for _ in range(iters):
            z = Xb @ beta
            pr = 1.0 / (1.0 + np.exp(-z))
            grad = Xb.T @ (sw * (pr - y)) + reg * beta
            H = (Xb * (sw * pr * (1 - pr))[:, None]).T @ Xb + np.diag(reg)
            step = np.linalg.solve(H, grad)
            beta -= step
            if np.max(np.abs(step)) < 1e-10:
                break
        return beta[:-1], beta[-1]

    class _Scaler:
        def fit(self, X):
            self.mean_, self.scale_ = X.mean(axis=0), X.std(axis=0)
            self.scale_[self.scale_ == 0] = 1.0
            return self

        def transform(self, X):
            return (X - self.mean_) / self.scale_

    rec = json.load(open(dev_result, encoding="utf-8"))["lr_model"]
    dev_gold = {r["item"]: r for r in (json.loads(l) for l in open(dev_labels, encoding="utf-8") if l.strip())
                if r.get("status", "done") == "done" and not r.get("not_photo")}
    dev_item_of = {json.loads(l)["crop"]: json.loads(l)["item"] for l in open(dev_items, encoding="utf-8") if l.strip()}
    dev_feats = [json.loads(l) for l in open(dev_features, encoding="utf-8") if l.strip()]
    rows = lr_rows(dev_feats, dev_gold, dev_item_of)
    X = np.array([r["x"] for r in rows]); y = np.array([r["y"] for r in rows])
    sc = _Scaler().fit(X)
    coef_arr, intercept = fit_logreg(sc.transform(X), y)
    coef = [round(float(c), 4) for c in coef_arr]
    drift = max(abs(a - b) for a, b in zip(coef, rec["coef_standardised"]))
    if drift > 0.02 or len(rows) != rec["pairs"]:
        sys.exit(f"重訓的配對模型跟開發集紀錄不一樣（係數最大差 {drift:.4f}，配對 {len(rows)} vs {rec['pairs']}）")
    test_rows = lr_rows(feats, None, {})
    chosen = collections.defaultdict(list)
    if test_rows:
        prob = 1.0 / (1.0 + np.exp(-(sc.transform(np.array([r["x"] for r in test_rows])) @ coef_arr + intercept)))
        for r, pr in zip(test_rows, prob):
            if pr >= rec["cut"]:
                chosen[r["crop"]].append(r["box"])
    info = {"fitted_on": dev_result, "pairs": len(rows), "coef_max_abs_diff": round(drift, 5), "cut": rec["cut"],
            "intercept_diff": round(abs(float(intercept) - rec["intercept"]), 5), "solver": "numpy Newton (sklearn blocked 10/27)",
            "test_pairs": len(test_rows), "test_boxes_chosen": sum(len(v) for v in chosen.values())}
    return info, dict(chosen)


def train_lr(feats, gold, item_of):
    """The learned model of PLAN step 4: logistic regression on photo x box features (plus the box's distance rank
    on its page), scored leave-one-firm-out: for each firm, fit on the other firms' labelled photos, choose the
    probability cut that maximises box F1 on those firms, and apply both to the held-out firm. Returns the pooled
    scores and a model fitted on all labelled photos (coefficients, cut) for the record."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    by_photo = collections.defaultdict(list)
    for f in feats:
        by_photo[f["crop"]].append(f)
    rows = []
    for crop, fs in by_photo.items():
        g = gold.get(item_of.get(crop))
        if g is None:
            continue
        G = set(g.get("describing_boxes") or [])
        rank = {f["box"]: i for i, f in enumerate(sorted(fs, key=lambda f: f["gap_over_height"]))}
        for f in fs:
            v = [float(f["same_container"]), float(f["on_photo"]), f["gap_over_height"], min(f["gap_over_height"], 0.5),
                 f["x_share"], float(f["edge_aligned"]), float(f["numbered"]), math.log1p(f["order_gap"]),
                 math.log1p(f["chars"]), 1.0 / (1 + rank[f["box"]])]
            v += [float(f["side"] == s) for s in SIDES] + [float(f["role"] == r) for r in ROLES]
            rows.append({"crop": crop, "firm": crop.split("_")[0], "box": f["box"], "x": v, "y": int(f["box"] in G)})
    names = ["same_container", "on_photo", "gap_over_height", "gap_capped", "x_share", "edge_aligned", "numbered",
             "log_order_gap", "log_chars", "inverse_distance_rank"] + [f"side_{s}" for s in SIDES] + [f"role_{r}" for r in ROLES]
    X = np.array([r["x"] for r in rows])
    y = np.array([r["y"] for r in rows])

    def fit(idx):
        sc = StandardScaler().fit(X[idx])
        m = LogisticRegression(max_iter=2000, class_weight="balanced").fit(sc.transform(X[idx]), y[idx])
        return sc, m

    def best_cut(prob, idx):
        best, bf = 0.5, -1.0
        for c in np.arange(0.05, 0.96, 0.05):
            P = prob >= c
            tp = int((P & (y[idx] == 1)).sum()); fp = int((P & (y[idx] == 0)).sum()); fn = int((~P & (y[idx] == 1)).sum())
            f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
            if f1 > bf:
                best, bf = float(round(c, 2)), f1
        return best
    firms = sorted({r["firm"] for r in rows})
    pred = np.zeros(len(rows), dtype=bool)   # out of fold: each firm's boxes come from a model fitted without it
    for firm in firms:
        tr = np.array([i for i, r in enumerate(rows) if r["firm"] != firm])
        te = np.array([i for i, r in enumerate(rows) if r["firm"] == firm])
        if y[tr].sum() == 0 or len(te) == 0:
            continue
        sc, m = fit(tr)
        cut = best_cut(m.predict_proba(sc.transform(X[tr]))[:, 1], tr)
        pred[te] = m.predict_proba(sc.transform(X[te]))[:, 1] >= cut
    tp = int((pred & (y == 1)).sum()); fp = int((pred & (y == 0)).sum()); fn = int((~pred & (y == 1)).sum())
    photos = sorted({r["crop"] for r in rows})
    hit = sum(1 for c in photos if any(pred[i] and y[i] for i, r in enumerate(rows) if r["crop"] == c))
    pr = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    scores = {"photos": len(photos), "precision": round(pr, 4), "recall": round(rc, 4),
              "f1": round(2 * pr * rc / (pr + rc), 4) if pr + rc else 0.0, "any_hit": hit, "tp": tp, "fp": fp, "fn": fn,
              "evaluation": "leave-one-firm-out (cut chosen on the training firms)", "firms": len(firms)}
    sc, m = fit(np.arange(len(rows)))
    cut = best_cut(m.predict_proba(sc.transform(X))[:, 1], np.arange(len(rows)))
    model = {"features": names, "coef_standardised": [round(float(c), 4) for c in m.coef_[0]],
             "intercept": round(float(m.intercept_[0]), 4), "cut": cut, "pairs": len(rows), "positives": int(y.sum())}
    oof = collections.defaultdict(list)
    for i, r in enumerate(rows):
        if pred[i]:
            oof[r["crop"]].append(r["box"])
    return scores, model, {c: oof.get(c, []) for c in photos}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--crops")
    src.add_argument("--r-items", choices=["dev", "test"])
    ap.add_argument("--labels")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--features", action="store_true", help="also write the photo x box features")
    ap.add_argument("--train", action="store_true", help="with --labels: the learned model, leave-one-firm-out")
    ap.add_argument("--apply-model", help="test split: the development context_link result whose learned model is applied (no training)")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))
    sparse_max = cfg["page_graph"].get("sparse_max_chars", 20)
    if args.crops:
        crops = Path(args.crops).read_text(encoding="utf-8").split()
        item_of = {json.loads(l)["crop"]: json.loads(l)["item"] for l in open(ROOT / "work" / "annotation" / "R" / "draft" / "items.jsonl", encoding="utf-8")}
    else:
        rows = [json.loads(l) for l in open(ROOT / "annotations" / args.r_items / "R_items.jsonl", encoding="utf-8")]
        crops = [r["crop"] for r in rows]
        item_of = {r["crop"]: r["item"] for r in rows}
    split = {}
    with open(ROOT / "work" / "photo_filter.csv", encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if d["crop"] in set(crops):
                split[d["crop"]] = (d["split"], d["report"], int(d["page"]))
    if any(s[0] != "dev" for s in split.values()):   # the test split: frozen settings only (CLAUDE.md rule 3)
        import frozen
        frozen.check("test", cfg)
        if args.train:
            sys.exit("測試集不訓練配對模型：用 --apply-model 套開發集訓練好的")
    recs = {}
    for rep in sorted({s[1] for s in split.values()}):
        for line in open(ROOT / "work" / "extract" / f"{rep}_pairs.jsonl", encoding="utf-8"):
            d = json.loads(line)
            if d["crop"] in split:
                recs[d["crop"]] = d

    out = {m: [] for m in METHODS}
    feats = []
    sparse = set()
    page_of = {}
    for c in crops:
        _, rep, pno = split[c]
        page = next(pg for pg in load_pages(rep) if pg["page"] == pno)
        pic = next(p for p in page["pictures"] if p["crop"] == c)
        cands = [b for b in page["boxes"] if not b["is_running"]]
        page_of[c] = (pic, {b["id"]: b for b in page["boxes"]})
        if page.get("text_chars_nonrunning", sum(b.get("chars") or 0 for b in cands)) <= sparse_max:
            sparse.add(c)
        for m in METHODS:
            got = predict(m, pic, page, cands, recs[c])
            out[m].append({"crop": c, "item": item_of.get(c), "boxes": [b["id"] for b in got if b in cands],
                           "page_of_boxes": [pno if b in cands else None for b in got],
                           "text": " / ".join(" ".join((b.get("text") or "").split()) for b in got)[:900],
                           "parts": parts_of(got, pic, cands)})
        if args.features or args.train or args.apply_model:
            for b in cands:
                feats.append({"crop": c, "box": b["id"], **features(pic, b, page)})

    Path(ROOT / "work" / "context").mkdir(parents=True, exist_ok=True)
    for m in METHODS:
        with open(ROOT / "work" / "context" / f"{m}_{args.tag}.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for r in out[m]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if args.features:
        with open(ROOT / "work" / "context" / f"features_{args.tag}.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for r in feats:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    res = {"tag": args.tag, "photos": len(crops), "sparse_pages": len(sparse), "methods": METHODS,
           "boxes_per_photo": {m: round(sum(len(r["boxes"]) for r in out[m]) / len(crops), 2) for m in METHODS},
           "empty": {m: sum(1 for r in out[m] if not r["boxes"]) for m in METHODS}}
    if args.labels:
        lab = json.loads(Path(args.labels).read_text(encoding="utf-8")) if args.labels.endswith(".json") else \
            {"rows": [json.loads(l) for l in open(args.labels, encoding="utf-8")]}
        gold = {r["item"]: r for r in lab["rows"] if r.get("status", "done") == "done" and not r.get("not_photo")}
        scores = {}
        for m in METHODS:
            tp = fp = fn = hit = n = 0
            prob = {"tp": 0, "fp": 0, "fn": 0}
            for r in out[m]:
                g = gold.get(r["item"])
                if g is None:
                    continue
                G, P = set(g.get("describing_boxes") or []), set(r["boxes"])
                n += 1
                tp += len(G & P)
                fp += len(P - G)
                fn += len(G - P)
                hit += bool(G & P)
                if g.get("text_box_problem"):
                    prob["tp"] += len(G & P)
                    prob["fp"] += len(P - G)
                    prob["fn"] += len(G - P)
            pr = tp / (tp + fp) if tp + fp else 0.0
            rc = tp / (tp + fn) if tp + fn else 0.0
            scores[m] = {"photos": n, "precision": round(pr, 4), "recall": round(rc, 4),
                         "f1": round(2 * pr * rc / (pr + rc), 4) if pr + rc else 0.0, "any_hit": hit,
                         "tp": tp, "fp": fp, "fn": fn, "text_box_problem_photos": prob}
        res["vs_labels"] = {"labels": args.labels, "gold_photos": len(gold),
                            "gold_boxes": sum(len(g.get("describing_boxes") or []) for g in gold.values()),
                            "no_text_photos": sum(1 for g in gold.values() if g.get("no_text")), "scores": scores}
        extra = {"gold": {c: [b for b in (gold[item_of[c]].get("describing_boxes") or []) if b in page_of[c][1]]
                          for c in crops if item_of.get(c) in gold}}   # Eden's boxes: module 2 with the right text
        if args.train:
            res["vs_labels"]["scores"]["M_lr"], res["lr_model"], extra["M_lr"] = train_lr(feats, gold, item_of)
        if args.apply_model:   # 10/27: the development model applied to the test photos; scored like the other methods
            res["lr_applied"], extra["M_lr"] = apply_lr(feats, args.apply_model, str(ROOT / "work" / "context" / "features_dev140.jsonl"),
                                                        str(ROOT / "annotations" / "dev" / "R.jsonl"), str(ROOT / "annotations" / "dev" / "R_items.jsonl"))
            tp = fp = fn = hit = n = 0
            for c in crops:
                g = gold.get(item_of.get(c))
                if g is None:
                    continue
                G, P = set(g.get("describing_boxes") or []), set(extra["M_lr"].get(c, []))
                n += 1; tp += len(G & P); fp += len(P - G); fn += len(G - P); hit += bool(G & P)
            pr = tp / (tp + fp) if tp + fp else 0.0
            rc = tp / (tp + fn) if tp + fn else 0.0
            res["vs_labels"]["scores"]["M_lr"] = {"photos": n, "precision": round(pr, 4), "recall": round(rc, 4),
                                                  "f1": round(2 * pr * rc / (pr + rc), 4) if pr + rc else 0.0, "any_hit": hit,
                                                  "tp": tp, "fp": fp, "fn": fn, "evaluation": "development model applied (no fitting on this split)"}
        for name, chosen in extra.items():   # same shape as the L1-L5 files (labelled photos only)
            with open(ROOT / "work" / "context" / f"{name}_{args.tag}.jsonl", "w", encoding="utf-8", newline="\n") as f:
                for c in crops:
                    if c not in chosen:
                        continue
                    pic, boxes = page_of[c]
                    got = [boxes[b] for b in chosen[c]]
                    f.write(json.dumps({"crop": c, "item": item_of.get(c), "boxes": chosen[c],
                                        "text": " / ".join(" ".join((b.get("text") or "").split()) for b in got)[:900],
                                        "parts": parts_of(got, pic)}, ensure_ascii=False) + "\n")
        res["context_files"] = [f"work/context/{m}_{args.tag}.jsonl" for m in METHODS + list(extra)]
    stamp = run_stamp()
    rpath = ROOT / "results" / f"context_link_{args.tag}_{stamp}.json"
    k = 2
    while rpath.exists():   # never overwrite a result (CLAUDE.md rule 2), even within the same minute
        rpath = ROOT / "results" / f"context_link_{args.tag}_{stamp}_{k}.json"
        k += 1
    rpath.write_text(json.dumps({"meta": run_meta("configs/default.yaml", [ROOT / "work" / "photo_filter.csv"]
                                                  + ([Path(args.labels)] if args.labels else [])), **res},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False)[:2500])
    print(f"wrote {rpath}")


if __name__ == "__main__":
    main()
