"""Annotations B, C and D as self-contained HTML batch pages, the way annotation A works (Eden 10/19: students label
on their own computers; README v1.5 怎麼抽題, 品質控管; manuals B_same_photo_zh.md, C_find_in_last_year_zh.md,
D_photo_boxes_zh.md).

  B  two images: the same photo (or the same shot)? Yes / No (+ not sure). Both annotators (kappa, PLAN §6).
     Items: each comparison method's picks on the development photos (reuse.py's and stock.py's candidates: MD5,
     pHash, DINOv2, SSCD at their best-F1 thresholds on the development synthetic pairs, and ours), a simple random
     sample per method, merged (each method's precision is read on its own sample). Checks and practice: development
     synthetic pairs (a copy of the same photo: Yes; two firms' photos with low SSCD: No).
  C  a photo and all photos of the same firm's report of the year before (thumbnails in page order): click every
     thumbnail of the same photo or shot, or tick 沒有相同的. Recall sample, test firms' 2024 and 2025 reports
     (README). One annotator (a1). Checks: a transformed copy (crop / flip / colour) of the query hidden in the grid.
  D  a whole page with a box around every image the extraction found: draw a box around a missed photo, click a box
     to mark it 框不完整 / 框到多張 / 被切開, or tick 這頁沒有照片. Test pages, no cover pages, no reports without a
     text layer (README). One annotator (a2). Checks: a page with one photo's box removed (it must be drawn).
Task data hold only opaque ids and images (README 原則); the batch plan, the check answers and the item sources are
in annotations/qc/ and annotations/{dev,test}/ (never in the pages).

  python src/annotate_bcd.py b-sample --split dev
  python src/annotate_bcd.py b-qc
  python src/annotate_bcd.py pages --task B          # batch pages, examples, practice, one zip per annotator
  python src/annotate_bcd.py check --task B --annotator a1
  (C and D: c-sample / d-sample, then pages and check with --task C / D)
"""
import argparse
import base64
import collections
import csv
import hashlib
import html
import io
import json
import math
import random
import re
import secrets
import statistics
import sys
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from annotate_export import CSS, interleave, longest_run, md_to_html, read_jsonl, write_jsonl  # noqa: E402
from meta import run_meta, run_stamp, sha256_file  # noqa: E402

PAGE_VERSION = "BCD-html-3"   # 10/23: header button to download a partly done batch; C out() takes the item; download fallback box
TASKS = {
    "B": {"zh": "標註 B：是不是同一張照片？", "batch": 50, "per_check": 10, "practice": 20, "need": 18, "annotators": ("a1", "a2"),
          "manual": "B_same_photo_zh.md", "limits": {"check_error": 0.20, "median_seconds": 1.0, "same_answer_run": 30}},
    "C": {"zh": "標註 C：在前一年的報告裡找同一張照片", "batch": 20, "per_check": 10, "practice": 5, "need": 5, "annotators": ("a1",),
          "manual": "C_find_in_last_year_zh.md", "limits": {"check_error": 0.20, "median_seconds": 10.0}},
    "D": {"zh": "標註 D：照片的框對不對？", "batch": 50, "per_check": 10, "practice": 10, "need": 9, "annotators": ("a2",),
          "manual": "D_photo_boxes_zh.md", "limits": {"check_error": 0.20, "median_seconds": 3.0}},
}
SIDE = {"B": 480, "C_query": 640, "C_thumb": 440, "D_page": 1200}   # 10/21: C shows a pool of ~10-20 candidates at 440 px (was every photo at 260 px)
QUALITY = 82


# ---------------------------------------------------------------- shared helpers
def cfg_all():
    import yaml
    return yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))


def work(task):
    return ROOT / "work" / "annotation" / task


def qc(task, name):
    return ROOT / "annotations" / "qc" / f"{task}_{name}"


def salt(task):
    p = work(task) / "salt.txt"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_hex(16), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()


def iid(task, key):
    """Opaque id, the same scheme for real items, checks and practice (nothing tells them apart)."""
    return task + hashlib.sha256(f"{salt(task)}:{task}:{key}".encode()).hexdigest()[:8]


def jpeg_uri(src, side, quality=QUALITY):
    from PIL import Image
    im = src if hasattr(src, "size") else Image.open(src)
    im = im.convert("RGB")
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def crop_path(c):
    return ROOT / "work" / "extract" / "crops" / c


