"""Photo filter (PLAN v2.0 §5 step 3, Table 2): is each extracted crop a real photograph?

For every crop in work/extract/*_pairs.jsonl the script
  1. marks crops that are never analysed, whatever the model says (exclusion reasons):
       image_only_report - the whole report is page images (image_only in results/download_check_*.json)
       cover_page        - first or last page of the PDF
       vector_shading    - not an embedded image: PyMuPDF reports vector gradient fills (PDF
                           shadings) as images with xref 0, raw RGB pixels at exactly one pixel
                           per point. 4558_2024 has 784 shading patterns and 11,239 such
                           "images" (LOG.md 2026-10-18 evening). Checked in the PDF itself.
                           Since extraction v2 (10/18 evening) extract_pairs.py skips them, so
                           on work/extract this finds none; it still flags them in work/extract_v1.
       layout_repeat     - on >= 3 pages (extract_pairs.py)
       duplicate         - the same picture again in the report; only the representative is kept
  2. embeds every crop with OpenCLIP (configs photo_filter: model, pretrained) and caches the
     vectors in work/features/openclip_{model}_{pretrained}/, images per report and prompts
     per prompt list, so a new threshold (or new prompts) never re-runs the image model,
  3. scores each crop zero-shot: softmax over all prompts (photo and non-photo), photo score =
     sum over the photo prompts; a photo when photo score >= photo_filter.photo_threshold, or (when
     photo_filter.vlm_threshold is set, 10/19) when the vision-language model's p(camera photo) >= vlm_threshold
     (photo_vlm.py's cached answers, work/evidence/vlm_is_photo.jsonl); otherwise reason "nonphoto".
Writes work/photo_filter.csv (one row per crop; keep=1 when there is no exclusion reason),
results/photo_filter_{stamp}.json (meta and counts) and reports/photo_filter_{stamp}.html.
Samples in the HTML come from development-set reports only (CLAUDE.md rule 3).

  python src/photo_filter.py --firms 1101 1707 4934 2882 2610     # try on 5 firms first
  python src/photo_filter.py --all
  python src/photo_filter.py --all --stage vector                # PDF check only, no model
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
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "work" / "models" / "hf"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # weights are in work/models (check_models.py); never fetch a newer revision
sys.path.insert(0, str(ROOT / "src"))
import extract_pairs as ep  # noqa: E402
from meta import package_versions, run_meta, run_stamp  # noqa: E402

REASONS = ["image_only_report", "cover_page", "vector_shading", "layout_repeat", "duplicate", "nonphoto"]
REASON_LABEL = {"image_only_report": "沒有文字層的報告","cover_page": "封面頁（第一頁、最後一頁）",
                "vector_shading": "向量漸層（不是內嵌圖片）", "layout_repeat": "版面元素（3 頁以上）",
                "duplicate": "報告內重複（只留一張）", "nonphoto": "模型判斷不是照片"}
VECTOR_VERSION = 2  # bump when the vector check changes, so cached results are recomputed


# ---------------------------------------------------------------- inputs
def load_yaml(path):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_pairs(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_path(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def latest(pattern):
    found = sorted(Path("results").glob(pattern))
    return found[-1] if found else None


def select_reports(catalog, args):
    rows = []
    for row in catalog:
        name = f"{row['firm_id'].strip()}_{row['year'].strip()}"
        if args.all or (args.firms and row["firm_id"].strip() in args.firms) or (args.reports and name in args.reports):
            rows.append(dict(row, name=name))
    return rows


# ---------------------------------------------------------------- step 1: vector shading
is_shading_image = ep.is_shading_image  # one rule, shared with the extraction


def placements_with_shading(page, keep_shading):
    """extract_pairs.picture_placements(page), plus a shading flag for every merged layer.
    Same filters and the same merge as extract_pairs.py, so the i-th entry is record idx i.
    keep_shading=False reproduces extract_pairs.py since it skips shadings (extraction v2,
    10/18 evening): every flag is then False and the check only confirms the records match."""
    import pymupdf
    seen, out = set(), []
    for info in ep.image_info(page, keep_shading=keep_shading):
        rect = pymupdf.Rect(info["bbox"]) & page.rect
        if rect.is_empty:
            continue
        w_px, h_px = info.get("width", 0), info.get("height", 0)
        key = (info.get("xref", 0), round(rect.x0), round(rect.y0), round(rect.x1), round(rect.y1))
        if key in seen:
            continue
        seen.add(key)
        if min(w_px, h_px) < ep.MIN_PX or min(rect.width, rect.height) < ep.MIN_PT:
            continue
        out.append({"rect": rect, "xref": info.get("xref", 0), "px_w": w_px, "px_h": h_px,
                    "shade": is_shading_image(info)})
    merged = []
    for p in sorted(out, key=lambda p: -(p["px_w"] * p["px_h"])):
        target = next((m for m in merged if ep.rect_iou(p["rect"], m["rect"]) >= ep.LAYER_IOU), None)
        if target is None:
            merged.append(dict(p, layers=[p["xref"]], shade=[p["shade"]]))
        else:
            target["rect"] = target["rect"] | p["rect"]
            target["layers"].append(p["xref"])
            target["shade"].append(p["shade"])
    merged.sort(key=lambda m: (m["rect"].y0, m["rect"].x0))
    return merged


def vector_check(job):
    """Crops of one report that are only vector shading. Opens the PDF only on pages that have
    a record whose layers all lack an xref (every shading entry has xref 0). Cached."""
    name, pdf, pairs, cache_dir = job
    cache = Path(cache_dir) / f"{name}.json"
    pairs_sha = sha256_path(pairs)
    import pymupdf
    if cache.exists():
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("pairs_sha256") == pairs_sha and c.get("version") == VECTOR_VERSION \
                and c.get("pymupdf") == pymupdf.VersionBind:
            return c
    t0 = time.time()
    recs = read_pairs(pairs)
    by_page = collections.defaultdict(list)
    for r in recs:
        by_page[r["page"]].append(r)
    pages = sorted({r["page"] for r in recs if all(x == 0 for x in r["layers"])})
    flagged, mismatch, mixed = [], [], 0
    if pages:
        doc = pymupdf.open(pdf)
        for p in pages:
            want = sorted(by_page[p], key=lambda r: r["idx"])
            for keep in (False, True):  # extraction v2 (shadings skipped), else v1 (work/extract_v1)
                got = placements_with_shading(doc[p - 1], keep)
                same = len(got) == len(want) and all(
                    [round(v, 1) for v in m["rect"]] == r["bbox"] and m["layers"] == r["layers"]
                    for m, r in zip(got, want))
                if same:
                    break
            if not same:  # do not guess: leave the page unflagged and report it
                mismatch.append(p)
                continue
            for m, r in zip(got, want):
                if all(m["shade"]):
                    flagged.append(r["crop"])
                elif any(m["shade"]):
                    mixed += 1  # a real image merged with a gradient on top: kept
        doc.close()
    c = {"report": name, "version": VECTOR_VERSION, "pymupdf": pymupdf.VersionBind, "pairs_sha256": pairs_sha,
         "records": len(recs), "pages_checked": len(pages), "vector_crops": flagged, "mixed_kept": mixed,
         "mismatch_pages": mismatch, "seconds": round(time.time() - t0, 1)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    return c


def run_vector_checks(rows, args, cache_dir):
    jobs = [(r["name"], r["file_path"].strip(), str(Path(args.extract) / f"{r['name']}_pairs.jsonl"), str(cache_dir))
            for r in rows]
    out = {}
    if args.workers > 1 and len(jobs) > 1:
        with Pool(args.workers) as pool:
            for c in pool.imap_unordered(vector_check, jobs):
                out[c["report"]] = c
                print(f"[vector] {c['report']}: {len(c['vector_crops'])}/{c['records']}"
                      f"{'，對不上 ' + str(len(c['mismatch_pages'])) + ' 頁' if c['mismatch_pages'] else ''}", flush=True)
    else:
        for job in jobs:
            c = vector_check(job)
            out[c["report"]] = c
            print(f"[vector] {c['report']}: {len(c['vector_crops'])}/{c['records']}", flush=True)
    return out


# ---------------------------------------------------------------- step 2: OpenCLIP vectors (cached)
def quick_gelu(model_name, pretrained):
    """The OpenAI CLIP weights were trained with QuickGELU; open_clip's 'ViT-L-14' config uses
    GELU and only warns about the mismatch, so the activation has to be forced."""
    import open_clip
    return bool((open_clip.get_pretrained_cfg(model_name, pretrained) or {}).get("quick_gelu"))


def hf_revision(model_name, pretrained):
    import open_clip
    cfg = open_clip.get_pretrained_cfg(model_name, pretrained) or {}
    hub = (cfg.get("hf_hub") or "").strip("/")
    if not hub:
        return hub, None
    ref = Path(os.environ["HF_HOME"]) / "hub" / f"models--{hub.replace('/', '--')}" / "refs" / "main"
    return hub, ref.read_text().strip() if ref.exists() else None


class Clip:
    def __init__(self, pf, device):
        import open_clip
        import torch
        self.torch = torch
        hub, rev = hf_revision(pf["model"], pf["pretrained"])
        qg = quick_gelu(pf["model"], pf["pretrained"])
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            pf["model"], pretrained=pf["pretrained"], device=device, force_quick_gelu=qg)
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(pf["model"])
        self.device = device
        self.info = {"model": pf["model"], "pretrained": pf["pretrained"], "quick_gelu": qg, "hf_hub": hub,
                     "hf_revision": rev, "logit_scale": float(self.model.logit_scale.exp().item()), "device": device}

    def load(self, path):
        from PIL import Image
        try:
            with Image.open(path) as im:
                return self.preprocess(im.convert("RGB")), True
        except Exception:
            return None, False

    def images(self, paths, batch_size, workers):
        import numpy as np
        t = self.torch
        out = np.zeros((len(paths), self.model.visual.output_dim), dtype=np.float16)
        ok = np.zeros(len(paths), dtype=bool)
        with ThreadPoolExecutor(workers) as pool:
            for start in range(0, len(paths), batch_size):
                loaded = list(pool.map(self.load, paths[start:start + batch_size]))
                idx = [i for i, (_, good) in enumerate(loaded) if good]
                if not idx:
                    continue
                with t.no_grad():
                    x = t.stack([loaded[i][0] for i in idx]).to(self.device)
                    v = self.model.encode_image(x)
                    v = (v / v.norm(dim=-1, keepdim=True)).float().cpu().numpy()
                for k, i in enumerate(idx):
                    out[start + i] = v[k]
                    ok[start + i] = True
        return out, ok

    def texts(self, prompts):
        t = self.torch
        with t.no_grad():
            v = self.model.encode_text(self.tokenizer(prompts).to(self.device))
            return (v / v.norm(dim=-1, keepdim=True)).float().cpu().numpy()


def model_dir(features, pf):
    qg = "_quickgelu" if quick_gelu(pf["model"], pf["pretrained"]) else ""
    return Path(features) / f"openclip_{pf['model']}_{pf['pretrained']}{qg}"


def cached_image_vectors(name, crops, crop_dir, mdir, clip_factory, pf):
    """Vectors for every crop of one report, from work/features when the crop file is unchanged."""
    import numpy as np
    path = mdir / f"{name}.npz"
    sizes = [(crop_dir / c).stat().st_size if (crop_dir / c).exists() else -1 for c in crops]
    have = {}
    if path.exists():
        z = np.load(path, allow_pickle=False)
        for c, s, v, good in zip(z["crop"], z["bytes"], z["emb"], z["ok"]):
            have[str(c)] = (int(s), v, bool(good))
    todo = [i for i, (c, s) in enumerate(zip(crops, sizes)) if c not in have or have[c][0] != s]
    if todo:
        clip = clip_factory()
        vec, ok = clip.images([str(crop_dir / crops[i]) for i in todo], pf.get("batch_size", 64),
                              pf.get("load_workers", 8))
        for k, i in enumerate(todo):
            have[crops[i]] = (sizes[i], vec[k], bool(ok[k]))
    dim = next(iter(have.values()))[1].shape[0] if have else 768
    emb = np.stack([have[c][1] for c in crops]) if crops else np.zeros((0, dim), np.float16)
    ok = np.array([have[c][2] for c in crops], dtype=bool)
    if todo:
        mdir.mkdir(parents=True, exist_ok=True)
        np.savez(path, crop=np.array(crops), bytes=np.array(sizes, dtype=np.int64), emb=emb.astype(np.float16), ok=ok)
    return emb.astype(np.float32), ok, len(todo)


def cached_text_vectors(prompts, mdir, clip_factory):
    import numpy as np
    key = sha256_text(json.dumps(prompts, ensure_ascii=False))[:16]
    path = mdir / f"text_{key}.npz"
    if path.exists():
        z = np.load(path, allow_pickle=False)
        if [str(p) for p in z["prompt"]] == prompts:
            return z["emb"], float(z["logit_scale"])
    clip = clip_factory()
    emb = clip.texts(prompts)
    mdir.mkdir(parents=True, exist_ok=True)
    np.savez(path, prompt=np.array(prompts), emb=emb, logit_scale=np.array(clip.info["logit_scale"]))
    return emb, clip.info["logit_scale"]


# ---------------------------------------------------------------- step 3: scores and rows
def softmax_rows(x):
    import numpy as np
    x = x - x.max(axis=1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=1, keepdims=True)


def prompt_col(p):
    return f"p[{p}]"


def build_rows(row, recs, image_only, vector_crops, probs, ok, prompts, n_photo, thr, vlm=None, vthr=None):
    n_pages = int(row["pages"])
    out = []
    for k, r in enumerate(recs):
        photo = float(probs[k, :n_photo].sum()) if ok[k] else None
        flags = {
            "image_only_report": image_only,
            "cover_page": r["page"] in (1, n_pages),
            "vector_shading": r["crop"] in vector_crops,
            "layout_repeat": r["doc_role"] == "layout_repeat",
            "duplicate": r["doc_role"] != "layout_repeat" and not r["representative"],
        }
        v = (vlm or {}).get(r["crop"])
        is_photo = (photo is not None and photo >= thr) or (vthr is not None and v is not None and v >= vthr)
        reasons = [k2 for k2 in REASONS[:-1] if flags[k2]] + ([] if is_photo else ["nonphoto"])
        top = int(probs[k].argmax()) if ok[k] else None
        d = {"crop": r["crop"], "report": row["name"], "firm_id": row["firm_id"].strip(), "year": row["year"].strip(),
             "split": row["split"].strip(), "page": r["page"], "n_pages": n_pages, "idx": r["idx"],
             "px_w": r["px_w"], "px_h": r["px_h"], "area_ratio": r["area_ratio"], "doc_role": r["doc_role"],
             "representative": int(r["representative"]),
             "cover_page": int(flags["cover_page"]), "image_only_report": int(image_only),
             "vector_shading": int(flags["vector_shading"]),
             "photo_score": None if photo is None else round(photo, 4), "vlm_p": v,
             "nonphoto_score": None if photo is None else round(1 - photo, 4)}
        for j, p in enumerate(prompts):
            d[prompt_col(p)] = round(float(probs[k, j]), 4) if ok[k] else None
        d.update({"top_prompt": prompts[top] if top is not None else "", "is_photo": int(is_photo),
                  "exclude": "|".join(reasons), "keep": int(not reasons)})
        out.append(d)
    return out


def load_vlm_answers(cfg):
    """photo_vlm.py's cached p(camera photo) per crop, for the configured model revision and prompt."""
    import hashlib
    ev = cfg["evidence"]
    ph = hashlib.sha256((ROOT / "configs" / "prompts" / "is_photo.txt").read_bytes()).hexdigest()
    out = {}
    path = ROOT / "work" / "evidence" / "vlm_is_photo.jsonl"
    if path.exists():
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            if r.get("revision") == ev["vlm_revision"] and r.get("prompt_sha256") == ph:
                out[r["crop"]] = r["p_yes"]
    return out


