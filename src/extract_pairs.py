"""Pair each picture in a sustainability-report PDF with the text a reader sees next to it.

For every page the script
  1. lists where embedded images are drawn (one entry per placement),
  2. drops icons, logos and other small pictures, and vector gradient fills that PyMuPDF
     reports as images (is_shading_image; "v2", 10/18 evening),
  3. renders the visible area of each remaining picture (what the reader sees,
     including crops, masks and text printed on top of it),
  4. collects the text around it:
       caption   - a short text block directly below or above the picture
       overlay   - text printed on top of the picture
       paragraphs- the nearest body-text blocks (same column preferred)
       heading   - the largest-font short text on the page
       section   - short text in the top/bottom page margin (running header/footer)
and writes one JSON line per picture.

Each record also lists, for every embedded image file behind the picture,
  layer_md5       - MD5 of the file, used by the bit-identical baseline (B1)
  layer_xmp_date  - earliest creation date in the XMP metadata attached to the image
                    object ('' if none). A file date is not the shooting date, but a date
                    years before the report is a hint that the photo is old.

Usage:
  python -I extract_pairs.py --pdf REPORT.pdf --name 1101_2025 --out work/extract
  (pilot settings: --crop-long-side 480 --jpg-quality 80)
"""
import argparse
import collections
import hashlib
import json
import re
import statistics
from pathlib import Path

import pymupdf

MIN_PX = 150            # shorter side of the embedded image file, pixels
MIN_PT = 72             # shorter side of the drawn area on the page, points (1 inch)
CAPTION_GAP = 28        # max vertical gap between picture and caption, points
CAPTION_MAXLEN = 120    # max characters for a caption
PARA_MINLEN = 25        # min characters for a block to count as body text
MARGIN_FRAC = 0.06      # top/bottom page margin used for running headers/footers
CROP_LONG_SIDE = 1024   # longer side of the saved crop, pixels (pilot used 480; matching needs detail)
JPG_QUALITY = 90        # pilot used 80; higher keeps fewer compression artefacts for matching
N_PARAGRAPHS = 2
LAYER_IOU = 0.6         # same-page placements overlapping this much are one picture
DHASH_MAX_BITS = 4      # crops whose 64-bit difference hash differs in <= this many bits look the same
LAYOUT_MIN_PAGES = 3    # a picture repeated on this many pages is a layout element (chapter background etc.)


def _is_ascii_word(ch):
    return ch.isascii() and ch.isalnum()


def block_text(block):
    """Join spans without spaces (Chinese) but keep a space between Latin words across lines."""
    lines = []
    for line in block["lines"]:
        lines.append("".join(span["text"] for span in line["spans"]).strip())
    out = ""
    for ln in lines:
        if not ln:
            continue
        if out and _is_ascii_word(out[-1]) and _is_ascii_word(ln[0]):
            out += " "
        out += ln
    return out.strip()


def block_font_size(block):
    sizes = [s["size"] for l in block["lines"] for s in l["spans"] if s["text"].strip()]
    return max(sizes) if sizes else 0.0


def horizontal_overlap(a, b):
    """Overlap of the x-ranges, as a fraction of the narrower rectangle's width."""
    lo, hi = max(a.x0, b.x0), min(a.x1, b.x1)
    if hi <= lo:
        return 0.0
    return (hi - lo) / max(1e-6, min(a.width, b.width))


def rect_gap(a, b):
    """Shortest distance between two rectangles (0 if they touch or overlap)."""
    dx = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
    dy = max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1))
    return (dx * dx + dy * dy) ** 0.5


def inside_fraction(inner, outer):
    """Fraction of `inner`'s area that lies inside `outer`."""
    inter = pymupdf.Rect(inner) & outer
    if inter.is_empty or inner.get_area() <= 0:
        return 0.0
    return inter.get_area() / inner.get_area()


NOT_CAPTION_PREFIX = ("註", "*", "※", "資料來源", "來源", "Source", "Note")
NUMBERED_HEADING = re.compile(r"^\(?[0-9０-９一二三四五六七八九十]+(\.[0-9]+)*[\)\.、）]?\s*\S")


def _norm(text):
    """Text key used to spot running headers/menus that repeat on many pages."""
    return re.sub(r"[\s0-9]", "", text)[:60]


def raw_text_blocks(page):
    """Text blocks of one page. extract_report() reads each page once and reuses the result.
    Keep the default "dict" flags: dropping image blocks is faster, but MuPDF then joins
    text across pictures into bigger blocks and captions pick up paragraph text (tested
    2026-10-18 on the pilot reports)."""
    blocks = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        text = block_text(b)
        if not text:
            continue
        blocks.append({"rect": pymupdf.Rect(b["bbox"]), "text": text, "size": block_font_size(b)})
    return blocks


