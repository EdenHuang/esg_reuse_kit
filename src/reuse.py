"""Cross-year photo reuse (PLAN §5 step 6, Table 7): candidates by global descriptors, verified by local matching.

For each firm and each photo of year t (photo filter keep == 1), the photos of year t-1 of the same firm are
ranked by DINOv2 and by SSCD cosine (with the left-right mirror of the query: the higher of the two); the top
`reuse.topk` of each list are candidates. A candidate is verified with SuperPoint + LightGlue and a MAGSAC
homography (OpenCV USAC_MAGSAC, 8 px, as in check_models.py), first in the orientation (mirrored or not) that SSCD
prefers and, when that fails, also in the other one: the pair is "the same photo or the same shot" when
inliers >= reuse.min_inliers and coverage >=
reuse.min_coverage (coverage = area of the convex hull of the inliers / image area, the smaller of the two).

Evaluation on the development synthetic pairs (data/synthetic/dev/pairs.jsonl, make_synthetic.py) compares
  MD5      bit-identical embedded files (a synthetic copy is a new file, so 0 recall by construction)
  pHash    64-bit DCT hash, Hamming distance (min over mirror)
  DINOv2   cosine, SSCD cosine (max over mirror)
  ours     LightGlue + MAGSAC inliers and coverage at the configured thresholds
with average precision (threshold-free) and precision / recall / F1, at the configured thresholds for ours and
at the best-F1 threshold on these dev pairs for the score baselines (so the baselines are optimistic here).
It also reports retrieval recall@k: is the original among the top-k of the 1,627 dev photos for the copy?
Only the development split is used (the test split waits for 11/2, CLAUDE.md rule 3).

Every verification result is cached in work/reuse/verify_cache.jsonl (rule 9: changing a threshold never re-runs
the models). Writes work/reuse/{split}_candidates.jsonl, results/reuse_{split}_{stamp}.json and
reports/reuse_{split}_{stamp}.html.

  python src/reuse.py --split dev
"""
import argparse
import base64
import collections
import csv
import hashlib
import html
import io
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("TORCH_HOME", str(ROOT / "work" / "models" / "torch"))
sys.path.insert(0, str(ROOT / "src"))
import features  # noqa: E402
from meta import package_versions, run_meta, run_stamp, sha256_file  # noqa: E402

VERIFY_VERSION = 1   # bump when the verification changes (keypoints, matcher, RANSAC), so the cache is redone
MAX_KEYPOINTS = 2048
RANSAC_PX = 8.0


