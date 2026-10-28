"""How many photos are cut into tiles (decision 16, TODO_EDEN (1b)): groups of images on the same page that touch
edge to edge, from the extraction boxes (work/extract/*_pairs.jsonl) and the photo filter (work/photo_filter.csv).

  tile pair   two images on the same page whose facing edges are at most tiles.gap_pt apart, whose shared edge
              covers at least tiles.min_shared_edge of the shorter side (same test as logo_refs.abuts), and which
              overlap by at most tiles.max_overlap of the smaller area
  strict      in addition both ends of the shared edge line up (within gap_pt): a clean grid
  continuous  in addition the picture runs on across the seam: on the page rendered at tiles.seam_zoom px per pt,
              the grey pixel lines just either side of the seam, along the shared edge, both vary (spread >=
              tiles.seam_min_std grey levels, the larger at most tiles.seam_max_std_ratio times the smaller) and
              move together (correlation >= tiles.seam_min_corr). Different photos laid side by side (photo grids,
              panels, icon rows, white borders or separators) fail; one picture cut into pieces passes. The 10/18
              look at 20 dev groups: most edge-to-edge groups were different photos
  group       connected pairs, two or more images

Counted among the images given to the photo model (photo filter: none of the first five exclusion reasons; Table 2's
population, decision 1). Decision 16's 2% rule is read, as the work order states it, on the continuous groups over
all reports (a descriptive count of the extraction, no labels or scores); the development split and the plain
edge-to-edge count are reported alongside. Writes results/tile_groups_{stamp}.json and reports/tile_groups_{stamp}.html
(development pages only: random continuous groups, which a merge would join, and random groups that are not).

  python src/tile_groups.py
"""
import argparse
import base64
import collections
import csv
import html
import json
import random
import statistics
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp  # noqa: E402

REASONS_BEFORE_MODEL = {"image_only_report", "cover_page", "vector_shading", "layout_repeat", "duplicate"}
DEFAULTS = {"gap_pt": 1.5, "min_shared_edge": 0.5, "max_overlap": 0.1, "seam_zoom": 2.0, "seam_min_std": 2.0,
            "seam_max_std_ratio": 1.5, "seam_min_corr": 0.9, "decide_share": 0.02}


def tile_pair(a, b, p):
    """None, or {"axis": "x" (side by side) | "y" (stacked), "strict": bool} for boxes [x0, y0, x1, y1] in points."""
    tol = p["gap_pt"]
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    area = lambda r: max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])  # noqa: E731
    small = min(area(a), area(b))
    if small <= 0 or (ix > 0 and iy > 0 and ix * iy > p["max_overlap"] * small):
        return None
    for axis in ("x", "y"):
        if axis == "x":
            meet = abs(a[2] - b[0]) <= tol or abs(b[2] - a[0]) <= tol
            shared, short = iy, min(a[3] - a[1], b[3] - b[1])
            ends = abs(a[1] - b[1]) <= tol and abs(a[3] - b[3]) <= tol
        else:
            meet = abs(a[3] - b[1]) <= tol or abs(b[3] - a[1]) <= tol
            shared, short = ix, min(a[2] - a[0], b[2] - b[0])
            ends = abs(a[0] - b[0]) <= tol and abs(a[2] - b[2]) <= tol
        if meet and short > 0 and shared >= p["min_shared_edge"] * short:
            return {"axis": axis, "strict": ends}
    return None


def seam_profiles(img, z, a, b, axis):
    """(spread left, spread right, correlation) of the grey pixel lines just either side of the seam, along the
    shared edge, on the rendered page; None when the shared edge is too short to measure."""
    import math
    import numpy as np
    if axis == "y":   # transpose the problem: stacked tiles become side by side
        img = img.transpose(1, 0, 2)
        a, b = [a[1], a[0], a[3], a[2]], [b[1], b[0], b[3], b[2]]
    left, right = (a, b) if a[0] <= b[0] else (b, a)
    h, w = img.shape[:2]
    xl = math.floor(left[2] * z) - 2
    xr = math.ceil(right[0] * z) + 1
    y0 = math.ceil(max(left[1], right[1]) * z) + 2
    y1 = min(math.floor(min(left[3], right[3]) * z) - 2, h)
    if y1 - y0 < 8 or xl < 0 or xr >= w or xl >= xr:
        return None
    L, R = img[y0:y1, xl, :].mean(axis=1), img[y0:y1, xr, :].mean(axis=1)
    sl, sr = float(L.std()), float(R.std())
    corr = float(np.corrcoef(L, R)[0, 1]) if sl > 0 and sr > 0 else 0.0
    if corr != corr:   # NaN: one line is constant up to rounding
        corr = 0.0
    return round(sl, 2), round(sr, 2), round(corr, 3)


