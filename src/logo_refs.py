"""Logo reference images for every firm (PLAN v2.0 §5 step 2 "logo 參考"). evidence.py (step 5)
matches them against photos with SuperPoint + LightGlue and a MAGSAC homography (Table 6 ablation
"no logo anchor", Fig. 3 anchor examples).

Four stages:
  1. scan, one PDF at a time, cached in work/logo_refs/_scan/{report}.json. Every embedded-image
     placement comes from extract_pairs.image_info(); vector gradient fills are skipped with
     is_shading_image (each one is counted again here, as a check). A placement is kept when
       - the image file is >= min_px pixels on its short side (16: smaller ones are bullets, dots
         and hairlines, and SuperPoint, which works on 8x8 cells, finds almost nothing in them);
       - it is drawn >= scan_min_pt on its short side (the lowest of the three floors below);
       - it covers <= max_area_frac of the page, and at least half of it is on the page.
     Placements of the same image are grouped (MD5 of the embedded file, the same key as
     layer_md5; for inline images the pixel digest). Each group keeps its pages and its drawn
     size on each page, and gets a clean picture (soft mask composited on white, or on dark grey
     when the logo itself is white). Groups that could become candidates also get a PNG, a QR
     test and "text on top": the share of their placements that have a text word drawn inside
     the image box (navigation tabs, title backgrounds).
  2. candidates of a firm, merged across years (same file, or the same look: 256-bit dHash,
     aspect ratio and mean colour close). Sources (PLAN §5 step 2):
       repeated   - on >= min_pages pages of one report, drawn >= min_short_side_pt (10 pt: decision 5, 10/18);
       cover      - on page 1, drawn >= cover_min_short_side_pt (8 pt; PLAN sets no floor here);
       back_cover - on the last page, same floor (use_back_cover: an extension of PLAN's cover).
     Logo score = weighted mean of eight 0-1 features (configs logo_refs.weights): small, edge,
     repeat (most pages in one report / 10), cover, cross_year, aspect, alpha, flat.
     Never proposed: shared (the same image is in >= shared_min_firms firms' reports), tile (abuts
     another image edge to edge, or partly overlaps another small image: a piece of a picture put
     together from several images), background (text on top), qr, blank (faint), too_simple (fewer ORB keypoints than
     evidence.logo_min_inliers).
  3. OCR of the candidates that are not excluded (PaddleOCR in .venv_ocr, cached in
     work/logo_refs/_ocr/), matched against the firm's names: catalog short name, full Chinese
     name (reports_index.csv) and the English name and English short name of the ESG InfoHub
     company list (data/index/raw/company_lists.json).
  4. auto proposal, then Eden's review.
       proposed = not excluded, score >= select_min_score, top max_refs_per_firm, and at least
       one of: on >= min_pages pages of one report (any drawn size >= scan_min_pt; look-alikes
       count), also used in another year, or its OCR text matches a firm name. A picture seen
       only on one year's cover or back cover was wrong 14 times out of 15 in the 10/18 dev check.
       reports/logo_refs/review_{tag}.html lets Eden tick the real logos and pick the logo form
       of each firm; its CSV goes to annotations/logo_refs/review*.csv. Only confirmed pictures
       become references (work/logo_refs/{firm}/refs/), and coverage counts only those.
     What-if: the same with the repeated-source floor at whatif_min_short_side_pt, to show Eden what a
     lower floor would add. It changes no output (with both at 10 pt it adds nothing).
  Vector logos (decision 10): clusters of vector paths on the cover / back cover and in repeated
     header / footer spots are rendered and become candidates like images (vector_groups()).
No language or vision model is used to pick logos: every number is a plain image or layout
measurement, plus the OCR text.

Writes work/logo_refs/{firm}/ (candidates/cand_XX.png, refs/ref_XX.png, index.json),
work/logo_refs/missing.csv (rebuilt from every firm's index.json), results/logo_refs_{stamp}_{tag}.json
and reports/logo_refs/{firm}.html, index.html, review_{tag}.html. Reports without a text layer
(image_only in results/download_check_*.json) are skipped and listed. A run with --reports or a
shared scope other than the configured one writes to work/logo_refs/_try/{stamp}_{tag}/ and
reports/logo_refs/_try/{stamp}_{tag}/ instead, so a quick look never replaces the real outputs.

  python src/logo_refs.py --firms 1101 1707 2882 2610 2385
  python src/logo_refs.py --split dev
  python src/logo_refs.py --all
  python src/logo_refs.py --reports 1101_2025 --shared-scope selected     # quick look
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
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import unicodedata
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import extract_pairs as ep  # noqa: E402
from meta import run_meta, run_stamp  # noqa: E402

SCAN_VERSION = 5  # 5: vector-drawn logos (10/18 decision 10)
SCAN_KEYS = ("min_px", "scan_min_pt", "max_area_frac", "edge_frac", "tile_gap_pt", "min_pages", "ref_long_side",
             "vector", "vector_band", "vector_tolerance", "vector_min_items", "vector_max_long_frac", "vector_render_px")
DEFAULTS = {"min_pages": 3, "min_short_side_pt": 10, "use_cover": True, "use_back_cover": True,
            "vector": True, "vector_band": 0.15, "vector_tolerance": 6, "vector_min_items": 3,
            "vector_max_long_frac": 0.5, "vector_render_px": 600,
            "cover_min_short_side_pt": 8, "whatif_min_short_side_pt": 10, "min_px": 16,
            "max_area_frac": 0.10, "edge_frac": 0.12, "tile_gap_pt": 1.5, "shared_min_firms": 3,
            "shared_scope": "dev", "lookalike_dhash_bits": 26, "lookalike_aspect_tol": 0.10,
            "lookalike_colour_tol": 30, "shared_dhash_bits": 32, "ref_long_side": 1024, "max_refs_per_firm": 5,
            "select_min_score": 0.5, "min_contrast": 48, "text_on_top_share": 0.5,
            "ocr": True, "ocr_lang": "chinese_cht", "ocr_min_score": 0.5,
            "review_glob": "annotations/logo_refs/review*.csv",
            "weights": {"small": 1.0, "edge": 1.0, "repeat": 1.0, "cover": 1.0, "cross_year": 2.0, "aspect": 0.5,
                        "alpha": 1.0, "flat": 1.0}}
FEATURES = ["small", "edge", "repeat", "cover", "cross_year", "aspect", "alpha", "flat"]
FEATURE_LABEL = {"small": "小", "edge": "靠邊", "repeat": "多頁", "cover": "封面封底", "cross_year": "跨年",
                 "aspect": "長寬", "alpha": "透明底", "flat": "色少"}
SOURCE_LABEL = {"repeated": "重複出現的小圖", "cover": "封面", "back_cover": "封底"}  # images and vector drawings alike
EXCLUDE_LABEL = {"shared": "共用圖示（很多家公司都有）", "tile": "碎片（大圖切成的，或跟別的小圖疊在一起拼成一張圖）",
                 "background": "底圖（上面疊了文字：頁籤、標題底色）", "qr": "QR code",
                 "blank": "空白或很淡的圖（色塊、陰影、漸層）", "too_simple": "特徵點太少（之後比對不到）"}
EXCLUDE_ORDER = list(EXCLUDE_LABEL)
STATUS_LABEL = {"proposed": "自動提議（還沒確認）", "no_gate": "沒提議：只在某一年的封面或封底，OCR 也對不上公司名稱",
                "low_score": "沒提議：分數不到門檻", "quota": "沒提議：名額已滿", "excluded": "排除"}
FORM_LABEL = {"embedded": "內嵌小圖（上面勾的就是）", "vector": "向量圖（用線條畫的）",
              "in_full_page_image": "畫在整頁大圖裡", "none_seen": "報告裡看不到公司 logo", "unsure": "不確定"}
REVIEW_FIELDS = ["firm_id", "row_type", "cand_id", "keys", "is_logo", "logo_form", "seen", "note",
                 "result_file", "exported_at"]
MAX_PLACEMENTS_KEPT = 30  # placements listed per group in the scan cache (counts are always complete)
MAX_END_PLACEMENTS = 40   # placements on the cover and the back cover kept per group
MAX_EXCLUDED_CARDS = 24   # excluded candidates shown in the HTML (all of them are in index.json)
TEXT_SAMPLE = 8           # inner-page placements checked for text on top (plus all cover/back-cover ones)

OCR_PREP = "v1: upscale to a 320 px long side when smaller (LANCZOS), 16 px white border"
OCR_CODE = r'''
import json, sys
# 10/20: Windows application control blocks shapely's GEOS DLL. Plain OCR never calls shapely (paddlex uses it only for
# layout analysis, region cropping and the OCR-VL pipeline; text detection uses pyclipper), so shapely's modules are
# replaced by stand-ins that fail loudly if anything does call them. Checked: the same texts and scores as before.
import importlib.abc, importlib.machinery, types
class _Blocked(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        def blocked(*a, **k):
            raise RuntimeError('shapely is blocked on this PC; plain OCR should not call it')
        return blocked
class _ShapelyStandIn(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name == 'shapely' or name.startswith('shapely.'):
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
    def create_module(self, spec):
        return _Blocked(spec.name)
    def exec_module(self, module):
        module.__path__ = []
sys.meta_path.insert(0, _ShapelyStandIn())
import paddle, paddleocr
from paddleocr import PaddleOCR
# enable_mkldnn=False: paddle 3.3.1 on Windows CPU fails in oneDNN (check_models.py, 10/18)
opts = dict(use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
            enable_mkldnn=False)
lang = sys.argv[3]
ocr = PaddleOCR(lang=lang, **opts)
jobs = json.load(open(sys.argv[1], encoding="utf-8"))
with open(sys.argv[2], "w", encoding="utf-8") as out:
    out.write(json.dumps({"_model": {"paddleocr": paddleocr.__version__, "paddle": paddle.__version__,
                                     "device": paddle.device.get_device(), "lang": lang}}) + "\n")
    for j in jobs:
        texts, scores = [], []
        for r in ocr.predict(j["path"]):
            texts += [str(x) for x in r["rec_texts"]]
            scores += [round(float(x), 4) for x in r["rec_scores"]]
        out.write(json.dumps({"id": j["id"], "texts": texts, "scores": scores}, ensure_ascii=False) + "\n")
        out.flush()
'''


# ---------------------------------------------------------------- small helpers
def load_yaml(path):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def params_from(cfg):
    p = json.loads(json.dumps(DEFAULTS))
    for k, v in (cfg.get("logo_refs") or {}).items():
        if k == "weights":
            p["weights"].update(v or {})
        else:
            p[k] = v
    ev = cfg.get("evidence") or {}
    # a reference with fewer keypoints than the inliers evidence.py demands can never be found
    p["min_keypoints"] = int(ev.get("logo_min_inliers", 15))
    # the same character-bigram overlap evidence.py uses for OCR anchors (PLAN §5 step 5)
    p["name_bigram_min"] = float(ev.get("ocr_match_threshold", 0.7))
    p["scan_min_pt"] = min(p["min_short_side_pt"], p["cover_min_short_side_pt"], p["whatif_min_short_side_pt"])
    return p


def latest(pattern):
    found = sorted((ROOT / "results").glob(pattern))
    return found[-1] if found else None


def abuts(a, b, tol):
    """True when two rectangles touch edge to edge (a mosaic of image tiles)."""
    if abs(a.x1 - b.x0) <= tol or abs(b.x1 - a.x0) <= tol:
        if min(a.y1, b.y1) - max(a.y0, b.y0) >= 0.5 * min(a.height, b.height):
            return True
    if abs(a.y1 - b.y0) <= tol or abs(b.y1 - a.y0) <= tol:
        if min(a.x1, b.x1) - max(a.x0, b.x0) >= 0.5 * min(a.width, b.width):
            return True
    return False


def edge_flags(r, pr, frac):
    """(near any page edge, near a corner) for rectangle r on page rect pr."""
    lim = frac * min(pr.width, pr.height)
    near_x = (r.x0 - pr.x0) <= lim or (pr.x1 - r.x1) <= lim
    near_y = (r.y0 - pr.y0) <= lim or (pr.y1 - r.y1) <= lim
    return near_x or near_y, near_x and near_y


def dhash256(im):
    """256-bit difference hash (16 x 16) of a PIL image, as 64 hex digits."""
    from PIL import Image
    g = im.convert("L").resize((17, 16), Image.LANCZOS)
    px = list(g.get_flattened_data() if hasattr(g, "get_flattened_data") else g.getdata())
    bits = 0
    for y in range(16):
        for x in range(16):
            bits = (bits << 1) | (px[y * 17 + x] > px[y * 17 + x + 1])
    return f"{bits:064x}"


# ---------------------------------------------------------------- clean pictures
def pix_to_pil(pix):
    import pymupdf
    from PIL import Image
    if pix.colorspace is not None and pix.colorspace.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    n_col = pix.n - pix.alpha
    mode = {1: "L", 3: "RGB"}[n_col]
    if pix.alpha:
        mode = {"L": "LA", "RGB": "RGBA"}[mode]
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def embedded_picture(doc, xref):
    """PIL image (RGBA when there is a soft mask) of an embedded image file."""
    import pymupdf
    im = pix_to_pil(pymupdf.Pixmap(doc, xref))
    if im.mode in ("LA", "RGBA"):
        return im.convert("RGBA")
    kind, val = doc.xref_get_key(xref, "SMask")
    if kind == "xref":
        mask = pix_to_pil(pymupdf.Pixmap(doc, int(val.split()[0]))).convert("L")
        if mask.size != im.size:
            mask = mask.resize(im.size)
        im = im.convert("RGB")
        im.putalpha(mask)
        return im
    return im.convert("RGB")


def inline_picture(page, rect, w_px, h_px, dict_cache):
    """PIL image of an inline image, from the page's text dict image blocks (bytes and mask)."""
    from PIL import Image
    if page.number not in dict_cache:
        dict_cache[page.number] = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 1]
    import pymupdf
    best = None
    for b in dict_cache[page.number]:
        if b.get("width") != w_px or b.get("height") != h_px:
            continue
        iou = ep.rect_iou(pymupdf.Rect(b["bbox"]), rect)
        if iou >= 0.9 and (best is None or iou > best[0]):
            best = (iou, b)
    if best is None:
        raise ValueError("inline image block not found")
    b = best[1]
    im = Image.open(io.BytesIO(b["image"]))
    im.load()
    if b.get("mask"):
        mask = Image.open(io.BytesIO(b["mask"])).convert("L")
        if mask.size != im.size:
            mask = mask.resize(im.size)
        im = im.convert("RGB")
        im.putalpha(mask)
        return im
    return im.convert("RGBA") if im.mode in ("LA", "RGBA", "PA") else im.convert("RGB")