def merge_fragments(blocks):
    """Join consecutive blocks that are lines of the same paragraph (same font size,
    same left edge, small vertical gap), so a paragraph's last line is not mistaken
    for a caption."""
    blocks = sorted(blocks, key=lambda b: (round(b["rect"].x0), b["rect"].y0))
    merged = []
    for b in blocks:
        if merged:
            p = merged[-1]
            same_size = abs(p["size"] - b["size"]) <= 0.5
            same_left = abs(p["rect"].x0 - b["rect"].x0) <= 3
            small_gap = 0 <= b["rect"].y0 - p["rect"].y1 <= 1.2 * max(b["size"], 1)
            if same_size and same_left and small_gap:
                p["rect"] = p["rect"] | b["rect"]
                p["text"] = p["text"] + b["text"]
                continue
        merged.append(dict(b))
    return merged


def page_text_blocks(raw_blocks, running_keys):
    """Paragraph-level blocks of a page (merge_fragments copies, so raw_blocks is not changed)."""
    blocks = merge_fragments(raw_blocks)
    for b in blocks:
        b["running"] = _norm(b["text"]) in running_keys
    return blocks


def running_text_keys(pages_blocks):
    """Texts that appear on many pages (menus, chapter tabs, footers).
    `pages_blocks` holds raw_text_blocks() of every page of the report."""
    pages_with = collections.Counter()
    for blocks in pages_blocks:
        keys = {_norm(b["text"]) for b in blocks}
        pages_with.update(k for k in keys if k)
    threshold = max(5, int(0.03 * len(pages_blocks)))
    return {k for k, n in pages_with.items() if n >= threshold}


def digit_ratio(text):
    t = re.sub(r"\s", "", text)
    return sum(ch.isdigit() for ch in t) / max(1, len(t))


def rect_iou(a, b):
    inter = pymupdf.Rect(a) & b
    if inter.is_empty:
        return 0.0
    ia = inter.get_area()
    return ia / (a.get_area() + b.get_area() - ia)


def merge_layers(pics, thr=LAYER_IOU):
    """Merge placements that overlap heavily on the same page (an image drawn twice with
    an offset, a photo with a gradient or mask layer on top). Keeps the largest image's
    metadata, grows the area to the union, and lists the merged objects in `layers`."""
    pics = sorted(pics, key=lambda p: -(p["px_w"] * p["px_h"]))
    merged = []
    for p in pics:
        target = next((m for m in merged if rect_iou(p["rect"], m["rect"]) >= thr), None)
        if target is None:
            q = dict(p)
            q["layers"] = [p["xref"]]
            merged.append(q)
        else:
            target["rect"] = target["rect"] | p["rect"]
            target["layers"].append(p["xref"])
    merged.sort(key=lambda m: (m["rect"].y0, m["rect"].x0))  # reading order on the page
    return merged


def is_shading_image(info):
    """True for an entry of page.get_image_info() that is a vector gradient fill (a PDF
    shading), not an image: PyMuPDF 1.28 reports shadings as images. All three must hold:
    no embedded file (xref 0); exactly one pixel per point, axis-aligned; raw pixels
    (size = width x height x colour components). Real images never met all three in the
    10/18 check (LOG.md 2026-10-18 16:05). 4558_2024 alone had 11,239 such entries."""
    t = info["transform"]
    w, h = info.get("width", 0), info.get("height", 0)
    return (info.get("xref", 0) == 0 and abs(t[1]) < 1e-3 and abs(t[2]) < 1e-3
            and abs(abs(t[0]) - w) < 0.01 and abs(abs(t[3]) - h) < 0.01
            and info.get("size") == w * h * info.get("colorspace", 0))


def image_info(page, keep_shading=False):
    """page.get_image_info(xrefs=True) without vector gradient fills (is_shading_image;
    keep_shading=True keeps them, for checks).

    One broken image on the page (e.g. 'image height is zero', 5522_2024 pages 11 and 58)
    makes that call fail for the whole page; then use the plain call and take the xref of
    the page image with the same pixel size, or 0 when that size is not unique on the page
    (xref 0 is handled like an inline image). Shadings are recognised before that guess."""
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        by_size = collections.defaultdict(set)
        for img in page.get_images(full=True):
            by_size[(img[2], img[3])].add(img[0])
        infos = page.get_image_info()
        for info in infos:
            info["xref"] = 0
            if is_shading_image(info):
                continue
            xrefs = by_size.get((info.get("width"), info.get("height")), set())
            info["xref"] = next(iter(xrefs)) if len(xrefs) == 1 else 0
    return infos if keep_shading else [i for i in infos if not is_shading_image(i)]


