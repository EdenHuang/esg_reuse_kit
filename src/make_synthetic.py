"""Synthetic copy pairs for the reuse detector (PLAN §5 step 6, Table 7): per split n_pos positives and n_neg
negatives (configs/default.yaml synthetic).

Source photos: the crops the photo filter keeps (work/photo_filter.csv keep == 1; the threshold is still the
initial value, so a few non-photos may be among them), of the split's firms.

  positive  (a, b): b is a copy of a after 1-6 edits, each drawn with its probability (synthetic.p_transform),
            at least one, applied in this order:
              crop     keep U(crop_keep_area) of the area, aspect x U(crop_aspect), random place
              flip     left-right mirror
              color    brightness and contrast x (1 +- brightness_contrast), saturation x (1 +- saturation),
                       hue +- hue_deg degrees
              text     a banner of U(overlay_text_area) of the area, opacity U(text_opacity), with Chinese text
              collage  pasted onto another firm's photo, covering U(collage_area) of it
              jpeg     re-encoded at quality U(jpeg_quality)
  negative  (a, b): two photos of different firms whose SSCD cosine is < neg_max_sscd (a stock photo used by
            two firms would otherwise be counted as a negative).

Writes data/synthetic/{split}/pos/{pair}.jpg and data/synthetic/{split}/pairs.jsonl (not in git: they hold
images), results/synthetic_{split}_{stamp}.json (counts, transforms, pair-file hash, meta) and
reports/synthetic_{split}_{stamp}.html (examples). For the test split no detector similarity of the positive
pairs is computed or shown (CLAUDE.md rule 3); SSCD is used there only to drop look-alike negatives.
Seed: 2026 and the split name.

  python src/make_synthetic.py --split dev
  python src/make_synthetic.py --split test
"""
import argparse
import base64
import collections
import csv
import hashlib
import html
import io
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import features  # noqa: E402
from meta import run_meta, run_stamp, sha256_file  # noqa: E402

TRANSFORMS = ["crop", "flip", "color", "text", "collage", "jpeg"]
TEXTS = ["永續發展", "2025 永續報告書", "淨零轉型", "員工關懷", "ESG Report", "社會參與", "綠色製程",
         "公司治理", "幸福職場", "Sustainability", "循環經濟", "氣候行動"]
FONT = "C:/Windows/Fonts/msjh.ttc"