def waterfall(rows):
    """Counts in REASONS order, each crop counted under its first reason (Table 1/2 style)."""
    c = collections.Counter()
    for d in rows:
        first = d["exclude"].split("|")[0] if d["exclude"] else "keep"
        c[first] += 1
    return {"crops": len(rows), **{k: c[k] for k in REASONS}, "photos": c["keep"],
            "candidates": len(rows) - sum(c[k] for k in REASONS[:-1])}


# ---------------------------------------------------------------- HTML
CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#666;--line:#ddd;--ok:#137333;--bad:#b3261e;--card:#f6f6f6;--bar:#6b8fd6}
@media (prefers-color-scheme: dark){:root{--bg:#161616;--fg:#eee;--mut:#aaa;--line:#333;--ok:#6dd58c;--bad:#ff8a80;--card:#222;--bar:#88a6e8}}
body{background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;margin:0 auto;max-width:1240px;padding:16px}
table{border-collapse:collapse;margin:8px 0 20px;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:5px 9px;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left} .wrap{overflow-x:auto}
.ok{color:var(--ok);font-weight:600} .bad{color:var(--bad);font-weight:600} .mut{color:var(--mut)}
.grid{display:flex;flex-wrap:wrap;gap:10px} .card{background:var(--card);border-radius:8px;padding:6px;width:172px}
.card img{max-width:160px;max-height:160px;display:block;margin:auto} .card p{margin:3px 0 0;font-size:12px;line-height:1.35;word-break:break-all}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px} @media (max-width:800px){.two{grid-template-columns:1fr}}
.bar{display:inline-block;height:12px;background:var(--bar);vertical-align:middle}
.note{background:var(--card);border-radius:8px;padding:10px 14px;margin:10px 0}
figure{margin:8px 0} figure img{max-width:100%;border:1px solid var(--line)}
"""


def thumb(path, size=160):
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((size, size))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=78)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def short_prompt(p):
    return p.replace("a photograph of ", "photo: ").replace("an ", "").replace("a ", "")


def cards(items, crop_dir, prompts):
    e = html.escape
    out = []
    for d in items:
        src = thumb(Path(crop_dir) / d["crop"])
        img = f'<img src="{src}" alt="">' if src else '<p class="mut">（沒有裁切圖）</p>'
        score = "—" if d["photo_score"] is None else f"{d['photo_score']:.3f}"
        top = d["top_prompt"]
        topv = d.get(prompt_col(top)) if top else None
        out.append(f'<div class="card">{img}<p><b>照片分數 {score}</b></p>'
                   f'<p class="mut">最高：{e(short_prompt(top))} {"" if topv is None else f"{topv:.2f}"}</p>'
                   f'<p class="mut">{e(d["crop"])}</p></div>')
    return '<div class="grid">' + "".join(out) + "</div>"


def write_html(path, result, all_rows, crop_dir):
    e = html.escape
    meta, pf = result["meta"], result["photo_filter"]
    thr, prompts = pf["photo_threshold"], pf["photo_prompts"] + pf["nonphoto_prompts"]
    by_crop = {d["crop"]: d for d in all_rows}
    tot = result["totals"]
    head = "".join(f"<th>{e(REASON_LABEL[k])}</th>" for k in REASONS)
    trs = []
    for name, w in list(result["per_report"].items()) + [("合計", tot)]:
        trs.append(f"<tr><td>{e(name)}</td><td>{w['crops']}</td>" + "".join(f"<td>{w[k]}</td>" for k in REASONS)
                   + f"<td>{w['candidates']}</td><td><b>{w['photos']}</b></td></tr>")
    s = result["samples"]
    hist = result["histogram"]
    top_n = max(hist["counts"]) or 1
    hist_rows = "".join(
        f"<tr><td>{lo:.1f}–{lo + 0.1:.1f}</td><td>{n}</td><td><span class='bar' style='width:{int(300 * n / top_n)}px'></span></td></tr>"
        for lo, n in zip(hist["lo"], hist["counts"]))
    parts = [f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>照片過濾 {e(result['tag'])}</title>
<style>{CSS}</style></head><body>
<h1>照片過濾：{e(result['tag'])}</h1>
<p class="mut">{e(meta['date'])}｜{e(result['model']['model'])} / {e(result['model']['pretrained'])}（HF {e(str(result['model']['hf_revision']))}）｜git {e(str(meta['git'].get('commit')))}{'（有未 commit 的修改）' if meta['git'].get('uncommitted_changes') else ''}</p>
<div class="note"><b>結果：</b>{len(result['per_report'])} 本報告、{tot['crops']} 張裁切圖。先排除 {tot['crops'] - tot['candidates']} 張（沒有文字層的報告、封面頁、向量漸層、版面元素、重複），剩 {tot['candidates']} 張給模型判斷；
照片分數 ≥ {thr}（初值）算照片：<b>{tot['photos']} 張照片、{tot['nonphoto']} 張不是照片</b>。
<br>照片分數 = 兩個照片類提示詞的機率加總（7 個提示詞一起做 softmax）。門檻還沒調：PLAN §5 規定要用標註 A 開發集 100 張決定，現在只是初值。
<br>這份只抽開發集的圖（CLAUDE.md 鐵則 3）。</div>
<h2>每本報告的數字</h2><p class="mut">每張圖只算在第一個符合的排除原因（由左到右）。「給模型判斷」= 沒有前五個原因的圖。</p>
<div class="wrap"><table><tr><th>報告</th><th>裁切圖</th>{head}<th>給模型判斷</th><th>照片</th></tr>{''.join(trs)}</table></div>
<h2>提示詞</h2><p>照片類：{e('、'.join(pf['photo_prompts']))}<br>非照片類：{e('、'.join(pf['nonphoto_prompts']))}</p>
<h2>給模型判斷的圖：照片分數分布</h2><div class="wrap"><table><tr><th>照片分數</th><th>張數</th><th></th></tr>{hist_rows}</table></div>
<h2>隨機抽：照片 {len(s['photo'])} 張、非照片 {len(s['nonphoto'])} 張（並排）</h2>
<div class="two"><div><h3>判斷為照片（分數 ≥ {thr}）</h3>{cards([by_crop[c] for c in s['photo']], crop_dir, prompts)}</div>
<div><h3>判斷為非照片（分數 &lt; {thr}）</h3>{cards([by_crop[c] for c in s['nonphoto']], crop_dir, prompts)}</div></div>
<h2>可能錯的例子：最靠近門檻的圖</h2><p class="mut">左邊是剛好過門檻（算照片）、右邊是差一點（算非照片）。門檻附近最容易錯。</p>
<div class="two"><div><h3>剛好 ≥ {thr}</h3>{cards([by_crop[c] for c in s['near_above']], crop_dir, prompts)}</div>
<div><h3>剛好 &lt; {thr}</h3>{cards([by_crop[c] for c in s['near_below']], crop_dir, prompts)}</div></div>
<h2>分數由低到高，每一段抽 6 張</h2><p class="mut">看照片從哪一段開始變多，可以大概看出門檻該放哪裡（正式門檻還是要用標註 A 定）。</p>"""]
    for label, crops in s["by_bin"]:
        parts.append(f"<h3>照片分數 {e(label)}</h3>{cards([by_crop[c] for c in crops], crop_dir, prompts) if crops else '<p class=mut>（沒有圖）</p>'}")
    parts.append("<h2>每個提示詞「得分最高」的例子（各抽 6 張）</h2>")
    for p, crops in s["by_prompt"]:
        parts.append(f"<h3>{e(p)}</h3>{cards([by_crop[c] for c in crops], crop_dir, prompts) if crops else '<p class=mut>（沒有圖）</p>'}")
    parts.append("<h2>先排除的圖（模型沒參與決定）</h2>")
    for k, crops in s["excluded"]:
        parts.append(f"<h3>{e(REASON_LABEL[k])}（抽 {len(crops)} 張）</h3>"
                     f"{cards([by_crop[c] for c in crops], crop_dir, prompts) if crops else '<p class=mut>（沒有圖）</p>'}")
    if result["vector_mismatch_pages"]:
        parts.append(f"<p class='bad'>向量漸層檢查：有頁面和抽取紀錄對不上，沒有標記：{e(json.dumps(result['vector_mismatch_pages'], ensure_ascii=False))}</p>")
    parts.append("</body></html>")
    Path(path).write_text("\n".join(parts), encoding="utf-8")