def picture_placements(page):
    """Visible picture placements on the page, de-duplicated, small ones removed."""
    seen = set()
    out = []
    page_rect = page.rect
    for info in image_info(page):
        rect = pymupdf.Rect(info["bbox"]) & page_rect
        if rect.is_empty:
            continue
        w_px, h_px = info.get("width", 0), info.get("height", 0)
        key = (info.get("xref", 0), round(rect.x0), round(rect.y0), round(rect.x1), round(rect.y1))
        if key in seen:
            continue
        seen.add(key)
        if min(w_px, h_px) < MIN_PX or min(rect.width, rect.height) < MIN_PT:
            continue
        out.append({"rect": rect, "xref": info.get("xref", 0), "px_w": w_px, "px_h": h_px})
    return merge_layers(out)


def vertical_overlap(a, b):
    lo, hi = max(a.y0, b.y0), min(a.y1, b.y1)
    if hi <= lo:
        return 0.0
    return (hi - lo) / max(1e-6, min(a.height, b.height))


def is_caption_like(blk, median_size):
    t = blk["text"]
    return (not blk["running"]
            and len(t) <= CAPTION_MAXLEN
            and not t.startswith(NOT_CAPTION_PREFIX)
            and digit_ratio(t) <= 0.4
            and blk["size"] <= 1.1 * median_size
            and not NUMBERED_HEADING.match(t))


def find_caption(img_rect, blocks, median_size):
    """Closest caption-like block below, above, or beside the picture.
    Below is preferred, then above, then left/right (penalties in points)."""
    best, best_score, best_pos = None, None, None
    for blk in blocks:
        r = blk["rect"]
        if not is_caption_like(blk, median_size) or inside_fraction(r, img_rect) > 0.5:
            continue
        options = []
        if horizontal_overlap(r, img_rect) >= 0.5:
            options += [(r.y0 - img_rect.y1, 0, "below"), (img_rect.y0 - r.y1, 4, "above")]
        if vertical_overlap(r, img_rect) >= 0.5:
            options += [(r.x0 - img_rect.x1, 8, "right"), (img_rect.x0 - r.x1, 8, "left")]
        for g, penalty, pos in options:
            if -2 <= g <= CAPTION_GAP:
                score = g + penalty
                if best_score is None or score < best_score:
                    best, best_score, best_pos = blk, score, pos
    return best, best_pos


def find_local_heading(img_rect, blocks, median_size, max_dist=250):
    """Nearest heading-looking block above the picture in the same column
    (larger font or numbered like '(4) ...' / '1.1 ...')."""
    best, best_d = None, None
    for blk in blocks:
        r = blk["rect"]
        if blk["running"] or len(blk["text"]) > 60:
            continue
        looks_heading = blk["size"] >= 1.1 * median_size or NUMBERED_HEADING.match(blk["text"])
        if not looks_heading or horizontal_overlap(r, img_rect) < 0.3:
            continue
        d = img_rect.y0 - r.y1
        if -2 <= d <= max_dist and (best_d is None or d < best_d):
            best, best_d = blk, d
    return best


def embedded_md5(doc, xref, cache):
    """MD5 of the embedded image file behind an xref ('' if it cannot be extracted)."""
    if xref in cache:
        return cache[xref]
    digest = ""
    if xref:
        try:
            digest = hashlib.md5(doc.extract_image(xref)["image"]).hexdigest()
        except Exception:  # broken or unsupported image stream
            digest = ""
    cache[xref] = digest
    return digest


XMP_DATE = re.compile(r"(?:xmp:CreateDate|exif:DateTimeOriginal|photoshop:DateCreated)"
                      r"\s*(?:=\s*[\"']|>)\s*(\d{4}-\d\d-\d\d)")


def xmp_create_date(doc, xref, cache):
    """Earliest creation date (YYYY-MM-DD) in the XMP metadata of an image object, or ''."""
    if xref in cache:
        return cache[xref]
    date = ""
    if xref:
        try:
            kind, val = doc.xref_get_key(xref, "Metadata")
            if kind == "xref":
                xmp = doc.xref_stream(int(val.split()[0])).decode("utf-8", "ignore")
                found = XMP_DATE.findall(xmp)
                date = min(found) if found else ""
        except Exception:  # missing or broken metadata stream
            date = ""
    cache[xref] = date
    return date