def clip_picture(page, rect, w_px, h_px, long_side):
    """What the reader sees in the box, rendered at about the image's own resolution."""
    import pymupdf
    from PIL import Image
    zoom = max(w_px / max(rect.width, 1e-3), h_px / max(rect.height, 1e-3), 2.0)
    zoom = min(zoom, 8.0, max(2.0, long_side / max(rect.width, rect.height)))
    pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def flatten(im, long_side):
    """Composite on white (dark grey when the visible part is itself almost white) and measure.
    Returns (RGB image, features)."""
    import numpy as np
    from PIL import Image
    if max(im.size) > long_side:
        s = long_side / max(im.size)
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    if im.mode == "RGBA":
        arr = np.asarray(im, dtype=np.float32)
        rgb, a = arr[..., :3], arr[..., 3:] / 255.0
    else:
        rgb = np.asarray(im.convert("RGB"), dtype=np.float32)
        a = np.ones(rgb.shape[:2] + (1,), dtype=np.float32)
    visible = a[..., 0] >= 0.5
    transparent = float(1 - visible.mean())
    bg_name, bg = "white", np.array([255, 255, 255], np.float32)
    if transparent >= 0.05 and visible.sum() > 0:
        lum = rgb[visible] @ np.array([0.299, 0.587, 0.114], np.float32)
        if lum.mean() >= 235:
            bg_name, bg = "dark", np.array([58, 58, 58], np.float32)
    out = rgb * a + bg * (1 - a)
    out_im = Image.fromarray(out.clip(0, 255).astype("uint8"), "RGB")
    small = out_im.copy()
    small.thumbnail((128, 128))
    s_arr = np.asarray(small)
    vis_small = np.asarray(Image.fromarray((visible * 255).astype("uint8")).resize(small.size)) >= 128
    pixels = s_arr[vis_small] if vis_small.sum() >= 16 else s_arr.reshape(-1, 3)
    q = (pixels // 32).astype(np.int32)
    bins = np.bincount(q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2], minlength=512)
    top8 = float(np.sort(bins)[::-1][:8].sum() / max(1, bins.sum()))
    gray = np.asarray(small.convert("L"), dtype=np.float32)
    lo, hi = np.percentile(gray, [2, 98])
    h = dhash256(out_im)
    return out_im, {"bg": bg_name, "transparent_share": round(transparent, 3), "top8_colour_share": round(top8, 3),
                    "gray_std": round(float(gray.std()), 1), "contrast": round(float(hi - lo), 1),
                    "keypoints": keypoint_count(out_im),
                    "dhash": h, "dhash_ones": bin(int(h, 16)).count("1"),
                    "mean_rgb": [round(float(v), 1) for v in s_arr.reshape(-1, 3).mean(axis=0)],
                    "ref_w": out_im.width, "ref_h": out_im.height}


def keypoint_count(im):
    """ORB keypoints (FAST threshold 20, an absolute contrast test) with the picture scaled to a
    256-px long side. A rough stand-in for what SuperPoint can find: flat tiles, shadows and a plain
    dot give 0; a logo with letters gives hundreds. Matching needs >= evidence.logo_min_inliers."""
    import cv2
    import numpy as np
    from PIL import Image
    s = 256 / max(im.size)
    g = np.asarray(im.convert("L").resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS))
    orb = cv2.ORB_create(nfeatures=1000, edgeThreshold=8, patchSize=15)
    return len(orb.detect(g, None))


def is_qr(im):
    try:
        import cv2
        import numpy as np
        arr = np.asarray(im.convert("RGB"))[:, :, ::-1]
        if max(arr.shape[:2]) < 300:
            s = 300 / max(arr.shape[:2])
            arr = cv2.resize(arr, None, fx=s, fy=s, interpolation=cv2.INTER_NEAREST)
        text, pts, _ = cv2.QRCodeDetector().detectAndDecode(arr)
        return bool(text)
    except Exception:
        return False


# ---------------------------------------------------------------- stage 1: scan one report
def page_facts(doc, pno):
    """What is on a page, as plain counts (no guess about where the logo is)."""
    import pymupdf
    pg = doc[pno - 1]
    imgs = ep.image_info(pg)
    area = pg.rect.get_area()
    return {"page": pno, "images": len(imgs),
            "full_page_image": any((pymupdf.Rect(i["bbox"]) & pg.rect).get_area() / area > 0.85 for i in imgs),
            "vector_paths": len(pg.get_drawings()), "text_chars": len(pg.get_text().strip())}


def scan_report(job):
    name, pdf, pdf_sha, scan_dir, p = job
    import pymupdf
    scan_dir = Path(scan_dir)
    cache = scan_dir / f"{name}.json"
    sig = {"version": SCAN_VERSION, "pdf_sha256": pdf_sha, "pymupdf": pymupdf.VersionBind,
           "params": {k: p[k] for k in SCAN_KEYS}}
    if cache.exists():
        try:
            c = json.loads(cache.read_text(encoding="utf-8"))
            if c.get("sig") == sig and all((scan_dir / g["png"]).exists() for g in c["groups"] if g.get("png")):
                c["from_cache"] = True
                return c
        except Exception:
            pass
    t0 = time.time()
    png_dir = scan_dir / name
    if png_dir.exists():
        shutil.rmtree(png_dir)
    doc = pymupdf.open(pdf)
    n_pages = len(doc)
    end_pages = {1, n_pages}
    stats = collections.Counter()
    pls = []
    for page in doc:
        pr = page.rect
        parea = pr.get_area()
        infos = []
        for info in ep.image_info(page, keep_shading=True):
            if ep.is_shading_image(info):
                stats["shading_skipped"] += 1  # vector gradient fill, not an image
                continue
            infos.append(info)
        seen, rects, page_pls = set(), [], []
        for info in infos:
            r = pymupdf.Rect(info["bbox"]) & pr
            if r.is_empty:
                continue
            key = (info.get("xref", 0), round(r.x0), round(r.y0), round(r.x1), round(r.y1))
            if key in seen:
                continue
            seen.add(key)
            rects.append(r)
            stats["placements"] += 1
            w_px, h_px = info.get("width", 0), info.get("height", 0)
            if min(w_px, h_px) < p["min_px"]:
                stats["below_min_px"] += 1
                continue
            short = min(r.width, r.height)
            if short < p["scan_min_pt"]:
                stats["below_scan_min_pt"] += 1
                continue
            if r.get_area() / parea > p["max_area_frac"]:
                stats["too_large"] += 1
                continue
            full = pymupdf.Rect(info["bbox"])
            if r.get_area() < 0.5 * full.get_area():
                stats["mostly_off_page"] += 1  # e.g. a cover photo that runs across the spread: a thin strip shows
                continue
            near, corner = edge_flags(r, pr, p["edge_frac"])
            page_pls.append({"page": page.number + 1, "rect": r, "xref": info.get("xref", 0), "w_px": w_px,
                             "h_px": h_px, "area": r.get_area() / parea, "near": near, "corner": corner,
                             "short": short, "rect_id": len(rects) - 1})
        for pl in page_pls:
            pl["tile"] = any(abuts(pl["rect"], other, p["tile_gap_pt"])
                             for k, other in enumerate(rects) if k != pl["rect_id"])
            # partly overlaps another small image: a piece of a picture put together from several
            # images (IoU >= 0.6 is the same picture drawn in layers, which extract_pairs merges)
            pl["piece"] = False
            for other in page_pls:
                if other is pl:
                    continue
                inter = (pl["rect"] & other["rect"]).get_area()
                if (inter > 0 and inter >= 0.1 * min(pl["rect"].get_area(), other["rect"].get_area())
                        and ep.rect_iou(pl["rect"], other["rect"]) < 0.6):
                    pl["piece"] = True
                    break
        if any(pl["xref"] == 0 for pl in page_pls):
            digests = {}
            try:
                for info in page.get_image_info(hashes=True, xrefs=True):
                    if info.get("xref", 0) == 0 and info.get("digest"):
                        r = pymupdf.Rect(info["bbox"]) & pr
                        digests[(round(r.x0), round(r.y0), round(r.x1), round(r.y1))] = info["digest"].hex()
            except Exception:
                pass
            for pl in page_pls:
                if pl["xref"] == 0:
                    r = pl["rect"]
                    pl["digest"] = digests.get((round(r.x0), round(r.y0), round(r.x1), round(r.y1)), "")
        pls.extend(page_pls)
    stats["window_placements"] = len(pls)

    md5_cache = {}
    groups = collections.OrderedDict()
    for pl in pls:
        if pl["xref"]:
            m = ep.embedded_md5(doc, pl["xref"], md5_cache)
            # an xref number only means something inside one PDF: keep the report in the key
            key = f"md5:{m}" if m else f"xref:{name}:{pl['xref']}"
        elif pl.get("digest"):
            key = f"inline:{pl['digest']}:{pl['w_px']}x{pl['h_px']}"
        else:
            r = pl["rect"]
            key = f"clip:{name}:p{pl['page']}:{round(r.x0)},{round(r.y0)},{round(r.x1)},{round(r.y1)}"
        groups.setdefault(key, []).append(pl)

    words_cache = {}

    def words_on(pno):
        if pno not in words_cache:
            words_cache[pno] = [(pymupdf.Rect(w[:4]), w[4]) for w in doc[pno - 1].get_text("words")]
        return words_cache[pno]

    def text_inside(r, pno):
        for wr, txt in words_on(pno):
            if not txt.strip():
                continue
            cx, cy = (wr.x0 + wr.x1) / 2, (wr.y0 + wr.y1) / 2
            if r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 and (wr & r).get_area() >= 0.5 * wr.get_area():
                return True
        return False

    out_groups = []
    dict_cache = {}
    png_dir.mkdir(parents=True, exist_ok=True)
    for gid, (key, members) in enumerate(groups.items()):
        pages = sorted({m["page"] for m in members})
        page_pt = {}
        for m in members:
            page_pt[m["page"]] = max(page_pt.get(m["page"], 0.0), m["short"])
        potential = len(pages) >= p["min_pages"] or 1 in pages or (n_pages > 1 and n_pages in pages)
        first = min(members, key=lambda m: (m["page"], m["rect"].y0, m["rect"].x0))
        page = doc[first["page"] - 1]
        how = "embedded" if first["xref"] else "inline"
        try:
            if first["xref"]:
                im = embedded_picture(doc, first["xref"])
            else:
                im = inline_picture(page, first["rect"], first["w_px"], first["h_px"], dict_cache)
        except Exception:
            how = "page_clip"
            try:
                im = clip_picture(page, first["rect"], first["w_px"], first["h_px"], p["ref_long_side"])
            except Exception:
                stats["unreadable_groups"] += 1
                continue
        ref, feats = flatten(im, p["ref_long_side"])
        ends = [m for m in members if m["page"] in end_pages]
        g = {"gid": gid, "key": key, "xrefs": sorted({m["xref"] for m in members}), "pages": pages,
             "page_pt": [[pg, round(v, 2)] for pg, v in sorted(page_pt.items())],
             "n_placements": len(members), "potential": potential, "how": how,
             "w_px": first["w_px"], "h_px": first["h_px"],
             "w_pt": round(statistics.median(m["rect"].width for m in members), 1),
             "h_pt": round(statistics.median(m["rect"].height for m in members), 1),
             "area_frac": round(statistics.median(m["area"] for m in members), 5),
             "near_edge_share": round(sum(m["near"] for m in members) / len(members), 3),
             "corner_share": round(sum(m["corner"] for m in members) / len(members), 3),
             "tile_share": round(sum(m["tile"] for m in members) / len(members), 3),
             "piece_share": round(sum(m["piece"] for m in members) / len(members), 3),
             "placements": [[m["page"]] + [round(v, 1) for v in m["rect"]] for m in members[:MAX_PLACEMENTS_KEPT]],
             "end_placements": [[m["page"]] + [round(v, 1) for v in m["rect"]] for m in ends[:MAX_END_PLACEMENTS]],
             **feats, "qr": False, "png": None, "text_on_top_share": None, "text_on_top_n": 0}
        if potential:
            g["qr"] = is_qr(ref)
            g["png"] = f"{name}/g{gid:04d}.png"
            ref.save(scan_dir / g["png"])
            sample = ends[:MAX_END_PLACEMENTS] + [m for m in members if m["page"] not in end_pages][:TEXT_SAMPLE]
            hits = sum(1 for m in sample if text_inside(m["rect"], m["page"]))
            g["text_on_top_share"] = round(hits / len(sample), 3)
            g["text_on_top_n"] = len(sample)
        out_groups.append(g)

    if p["vector"]:
        out_groups += vector_groups(doc, name, p, stats, len(groups), scan_dir, png_dir, text_inside)

    cover = page_facts(doc, 1)
    back = page_facts(doc, n_pages) if n_pages > 1 else None
    doc.close()
    c = {"sig": sig, "report": name, "n_pages": n_pages, "stats": dict(stats), "cover": cover, "back_cover": back,
         "n_groups": len(out_groups), "n_potential": sum(1 for g in out_groups if g["potential"]),
         "groups": out_groups, "seconds": round(time.time() - t0, 1)}
    scan_dir.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache)
    c["from_cache"] = False
    return c