def write_vector_html(path, result, catalog_rows, cache):
    """Vector-only stage: counts per report and two pages drawn with the flagged boxes."""
    import pymupdf
    e = html.escape
    meta = result["meta"]
    tot = result["totals"]
    rows = sorted(result["per_report"].items(), key=lambda kv: -kv[1]["vector_crops"])
    trs = "".join(f"<tr><td>{e(n)}</td><td>{e(v['split'])}</td><td>{v['records']}</td><td><b>{v['vector_crops']}</b></td>"
                  f"<td>{v['vector_analysed']}</td><td>{v['analysed']}</td><td>{v['mixed_kept']}</td><td>{len(v['mismatch_pages'])}</td></tr>"
                  for n, v in rows[:40])
    figs = []
    by_name = {r["name"]: r for r in catalog_rows}
    shown = []
    for split in ("test", "dev"):  # the worst report overall, and the worst development-set report
        cand = [n for n, v in rows if v["vector_crops"] and v["split"] == split and n not in shown]
        if cand:
            shown.append(cand[0])
    for name in shown:
        recs = read_pairs(Path(result["extract"]) / f"{name}_pairs.jsonl")
        flagged = set(cache[name]["vector_crops"])
        per_page = collections.Counter(r["page"] for r in recs if r["crop"] in flagged)
        p = per_page.most_common(1)[0][0]
        doc = pymupdf.open(by_name[name]["file_path"].strip())
        page = doc[p - 1]
        for r in recs:
            if r["page"] == p:
                red = r["crop"] in flagged
                page.draw_rect(pymupdf.Rect(r["bbox"]), color=(0.85, 0, 0) if red else (0, 0.2, 0.9), width=1.2 if red else 4)
        pix = page.get_pixmap(dpi=60)
        b64 = base64.b64encode(pix.tobytes("jpg", jpg_quality=75)).decode()
        doc.close()
        n_page = sum(1 for r in recs if r["page"] == p)
        figs.append(f"<figure><img src='data:image/jpeg;base64,{b64}' alt=''><figcaption>{e(name)} 第 {p} 頁：抽到 {n_page} 張，"
                    f"其中 <b>{per_page[p]} 張是向量漸層</b>（紅色細框）；藍色粗框是留下來的內嵌圖片。</figcaption></figure>")
    Path(path).write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>向量漸層檢查</title><style>{CSS}</style></head><body>