def load_yaml(path):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- photos
def photo_pool(split, filter_csv, extract_dir):
    rows = []
    with open(filter_csv, encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if d["split"] == split and d["keep"] == "1":
                rows.append({"crop": d["crop"], "report": d["report"], "firm": d["firm_id"], "year": int(d["year"]),
                             "page": int(d["page"])})
    md5 = {}
    for rep in sorted({r["report"] for r in rows}):
        with open(Path(extract_dir) / f"{rep}_pairs.jsonl", encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                md5[rec["crop"]] = [m for m in rec.get("layer_md5", []) if m]
    for r in rows:
        r["md5"] = md5.get(r["crop"], [])
    return sorted(rows, key=lambda r: r["crop"])


def open_rgb(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.convert("RGB")


def phash(im):
    """64-bit perceptual hash (DCT of a 32 x 32 grey image, top-left 8 x 8 without DC, median threshold)."""
    import cv2
    import numpy as np
    g = np.asarray(im.convert("L").resize((32, 32)), dtype=np.float32)
    d = cv2.dct(g)[:8, :8].flatten()
    med = np.median(d[1:])
    return int("".join("1" if v > med else "0" for v in d), 2)


def ham(a, b):
    return bin(a ^ b).count("1")


# ---------------------------------------------------------------- local verification (cached)
class Verifier:
    def __init__(self, cache_path, min_inliers, min_coverage):
        self.cache_path = Path(cache_path)
        self.min_inliers, self.min_coverage = min_inliers, min_coverage
        self.cache = {}
        if self.cache_path.exists():
            for line in open(self.cache_path, encoding="utf-8"):
                r = json.loads(line)
                if r.get("v") == VERIFY_VERSION:
                    self.cache[r["key"]] = r
        self.feats = {}
        self.models = None
        self.new = 0

    def _load(self):
        if self.models is None:
            from lightglue import LightGlue, SuperPoint
            dev = features.device()
            self.models = (SuperPoint(max_num_keypoints=MAX_KEYPOINTS).eval().to(dev),
                           LightGlue(features="superpoint").eval().to(dev), dev)
        return self.models

    def _features(self, img_id, loader, mirror):
        k = (img_id, mirror)
        if k not in self.feats:
            import numpy as np
            import torch
            from lightglue.utils import numpy_image_to_torch
            from PIL import ImageOps
            ext, _, dev = self._load()
            im = loader()
            if mirror:
                im = ImageOps.mirror(im)
            with torch.no_grad():
                f = ext.extract(numpy_image_to_torch(np.asarray(im)).to(dev))
            self.feats[k] = {kk: v.half().cpu() if v.dtype == torch.float32 and kk != "image_size" else v.cpu()
                             for kk, v in f.items()}
            self.feats[k]["_size"] = im.size
        return self.feats[k]

    def verify(self, id0, load0, id1, load1, mirror0=False):
        key = f"{id0}|{id1}|{int(mirror0)}"
        if key in self.cache:
            return self.cache[key]
        import cv2
        import numpy as np
        import torch
        from lightglue.utils import rbd
        _, matcher, dev = self._load()
        f0 = self._features(id0, load0, mirror0)
        f1 = self._features(id1, load1, False)

        def to_dev(f):
            return {k: (v.float() if v.dtype == torch.float16 else v).to(dev) for k, v in f.items() if k != "_size"}
        with torch.no_grad():
            m = matcher({"image0": to_dev(f0), "image1": to_dev(f1)})
        m = rbd(m)
        k0 = rbd({"k": f0["keypoints"]})["k"].float().numpy()[m["matches"][:, 0].cpu().numpy()]
        k1 = rbd({"k": f1["keypoints"]})["k"].float().numpy()[m["matches"][:, 1].cpu().numpy()]
        inl, cov = 0, 0.0
        if len(k0) >= 4:
            _, mask = cv2.findHomography(k0, k1, cv2.USAC_MAGSAC, RANSAC_PX)
            if mask is not None and mask.sum() >= 4:
                mask = mask.ravel().astype(bool)
                inl = int(mask.sum())
                covs = []
                for pts, (w, h) in ((k0[mask], f0["_size"]), (k1[mask], f1["_size"])):
                    hull = cv2.convexHull(pts.astype(np.float32))
                    covs.append(float(cv2.contourArea(hull)) / max(1.0, w * h))
                cov = round(min(covs), 4)
        r = {"key": key, "v": VERIFY_VERSION, "matches": int(len(k0)), "inliers": inl, "coverage": cov}
        self.cache[key] = r
        with open(self.cache_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(r) + "\n")
        self.new += 1
        return r

    def same(self, r):
        return r["inliers"] >= self.min_inliers and r["coverage"] >= self.min_coverage

    def verify_either(self, id0, load0, id1, load1, mirror_first):
        """Verify in the orientation the global vectors prefer; when that fails, also in the other one and keep the
        one with more inliers. SSCD is close to mirror-invariant, so its preference is wrong on some pairs
        (10/18 trial: 236 of 1,000 synthetic positives missed, most of them in the wrong orientation)."""
        v = self.verify(id0, load0, id1, load1, mirror0=mirror_first)
        if self.same(v):
            return v, mirror_first
        w = self.verify(id0, load0, id1, load1, mirror0=not mirror_first)
        return (w, not mirror_first) if w["inliers"] > v["inliers"] else (v, mirror_first)


# ---------------------------------------------------------------- metrics
def average_precision(scores, labels):
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    tp = fp = 0
    n_pos = sum(labels)
    ap, prev_r = 0.0, 0.0
    for i in order:
        if labels[i]:
            tp += 1
        else:
            fp += 1
        r, p = tp / n_pos, tp / (tp + fp)
        if labels[i]:
            ap += (r - prev_r) * p
            prev_r = r
    return round(ap, 4)


def prf(pred, labels):
    tp = sum(1 for p, y in zip(pred, labels) if p and y)
    fp = sum(1 for p, y in zip(pred, labels) if p and not y)
    fn = sum(1 for p, y in zip(pred, labels) if not p and y)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4), "tp": tp, "fp": fp, "fn": fn}


def best_f1(scores, labels):
    best = None
    for t in sorted(set(scores)):
        m = prf([s >= t for s in scores], labels)
        if best is None or m["f1"] > best[1]["f1"]:
            best = (t, m)
    return {"threshold": round(best[0], 4), **best[1]}


# ---------------------------------------------------------------- HTML
CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#666;--line:#ddd;--card:#f5f5f5;--ok:#137333;--bad:#b3261e}
@media (prefers-color-scheme: dark){:root{--bg:#161616;--fg:#eee;--mut:#aaa;--line:#333;--card:#222;--ok:#6dd58c;--bad:#ff8a80}}
body{background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;margin:0 auto;max-width:1200px;padding:16px}
.mut{color:var(--mut)} table{border-collapse:collapse;margin:8px 0 18px;font-variant-numeric:tabular-nums} th,td{border-bottom:1px solid var(--line);padding:4px 10px;text-align:right}
th:first-child,td:first-child{text-align:left} .wrap{overflow-x:auto} .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px}
.pair{background:var(--card);border-radius:10px;padding:8px} .pair .two{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.pair img{width:100%;height:150px;object-fit:contain;background:#0001;border-radius:6px} .pair p{margin:4px 0 0;font-size:13px}
.note{background:var(--card);border-radius:8px;padding:10px 14px}
"""


def thumb(im, size=320):
    im = im.copy()
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=75)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def card(im0, im1, text, sub=""):
    e = html.escape
    return (f'<div class="pair"><div class="two"><img src="{thumb(im0)}" alt=""><img src="{thumb(im1)}" alt=""></div>'
            f'<p>{e(text)}</p><p class="mut">{e(sub)}</p></div>')


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="dev", choices=["dev", "test"], help="test: only after configs/frozen.yaml (rule 3)")
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--dev-result", help="test split: the development reuse result whose score-baseline cut-offs are applied")
    args = ap.parse_args()
    cfg = load_yaml(ROOT / args.config)
    rc = cfg["reuse"]
    dev_cuts = None
    if args.split == "test":   # 10/27: frozen settings; baselines keep the cut-offs chosen on the development pairs
        import frozen
        if not frozen.check("test", cfg):
            sys.exit("測試集要等 configs/frozen.yaml（鐵則 3）")
        if not args.dev_result:
            sys.exit("測試集要給 --dev-result（開發集的 reuse 結果檔，比較方法的門檻從那裡來）")
        dev_cuts = {m: v["threshold"] for m, v in json.load(open(ROOT / args.dev_result, encoding="utf-8"))["synthetic"]["methods"].items()
                    if isinstance(v.get("threshold"), (int, float))}
    crop_dir = ROOT / cfg["paths"]["extract"] / "crops"
    work = ROOT / cfg["paths"]["reuse"]
    work.mkdir(parents=True, exist_ok=True)
    filter_csv = ROOT / "work" / "photo_filter.csv"
    t_start = time.time()
    pool = photo_pool(args.split, filter_csv, ROOT / cfg["paths"]["extract"])
    crops = [r["crop"] for r in pool]
    idx = {c: i for i, c in enumerate(crops)}
    print(f"{args.split}：照片 {len(pool)} 張、{len({r['firm'] for r in pool})} 家")
    vec = {m: features.embed_crops(crops, m, crop_dir) for m in ("dinov2", "sscd")}
    vflip = {m: features.embed_crops(crops, m, crop_dir, flip=True) for m in ("dinov2", "sscd")} if rc["use_flip"] else None
    ver = Verifier(work / "verify_cache.jsonl", rc["min_inliers"], rc["min_coverage"])
    load_crop = lambda c: (lambda: open_rgb(crop_dir / c))  # noqa: E731

    # ---- 1. real candidates: same firm, year t against year t - 1
    import numpy as np
    by_fy = collections.defaultdict(list)
    for r in pool:
        by_fy[(r["firm"], r["year"])].append(r)
    cand_rows = []
    years = sorted({r["year"] for r in pool})
    for (firm, year), qs in sorted(by_fy.items()):
        for back in rc["years_back"]:
            db = by_fy.get((firm, year - back), [])
            if not db:
                continue
            di = np.array([idx[r["crop"]] for r in db])
            for q in qs:
                qi = idx[q["crop"]]
                ranks, sims, flips = {}, {}, {}
                for m in ("dinov2", "sscd"):
                    s = vec[m][di] @ vec[m][qi]
                    sf = vec[m][di] @ vflip[m][qi] if vflip else s
                    best = np.maximum(s, sf)
                    order = np.argsort(-best)[:rc["topk"]]
                    for rank, j in enumerate(order, 1):
                        c = db[j]["crop"]
                        ranks.setdefault(c, {})[m] = rank
                        sims.setdefault(c, {})[m] = round(float(best[j]), 4)
                        if m == "sscd":
                            flips[c] = bool(sf[j] > s[j])
                for c in ranks:
                    if c not in flips:  # only in DINOv2's top-k: SSCD decides the orientation
                        j = idx[c]
                        flips[c] = bool(vec["sscd"][j] @ vflip["sscd"][qi] > vec["sscd"][j] @ vec["sscd"][qi]) if vflip else False
                    v, mir = ver.verify_either(q["crop"], load_crop(q["crop"]), c, load_crop(c), flips[c])
                    md5_same = bool(set(q["md5"]) & set(pool[idx[c]]["md5"]))
                    cand_rows.append({"firm": firm, "query": q["crop"], "year": year, "candidate": c, "back": back,
                                      "rank": ranks[c], "sim": sims[c], "mirror": mir, "mirror_by_sscd": flips[c],
                                      **{k: v[k] for k in ("matches", "inliers", "coverage")},
                                      "same": ver.same(v), "md5_same": md5_same})
        print(f"[reuse] {firm} {year}: {len(qs)} 張，候選累計 {len(cand_rows)}，新驗證 {ver.new}", flush=True)
    cand_path = work / f"{args.split}_candidates.jsonl"
    with open(cand_path, "w", encoding="utf-8", newline="\n") as f:
        for r in cand_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    same_pairs = [r for r in cand_rows if r["same"]]
    q_with = {r["query"] for r in same_pairs}
    n_query = sum(len(qs) for (firm, year), qs in by_fy.items() if any(by_fy.get((firm, year - b)) for b in rc["years_back"]))
    real = {"queries": n_query, "candidate_pairs": len(cand_rows), "verified_pairs": len(same_pairs),
            "queries_with_a_reuse": len(q_with), "share_of_queries": round(len(q_with) / n_query, 4) if n_query else None,
            "verified_md5_identical": sum(r["md5_same"] for r in same_pairs),
            "md5_identical_not_verified": sum(1 for r in cand_rows if r["md5_same"] and not r["same"]),
            "by_firm": {f: sum(1 for r in same_pairs if r["firm"] == f) for f in sorted({r["firm"] for r in pool})}}

    # ---- 2. synthetic pairs (dev): precision and recall
    syn_path = ROOT / cfg["paths"]["synthetic"] / args.split / "pairs.jsonl"
    syn = [json.loads(l) for l in open(syn_path, encoding="utf-8")]
    syn_dir = syn_path.parent
    b_is_file = lambda r: r["label"] == 1  # noqa: E731  positives: b is a synthetic copy on disk
    pos = [r for r in syn if r["label"] == 1]
    copy_ims = {r["pair"]: open_rgb(syn_dir / r["b"]) for r in pos}
    cv = {m: features.embed_images([copy_ims[r["pair"]] for r in pos], m) for m in ("dinov2", "sscd")}
    cvf = {m: features.embed_images([copy_ims[r["pair"]] for r in pos], m, flip=True) for m in ("dinov2", "sscd")}
    pos_i = {r["pair"]: k for k, r in enumerate(pos)}
    ph = {}

    def phash_of(key, im_loader):
        if key not in ph:
            from PIL import ImageOps
            im = im_loader()
            ph[key] = (phash(im), phash(ImageOps.mirror(im)))
        return ph[key]

    scores = {"md5": [], "phash": [], "dinov2": [], "sscd": [], "ours_inliers": [], "ours": []}
    labels, details = [], []
    for r in syn:
        a = r["a"]
        ai = idx[a]
        if b_is_file(r):
            k = pos_i[r["pair"]]
            bvec = {m: (cv[m][k], cvf[m][k]) for m in ("dinov2", "sscd")}
            b_id, b_load = f"syn:{r['pair']}", (lambda p=r["pair"]: copy_ims[p])
            md5_same = False  # a synthetic copy is a new file
        else:
            bj = idx[r["b"]]
            bvec = {m: (vec[m][bj], vflip[m][bj]) for m in ("dinov2", "sscd")}
            b_id, b_load = r["b"], load_crop(r["b"])
            md5_same = bool(set(pool[ai]["md5"]) & set(pool[bj]["md5"]))
        sims = {m: float(max(vec[m][ai] @ bvec[m][0], vec[m][ai] @ bvec[m][1])) for m in ("dinov2", "sscd")}
        mirror = bool(vec["sscd"][ai] @ bvec["sscd"][1] > vec["sscd"][ai] @ bvec["sscd"][0])
        # verify the copy against the original; the mirror is applied to the copy's side (SSCD's choice first)
        v, _ = ver.verify_either(b_id, b_load, a, load_crop(a), mirror)
        pa = phash_of(a, load_crop(a))
        pb = phash_of(b_id, b_load)
        dist = min(ham(pa[0], pb[0]), ham(pa[0], pb[1]))
        scores["md5"].append(1.0 if md5_same else 0.0)
        scores["phash"].append(1 - dist / 64)
        scores["dinov2"].append(sims["dinov2"])
        scores["sscd"].append(sims["sscd"])
        scores["ours_inliers"].append(float(v["inliers"]))
        scores["ours"].append(1.0 if ver.same(v) else 0.0)
        labels.append(r["label"])
        details.append({"pair": r["pair"], "label": r["label"], "a": a, "b": r["b"], "transforms": [t["name"] for t in r.get("transforms", [])],
                        "inliers": v["inliers"], "coverage": v["coverage"], "same": ver.same(v), "phash_dist": dist,
                        "dinov2": round(sims["dinov2"], 4), "sscd": round(sims["sscd"], 4)})
    methods = {
        "md5": {"ap": None, **prf([s >= 1 for s in scores["md5"]], labels), "note": "bit-identical file; 0 recall on edited copies by construction"},
        **{m: ({"ap": average_precision(scores[m], labels), **best_f1(scores[m], labels), "threshold_from": "best F1 on dev"} if dev_cuts is None else
                {"ap": average_precision(scores[m], labels), "threshold": dev_cuts[m], **prf([s >= dev_cuts[m] for s in scores[m]], labels),
                 "threshold_from": f"development pairs ({args.dev_result})"}) for m in ("phash", "dinov2", "sscd")},
        "ours": {"ap": average_precision(scores["ours_inliers"], labels), **prf([s >= 1 for s in scores["ours"]], labels),
                 "threshold": {"min_inliers": rc["min_inliers"], "min_coverage": rc["min_coverage"]}, "threshold_from": "configs (初值)"},
    }
    by_tf = {}
    for t in ("crop", "flip", "color", "text", "collage", "jpeg"):
        ds = [d for d in details if d["label"] == 1 and t in d["transforms"]]
        by_tf[t] = {"positives": len(ds), "ours_recall": round(sum(d["same"] for d in ds) / len(ds), 4) if ds else None}
    # retrieval recall@k: the copy as the query, all dev photos as the database
    rec = {}
    for m in ("dinov2", "sscd"):
        hits = 0
        for r in pos:
            k = pos_i[r["pair"]]
            s = np.maximum(vec[m] @ cv[m][k], vec[m] @ cvf[m][k])
            top = np.argsort(-s)[:rc["topk"]]
            hits += idx[r["a"]] in set(top.tolist())
        rec[m] = round(hits / len(pos), 4)
    union_hits = 0
    for r in pos:
        k = pos_i[r["pair"]]
        tops = set()
        for m in ("dinov2", "sscd"):
            s = np.maximum(vec[m] @ cv[m][k], vec[m] @ cvf[m][k])
            tops |= set(np.argsort(-s)[:rc["topk"]].tolist())
        union_hits += idx[r["a"]] in tops
    rec["union"] = round(union_hits / len(pos), 4)

    res = {"split": args.split, "photos": len(pool), "firms": len({r["firm"] for r in pool}),
           "photo_threshold": cfg["photo_filter"]["photo_threshold"], "reuse_config": rc,
           "verify": {"version": VERIFY_VERSION, "max_keypoints": MAX_KEYPOINTS, "ransac": f"cv2.USAC_MAGSAC {RANSAC_PX} px",
                      "new_verifications": ver.new, "cache": str((work / 'verify_cache.jsonl').relative_to(ROOT))},
           "models": {"dinov2": features.model_info("dinov2"), "sscd": features.model_info("sscd"),
                      "lightglue": package_versions(("lightglue", "opencv-python", "torch"))},
           "real": real, "candidates_file": str(cand_path.relative_to(ROOT)), "candidates_sha256": sha256_file(cand_path),
           "synthetic": {"pairs_file": str(syn_path.relative_to(ROOT)), "pairs_sha256": sha256_file(syn_path),
                         "positives": sum(labels), "negatives": len(labels) - sum(labels), "methods": methods,
                         "ours_recall_by_transform": by_tf, "retrieval_recall_at_k": {"k": rc["topk"], **rec}},
           "seconds": round(time.time() - t_start)}
    stamp = run_stamp()
    rp = ROOT / "results" / f"reuse_{args.split}_{stamp}.json"
    k = 2
    while rp.exists():   # never overwrite a result (CLAUDE.md rule 2)
        stamp = f"{run_stamp()}_{k}"
        rp = ROOT / "results" / f"reuse_{args.split}_{stamp}.json"
        k += 1
    rp.write_text(json.dumps({"meta": run_meta(args.config, [str(filter_csv), str(syn_path), str(cand_path)],
                                               packages=("pymupdf", "pillow", "numpy", "torch", "opencv-python", "lightglue")),
                              **res}, ensure_ascii=False, indent=1), encoding="utf-8")
    write_html(ROOT / "reports" / f"reuse_{args.split}_{stamp}.html", res, details, cand_rows, crop_dir, syn_dir, copy_ims)
    print(json.dumps({"real": {k: v for k, v in real.items() if k != "by_firm"}, "methods": methods,
                      "retrieval": res["synthetic"]["retrieval_recall_at_k"]}, ensure_ascii=False))
    print(f"wrote {cand_path}, {rp} and reports/reuse_{args.split}_{stamp}.html")


def write_html(path, res, details, cand_rows, crop_dir, syn_dir, copy_ims):
    e = html.escape
    rng = random.Random("2026:reuse:html")
    syn = res["synthetic"]
    name = {"md5": "MD5（同一個檔）", "phash": "感知雜湊", "dinov2": "DINOv2 餘弦", "sscd": "SSCD 餘弦", "ours": "本文（LightGlue＋MAGSAC）"}
    rows = ""
    for k, m in syn["methods"].items():
        thr = m.get("threshold")
        thr = ("內點 ≥ {min_inliers}、涵蓋 ≥ {min_coverage}".format(**thr) if isinstance(thr, dict) else ("—" if thr is None else f"{thr:g}"))
        ap_txt = "—" if m["ap"] is None else f"{m['ap']:.3f}"
        rows += (f"<tr><td>{name[k]}</td><td>{ap_txt}</td>"
                 f"<td>{m['precision']:.3f}</td><td>{m['recall']:.3f}</td><td>{m['f1']:.3f}</td><td>{e(thr)}</td></tr>")
    tf_zh = {"crop": "裁切", "flip": "翻轉", "color": "調色", "text": "壓字", "collage": "拼貼", "jpeg": "JPEG 重壓"}
    tf = ""
    for t, v in syn["ours_recall_by_transform"].items():
        r_txt = "—" if v["ours_recall"] is None else f"{v['ours_recall']:.3f}"
        tf += f"<tr><td>{tf_zh[t]}</td><td>{v['positives']}</td><td>{r_txt}</td></tr>"
    rr = syn["retrieval_recall_at_k"]
    real = res["real"]
    fn = [d for d in details if d["label"] == 1 and not d["same"]]
    fp = [d for d in details if d["label"] == 0 and d["same"]]

    def syn_card(d):
        a = open_rgb(Path(crop_dir) / d["a"])
        b = copy_ims[d["pair"]] if d["label"] == 1 else open_rgb(Path(crop_dir) / d["b"])
        tf_txt = "、".join(tf_zh[t] for t in d["transforms"]) or "不同公司的兩張"
        return card(a, b, f"{tf_txt}｜內點 {d['inliers']}、涵蓋 {d['coverage']:.2f}", f"SSCD {d['sscd']:.2f}、DINOv2 {d['dinov2']:.2f}、pHash 距離 {d['phash_dist']}")

    def real_card(r):
        return card(open_rgb(Path(crop_dir) / r["query"]), open_rgb(Path(crop_dir) / r["candidate"]),
                    f"{r['query']} ← {r['candidate']}｜內點 {r['inliers']}、涵蓋 {r['coverage']:.2f}" + ("｜MD5 相同" if r["md5_same"] else ""),
                    f"SSCD {r['sim'].get('sscd', '—')}、DINOv2 {r['sim'].get('dinov2', '—')}" + ("｜翻轉" if r["mirror"] else ""))
    same = [r for r in cand_rows if r["same"]]
    near = sorted((r for r in cand_rows if not r["same"] and r["inliers"] >= 10), key=lambda r: -r["inliers"])[:12]
    parts = [f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>跨年重用試跑 {res['split']}</title><style>{CSS}</style></head><body>
<h1>跨年重用試跑：開發集 {res['firms']} 家（PLAN §5 步驟 6，表 7）</h1>
<div class="note"><b>結果：</b>照片 {res['photos']} 張（照片過濾留下的，門檻還是初值 {res['photo_threshold']}）。同一家公司 t 年的每張照片，在 t−1 年的照片裡用 DINOv2、SSCD 各取前 {res['reuse_config']['topk']} 名（含左右翻轉），
再用 LightGlue＋MAGSAC 驗證：內點 ≥ {res['reuse_config']['min_inliers']}、涵蓋比例 ≥ {res['reuse_config']['min_coverage']} 算「同一張或同一畫面」（門檻是初值）。
<br>真實報告：{real['queries']} 張查詢照片、{real['candidate_pairs']} 組候選，驗證通過 <b>{real['verified_pairs']} 組</b>，涉及 {real['queries_with_a_reuse']} 張照片（{100 * (real['share_of_queries'] or 0):.1f}%）；其中 MD5 完全相同的 {real['verified_md5_identical']} 組。<b>這些還沒有人工確認</b>（標註 B）。
<br>合成資料（開發集）：正例 {syn['positives']}、負例 {syn['negatives']}。負例是隨機挑的不同公司照片，偏容易；比較方法的門檻是在同一批資料上挑最好的 F1，所以它們的數字偏樂觀。</div>
<h2>合成資料：各方法的精確率、召回率</h2><div class="wrap"><table><tr><th>方法</th><th>AP</th><th>精確率</th><th>召回率</th><th>F1</th><th>門檻</th></tr>{rows}</table></div>
<p class="mut">AP：平均精確率，不用門檻（本文用內點數排序）。MD5：合成的複本是新檔，照定義抓不到。</p>
<h2>候選召回率：原圖在不在前 {rr['k']} 名</h2><p>DINOv2 {rr['dinov2']:.3f}、SSCD {rr['sscd']:.3f}、兩者聯集 {rr['union']:.3f}（拿複本去 {res['photos']} 張開發集照片裡找原圖）。</p>
<h2>本文在各種變換的召回率</h2><div class="wrap"><table><tr><th>變換（有用到這種的正例）</th><th>正例數</th><th>召回率</th></tr>{tf}</table></div>
<h2>合成資料：本文漏掉的正例（隨機 12 組，共 {len(fn)} 組）</h2><div class="grid">{''.join(syn_card(d) for d in rng.sample(fn, min(12, len(fn))))}</div>
<h2>合成資料：本文誤判的負例（共 {len(fp)} 組）</h2><div class="grid">{''.join(syn_card(d) for d in fp[:12]) or '<p>沒有。</p>'}</div>
<h2>真實報告：驗證通過的配對（隨機 24 組，共 {len(same)} 組；左是 t 年、右是 t−1 年）</h2><div class="grid">{''.join(real_card(r) for r in rng.sample(same, min(24, len(same))))}</div>
<h2>真實報告：差一點沒過的（內點 ≥ 10，內點最多的 12 組）</h2><div class="grid">{''.join(real_card(r) for r in near) or '<p>沒有。</p>'}</div>
<h2>每家公司驗證通過的組數</h2><div class="wrap"><table><tr><th>公司</th><th>組數</th></tr>{''.join(f'<tr><td>{f}</td><td>{n}</td></tr>' for f, n in real['by_firm'].items())}</table></div>
</body></html>"""]
    Path(path).write_text("".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main()