def vector_groups(doc, name, p, stats, gid0, scan_dir, png_dir, text_inside):
    """Logos drawn with vector paths (decision 10, 10/18): clusters of drawings (page.cluster_drawings,
    tolerance vector_tolerance pt so the letters of a word mark stay together) on the cover and the back
    cover, and in the header/footer bands (top and bottom vector_band of the page) of inner pages. A cluster
    must have >= vector_min_items paths, a short side >= scan_min_pt, a long side <= vector_max_long_frac of
    the page width and <= max_area_frac of the page area. Clusters at the same place (4-pt grid) on several
    pages form one group; groups seen on >= min_pages pages or on the cover / back cover are rendered
    (what the reader sees in that box) and go into the same candidate pipeline as embedded images."""
    import pymupdf
    n = len(doc)
    band, tol = p["vector_band"], p["vector_tolerance"]
    spots = collections.OrderedDict()
    for page in doc:
        pr = page.rect
        end = page.number in (0, n - 1)
        try:
            dr = page.get_drawings()
        except Exception:
            stats["vector_unreadable_pages"] += 1
            continue
        if not end:  # inner pages: only the header and footer bands
            dr = [d for d in dr if d["rect"].y1 <= pr.y0 + band * pr.height or d["rect"].y0 >= pr.y1 - band * pr.height]
        if not dr:
            continue
        try:
            rects = page.cluster_drawings(drawings=dr, x_tolerance=tol, y_tolerance=tol)
        except Exception:
            stats["vector_unreadable_pages"] += 1
            continue
        for r in rects:
            r = pymupdf.Rect(r) & pr
            if r.is_empty:
                continue
            short, long_ = min(r.width, r.height), max(r.width, r.height)
            if short < p["scan_min_pt"] or long_ > p["vector_max_long_frac"] * pr.width \
                    or r.get_area() / pr.get_area() > p["max_area_frac"]:
                continue
            grown = r + (-1, -1, 1, 1)
            items = sum(1 for d in dr if pymupdf.Rect(d["rect"]) in grown)
            if items < p["vector_min_items"]:
                continue
            stats["vector_clusters"] += 1
            near, corner = edge_flags(r, pr, p["edge_frac"])
            key = tuple(round(v / 4) * 4 for v in r)
            spots.setdefault(key, []).append({"page": page.number + 1, "rect": r, "items": items, "near": near,
                                              "corner": corner, "short": short, "area": r.get_area() / pr.get_area()})
    out = []
    for k, (key, members) in enumerate(spots.items()):
        pages = sorted({m["page"] for m in members})
        if not (len(pages) >= p["min_pages"] or 1 in pages or n in pages):
            continue
        first = min(members, key=lambda m: m["page"])
        r = first["rect"]
        zoom = min(8.0, max(2.0, p["vector_render_px"] / max(r.width, r.height)))
        try:
            im = clip_picture(doc[first["page"] - 1], r, round(r.width * zoom), round(r.height * zoom), p["ref_long_side"])
        except Exception:
            stats["unreadable_groups"] += 1
            continue
        ref, feats = flatten(im, p["ref_long_side"])
        gid = gid0 + k
        page_pt = {}
        for m in members:
            page_pt[m["page"]] = max(page_pt.get(m["page"], 0.0), m["short"])
        ends = [m for m in members if m["page"] in (1, n)]
        g = {"gid": gid, "key": f"vector:{name}:{','.join(str(v) for v in key)}", "xrefs": [], "pages": pages,
             "page_pt": [[pg, round(v, 2)] for pg, v in sorted(page_pt.items())],
             "n_placements": len(members), "potential": True, "how": "vector", "w_px": ref.width, "h_px": ref.height,
             "w_pt": round(statistics.median(m["rect"].width for m in members), 1),
             "h_pt": round(statistics.median(m["rect"].height for m in members), 1),
             "area_frac": round(statistics.median(m["area"] for m in members), 5),
             "near_edge_share": round(sum(m["near"] for m in members) / len(members), 3),
             "corner_share": round(sum(m["corner"] for m in members) / len(members), 3),
             "tile_share": 0.0, "piece_share": 0.0, "vector_items": first["items"],
             "placements": [[m["page"]] + [round(v, 1) for v in m["rect"]] for m in members[:MAX_PLACEMENTS_KEPT]],
             "end_placements": [[m["page"]] + [round(v, 1) for v in m["rect"]] for m in ends[:MAX_END_PLACEMENTS]],
             **feats, "qr": is_qr(ref), "png": f"{name}/v{gid:04d}.png"}
        ref.save(scan_dir / g["png"])
        sample = ends[:MAX_END_PLACEMENTS] + [m for m in members if m["page"] not in (1, n)][:TEXT_SAMPLE]
        hits = sum(1 for m in sample if text_inside(m["rect"], m["page"]))
        g["text_on_top_share"] = round(hits / len(sample), 3)
        g["text_on_top_n"] = len(sample)
        stats["vector_groups"] += 1
        out.append(g)
    return out


def scan_report_safe(job):
    """scan_report, but a PDF that cannot be read gives an error record instead of stopping the run."""
    try:
        return scan_report(job)
    except Exception as exc:  # broken PDF or image stream
        return {"report": job[0], "error": f"{type(exc).__name__}: {exc}", "from_cache": False,
                "n_potential": 0, "n_groups": 0, "groups": [], "seconds": 0}


def run_scans(jobs, workers):
    out = {}

    def say(k, c):
        if c.get("error"):
            print(f"[scan {k}/{len(jobs)}] {c['report']}: 讀不了 {c['error']}", flush=True)
        elif not c["from_cache"]:
            print(f"[scan {k}/{len(jobs)}] {c['report']}: {c['n_potential']} 組可能的候選 / {c['n_groups']} 組小圖"
                  f"（{c['seconds']} 秒）", flush=True)

    if workers > 1 and len(jobs) > 1:
        with Pool(workers) as pool:
            for k, c in enumerate(pool.imap_unordered(scan_report_safe, jobs), 1):
                out[c["report"]] = c
                say(k, c)
    else:
        for k, job in enumerate(jobs, 1):
            c = scan_report_safe(job)
            out[c["report"]] = c
            say(k, c)
    return out


# ---------------------------------------------------------------- stage 2: shared index and candidates
def informative(g):
    """A dHash only means something when the picture is not flat (flat pictures all hash alike)."""
    return g["gray_std"] >= 10 and g["dhash_ones"] >= 16


class SharedIndex:
    """Every scanned small image (all groups, not only candidates) with its firm, for 'how many
    firms use this image'."""

    def __init__(self, scans, firm_of, p):
        import numpy as np
        self.p = p
        self.by_key = collections.defaultdict(set)
        rows, firms, aspect, colour = [], [], [], []
        for name, c in scans.items():
            f = firm_of[name]
            for g in c["groups"]:
                self.by_key[g["key"]].add(f)
                if informative(g):
                    rows.append(np.frombuffer(bytes.fromhex(g["dhash"]), dtype=np.uint8))
                    firms.append(f)
                    aspect.append(g["w_pt"] / max(g["h_pt"], 1e-3))
                    colour.append(g["mean_rgb"])
        self.h = np.stack(rows) if rows else np.zeros((0, 32), np.uint8)
        self.firms = np.array(firms)
        self.aspect = np.array(aspect, dtype=np.float32)
        self.colour = np.array(colour, dtype=np.float32).reshape(-1, 3)
        self.popcount = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint16)
        self.n_reports = len(scans)
        self.n_firms = len(set(firm_of[n] for n in scans))

    def firms_with(self, g, own_firm):
        """(firms whose reports use this image, nearest look-alike distance in another firm in
        dHash bits, or None)."""
        import numpy as np
        found = set(self.by_key.get(g["key"], set()))
        nearest = 0 if found - {own_firm} else None
        if informative(g) and len(self.h):
            q = np.frombuffer(bytes.fromhex(g["dhash"]), dtype=np.uint8)
            d = self.popcount[np.bitwise_xor(self.h, q)].sum(axis=1)
            a = g["w_pt"] / max(g["h_pt"], 1e-3)
            shape_ok = ((np.abs(self.aspect - a) / a <= self.p["lookalike_aspect_tol"])
                        & (np.abs(self.colour - np.array(g["mean_rgb"], np.float32)).mean(axis=1)
                           <= self.p["lookalike_colour_tol"]))
            found |= set(self.firms[(d <= self.p["shared_dhash_bits"]) & shape_ok].tolist())
            other = shape_ok & (self.firms != own_firm)
            if other.any():
                dmin = int(d[other].min())
                nearest = dmin if nearest is None else min(nearest, dmin)
        return found, nearest


def prep_groups(reports, scans):
    out = []
    for r in reports:
        c = scans[r["name"]]
        for g in c["groups"]:
            gg = dict(g, report=r["name"], year=r["year"], n_pages=c["n_pages"])
            gg["_h"] = int(g["dhash"], 16)
            gg["_ar"] = g["w_pt"] / max(g["h_pt"], 1e-3)
            gg["_pt"] = {int(a): float(b) for a, b in g["page_pt"]}
            gg["_inf"] = informative(g)
            out.append(gg)
    return out


def same_picture(a, b, p):
    if a["key"] == b["key"]:
        return True
    if not (a["_inf"] and b["_inf"]):
        return False
    return ((a["_h"] ^ b["_h"]).bit_count() <= p["lookalike_dhash_bits"]
            and abs(a["_ar"] - b["_ar"]) / a["_ar"] <= p["lookalike_aspect_tol"]
            and sum(abs(x - y) for x, y in zip(a["mean_rgb"], b["mean_rgb"])) / 3 <= p["lookalike_colour_tol"])


def sources_at(g, floors, p):
    """Candidate sources of a group with the given drawn-size floors (PLAN §5 step 2)."""
    pt = g["_pt"]
    s = []
    if sum(1 for v in pt.values() if v >= floors["repeated"]) >= p["min_pages"]:
        s.append("repeated")
    if p["use_cover"] and pt.get(1, 0.0) >= floors["cover"]:
        s.append("cover")
    if p["use_back_cover"] and g["n_pages"] > 1 and pt.get(g["n_pages"], 0.0) >= floors["cover"]:
        s.append("back_cover")
    return s


def ramp(x, lo, hi):
    """0 at lo, 1 at hi, linear in between (lo > hi allowed for a falling ramp)."""
    if lo == hi:
        return 1.0 if x >= hi else 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def feature_scores(members, max_pages, years_present, years_available, p):
    rep = max(members, key=lambda g: (g["w_px"] * g["h_px"], -min(g["pages"])))
    n_pl = sum(g["n_placements"] for g in members)
    area = statistics.median(g["area_frac"] for g in members)
    near = sum(g["near_edge_share"] * g["n_placements"] for g in members) / n_pl
    corner = sum(g["corner_share"] * g["n_placements"] for g in members) / n_pl
    r = rep["w_pt"] / max(rep["h_pt"], 1e-3)
    if 0.25 <= r <= 8:
        aspect = 1.0
    elif r > 8:
        aspect = max(0.0, 1 - math.log2(r / 8))
    else:
        aspect = max(0.0, 1 - math.log2(0.25 / r))
    other = len(years_present) - 1
    s = {"small": ramp(area, p["max_area_frac"], 0.005),
         "edge": 0.5 * near + 0.5 * corner,
         "repeat": min(1.0, max_pages / 10),
         "cover": 1.0 if any(s in ("cover", "back_cover") for g in members for s in g["sources"]) else 0.0,
         "cross_year": other / (years_available - 1) if years_available > 1 else 0.0,
         "aspect": aspect,
         "alpha": 1.0 if any(g["transparent_share"] >= 0.05 for g in members) else 0.0,
         "flat": ramp(rep["top8_colour_share"], 0.5, 0.9)}
    w = p["weights"]
    score = sum(w[k] * s[k] for k in FEATURES) / sum(w[k] for k in FEATURES)
    return rep, {k: round(v, 3) for k, v in s.items()}, round(score, 3)