def describe_page(page, report_name, crop_dir, running_keys, raw_blocks=None,
                  crop_long_side=CROP_LONG_SIDE, jpg_quality=JPG_QUALITY, md5_cache=None,
                  xmp_cache=None):
    md5_cache = {} if md5_cache is None else md5_cache
    xmp_cache = {} if xmp_cache is None else xmp_cache
    raw_blocks = raw_text_blocks(page) if raw_blocks is None else raw_blocks
    page_rect = page.rect
    page_area = page_rect.get_area()
    blocks = page_text_blocks(raw_blocks, running_keys)
    sizes = [b["size"] for b in blocks if b["size"] > 0 and not b["running"]]
    median_size = statistics.median(sizes) if sizes else 0.0

    top_cut = page_rect.y0 + MARGIN_FRAC * page_rect.height
    bottom_cut = page_rect.y1 - MARGIN_FRAC * page_rect.height
    margin_blocks = [b for b in blocks if (b["rect"].y1 <= top_cut or b["rect"].y0 >= bottom_cut)
                     and len(b["text"]) <= 40]
    section_hint = " | ".join(b["text"] for b in margin_blocks if not b["running"])[:120]

    heading = ""
    body_candidates = [b for b in blocks if b not in margin_blocks and not b["running"]]
    if body_candidates and median_size:
        top = max(body_candidates, key=lambda b: b["size"])
        if top["size"] >= 1.25 * median_size and len(top["text"]) <= 40:
            heading = top["text"]

    records = []
    for i, pic in enumerate(picture_placements(page)):
        img_rect = pic["rect"]
        caption_blk, caption_pos = find_caption(img_rect, body_candidates, median_size)
        local_heading = find_local_heading(img_rect, body_candidates, median_size)
        overlay = [b for b in body_candidates if inside_fraction(b["rect"], img_rect) >= 0.6]
        used = {id(b) for b in overlay}
        for b in (caption_blk, local_heading):
            if b is not None:
                used.add(id(b))

        paras = []
        for b in body_candidates:
            if id(b) in used or len(b["text"]) < PARA_MINLEN:
                continue
            g = rect_gap(img_rect, b["rect"])
            same_column = horizontal_overlap(img_rect, b["rect"]) > 0.3
            score = g + (0 if same_column else 80)  # prefer text in the same column
            paras.append((score, g, b))
        paras.sort(key=lambda t: t[0])

        scale = crop_long_side / max(img_rect.width, img_rect.height)  # pixels per point
        pix = page.get_pixmap(clip=img_rect, matrix=pymupdf.Matrix(scale, scale))
        crop_name = f"{report_name}_p{page.number + 1:03d}_{i:02d}.jpg"
        pix.save(str(crop_dir / crop_name), jpg_quality=jpg_quality)

        records.append({
            "report": report_name,
            "page": page.number + 1,
            "idx": i,
            "xref": pic["xref"],
            "layers": pic["layers"],
            "layer_md5": [embedded_md5(page.parent, x, md5_cache) for x in pic["layers"]],
            "layer_xmp_date": [xmp_create_date(page.parent, x, xmp_cache) for x in pic["layers"]],
            "px_w": pic["px_w"],
            "px_h": pic["px_h"],
            "bbox": [round(v, 1) for v in img_rect],
            "area_ratio": round(img_rect.get_area() / page_area, 3),
            "full_page": img_rect.get_area() / page_area > 0.85,
            "crop": crop_name,
            "caption": caption_blk["text"] if caption_blk else "",
            "caption_pos": caption_pos or "",
            "local_heading": local_heading["text"] if local_heading else "",
            "overlay_text": " / ".join(b["text"] for b in overlay)[:300],
            "paragraphs": [{"text": b["text"][:400], "gap_pt": round(g, 1)}
                           for _, g, b in paras[:N_PARAGRAPHS]],
            "heading": heading,
            "section_hint": section_hint,
        })
    return records


def dhash(path, n=8):
    """64-bit difference hash of a crop: robust to resizing and small colour changes."""
    from PIL import Image
    im = Image.open(path).convert("L").resize((n + 1, n), Image.LANCZOS)
    px = list(im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata())
    bits = 0
    for y in range(n):
        for x in range(n):
            bits = (bits << 1) | (px[y * (n + 1) + x] > px[y * (n + 1) + x + 1])
    return bits


