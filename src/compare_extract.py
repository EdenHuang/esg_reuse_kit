"""Explain the difference between two extractions (v1 = before, v2 = vector shadings skipped).

  python src/compare_extract.py --catalog data/catalog.csv --old work/extract_v1 --new work/extract --tag all

For every report with both {name}_pairs.jsonl files:
  - finds the v1 pictures that are only vector shading, from the PDF itself (photo_filter.vector_check
    on the v1 records; cached in --vector-cache),
  - matches v1 and v2 records on the same page: by the embedded-image xrefs, or by the box when the
    picture has no xref,
  - the difference is explained when the v1 records missing from v2 are exactly the shading
    pictures, v2 has no new records, every layer dropped from a kept record is an xref-0 layer of a
    v1 picture that had a shading layer, and the Table 1 counts move by exactly what those
    pictures account for.
Field differences of matched records are counted per field (crop file names move when an earlier
picture on the page is gone; a picture that lost a shading layer has a smaller box).
When that strict test fails, a page-level proof still shows whether only shadings went away: on every
page the embedded-image layers (xref > 0) are identical, and the page lost exactly as many xref-0
layers as the PDF has shading entries there. Such a report is "explained with side effects": a shading
that had been merged into a picture made its box bigger, joined two images into one picture, or linked
in-report duplicate groups; removing it changes those too.
Writes results/extract_compare_{stamp}_{tag}.json. Exit code 3 when the page-level proof fails anywhere.
"""
import argparse
import collections
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_filter as pf  # noqa: E402
from meta import run_meta, run_stamp  # noqa: E402
from report_extract import stats  # noqa: E402

SKIP = {"crop", "idx", "report"}


def key(r):
    xs = tuple(sorted(x for x in r["layers"] if x))
    return (r["page"], "x", xs) if xs else (r["page"], "b", tuple(r["bbox"]))


def shading_and_hidden(page):
    """(shading layers v1 kept, images v1 hid) on a page. extract_pairs.py de-duplicates entries by (xref,
    rounded box) before the size floor. In v1 a shading that came first took the key, so a later
    non-shading xref-0 image with the same box was dropped (even when the shading itself was then too small
    to keep); v2 no longer sees the shading and keeps that image."""
    import pymupdf
    import extract_pairs as ep
    keys = {}
    for info in ep.image_info(page, keep_shading=True):
        rect = pymupdf.Rect(info["bbox"]) & page.rect
        if rect.is_empty:
            continue
        w, h = info.get("width", 0), info.get("height", 0)
        key = (info.get("xref", 0), round(rect.x0), round(rect.y0), round(rect.x1), round(rect.y1))
        big = min(w, h) >= ep.MIN_PX and min(rect.width, rect.height) >= ep.MIN_PT
        keys.setdefault(key, []).append((ep.is_shading_image(info), big))
    kept_shading = sum(1 for v in keys.values() if v[0][0] and v[0][1])
    hidden = 0
    for v in keys.values():
        nxt = next((e for e in v[1:] if not e[0]), None) if v[0][0] else None
        hidden += bool(nxt and nxt[1])
    return kept_shading, hidden