def continuous(prof, p):
    """The picture runs on across the seam: both lines vary (not a flat border or separator), by a similar amount,
    and in step. Different photos side by side fail one of the three (10/18 check on 20 dev groups)."""
    if prof is None:
        return False
    sl, sr, corr = prof
    lo, hi = min(sl, sr), max(sl, sr)
    return lo >= p["seam_min_std"] and hi <= p["seam_max_std_ratio"] * lo and corr >= p["seam_min_corr"]


def components(n, edges):
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, j in edges:
        parent[find(i)] = find(j)
    comp = collections.defaultdict(list)
    for i in range(n):
        comp[find(i)].append(i)
    return [sorted(c) for c in comp.values() if len(c) >= 2]


def summarize(judged, groups, kept):
    tiles = sum(len(g) for g in groups)
    sizes = [len(g) for g in groups]
    kept_tiles = sum(sum(1 for c in g if c in kept) for g in groups)
    return {"judged_images": judged, "groups": len(groups), "tiles_in_groups": tiles,
            "share_of_judged": round(tiles / judged, 4) if judged else None,
            "mean_tiles_per_group": round(statistics.mean(sizes), 2) if sizes else None,
            "group_sizes": dict(sorted(collections.Counter(sizes).items())),
            "units_if_merged": judged - tiles + len(groups),
            "kept_photos": len(kept), "kept_photos_in_groups": kept_tiles,
            "share_of_kept_photos": round(kept_tiles / len(kept), 4) if kept else None,
            "groups_with_a_kept_photo": sum(1 for g in groups if any(c in kept for c in g)),
            "groups_all_kept": sum(1 for g in groups if all(c in kept for c in g))}


def render(page, z):
    import numpy as np
    import pymupdf
    pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).astype(np.float32)