def load_yaml(path):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def photos(split, filter_csv):
    out = []
    with open(filter_csv, encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if d["split"] == split and d["keep"] == "1":
                out.append({"crop": d["crop"], "firm": d["firm_id"], "report": d["report"]})
    return sorted(out, key=lambda d: d["crop"])


# ---------------------------------------------------------------- edits
def t_crop(im, rng, s):
    lo, hi = s["crop_keep_area"]
    a = rng.uniform(lo, hi)
    r = rng.uniform(*s["crop_aspect"])
    w = min(im.width, max(8, round(im.width * (a * r) ** 0.5)))
    h = min(im.height, max(8, round(im.height * (a / r) ** 0.5)))
    x, y = rng.randint(0, im.width - w), rng.randint(0, im.height - h)
    return im.crop((x, y, x + w, y + h)), {"keep_area": round(w * h / (im.width * im.height), 3), "box": [x, y, x + w, y + h]}


def t_flip(im, rng, s):
    from PIL import ImageOps
    return ImageOps.mirror(im), {}


def t_color(im, rng, s):
    from PIL import ImageEnhance
    b = 1 + rng.uniform(-s["brightness_contrast"], s["brightness_contrast"])
    c = 1 + rng.uniform(-s["brightness_contrast"], s["brightness_contrast"])
    sat = 1 + rng.uniform(-s["saturation"], s["saturation"])
    hue = rng.uniform(-s["hue_deg"], s["hue_deg"])
    im = ImageEnhance.Brightness(im).enhance(b)
    im = ImageEnhance.Contrast(im).enhance(c)
    im = ImageEnhance.Color(im).enhance(sat)
    hsv = im.convert("HSV")
    h, sv, v = hsv.split()
    shift = round(hue / 360 * 255)
    h = h.point(lambda x: (x + shift) % 256)
    from PIL import Image
    im = Image.merge("HSV", (h, sv, v)).convert("RGB")
    return im, {"brightness": round(b, 3), "contrast": round(c, 3), "saturation": round(sat, 3), "hue_deg": round(hue, 1)}


def t_text(im, rng, s):
    from PIL import Image, ImageDraw, ImageFont
    lo, hi = s["overlay_text_area"]
    a = rng.uniform(lo, hi)
    bh = max(10, round(im.height * min(0.9, a)))  # a full-width banner of the drawn area share
    bw = min(im.width, round(a * im.width * im.height / bh))
    y = rng.choice([0, im.height - bh, (im.height - bh) // 2])
    x = rng.randint(0, im.width - bw)
    colour = tuple(rng.randint(0, 255) for _ in range(3))
    op = rng.uniform(*s["text_opacity"])
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle([x, y, x + bw, y + bh], fill=colour + (round(255 * op),))
    text = rng.choice(TEXTS)
    size = max(10, round(bh * 0.5))
    font = ImageFont.truetype(FONT, size)
    tw = d.textlength(text, font=font)
    if tw > bw * 0.9:
        size = max(8, round(size * bw * 0.9 / tw))
        font = ImageFont.truetype(FONT, size)
    ink = (255, 255, 255, 255) if sum(colour) < 380 else (20, 20, 20, 255)
    d.text((x + bw / 2, y + bh / 2), text, fill=ink, font=font, anchor="mm")
    out = Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")
    return out, {"area": round(bw * bh / (im.width * im.height), 3), "opacity": round(op, 2), "text": text}


def t_collage(im, rng, s, background):
    from PIL import Image
    lo, hi = s["collage_area"]
    a = rng.uniform(lo, hi)
    canvas = background.convert("RGB").resize(im.size)
    scale = a ** 0.5
    w, h = max(8, round(im.width * scale)), max(8, round(im.height * scale))
    small = im.resize((w, h), Image.LANCZOS)
    x, y = rng.randint(0, im.width - w), rng.randint(0, im.height - h)
    canvas.paste(small, (x, y))
    return canvas, {"area": round(w * h / (im.width * im.height), 3), "box": [x, y, x + w, y + h]}


def make_positive(src, rng, s, background_pick):
    picks = [t for t in TRANSFORMS if rng.random() < s["p_transform"][t]]
    if not picks:
        picks = [rng.choice(TRANSFORMS)]
    im, steps, quality = src.convert("RGB"), [], 95
    for t in TRANSFORMS:
        if t not in picks:
            continue
        if t == "crop":
            im, prm = t_crop(im, rng, s)
        elif t == "flip":
            im, prm = t_flip(im, rng, s)
        elif t == "color":
            im, prm = t_color(im, rng, s)
        elif t == "text":
            im, prm = t_text(im, rng, s)
        elif t == "collage":
            bg_crop, bg = background_pick()
            im, prm = t_collage(im, rng, s, bg)
            prm["background"] = bg_crop
        else:
            quality = rng.randint(*s["jpeg_quality"])
            prm = {"quality": quality}
        steps.append({"name": t, **prm})
    return im, steps, quality


# ---------------------------------------------------------------- HTML
CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#666;--line:#ddd;--card:#f5f5f5}
@media (prefers-color-scheme: dark){:root{--bg:#161616;--fg:#eee;--mut:#aaa;--line:#333;--card:#222}}
body{background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;margin:0 auto;max-width:1200px;padding:16px}
.mut{color:var(--mut)} table{border-collapse:collapse} th,td{border-bottom:1px solid var(--line);padding:4px 10px;text-align:right}
th:first-child,td:first-child{text-align:left} .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px}
.pair{background:var(--card);border-radius:10px;padding:8px} .pair .two{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.pair img{width:100%;height:150px;object-fit:contain;background:#0001;border-radius:6px} .pair p{margin:4px 0 0;font-size:13px}
"""


def thumb(path_or_im, size=320):
    from PIL import Image
    im = Image.open(path_or_im) if not hasattr(path_or_im, "size") else path_or_im
    im = im.convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=75)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


STEP_ZH = {"crop": "裁切", "flip": "翻轉", "color": "調色", "text": "壓字", "collage": "拼貼", "jpeg": "JPEG 重壓"}


def write_html(path, res, pos_rows, neg_rows, crop_dir, out_dir, split):
    e = html.escape
    rng = random.Random(f"2026:synthetic:html:{split}")
    show_pos = rng.sample(pos_rows, min(24, len(pos_rows)))
    show_neg = rng.sample(neg_rows, min(12, len(neg_rows)))

    def pos_card(r):
        steps = "、".join(STEP_ZH[s["name"]] + (f"（{s['text']}）" if s["name"] == "text" else "") for s in r["transforms"])
        sim = f"｜SSCD {r['sscd']:.2f}、DINOv2 {r['dinov2']:.2f}" if "sscd" in r else ""
        return (f'<div class="pair"><div class="two"><img src="{thumb(Path(crop_dir) / r["a"])}" alt="">'
                f'<img src="{thumb(out_dir / r["b"])}" alt=""></div><p>{e(steps)}{sim}</p><p class="mut">{e(r["a"])}</p></div>')

    def neg_card(r):
        return (f'<div class="pair"><div class="two"><img src="{thumb(Path(crop_dir) / r["a"])}" alt="">'
                f'<img src="{thumb(Path(crop_dir) / r["b"])}" alt=""></div><p>SSCD {r["sscd"]:.2f}</p>'
                f'<p class="mut">{e(r["a"])}｜{e(r["b"])}</p></div>')

    tr = "".join(f"<tr><td>{STEP_ZH[k]}</td><td>{v}</td></tr>" for k, v in res["transform_counts"].items())
    nt = "".join(f"<tr><td>{k} 種</td><td>{v}</td></tr>" for k, v in sorted(res["n_transforms_per_pair"].items()))
    sim_note = ""
    if res.get("positive_similarity"):
        ps = res["positive_similarity"]
        sim_note = (f"<p>開發集正例（原圖對變換後）的相似度中位數：SSCD {ps['sscd_median']:.3f}、DINOv2 {ps['dinov2_median']:.3f}；"
                    f"負例 SSCD 中位數 {res['negatives']['sscd_median']:.3f}。只是檢查合成資料合不合理，不是重用偵測的結果（那是 reuse.py）。</p>")
    Path(path).write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>合成資料 {split}</title><style>{CSS}</style></head><body>
<h1>重用偵測的合成資料：{'開發集' if split == 'dev' else '測試集'}</h1>
<p>原圖是照片過濾留下的照片（{res['source_photos']} 張，{res['source_firms']} 家；照片門檻還是初值 {res['photo_threshold']}）。
正例 {res['positives']} 組：原圖和做過變換的複本（左原圖、右複本）。負例 {res['negatives']['pairs']} 組：不同公司的兩張照片，
SSCD 相似度 ≥ {res['neg_max_sscd']} 的配對先排除（排除 {res['negatives']['excluded_look_alike']} 次重抽）。</p>
{sim_note}
<h2>變換用了幾次</h2><table><tr><th>變換</th><th>正例數</th></tr>{tr}</table>
<table><tr><th>一組用了</th><th>正例數</th></tr>{nt}</table>
<h2>正例（隨機 {len(show_pos)} 組）</h2><div class="grid">{''.join(pos_card(r) for r in show_pos)}</div>
<h2>負例（隨機 {len(show_neg)} 組）</h2><div class="grid">{''.join(neg_card(r) for r in show_neg)}</div>
</body></html>""", encoding="utf-8")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True, choices=["dev", "test"])
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--force", action="store_true", help="replace an existing data/synthetic/{split}")
    args = ap.parse_args()
    cfg = load_yaml(ROOT / args.config)
    s, seed = cfg["synthetic"], cfg["seed"]
    crop_dir = ROOT / cfg["paths"]["extract"] / "crops"
    filter_csv = ROOT / "work" / "photo_filter.csv"
    out_dir = ROOT / cfg["paths"]["synthetic"] / args.split
    if (out_dir / "pairs.jsonl").exists() and not args.force:
        sys.exit(f"{out_dir} 已經有合成資料；要重產請加 --force")
    pool = photos(args.split, filter_csv)
    firms = sorted({p["firm"] for p in pool})
    if len(pool) < s["n_pos"] or len(firms) < 2:
        sys.exit(f"照片不夠：{len(pool)} 張、{len(firms)} 家")
    print(f"{args.split}：照片 {len(pool)} 張、{len(firms)} 家")
    rng = random.Random(f"{seed}:synthetic:{args.split}")
    if out_dir.exists():
        for f in (out_dir / "pos").glob("*.jpg"):
            f.unlink()
    (out_dir / "pos").mkdir(parents=True, exist_ok=True)

    # SSCD of the pool: drop look-alike negatives (and, on dev only, a sanity check of the positives)
    vec = features.embed_crops([p["crop"] for p in pool], "sscd", crop_dir)
    idx = {p["crop"]: i for i, p in enumerate(pool)}
    by_firm = collections.defaultdict(list)
    for p in pool:
        by_firm[p["firm"]].append(p)

    from PIL import Image
    srcs = rng.sample(pool, s["n_pos"])
    pos_rows = []
    for k, a in enumerate(srcs):
        others = [f for f in firms if f != a["firm"]]

        def background_pick(a=a, others=others):
            b = rng.choice(by_firm[rng.choice(others)])
            with Image.open(crop_dir / b["crop"]) as bim:
                return b["crop"], bim.convert("RGB")

        with Image.open(crop_dir / a["crop"]) as im:
            out, steps, quality = make_positive(im, rng, s, background_pick)
        name = f"pos/{args.split}_pos_{k:04d}.jpg"
        out.save(out_dir / name, "JPEG", quality=quality)
        pos_rows.append({"pair": f"{args.split}_pos_{k:04d}", "label": 1, "a": a["crop"], "a_firm": a["firm"],
                         "b": name, "b_firm": a["firm"], "transforms": steps})
    neg_rows, seen, excluded = [], set(), 0
    while len(neg_rows) < s["n_neg"]:
        a = rng.choice(pool)
        b = rng.choice(by_firm[rng.choice([f for f in firms if f != a["firm"]])])
        key = tuple(sorted((a["crop"], b["crop"])))
        if key in seen:
            continue
        sim = float(vec[idx[a["crop"]]] @ vec[idx[b["crop"]]])
        if sim >= s["neg_max_sscd"]:
            excluded += 1
            continue
        seen.add(key)
        neg_rows.append({"pair": f"{args.split}_neg_{len(neg_rows):04d}", "label": 0, "a": a["crop"], "a_firm": a["firm"],
                         "b": b["crop"], "b_firm": b["firm"], "sscd": round(sim, 4)})
    pos_sim = None
    if args.split == "dev":  # sanity check of the synthetic data only; never on test (rule 3)
        ims = []
        for r in pos_rows:
            with Image.open(out_dir / r["b"]) as im:
                ims.append(im.convert("RGB"))
        for model in ("sscd", "dinov2"):
            va = features.embed_crops([r["a"] for r in pos_rows], model, crop_dir)
            vb = features.embed_images(ims, model)
            for r, x, y in zip(pos_rows, va, vb):
                r[model] = round(float(x @ y), 4)
        import statistics
        pos_sim = {"sscd_median": statistics.median(r["sscd"] for r in pos_rows),
                   "dinov2_median": statistics.median(r["dinov2"] for r in pos_rows)}
    pairs = out_dir / "pairs.jsonl"
    with open(pairs, "w", encoding="utf-8", newline="\n") as f:
        for r in pos_rows + neg_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    import statistics
    res = {"split": args.split, "source": s["source"], "photo_threshold": cfg["photo_filter"]["photo_threshold"],
           "source_photos": len(pool), "source_firms": len(firms), "positives": len(pos_rows),
           "transform_counts": {t: sum(any(x["name"] == t for x in r["transforms"]) for r in pos_rows) for t in TRANSFORMS},
           "n_transforms_per_pair": dict(sorted(collections.Counter(len(r["transforms"]) for r in pos_rows).items())),
           "negatives": {"pairs": len(neg_rows), "excluded_look_alike": excluded,
                         "sscd_median": statistics.median(r["sscd"] for r in neg_rows),
                         "sscd_max": max(r["sscd"] for r in neg_rows)},
           "neg_max_sscd": s["neg_max_sscd"], "positive_similarity": pos_sim, "pairs_file": str(pairs.relative_to(ROOT)),
           "pairs_sha256": sha256_file(pairs), "models": {"sscd": features.model_info("sscd"),
                                                          **({"dinov2": features.model_info("dinov2")} if pos_sim else {})}}
    stamp = run_stamp()
    rp = ROOT / "results" / f"synthetic_{args.split}_{stamp}.json"
    k = 2
    while rp.exists():   # never overwrite a result (CLAUDE.md rule 2)
        stamp = f"{run_stamp()}_{k}"
        rp = ROOT / "results" / f"synthetic_{args.split}_{stamp}.json"
        k += 1
    rp.write_text(json.dumps({"meta": run_meta(args.config, [str(filter_csv), str(pairs)]), **res}, ensure_ascii=False, indent=1),
                  encoding="utf-8")
    hp = ROOT / "reports" / f"synthetic_{args.split}_{stamp}.html"
    write_html(hp, res, pos_rows, neg_rows, crop_dir, out_dir, args.split)
    print(json.dumps({k: res[k] for k in ("positives", "transform_counts", "n_transforms_per_pair", "negatives",
                                          "positive_similarity")}, ensure_ascii=False))
    print(f"wrote {pairs}, {rp} and {hp}")


if __name__ == "__main__":
    main()