def compare(name, pdf, old_p, new_p, cache_dir, n_pages):
    old, new = pf.read_pairs(old_p), pf.read_pairs(new_p)
    vec = pf.vector_check((name, pdf, str(old_p), str(cache_dir)))
    shade = set(vec["vector_crops"])
    om = collections.defaultdict(list)
    for r in old:
        om[key(r)].append(r)
    nm = collections.defaultdict(list)
    for r in new:
        nm[key(r)].append(r)
    matched, removed, added = [], [], []
    for k in set(om) | set(nm):
        a, b = om.get(k, []), nm.get(k, [])
        matched += list(zip(a, b))
        removed += a[len(b):]
        added += b[len(a):]
    removed_set = {r["crop"] for r in removed}
    dropped_layers, bad_drop, field_diff = 0, [], collections.Counter()
    for a, b in matched:
        lost = collections.Counter(a["layers"]) - collections.Counter(b["layers"])
        extra = collections.Counter(b["layers"]) - collections.Counter(a["layers"])
        dropped_layers += sum(lost.values())
        if extra or any(x != 0 for x in lost):
            bad_drop.append(a["crop"])
        for f in (set(a) | set(b)) - SKIP:
            if a.get(f) != b.get(f):
                field_diff[f] += 1
    # Page-level proof that only shadings went away: on every page the embedded-image layers (xref > 0) are the
    # same in v1 and v2, and the change in xref-0 layers equals (shading layers v1 kept) - (images v1 hid behind a
    # shading with the same box), both counted in the PDF. Checked on every page where either version has an
    # xref-0 layer.
    real_diff_pages, zero_diff = [], {}
    by_page_old, by_page_new = collections.defaultdict(list), collections.defaultdict(list)
    for r in old:
        by_page_old[r["page"]] += r["layers"]
    for r in new:
        by_page_new[r["page"]] += r["layers"]
    for p in set(by_page_old) | set(by_page_new):
        a, b = by_page_old.get(p, []), by_page_new.get(p, [])
        if collections.Counter(x for x in a if x) != collections.Counter(x for x in b if x):
            real_diff_pages.append(p)
        if any(not x for x in a + b):
            zero_diff[p] = sum(1 for x in a if not x) - sum(1 for x in b if not x)
    shade_layers, hidden = {}, {}
    if zero_diff:
        import pymupdf
        with pymupdf.open(pdf) as doc:
            for p in zero_diff:
                shade_layers[p], hidden[p] = shading_and_hidden(doc[p - 1])
    zero_bad_pages = sorted(p for p, dz in zero_diff.items() if dz != shade_layers[p] - hidden[p])
    so, sn = stats(old, n_pages), stats(new, n_pages)
    shade_recs = [r for r in old if r["crop"] in shade]
    expect = {  # what removing the shading pictures alone does to the Table 1 counts
        "placements": sum(len(r["layers"]) for r in shade_recs) + dropped_layers,
        "pictures": len(shade_recs),
        "distinct": sum(bool(r["representative"]) for r in shade_recs),
        "analysed": sum(bool(r["representative"]) and r["doc_role"] != "layout_repeat" for r in shade_recs),
    }
    got = {k: so[k] - sn[k] for k in expect}
    explained = (removed_set == shade and not added and not bad_drop and not vec["mismatch_pages"]
                 and got == expect)
    only_shading = not real_diff_pages and not zero_bad_pages
    side = []
    if not explained and only_shading:
        if sum(hidden.values()):
            side.append(f"{sum(hidden.values())} image(s) without an embedded file that v1 hid behind a shading with the same box are now kept")
        if removed_set - shade or added:
            side.append("a picture's box changed or it split: a shading layer had been merged into it")
        split = expect["pictures"] - got["pictures"] - sum(hidden.values())  # extra v2 pictures not from hidden images
        if split:
            side.append(f"{split:+d} pictures: a shading had joined images into one picture (or kept them apart)")
        if got["distinct"] - got["pictures"] != expect["distinct"] - expect["pictures"]:
            side.append("in-report duplicate groups changed (dHash or shared-xref grouping)")
    return {"report": name, "explained": explained, "only_shading_removed": only_shading, "side_effects": side,
            "pages_with_different_embedded_layers": sorted(real_diff_pages),
            "pages_where_lost_xref0_layers_differ_from_shadings": zero_bad_pages,
            "xref0_layers_lost": sum(zero_diff.values()), "shading_layers_on_those_pages": sum(shade_layers.values()),
            "images_v1_hid_behind_a_shading": sum(hidden.values()),
            "old": so, "new": sn, "diff": got, "expected_from_shading": expect,
            "shading_pictures_v1": len(shade), "v1_pictures_with_a_shading_layer_kept": vec["mixed_kept"],
            "removed": len(removed), "removed_not_shading": sorted(removed_set - shade),
            "shading_not_removed": sorted(shade - removed_set), "added": [r["crop"] for r in added],
            "layers_dropped_from_kept": dropped_layers, "bad_layer_drops": bad_drop,
            "vector_check_mismatch_pages": vec["mismatch_pages"], "field_differences": dict(field_diff)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--old", default="work/extract_v1")
    ap.add_argument("--new", default="work/extract")
    ap.add_argument("--vector-cache", default="work/features/vector_shading_v1")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()
    rows = list(csv.DictReader(open(args.catalog, encoding="utf-8-sig")))
    out, inputs = [], [args.catalog]
    for row in rows:
        name = f"{row['firm_id'].strip()}_{row['year'].strip()}"
        if args.only and name not in args.only:
            continue
        o, n = Path(args.old) / f"{name}_pairs.jsonl", Path(args.new) / f"{name}_pairs.jsonl"
        if not (o.exists() and n.exists()):
            continue
        res = compare(name, row["file_path"].strip(), o, n, Path(args.vector_cache), int(row["pages"]) if row.get("pages") else None)
        out.append(res)
        inputs += [str(o), str(n)]
        d = res["diff"]
        verdict = "explained" if res["explained"] else ("only shadings removed; side effects: " + "; ".join(res["side_effects"])
                                                         if res["only_shading_removed"] else "NOT EXPLAINED")
        print(f"{name}: {verdict}｜漸層 {res['shading_pictures_v1']} 張｜"
              f"圖片位置 -{d['placements']} 合併後 -{d['pictures']} 不同的圖 -{d['distinct']} 列入分析 -{d['analysed']}｜"
              f"欄位不同 {res['field_differences']}", flush=True)
    tot = {k: sum(r["diff"][k] for r in out) for k in ("placements", "pictures", "distinct", "analysed")}
    summary = {"reports": len(out), "explained": sum(r["explained"] for r in out),
               "explained_with_side_effects": [r["report"] for r in out if not r["explained"] and r["only_shading_removed"]],
               "not_explained": [r["report"] for r in out if not r["explained"] and not r["only_shading_removed"]],
               "all_reports_only_shading_removed": all(r["only_shading_removed"] for r in out),
               "xref0_layers_lost": sum(r["xref0_layers_lost"] for r in out),
               "images_v1_hid_behind_a_shading": sum(r["images_v1_hid_behind_a_shading"] for r in out),
               "reports_with_hidden_images": [r["report"] for r in out if r["images_v1_hid_behind_a_shading"]],
               "side_effect_counts": dict(collections.Counter(s for r in out for s in r["side_effects"])),
               "shading_pictures_v1": sum(r["shading_pictures_v1"] for r in out), "diff_total": tot,
               "kept_pictures_that_lost_a_shading_layer": sum(r["v1_pictures_with_a_shading_layer_kept"] for r in out)}
    stamp = run_stamp()
    path = Path("results") / f"extract_compare_{stamp}_{args.tag}.json"
    path.write_text(json.dumps({"meta": run_meta("configs/default.yaml", inputs[:1]), "old": args.old, "new": args.new,
                                "summary": summary, "reports": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print(f"wrote {path}")
    sys.exit(0 if not summary["not_explained"] else 3)


if __name__ == "__main__":
    main()