def page_card(pdf, g, keep, long_side=900):
    import pymupdf
    with pymupdf.open(pdf) as doc:
        page = doc[g["page"] - 1]
        z = long_side / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
        W, H = page.rect.width, page.rect.height
        img = base64.b64encode(pix.tobytes("jpg", jpg_quality=80)).decode()
    divs = "".join(f'<div class="b{" k" if c in keep else ""}" style="left:{100 * b[0] / W:.2f}%;top:{100 * b[1] / H:.2f}%;'
                   f'width:{100 * (b[2] - b[0]) / W:.2f}%;height:{100 * (b[3] - b[1]) / H:.2f}%"></div>'
                   for b, c in zip(g["bboxes"], g["crops"]))
    thumbs = "".join(f'<img class="t" src="data:image/jpeg;base64,{base64.b64encode((ROOT / "work" / "extract" / "crops" / c).read_bytes()).decode()}" '
                     f'title="{html.escape(c)}" alt="">' for c in g["crops"])
    joined = f'相鄰的邊 {g["edges"]} 條，接縫兩邊畫面連得起來的 {g["continuous_edges"]} 條'
    return (f'<section><h2>{g["report"]} 第 {g["page"]} 頁：{len(g["crops"])} 塊，照片過濾留下 {sum(c in keep for c in g["crops"])} 塊</h2>'
            f'<p class="mut">{joined}</p>'
            f'<div class="pg"><img src="data:image/jpeg;base64,{img}" alt="">{divs}</div><div class="row">{thumbs}</div></section>')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--examples", type=int, default=20, help="continuous groups shown (and half as many others)")
    args = ap.parse_args()
    import pymupdf
    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))
    p = dict(DEFAULTS, **(cfg.get("tiles") or {}))
    filter_csv = ROOT / "work" / "photo_filter.csv"
    by_report = collections.defaultdict(list)
    with open(filter_csv, encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            by_report[d["report"]].append(d)

    judged_n, kept = collections.Counter(), collections.defaultdict(set)
    pages = []   # pages with at least one edge-to-edge pair
    for report, ds in sorted(by_report.items()):
        split = ds[0]["split"]
        judged = {d["crop"] for d in ds if not (set(filter(None, d["exclude"].split("|"))) & REASONS_BEFORE_MODEL)}
        if not judged:
            continue
        judged_n[split] += len(judged)
        kept[split] |= {d["crop"] for d in ds if d["keep"] == "1"}
        by_page = collections.defaultdict(list)
        with open(ROOT / "work" / "extract" / f"{report}_pairs.jsonl", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r["crop"] in judged:
                    by_page[r["page"]].append({"crop": r["crop"], "bbox": r["bbox"]})
        for pg, items in sorted(by_page.items()):
            items.sort(key=lambda it: it["crop"])
            edges = []
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    t = tile_pair(items[i]["bbox"], items[j]["bbox"], p)
                    if t:
                        edges.append(dict(t, i=i, j=j))
            if edges:
                pages.append({"split": split, "report": report, "page": pg, "items": items, "edges": edges})

    print(f"有相鄰圖片的頁 {len(pages)}；算接縫……", flush=True)
    by_rep = collections.defaultdict(list)
    for pr in pages:
        by_rep[pr["report"]].append(pr)
    for report, prs in sorted(by_rep.items()):
        with pymupdf.open(ROOT / "data" / "raw" / f"{report}.pdf") as doc:
            for pr in prs:
                img = render(doc[pr["page"] - 1], p["seam_zoom"])
                for e in pr["edges"]:
                    e["seam"] = seam_profiles(img, p["seam_zoom"], pr["items"][e["i"]]["bbox"], pr["items"][e["j"]]["bbox"], e["axis"])
                    e["continuous"] = continuous(e["seam"], p)

    kinds = {"edge_to_edge": lambda e: True, "strict": lambda e: e["strict"], "continuous": lambda e: e["continuous"]}
    groups = {k: collections.defaultdict(list) for k in kinds}
    loose_groups = []
    for pr in pages:
        for k, keep_edge in kinds.items():
            for g in components(len(pr["items"]), [(e["i"], e["j"]) for e in pr["edges"] if keep_edge(e)]):
                crops = [pr["items"][i]["crop"] for i in g]
                groups[k][pr["split"]].append(crops)
                if k == "edge_to_edge":
                    es = [e for e in pr["edges"] if e["i"] in g]
                    loose_groups.append({"split": pr["split"], "report": pr["report"], "page": pr["page"], "crops": crops,
                                         "bboxes": [pr["items"][i]["bbox"] for i in g], "seams": [e["seam"] for e in es],
                                         "continuous_edges": sum(e["continuous"] for e in es), "edges": len(es)})
    res = {"params": p}
    allk = kept["dev"] | kept["test"]
    for k in kinds:
        res[k] = {s: summarize(judged_n[s], groups[k][s], kept[s]) for s in ("dev", "test")}
        res[k]["total"] = summarize(sum(judged_n.values()), groups[k]["dev"] + groups[k]["test"], allk)
    dev_edges = [e for pr in pages if pr["split"] == "dev" for e in pr["edges"]]
    res["dev_edges"] = {"pairs": len(dev_edges), "continuous": sum(e["continuous"] for e in dev_edges),
                        "flat_or_unmeasured": sum(1 for e in dev_edges if e["seam"] is None or min(e["seam"][:2]) < p["seam_min_std"])}
    total_share = res["continuous"]["total"]["share_of_judged"] or 0.0
    res["decision_16"] = {"rule": f"merge when the share of judged images (all reports) in continuous tile groups >= {p['decide_share']:.0%}",
                          "total_share": total_share, "dev_share": res["continuous"]["dev"]["share_of_judged"],
                          "edge_to_edge_total_share": res["edge_to_edge"]["total"]["share_of_judged"],
                          "edge_to_edge_dev_share": res["edge_to_edge"]["dev"]["share_of_judged"],
                          "kept_photo_total_share": res["continuous"]["total"]["share_of_kept_photos"],
                          "merge": total_share >= p["decide_share"]}

    rng = random.Random(f"{cfg['seed']}:tiles")
    dev = [g for g in loose_groups if g["split"] == "dev"]
    cont = [g for g in dev if g["continuous_edges"] == g["edges"]]
    other = [g for g in dev if g["continuous_edges"] == 0]
    pick_c = sorted(rng.sample(cont, min(args.examples, len(cont))), key=lambda g: (g["report"], g["page"]))
    pick_o = sorted(rng.sample(other, min(args.examples // 2, len(other))), key=lambda g: (g["report"], g["page"]))
    res["examples"] = {"continuous": [{k: g[k] for k in ("report", "page", "crops", "seams")} for g in pick_c],
                       "not_continuous": [{k: g[k] for k in ("report", "page", "crops", "seams")} for g in pick_o]}

    stamp = run_stamp()
    rpath = ROOT / "results" / f"tile_groups_{stamp}.json"
    k = 2
    while rpath.exists():
        rpath = ROOT / "results" / f"tile_groups_{stamp}_{k}.json"
        k += 1
    rpath.write_text(json.dumps({"meta": run_meta("configs/default.yaml", [filter_csv]), **res}, ensure_ascii=False, indent=1),
                     encoding="utf-8")

    pdf = lambda g: ROOT / "data" / "raw" / f"{g['report']}.pdf"  # noqa: E731
    cards_c = "".join(page_card(pdf(g), g, kept["dev"]) for g in pick_c)
    cards_o = "".join(page_card(pdf(g), g, kept["dev"]) for g in pick_o)
    names = {"edge_to_edge": "邊對邊貼在一起（上限）", "strict": "…而且排成整齊的格子", "continuous": "…而且接縫兩邊畫面連續（同一張圖切開）"}
    rows_html = "".join(
        f'<tr><td>{names[k]}</td><td>{s}</td><td>{v["judged_images"]:,}</td><td>{v["groups"]:,}</td><td>{v["tiles_in_groups"]:,}</td>'
        f'<td>{100 * (v["share_of_judged"] or 0):.2f}%</td><td>{v["mean_tiles_per_group"] or "—"}</td>'
        f'<td>{v["kept_photos_in_groups"]:,}／{v["kept_photos"]:,}（{100 * (v["share_of_kept_photos"] or 0):.2f}%）</td></tr>'
        for k in kinds for s, v in (("開發集", res[k]["dev"]), ("全部", res[k]["total"])))
    d16 = res["decision_16"]
    page_html = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>切成小塊的照片</title><style>
:root{{--bg:#fafafa;--fg:#222;--mut:#666;--line:#ddd}}
body{{font-family:system-ui,"Microsoft JhengHei",sans-serif;margin:16px;background:var(--bg);color:var(--fg);max-width:1000px}}
table{{border-collapse:collapse;font-size:14px}} td,th{{border:1px solid var(--line);padding:4px 8px;text-align:right}}
td:first-child,th:first-child{{text-align:left}} .wrap{{overflow-x:auto}}
section{{margin:0 0 32px}} h2{{font-size:15px;font-weight:600}}
.pg{{position:relative;display:inline-block;max-width:100%}} .pg img{{display:block;max-width:100%}}
.b{{position:absolute;box-sizing:border-box;border:2px solid #e8590c}} .b.k{{border-color:#1a56c4}}
.row{{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}} .t{{max-height:110px;max-width:45%;border:1px solid var(--line)}}
.mut{{color:var(--mut)}}
</style></head><body><h1 style="font-size:20px">切成小塊的照片有多少</h1>
<p>同一頁上邊對邊貼在一起（間隙 ≤ {p['gap_pt']} pt、共用的邊至少占短邊的一半）的圖片算一組。但很多組其實是<b>不同的照片排在一起</b>（照片牆、色塊、圖示列），不能合併；所以再看接縫：把頁面畫出來，取緊貼接縫兩邊的兩排像素，兩排都有明暗變化（不是白邊或分隔線）、變化差不多大、而且一起變（相關係數 ≥ {p['seam_min_corr']:g}），算「畫面連續」，也就是同一張圖被切開。</p>
<p>只算「給模型判斷的圖」（封面頁、沒有文字層的報告、向量漸層、版面元素、報告內重複都已經先排除）。</p>
<p><b>全部報告 {100 * d16['total_share']:.2f}% 的圖是「同一張圖切開」的一塊</b>（開發集 {100 * (d16['dev_share'] or 0):.2f}%；只看邊對邊、不看接縫是 {100 * (d16['edge_to_edge_total_share'] or 0):.2f}%，開發集 {100 * (d16['edge_to_edge_dev_share'] or 0):.2f}%）。照片過濾留下的照片裡是 {100 * (d16['kept_photo_total_share'] or 0):.2f}%。決定 16 的規則：≥ {p['decide_share']:.0%} 就合併 → <b>{"要合併" if d16['merge'] else "不合併"}</b>。接縫的判斷是初值，下面的例子可以看它對不對。</p>
<div class="wrap"><table><tr><th></th><th></th><th>給模型判斷的圖</th><th>組數</th><th>組裡的圖塊</th><th>占給模型判斷的圖</th><th>每組平均塊數</th><th>照片過濾留下的照片裡，屬於某一組的</th></tr>{rows_html}</table></div>
<h2 style="margin-top:24px">開發集：接縫連續的組，隨機 {len(pick_c)} 組（合併的話會合起來的）</h2><p class="mut">藍框：照片過濾留下的；橘框：沒留下的。下面一排是各塊的裁切圖。</p>
{cards_c}
<h2 style="margin-top:24px">開發集：接縫不連續的組，隨機 {len(pick_o)} 組（不同的圖排在一起，不合併）</h2>
{cards_o}</body></html>"""
    hpath = ROOT / "reports" / f"tile_groups_{stamp}.html"
    hpath.write_text(page_html, encoding="utf-8")
    print(json.dumps({"decision_16": d16, "dev_edges": res["dev_edges"],
                      **{k: res[k]["dev"] for k in kinds}}, ensure_ascii=False))
    print(f"wrote {rpath} and {hpath}")


if __name__ == "__main__":
    main()