def build_items(firm_id, groups, years_available, shared, p, floors):
    """Candidates of one firm with the given floors, merged across years, scored and checked for
    exclusions. Pure: writes nothing."""
    cands = []
    for g in groups:
        if not g.get("png"):
            continue
        s = sources_at(g, floors, p)
        if s:
            cands.append(dict(g, sources=s))
    parent = list(range(len(cands)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            if same_picture(cands[i], cands[j], p):
                parent[find(i)] = find(j)
    clusters = collections.defaultdict(list)
    for i in range(len(cands)):
        clusters[find(i)].append(cands[i])

    items = []
    for members in clusters.values():
        # every use of the same picture in the firm's reports (any page, any size >= scan_min_pt)
        look = [g for g in groups if any(same_picture(g, m, p) for m in members)]
        years_present = sorted({g["year"] for g in look})
        pages_by_report = collections.defaultdict(set)
        for g in look:
            pages_by_report[g["report"]] |= set(g["_pt"])
        max_pages = max(len(v) for v in pages_by_report.values())
        rep, feats, score = feature_scores(members, max_pages, years_present, years_available, p)
        other_firms, nearest = set(), None
        for m in members:
            f, d = shared.firms_with(m, firm_id)
            other_firms |= f
            if d is not None:
                nearest = d if nearest is None else min(nearest, d)
        other_firms.discard(firm_id)
        n_text = sum(m["text_on_top_n"] for m in members)
        text_share = (sum((m["text_on_top_share"] or 0) * m["text_on_top_n"] for m in members) / n_text
                      if n_text else 0.0)
        exclude = []
        if len(other_firms) + 1 >= p["shared_min_firms"]:
            exclude.append("shared")
        n_pl = sum(m["n_placements"] for m in members)
        tile_share = sum(m["tile_share"] * m["n_placements"] for m in members) / n_pl
        piece_share = sum(m["piece_share"] * m["n_placements"] for m in members) / n_pl
        if tile_share >= 0.5 or piece_share >= 0.5:
            exclude.append("tile")
        if text_share >= p["text_on_top_share"]:
            exclude.append("background")
        if any(m["qr"] for m in members):
            exclude.append("qr")
        if rep["contrast"] < p["min_contrast"]:
            exclude.append("blank")
        elif rep["keypoints"] < p["min_keypoints"]:
            exclude.append("too_simple")
        items.append({
            "sources": sorted({s for m in members for s in m["sources"]}, key=list(SOURCE_LABEL).index),
            "years": years_present,
            "candidate_in": [{"report": m["report"], "pages": m["pages"], "sources": m["sources"],
                              "n_placements": m["n_placements"], "key": m["key"], "how": m["how"],
                              "placements": m["placements"][:10], "end_placements": m["end_placements"]}
                             for m in sorted(members, key=lambda m: (m["report"], m["gid"]))],
            "keys": sorted({m["key"] for m in members}), "ref_key": rep["key"],
            "rep_report": rep["report"], "rep_png_scan": rep["png"], "how": rep["how"],
            "w_px": rep["w_px"], "h_px": rep["h_px"], "w_pt": rep["w_pt"], "h_pt": rep["h_pt"],
            "ref_w": rep["ref_w"], "ref_h": rep["ref_h"], "bg": rep["bg"],
            "area_frac": rep["area_frac"], "top8_colour_share": rep["top8_colour_share"],
            "contrast": rep["contrast"], "keypoints": rep["keypoints"],
            "transparent_share": rep["transparent_share"], "text_on_top_share": round(text_share, 3),
            "tile_share": round(tile_share, 3), "piece_share": round(piece_share, 3),
            "max_pages_in_a_report": max_pages,
            "pages_by_report": {k: len(v) for k, v in sorted(pages_by_report.items())},
            "other_firms": len(other_firms), "other_firm_ids": sorted(other_firms),
            "nearest_other_firm_bits": nearest,
            "cross_year_count": len(years_present) - 1,
            "features": feats, "score": score, "exclude": exclude,
            "ocr": None, "name_match": None, "gate": None, "status": None, "proposed": False, "confirmed": None,
        })
    items.sort(key=lambda d: (-d["score"], d["rep_report"], d["ref_key"]))
    return items


def select(items, p):
    """Auto proposal: not excluded, at least one gate, score >= select_min_score, top N."""
    n = 0
    for d in items:
        gate = {"repeat": d["max_pages_in_a_report"] >= p["min_pages"], "cross_year": d["cross_year_count"] >= 1,
                "name": bool(d["name_match"])}
        d["gate"] = gate
        if d["exclude"]:
            st = "excluded"
        elif not any(gate.values()):
            st = "no_gate"
        elif d["score"] < p["select_min_score"]:
            st = "low_score"
        elif n >= p["max_refs_per_firm"]:
            st = "quota"
        else:
            st = "proposed"
            n += 1
        d["status"] = st
        d["proposed"] = st == "proposed"


# ---------------------------------------------------------------- stage 3: OCR and firm names
def norm_name(s):
    s = unicodedata.normalize("NFKC", s or "").replace("臺", "台").upper()
    return "".join(ch for ch in s if ch.isalnum())


EN_TAIL = re.compile(r"[\s,.]*(CO\.?,?\s*LTD\.?|CORPORATION|CORP\.?|INC\.?|LTD\.?|LIMITED|COMPANY|CO\.?)\s*$", re.I)


def load_names(catalog, index_dir):
    """firm_id -> [(kind, normalised name, name as written)] from the catalog, reports_index.csv
    and the ESG InfoHub company list (CLAUDE.md: 定錨字串)."""
    want = {r["firm_id"] for r in catalog}
    names = collections.defaultdict(list)
    sources = []

    def add(fid, kind, s):
        n = norm_name(s)
        if fid in want and len(n) >= 2 and all(x[1] != n for x in names[fid]):
            names[fid].append((kind, n, s.strip()))

    for r in catalog:
        add(r["firm_id"], "zh_short", r["firm_name"])
        add(r["firm_id"], "zh_short", re.sub(r"[-\s]*KY$", "", r["firm_name"].strip(), flags=re.I))
    ri = Path(index_dir) / "reports_index.csv"
    if ri.is_file():
        sources.append(str(ri))
        with open(ri, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                full = (r.get("firm_full_name") or "").strip()
                if full:
                    add(r["firm_id"].strip(), "zh_full", re.sub(r"(股份有限公司|有限公司|公司)$", "", full))
    cl = Path(index_dir) / "raw" / "company_lists.json"
    if cl.is_file():
        sources.append(str(cl))
        d = json.loads(cl.read_text(encoding="utf-8"))
        for market in d.values():
            for c in market.get("companyNameList", []):
                fid = str(c.get("companyCode", "")).strip()
                zh = re.sub(r"^\s*\d+\s*", "", c.get("companyName") or "")
                add(fid, "zh_short", zh)
                m = re.match(r"^\s*\d+\s*-\s*(.*?)\s*(?:\(([^()]*)\))?\s*$", c.get("companyEName") or "")
                if m:
                    full = m.group(1)
                    for _ in range(3):
                        full = EN_TAIL.sub("", full)
                    add(fid, "en_full", full)
                    if m.group(2):
                        add(fid, "en_short", m.group(2))
    return names, sources


def name_match(ocr, names, p):
    """The firm name the OCR text matches, or None. Short English names (2-3 letters) must be a
    whole OCR word; other names must be contained in the text; the full Chinese name may also
    match by character-bigram overlap >= name_bigram_min."""
    if not ocr:
        return None
    lines = [t for t, s in zip(ocr["texts"], ocr["scores"]) if s >= p["ocr_min_score"] and t.strip()]
    if not lines:
        return None
    text = norm_name("".join(lines))
    # 10/22 (NEXT C11): with short_name_split_punct, a short English name also counts when it is a token bounded by
    # punctuation ("NHOA.TCC" -> TCC); the default keeps the original word split
    split = r"[\s/|,·•.:;()\[\]【】（）、｜]+" if p.get("short_name_split_punct") else r"[\s/|,·•]+"
    tokens = {norm_name(x) for ln in lines for x in re.split(split, ln)} | {norm_name(ln) for ln in lines}
    for kind, n, shown in names:
        if kind == "en_short" and len(n) < 4:
            if n in tokens:
                return {"kind": kind, "name": shown, "how": "whole word"}
            continue
        if kind == "en_full" and len(n) < 5:
            continue
        if n in text:
            return {"kind": kind, "name": shown, "how": "contained"}
    for kind, n, shown in names:
        if kind == "zh_full" and len(n) >= 3:
            bigrams = {n[i:i + 2] for i in range(len(n) - 1)}
            cover = sum(1 for b in bigrams if b in text) / len(bigrams)
            if cover >= p["name_bigram_min"]:
                return {"kind": kind, "name": shown, "how": f"bigram {cover:.2f}"}
    return None


def run_ocr(items, p, ocr_dir):
    """OCR of each item's picture with PaddleOCR in .venv_ocr; results cached per picture."""
    from PIL import Image, ImageOps
    t0 = time.time()
    info = {"enabled": bool(p["ocr"]), "prep": OCR_PREP, "lang": p["ocr_lang"], "min_score": p["ocr_min_score"]}
    if not p["ocr"] or not items:
        info.update(ok=True, n_pictures=0, new=0)
        return info
    ocr_dir.mkdir(parents=True, exist_ok=True)
    todo = collections.OrderedDict()
    for d in items:
        src = Path(p["_scan_dir"]) / d["rep_png_scan"]
        sha = hashlib.sha1(src.read_bytes() + f"|{OCR_PREP}|{p['ocr_lang']}".encode()).hexdigest()
        d["_ocr_sha"] = sha
        if not (ocr_dir / f"{sha}.json").exists():
            todo.setdefault(sha, src)
    info["n_pictures"] = len({d["_ocr_sha"] for d in items})
    info["new"] = len(todo)
    if todo:
        py = ROOT / ".venv_ocr" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not py.is_file():
            info.update(ok=False, error="沒有 .venv_ocr")
            return info
        in_dir = ocr_dir / "_in"
        if in_dir.exists():
            shutil.rmtree(in_dir)
        in_dir.mkdir(parents=True)
        jobs = []
        for sha, src in todo.items():
            im = Image.open(src).convert("RGB")
            s = 320 / max(im.size)
            if s > 1:
                im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
            im = ImageOps.expand(im, border=16, fill=(255, 255, 255))
            q = in_dir / f"{sha}.png"
            im.save(q)
            jobs.append({"id": sha, "path": str(q)})
        (in_dir / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
        out_path = in_dir / "out.jsonl"
        env = dict(os.environ, PADDLE_PDX_CACHE_HOME=str(ROOT / "work" / "models" / "paddlex"))
        print(f"[ocr] {len(jobs)} 張圖（PaddleOCR，.venv_ocr）", flush=True)
        proc = subprocess.run([str(py), "-I", "-c", OCR_CODE, str(in_dir / "jobs.json"), str(out_path), p["ocr_lang"]],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=7200)
        model, got = None, 0
        models = sorted(set(re.findall(r"Creating model: \('([^']+)'", (proc.stderr or "") + (proc.stdout or ""))))
        if out_path.exists():
            for line in out_path.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                if "_model" in rec:
                    model = dict(rec["_model"], models=models)
                    continue
                (ocr_dir / f"{rec['id']}.json").write_text(json.dumps(
                    {"texts": rec["texts"], "scores": rec["scores"], "model": model, "prep": OCR_PREP},
                    ensure_ascii=False), encoding="utf-8")
                got += 1
        if got < len(jobs):
            info.update(ok=False, error=f"OCR 只完成 {got}/{len(jobs)} 張：{(proc.stderr or proc.stdout or '')[-800:]}")
        shutil.rmtree(in_dir, ignore_errors=True)
    models = {}
    for d in items:
        f = ocr_dir / f"{d['_ocr_sha']}.json"
        if f.exists():
            rec = json.loads(f.read_text(encoding="utf-8"))
            d["ocr"] = {"texts": rec["texts"], "scores": rec["scores"]}
            if rec.get("model"):
                models[json.dumps(rec["model"], sort_keys=True)] = rec["model"]
    info.setdefault("ok", True)
    info["models"] = list(models.values())
    info["seconds"] = round(time.time() - t0, 1)
    return info


# ---------------------------------------------------------------- stage 4: Eden's review
def load_review(pattern):
    """annotations/logo_refs/review*.csv, in name order. A firm counts as reviewed only when its
    'firm' row says seen=1; a later file replaces an earlier review of the same firm."""
    pat = Path(pattern)
    files = sorted(pat.parent.glob(pat.name) if pat.is_absolute() else ROOT.glob(pattern))

    def shown(f):
        try:
            return str(f.relative_to(ROOT)).replace("\\", "/")
        except ValueError:
            return str(f)

    firms = {}
    for f in files:
        with open(f, encoding="utf-8-sig", newline="") as fh:
            rows = [{k: (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(fh)]
        for fr in rows:
            if fr.get("row_type") != "firm" or fr.get("seen") != "1":
                continue
            fid = fr["firm_id"]
            refs = [{"keys": set(r.get("keys", "").split()), "is_logo": r.get("is_logo") == "1", "cand_id": r.get("cand_id")}
                    for r in rows if r.get("firm_id") == fid and r.get("row_type") == "ref"]
            firms[fid] = {"file": shown(f), "logo_form": fr.get("logo_form", ""),
                          "note": fr.get("note", ""), "exported_at": fr.get("exported_at", ""),
                          "reviewed_result_file": fr.get("result_file", ""), "refs": refs}
    return [shown(f) for f in files], firms


def apply_review(items, rv):
    """confirmed = True when Eden ticked a tile holding this picture, False when Eden left it
    unticked, None when the firm or the picture was not reviewed."""
    used = set()
    for d in items:
        d["confirmed"] = None
        if not rv:
            continue
        ks = set(d["keys"])
        yes = [k for k, r in enumerate(rv["refs"]) if r["is_logo"] and ks & r["keys"]]
        no = [k for k, r in enumerate(rv["refs"]) if not r["is_logo"] and ks & r["keys"]]
        used |= set(yes) | set(no)
        d["confirmed"] = True if yes else (False if no else None)
    return used


# ---------------------------------------------------------------- reasons
def page_facts_text(f, label):
    if not f:
        return ""
    return (f"{label}（第 {f['page']} 頁）有 {f['images']} 張圖{'，其中一張是整頁大圖' if f['full_page_image'] else ''}、"
            f"{f['vector_paths']} 個向量路徑、{f['text_chars']} 個字")


def report_reason(c, n_cands, p):
    """Why a report gave no candidate: only what was measured (no guess about the logo's form)."""
    if n_cands:
        return "", ""
    facts = "；".join(x for x in (page_facts_text(c["cover"], "封面"), page_facts_text(c.get("back_cover"), "封底")) if x)
    return "no_candidate_in_window", (
        f"這本沒有候選：沒有在 {p['min_pages']} 頁以上出現、短邊 ≥ {p['min_short_side_pt']} pt 的小圖，"
        f"封面和封底也沒有短邊 ≥ {p['cover_min_short_side_pt']} pt 的小圖。{facts}")


def firm_reason(items, reps, p):
    if not reps:
        return "no_usable_report", "這家公司的報告都沒有文字層"
    if not items:
        return "no_candidate_in_window", (f"三年都沒有候選：沒有在 {p['min_pages']} 頁以上出現、短邊 ≥ "
                                          f"{p['min_short_side_pt']} pt 的小圖，封面和封底也沒有短邊 ≥ "
                                          f"{p['cover_min_short_side_pt']} pt 的小圖")
    if any(d["proposed"] for d in items):
        return "", ""
    live = [d for d in items if not d["exclude"]]
    if not live:
        return "only_excluded", "候選全部被排除（共用圖示、碎片、底圖、QR code、空白圖、特徵點太少）"
    if any(d["status"] == "low_score" for d in live):
        return "low_score", f"有符合提議條件的候選，但 logo 分數都低於 {p['select_min_score']}"
    return "only_single_year_cover", "沒被排除的候選都只在某一年的封面或封底出現，OCR 讀到的字也對不上公司名稱"


# ---------------------------------------------------------------- HTML
CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#666;--line:#ddd;--ok:#137333;--bad:#b3261e;--warn:#a15c00;--card:#f4f4f5;--tile:#fff;--acc:#1a5fb4}
@media (prefers-color-scheme: dark){:root{--bg:#161616;--fg:#eee;--mut:#aaa;--line:#3a3a3a;--ok:#6dd58c;--bad:#ff8a80;--warn:#ffb74d;--card:#232323;--tile:#fff;--acc:#8ab4f8}}
body{background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;margin:0 auto;max-width:1240px;padding:16px}
a{color:var(--acc)}
h1{font-size:24px;margin:8px 0} h2{font-size:19px;margin:24px 0 8px} h3{font-size:16px;margin:14px 0 6px}
table{border-collapse:collapse;margin:8px 0 18px;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:5px 9px;text-align:left;vertical-align:top}
.wrap{overflow-x:auto} .mut{color:var(--mut)} .ok{color:var(--ok);font-weight:600} .bad{color:var(--bad);font-weight:600} .warn{color:var(--warn);font-weight:600}
.note{background:var(--card);border-radius:8px;padding:10px 14px;margin:10px 0}
.covers{display:flex;flex-wrap:wrap;gap:12px} .covers figure{margin:0;max-width:100%} .covers img{max-width:100%;border:1px solid var(--line);display:block}
figcaption{font-size:13px;color:var(--mut);max-width:300px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.card{background:var(--card);border-radius:10px;padding:10px;border:2px solid transparent}
.card.sel{border-color:var(--ok)} .card.exc{opacity:.78}
.logo{background:var(--tile);border-radius:6px;display:flex;align-items:center;justify-content:center;height:150px;padding:6px}
.logo img{max-width:100%;max-height:138px}
.card p{margin:4px 0;font-size:13px;line-height:1.45;word-break:break-word}
.where img{max-width:100%;border:1px solid var(--line);margin-top:4px}
.feat{display:grid;grid-template-columns:auto 1fr auto;gap:1px 6px;font-size:12px;align-items:center;margin-top:4px}
.bar{height:8px;border-radius:4px;background:var(--line);overflow:hidden} .bar span{display:block;height:100%;background:var(--ok)}
.tag{display:inline-block;border-radius:4px;padding:0 6px;font-size:12px;font-weight:600}
.tag.sel{background:var(--ok);color:var(--bg)} .tag.no{background:var(--line);color:var(--fg)} .tag.exc{background:var(--bad);color:var(--bg)}
.tag.yes{background:var(--acc);color:var(--bg)}
code{font-size:13px}
details{margin:8px 0} summary{cursor:pointer;font-weight:600}
"""
REVIEW_CSS = """
.bar-top{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 0;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
button{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
button.main{background:var(--acc);color:var(--bg);border-color:var(--acc);font-weight:600}
.firm{border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:16px 0}
.firm.done{border-color:var(--ok)}
.firm h2{margin:4px 0 8px}
.mini{display:flex;flex-wrap:wrap;gap:6px} .mini figure{margin:0} .mini img{height:150px;border:1px solid var(--line);display:block}
.mini figcaption{font-size:11px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px;margin:8px 0}
.tile{background:var(--card);border-radius:8px;padding:6px;border:2px solid transparent;display:block;cursor:pointer}
.tile.on{border-color:var(--ok)}
.tile .pic{background:var(--tile);border-radius:5px;height:110px;display:flex;align-items:center;justify-content:center}
.tile .pic img{max-width:100%;max-height:104px}
.tile p{margin:3px 0;font-size:12px;line-height:1.4;word-break:break-word}
.tile input{transform:scale(1.25);margin-right:6px}
.ctl{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-top:6px}
select,input[type=text]{font:inherit;padding:4px 6px;border-radius:5px;border:1px solid var(--line);background:var(--bg);color:var(--fg)}
textarea{width:100%;height:120px;font:12px monospace;background:var(--card);color:var(--fg);border:1px solid var(--line)}
"""


def data_uri(im, quality=82, max_side=None):
    from PIL import Image
    im = im.convert("RGB")
    if max_side and max(im.size) > max_side:
        im = im.copy()
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def page_thumb(doc, pno, boxes, width):
    """Page pno (1-based) rendered `width` px wide, with boxes [(rect, colour, label, thick)] drawn."""
    import pymupdf
    from PIL import Image, ImageDraw
    page = doc[pno - 1]
    zoom = width / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    d = ImageDraw.Draw(im)
    for rect, colour, label, thick in boxes:
        x0, y0, x1, y1 = [v * zoom for v in rect]
        for k in range(thick):
            d.rectangle([x0 - k, y0 - k, x1 + k, y1 + k], outline=colour)
        if label:
            d.rectangle([x0, max(0, y0 - 13), x0 + 7 * len(label) + 4, max(13, y0)], fill=colour)
            d.text((x0 + 2, max(0, y0 - 13)), label, fill=(255, 255, 255))
    return im


COL_SEL, COL_NO, COL_EXC = (19, 140, 60), (230, 120, 0), (140, 140, 140)


def item_colour(d):
    return COL_SEL if d["proposed"] else (COL_EXC if d["exclude"] else COL_NO)


def pages_text(pages, n=12):
    s = "、".join(str(x) for x in pages[:n])
    return s + (f"…（共 {len(pages)} 頁）" if len(pages) > n else "")


def gate_text(d, p):
    if not d.get("gate"):
        return ""
    g = d["gate"]
    nm = d.get("name_match")
    return ("提議條件：" + "｜".join([
        f"多頁 {'✔' if g['repeat'] else '✘'}（一本最多 {d['max_pages_in_a_report']} 頁）",
        f"跨年 {'✔' if g['cross_year'] else '✘'}",
        f"名稱 {'✔（' + nm['name'] + '）' if nm else '✘'}"]))


def ocr_text(d):
    if d.get("ocr") is None:
        return "OCR：沒跑（被排除的不跑）" if d["exclude"] else "OCR：沒有結果"
    t = [x for x in d["ocr"]["texts"] if x.strip()]
    return "OCR：" + ("、".join(f"「{x}」" for x in t[:6]) if t else "沒讀到字")


def status_tag(d, e, whatif=None):
    if d["exclude"]:
        tag = f"<span class='tag exc'>排除：{e('、'.join(EXCLUDE_LABEL[x] for x in d['exclude']))}</span>"
    elif d["proposed"] and whatif:
        tag = f"<span class='tag sel'>門檻降到 {whatif:g} pt 時會被自動提議</span>"
    elif d["proposed"]:
        tag = "<span class='tag sel'>自動提議（還沒確認）</span>"
    else:
        tag = f"<span class='tag no'>{e(STATUS_LABEL[d['status']])}</span>"
    if d.get("confirmed") is True:
        tag += " <span class='tag yes'>Eden：是 logo</span>"
    elif d.get("confirmed") is False:
        tag += " <span class='tag no'>Eden：不是</span>"
    return tag


def firm_html_job(job):
    """Writes reports/logo_refs/{firm}.html; returns the pictures the review page needs."""
    import pymupdf
    from PIL import Image
    (path, firm, items, wnew, reports, rinfo, p, meta, shared_info, rv, result_file, skipped) = job
    e = html.escape
    docs = {}

    def doc_of(rep):
        if rep not in docs:
            docs[rep] = pymupdf.open(next(r["file_path"] for r in reports if r["name"] == rep))
        return docs[rep]

    covers, mini = [], []
    for r in reports:
        ri = rinfo[r["name"]]
        n = ri["n_pages"]
        for pno, label in [(1, "封面")] + ([(n, "封底")] if n > 1 else []):
            boxes = []
            for d in items:
                for ci in d["candidate_in"]:
                    if ci["report"] == r["name"]:
                        boxes += [(pl[1:], item_colour(d), d["id"][5:], 3 if d["proposed"] else 2)
                                  for pl in ci["end_placements"] if pl[0] == pno]
            im = page_thumb(doc_of(r["name"]), pno, boxes, 300)
            if pno == 1:
                bs = ri["by_source"]
                cap = (f"{e(r['name'])} 封面（第 1 頁）。這本報告 {ri['candidates']} 個候選"
                       f"（封面 {bs.get('cover', 0)}、封底 {bs.get('back_cover', 0)}、重複小圖 {bs.get('repeated', 0)}）"
                       + (f"；{e(ri['reason'])}" if ri["reason"] else ""))
            else:
                cap = f"{e(r['name'])} 封底（第 {n} 頁）"
            covers.append(f"<figure><img src='{data_uri(im, 76)}' alt=''><figcaption>{cap}</figcaption></figure>")
            small = im.copy()
            small.thumbnail((200, 150), Image.LANCZOS)
            mini.append((f"{r['name']} {label}", data_uri(small, 72)))

    def pic(d):
        return Image.open(Path(p["_scan_dir"]) / d["rep_png_scan"])

    def card(d, whatif=None):
        logo_uri = data_uri(pic(d), 85, 260)
        where = d["candidate_in"][0]
        pl = where["placements"][0]
        wim = page_thumb(doc_of(where["report"]), pl[0], [(pl[1:], item_colour(d), "", 3)], 250)
        src = "、".join(SOURCE_LABEL[s] for s in d["sources"])
        where_lines = "".join(f"<br>{e(ci['report'])}：第 {e(pages_text(ci['pages']))} 頁" for ci in d["candidate_in"])
        feats = "".join(
            f"<span>{FEATURE_LABEL[k]}</span><span class='bar'><span style='width:{int(100 * d['features'][k])}%'></span></span>"
            f"<span>{d['features'][k]:.2f}</span>" for k in FEATURES)
        other = (f"<span class='bad'>{d['other_firms']} 家其他公司也有</span>" if d["other_firms"] else "其他公司都沒有")
        near = ("" if d["nearest_other_firm_bits"] is None else f"（別家最像的差 {d['nearest_other_firm_bits']} 位元）")
        how = {"embedded": "內嵌圖檔", "inline": "內嵌圖檔（inline）", "page_clip": "抽不出圖檔，改截頁面",
               "vector": "向量圖（線條畫的，從頁面截出來）"}[d["how"]]
        return f"""<div class="card {'sel' if d['proposed'] else ('exc' if d['exclude'] else '')}">
<p><b>#{e(d['id'].split('_')[1])}</b>　{status_tag(d, e, whatif)}　<b>分數 {d['score']:.2f}</b></p>
<div class="logo"><img src="{logo_uri}" alt=""></div>
<p>來源：{e(src)}{where_lines}</p>
<p>出現在 {len(d['years'])} 個年度（{e('、'.join(d['years']))}）｜{other}{e(near)}</p>
<p>{e(gate_text(d, p))}</p>
<p>{e(ocr_text(d))}</p>
<p>大小：圖檔 {d['w_px']}×{d['h_px']} 像素；頁面上 {d['w_pt']:.0f}×{d['h_pt']:.0f} pt（占頁面 {100 * d['area_frac']:.2f}%）｜{how}{'｜白色 logo，底色改深灰' if d['bg'] == 'dark' else ''}</p>
<p class="mut">特徵點 {d['keypoints']}｜對比 {d['contrast']:.0f}｜前 8 色占 {100 * d['top8_colour_share']:.0f}%｜上面疊字 {100 * d['text_on_top_share']:.0f}%</p>
<div class="feat">{feats}</div>
<div class="where"><img src="{data_uri(wim, 75)}" alt=""><p class="mut">{e(where['report'])} 第 {pl[0]} 頁的位置</p></div>
</div>"""

    kept = [d for d in items if not d["exclude"]]
    excluded = [d for d in items if d["exclude"]]
    shown_exc = excluded[:MAX_EXCLUDED_CARDS]
    w_kept = [d for d in wnew if not d["exclude"]]
    cards_kept = [card(d) for d in kept]
    cards_exc = [card(d) for d in shown_exc]
    cards_w = [card(d, p["whatif_min_short_side_pt"]) for d in w_kept]
    tiles = {d["id"]: data_uri(pic(d), 82, 220) for d in kept + shown_exc + w_kept}
    for doc in docs.values():
        doc.close()
    exc_counts = collections.Counter(x for d in excluded for x in d["exclude"])
    exc_text = "、".join(f"{EXCLUDE_LABEL[k]} {exc_counts[k]}" for k in EXCLUDE_ORDER if exc_counts[k])

    used = [r["name"] for r in reports]
    w = p["weights"]
    wtxt = "、".join(f"{FEATURE_LABEL[k]}×{w[k]:g}" for k in FEATURES)
    miss = "".join(f"<li>{e(s)}：沒有文字層的報告，整本跳過</li>" for s in skipped)
    n_prop = sum(d["proposed"] for d in items)
    if rv:
        n_conf = sum(1 for d in items if d["confirmed"] is True)
        review = (f"<span class='ok'>Eden 已確認（{e(rv['file'])}）：{n_conf} 張是 logo</span>；logo 形式："
                  f"{e(FORM_LABEL.get(rv['logo_form'], rv['logo_form'] or '沒填'))}")
    else:
        review = "<span class='warn'>還沒確認</span>（到確認頁勾選；確認前這家公司沒有可用的參考圖）"
    w_prop = sum(1 for d in wnew if d["proposed"])
    Path(path).write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>logo 參考圖 {e(firm['firm_id'])} {e(firm['firm_name'])}</title>
<style>{CSS}</style></head><body>
<h1>{e(firm['firm_id'])} {e(firm['firm_name'])}：logo 參考圖</h1>
<p class="mut">{e(meta['date'])}｜git {e(str(meta['git'].get('commit')))}{'（有未 commit 的修改）' if meta['git'].get('uncommitted_changes') else ''}｜PyMuPDF {e(str(meta['packages'].get('pymupdf')))}｜結果檔 <code>{e(result_file)}</code>｜<a href="index.html">總覽</a></p>
<div class="note"><b>結果：</b>{len(used)} 本報告（{e('、'.join(used))}），{len(items)} 張候選，其中 {len(excluded)} 張排除，
<b>自動提議 {n_prop} 張</b>（程式挑的，還沒人看過，不能直接當參考圖）。<b>確認：</b>{review}。
<br><b>這是什麼：</b>之後要拿這些圖去照片裡找這家公司的 logo（SuperPoint＋LightGlue 比對特徵點，再用單應矩陣確認形狀對得上）。所以只收報告裡<b>真的內嵌的圖檔</b>；
用線條畫的向量 logo、畫在整頁大圖裡的 logo 抽不出來，先跳過（PLAN §5 步驟 2）。PDF 的向量漸層不是圖片，已排除。
<br><b>候選怎麼來：</b>①同一本報告裡出現在 {p['min_pages']} 頁以上、頁面上短邊 ≥ {p['min_short_side_pt']} pt 的小圖（10/18 決定 5 改成這個門檻）；
②封面（第 1 頁）、③封底（最後一頁）上短邊 ≥ {p['cover_min_short_side_pt']} pt 的小圖。
小圖包括內嵌的圖檔，也包括用線條畫的向量圖（10/18 決定 10）：內頁只看頁首、頁尾，封面封底全看；向量圖從頁面截出來，卡片上會寫「向量圖」。
「小圖」= 圖檔短邊 ≥ {p['min_px']} 像素、面積不超過頁面 {100 * p['max_area_frac']:.0f}%。
<br><b>分數：</b>八個 0～1 的特徵加權平均（{e(wtxt)}）。小＝占頁面越小越高；靠邊＝靠近頁緣或在角落；多頁＝一本裡出現的頁數÷10；
封面封底＝出現在封面或封底；跨年＝同一張圖在這家公司其他年度也用到；長寬＝長寬比在 1:4 到 8:1 之間；透明底＝圖檔有透明背景；色少＝顏色集中在少數幾種。
<br><b>自動提議的條件：</b>沒被排除、分數 ≥ {p['select_min_score']}（初值）、最多 {p['max_refs_per_firm']} 張，而且下面三項至少一項：
在同一本出現 {p['min_pages']} 頁以上（任何大小）；在別的年度也用到；OCR 讀到的字對得上公司名稱（簡稱、全名、英文名）。
只在某一年封面或封底出現的圖，10/18 開發集檢查 15 張錯 14 張，所以不自動提議。
<br><b>一定不選：</b>共用圖示（同一張圖出現在 {p['shared_min_firms']} 家以上公司的報告；比對範圍：{shared_info['n_reports']} 本報告、{shared_info['n_firms']} 家公司）、
碎片（大圖切成的，或跟別的小圖部分重疊、拼成一張圖的小塊）、底圖（上面疊了文字）、QR code、空白或很淡的圖、特徵點不到 {p['min_keypoints']} 個的圖。
<br>封面、封底縮圖上：綠框＝自動提議，橘框＝候選沒提議，灰框＝排除。框上的數字是下面卡片的編號。</div>
{('<ul>' + miss + '</ul>') if miss else ''}
<h2>封面與封底</h2><div class="covers">{''.join(covers)}</div>
<h2>候選（沒被排除的 {len(kept)} 張，分數由高到低）</h2>
{'<div class="grid">' + ''.join(cards_kept) + '</div>' if cards_kept else '<p class="bad">沒有。</p>'}
<h2>排除的候選（{len(excluded)} 張）</h2>
<p class="mut">{e(exc_text) if exc_text else '沒有'}{f'。只列分數最高的 {len(shown_exc)} 張。' if len(excluded) > len(shown_exc) else ''}</p>
{'<details><summary>展開</summary><div class="grid">' + ''.join(cards_exc) + '</div></details>' if cards_exc else ''}
<h2>如果重複小圖的門檻從 {p['min_short_side_pt']} pt 降到 {p['whatif_min_short_side_pt']} pt</h2>
<p>會多出 {len(wnew)} 張候選（{len(w_kept)} 張沒被排除，其中 {w_prop} 張會被自動提議）。這只是給 Eden 決定門檻用，不影響上面的結果。</p>
{'<details><summary>展開</summary><div class="grid">' + ''.join(cards_w) + '</div></details>' if cards_w else ''}
</body></html>""", encoding="utf-8")
    return {"firm_id": firm["firm_id"], "mini": mini, "tiles": tiles}


def write_index_html(path, result, review_name):
    e = html.escape
    names = [review_name] if isinstance(review_name, str) else list(review_name)   # 10/22: several pages for 120 firms
    review_links = "、".join(f'<a href="{e(n)}">確認頁{" 第 %d 頁" % (k + 1) if len(names) > 1 else ""}</a>'
                             for k, n in enumerate(names))
    rows = []
    for fid, f in result["per_firm"].items():
        rv = f["review"]
        conf = (f"<b>{rv['confirmed']}</b>" if rv["status"] == "reviewed" else "<span class='mut'>還沒確認</span>")
        form = e(FORM_LABEL.get(rv.get("logo_form") or "", rv.get("logo_form") or "")) if rv["status"] == "reviewed" else ""
        rows.append(f"<tr><td><a href='{e(fid)}.html'>{e(fid)} {e(f['firm_name'])}</a></td><td>{len(f['reports_used'])}</td>"
                    f"<td>{len(f['reports_skipped'])}</td><td>{f['candidates']}</td><td>{f['excluded']}</td>"
                    f"<td>{f['auto_proposed']}</td><td>{e(f['auto_reason'])}</td><td>{conf}</td><td>{form}</td>"
                    f"<td>{f['whatif']['new_candidates']}／{f['whatif']['new_proposed']}</td></tr>")
    t = result["totals"]
    rv = t["review"]
    cov = (f"<b>{rv['firms_with_confirmed_ref']} / {t['firms']}</b>（{100 * rv['coverage_confirmed']:.0f}%）"
           if rv["coverage_confirmed"] is not None else
           f"<span class='warn'>還不能算</span>（{rv['firms_reviewed']} / {t['firms']} 家確認過；全部確認完才算）")
    Path(path).write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>logo 參考圖總覽</title><style>{CSS}</style></head><body>
<h1>logo 參考圖：{t['firms']} 家公司</h1>
<div class="note">涵蓋率（有 Eden 確認過的參考圖的公司）：{cov}。
<br>程式自動提議：{t['auto_proposed']} 張，分布在 {t['firms_with_auto_proposal']} 家（還沒人看過，不是涵蓋率）。
<br><b>請到 {review_links} 勾選</b>，匯出的 CSV 放到 <code>annotations/logo_refs/</code>{"（每頁各匯出一份）" if len(names) > 1 else ""}。
<br>跳過沒有文字層的報告 {t['reports_skipped']} 本。結果檔：<code>{e(result['result_file'])}</code></div>
<div class="wrap"><table><tr><th>公司</th><th>用到的報告</th><th>跳過的報告</th><th>候選</th><th>排除</th><th>自動提議</th>
<th>沒提議的原因</th><th>確認的參考圖</th><th>logo 形式</th><th>降到 {result['params']['whatif_min_short_side_pt']} pt 多出的候選／提議</th></tr>{''.join(rows)}</table></div>
</body></html>""", encoding="utf-8")


def write_review_html(path, tag, result, bundles, frags, p, split_cmd="python src/logo_refs.py --split dev", pages=None):
    """One page for Eden: tick the real logos of each firm, pick the logo form, export a CSV."""
    e = html.escape
    secs = []
    for fid, B in bundles.items():
        firm, items, wnew, rv = B["firm"], B["items"], B["wnew"], B["review"]
        fr = frags.get(fid)
        if not fr:
            continue
        mini = "".join(f"<figure><img src='{u}' alt='' loading='lazy'><figcaption>{e(c)}</figcaption></figure>"
                       for c, u in fr["mini"])

        def tile(d, kind):
            if d["id"] not in fr["tiles"]:
                return ""
            on = (d["confirmed"] is True) if d["confirmed"] is not None else (d["proposed"] and kind == "cand")
            years = "、".join(d["years"])
            src = "、".join(SOURCE_LABEL[s] for s in d["sources"]) + ("（向量圖）" if d["how"] == "vector" else "")
            if d["exclude"]:
                st = "排除：" + "、".join(EXCLUDE_LABEL[x] for x in d["exclude"])
            elif kind == "whatif":
                st = f"降到 {p['whatif_min_short_side_pt']:g} pt 才出現" + ("，會被自動提議" if d["proposed"] else "")
            else:
                st = "自動提議" if d["proposed"] else STATUS_LABEL[d["status"]]
            t = [x for x in (d["ocr"] or {}).get("texts", []) if x.strip()]
            return (f"<label class='tile{' on' if on else ''}'><div class='pic'><img src='{fr['tiles'][d['id']]}' alt='' loading='lazy'></div>"
                    f"<p><input type='checkbox' data-id='{e(d['id'])}' data-keys='{e(' '.join(d['keys']))}'{' checked' if on else ''}>"
                    f"<b>是 logo</b>　<span class='mut'>#{e(d['id'])}</span></p>"
                    f"<p>{e(st)}｜分數 {d['score']:.2f}</p><p class='mut'>{e(src)}｜{e(years)}｜一本最多 {d['max_pages_in_a_report']} 頁"
                    + (f"｜OCR「{e(' '.join(t[:3]))}」" if t else "")
                    + (f"｜名稱對得上（{e(d['name_match']['name'])}）" if d.get("name_match") else "")
                    + "</p></label>")

        kept = [d for d in items if not d["exclude"]]
        exc = [d for d in items if d["exclude"]][:MAX_EXCLUDED_CARDS]
        wk = [d for d in wnew if not d["exclude"]]
        n_exc = sum(1 for d in items if d["exclude"])
        form = rv["logo_form"] if rv else ""
        opts = "".join(f"<option value='{k}'{' selected' if k == form else ''}>{e(v)}</option>"
                       for k, v in [("", "（請選）")] + list(FORM_LABEL.items()))
        secs.append(f"""<section class="firm" data-firm="{e(fid)}">
<h2>{e(fid)} {e(firm['firm_name'])} <span class="mut" style="font-size:14px;font-weight:400">候選 {len(items)}（排除 {n_exc}）｜自動提議 {sum(d['proposed'] for d in items)}｜<a href="{e(fid)}.html" target="_blank">詳細頁</a></span></h2>
<div class="mini">{mini}</div>
<h3>候選（沒被排除的 {len(kept)} 張）</h3>
{'<div class="tiles">' + ''.join(tile(d, 'cand') for d in kept) + '</div>' if kept else '<p class="mut">沒有。</p>'}
{f'<details><summary>被排除的 {n_exc} 張（列分數最高的 {len(exc)} 張；程式排除的通常不是 logo，有看到真的 logo 再勾）</summary><div class="tiles">' + ''.join(tile(d, 'exc') for d in exc) + '</div></details>' if exc else ''}
{f'<details><summary>門檻降到 {p["whatif_min_short_side_pt"]} pt 才會出現的 {len(wk)} 張（例如頁尾的小 logo；先勾起來，門檻決定後才會用）</summary><div class="tiles">' + ''.join(tile(d, 'whatif') for d in wk) + '</div></details>' if wk else ''}
<div class="ctl"><label>這家的 logo 形式：<select class="form">{opts}</select></label>
<input type="text" class="note" placeholder="備註（可空白）" value="{e(rv['note']) if rv else ''}" size="30">
<label><input type="checkbox" class="seen"{' checked' if rv else ''}> <b>這家看完了</b></label></div>
</section>""")
    t = result["totals"]
    n_firms = len(secs)
    page_note, nav = "", ""
    if pages:   # (this page number, number of pages, file names): one CSV per page, the loader reads them all
        i, n, names = pages
        page_note = f"（第 {i}／{n} 頁）"
        nav = ("<p class='mut'>共 " + str(n) + " 頁，每一頁看完各匯出一份 CSV（檔名不同，都放進同一個資料夾）："
               + "　".join(f"<b>第 {k + 1} 頁</b>" if k + 1 == i else f"<a href='{e(nm)}'>第 {k + 1} 頁</a>"
                           for k, nm in enumerate(names)) + "</p>")
    Path(path).write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>logo 參考圖確認</title><style>{CSS}{REVIEW_CSS}</style></head><body>
<h1>logo 參考圖確認：{n_firms} 家公司{page_note}</h1>{nav}
<div class="note"><b>為什麼要做：</b>程式挑的 logo 會挑錯（例如 RE100 標章、SDG 圖示、裝飾插畫）。
evidence.py 只會用你勾過的圖去照片裡找 logo，涵蓋率也只算你確認過的。
<br><b>怎麼做（約 15 分鐘）：</b>
<br>1. 每家公司先看封面、封底縮圖，再看下面的候選圖。
<br>2. 是這家公司的 logo 就勾「是 logo」；同集團、子公司、自家品牌的也算（跟標註 R 的定錨規則一樣）。
認證標章、倡議（RE100、SBTi）、GRI、SDG、獎項、合作單位的 logo、裝飾插畫都不算。程式自動提議的已先勾好，請逐張確認，錯的取消。
<br>3. 選「這家的 logo 形式」：看封面、封底，這家公司自己的 logo 是內嵌小圖（下面有勾到）、向量圖（用線條畫的，沒出現在候選裡）、畫在整頁大圖裡，還是報告裡看不到。這一欄用來數「幾家公司拿不到 logo、為什麼」（PLAN §5 步驟 2）。
<br>4. 看完一家，勾「這家看完了」。沒勾的公司，匯出時不會被採用。
<br>5. 全部看完，按「匯出 CSV」。把下載的檔案放到 <code>annotations/logo_refs/</code>（檔名要以 <code>review</code> 開頭），再跑
<code>{e(split_cmd)}</code>，參考圖就會寫進 <code>work/logo_refs/&lt;代號&gt;/refs/</code>。
<br>勾選會暫存在這個瀏覽器（關掉再開還在；換電腦或清除瀏覽資料就沒了），可以分幾次做。
<br><span class="mut">結果檔：<code>{e(result['result_file'])}</code>｜自動提議 {t['auto_proposed']} 張、分布在 {t['firms_with_auto_proposal']} 家（還沒人看過）</span></div>
<div class="bar-top"><button class="main" id="export">匯出 CSV</button><span id="prog"></span>
<button id="reset">清掉這頁的暫存，回到預設</button></div>
{''.join(secs)}
<h2>匯出的內容</h2><p class="mut">按「匯出 CSV」後，下載的檔案內容也會出現在這裡（下載被擋時可以複製貼上存檔，存成 UTF-8）。</p>
<textarea id="out" readonly></textarea>
<script>
const TAG = {json.dumps(tag)}, RESULT = {json.dumps(result['result_file'])}, STORE = "logo_review_" + TAG;
function sections() {{ return Array.from(document.querySelectorAll("section.firm")); }}
function snapshot() {{
  const s = {{}};
  for (const sec of sections()) {{
    const f = sec.dataset.firm, c = {{}};
    sec.querySelectorAll("input[data-id]").forEach(x => c[x.dataset.id] = x.checked);
    s[f] = {{c: c, form: sec.querySelector("select.form").value, note: sec.querySelector("input.note").value,
            seen: sec.querySelector("input.seen").checked}};
  }}
  return s;
}}
function save() {{ try {{ localStorage.setItem(STORE, JSON.stringify(snapshot())); }} catch (err) {{}} }}
function restore() {{
  let s = null;
  try {{ s = JSON.parse(localStorage.getItem(STORE) || "null"); }} catch (err) {{ s = null; }}
  if (!s) return;
  for (const sec of sections()) {{
    const v = s[sec.dataset.firm]; if (!v) continue;
    sec.querySelectorAll("input[data-id]").forEach(x => {{ if (x.dataset.id in v.c) x.checked = v.c[x.dataset.id]; }});
    sec.querySelector("select.form").value = v.form || "";
    sec.querySelector("input.note").value = v.note || "";
    sec.querySelector("input.seen").checked = !!v.seen;
  }}
}}
function paint() {{
  let done = 0;
  for (const sec of sections()) {{
    sec.querySelectorAll("input[data-id]").forEach(x => x.closest(".tile").classList.toggle("on", x.checked));
    const seen = sec.querySelector("input.seen").checked;
    sec.classList.toggle("done", seen); if (seen) done++;
  }}
  document.getElementById("prog").textContent = "已看完 " + done + " / " + sections().length + " 家";
}}
function q(v) {{ v = String(v == null ? "" : v); return /[",\\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }}
function exportCsv() {{
  const now = new Date().toISOString();
  const rows = [{json.dumps(REVIEW_FIELDS)}.join(",")];
  for (const sec of sections()) {{
    const f = sec.dataset.firm, seen = sec.querySelector("input.seen").checked ? "1" : "0";
    rows.push([f, "firm", "", "", "", sec.querySelector("select.form").value, seen,
               sec.querySelector("input.note").value, RESULT, now].map(q).join(","));
    sec.querySelectorAll("input[data-id]").forEach(x => rows.push(
      [f, "ref", x.dataset.id, x.dataset.keys, x.checked ? "1" : "0", "", seen, "", RESULT, now].map(q).join(",")));
  }}
  const text = rows.join("\\r\\n") + "\\r\\n";
  document.getElementById("out").value = text;
  try {{
    const blob = new Blob(["\\ufeff" + text], {{type: "text/csv;charset=utf-8"}});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "review_" + TAG + "_" + now.slice(0, 10).replace(/-/g, "") + ".csv";
    document.body.appendChild(a); a.click(); a.remove();
  }} catch (err) {{}}
}}
restore(); paint();
document.addEventListener("change", () => {{ save(); paint(); }});
document.addEventListener("input", (ev) => {{ if (ev.target.classList.contains("note")) save(); }});
document.getElementById("export").addEventListener("click", exportCsv);
document.getElementById("reset").addEventListener("click", () => {{
  try {{ localStorage.removeItem(STORE); }} catch (err) {{}}
  location.reload();
}});
</script>
</body></html>""", encoding="utf-8")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    pick = ap.add_mutually_exclusive_group(required=True)
    pick.add_argument("--firms", nargs="+", help="firm ids, all three years")
    pick.add_argument("--split", choices=["dev", "test"], help="every firm of one split")
    pick.add_argument("--reports", nargs="+", help="report names such as 1101_2025 (quick look: scratch output)")
    pick.add_argument("--all", action="store_true")
    ap.add_argument("--shared-scope", choices=["all", "dev", "selected"],
                    help="reports scanned to count how many firms use an image (default: logo_refs.shared_scope in "
                         "the config; another scope writes to a scratch folder)")
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--download-check", help="default: the newest results/download_check_*.json")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tag", help="label for the result file name (default: Nfirms / dev30 / all)")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--review-chunk", type=int, default=30, help="firms per review page (0: one page); 10/22 for the test split")
    ap.add_argument("--no-ocr", action="store_true", help="skip OCR (the name gate is then never met)")
    ap.add_argument("--review-glob", help="review CSV files to read (default: logo_refs.review_glob in the config; "
                                          "another value writes to a scratch folder)")
    args = ap.parse_args()

    cfg = load_yaml(args.config)
    paths = cfg["paths"]
    p = params_from(cfg)
    if args.no_ocr:
        p["ocr"] = False
    scope_name = args.shared_scope or p["shared_scope"]
    official = (not args.reports and scope_name == p["shared_scope"]
                and (args.review_glob is None or args.review_glob == p["review_glob"]))
    if args.review_glob:
        p["review_glob"] = args.review_glob
    base_dir = Path(paths["logo_refs"])
    scan_dir, ocr_dir = base_dir / "_scan", base_dir / "_ocr"
    p["_scan_dir"] = str(scan_dir)
    with open(paths["catalog"], encoding="utf-8-sig", newline="") as f:
        catalog = [dict(r, name=f"{r['firm_id'].strip()}_{r['year'].strip()}") for r in csv.DictReader(f)]
    for r in catalog:
        for k in ("firm_id", "year", "file_path", "split", "sha256", "firm_name"):
            r[k] = r[k].strip()
    dl_path = Path(args.download_check) if args.download_check else latest("download_check_*.json")
    dl = {r["name"]: r for r in json.loads(Path(dl_path).read_text(encoding="utf-8"))["reports"]}
    not_checked = [r["name"] for r in catalog if r["name"] not in dl]
    if not_checked:
        sys.exit(f"{dl_path} 沒有這些報告的 image_only：{not_checked}")
    image_only = {n for n, r in dl.items() if r["image_only"]}

    if args.all:
        chosen = catalog
    elif args.split:
        chosen = [r for r in catalog if r["split"] == args.split]
    elif args.firms:
        chosen = [r for r in catalog if r["firm_id"] in args.firms]
    else:
        chosen = [r for r in catalog if r["name"] in args.reports]
    if not chosen:
        sys.exit("沒有選到任何報告")
    n_chosen_firms = len({r["firm_id"] for r in chosen})
    tag = args.tag or ("all" if args.all else (f"{args.split}{n_chosen_firms}" if args.split else f"{n_chosen_firms}firms"))
    stamp = run_stamp()
    if official:
        out_dir, html_dir = base_dir, Path(paths["reports"]) / "logo_refs"
    else:
        out_dir = base_dir / "_try" / f"{stamp}_{tag}"
        html_dir = Path(paths["reports"]) / "logo_refs" / "_try" / f"{stamp}_{tag}"
        print(f"快速查看：輸出寫到 {out_dir} 和 {html_dir}，正式的參考圖不動", flush=True)
    skipped = [r for r in chosen if r["name"] in image_only]
    use = [r for r in chosen if r["name"] not in image_only]
    if scope_name == "all":
        scope = [r for r in catalog if r["name"] not in image_only]
    elif scope_name == "dev":
        scope = [r for r in catalog if r["name"] not in image_only and r["split"] == "dev"]
    else:
        scope = list(use)
    scope_names = {r["name"] for r in scope} | {r["name"] for r in use}
    to_scan = [r for r in catalog if r["name"] in scope_names]
    missing_pdf = [r["name"] for r in to_scan if not Path(r["file_path"]).is_file()]
    if missing_pdf:
        sys.exit(f"找不到 PDF：{missing_pdf}")

    t0 = time.time()
    jobs = [(r["name"], r["file_path"], r["sha256"], str(scan_dir), {k: p[k] for k in SCAN_KEYS}) for r in to_scan]
    # selected reports first, so their scans are ready even if the shared scan is interrupted
    jobs.sort(key=lambda j: (j[0] not in {r["name"] for r in use}, j[0]))
    scans = run_scans(jobs, args.workers)
    scan_s = time.time() - t0
    errors = {n: c["error"] for n, c in scans.items() if c.get("error")}
    unreadable = [r for r in use if r["name"] in errors]
    use = [r for r in use if r["name"] not in errors]
    scope_names -= set(errors)
    firm_of = {r["name"]: r["firm_id"] for r in catalog}
    shared = SharedIndex({n: scans[n] for n in scope_names}, firm_of, p)
    shared_info = {"scope": scope_name, "n_reports": shared.n_reports, "n_firms": shared.n_firms,
                   "n_small_images": int(len(shared.h)),
                   "image_only_reports_left_out": sorted(n for n in image_only if n in {r["name"] for r in catalog})}
    names, name_sources = load_names(catalog, paths["index"])
    floors = {"repeated": p["min_short_side_pt"], "cover": p["cover_min_short_side_pt"]}
    floors_w = {"repeated": p["whatif_min_short_side_pt"], "cover": p["cover_min_short_side_pt"]}

    # ---- candidates (official floors and the what-if floor)
    firms = collections.OrderedDict()
    for r in chosen:
        firms.setdefault(r["firm_id"], {"firm_id": r["firm_id"], "firm_name": r["firm_name"], "split": r["split"]})
    bundles = collections.OrderedDict()
    t1 = time.time()
    for fid, firm in firms.items():
        reps = [r for r in use if r["firm_id"] == fid]
        groups = prep_groups(reps, scans)
        items = build_items(fid, groups, len(reps), shared, p, floors) if reps else []
        items_w = build_items(fid, groups, len(reps), shared, p, floors_w) if reps else []
        official_keys = {k for d in items for k in d["keys"]}
        bundles[fid] = {"firm": firm, "reps": reps, "skipped": [r for r in skipped if r["firm_id"] == fid],
                        "items": items, "items_w": items_w,
                        "wnew": [d for d in items_w if not set(d["keys"]) & official_keys]}
    build_s = time.time() - t1

    # ---- OCR and the name gate
    live = [d for B in bundles.values() for d in B["items"] + B["items_w"] if not d["exclude"]]
    ocr_info = run_ocr(live, p, ocr_dir)
    if not ocr_info.get("ok"):
        print(f"[ocr] 失敗：{ocr_info.get('error')}；名稱條件這次都不成立", flush=True)
    for fid, B in bundles.items():
        for d in B["items"] + B["items_w"]:
            if not d["exclude"]:
                d["name_match"] = name_match(d["ocr"], names.get(fid, []), p)
        select(B["items"], p)
        select(B["items_w"], p)

    # ---- Eden's review
    review_files, reviews = load_review(p["review_glob"])
    for fid, B in bundles.items():
        rv = reviews.get(fid)
        B["review"] = rv
        used = apply_review(B["items"], rv)
        used |= apply_review(B["wnew"], rv)
        B["review_unmatched"] = (len(rv["refs"]) - len(used)) if rv else 0
        for k, d in enumerate(B["items"]):
            d["id"] = f"cand_{k:02d}"
            d["png"] = f"candidates/{d['id']}.png"
        for k, d in enumerate(B["wnew"]):
            d["id"] = f"w{p['whatif_min_short_side_pt']:g}_{k:02d}"

    # ---- per report, per firm, outputs
    per_firm, per_report = collections.OrderedDict(), collections.OrderedDict()
    rinfo = {}
    for r in skipped:
        per_report[r["name"]] = {"firm_id": r["firm_id"], "year": r["year"], "split": r["split"], "skipped": "image_only_report"}
    for r in unreadable:
        per_report[r["name"]] = {"firm_id": r["firm_id"], "year": r["year"], "split": r["split"],
                                 "skipped": "scan_error", "error": errors[r["name"]]}
    report_rows = collections.defaultdict(list)
    for r in skipped:
        report_rows[r["firm_id"]].append({"level": "report", "report": r["name"], "reason_code": "image_only_report",
                                          "reason": "沒有文字層的報告（整本跳過，10/18 Eden 決定 5）"})
    for r in unreadable:
        report_rows[r["firm_id"]].append({"level": "report", "report": r["name"], "reason_code": "scan_error",
                                          "reason": f"PDF 讀不了：{errors[r['name']]}"})
    for r in use:
        c = scans[r["name"]]
        members = [ci for d in bundles[r["firm_id"]]["items"] for ci in d["candidate_in"] if ci["report"] == r["name"]]
        by_source = dict(collections.Counter(s for ci in members for s in ci["sources"]))
        code, reason = report_reason(c, len(members), p)
        rinfo[r["name"]] = {"n_pages": c["n_pages"], "candidates": len(members), "by_source": by_source, "reason": reason}
        per_report[r["name"]] = {
            "firm_id": r["firm_id"], "year": r["year"], "split": r["split"], "pages": c["n_pages"],
            **{k: c["stats"].get(k, 0) for k in ("placements", "shading_skipped", "below_min_px", "below_scan_min_pt",
                                                 "too_large", "mostly_off_page", "window_placements",
                                                 "unreadable_groups")},
            "small_image_groups": c["n_groups"], "potential_groups": c["n_potential"],
            "candidates": len(members), "candidates_by_source": by_source,
            "cover": c["cover"], "back_cover": c.get("back_cover"), "reason_code": code,
            "scan_seconds": c["seconds"], "scan_from_cache": c["from_cache"]}
        if code:
            report_rows[r["firm_id"]].append({"level": "report", "report": r["name"], "reason_code": code, "reason": reason})

    for fid, B in bundles.items():
        firm, items, reps, rv = B["firm"], B["items"], B["reps"], B["review"]
        code, reason = firm_reason(items, reps, p)
        prop = [d for d in items if d["proposed"]]
        conf = [d for d in items if d["confirmed"] is True]
        w_prop = [d for d in B["wnew"] if d["proposed"]]
        review = {"status": "reviewed" if rv else "not_reviewed", "file": rv["file"] if rv else None,
                  "logo_form": rv["logo_form"] if rv else None, "note": rv["note"] if rv else None,
                  "confirmed": len(conf),
                  "proposals_confirmed": sum(1 for d in prop if d["confirmed"] is True) if rv else None,
                  "proposals_rejected": sum(1 for d in prop if d["confirmed"] is False) if rv else None,
                  "confirmed_not_proposed": sum(1 for d in conf if not d["proposed"]) if rv else None,
                  "whatif_confirmed": sum(1 for d in B["wnew"] if d["confirmed"] is True) if rv else None,
                  "review_rows_not_matched": B["review_unmatched"]}
        refs = []
        for k, d in enumerate(conf):
            refs.append({"id": f"ref_{k:02d}", "png": f"refs/ref_{k:02d}.png", "cand_id": d["id"], "keys": d["keys"],
                         "sources": d["sources"], "years": d["years"], "w_px": d["w_px"], "h_px": d["h_px"],
                         "score": d["score"], "auto_proposed": d["proposed"]})
        firm_rows = [dict(row, firm_id=fid, firm_name=firm["firm_name"], split=firm["split"]) for row in report_rows[fid]]
        if not conf:
            firm_rows.append({"level": "firm", "firm_id": fid, "firm_name": firm["firm_name"], "report": "",
                              "split": firm["split"],
                              "reason_code": code or ("review_found_no_logo" if rv else "not_reviewed"),
                              "reason": reason or ("Eden 看過，沒有勾任何一張" if rv else "有自動提議，但還沒確認")})
        for row in firm_rows:
            row.update(auto_proposed=len(prop), review_status=review["status"], confirmed_refs=len(conf),
                       logo_form=review["logo_form"] or "")
        B["missing_rows"] = firm_rows
        per_firm[fid] = {
            "firm_name": firm["firm_name"], "split": firm["split"],
            "reports_used": [r["name"] for r in reps], "reports_skipped": [r["name"] for r in B["skipped"]],
            "candidates": len(items), "excluded": sum(1 for d in items if d["exclude"]),
            "excluded_by_reason": dict(collections.Counter(x for d in items for x in d["exclude"])),
            "status_counts": dict(collections.Counter(d["status"] for d in items)),
            "auto_proposed": len(prop), "auto_reason_code": code, "auto_reason": reason,
            "proposed_refs": [{k: d[k] for k in ("id", "score", "sources", "years", "w_px", "h_px", "gate", "name_match",
                                                 "max_pages_in_a_report")} for d in prop],
            "review": review, "has_confirmed_ref": (len(conf) > 0) if rv else None,
            "confirmed_refs": [{k: x[k] for k in ("id", "cand_id", "sources", "years", "w_px", "h_px")} for x in refs],
            "whatif": {"floor_pt": p["whatif_min_short_side_pt"], "new_candidates": len(B["wnew"]),
                       "new_not_excluded": sum(1 for d in B["wnew"] if not d["exclude"]),
                       "new_proposed": len(w_prop), "new_proposed_ids": [d["id"] for d in w_prop],
                       "firm_has_proposal": any(d["proposed"] for d in B["items_w"])}}
        B["refs"] = refs
        if reps:
            firm_dir = out_dir / fid
            if firm_dir.exists():
                shutil.rmtree(firm_dir)
            (firm_dir / "candidates").mkdir(parents=True)
            for d in items:
                shutil.copyfile(scan_dir / d["rep_png_scan"], firm_dir / d["png"])
            if refs:
                (firm_dir / "refs").mkdir()
                for x, d in zip(refs, conf):
                    shutil.copyfile(scan_dir / d["rep_png_scan"], firm_dir / x["png"])
            idx = {"firm_id": fid, "firm_name": firm["firm_name"], "split": firm["split"], "date": run_stamp(),
                   "note": "evidence.py uses only 'refs' (pictures Eden confirmed); 'candidates' are for review",
                   "reports_used": per_firm[fid]["reports_used"], "reports_skipped": per_firm[fid]["reports_skipped"],
                   "params": {k: v for k, v in p.items() if not k.startswith("_")}, "shared_index": shared_info,
                   "review": review, "refs": refs,
                   "candidates": [{k: v for k, v in d.items() if not k.startswith("_")} for d in items],
                   "whatif_new_candidates": [{k: v for k, v in d.items() if not k.startswith("_")} for d in B["wnew"]],
                   "missing_rows": firm_rows}
            (firm_dir / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")

    # missing.csv: every firm folder in this output, so a partial run does not drop other firms
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in sorted(out_dir.glob("*/index.json")):
        try:
            rows += json.loads(f.read_text(encoding="utf-8")).get("missing_rows", [])
        except Exception:
            pass
    for fid, B in bundles.items():  # firms without a usable report have no folder
        if not B["reps"]:
            rows += B["missing_rows"]
    miss_path = out_dir / "missing.csv"
    fields = ["level", "firm_id", "firm_name", "report", "split", "reason_code", "reason", "auto_proposed",
              "review_status", "confirmed_refs", "logo_form"]
    with open(miss_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # ---- totals and the result file
    all_items = [d for B in bundles.values() for d in B["items"]]
    all_w = [d for B in bundles.values() for d in B["wnew"]]
    n_firms = len(per_firm)
    reviewed = [f for f in per_firm.values() if f["review"]["status"] == "reviewed"]
    with_conf = sum(1 for f in reviewed if f["has_confirmed_ref"])
    prop_reviewed = [d for B in bundles.values() if B["review"] for d in B["items"] if d["proposed"]]
    totals = {
        "firms": n_firms, "reports_selected": len(chosen), "reports_used": len(use), "reports_skipped": len(skipped),
        "reports_skipped_names": [r["name"] for r in skipped], "reports_unreadable": len(unreadable),
        "scan_errors_in_shared_scope": errors,
        "reports_with_candidates": sum(1 for r in use if rinfo[r["name"]]["candidates"]),
        "candidates": len(all_items), "excluded": sum(1 for d in all_items if d["exclude"]),
        "excluded_by_reason": dict(collections.Counter(x for d in all_items for x in d["exclude"])),
        "status_counts": dict(collections.Counter(d["status"] for d in all_items)),
        "auto_proposed": sum(1 for d in all_items if d["proposed"]),
        "firms_with_auto_proposal": sum(1 for f in per_firm.values() if f["auto_proposed"]),
        "auto_proposal_note": "not reviewed: not coverage (coverage counts only refs Eden confirmed)",
        "proposed_by_gate": {k: sum(1 for d in all_items if d["proposed"] and d["gate"][k])
                             for k in ("repeat", "cross_year", "name")},
        "blocked_by_gate_only": sum(1 for d in all_items if d["status"] == "no_gate" and d["score"] >= p["select_min_score"]),
        "name_matches": sum(1 for d in all_items if d.get("name_match")),
        "by_source": dict(collections.Counter(s for d in all_items for s in d["sources"])),
        "proposed_by_source": dict(collections.Counter(s for d in all_items if d["proposed"] for s in d["sources"])),
        "auto_reason_codes": dict(collections.Counter(f["auto_reason_code"] or "proposed" for f in per_firm.values())),
        "shading_skipped": sum(v.get("shading_skipped", 0) for v in per_report.values()),
        "review": {"files": review_files, "firms_reviewed": len(reviewed), "firms_with_confirmed_ref": with_conf,
                   "coverage_confirmed": round(with_conf / n_firms, 4) if n_firms and len(reviewed) == n_firms else None,
                   "coverage_among_reviewed": round(with_conf / len(reviewed), 4) if reviewed else None,
                   "confirmed_refs": sum(f["review"]["confirmed"] for f in reviewed),
                   "auto_proposals_reviewed": len(prop_reviewed),
                   "auto_proposals_confirmed": sum(1 for d in prop_reviewed if d["confirmed"] is True),
                   "logo_form": dict(collections.Counter(f["review"]["logo_form"] or "blank" for f in reviewed))},
        "whatif": {"floor_pt": p["whatif_min_short_side_pt"], "new_candidates": len(all_w),
                   "new_not_excluded": sum(1 for d in all_w if not d["exclude"]),
                   "new_proposed": sum(1 for d in all_w if d["proposed"]),
                   "firms_with_new_candidates": sum(1 for B in bundles.values() if B["wnew"]),
                   "firms_gaining_a_proposal": sum(1 for f in per_firm.values()
                                                   if not f["auto_proposed"] and f["whatif"]["firm_has_proposal"])},
    }
    res_dir = Path(paths["results"]) if official else out_dir  # a quick look stays out of results/
    res_dir.mkdir(parents=True, exist_ok=True)
    out_json = res_dir / f"logo_refs_{stamp}_{tag}.json"
    for suffix in "bcdefgh":  # two runs in the same minute: never overwrite the earlier result
        if not out_json.exists():
            break
        out_json = res_dir / f"logo_refs_{stamp}{suffix}_{tag}.json"
    data_files = [paths["catalog"], str(dl_path)] + name_sources
    meta = run_meta(args.config, data_files, packages=("pymupdf", "pillow", "numpy", "pyyaml", "opencv-python",
                                                         "opencv-python-headless", "opencv-contrib-python"))
    result_file = str(out_json).replace("\\", "/")
    review_name = f"review_{tag}.html"
    fids_html = [fid for fid, B in bundles.items() if B["reps"]]
    chunk = args.review_chunk if 0 < args.review_chunk < len(fids_html) else max(len(fids_html), 1)
    review_groups = [fids_html[i:i + chunk] for i in range(0, len(fids_html), chunk)] or [fids_html]
    review_names = ([review_name] if len(review_groups) == 1
                    else [f"review_{tag}_p{i + 1}.html" for i in range(len(review_groups))])
    result = {"meta": meta, "tag": tag, "result_file": result_file, "official_output": official,
              "params": {k: v for k, v in p.items() if not k.startswith("_")},
              "floors_pt": {"official": floors, "whatif": floors_w, "scan": p["scan_min_pt"]},
              "download_check": str(dl_path), "shared_index": shared_info, "name_sources": name_sources,
              "ocr": ocr_info,
              "seconds": {"scan": round(scan_s), "scan_sum_recorded_in_cache": round(sum(
                  scans[r["name"]].get("seconds", 0) for r in to_scan if r["name"] in scans)),
                  "candidates": round(build_s), "total_before_html": round(time.time() - t0)},
              "outputs": {"refs_dir": str(out_dir), "missing_csv": str(miss_path),
                          "html": [] if args.no_html else [str(html_dir / f"{fid}.html") for fid in per_firm]
                          + [str(html_dir / "index.html")] + [str(html_dir / n) for n in review_names]},
              "totals": totals, "per_firm": per_firm, "per_report": per_report}
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.no_html:
        html_dir.mkdir(parents=True, exist_ok=True)
        hjobs = []
        for fid, B in bundles.items():
            if not B["reps"]:
                continue
            hjobs.append((str(html_dir / f"{fid}.html"), B["firm"], B["items"], B["wnew"],
                          [{"name": r["name"], "file_path": r["file_path"], "year": r["year"]} for r in B["reps"]],
                          {r["name"]: rinfo[r["name"]] for r in B["reps"]}, {k: v for k, v in p.items()}, meta,
                          shared_info, B["review"], result_file, [r["name"] for r in B["skipped"]]))
        frags = {}
        if args.workers > 1 and len(hjobs) > 1:
            with Pool(min(args.workers, len(hjobs))) as pool:
                for fr in pool.imap_unordered(firm_html_job, hjobs):
                    frags[fr["firm_id"]] = fr
        else:
            for j in hjobs:
                fr = firm_html_job(j)
                frags[fr["firm_id"]] = fr
        if args.split:
            split_cmd = f"python src/logo_refs.py --split {args.split}"
        elif args.all:
            split_cmd = "python src/logo_refs.py --all"
        elif args.firms:
            split_cmd = "python src/logo_refs.py --firms " + " ".join(args.firms)
        else:
            split_cmd = "python src/logo_refs.py --reports " + " ".join(args.reports)
        write_index_html(html_dir / "index.html", result, review_names)
        for i, (name, group) in enumerate(zip(review_names, review_groups)):
            sub = collections.OrderedDict((fid, bundles[fid]) for fid in group if fid in frags)
            write_review_html(html_dir / name, tag if len(review_groups) == 1 else f"{tag}_p{i + 1}", result, sub,
                              frags, p, split_cmd, None if len(review_groups) == 1 else (i + 1, len(review_groups), review_names))
    print(json.dumps({k: totals[k] for k in ("firms", "reports_used", "reports_skipped", "candidates", "excluded",
                                             "auto_proposed", "firms_with_auto_proposal", "proposed_by_gate",
                                             "blocked_by_gate_only", "review", "whatif")}, ensure_ascii=False))
    for fid, f in per_firm.items():
        print(f"{fid} {f['firm_name']}: 報告 {len(f['reports_used'])} 本，候選 {f['candidates']}，排除 {f['excluded']}，"
              f"自動提議 {f['auto_proposed']} {f['auto_reason_code']}｜{p['whatif_min_short_side_pt']:g} pt 多出 "
              f"{f['whatif']['new_candidates']}，提議 {f['whatif']['new_proposed']}")
    print(f"wrote {out_json}, {miss_path}" + ("" if args.no_html else f", {html_dir}"))


if __name__ == "__main__":
    main()