def write_result(name, summary, inputs):
    stamp = run_stamp()
    path = ROOT / "results" / f"{name}_{stamp}.json"
    k = 2
    while path.exists():
        path = ROOT / "results" / f"{name}_{stamp}_{k}.json"
        k += 1
    path.write_text(json.dumps({"meta": run_meta("configs/default.yaml", [str(p) for p in inputs]), **summary},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {path}")
    return path


def filter_rows():
    with open(ROOT / "work" / "photo_filter.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def page_id(e):
    s = json.dumps({k: e[k] for k in ("task", "annotator", "batch", "version", "items")}, sort_keys=True)
    return hashlib.sha256(s.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- B: items
def cmd_b_sample(args):
    """Each method's picks among the reuse and stock candidates of the split; a simple random sample per method."""
    import reuse
    cfg = cfg_all()
    path = ROOT / "annotations" / args.split / "B_items.jsonl"
    if path.exists() and not args.force:
        sys.exit(f"{path} 已經存在；要重抽加 --force（還沒有答案時才可以）")
    if args.split != "dev":
        sys.exit("B 的測試集配對要等 configs/frozen.yaml 之後用凍結的方法產生（鐵則 3）")
    syn = sorted((ROOT / "results").glob(f"reuse_{args.split}_*.json"))[-1]
    th = {m: json.load(open(syn, encoding="utf-8"))["synthetic"]["methods"][m]["threshold"] for m in ("phash", "dinov2", "sscd")}
    reuse_rows = [json.loads(l) for l in open(ROOT / "work" / "reuse" / f"{args.split}_candidates.jsonl", encoding="utf-8")]
    stock_rows = [json.loads(l) for l in open(ROOT / "work" / "reuse" / f"stock_{args.split}_candidates.jsonl", encoding="utf-8")]
    pairs = {}
    for r in reuse_rows:
        k = tuple(sorted((r["query"], r["candidate"])))
        pairs[k] = {"kind": "reuse", "md5": r["md5_same"], "dinov2": r["sim"].get("dinov2"), "sscd": r["sim"].get("sscd"),
                    "ours": r["same"], "inliers": r["inliers"], "coverage": r["coverage"]}
    for r in stock_rows:
        k = tuple(sorted((r["a"], r["b"])))
        pairs.setdefault(k, {"kind": "stock", "md5": r["md5_same"], "dinov2": None, "sscd": r["sscd"], "ours": r["same"],
                             "inliers": r["inliers"], "coverage": r["coverage"]})
    ph = {}
    for c in sorted({x for k in pairs for x in k}):
        with reuse.open_rgb(crop_path(c)) as im:
            from PIL import ImageOps
            ph[c] = (reuse.phash(im), reuse.phash(ImageOps.mirror(im)))
    max_dist = round((1 - th["phash"]) * 64)
    for (a, b), p in pairs.items():
        p["phash_dist"] = min(reuse.ham(ph[a][0], ph[b][0]), reuse.ham(ph[a][0], ph[b][1]))
    pick = {"md5": lambda p: p["md5"], "phash": lambda p: p["phash_dist"] <= max_dist,
            "dinov2": lambda p: (p["dinov2"] or 0) >= th["dinov2"], "sscd": lambda p: (p["sscd"] or 0) >= th["sscd"],
            "ours": lambda p: p["ours"]}
    quota = {("reuse", "md5"): 20, ("reuse", "phash"): 30, ("reuse", "dinov2"): 40, ("reuse", "sscd"): 30, ("reuse", "ours"): 40,
             ("stock", "phash"): 15, ("stock", "sscd"): 30, ("stock", "ours"): 20}
    rng = random.Random(f"{cfg['seed']}:B:{args.split}")
    chosen = collections.defaultdict(set)
    pool_sizes = {}
    for (kind, m), n in quota.items():
        cands = sorted(k for k, p in pairs.items() if p["kind"] == kind and pick[m](p))
        pool_sizes[f"{kind}/{m}"] = len(cands)
        for k in (rng.sample(cands, n) if len(cands) > n else cands):
            chosen[k].add(f"{kind}/{m}")
    rows = []
    for k in sorted(chosen):
        p = pairs[k]
        left = rng.random() < 0.5
        a, b = (k[0], k[1]) if left else (k[1], k[0])
        rows.append({"item": iid("B", f"{a}|{b}"), "a": a, "b": b, "split": args.split, "kind": p["kind"],
                     "sampled_for": sorted(chosen[k]), "picked_by": sorted(m for m in pick if pick[m](p)),
                     **{x: p[x] for x in ("phash_dist", "dinov2", "sscd", "inliers", "coverage")}})
    write_jsonl(path, rows)
    write_result(f"annotation_B_sample_{args.split}", {"pairs_in_candidates": len(pairs), "thresholds": th,
                                                      "phash_max_distance": max_dist, "pool_sizes": pool_sizes,
                                                      "quota": {f"{k}/{m}": n for (k, m), n in quota.items()},
                                                      "items": len(rows), "by_kind": dict(collections.Counter(r["kind"] for r in rows))},
                 [ROOT / "work" / "reuse" / f"{args.split}_candidates.jsonl", ROOT / "work" / "reuse" / f"stock_{args.split}_candidates.jsonl", syn])
    print(f"B {args.split}：{len(rows)} 對 → {path}")


TRANSFORM_ZH = {"crop": "裁切", "flip": "左右翻轉", "color": "調色", "text": "壓了字", "collage": "放進拼貼", "jpeg": "壓縮"}


def cmd_b_qc(args):
    """Examples, two practice sets and the checks from the development synthetic pairs (answers known by construction)."""
    cfg = cfg_all()
    syn = [json.loads(l) for l in open(ROOT / "data" / "synthetic" / "dev" / "pairs.jsonl", encoding="utf-8")]
    used = {x for r in read_jsonl(ROOT / "annotations" / "dev" / "B_items.jsonl") for x in (r["a"], r["b"])}
    rng = random.Random(f"{cfg['seed']}:B:qc")
    pos = [r for r in syn if r["label"] == 1 and r["a"] not in used]
    neg = [r for r in syn if r["label"] == 0 and r["a"] not in used and r["b"] not in used]
    rng.shuffle(pos)
    rng.shuffle(neg)
    n_batches = math.ceil(len(read_jsonl(ROOT / "annotations" / "dev" / "B_items.jsonl")) / TASKS["B"]["batch"]) + 20   # room for the test batches
    need = {"example": (6, 6), "practice": (20, 20), "check": (3 * n_batches, 2 * n_batches)}
    rows, ip, ineg = [], 0, 0
    for role, (np_, nn) in need.items():
        for r in pos[ip:ip + np_]:
            rows.append({"role": role, "a": r["a"], "b": r["b"], "b_synthetic": True, "answer": "Yes",
                         "reason": "是：同一張照片，" + "、".join(TRANSFORM_ZH.get(t["name"], t["name"]) for t in r["transforms"]) + "過"})
        for r in neg[ineg:ineg + nn]:
            rows.append({"role": role, "a": r["a"], "b": r["b"], "b_synthetic": False, "answer": "No", "reason": "不是：兩張是不同的場景"})
        ip, ineg = ip + np_, ineg + nn
    for r in rows:
        r["item"] = iid("B", f"qc|{r['a']}|{r['b']}")
    write_jsonl(qc("B", "qc.jsonl"), rows)
    print(dict(collections.Counter((r["role"], r["answer"]) for r in rows)))


def b_img(path_or_crop, synthetic):
    p = (ROOT / "data" / "synthetic" / "dev" / path_or_crop) if synthetic else crop_path(path_or_crop)
    return jpeg_uri(p, SIDE["B"])


# ---------------------------------------------------------------- C: items
def cmd_c_sample(args):
    """README: 200 photos of the test firms' 2024 and 2025 reports; each with every kept photo of the same firm's
    report of the year before (page order). Not when that report has no text layer or no kept photo."""
    cfg = cfg_all()
    path = ROOT / "annotations" / "test" / "C_items.jsonl"
    if path.exists() and not args.force:
        sys.exit(f"{path} 已經存在")
    rows = filter_rows()
    kept = collections.defaultdict(list)
    image_only = {d["report"] for d in rows if d["image_only_report"] == "1"}
    for d in rows:
        if d["keep"] == "1":
            kept[d["report"]].append(d)
    for k in kept:
        kept[k].sort(key=lambda d: (int(d["page"]), int(d["idx"])))
    def prev(rep):
        firm, year = rep.split("_")
        return f"{firm}_{int(year) - 1}"
    pool = sorted(d["crop"] for d in rows if d["keep"] == "1" and d["split"] == "test" and d["year"] in ("2024", "2025")
                  and prev(d["report"]) not in image_only and kept.get(prev(d["report"])))
    rng = random.Random(f"{cfg['seed']}:C:test")
    pick = rng.sample(pool, min(args.n + args.spares, len(pool)))
    by = {d["crop"]: d for d in rows}
    items = [{"item": iid("C", c), "query": c, "report": by[c]["report"], "prev_report": prev(by[c]["report"]),
              "grid": [d["crop"] for d in kept[prev(by[c]["report"])]], "split": "test", "order": k + 1, "spare": k >= args.n}
             for k, c in enumerate(pick)]
    write_jsonl(path, items)
    excluded = sum(1 for d in rows if d["keep"] == "1" and d["split"] == "test" and d["year"] in ("2024", "2025")) - len(pool)
    write_result("annotation_C_sample_test", {"pool": len(pool), "excluded_prev_report_without_text_or_photos": excluded,
                                              "items": len(items), "spares": args.spares,
                                              "grid_size": {"median": statistics.median(len(i["grid"]) for i in items),
                                                            "max": max(len(i["grid"]) for i in items)}},
                 [ROOT / "work" / "photo_filter.csv"])
    print(f"C：{len(items)} 題 → {path}")


def transformed_copy(crop, rng, cfg):
    """A crop / flip / colour copy of a photo (make_synthetic.py's transforms), for C's checks and practice."""
    import make_synthetic as ms
    from PIL import Image
    s = cfg["synthetic"]
    with Image.open(crop_path(crop)) as im:
        im = im.convert("RGB")
    names = rng.sample(["crop", "flip", "color"], 2)
    for n in names:
        im = {"crop": ms.t_crop, "flip": ms.t_flip, "color": ms.t_color}[n](im, rng, s)
        if isinstance(im, tuple):
            im = im[0]
    return im, names


def cmd_c_qc(args):
    """Checks (test firms, not in the items: the query's copy hidden in its grid) and practice / examples (development
    firms). The copy's position in the grid is the answer."""
    cfg = cfg_all()
    rows = filter_rows()
    items = read_jsonl(ROOT / "annotations" / "test" / "C_items.jsonl")
    used = {i["query"] for i in items}
    kept = collections.defaultdict(list)
    image_only = {d["report"] for d in rows if d["image_only_report"] == "1"}
    for d in rows:
        if d["keep"] == "1":
            kept[d["report"]].append(d)
    for k in kept:
        kept[k].sort(key=lambda d: (int(d["page"]), int(d["idx"])))
    rng = random.Random(f"{cfg['seed']}:C:qc")
    out_dir = work("C") / "copies"
    out_dir.mkdir(parents=True, exist_ok=True)

    def make(split, n, role):
        pool = sorted(d["crop"] for d in rows if d["keep"] == "1" and d["split"] == split and d["year"] in ("2024", "2025")
                      and d["crop"] not in used and f"{d['firm_id']}_{int(d['year']) - 1}" not in image_only
                      and kept.get(f"{d['firm_id']}_{int(d['year']) - 1}"))
        out = []
        for c in rng.sample(pool, n):
            used.add(c)
            prev = f"{c.split('_')[0]}_{int(c.split('_')[1]) - 1}"
            grid = [d["crop"] for d in kept[prev]]
            im, names = transformed_copy(c, rng, cfg)
            name = f"copy_{hashlib.sha1(c.encode()).hexdigest()[:10]}.jpg"
            im.save(out_dir / name, "JPEG", quality=88)
            pos = rng.randint(0, len(grid))
            grid = grid[:pos] + [f"copies/{name}"] + grid[pos:]
            out.append({"item": iid("C", f"qc|{c}"), "role": role, "query": c, "grid": grid, "answer": [pos],
                        "copy": name, "transforms": names, "split": split})
        return out
    n_checks = math.ceil(sum(not i["spare"] for i in items) / TASKS["C"]["batch"]) * max(1, TASKS["C"]["batch"] // TASKS["C"]["per_check"]) + 4
    rows_out = make("test", n_checks, "check") + make("dev", TASKS["C"]["practice"] * 2, "practice") + make("dev", 2, "example")
    write_jsonl(qc("C", "qc.jsonl"), rows_out)
    print(dict(collections.Counter(r["role"] for r in rows_out)))


def c_img(name, side):
    p = work("C") / name if name.startswith("copies/") else crop_path(name)
    return jpeg_uri(p, side, 75 if side <= SIDE["C_thumb"] else QUALITY)


# ---------------------------------------------------------------- D: items
def page_boxes(report, page):
    """Every image the extraction found on the page (work/extract/<report>_pairs.jsonl), as (crop, bbox in points)."""
    out = []
    for line in open(ROOT / "work" / "extract" / f"{report}_pairs.jsonl", encoding="utf-8"):
        r = json.loads(line)
        if r["page"] == page:
            out.append((r["crop"], r["bbox"]))
    return out


def cmd_d_sample(args):
    """README: 200 random test pages, no cover pages (first and last), no reports without a text layer."""
    cfg = cfg_all()
    path = ROOT / "annotations" / "test" / "D_items.jsonl"
    if path.exists() and not args.force:
        sys.exit(f"{path} 已經存在")
    cat = list(csv.DictReader(open(ROOT / cfg["paths"]["catalog"], encoding="utf-8-sig", newline="")))
    rows = filter_rows()
    image_only = {d["report"] for d in rows if d["image_only_report"] == "1"}
    pages = []
    for r in cat:
        rep = f"{r['firm_id'].strip()}_{r['year'].strip()}"
        if r["split"].strip() != "test" or rep in image_only:
            continue
        n = int(r["pages"])
        pages += [(rep, p) for p in range(2, n)]
    rng = random.Random(f"{cfg['seed']}:D:test")
    pick = rng.sample(sorted(pages), args.n)
    items = [{"item": iid("D", f"{rep}|{p}"), "report": rep, "page": p, "split": "test",
              "boxes": [c for c, _ in page_boxes(rep, p)]} for rep, p in pick]
    write_jsonl(path, items)
    write_result("annotation_D_sample_test", {"pages_in_pool": len(pages), "items": len(items),
                                              "pages_without_boxes": sum(1 for i in items if not i["boxes"])},
                 [ROOT / cfg["paths"]["catalog"], ROOT / "work" / "photo_filter.csv"])
    print(f"D：{len(items)} 頁 → {path}")


def cmd_d_qc(args):
    """Checks (test pages not in the items) and practice / examples (development pages): one kept photo's box removed;
    that photo is the answer."""
    cfg = cfg_all()
    rows = filter_rows()
    used = {(i["report"], i["page"]) for i in read_jsonl(ROOT / "annotations" / "test" / "D_items.jsonl")}
    rng = random.Random(f"{cfg['seed']}:D:qc")
    out = []

    def make(split, n, role):
        cands = sorted({(d["report"], int(d["page"])) for d in rows if d["split"] == split and d["keep"] == "1"} - used)
        for rep, p in rng.sample(cands, n):
            used.add((rep, p))
            boxes = page_boxes(rep, p)
            photos = [c for c, _ in boxes if any(d["crop"] == c and d["keep"] == "1" for d in rows if d["report"] == rep)]
            removed = rng.choice(photos)
            out.append({"item": iid("D", f"qc|{rep}|{p}"), "role": role, "report": rep, "page": p, "split": split,
                        "boxes": [c for c, _ in boxes if c != removed], "removed": removed,
                        "removed_bbox": dict(boxes)[removed]})
    n_checks = math.ceil(len(read_jsonl(ROOT / "annotations" / "test" / "D_items.jsonl")) / TASKS["D"]["batch"]) * \
        (TASKS["D"]["batch"] // TASKS["D"]["per_check"]) + 5
    make("test", n_checks, "check")
    make("dev", TASKS["D"]["practice"] * 2, "practice")
    make("dev", 2, "example")
    write_jsonl(qc("D", "qc.jsonl"), out)
    print(dict(collections.Counter(r["role"] for r in out)))


def render_page(report, page, side):
    """The page as a JPEG data URI, and its size in points."""
    import pymupdf
    with pymupdf.open(ROOT / "data" / "raw" / f"{report}.pdf") as doc:
        pg = doc[page - 1]
        z = side / max(pg.rect.width, pg.rect.height)
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
        uri = "data:image/jpeg;base64," + base64.b64encode(pix.tobytes("jpg", jpg_quality=QUALITY)).decode()
        return uri, pg.rect.width, pg.rect.height


def d_view(it):
    """{page image, boxes in % of the page} for an item (real, check or practice)."""
    uri, W, H = render_page(it["report"], it["page"], SIDE["D_page"])
    bb = dict(page_boxes(it["report"], it["page"]))
    boxes = []
    for k, c in enumerate(it["boxes"], 1):
        x0, y0, x1, y1 = bb[c]
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        boxes.append({"b": f"b{k:02d}", "x": round(100 * x0 / W, 2), "y": round(100 * y0 / H, 2),
                      "w": round(100 * (x1 - x0) / W, 2), "h": round(100 * (y1 - y0) / H, 2)})
    return uri, boxes, (W, H)


def pct_box(bbox, W, H):
    x0, y0, x1, y1 = bbox
    return {"x": round(100 * x0 / W, 2), "y": round(100 * y0 / H, 2), "w": round(100 * (x1 - x0) / W, 2), "h": round(100 * (y1 - y0) / H, 2)}


def hit(drawn, target):
    """A drawn box finds the target when it covers at least half of the target and the target is at least a third
    of the drawn box (so a box around the whole page does not count)."""
    ix = min(drawn["x"] + drawn["w"], target["x"] + target["w"]) - max(drawn["x"], target["x"])
    iy = min(drawn["y"] + drawn["h"], target["y"] + target["h"]) - max(drawn["y"], target["y"])
    if ix <= 0 or iy <= 0:
        return False
    inter = ix * iy
    return inter >= 0.5 * target["w"] * target["h"] and inter >= (drawn["w"] * drawn["h"]) / 3


# ---------------------------------------------------------------- the batch page (shared engine, one item renderer per task)
ENGINE_JS = r"""
const KEY='task'+META.task+'-'+META.page_id;
let S={idx:0, seq:0, ans:{}, sec:{}, downloads:0};
try{const s=JSON.parse(localStorage.getItem(KEY)||'null'); if(s&&s.page_id===META.page_id&&s.state) S=s.state;}catch(err){}
function save(){try{localStorage.setItem(KEY, JSON.stringify({page_id:META.page_id, state:S}));}catch(err){}}
let t0=null;
function startClock(){ if(t0===null && S.idx<ITEMS.length && !document.hidden) t0=performance.now(); }  // 10/20: render() after a click used to restart the clock and drop the time before it (C, D, R2)
function stopClock(){ if(t0!==null && S.idx<ITEMS.length){ const id=ITEMS[S.idx].item; S.sec[id]=(S.sec[id]||0)+(performance.now()-t0)/1000; } t0=null; save(); }
document.addEventListener('visibilitychange',()=>{ if(document.hidden) stopClock(); else startClock(); });
window.addEventListener('pagehide', stopClock);
function rec(id){ return S.ans[id]||(S.ans[id]=Object.assign(TASK.blank(), {seq:null, changes:0})); }
function touched(a){ if(a.seq===null) a.seq=++S.seq; else a.changes++; save(); }
function answered(){ return ITEMS.filter(it=>S.ans[it.item]&&TASK.done(S.ans[it.item])).length; }
function render(){
  document.getElementById('prog').textContent='已答 '+answered()+' / '+ITEMS.length;
  if(S.idx>=ITEMS.length){ finish(); return; }
  const it=ITEMS[S.idx], a=rec(it.item);
  document.getElementById('stage').innerHTML='<p class="mut">第 '+(S.idx+1)+' 題，共 '+ITEMS.length+' 題</p>'+TASK.html(it,a)+
    '<p class="row"><button onclick="back()"'+(S.idx===0?' disabled':'')+'>&larr; 上一題</button> '+
    '<button onclick="next()">下一題 &rarr;（Enter）</button></p><p id="msg" class="bad row"></p>';
  if(TASK.after) TASK.after(it,a);
  startClock();
}
function next(){ if(S.idx>=ITEMS.length) return; const a=rec(ITEMS[S.idx].item);
  if(!TASK.done(a)){ document.getElementById('msg').textContent=TASK.need; return; }
  stopClock(); S.idx++; save(); render(); }
function back(){ if(S.idx>0){ stopClock(); S.idx--; save(); render(); } }
function pad(x){ return String(x).padStart(2,'0'); }
function finish(){
  const n=ITEMS.length, done=answered();
  document.getElementById('stage').innerHTML='<h2>第 '+META.batch+' 批：已答 '+done+' / '+n+'</h2>'+
    (done<n?'<p class="bad">還有題目沒答，按「上一題」回去補。</p>':'')+
    '<p class="row"><button class="big" onclick="dl()">下載答案</button></p>'+
    '<p>下載後，把檔案（檔名開頭是 <b>'+META.task+'_'+META.annotator+'_batch'+pad(META.batch)+'_</b>）用 email 寄給黃老師，不要改檔名或內容。</p>'+
    '<p class="row"><button onclick="back()">&larr; 上一題</button></p>';
}
function dl(){
  const d=new Date(), stamp=d.getFullYear()+pad(d.getMonth()+1)+pad(d.getDate())+'_'+pad(d.getHours())+pad(d.getMinutes())+pad(d.getSeconds());
  const answers=ITEMS.map((it,i)=>{ const a=S.ans[it.item]||Object.assign(TASK.blank(),{seq:null,changes:0});
    return Object.assign({item:it.item, position:i+1}, TASK.out(a, it), {seconds:Math.round((S.sec[it.item]||0)*10)/10, answered_seq:a.seq||null, changes:a.changes||0}); });
  S.downloads++; save();
  const out={page_version:META.page_version, task:META.task, annotator:META.annotator, batch:META.batch, version:META.version, page_id:META.page_id,
             downloaded_at:d.toISOString(), download_no:S.downloads, n_items:ITEMS.length, answers:answers};
  const blob=new Blob([JSON.stringify(out,null,1)],{type:'application/json'});
  const l=document.createElement('a'); l.href=URL.createObjectURL(blob); l.download=META.task+'_'+META.annotator+'_batch'+pad(META.batch)+'_'+stamp+'.json';
  let clicked=true; try{ l.click(); }catch(err){ clicked=false; }
  const box=document.getElementById('dlbox');   // 10/23: always show what was produced; the JSON can be copied if the download is blocked
  if(box){ box.hidden=false; document.getElementById('dlmsg').textContent=(clicked?'已產生 ':'瀏覽器擋住下載，請手動存：')+l.download+'（第 '+S.downloads+' 次下載，已答 '+answered()+' / '+ITEMS.length+'）。'+
    '如果沒有看到下載，把下面的內容全選複製，貼到記事本，存成上面的檔名（副檔名 .json）。'; document.getElementById('dltxt').value=JSON.stringify(out,null,1); }
}
document.addEventListener('keydown',ev=>{
  if(S.idx>=ITEMS.length){ if(ev.key==='ArrowLeft'||ev.key==='Backspace'){ ev.preventDefault(); back(); } return; }
  if(TASK.key && TASK.key(ev)) return;
  if(ev.key==='Enter'||ev.key==='ArrowRight'){ ev.preventDefault(); next(); }
  else if(ev.key==='ArrowLeft'||(ev.key==='Backspace'&&ev.target.tagName!=='INPUT')){ ev.preventDefault(); back(); }
});
try{ if(window.matchMedia('(pointer:coarse)').matches && Math.min(screen.width, screen.height)<700) document.getElementById('phone').hidden=false; }catch(err){}
render();
"""

TASK_JS = {
"B": r"""
const TASK={
  blank:()=>({answer:null, not_sure:false}),
  html:(it,a)=>'<div class="two"><img src="'+IMG[it.l]+'" alt=""><img src="'+IMG[it.r]+'" alt=""></div>'+
    '<p class="row"><button class="big'+(a.answer==='Yes'?' sel':'')+'" onclick="pick(\'Yes\')">1　是</button>'+
    '<button class="big'+(a.answer==='No'?' sel':'')+'" onclick="pick(\'No\')">2　不是</button>'+
    '<label class="ns"><input type="checkbox" id="ns"'+(a.not_sure?' checked':'')+' onchange="ns(this.checked)"> 3　不確定</label></p>',
  done:a=>!!a.answer, need:'先選 1（是）或 2（不是）。',
  out:a=>({answer:a.answer, not_sure:!!a.not_sure}),
  key:ev=>{ if(ev.key==='1'){pick('Yes');return true;} if(ev.key==='2'){pick('No');return true;}
            if(ev.key==='3'){const b=document.getElementById('ns'); b.checked=!b.checked; ns(b.checked); return true;} return false; }
};
function pick(x){ const a=rec(ITEMS[S.idx].item); if(a.answer!==x){ a.answer=x; touched(a); } stopClock(); render(); }
function ns(v){ rec(ITEMS[S.idx].item).not_sure=v; save(); }
""",
"C": r"""
// 10/21: B-style. The tool's top pick (it.h) sits beside the query for 1 是 / 2 不是; the other candidates are below
// for extra clicks. selected holds the hint's index when the answer is 是; no_match = 不是 and nothing else clicked.
const TASK={
  blank:()=>({hint_answer:null, selected:[], no_match:false}),
  html:(it,a)=>'<p class="mut">左：這張照片。右：<b>程式猜前一年報告裡的這張是同一張</b>。是不是同一張（或同一個鏡頭）？</p>'+
    '<div class="two"><img src="'+IMG[it.q]+'" alt=""><div class="hb"><img src="'+IMG[it.grid[it.h]]+'" alt=""><span class="z" onclick="zoom(event,\''+it.grid[it.h]+'\')">🔍</span></div></div>'+
    '<p class="row"><button class="big'+(a.hint_answer==='Yes'?' sel':'')+'" onclick="pick(\'Yes\')">1　是</button>'+
    '<button class="big'+(a.hint_answer==='No'?' sel':'')+'" onclick="pick(\'No\')">2　不是</button></p>'+
    '<p>其他比較像的（照頁碼排）：有同一張或同一個鏡頭的也點選，再點一下取消；點 🔍 放大。都沒有就不用點。</p><div class="tg">'+
    it.grid.map((g,k)=>k===it.h?'':'<div class="t'+(a.selected.includes(k)?' sel':'')+'" onclick="tog('+k+')"><img src="'+IMG[g]+'" alt=""><span class="z" onclick="zoom(event,\''+g+'\')">🔍</span></div>').join('')+
    '</div><div id="zoom" hidden onclick="this.hidden=true"><img id="zimg" alt=""></div>',
  done:a=>!!a.hint_answer, need:'先回答右邊那張是不是同一張（1 或 2）。',
  out:(a,it)=>{ it=it||ITEMS[S.idx]; const s=a.selected.filter(k=>k!==it.h); if(a.hint_answer==='Yes') s.push(it.h);   // 10/23: the item is passed in (dl() on the finish screen has no current item)
    return {hint:it.h, hint_answer:a.hint_answer, selected:s.sort((x,y)=>x-y), no_match:a.hint_answer==='No'&&s.length===0}; },
  key:ev=>{ if(ev.key==='1'){pick('Yes');return true;} if(ev.key==='2'){pick('No');return true;} return false; }
};
function pick(x){ const a=rec(ITEMS[S.idx].item); if(a.hint_answer!==x){ a.hint_answer=x; touched(a); } render(); }
function tog(k){ const a=rec(ITEMS[S.idx].item); const i=a.selected.indexOf(k); if(i>=0) a.selected.splice(i,1); else a.selected.push(k); touched(a); render(); }
function zoom(ev,g){ ev.stopPropagation(); document.getElementById('zimg').src=IMG[g]; document.getElementById('zoom').hidden=false; }
""",
"D": r"""
const STATES=['ok','partial','merged','split'], ZH={ok:'',partial:'框不完整',merged:'框到多張',split:'被切開'};
const TASK={
  blank:()=>({boxes:{}, drawn:[], no_photos:false, seen:true}),
  html:(it,a)=>'<p class="mut">點框改狀態（框不完整 → 框到多張 → 被切開 → 回到正常）；在沒有框的地方按住拖曳，畫出漏掉的照片；點畫的框可以刪掉。</p>'+
    '<p><b>框到圖表、表格、logo、導覽列的框不用管。</b>只看照片：每張照片都要有一個剛好的框，沒有就畫；整頁沒有照片才勾「這頁沒有照片」。</p>'+
    '<div class="pg" id="pg"><img src="'+IMG[it.p]+'" alt="" draggable="false">'+
    it.boxes.map(b=>{const s=a.boxes[b.b]||'ok'; return '<div class="bx '+s+'" style="left:'+b.x+'%;top:'+b.y+'%;width:'+b.w+'%;height:'+b.h+'%" onmousedown="event.stopPropagation()" onclick="cyc(\''+b.b+'\')"><span>'+ZH[s]+'</span></div>';}).join('')+
    a.drawn.map((d,k)=>'<div class="dr" style="left:'+d.x+'%;top:'+d.y+'%;width:'+d.w+'%;height:'+d.h+'%" onmousedown="event.stopPropagation()" onclick="del('+k+')"><span>漏掉的照片</span></div>').join('')+
    '<div class="dr tmp" id="tmp" hidden></div></div>'+
    '<p class="row"><label class="ns"><input type="checkbox" id="np"'+(a.no_photos?' checked':'')+' onchange="np(this.checked)"> 這頁沒有照片</label></p>',
  after:(it,a)=>{ const pg=document.getElementById('pg'); let st=null;
    const pos=ev=>{const r=pg.getBoundingClientRect(); return {x:Math.max(0,Math.min(100,100*(ev.clientX-r.left)/r.width)), y:Math.max(0,Math.min(100,100*(ev.clientY-r.top)/r.height))};};
    pg.onmousedown=ev=>{ ev.preventDefault(); st=pos(ev); };
    window.onmousemove=ev=>{ if(!st) return; const p=pos(ev), t=document.getElementById('tmp'); t.hidden=false;
      t.style.left=Math.min(st.x,p.x)+'%'; t.style.top=Math.min(st.y,p.y)+'%'; t.style.width=Math.abs(p.x-st.x)+'%'; t.style.height=Math.abs(p.y-st.y)+'%'; };
    window.onmouseup=ev=>{ if(!st) return; const p=pos(ev), w=Math.abs(p.x-st.x), h=Math.abs(p.y-st.y);
      if(w>1.5&&h>1.5){ a.drawn.push({x:+Math.min(st.x,p.x).toFixed(2), y:+Math.min(st.y,p.y).toFixed(2), w:+w.toFixed(2), h:+h.toFixed(2)}); a.no_photos=false; touched(a); }
      st=null; render(); };
  },
  done:a=>true, need:'',
  out:a=>({boxes:Object.fromEntries(Object.entries(a.boxes).filter(([k,v])=>v!=='ok')), drawn:a.drawn, no_photos:!!a.no_photos}),
  key:ev=>false
};
function cyc(b){ const a=rec(ITEMS[S.idx].item); const s=a.boxes[b]||'ok'; a.boxes[b]=STATES[(STATES.indexOf(s)+1)%STATES.length]; if(a.boxes[b]!=='ok') a.no_photos=false; touched(a); render(); }
function del(k){ const a=rec(ITEMS[S.idx].item); a.drawn.splice(k,1); touched(a); render(); }
// 10/20: 這頁沒有照片 and box marks exclude each other (a2 batches 1-2 had both on six pages)
function np(v){ const a=rec(ITEMS[S.idx].item); a.no_photos=v; if(v){ a.boxes={}; a.drawn=[]; } touched(a); render(); }
""",
}

TASK_CSS = """
#top{display:flex;gap:16px;align-items:baseline;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:6px}
.row{text-align:center} button.sel{outline:3px solid var(--acc);background:var(--acc);color:var(--bg)} button:disabled{opacity:.45}
.ns{font-size:18px;margin-left:12px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0} .two img{width:100%;max-height:58vh;object-fit:contain;background:var(--card);border-radius:8px}
.q img{max-width:100%;max-height:42vh;display:block;margin:0 auto;border-radius:8px}
.tg{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.t{position:relative;border:3px solid transparent;border-radius:6px;cursor:pointer;background:var(--card)} .t img{width:100%;height:120px;object-fit:contain;display:block}
.t.sel{border-color:var(--acc)}
.hb{position:relative;border:4px solid var(--acc);border-radius:8px} .hb::before{content:'程式猜這張';position:absolute;left:6px;top:6px;background:var(--acc);color:var(--bg);padding:0 8px;border-radius:4px;font-size:14px;z-index:1}
.hb img{width:100%;max-height:58vh;object-fit:contain;display:block;background:var(--card)}
.cw{display:grid;grid-template-columns:minmax(260px,34%) 1fr;gap:16px;align-items:start} .cw .q{position:sticky;top:6px}
.cw .q img{max-height:82vh} .cw .tg{grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px} .cw .t img{height:225px}
.cw .t.sel{outline:3px solid var(--acc)} .cw .t.sel::after{content:'同一張';position:absolute;left:4px;top:4px;background:var(--acc);color:var(--bg);padding:0 8px;border-radius:4px;font-size:14px}
@media (max-width:800px){.cw{grid-template-columns:1fr} .cw .q{position:static}}
.z{position:absolute;right:2px;bottom:2px;background:#fffc;border-radius:4px;padding:0 4px;font-size:14px}
#zoom{position:fixed;inset:0;background:#000c;display:flex;align-items:center;justify-content:center;z-index:9} #zoom img{max-width:92vw;max-height:92vh}
#zoom[hidden]{display:none}  /* 10/20 (Eden: C 完全不能點): display:flex overrode [hidden], the dark layer covered the page */
.pg{position:relative;display:inline-block;max-width:100%;user-select:none;cursor:crosshair} .pg img{display:block;max-width:100%;max-height:78vh}
.bx{position:absolute;box-sizing:border-box;border:2px solid #1a56c4;cursor:pointer} .bx span{position:absolute;left:0;top:0;background:#b3261e;color:#fff;font-size:12px;padding:0 3px}
.bx.partial,.bx.merged,.bx.split{border:3px solid #b3261e;background:#b3261e22}
.dr{position:absolute;box-sizing:border-box;border:3px dashed #e8590c;background:#e8590c22;cursor:pointer} .dr span{position:absolute;left:0;top:0;background:#e8590c;color:#fff;font-size:12px;padding:0 3px}
.tmp{pointer-events:none}
"""


def batch_page(task, e, items, imgs):
    meta = {"task": task, "annotator": e["annotator"], "batch": e["batch"], "version": e["version"], "page_id": e["page_id"],
            "page_version": PAGE_VERSION, "n": len(items)}
    for it in items:   # nothing but opaque ids and image keys in the page (README 原則)
        if not re.fullmatch(task + r"[0-9a-f]{8}", it["item"]):
            raise SystemExit(f"題號格式不對：{it['item']}")
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{TASKS[task]['zh']} – {e['annotator']} 第 {e['batch']} 批</title>
<style>{CSS}{TASK_CSS}</style></head><body>
<div id="top"><b>{TASKS[task]['zh']}</b><span class="mut">標註者 {e['annotator']}・第 {e['batch']} 批{'' if e['version'] == 1 else '（重做版 ' + str(e['version']) + '）'}</span><span id="prog" class="mut"></span> <button id="dlnow" onclick="dl()" title="還沒做完也可以先下載；之後接著做，做完再下載一次（檢查時以最新的檔為準）">先下載目前答案</button></div>
<p id="phone" class="bad" hidden>請用筆電或桌機的瀏覽器開這個檔案，不要用手機。</p>
<div id="stage"></div>
<div id="dlbox" hidden><p id="dlmsg" class="bad"></p><textarea id="dltxt" readonly style="width:100%;height:110px;font-size:11px"></textarea></div>
<p class="mut">按 Enter 或 →＝下一題，←＝上一題。進度存在這個瀏覽器裡：關掉再開（同一台電腦、同一個瀏覽器）可以接著做。</p>
<script>
const META={json.dumps(meta)};
const ITEMS={json.dumps(items)};
const IMG={json.dumps(imgs)};
{TASK_JS[task]}
{ENGINE_JS}
</script></body></html>"""


def item_views(task, rows):
    """Page items (opaque ids and image keys) and the image dictionary for a list of real / check rows."""
    items, imgs = [], {}

    def key(prefix, name, maker):
        k = prefix + hashlib.sha1(name.encode()).hexdigest()[:10]
        if k not in imgs:
            imgs[k] = maker()
        return k
    for r in rows:
        if task == "B":
            items.append({"item": r["item"], "l": key("i", r["a"], lambda r=r: b_img(r["a"], False)),
                          "r": key("i", r["b"] + ("#s" if r.get("b_synthetic") else ""), lambda r=r: b_img(r["b"], r.get("b_synthetic")))})
        elif task == "C":
            items.append({"item": r["item"], "q": key("q", r["query"], lambda r=r: c_img(r["query"], SIDE["C_query"])),
                          "grid": [key("g", g, lambda g=g: c_img(g, SIDE["C_thumb"])) for g in r["grid"]], "h": r.get("hint", 0)})
        else:
            uri, boxes, _ = d_view(r)
            items.append({"item": r["item"], "p": key("p", f"{r['report']}|{r['page']}|{len(r['boxes'])}", lambda uri=uri: uri), "boxes": boxes})
    return items, imgs


# ---------------------------------------------------------------- practice and example pages
PRACTICE_JS = {
"B": r"""
function view(d){ return '<div class="two"><img src="'+d.l+'" alt=""><img src="'+d.r+'" alt=""></div>'+
  '<p class="row"><button class="big" onclick="ans(\'Yes\')">1　是</button><button class="big" onclick="ans(\'No\')">2　不是</button></p><div id="fb"></div>'; }
function grade(d,a){ return a===d.answer; }
function truth(d){ return '答案是：'+(d.answer==='Yes'?'是':'不是')+'。'+d.reason; }
document.addEventListener('keydown',ev=>{ if(ev.key==='1') ans('Yes'); else if(ev.key==='2') ans('No'); else if(ev.key==='Enter') next(); });
""",
"C": r"""
let SEL=[], HA=null;
function view(d){ SEL=[]; HA=null; return '<p class="mut">左：這張照片。右：<b>程式猜前一年報告裡的這張是同一張</b>。是不是？</p>'+
  '<div class="two"><img src="'+d.q+'" alt=""><div class="hb"><img src="'+d.grid[d.hint]+'" alt=""></div></div>'+
  '<p class="row"><button class="big" id="hy" onclick="ha(\'Yes\')">1　是</button><button class="big" id="hn" onclick="ha(\'No\')">2　不是</button></p>'+
  '<p>其他比較像的：有同一張的也點選。</p><div class="tg">'+d.grid.map((g,k)=>k===d.hint?'':'<div class="t" id="t'+k+'" onclick="tg('+k+')"><img src="'+g+'" alt=""></div>').join('')+
  '</div><p class="row"><button class="big" onclick="submit(d)">送出</button></p><div id="fb"></div>'; }
function ha(x){ if(answered) return; HA=x; document.getElementById('hy').classList.toggle('sel',x==='Yes'); document.getElementById('hn').classList.toggle('sel',x==='No'); }
function submit(d){ if(answered) return; if(!HA){ alert('先回答右邊那張是不是同一張（1 或 2）。'); return; } ans(HA==='Yes'?SEL.concat([d.hint]):SEL.slice()); }
function tg(k){ if(answered) return; const i=SEL.indexOf(k); if(i>=0) SEL.splice(i,1); else SEL.push(k); document.getElementById('t'+k).classList.toggle('sel'); }
function grade(d,a){ return d.answer.every(x=>a.includes(x)); }
function truth(d){ d.answer.forEach(x=>{ const el=document.getElementById('t'+x)||document.querySelector('.hb'); el.style.outline='4px solid #137333'; });
  return (d.answer.includes(d.hint)?'程式猜的那張就是同一張':'程式猜錯了，綠框那張才是同一張')+'（'+d.reason+'）。'; }
document.addEventListener('keydown',ev=>{ if(ev.key==='1') ha('Yes'); else if(ev.key==='2') ha('No'); });
document.addEventListener('keydown',ev=>{ if(ev.key==='Enter') next(); });
""",
"D": r"""
let DR=[];
function view(d){ DR=[]; setTimeout(()=>arm(d),0); return '<p class="mut">在沒有框的照片上按住拖曳，畫出漏掉的照片；畫錯了點那個框刪掉。</p><div class="pg" id="pg"><img src="'+d.p+'" alt="" draggable="false">'+
  d.boxes.map(b=>'<div class="bx" style="left:'+b.x+'%;top:'+b.y+'%;width:'+b.w+'%;height:'+b.h+'%" onmousedown="event.stopPropagation()"></div>').join('')+
  '<div class="dr tmp" id="tmp" hidden></div></div><p class="row"><button class="big" onclick="ans(DR.slice())">送出</button></p><div id="fb"></div>'; }
function arm(d){ const pg=document.getElementById('pg'); let st=null;
  const pos=ev=>{const r=pg.getBoundingClientRect(); return {x:100*(ev.clientX-r.left)/r.width, y:100*(ev.clientY-r.top)/r.height};};
  pg.onmousedown=ev=>{ if(answered) return; ev.preventDefault(); st=pos(ev); };
  window.onmousemove=ev=>{ if(!st) return; const p=pos(ev), t=document.getElementById('tmp'); t.hidden=false; t.style.left=Math.min(st.x,p.x)+'%'; t.style.top=Math.min(st.y,p.y)+'%'; t.style.width=Math.abs(p.x-st.x)+'%'; t.style.height=Math.abs(p.y-st.y)+'%'; };
  window.onmouseup=ev=>{ if(!st) return; const p=pos(ev), w=Math.abs(p.x-st.x), h=Math.abs(p.y-st.y);
    if(w>1.5&&h>1.5){ const b={x:Math.min(st.x,p.x), y:Math.min(st.y,p.y), w:w, h:h}; DR.push(b);
      const el=document.createElement('div'); el.className='dr'; el.style.cssText='left:'+b.x+'%;top:'+b.y+'%;width:'+b.w+'%;height:'+b.h+'%';
      el.onmousedown=e=>e.stopPropagation(); el.onclick=()=>{ if(answered) return; DR.splice(DR.indexOf(b),1); el.remove(); }; pg.appendChild(el); }
    st=null; document.getElementById('tmp').hidden=true; };
}
function hit(r,t){ const ix=Math.min(r.x+r.w,t.x+t.w)-Math.max(r.x,t.x), iy=Math.min(r.y+r.h,t.y+t.h)-Math.max(r.y,t.y);
  if(ix<=0||iy<=0) return false; const inter=ix*iy; return inter>=0.5*t.w*t.h && inter>=r.w*r.h/3; }
function grade(d,a){ return a.some(r=>hit(r,d.answer)); }
function truth(d){ const t=d.answer, el=document.createElement('div'); el.className='dr'; el.style.cssText='left:'+t.x+'%;top:'+t.y+'%;width:'+t.w+'%;height:'+t.h+'%;border-color:#137333;background:#13733322';
  document.getElementById('pg').appendChild(el); return '綠框這張照片沒有被框到，要畫一個框標成「漏掉的照片」。'; }
document.addEventListener('keydown',ev=>{ if(ev.key==='Enter') next(); });
""",
}


def practice_page(task, k, data):
    need = TASKS[task]["need"]
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{TASKS[task]['zh']} 練習第 {k} 組</title>
<style>{CSS}{TASK_CSS}</style></head><body>
<h1>{TASKS[task]['zh']}：練習第 {k} 組</h1>
<p>每答一題就會看到正確答案，按 <b>Enter</b> 或「下一題」繼續。{len(data)} 題要答對 <b>{need} 題</b>，才能開始正式題。</p>
<div id="stage"></div>
<script>
const DATA={json.dumps(data, ensure_ascii=False)};
const NEED={need}; let i=0, right=0, answered=false; const log=[];
{PRACTICE_JS[task]}
function show(){{
  const s=document.getElementById('stage');
  if(i>=DATA.length){{
    const ok=right>=NEED;
    s.innerHTML='<h2>成績：'+right+' / '+DATA.length+'</h2><p class="'+(ok?'ok':'bad')+'">'+(ok?'通過了。告訴黃老師，然後開始第 1 批。':'還沒通過。再看一次例子，然後做另一組練習。')+'</p><button onclick="dl()">存下我的成績</button>';
    return;
  }}
  answered=false; s.innerHTML='<p class="mut">第 '+(i+1)+' 題，共 '+DATA.length+' 題</p>'+view(DATA[i]);
}}
function ans(a){{
  if(answered) return; answered=true;
  const d=DATA[i], ok=grade(d,a); if(ok) right++;
  log.push({{id:d.id, answer:a, correct:ok}});
  document.getElementById('fb').innerHTML='<p class="'+(ok?'ok':'bad')+'">'+(ok?'答對了。':'不對。')+'</p><p>'+truth(d)+'</p><button onclick="next()">下一題（Enter）</button>';
}}
function next(){{ if(answered){{ i++; show(); }} }}
function dl(){{
  let code=(prompt('輸入黃老師給你的代號（a1 或 a2），不要輸入名字：')||'').trim().toLowerCase();
  if(!/^a[12]$/.test(code)) code='unknown';
  const blob=new Blob([JSON.stringify({{task:'{task}', set:{k}, annotator:code, score:right, of:DATA.length, need:NEED, answers:log}},null,1)],{{type:'application/json'}});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='{task}_practice_{k}_'+code+'.json'; a.click();
}}
show();
</script></body></html>"""


def practice_data(task, rows):
    out = []
    for r in rows:
        if task == "B":
            out.append({"id": r["item"], "l": b_img(r["a"], False), "r": b_img(r["b"], r["b_synthetic"]), "answer": r["answer"], "reason": r["reason"]})
        elif task == "C":
            out.append({"id": r["item"], "q": c_img(r["query"], SIDE["C_query"]), "grid": [c_img(g, SIDE["C_thumb"]) for g in r["grid"]],
                        "hint": r.get("hint", 0), "answer": r["answer"], "reason": "、".join(TRANSFORM_ZH[t] for t in r["transforms"]) + "過"})
        else:
            uri, boxes, (W, H) = d_view(r)
            out.append({"id": r["item"], "p": uri, "boxes": boxes, "answer": pct_box(r["removed_bbox"], W, H)})
    return out


def examples_page(task, rows):
    e = html.escape
    cards = []
    for r in rows:
        if task == "B":
            cards.append(f'<div class="ex"><div class="two"><img src="{b_img(r["a"], False)}" alt=""><img src="{b_img(r["b"], r["b_synthetic"])}" alt=""></div>'
                         f'<p><b>{e(r["reason"])}</b></p></div>')
        elif task == "C":
            pos, h = r["answer"][0], r.get("hint", 0)
            grid = "".join(f'<div class="t{" sel" if k == pos else ""}"><img src="{c_img(g, SIDE["C_thumb"])}" alt=""></div>' for k, g in enumerate(r["grid"]) if k != h)
            verdict = "程式猜對了：右邊那張就是同一張，答「是」。" if h == pos else "程式猜錯了：右邊那張不是，答「不是」；下面藍框那張才是同一張，點選它。"
            cards.append(f'<div class="ex"><div class="two"><img src="{c_img(r["query"], SIDE["C_query"])}" alt=""><div class="hb"><img src="{c_img(r["grid"][h], SIDE["C_thumb"])}" alt=""></div></div>'
                         f'<div class="tg">{grid}</div><p><b>{verdict}（{e("、".join(TRANSFORM_ZH[t] for t in r["transforms"]))}過）</b></p></div>')
        else:
            uri, boxes, (W, H) = d_view(r)
            t = pct_box(r["removed_bbox"], W, H)
            bx = "".join(f'<div class="bx" style="left:{b["x"]}%;top:{b["y"]}%;width:{b["w"]}%;height:{b["h"]}%"></div>' for b in boxes)
            cards.append(f'<div class="ex"><div class="pg"><img src="{uri}" alt="">{bx}<div class="dr" style="left:{t["x"]}%;top:{t["y"]}%;width:{t["w"]}%;height:{t["h"]}%"><span>漏掉的照片</span></div></div>'
                         f'<p><b>藍框是程式找到的圖。橘色虛線這張照片沒有框，要自己畫一個框，標成「漏掉的照片」。</b>其他情形：一個框只框到照片的一部分，點它選「框不完整」；一個框框到兩張以上的照片，選「框到多張」；一張照片被好幾個框切開，點其中一個選「被切開」。</p></div>')
    intro = {"B": "左右兩張一樣的是「是」，不一樣的是「不是」。", "C": "上面是要找的照片，下面是前一年報告的照片。",
             "D": "看頁面上的每一張照片，框得對不對。"}[task]
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{TASKS[task]['zh']} 例子</title>
<style>{CSS}{TASK_CSS}.ex{{border-top:1px solid var(--line);padding:16px 0}}</style></head><body>
<h1>{TASKS[task]['zh']}：例子</h1><p>{intro}先看<a href="{task}_guidelines.html">手冊</a>，再把這些例子全部看過。</p>{''.join(cards)}</body></html>"""


# ---------------------------------------------------------------- plan, pages, packages
def real_items(task):
    return read_jsonl(ROOT / "annotations" / "dev" / f"{task}_items.jsonl") + read_jsonl(ROOT / "annotations" / "test" / f"{task}_items.jsonl")


def cmd_pages(args):
    task = args.task
    cfg = cfg_all()
    t = TASKS[task]
    qrows = read_jsonl(qc(task, "qc.jsonl"))
    if not qrows:
        sys.exit(f"先跑 {task.lower()}-qc")
    real = [r for r in real_items(task) if not r.get("spare")]
    if not real:
        sys.exit(f"先跑 {task.lower()}-sample")
    plan_path, key_path = qc(task, "task_plan.json"), qc(task, "checks_key.jsonl")
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else []
    key = read_jsonl(key_path)
    planned = {i for e in plan for i in e["items"]}
    new_real = [r for r in real if r["item"] not in planned]
    checks = [r for r in qrows if r["role"] == "check" and r["item"] not in planned and r["item"] not in {k["item"] for k in key}]
    rng = random.Random(f"{cfg['seed']}:{task}:plan:{len(plan)}")
    rng.shuffle(checks)   # the qc file lists the Yes checks before the No checks
    for ann in t["annotators"]:
        mine = [e for e in plan if e["annotator"] == ann]
        b0 = max((e["batch"] for e in mine), default=0)
        r2 = random.Random(f"{cfg['seed']}:{task}:order:{ann}:{b0}")
        for split in ("dev", "test"):
            ids = [r["item"] for r in new_real if r["split"] == split]
            r2.shuffle(ids)
            for s in range(0, len(ids), t["batch"]):
                part = ids[s:s + t["batch"]]
                n_chk = max(1, round(len(part) / t["per_check"]))
                pool = [c for c in checks if task == "B" or c["split"] == split]
                if len(pool) < n_chk:
                    sys.exit("檢查題不夠")
                chk = [pool.pop(0) for _ in range(n_chk)]
                for c in chk:
                    checks.remove(c)
                b0 += 1
                e = {"task": task, "annotator": ann, "batch": b0, "split": split, "version": 1,
                     "items": interleave(part, [c["item"] for c in chk], r2), "real": len(part), "checks": n_chk}
                e["page_id"] = page_id(e)
                plan.append(e)
                key += [{"annotator": ann, "batch": b0, "item": c["item"], "answer": c.get("answer", c.get("removed"))} for c in chk]
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    write_jsonl(key_path, key)
    rows_by_id = {r["item"]: r for r in real} | {r["item"]: r for r in qrows}
    made = []
    examples = [r for r in qrows if r["role"] == "example"]
    practice = [r for r in qrows if r["role"] == "practice"]
    rng.shuffle(practice)
    sets = [practice[:t["practice"]], practice[t["practice"]:2 * t["practice"]]]
    guide = md_to_html((ROOT / "annotations" / "guidelines" / t["manual"]).read_text(encoding="utf-8"))
    for ann in t["annotators"]:
        stage = work(task) / f"package_{ann}"
        stage.mkdir(parents=True, exist_ok=True)
        for old in stage.glob("*.html"):
            old.unlink()
        (stage / f"{task}_guidelines.html").write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{t['zh']} 手冊</title><style>{CSS}</style></head><body>{guide}</body></html>""", encoding="utf-8")
        (stage / f"{task}_examples.html").write_text(examples_page(task, examples), encoding="utf-8")
        for k, s in enumerate(sets, 1):
            if s:
                (stage / f"{task}_practice_{k}.html").write_text(practice_page(task, k, practice_data(task, s)), encoding="utf-8")
        links = []
        newest = {}
        for e in plan:   # 10/21: only the newest version of each batch is built and linked (C got version 2 with pools)
            if e["annotator"] == ann and (e["batch"] not in newest or e["version"] > newest[e["batch"]]["version"]):
                newest[e["batch"]] = e
        for e in sorted(newest.values(), key=lambda e: e["batch"]):
            items, imgs = item_views(task, [rows_by_id[i] for i in e["items"]])
            name = f"{task}_{ann}_batch{e['batch']:02d}" + (f"_v{e['version']}" if e["version"] > 1 else "") + ".html"
            p = stage / name
            p.write_text(batch_page(task, e, items, imgs), encoding="utf-8")
            links.append((e, name))
            made.append({"annotator": ann, "batch": e["batch"], "split": e["split"], "page": name, "page_id": e["page_id"],
                         "bytes": p.stat().st_size, "items": len(e["items"]), "checks": e["checks"], "sha256": sha256_file(p)})
        rows_html = "".join(f'<li><a href="{n}">第 {e["batch"]} 批</a></li>' for e, n in links)
        (stage / "index.html").write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{t['zh']} – {ann}</title><style>{CSS}</style></head><body>
<h1>{t['zh']}</h1><p>你的代號是 <b>{ann}</b>。請用筆電或桌機的瀏覽器開這些檔案，不要用手機。檔案要放在同一個資料夾裡。</p>
<ol><li>看<a href="{task}_guidelines.html">手冊</a>。</li><li>看<a href="{task}_examples.html">例子</a>。</li>
<li>做<a href="{task}_practice_1.html">練習第 1 組</a>，{t['practice']} 題要答對 {t['need']} 題。沒過就再看一次例子，做<a href="{task}_practice_2.html">練習第 2 組</a>。做完按「存下我的成績」，把檔案寄給黃老師。</li>
<li>照順序做下面每一批。每批做完按「下載答案」，馬上把檔案 email 給黃老師。<ol>{rows_html}</ol></li></ol>
<p class="mut">不要跟其他標註者討論題目。有問題問黃老師。</p></body></html>""", encoding="utf-8")
        z = work(task) / f"package_{ann}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(stage.glob("*.html")):
                zf.write(p, f"task_{task}_{ann}/{p.name}")
        extracted = stage / f"task_{task}_{ann}"   # 10/23: the zip extracted in place (what Eden opens) gets the same files
        if extracted.is_dir():
            for p in sorted(stage.glob("*.html")):
                shutil.copyfile(p, extracted / p.name)
            print(f"  也更新了 {extracted}（{len(list(stage.glob('*.html')))} 個檔）")
        print(f"{task} {ann}：{len(links)} 批 → {z}（{z.stat().st_size / 1e6:.1f} MB）")
    write_result(f"annotation_{task}_pages", {"page_version": PAGE_VERSION, "pages": made, "practice_sets": [len(s) for s in sets],
                                              "examples": len(examples)}, [plan_path, key_path])


# ---------------------------------------------------------------- checking returned answer files
def read_returns(task, ann, plan):
    folder = work(task) / "returns"
    files = sorted(folder.glob(f"{task}_{ann}_batch*.json")) if folder.exists() else []
    cur = {}
    for e in plan:
        if e["annotator"] == ann:
            k = e["batch"]
            if k not in cur or e["version"] > cur[k]["version"]:
                cur[k] = e
    newest, notes = {}, []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        e = cur.get(d.get("batch"))
        if d.get("task") != task or d.get("annotator") != ann or e is None:
            notes.append(f"{f.name}：不是 {ann} 的 {task} 答案檔，略過")
            continue
        if d.get("page_id") != e["page_id"]:
            notes.append(f"{f.name}：頁面版本跟題目表不合（舊版本或被改過），略過")
            continue
        if d["batch"] not in newest or d["downloaded_at"] > newest[d["batch"]][0]["downloaded_at"]:
            newest[d["batch"]] = (d, f.name)
    return cur, newest, notes


def cmd_check(args):
    task, ann = args.task, args.annotator
    t = TASKS[task]
    plan = json.loads(qc(task, "task_plan.json").read_text(encoding="utf-8"))
    # 10/20: checks whose expected answer turned out ambiguous are listed in {task}_checks_void.jsonl and not counted
    void = {r["item"] for r in read_jsonl(qc(task, "checks_void.jsonl")) if r["annotator"] == ann}
    key = {(k["annotator"], k["item"]): k for k in read_jsonl(qc(task, "checks_key.jsonl")) if k["item"] not in void}
    qrows = {r["item"]: r for r in read_jsonl(qc(task, "qc.jsonl"))}
    real = {r["item"]: r for r in real_items(task)}
    cur, newest, notes = read_returns(task, ann, plan)
    for n in notes:
        print("注意：" + n)
    report, alerts, labels = [], [], collections.defaultdict(list)
    for b in sorted(newest):
        d, fname = newest[b]
        e = cur[b]
        ans = {a["item"]: a for a in d["answers"]}
        missing = [i for i in e["items"] if i not in ans or (task == "B" and not ans[i].get("answer"))
                   or (task == "C" and not ans[i].get("hint_answer"))]   # 10/21: the hint's yes / no is the required answer
        wrong = []
        for i in e["items"]:
            k = key.get((ann, i))
            if not k or i not in ans:
                continue
            a = ans[i]
            if task == "B":
                ok = a.get("answer") == k["answer"]
            elif task == "C":
                ok = all(x in (a.get("selected") or []) for x in k["answer"])
            else:
                r = qrows[i]
                W, H = page_size(r)
                ok = any(hit(x, pct_box(r["removed_bbox"], W, H)) for x in (a.get("drawn") or []))
            if not ok:
                wrong.append(i)
        secs = [ans[i]["seconds"] for i in e["items"] if i in ans and ans[i].get("seconds") is not None]
        med = statistics.median(secs) if secs else None
        flags = []
        n_chk = sum(1 for i in e["items"] if (ann, i) in key)
        failed = bool(n_chk) and len(wrong) / n_chk > t["limits"]["check_error"]
        if failed:
            flags.append(f"檢查題錯 {len(wrong)}/{n_chk}（超過 2 成）：這一批要重標，先不匯入")
        if med is not None and med < t["limits"]["median_seconds"]:
            flags.append(f"每題秒數中位數 {med:.1f} 秒（不到 {t['limits']['median_seconds']:g} 秒）：先停，跟標註者確認")
        run = longest_run([ans[i].get("answer") for i in e["items"] if i in ans]) if task == "B" else None
        if run is not None and run >= t["limits"]["same_answer_run"]:
            flags.append(f"連續 {run} 題同一個答案：先停，跟標註者確認")
        if missing:
            flags.append(f"{len(missing)} 題沒答")
        if task == "D":   # 10/20: pages answered both ways (the page now prevents it; older pages did not)
            both = [ans[i]["position"] for i in e["items"] if i in ans and ans[i].get("no_photos")
                    and (ans[i].get("boxes") or ans[i].get("drawn"))]
            if both:
                flags.append(f"第 {'、'.join(map(str, both))} 頁勾了「這頁沒有照片」又改了框：匯入時以「沒有照片」為準，改的框另存")
        alerts += [f"batch {b}: {x}" for x in flags]
        report.append({"batch": b, "split": e["split"], "file": fname, "expected": len(e["items"]), "missing": len(missing),
                       "checks": n_chk, "checks_void": e["checks"] - n_chk, "checks_wrong": len(wrong), "median_seconds": med,
                       "longest_same_answer": run, "flags": flags, "imported": not failed})
        if failed:   # 10/20: a batch that fails its checks is not imported until it is redone
            continue
        for i in e["items"]:
            if i in real and i in ans:
                a = ans[i]
                row = {"item": i, "annotator": ann, "batch": b, "seconds": a.get("seconds"), "source": fname}
                if task == "B":
                    row |= {"answer": a.get("answer"), "not_sure": a.get("not_sure")}
                elif task == "C":
                    grid = real[i]["grid"]
                    row |= {"selected": [grid[x] for x in a.get("selected") or [] if 0 <= x < len(grid)], "no_match": a.get("no_match"),
                            "hint": grid[real[i]["hint"]] if "hint" in real[i] else None, "hint_answer": a.get("hint_answer")}
                else:
                    names = {f"b{k:02d}": c for k, c in enumerate(real[i]["boxes"], 1)}
                    marks = {names[k]: v for k, v in (a.get("boxes") or {}).items() if k in names}
                    row |= {"boxes": marks, "drawn": a.get("drawn") or [], "no_photos": a.get("no_photos")}
                    if a.get("no_photos") and not a.get("drawn") and marks:
                        # 10/20: older pages let 這頁沒有照片 and box marks coexist; on the six such pages the marks sat on
                        # navigation bars, sidebars and table backgrounds, so 沒有照片 wins and the marks are kept aside
                        row |= {"boxes": {}, "boxes_dropped": marks}
                labels[real[i]["split"]].append(row)
    if args.dry_run:
        for r in report:
            print(f"batch {r['batch']}: 沒答 {r['missing']}｜檢查題錯 {r['checks_wrong']}/{r['checks']}（不算 {r['checks_void']}）｜秒數中位數 {r['median_seconds']}"
                  + (f"｜連續同答案最長 {r['longest_same_answer']}" if task == "B" else "") + ("" if r["imported"] else "｜不匯入"))
        print(("提醒 Eden：\n  " + "\n  ".join(alerts)) if alerts else "沒有超過門檻的情形。")
        print("（--dry-run：沒有寫任何檔）")
        return
    for split, rows in labels.items():
        path = ROOT / "annotations" / split / f"{task}.jsonl"
        old = [r for r in read_jsonl(path) if r["annotator"] != ann]
        write_jsonl(path, sorted(old + rows, key=lambda r: (r["annotator"], r["item"])))
    write_result(f"annotation_{task}_batch_check_{ann}", {"task": task, "annotator": ann, "notes": notes, "limits": t["limits"],
                                                         "batches": report, "alerts": alerts},
                 [work(task) / "returns" / v[1] for v in newest.values()])
    for r in report:
        print(f"batch {r['batch']}: 沒答 {r['missing']}｜檢查題錯 {r['checks_wrong']}/{r['checks']}（不算 {r['checks_void']}）｜秒數中位數 "
              f"{'—' if r['median_seconds'] is None else round(r['median_seconds'], 1)}" + (f"｜連續同答案最長 {r['longest_same_answer']}" if task == "B" else "")
              + ("" if r["imported"] else "｜不匯入"))
    print("提醒 Eden：\n  " + "\n  ".join(alerts) if alerts else "沒有超過門檻的情形。")


def cmd_c_pool(args):
    """10/21 (Eden: C 的圖太小、太難確定；先幫忙抓，我們 verify): instead of every photo of the previous year's report,
    each C item shows a pool of candidates, the union of SSCD top-k, DINOv2 top-k (both with the mirrored query, k as
    reuse.topk, the reuse detector's own setting) and perceptual-hash top-k_phash, in page order. Recall is then
    pooled recall (TREC-style): a reused photo that none of the three ranks that high is not found. The full list stays
    in grid_full; grid becomes the pool; for checks and practice the transformed copy is inserted at a seeded position
    and answer is its index in the pool (answer_full keeps the old one). The C plan gets a new version of every batch
    (new page ids, so answers saved in a browser for the old grids are not reused). No scores are shown (rule 7)."""
    import numpy as np
    from PIL import Image, ImageOps
    import features
    from reuse import ham, phash
    cfg = cfg_all()
    k, k_ph = int(cfg["reuse"]["topk"]), args.phash_k
    crop_dir = ROOT / "work" / "extract" / "crops"
    ipath, qpath = ROOT / "annotations" / "test" / "C_items.jsonl", qc("C", "qc.jsonl")
    items, qrows = read_jsonl(ipath), read_jsonl(qpath)
    rows = items + qrows
    for r in rows:
        r.setdefault("grid_full", list(r["grid"]))
        if "answer" in r:
            r.setdefault("answer_full", list(r["answer"]))
    crops = sorted({r["query"] for r in rows} | {g for r in rows for g in r["grid_full"] if not g.startswith("copies/")})
    vec = {m: dict(zip(crops, features.embed_crops(crops, m, crop_dir))) for m in ("dinov2", "sscd")}
    vfl = {m: dict(zip(crops, features.embed_crops(crops, m, crop_dir, flip=True))) for m in ("dinov2", "sscd")}
    ph = {}
    for c in crops:
        with Image.open(crop_dir / c) as im:
            im = im.convert("RGB")
            ph[c] = (phash(im), phash(ImageOps.mirror(im)))
    rng = random.Random(f"{cfg['seed']}:C:pool")
    sizes = []
    for r in rows:
        g = [x for x in r["grid_full"] if not x.startswith("copies/")]
        q = r["query"]
        pick = set()
        for m in ("dinov2", "sscd"):
            G = np.stack([vec[m][x] for x in g])
            best = np.maximum(G @ vec[m][q], G @ vfl[m][q])
            pick |= set(np.argsort(-best)[:k].tolist())
        d = [min(ham(ph[x][0], ph[q][0]), ham(ph[x][0], ph[q][1])) for x in g]
        pick |= set(np.argsort(d, kind="stable")[:k_ph].tolist())
        pool = [g[j] for j in sorted(pick)]
        # 10/21 Eden (「C 不是人標，妳要給提示」): the page shows the tool's top pick next to the query for a yes / no
        # answer, B-style; hint = the SSCD nearest candidate (retrieval rank only, not the local-matching verifier, so
        # the labels stay independent of the detector's thresholds). An exception to rule 7 recorded in PLAN §13.
        G = np.stack([vec["sscd"][x] for x in pool])
        hint = int(np.argmax(np.maximum(G @ vec["sscd"][q], G @ vfl["sscd"][q])))
        if r.get("copy"):
            pos = rng.randint(0, len(pool))
            pool = pool[:pos] + [f"copies/{r['copy']}"] + pool[pos:]
            r["answer"] = [pos]
            if hint >= pos:
                hint += 1
            if rng.random() < 0.5:   # half of the checks hint at the copy (as SSCD would), half at the nearest other photo
                hint = pos
        r["grid"] = pool
        r["hint"] = hint
        r["pool"] = {"sscd_top": k, "dinov2_top": k, "phash_top": k_ph, "flip": True, "of": len(g), "hint": "sscd_top1"}
        sizes.append(len(pool))
    write_jsonl(ipath, items)
    write_jsonl(qpath, qrows)
    plan_path, key_path = qc("C", "task_plan.json"), qc("C", "checks_key.jsonl")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    latest = {}
    for e in plan:
        if (e["annotator"], e["batch"]) not in latest or e["version"] > latest[(e["annotator"], e["batch"])]["version"]:
            latest[(e["annotator"], e["batch"])] = e
    for e in latest.values():
        n = dict(e, version=e["version"] + 1)
        n["page_id"] = page_id(n)
        plan.append(n)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    ans = {r["item"]: r["answer"] for r in qrows if "answer" in r}
    key = [dict(x, answer=ans.get(x["item"], x["answer"])) for x in read_jsonl(key_path)]
    write_jsonl(key_path, key)
    summary = {"items": len(items), "qc_rows": len(qrows), "k": k, "k_phash": k_ph,
               "pool_size": {"median": statistics.median(sizes), "max": max(sizes), "min": min(sizes)},
               "full_grid_size": {"median": statistics.median(len(r["grid_full"]) for r in rows)},
               "new_plan_versions": len(latest)}
    write_result("annotation_C_pool", summary, [ipath, qpath])
    print(summary)


def cmd_d_review(args):
    """10/20 (Eden: 「你根本沒說錯哪」): reports/D_review_{ann}_*.html shows, on the page itself, every check page answered
    wrong (green = the photo whose box was removed, which had to be drawn) and every page with 這頁沒有照片 ticked and box
    marks at the same time. Only checks and the annotator's own contradictions; no label counts (rule 3: D is test)."""
    ann = args.annotator
    plan = json.loads(qc("D", "task_plan.json").read_text(encoding="utf-8"))
    key = {k["item"] for k in read_jsonl(qc("D", "checks_key.jsonl")) if k["annotator"] == ann}
    void = {r["item"]: r["reason"] for r in read_jsonl(qc("D", "checks_void.jsonl")) if r["annotator"] == ann}
    rows = {r["item"]: r for r in read_jsonl(qc("D", "qc.jsonl"))} | {r["item"]: r for r in real_items("D")}
    cur, newest, notes = read_returns("D", ann, plan)
    cards = []
    for b in sorted(newest):
        d, fname = newest[b]
        for a in d["answers"]:
            r = rows[a["item"]]
            uri, boxes, (W, H) = d_view(r)
            is_check = a["item"] in key
            target = pct_box(r["removed_bbox"], W, H) if is_check else None
            wrong = is_check and not any(hit(x, target) for x in a.get("drawn") or [])
            both = a.get("no_photos") and (a.get("boxes") or a.get("drawn"))
            if not (wrong or both):
                continue
            did = []
            if a.get("no_photos"):
                did.append("勾了「這頁沒有照片」")
            did += [f"把框 {k[1:]} 標成「{ZH_D[v]}」" for k, v in sorted((a.get("boxes") or {}).items())]
            if a.get("drawn"):
                did.append(f"畫了 {len(a['drawn'])} 個框（橘色）")
            if not did:
                did.append("什麼都沒動，直接下一頁")
            if a["item"] in void:
                why = "<b>這題不算錯，是檢查題出得不好：</b>" + void[a["item"]] + "。"
            elif wrong:
                why = "這頁有一張照片<b>沒有框</b>（綠色虛線），要自己畫一個框把它框起來。" + \
                      ("但這頁勾了「這頁沒有照片」。" if a.get("no_photos") else "這張沒有被畫出來。")
            else:
                why = "勾了「這頁沒有照片」，又把框標成框不完整／框到多張。兩個不能同時成立：真的沒有照片，框就不用管（框到圖表、logo 沒關係）；有照片，就不要勾。"
            shapes = "".join(f'<div class="bx{" red" if bx["b"] in (a.get("boxes") or {}) else ""}" style="left:{bx["x"]}%;top:{bx["y"]}%;width:{bx["w"]}%;height:{bx["h"]}%">'
                             f'<span>{bx["b"][1:]}{"：" + ZH_D[a["boxes"][bx["b"]]] if bx["b"] in (a.get("boxes") or {}) else ""}</span></div>' for bx in boxes)
            shapes += "".join(f'<div class="dr" style="left:{x["x"]}%;top:{x["y"]}%;width:{x["w"]}%;height:{x["h"]}%"><span>畫的框</span></div>' for x in a.get("drawn") or [])
            if target:
                shapes += f'<div class="gt" style="left:{target["x"]}%;top:{target["y"]}%;width:{target["w"]}%;height:{target["h"]}%"><span>這張沒有框</span></div>'
            cards.append(f'<section><h2>第 {b} 批 第 {a["position"]} 頁 <small>{"檢查題出得不好，不算錯" if a["item"] in void else "檢查題，答錯" if wrong else "答案自相矛盾（匯入時以「沒有照片」為準）"}｜這頁停了 {a["seconds"]} 秒</small></h2>'
                         f'<p><b>做了什麼：</b>{"；".join(did)}。<br><b>問題：</b>{why}</p>'
                         f'<div class="pg"><img src="{uri}" alt="">{shapes}</div></section>')
    css = """body{max-width:1100px} section{border-top:2px solid var(--line);margin:28px 0} h2 small{font-weight:normal;color:var(--mut)}
.pg{position:relative;display:block;width:100%;line-height:0} .pg img{width:100%;height:auto;display:block}
.bx,.dr,.gt{position:absolute;box-sizing:border-box} .bx{border:1px solid #888} .bx.red{border:3px solid #b3261e;background:#b3261e22}
.dr{border:3px solid #e8590c} .gt{border:4px dashed #137333;background:#13733322}
.pg span{position:absolute;left:0;top:0;font:13px/1.3 system-ui;background:#fffd;color:#000;padding:1px 4px}"""
    stamp = run_stamp()
    out = ROOT / "reports" / f"D_review_{ann}_{stamp}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>標註 D 錯在哪：{ann}</title><style>{CSS}{css}</style></head><body><h1>標註 D：{ann} 錯在哪（{len(cards)} 頁）</h1>
<p>灰框＝程式找到的圖；<b style="color:#137333">綠色虛線</b>＝沒有框的照片（應該畫出來）；<b style="color:#b3261e">紅框</b>＝標註者改了狀態的框；<b style="color:#e8590c">橘框</b>＝標註者畫的框。
檢查題是頁數答案已知的頁（拿掉一張照片的框）；<b>給標註者看時不要說哪幾頁是檢查題</b>。</p>{''.join(cards)}</body></html>""", encoding="utf-8")
    print(f"{len(cards)} 頁 → {out}")


ZH_D = {"partial": "框不完整", "merged": "框到多張", "split": "被切開"}


def page_size(r):
    import pymupdf
    with pymupdf.open(ROOT / "data" / "raw" / f"{r['report']}.pdf") as doc:
        pg = doc[r["page"] - 1]
        return pg.rect.width, pg.rect.height


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("b-sample")
    p.add_argument("--split", default="dev", choices=["dev", "test"])
    p.add_argument("--force", action="store_true")
    sub.add_parser("b-qc")
    p = sub.add_parser("c-sample")
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--spares", type=int, default=0)
    p.add_argument("--force", action="store_true")
    sub.add_parser("c-qc")
    p = sub.add_parser("d-sample")
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--force", action="store_true")
    sub.add_parser("d-qc")
    p = sub.add_parser("pages")
    p.add_argument("--task", required=True, choices=list(TASKS))
    p = sub.add_parser("check")
    p.add_argument("--task", required=True, choices=list(TASKS))
    p.add_argument("--annotator", required=True, choices=["a1", "a2"])
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("c-pool")
    p.add_argument("--phash-k", type=int, default=5)
    p = sub.add_parser("d-review")
    p.add_argument("--annotator", default="a2", choices=["a1", "a2"])
    args = ap.parse_args()
    {"b-sample": cmd_b_sample, "b-qc": cmd_b_qc, "c-sample": cmd_c_sample, "c-qc": cmd_c_qc, "d-sample": cmd_d_sample,
     "d-qc": cmd_d_qc, "pages": cmd_pages, "check": cmd_check, "c-pool": cmd_c_pool, "d-review": cmd_d_review}[args.cmd](args)


if __name__ == "__main__":
    main()