<h1>向量漸層被當成圖片：{len(result['per_report'])} 本報告的檢查</h1>
<p class="mut">{e(meta['date'])}｜PyMuPDF {e(str(meta['packages'].get('pymupdf')))}｜git {e(str(meta['git'].get('commit')))}</p>
<div class="note"><b>結果：</b>抽取時的 {tot['records']} 張裁切圖裡，<b>{tot['vector_crops']} 張</b>不是內嵌圖片，而是 PDF 的向量漸層（背景斜線、卡片陰影、色塊漸層），
分布在 {tot['reports_with_vector']} 本報告。只算「列入分析」的圖（抽取時的定義）：{tot['analysed']} 張裡有 {tot['vector_analysed']} 張。
<br>原因：PyMuPDF 的 <code>get_image_info()</code> 會把漸層填色也當成圖片回報。這種「圖片」沒有內嵌檔（xref 0）、一個點剛好一個像素、像素沒壓縮。真的內嵌圖片不會同時符合這三點。
<br>和抽取紀錄逐頁比對：對不上的頁數 {tot['mismatch_pages']}（對不上的頁不標記）。真圖片上面疊一層漸層的（合併成一張）照樣保留：{tot['mixed_kept']} 張。</div>
<h2>例子</h2>{''.join(figs)}
<h2>向量漸層最多的 40 本</h2><div class="wrap"><table><tr><th>報告</th><th>組別</th><th>裁切圖</th><th>向量漸層</th><th>其中列入分析的</th><th>列入分析</th><th>真圖片疊漸層（保留）</th><th>對不上的頁</th></tr>{trs}</table></div>
</body></html>""", encoding="utf-8")


# ---------------------------------------------------------------- main
def sample(rng, items, n):
    items = list(items)
    return rng.sample(items, n) if len(items) > n else items


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    pick = ap.add_mutually_exclusive_group(required=True)
    pick.add_argument("--firms", nargs="+", help="firm ids, all three years")
    pick.add_argument("--reports", nargs="+", help="report names such as 1101_2025")
    pick.add_argument("--all", action="store_true")
    ap.add_argument("--stage", choices=["full", "vector"], default="full",
                    help="vector = only the PDF check for vector shading (no model)")
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--download-check", help="default: the newest results/download_check_*.json")
    ap.add_argument("--out", default="work/photo_filter.csv")
    ap.add_argument("--tag", help="label for result file names (default: 5firms / all / vector)")
    ap.add_argument("--workers", type=int, default=8, help="processes for the PDF check")
    ap.add_argument("--device", default=None)
    ap.add_argument("--n-sample", type=int, default=30)
    args = ap.parse_args()

    cfg = load_yaml(args.config)
    pf, paths, seed = cfg["photo_filter"], cfg["paths"], cfg["seed"]
    args.extract = paths["extract"]
    crop_dir = Path(paths["extract"]) / "crops"
    with open(paths["catalog"], encoding="utf-8-sig", newline="") as f:
        catalog = list(csv.DictReader(f))
    rows = select_reports(catalog, args)
    missing = [r["name"] for r in rows if not (Path(paths["extract"]) / f"{r['name']}_pairs.jsonl").exists()]
    if missing:
        sys.exit(f"沒有抽取結果：{missing}")
    if not rows:
        sys.exit("沒有選到任何報告")
    dl_path = Path(args.download_check) if args.download_check else latest("download_check_*.json")
    dl = {r["name"]: r for r in json.loads(Path(dl_path).read_text(encoding="utf-8"))["reports"]}
    not_checked = [r["name"] for r in rows if r["name"] not in dl]
    if not_checked:
        sys.exit(f"{dl_path} 沒有這些報告的 image_only：{not_checked}")
    image_only = {r["name"] for r in rows if dl[r["name"]]["image_only"]}
    tag = args.tag or ("vector" if args.stage == "vector" else ("all" if args.all else f"{len({r['firm_id'] for r in rows})}firms"))
    stamp = run_stamp()
    feat = Path(paths["features"])
    data_files = [paths["catalog"], str(dl_path)] + [str(Path(paths["extract"]) / f"{r['name']}_pairs.jsonl") for r in rows]

    t0 = time.time()
    vec = run_vector_checks(rows, args, feat / "vector_shading") if pf.get("exclude_vector_shading", True) else {}
    print(f"[vector] 完成，{time.time() - t0:.0f} 秒", flush=True)

    Path(paths["results"]).mkdir(exist_ok=True)
    Path(paths["reports"]).mkdir(exist_ok=True)
    if args.stage == "vector":
        per = {}
        for r in rows:
            c = vec[r["name"]]
            recs = read_pairs(Path(paths["extract"]) / f"{r['name']}_pairs.jsonl")
            flagged = set(c["vector_crops"])
            analysed = [x for x in recs if x["representative"] and x["doc_role"] != "layout_repeat"]
            per[r["name"]] = {"split": r["split"].strip(), "image_only_report": r["name"] in image_only,
                              "records": c["records"], "vector_crops": len(flagged),
                              "analysed": len(analysed), "vector_analysed": sum(x["crop"] in flagged for x in analysed),
                              "mixed_kept": c["mixed_kept"], "mismatch_pages": c["mismatch_pages"],
                              "pages_checked": c["pages_checked"]}
        tot = {k: sum(v[k] for v in per.values()) for k in ("records", "vector_crops", "analysed", "vector_analysed", "mixed_kept")}
        tot["mismatch_pages"] = sum(len(v["mismatch_pages"]) for v in per.values())
        tot["reports_with_vector"] = sum(v["vector_crops"] > 0 for v in per.values())
        for split in ("dev", "test"):
            sub = [v for v in per.values() if v["split"] == split]
            tot[split] = {k: sum(v[k] for v in sub) for k in ("records", "vector_crops", "analysed", "vector_analysed")}
        result = {"meta": run_meta(args.config, data_files), "tag": tag, "extract": paths["extract"],
                  "rule": is_shading_image.__doc__, "totals": tot, "per_report": per}
        out_json = Path(paths["results"]) / f"photo_filter_vector_{stamp}.json"
        out_html = Path(paths["reports"]) / f"photo_filter_vector_{stamp}.html"
        out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        write_vector_html(out_html, result, rows, vec)
        print(json.dumps(tot, ensure_ascii=False))
        print(f"wrote {out_json} and {out_html}")
        return

    import numpy as np
    import torch
    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    mdir = model_dir(feat, pf)
    holder = {}

    def clip_factory():
        if "clip" not in holder:
            print(f"[clip] 載入 {pf['model']} / {pf['pretrained']}（{device}）", flush=True)
            holder["clip"] = Clip(pf, device)
        return holder["clip"]

    prompts = pf["photo_prompts"] + pf["nonphoto_prompts"]
    n_photo = len(pf["photo_prompts"])
    text, logit_scale = cached_text_vectors(prompts, mdir, clip_factory)
    thr = float(pf["photo_threshold"])
    vthr = pf.get("vlm_threshold")
    vlm = load_vlm_answers(cfg) if vthr is not None else None
    all_rows, per, encoded = [], {}, 0
    t1 = time.time()
    for r in rows:
        recs = read_pairs(Path(paths["extract"]) / f"{r['name']}_pairs.jsonl")
        emb, ok, n_new = cached_image_vectors(r["name"], [x["crop"] for x in recs], crop_dir, mdir, clip_factory, pf)
        encoded += n_new
        probs = softmax_rows(logit_scale * emb @ text.T) if len(recs) else np.zeros((0, len(prompts)))
        flagged = set(vec.get(r["name"], {}).get("vector_crops", []))
        rws = build_rows(r, recs, r["name"] in image_only, flagged, probs, ok, prompts, n_photo, thr, vlm, vthr)
        all_rows += rws
        per[r["name"]] = waterfall(rws)
        print(f"[clip] {r['name']}: {len(recs)} 張（新算 {n_new}），照片 {per[r['name']]['photos']}", flush=True)
    clip_s = time.time() - t1

    out_csv = Path(args.out)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    cols = list(all_rows[0].keys())
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(all_rows)

    tot = waterfall(all_rows)
    cand = [d for d in all_rows if d["photo_score"] is not None and
            not set(d["exclude"].split("|")) & set(REASONS[:-1])]
    dev_cand = [d for d in cand if d["split"] == "dev"]
    rng = random.Random(seed)
    photos = [d["crop"] for d in dev_cand if d["is_photo"]]
    nonph = [d["crop"] for d in dev_cand if not d["is_photo"]]
    above = sorted((d for d in dev_cand if d["is_photo"]), key=lambda d: d["photo_score"])[:15]
    below = sorted((d for d in dev_cand if not d["is_photo"]), key=lambda d: -d["photo_score"])[:15]
    edges = [i / 10 for i in range(11)]
    by_bin = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        inside = [d["crop"] for d in dev_cand if lo <= d["photo_score"] < hi or (hi == 1.0 and d["photo_score"] == 1.0)]
        by_bin.append((f"{lo:.1f}–{hi:.1f}（{len(inside)} 張）", sample(rng, inside, 6)))
    by_prompt = [(p, sample(rng, [d["crop"] for d in dev_cand if d["top_prompt"] == p], 6)) for p in prompts]
    excluded = []
    for k in REASONS[:-1]:
        first = [d["crop"] for d in all_rows if d["split"] == "dev" and d["exclude"].split("|")[0] == k]
        excluded.append((k, sample(rng, first, 12)))
    hist = {"lo": edges[:-1], "counts": [sum(1 for d in cand if lo <= d["photo_score"] < hi or (hi == 1.0 and d["photo_score"] == 1.0))
                                         for lo, hi in zip(edges[:-1], edges[1:])]}
    clip_info = holder["clip"].info if "clip" in holder else dict(
        zip(["hf_hub", "hf_revision"], hf_revision(pf["model"], pf["pretrained"])), model=pf["model"],
        pretrained=pf["pretrained"], quick_gelu=quick_gelu(pf["model"], pf["pretrained"]),
        logit_scale=logit_scale, device=device, note="all vectors came from the cache")
    clip_info["cache_dir"] = str(mdir)
    meta = run_meta(args.config, data_files + [str(out_csv)],
                    packages=("pymupdf", "pillow", "numpy", "pyyaml", "torch", "open_clip_torch", "torchvision"))
    result = {
        "meta": meta, "tag": tag, "model": clip_info,
        "photo_filter": {k: pf.get(k) for k in ("photo_prompts", "nonphoto_prompts", "photo_threshold", "vlm_threshold")},
        "vlm_rule": None if vthr is None else {
            "answers_loaded": len(vlm), "judged_without_answer": sum(1 for d in all_rows if not (set(d["exclude"].split("|")) & set(REASONS[:-1])) and d["vlm_p"] is None),
            "photos_by_openclip_only": sum(1 for d in all_rows if d["keep"] and (d["photo_score"] or 0) >= thr and (d["vlm_p"] or 0) < vthr),
            "photos_by_vlm_only": sum(1 for d in all_rows if d["keep"] and (d["photo_score"] or 0) < thr),
            "photos_by_both": sum(1 for d in all_rows if d["keep"] and (d["photo_score"] or 0) >= thr and (d["vlm_p"] or 0) >= vthr)},
        "prompts_sha256": sha256_text(json.dumps(prompts, ensure_ascii=False)),
        "download_check": str(dl_path), "csv": str(out_csv),
        "image_only_reports": sorted(image_only), "n_image_only_reports": len(image_only),
        "vector_mismatch_pages": {n: c["mismatch_pages"] for n, c in vec.items() if c["mismatch_pages"]},
        "seconds": {"vector": round(t1 - t0), "clip_and_score": round(clip_s)}, "crops_encoded_this_run": encoded,
        "totals": tot, "per_report": per,
        "per_split": {s: waterfall([d for d in all_rows if d["split"] == s]) for s in sorted({d["split"] for d in all_rows})},
        "histogram": hist,
        "samples": {"photo": sample(rng, photos, args.n_sample), "nonphoto": sample(rng, nonph, args.n_sample),
                    "near_above": [d["crop"] for d in above], "near_below": [d["crop"] for d in below],
                    "by_bin": by_bin, "by_prompt": by_prompt, "excluded": excluded},
    }
    out_json = Path(paths["results"]) / f"photo_filter_{stamp}_{tag}.json"
    out_html = Path(paths["reports"]) / f"photo_filter_{stamp}_{tag}.html"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    write_html(out_html, result, all_rows, crop_dir)
    print(json.dumps(tot, ensure_ascii=False))
    print(f"wrote {out_csv}, {out_json} and {out_html}")


if __name__ == "__main__":
    main()