def mark_repeats(records, crop_dir):
    """Group pictures that are the same object (same xref) or look the same (close dHash
    and similar shape) anywhere in the report, and label each record:
      doc_role = 'unique' | 'reused' (2 pages) | 'layout_repeat' (>= LAYOUT_MIN_PAGES pages)."""
    n = len(records)
    hashes = [dhash(crop_dir / r["crop"]) for r in records]
    aspect = [(r["bbox"][2] - r["bbox"][0]) / max(1e-6, r["bbox"][3] - r["bbox"][1]) for r in records]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            same_object = records[i]["xref"] != 0 and records[i]["xref"] == records[j]["xref"]
            looks_same = (bin(hashes[i] ^ hashes[j]).count("1") <= DHASH_MAX_BITS
                          and abs(aspect[i] - aspect[j]) / aspect[i] < 0.1)
            if same_object or looks_same:
                parent[find(i)] = find(j)

    groups = collections.defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    for gid, (_, members) in enumerate(sorted(groups.items(), key=lambda t: min(t[1]))):
        pages = sorted({records[k]["page"] for k in members})
        role = "unique"
        if len(members) > 1:
            if len(pages) >= LAYOUT_MIN_PAGES:
                role = "layout_repeat"
            elif len(pages) == 1:
                role = "same_page_repeat"  # one object drawn twice on a page (clipped pieces)
            else:
                role = "reused"
        first = min(members, key=lambda k: (records[k]["page"], records[k]["idx"]))
        for k in members:
            records[k].update({"dup_group": gid, "dup_count": len(members), "dup_pages": pages,
                               "doc_role": role, "representative": k == first})


def extract_report(pdf_path, name, out_dir, crop_long_side=CROP_LONG_SIDE,
                   jpg_quality=JPG_QUALITY):
    """Whole extraction for one PDF: writes crops/ and {name}_pairs.jsonl into out_dir.
    The jsonl is written to a temporary file and renamed at the end, so an existing
    jsonl always means a finished report (batch runs can skip it).
    Returns (records, number of pages, running-text keys)."""
    out_dir = Path(out_dir)
    crop_dir = out_dir / "crops"
    crop_dir.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(pdf_path)
    pages_blocks = [raw_text_blocks(page) for page in doc]
    running_keys = running_text_keys(pages_blocks)
    records = []
    md5_cache, xmp_cache = {}, {}
    for page in doc:
        records.extend(describe_page(page, name, crop_dir, running_keys, pages_blocks[page.number],
                                     crop_long_side, jpg_quality, md5_cache, xmp_cache))
    mark_repeats(records, crop_dir)

    jsonl = out_dir / f"{name}_pairs.jsonl"
    tmp = out_dir / f"{name}_pairs.jsonl.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(jsonl)
    n_pages = len(doc)
    doc.close()
    return records, n_pages, running_keys


def summary_lines(name, records, n_pages, running_keys):
    n = len(records)
    pages_with = len({r["page"] for r in records})
    with_cap = sum(1 for r in records if r["caption"])
    with_overlay = sum(1 for r in records if r["overlay_text"])
    with_para = sum(1 for r in records if r["paragraphs"])
    full = sum(1 for r in records if r["full_page"])
    with_head = sum(1 for r in records if r["local_heading"])
    raw = sum(len(r["layers"]) for r in records)
    reps = [r for r in records if r["representative"]]
    layout = [r for r in reps if r["doc_role"] == "layout_repeat"]
    reused = [r for r in reps if r["doc_role"] == "reused"]
    same_page = [r for r in reps if r["doc_role"] == "same_page_repeat"]
    return [
        f"{name}: pages={n_pages} pictures={n} on {pages_with} pages | "
        f"caption={with_cap} local_heading={with_head} overlay_text={with_overlay} "
        f"paragraph={with_para} full_page={full} | running texts={len(running_keys)}",
        f"  placements={raw} -> after merging layers={n} -> distinct pictures={len(reps)} "
        f"(layout repeats={len(layout)} covering {sum(r['dup_count'] for r in layout)} placements, "
        f"reused on 2 pages={len(reused)}, drawn twice on one page={len(same_page)})",
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--name", required=True, help="short report name used in file names")
    ap.add_argument("--out", required=True)
    ap.add_argument("--crop-long-side", type=int, default=CROP_LONG_SIDE)
    ap.add_argument("--jpg-quality", type=int, default=JPG_QUALITY)
    args = ap.parse_args()

    records, n_pages, running_keys = extract_report(args.pdf, args.name, args.out,
                                                    args.crop_long_side, args.jpg_quality)
    for line in summary_lines(args.name, records, n_pages, running_keys):
        print(line)
    print(f"wrote {Path(args.out) / (args.name + '_pairs.jsonl')}")


if __name__ == "__main__":
    main()
