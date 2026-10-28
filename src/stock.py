"""Cross-firm stock photos (PLAN §5 step 6, Table 7): the same photo (or the same shot) in two different firms'
reports, a sign of stock imagery or of material shared between firms.

For every photo the photo filter keeps in the split, the photos of the other firms are searched by SSCD cosine
(FAISS inner product on the L2-normalised vectors; the query's mirror image too, the higher of the two); the top
reuse.stock_topk_other_firms other-firm photos are candidates, and each unordered pair is verified once with
reuse.py's check (SuperPoint + LightGlue, MAGSAC homography, inliers >= reuse.min_inliers and coverage >=
reuse.min_coverage, both orientations; the same cache, work/reuse/verify_cache.jsonl). PLAN: 很少就照實報告.
Development split only until configs/frozen.yaml exists (CLAUDE.md rule 3); annotation B labels these pairs later.
Writes work/reuse/stock_{split}_candidates.jsonl, results/stock_{split}_{stamp}.json and reports/stock_{split}_{stamp}.html.

  python src/stock.py --split dev
"""
import argparse
import collections
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import features  # noqa: E402
import reuse  # noqa: E402
from meta import run_meta, run_stamp  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--search", type=int, default=60, help="neighbours fetched before dropping the query's own firm")
    ap.add_argument("--show", type=int, default=60)
    args = ap.parse_args()
    import faiss
    import numpy as np
    cfg = reuse.load_yaml(ROOT / "configs" / "default.yaml")
    if args.split == "test" and not (ROOT / "configs" / "frozen.yaml").exists():
        sys.exit("測試集要等 configs/frozen.yaml（鐵則 3）")
    rc = cfg["reuse"]
    k_other = int(rc.get("stock_topk_other_firms", 5))
    crop_dir = ROOT / cfg["paths"]["extract"] / "crops"
    work = ROOT / cfg["paths"]["reuse"]
    filter_csv = ROOT / "work" / "photo_filter.csv"
    t0 = time.time()
    pool = reuse.photo_pool(args.split, filter_csv, ROOT / cfg["paths"]["extract"])
    crops = [r["crop"] for r in pool]
    names = {}
    with open(ROOT / cfg["paths"]["catalog"], encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            names[r["firm_id"].strip()] = r["firm_name"].strip()
    print(f"{args.split}：照片 {len(pool)} 張、{len({r['firm'] for r in pool})} 家", flush=True)
    vec = features.embed_crops(crops, "sscd", crop_dir).astype("float32")
    vfl = features.embed_crops(crops, "sscd", crop_dir, flip=True).astype("float32")
    index = faiss.IndexFlatIP(vec.shape[1])
    index.add(vec)
    s0, i0 = index.search(vec, min(args.search, len(crops)))
    s1, i1 = index.search(vfl, min(args.search, len(crops)))
    firm = [r["firm"] for r in pool]
    pairs = {}
    for q in range(len(crops)):
        best = {}
        for sims, ids, mir in ((s0[q], i0[q], False), (s1[q], i1[q], True)):
            for s, j in zip(sims, ids):
                if j < 0 or firm[j] == firm[q]:
                    continue
                if j not in best or s > best[j][0]:
                    best[j] = (float(s), mir)
        for j, (s, mir) in sorted(best.items(), key=lambda kv: -kv[1][0])[:k_other]:
            key = (min(q, j), max(q, j))
            if key not in pairs or s > pairs[key]["sscd"]:
                pairs[key] = {"sscd": round(s, 4), "mirror_first": mir}   # verify_either tries the other side too
    ver = reuse.Verifier(work / "verify_cache.jsonl", rc["min_inliers"], rc["min_coverage"])
    load = lambda c: (lambda: reuse.open_rgb(crop_dir / c))  # noqa: E731
    rows = []
    for (a, b), p in sorted(pairs.items()):
        v, mir = ver.verify_either(crops[a], load(crops[a]), crops[b], load(crops[b]), p["mirror_first"])
        md5_same = bool(set(pool[a]["md5"]) & set(pool[b]["md5"]))
        rows.append({"a": crops[a], "b": crops[b], "firm_a": firm[a], "firm_b": firm[b], "sscd": p["sscd"], "mirror": mir,
                     **{k: v[k] for k in ("matches", "inliers", "coverage")}, "same": ver.same(v), "md5_same": md5_same})
        if len(rows) % 1000 == 0:
            print(f"[stock] 驗證 {len(rows)}/{len(pairs)}，新驗證 {ver.new}", flush=True)
    work.mkdir(parents=True, exist_ok=True)
    cand_path = work / f"stock_{args.split}_candidates.jsonl"
    with open(cand_path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    same = [r for r in rows if r["same"]]
    photos_with = {c for r in same for c in (r["a"], r["b"])}
    firm_pairs = collections.Counter(tuple(sorted((r["firm_a"], r["firm_b"]))) for r in same)
    res = {"split": args.split, "photos": len(pool), "firms": len(set(firm)), "topk_other_firms": k_other,
           "candidate_pairs": len(rows), "verified_pairs": len(same), "verified_md5_identical": sum(r["md5_same"] for r in same),
           "photos_with_a_cross_firm_match": len(photos_with),
           "share_of_photos": round(len(photos_with) / len(pool), 4) if pool else None,
           "firm_pairs": [{"firms": list(k), "names": [names.get(x, "") for x in k], "pairs": n} for k, n in firm_pairs.most_common()],
           "firms_involved": len({x for k in firm_pairs for x in k}),
           "reuse_config": {k: rc[k] for k in ("min_inliers", "min_coverage", "stock_topk_other_firms")},
           "verify": {"version": reuse.VERIFY_VERSION, "new_verifications": ver.new}, "seconds": round(time.time() - t0, 1),
           "candidates_file": str(cand_path.relative_to(ROOT))}
    stamp = run_stamp()
    rp = ROOT / "results" / f"stock_{args.split}_{stamp}.json"
    k = 2
    while rp.exists():
        rp = ROOT / "results" / f"stock_{args.split}_{stamp}_{k}.json"
        k += 1
    rp.write_text(json.dumps({"meta": run_meta("configs/default.yaml", [filter_csv], packages=("torch", "faiss-cpu", "opencv-python", "numpy")),
                              **res}, ensure_ascii=False, indent=1), encoding="utf-8")
    cards = "".join(reuse.card(reuse.open_rgb(crop_dir / r["a"]), reuse.open_rgb(crop_dir / r["b"]),
                               f"{names.get(r['firm_a'], r['firm_a'])} {r['a']} ↔ {names.get(r['firm_b'], r['firm_b'])} {r['b']}",
                               f"內點 {r['inliers']}、涵蓋 {r['coverage']:.2f}、SSCD {r['sscd']:.2f}" + ("｜同一個檔（MD5 相同）" if r["md5_same"] else ""))
                    for r in sorted(same, key=lambda r: -r["inliers"])[:args.show])
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>跨公司圖庫照</title><style>body{{font-family:system-ui,"Microsoft JhengHei",sans-serif;margin:16px;background:#fafafa;color:#222;max-width:1100px}}
.pair{{display:flex;gap:8px;flex-wrap:wrap;border-top:1px solid #ddd;padding:10px 0}} .pair img{{max-width:min(320px,48%);height:auto}} .mut{{color:#666;font-size:13px}}</style></head><body>
<h1 style="font-size:20px">跨公司圖庫照：{'開發集' if args.split == 'dev' else '測試集'} {res['firms']} 家</h1>
<p>照片過濾留下的 {len(pool):,} 張照片，每張在<b>其他公司</b>的照片裡用 SSCD 找最像的 {k_other} 張，再用跟跨年重用一樣的方法驗證（內點 ≥ {rc['min_inliers']}、涵蓋 ≥ {rc['min_coverage']}，門檻是初值）。</p>
<p>候選 {len(rows):,} 對，<b>驗證通過 {len(same)} 對</b>（其中檔案完全相同 {res['verified_md5_identical']} 對），涉及 {len(photos_with)} 張照片（{100 * (res['share_of_photos'] or 0):.2f}%）、{res['firms_involved']} 家公司。這些還沒有人工確認（標註 B）。</p>
{cards or '<p>沒有。</p>'}</body></html>"""
    hp = ROOT / "reports" / f"{rp.stem}.html"
    hp.write_text(page, encoding="utf-8")
    print(json.dumps({k2: res[k2] for k2 in ("photos", "firms", "candidate_pairs", "verified_pairs", "verified_md5_identical",
                                             "photos_with_a_cross_firm_match", "share_of_photos", "firms_involved", "seconds")}, ensure_ascii=False))
    print(f"wrote {cand_path}, {rp} and {hp}")


if __name__ == "__main__":
    main()
