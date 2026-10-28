"""Page structure of every page that has a picture (PLAN v2.0 §5 step 2). It feeds step 4
(context_link.py) and gives the grey boxes the annotator clicks in task R.

Input: the PDF (data/raw, read only with pymupdf) and the extraction records
work/extract/{name}_pairs.jsonl (extraction v2: vector gradients already skipped). Reports
without a text layer (image_only in results/download_check_*.json) are skipped and listed.
Output per report: work/page_graph/{name}.jsonl (one JSON line per page that has at least one
picture record) and work/page_graph/{name}_meta.json (font levels, counts, checks, timing).

Version 2 (10/18 evening, after the independent review): tight line boxes, line pitch from
distinct baselines, running = same text at the same place, repeated titles kept, invisible and
overprinted text removed, garbled text flagged, pictures placed in the reading order, heading
levels anchored on section numbers, captions not printed on pictures, problem pages in the HTML.

TEXT ("boxes": one box per paragraph, caption, heading or list item)
  All text is read with pymupdf.TOOLS.set_small_glyph_heights(True) (switched on only while a
  report is read, then restored): line boxes are one font size tall. Without it, 8 of the 15
  development reports have line boxes about 2.85 font sizes tall, the lines of a paragraph
  overlap, and paragraphs came out interleaved or one box per line. MuPDF's blocks, lines and
  text are the same either way (checked on 8 pages); get_text("blocks") gives the same boxes.
  0. clean: a span is dropped when (a) it is invisible: drawn before a picture, gradient or
     opaque fill that covers >= 90% of it (page.get_texttrace() seqno vs page.get_bboxlog()
     index / get_drawings() seqno) AND a pixel test shows nothing changes when only that text is
     removed (render the span, remove the text with a redaction that keeps pictures and
     drawings, render again: mean difference <= 1/255 per channel; e.g. 2882_2025 p18's
     covered title, while 1101_2023 p9's text under a see-through picture stays); or (b) it is
     drawn twice: the same text again at the same place (all four edges within 0.25 x size +
     0.5 pt; overprint, fake bold, '26' drawn three times).
  1. cut: a MuPDF block is cut where its lines sit side by side (two columns in one block),
     where the font size of two consecutive lines differs by more than split_size_jump, after
     a short line that ends a sentence (。！？：), and before a new list item (a bullet glyph,
     ❶-❿ ➊-➓, a heading bar ▎; or '1.', '(2)', '三、' after a sentence end or a short line);
     never between two parts of the same visual line (MuPDF sometimes gives one visual line as
     two line objects on the same baseline). Blocks with vertical text stay whole.
  2. merge (merge_lines): the rule of ep.merge_fragments (same size +-0.5, same left edge +-3
     pt, gap between the line boxes up to 1.2 font sizes) applied top to bottom inside clusters
     of left edges (+-3 pt), not in round(x0) order (x0 233.3 vs 233.7 used to give two
     interleaved boxes), separately for each group of pieces in the same coloured containers
     with the same running flag (a box never crosses a container edge). Two identical texts are
     never joined (table cells with the same value in consecutive rows).
  3. join (join_blocks): blocks whose baselines are one line apart (0.3-2.5 font sizes) with
     the same size and the same left edge, the same centre (centred captions), an indented
     first line, the indented next lines of a list item, or a paragraph that wraps around a
     picture (same right edge, no paragraph end, a picture fills the space between the two
     left edges) -- never at a new list item.
  4. re-cut (paragraph_runs), top to bottom: where the baseline distance to the next piece
     exceeds the line pitch of the paragraph so far by max(1 pt, 0.25 x size), after a short
     line ending a sentence, at a new list item, and between identical texts. Line pitch =
     median distance between DISTINCT visual lines of the paragraph so far (for a single line:
     the next piece's pitch, else the median of the whole joined block). Two line objects on
     one baseline used to give a pitch of ~0 and one box per line (2610_2024 p38); a formula
     with a small pitch above a paragraph used to cut the paragraph per line (1707_2025 p103).
  Fields: id (b000... in top-to-bottom, left-to-right order of the top-left corner; ids do
  not depend on the reading-order rule), bbox, text, size (largest span size), chars
  (non-space characters), lines (visual lines), bold (share of characters in bold fonts:
  span flag 16 or a bold font name; 'DemiLight' is not bold), garbled (share of characters
  that are control characters, private-use characters outside the U+F000-F0FF symbol-font
  area, U+FFFD, CJK extension A, radicals, Yi / Cham / Hangul / Thai: a broken font map),
  is_running, repeat_pages (pages on which the same text is a MuPDF block), slot_pages (pages
  on which the same text up to digits is at this place: high for menus and headers), role,
  order, column, top_column, containers (outer to inner), on_pictures (idx of pictures whose
  visible box holds >= 60% of the box).

RUNNING TEXT (is_running: menus, tabs, headers, footers, page numbers)
  Running keys as in extract_pairs.py: ep._norm() of the MuPDF blocks of ALL pages, repeated on
  >= max(5, 3% of pages) pages (pages without pictures are read with get_text("blocks"), same
  blocks; --verify-running reads every page with "dict" and compares keys and positions). A
  piece or box whose key repeats is running when
    (a) it is in the top or bottom 10% of the page (headers, footers with page numbers), or
    (b) the same text (digits kept) is at the same place (x0, y0 within slot_tolerance pt) on
        >= that many pages (menus, tabs). Text that repeats at moving places ('ACTION
        SPOTLIGHT', '管理方針') is not running.
  EXCEPT a repeated title: 2-50 characters with a word in it (not a roman numeral), digits <=
  40%, size >= repeat_heading_ratio x body size (body size of the not-yet-running pieces), at
  this place (text up to digits, x0/y0 within slot_tolerance) on <= repeat_heading_max_share of
  the report's pages, and not part of a menu (fewer than 2 other repeated pieces on the page
  share its left edge or its vertical centre). So the section title printed on every page of
  its section ('1.1 永續治理與管理', '2.2 食品安全責任', '目標設定') and a chapter title on a divider
  page are headings, while the report title in the header (same place on > 25% of pages), menu
  bars and side menus stay running. Also running: a bare page number (1-3 digits, repeated
  identical tokens collapsed) in the outer 12% of the page, or a roman numeral (valid numeral
  only) in the top or bottom 12% at <= 1.5 x body size.

COLOURED CONTAINERS ("containers")
  Filled paths of page.get_drawings(), cut into sub-paths. A sub-path is a rectangle when it is
  a 're' item, a rectangular 'qu' item, or a run of at most 16 line/curve items whose polygon
  covers >= container_rect_fill of its bounding box (rounded corners pass; circles do not).
  Kept when its area is between container_min_area and container_max_area of the page and it
  is visible: the colour under it (the last earlier rectangle fill / picture / gradient in the
  page.get_bboxlog() order covering >= 98% of it, else the white page) differs by more than
  colour_tolerance after fill opacity; not hidden by a later picture or opaque fill (>= 95%);
  not a tint over a picture (IoU >= 0.6 with a picture record). Gradients are not paths, so
  gradient boxes are not containers. Fields: id, bbox, fill, hex, opacity, area_ratio, under,
  parent (smallest container holding >= 95% of it), depth, panel (holds every non-running box
  of the page, at least 2, or covers >= panel_area of the page: a page-wide panel, constant
  for the 'same container' feature).

FONT LEVELS (per report; {name}_meta.json "font")
  body size = most common span size (0.5 pt bins), weighted by characters, over the
  non-running, non-garbled boxes of the pages with pictures. Heading sizes = rounded sizes >=
  heading_min_ratio x body carried by heading-like boxes (2-50 characters with a word, digits
  <= 40%, not running, not garbled, not on the first or last page) on >= heading_min_pages
  different pages (so a display size used on one or two divider pages is not a level).
  Levels: if some heading size carries section numbers ('4.1 人權': one dot, followed by a
  word) on >= 3 pages, the size with most such pages is h2 (section; sizes within max(0.5 pt,
  5%) join it), every larger size is h1, every smaller heading size h3 ("numbered_anchor"). Otherwise the
  sizes are cut into up to heading_levels groups by optimal 1-D clustering of log(size)
  weighted by pages, largest = h1 ("size_clusters"). Band limits are geometric means of
  neighbouring groups; sizes above h1 are h1, sizes between the threshold and the lowest group
  join it.
  role (first rule that matches):
    garbled   >= garbled_box_share of the characters are garbled;
    note      starts with 註 ※ * 資料來源 來源 Source Note 備註;
    h1/h2/h3  heading-like and size in a level; also h3 at body size: <= 30 characters, no
              sentence end, and numbered ('1.1 ...', '(4) ...', '三、...') or bold (>= 60%,
              <= 2 lines) in a report whose body-size text is <= 25% bold;
    caption   ep.is_caption_like against the body size (<= 120 characters, size <= 1.1 x body,
              not numbered), <= caption_max_lines lines, not the continuation of a paragraph
              (a box of the same size ends directly above it, baselines <= 2 font sizes apart,
              in the same column, with the same left or right edge or without a paragraph
              end), and below / above a picture (>= 60% of the box's width under / over it) or
              beside it (>= 60% of its height, level with its top or bottom edge), the picture
              not a layout repeat (gap <= ep.CAPTION_GAP, overlap <= max(2 pt, 0.3 x height)).
              Text printed ON a picture is not a caption by position (it is in on_pictures);
    body      everything else.

PICTURES ("pictures")
  The work/extract records of the page: idx, crop, bbox (the extraction's), visible_bbox (bbox
  minus the strips that a picture drawn later covers over its full height or width, in the
  page.get_bboxlog() order: rows of overlapping photos; a picture clipped by a mask keeps its
  larger box), doc_role, representative, analysed (列入分析), area_ratio, containers,
  overlay_boxes, order, column, top_column. Captions, on_pictures and the reading order use
  visible_bbox.

READING ORDER (box "order", "column", "top_column"; picture "order"; page "columns")
  Recursive XY-cut over the non-running boxes AND the pictures (not layout repeats, at most
  picture_unit_max_area of the page, no box printed on them), columns first. A coloured
  container holding two or more of them is one unit.
    1. groups separated by >= column_gap pt of white space over the whole region are columns,
       read left to right, each with a new column id;
    2. else bands top to bottom: spanners (the widest units, each >= 50% of the region's
       width, taken until the rest splits into columns) with the units overlapping them by
       >= 50% of the smaller height; without spanners every horizontal white line cuts;
    3. else rows: units whose vertical centres are within half the smaller height form a row,
       rows top to bottom, each left to right.
  column = id of the leaf region (a tree: page "columns" lists each region's parent), so two
  boxes in the same visual column can have different ids; top_column = the first column split
  under the page (0 = page-wide flow), the one to use for "same column". Running boxes come
  last, column -1. A picture's order = the number of boxes read before it (it sits between
  boxes order-1 and order); pictures with text printed on them (backgrounds, photos with a
  label) are not cut units and take the order of their first printed box; others outside the
  cut (layout repeats, pictures > picture_unit_max_area) get null.

PROBLEM CHECKS (page "issues", "issue_boxes"; meta "issues"; HTML section 問題頁)
  centre_inside (box centre inside another box), interleaved (same left edge and size, the
  lines of one box between the lines of the other), stacked_lines (>= 3 single-line boxes with
  the same left edge and size one line apart, different texts: a paragraph cut per line),
  running_title (the largest title-sized box of a non-cover page is running although it is in
  the content area: not in the top/bottom 10% nor the outer 15% at the sides), doubled (text
  like 'AA', not '2020'), duplicate_boxes (same text, IoU >= 0.8), garbled (>= 20% garbled
  characters), hidden_dropped (invisible spans removed). Regression checks on the pages from the
  10/18 review run when those reports are in the run (result file "regression_checks").

--sparse-stats counts, over ALL reports of the catalog, the 列入分析 pictures (representative,
not layout_repeat) on pages with at most sparse_max_chars non-space characters, not counting
cover pages (first and last page of the PDF), with two definitions: (1) all text of the page
(page.get_text("text")); (2) the visible, non-running text of page_graph (the characters of
its non-running boxes: menus, headers, page numbers, invisible and overprinted text left out).
Headline: the reports with a text layer; the no-text reports separately ->
results/page_sparse_text_{stamp}.json.

  python src/page_graph.py --firms 1101 1707 2882 2610 2385 --verify-running   # + reports/page_graph/{firm}.html
  python src/page_graph.py --reports 1101_2025
  python src/page_graph.py --all --workers 8
  python src/page_graph.py --sparse-stats --workers 8
"""
import argparse
import base64
import bisect
import collections
import csv
import hashlib
import html
import json
import math
import random
import re
import statistics
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pymupdf  # noqa: E402

import extract_pairs as ep  # noqa: E402
from meta import run_meta, run_stamp, sha256_file  # noqa: E402

VERSION = 2  # bump when the output changes, so --all recomputes finished reports
DICT_FLAGS = pymupdf.TEXTFLAGS_DICT  # flags of get_text("dict"); used for "blocks"/"text" too
DEFAULTS = {  # used when configs/default.yaml has no value (all are listed there)
    "container_min_area": 0.02, "container_max_area": 0.90, "heading_levels": 3,
    "container_rect_fill": 0.90, "colour_tolerance": 0.02, "inside_container": 0.75,
    "heading_min_ratio": 1.15, "heading_min_pages": 3, "heading_max_chars": 50,
    "split_size_jump": 0.20, "column_gap": 3, "sparse_max_chars": 20,
    "slot_tolerance": 4, "repeat_heading_ratio": 1.5, "repeat_heading_max_share": 0.25,
    "caption_max_lines": 3, "picture_unit_max_area": 0.6, "garbled_box_share": 0.5, "panel_area": 0.8,
}
LEVEL_NAMES = {1: "chapter", 2: "section", 3: "subheading"}
NOTE_PREFIX = ep.NOT_CAPTION_PREFIX + ("備註",)
NUMBERED = re.compile(r"^(?:\d+(?:\.\d+)+|[\(（]\d+[\)）]|[\(（][一二三四五六七八九十]+[\)）]|"
                      r"[一二三四五六七八九十]+、|\d+[、.](?!\d))\s*\S")
SECTION_NO = re.compile(r"^\s*\d{1,2}[.．]\d{1,2}(?![\d.．])\s*(?![億萬千百元噸度倍%％])[一-鿿A-Za-z]")
BULLET = re.compile(r"^\s*[•●■◆◇▶►▸▹▪◼・‧·∙○◎※➀-➓①-⑳❶-❿⚫➢➤✓✔❖]")
HEADING_BAR = re.compile(r"^\s*[▎▍▌▐┃|｜]")
ITEM_START = re.compile(r"^\s*(?:[一二三四五六七八九十]+、|[\(（]?\d{1,2}[\)）.、](?!\d)|[\(（][一二三四五六七八九十]+[\)）])")
PARA_END = re.compile(r"[。！？；：:;!?」』）)]\s*$")
SENT_END = re.compile(r"[。！？：!?:]\s*$")
BOLD_FONT = re.compile(r"(?:semi|demi)-?bold|bold|heavy|black|demi(?!-?light)|w[6-9]\b|bd\b", re.I)
GARBLED = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f฀-࿿⺀-⿿㐀-䶿"  # U+F000-F0FF
                     "ꀀ-꯿가-힯--�\U000f0000-\U0010ffff]")  # = symbol fonts
WORDY = re.compile(r"[一-鿿]|[A-Za-z]{3}")
PAGE_DIGITS = re.compile(r"^[-–—]?(?:p\.?)?\d{1,3}[-–—]?$", re.I)
ROMAN = re.compile(r"^(?=[ivxlc])c{0,3}(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})$", re.I)
OVERLAY_INSIDE = 0.6      # as extract_pairs.py describe_page()
BOLD_BODY_MAX = 0.25      # bold short lines are subheadings only where body-size text is mostly not bold
HUGE = 1e7                # bboxlog entries of clipped gradients have infinite boxes


# ---------------------------------------------------------------- small helpers
def read_pairs(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_yaml(path):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def latest(pattern):
    found = sorted((ROOT / "results").glob(pattern))
    return found[-1] if found else None


def rnd(rect, nd=1):
    return [round(v, nd) for v in rect]


def nchars(text):
    return len("".join(text.split()))


def n_garbled(text):
    return len(GARBLED.findall(text))


def half(size):
    return round(size * 2) / 2


def inside(inner, outer):
    """Share of `inner` (x0, y0, x1, y1) inside `outer`; plain floats, used in the hot loops."""
    w = min(inner[2], outer[2]) - max(inner[0], outer[0])
    h = min(inner[3], outer[3]) - max(inner[1], outer[1])
    a = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return 0.0 if w <= 0 or h <= 0 or a <= 0 else w * h / a


def area(r):
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def box_iou(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    i = w * h
    return i / (area(a) + area(b) - i)


def union(rects):
    r = pymupdf.Rect(rects[0])
    for x in rects[1:]:
        r |= x
    return r


def to_rgb(c):
    if c is None:
        return None
    c = tuple(float(v) for v in c)
    if len(c) == 3:
        return c
    if len(c) == 1:
        return (c[0],) * 3
    if len(c) == 4:  # CMYK
        k = c[3]
        return tuple((1 - v) * (1 - k) for v in c[:3])
    return None


def to_hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in rgb)


def heading_text(t, pg):
    """Text that can be a title: 2..heading_max_chars characters, a word in it, digits <= 40%."""
    n = nchars(t)
    return (2 <= n <= pg["heading_max_chars"] and bool(WORDY.search(t)) and ep.digit_ratio(t) <= 0.4
            and not ROMAN.match(re.sub(r"\s", "", t)))


class small_glyphs:
    """pymupdf.TOOLS.set_small_glyph_heights(True) while a report is read (a process-wide
    switch; the old value is put back, so importing page_graph changes nothing)."""

    def __enter__(self):
        self.old = bool(pymupdf.TOOLS.set_small_glyph_heights())
        pymupdf.TOOLS.set_small_glyph_heights(True)
        return self

    def __exit__(self, *exc):
        pymupdf.TOOLS.set_small_glyph_heights(self.old)
        return False


class _DictPage:
    """Stands in for a page in ep.raw_text_blocks(): returns a get_text("dict") made earlier,
    so each page is read once and the running keys use exactly the extract_pairs.py blocks."""

    def __init__(self, d):
        self.d = d

    def get_text(self, kind, *args, **kwargs):
        assert kind == "dict"
        return self.d


def block_texts(page):
    """Text and box of each block for the running keys on a page without pictures:
    get_text("blocks") with the "dict" flags gives the same blocks and (with small glyph
    heights) the same boxes; ep._norm() drops the whitespace that differs."""
    return [{"text": b[4], "rect": tuple(b[:4])} for b in page.get_text("blocks", flags=DICT_FLAGS)
            if b[6] == 0 and b[4].strip()]


# ---------------------------------------------------------------- coloured containers
def path_rects(path, rect_fill):
    """Sub-paths of one filled path as (Rect, is_rectangle)."""
    out, verts, ctrl, n, last = [], [], [], 0, None

    def flush():
        if len(verts) >= 3:
            xs = [p.x for p in verts + ctrl]
            ys = [p.y for p in verts + ctrl]
            r = pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))
            poly = 0.0
            for a, b in zip(verts, verts[1:] + verts[:1]):
                poly += a.x * b.y - b.x * a.y
            ok = n <= 16 and r.get_area() > 0 and abs(poly) / 2 >= rect_fill * r.get_area()
            out.append((r, ok))

    for it in path["items"]:
        kind = it[0]
        if kind == "re":
            flush()
            verts, ctrl, n, last = [], [], 0, None
            out.append((pymupdf.Rect(it[1]), True))
        elif kind == "qu":
            flush()
            verts, ctrl, n, last = [], [], 0, None
            q = pymupdf.Quad(it[1])
            out.append((q.rect, bool(q.is_rectangular)))
        elif kind in ("l", "c"):
            p0, p1 = pymupdf.Point(it[1]), pymupdf.Point(it[-1])
            if last is None or abs(p0.x - last.x) > 0.5 or abs(p0.y - last.y) > 0.5:
                flush()
                verts, ctrl, n = [p0], [], 0
            verts.append(p1)
            if kind == "c":
                ctrl += [pymupdf.Point(it[2]), pymupdf.Point(it[3])]
            n += 1
            last = p1
    flush()
    return out


def find_containers(page, drawings, bboxlog, pg, pic_boxes=()):
    """Coloured containers of a page (see the module docstring)."""
    prect = page.rect
    parea = prect.get_area()
    layers, cands = [], []  # layers: (order, box, kind, rgb, opacity)
    dropped = collections.Counter()
    for path in drawings:
        rgb = to_rgb(path.get("fill"))
        if rgb is None:
            continue
        op = path.get("fill_opacity")
        op = 1.0 if op is None else float(op)
        seq = path.get("seqno", 0)
        for j, (r, is_rect) in enumerate(path_rects(path, pg["container_rect_fill"])):
            r = pymupdf.Rect(r) & prect
            if r.is_empty:
                continue
            box = tuple(r)
            ratio = r.get_area() / parea
            if not is_rect:
                if ratio >= pg["container_min_area"]:
                    dropped["not_rectangle"] += 1
                continue
            layers.append(((seq, j), box, "colour", rgb, op))
            if ratio < pg["container_min_area"]:
                continue
            if ratio > pg["container_max_area"]:
                dropped["page_background"] += 1
                continue
            cands.append({"order": (seq, j), "box": box, "rgb": rgb, "op": op, "ratio": ratio})
    for k, entry in enumerate(bboxlog):
        kind = entry[0]
        if kind in ("fill-image", "fill-imgmask", "fill-shade"):
            box = tuple(pymupdf.Rect(entry[1]) & prect)
            if area(box) > 0:
                layers.append(((k, -1), box, "gradient" if kind == "fill-shade" else "picture", None, 1.0))
    layers.sort(key=lambda t: t[0])
    orders = [t[0] for t in layers]

    kept = []
    for c in sorted(cands, key=lambda c: c["order"]):
        pos = bisect.bisect_left(orders, c["order"])
        under_kind, under_rgb = "page", (1.0, 1.0, 1.0)
        for order, box, kind, rgb, _ in reversed(layers[:pos]):
            if inside(c["box"], box) >= 0.98:
                under_kind, under_rgb = kind, rgb
                break
        if under_kind in ("page", "colour"):
            diff = c["op"] * max(abs(a - b) for a, b in zip(c["rgb"], under_rgb))
            if diff <= pg["colour_tolerance"]:
                dropped["same_colour_as_under"] += 1
                continue
        hidden = any(inside(c["box"], box) >= 0.95 and (kind == "picture" or (kind == "colour" and op >= 0.99))
                     for order, box, kind, rgb, op in layers[pos + 1:])
        if hidden:
            dropped["hidden_later"] += 1
            continue
        if under_kind == "picture" and any(box_iou(c["box"], tuple(p)) >= 0.6 for p in pic_boxes):
            dropped["picture_tint"] += 1
            continue
        c["under"] = under_kind
        kept.append(c)

    kept.sort(key=lambda c: (round(c["box"][1], 1), c["box"][0]))
    for i, c in enumerate(kept):
        c["id"] = f"c{i}"
    for c in kept:
        holders = [o for o in kept if o is not c and area(o["box"]) > area(c["box"]) * 1.001
                   and inside(c["box"], o["box"]) >= 0.95]
        c["parent"] = min(holders, key=lambda o: area(o["box"]))["id"] if holders else None
    by_id = {c["id"]: c for c in kept}
    for c in kept:
        d, p = 0, c["parent"]
        while p is not None and d < 50:
            d, p = d + 1, by_id[p]["parent"]
        c["depth"] = d
    return kept, dropped


def container_json(c):
    return {"id": c["id"], "bbox": rnd(c["box"]), "fill": [round(v, 3) for v in c["rgb"]], "hex": to_hex(c["rgb"]),
            "opacity": round(c["op"], 3), "area_ratio": round(c["ratio"], 4), "under": c["under"],
            "parent": c["parent"], "depth": c["depth"], "panel": c.get("panel", False)}


def containers_of(box, containers, thr):
    """Ids of the containers holding >= thr of `box`, outer to inner."""
    hit = [c for c in containers if inside(box, c["box"]) >= thr]
    return tuple(c["id"] for c in sorted(hit, key=lambda c: (c["depth"], -area(c["box"]))))


# ---------------------------------------------------------------- invisible text
def hidden_text_spans(page, drawings, bboxlog, cache):
    """Spans of text that a later picture, gradient or opaque fill covers and that do not show
    on the rendered page (see the module docstring). Returns [(rect, text without spaces)].
    `cache` holds a second copy of the document (opened on first use): the pixel test removes
    text from it with a redaction, the copy that is read is not changed."""
    covers = [(k, tuple(e[1])) for k, e in enumerate(bboxlog)
              if e[0] in ("fill-image", "fill-imgmask", "fill-shade") and max(abs(v) for v in e[1]) < HUGE]
    for d in drawings:
        op = d.get("fill_opacity")
        if d.get("fill") is not None and (op is None or op >= 0.99):
            covers.append((d.get("seqno", 0), tuple(d["rect"])))
    covers = sorted(((k, b) for k, b in covers if area(b) >= 20), key=lambda t: t[0])
    if not covers:
        return []
    seqs = [k for k, _ in covers]
    cands = []
    for t in page.get_texttrace():
        if t["type"] not in (0, 1) or t.get("opacity", 1) == 0:  # never invisible (OCR) or clip text
            continue
        r = tuple(t["bbox"])
        a = area(r)
        if a <= 0:
            continue
        later = covers[bisect.bisect_right(seqs, t["seqno"]):]
        if not any(area(b) >= 0.9 * a and inside(r, b) >= 0.9 for _, b in later):
            continue
        text = "".join(chr(c[0]) for c in t["chars"])
        if text.strip():
            cands.append((r, re.sub(r"\s", "", text)))
    if not cands:
        return []
    cache["candidates"] = cache.get("candidates", 0) + len(cands)
    try:
        if cache.get("doc") is None:
            cache["doc"] = pymupdf.open(cache["pdf"])
        cp = cache["doc"][page.number]
        mat = pymupdf.Matrix(2, 2)
        before = [cp.get_pixmap(matrix=mat, clip=pymupdf.Rect(r), colorspace=pymupdf.csRGB, alpha=False) for r, _ in cands]
        for r, _ in cands:
            cp.add_redact_annot(pymupdf.Rect(r), fill=False)
        cp.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                            text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        out = []
        for (r, text), b in zip(cands, before):
            a = cp.get_pixmap(matrix=mat, clip=pymupdf.Rect(r), colorspace=pymupdf.csRGB, alpha=False)
            sa, sb = a.samples, b.samples
            if len(sa) != len(sb) or not sa:
                continue
            if sum(abs(x - y) for x, y in zip(sa, sb)) / len(sa) <= 1.0:
                out.append((r, text))
        return out
    except Exception as e:  # pixel test failed: keep the text
        cache["errors"] = cache.get("errors", 0) + 1
        cache["last_error"] = f"page {page.number + 1}: {type(e).__name__}: {e}"
        return []


def span_hidden(span, text, hidden):
    t = re.sub(r"\s", "", text)
    bb = span["bbox"]
    for r, ht in hidden:
        if (t in ht or ht in t) and inside(bb, (r[0] - 1, r[1] - 1, r[2] + 1, r[3] + 1)) >= 0.6:
            return True
    return False


# ---------------------------------------------------------------- text pieces
def line_text(line):
    return "".join(s["text"] for s in line["spans"]).strip()


def line_size(line):
    sizes = [s["size"] for s in line["spans"] if s["text"].strip()]
    return max(sizes) if sizes else 0.0


def line_baseline(line):
    sp = [s for s in line["spans"] if s["text"].strip()]
    return max(s["origin"][1] for s in sp) if sp else line["bbox"][3]


def visual_lines(lines):
    """MuPDF lines grouped into visual lines (baselines within 0.3 x size), top to bottom:
    [(baseline, x0, x1, size)]."""
    items = sorted((line_baseline(ln), ln["bbox"][0], ln["bbox"][2], line_size(ln)) for ln in lines)
    out = []
    for bl, x0, x1, sz in items:
        if out and bl - out[-1][0] <= 0.3 * max(sz, out[-1][3], 1):
            o = out[-1]
            out[-1] = (o[0], min(o[1], x0), max(o[2], x1), max(o[3], sz))
        else:
            out.append((bl, x0, x1, sz))
    return out


def starts_item(text, prev_text, prev_short=False):
    """`text` starts a new list item: a bullet glyph or heading bar, or a number marker
    ('1.', '(2)', '三、') after a sentence end or a short line."""
    if BULLET.match(text) or HEADING_BAR.match(text):
        return True
    return bool(ITEM_START.match(text) and (prev_short or PARA_END.search(prev_text)))


def split_block(block, jump):
    """Lines of one MuPDF block cut into pieces (see the module docstring); [lines] if not cut."""
    lines = [ln for ln in block["lines"] if line_text(ln)]
    if len(lines) <= 1 or any(abs(ln["dir"][1]) > 0.1 for ln in lines):
        return [lines]
    n = len(lines)
    rects = [ln["bbox"] for ln in lines]
    bls = [line_baseline(ln) for ln in lines]
    sizes = [line_size(ln) for ln in lines]
    vis_x1 = [max(rects[j][2] for j in range(n) if abs(bls[j] - bls[i]) <= 0.3 * max(sizes[i], 1)) for i in range(n)]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            same_line = abs(bls[i] - bls[j]) <= 0.3 * max(sizes[i], sizes[j], 1)
            if min(rects[i][2], rects[j][2]) - max(rects[i][0], rects[j][0]) > 0 or (
                    same_line and abs(min(rects[i][2], rects[j][2]) - max(rects[i][0], rects[j][0])) <= 0.6 * max(sizes[i], 1)):
                parent[find(i)] = find(j)  # overlapping in x, or the next part of the same visual line (not a column gap)
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    pieces = []
    for members in sorted(groups.values(), key=lambda m: m[0]):
        right = max(rects[k][2] for k in members)
        run = [members[0]]
        for k in members[1:]:
            prev = run[-1]
            a, b = sizes[prev], sizes[k]
            if abs(bls[prev] - bls[k]) <= 0.3 * max(a, b, 1):  # same visual line: never cut
                run.append(k)
                continue
            size_jump = a and b and max(a, b) / min(a, b) > 1 + jump
            short = vis_x1[prev] < right - 2 * max(a, 1)
            ended = short and SENT_END.search(line_text(lines[prev]))  # short line ending a sentence
            if size_jump or ended or starts_item(line_text(lines[k]), line_text(lines[prev]), short):
                pieces.append(run)
                run = [k]
            else:
                run.append(k)
        pieces.append(run)
    return [[lines[k] for k in p] for p in pieces]


def span_bold(span):
    return bool(span["flags"] & 16) or bool(BOLD_FONT.search(span.get("font", "")))


def clean_block(b, hidden, seen, stats, dropped):
    """The block without invisible spans and without spans drawn a second time at the same place;
    None if nothing is left. Invisible spans are added to `dropped` as (bbox, text)."""
    lines, changed = [], False
    for ln in b["lines"]:
        spans = []
        for s in ln["spans"]:
            t = s["text"].strip()
            if t:
                if hidden and span_hidden(s, t, hidden):
                    stats["hidden_spans"] += 1
                    stats["hidden_chars"] += nchars(t)
                    dropped.append((tuple(s["bbox"]), t))
                    changed = True
                    continue
                tol = 0.25 * s["size"] + 0.5
                bb = s["bbox"]
                if any(max(abs(u - v) for u, v in zip(bb, o)) <= tol for o in seen[t]):
                    stats["duplicate_spans"] += 1
                    stats["duplicate_chars"] += nchars(t)
                    changed = True
                    continue
                seen[t].append(bb)
            spans.append(s)
        filled = [s for s in spans if s["text"].strip()]
        if not filled:
            continue
        if len(spans) != len(ln["spans"]):
            ln = dict(ln, spans=spans, bbox=tuple(union([s["bbox"] for s in filled])))
        lines.append(ln)
    if not lines:
        return None
    bbox = tuple(union([ln["bbox"] for ln in lines])) if changed else b["bbox"]
    return {"type": 0, "lines": lines, "bbox": bbox}


def page_pieces(d, jump, hidden):
    """Text pieces of one page from its get_text("dict"); returns (pieces, blocks cut, clean stats,
    invisible spans dropped)."""
    pieces, n_cut, stats, dropped = [], 0, collections.Counter(), []
    seen = collections.defaultdict(list)
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        raw_text = ep.block_text(b)
        if not raw_text:
            continue
        cb = clean_block(b, hidden, seen, stats, dropped)
        if cb is None:
            continue
        parts = split_block(cb, jump)
        if len(parts) > 1:
            n_cut += 1
        for lines in parts:
            sub = {"lines": lines}
            t = ep.block_text(sub)
            if not t:
                continue
            vl = visual_lines(lines)
            spans = [(s["size"], nchars(s["text"]), span_bold(s)) for ln in lines for s in ln["spans"] if s["text"].strip()]
            pieces.append({"rect": union([ln["bbox"] for ln in lines]), "text": t, "size": ep.block_font_size(sub),
                           "raw_text": raw_text, "raw_rect": pymupdf.Rect(b["bbox"]), "n_lines": len(vl), "spans": spans,
                           "baselines": [v[0] for v in vl], "last_x1": vl[-1][2],
                           "first_cx": (vl[0][1] + vl[0][2]) / 2, "last_cx": (vl[-1][1] + vl[-1][2]) / 2})
    return pieces, n_cut, stats, dropped


# ---------------------------------------------------------------- running text
def exact_key(text):
    return re.sub(r"\s", "", text)[:60]


class Repeats:
    """Texts that repeat over the pages of a report: ep's running keys (digits removed) and, for
    the exact text (digits kept), on which pages and where it is."""

    def __init__(self, pages_raw, tol):
        self.keys = ep.running_text_keys(pages_raw)
        self.n_pages = len(pages_raw)
        self.thr = max(5, int(0.03 * len(pages_raw)))  # as ep.running_text_keys
        self.tol = tol
        self.pos = collections.defaultdict(list)
        self.npos = collections.defaultdict(list)
        for i, blocks in enumerate(pages_raw):
            for b in blocks:
                k = exact_key(b["text"])
                if k:
                    self.pos[k].append((i, b["rect"][0], b["rect"][1]))
                nk = ep._norm(b["text"])
                if nk in self.keys:
                    self.npos[nk].append((i, b["rect"][0], b["rect"][1]))
        self.pages = {k: len({p for p, _, _ in v}) for k, v in self.pos.items()}

    def exact_pages(self, text):
        return self.pages.get(exact_key(text), 0)

    def _at(self, occ, rect):
        return len({p for p, x, y in occ if abs(x - rect[0]) <= self.tol and abs(y - rect[1]) <= self.tol})

    def slot_pages(self, text, rect):
        """Pages on which the same text is at the same place (x0, y0 within tol)."""
        k = exact_key(text)
        if self.pages.get(k, 0) < self.thr:
            return 0
        return self._at(self.pos[k], rect)

    def norm_slot_pages(self, text, rect):
        """Pages on which the same text up to digits is at the same place."""
        return self._at(self.npos.get(ep._norm(text), ()), rect)

    def signature(self):
        return {"keys": sorted(self.keys),
                "pos": sorted((k, p, round(x, 1), round(y, 1)) for k, v in self.pos.items() if self.pages[k] >= self.thr
                              for p, x, y in v)}


def running_kind(texts, rect, rep, page_h):
    """'band' (key repeats, top or bottom 10% of the page), 'slot' (same text at the same place on
    many pages) or None; texts = [(text, rect)] (the MuPDF block and the piece)."""
    if not any(ep._norm(t) in rep.keys for t, _ in texts):
        return None, None
    if rect.y1 <= 0.1 * page_h or rect.y0 >= 0.9 * page_h:
        return "band", texts[-1][0]
    for t, r in texts:
        if rep.slot_pages(t, r) >= rep.thr:
            return "slot", t
    return None, None


def repeated_heading(text, size, rect, trigger, rep, body, others, pg):
    """A title repeated on the pages of its section, not navigation (see the module docstring).
    `others`: rects of the other repeated pieces on the page (menu test)."""
    if not body or not heading_text(text, pg) or size < pg["repeat_heading_ratio"] * body:
        return False
    if rep.norm_slot_pages(text, rect) > pg["repeat_heading_max_share"] * rep.n_pages:
        return False  # at this place on many pages: the report title in the header, a menu
    yc = (rect.y0 + rect.y1) / 2
    aligned = sum(1 for r in others if r is not rect and (abs(r.x0 - rect.x0) <= 3 or abs((r.y0 + r.y1) / 2 - yc) <= 3))
    return aligned < 2


def is_page_number(text, rect, size, body, page_w, page_h):
    toks = text.split()
    t = toks[0] if toks and all(x == toks[0] for x in toks) else re.sub(r"\s", "", text)
    if PAGE_DIGITS.match(t):
        return rect.y1 <= 0.12 * page_h or rect.y0 >= 0.88 * page_h or rect.x1 <= 0.12 * page_w or rect.x0 >= 0.88 * page_w
    if ROMAN.match(t):
        return (rect.y1 <= 0.12 * page_h or rect.y0 >= 0.88 * page_h) and size <= 1.5 * (body or size)
    return False


def body_size(items, garbled_max=1.0):
    chars = collections.Counter()
    for it in items:
        if it.get("garbled_share", 0) < garbled_max:
            for size, n, _ in it["spans"]:
                chars[half(size)] += n
    return max(chars, key=lambda s: (chars[s], -s)) if chars else None


def decide_running(work, rep, pg):
    """Running flag of every piece; returns the body size used for the repeated-title test."""
    for w in work.values():
        for pc in w["pieces"]:
            pc["kind"], pc["trigger"] = running_kind([(pc["raw_text"], pc["raw_rect"]), (pc["text"], pc["rect"])],
                                                     pc["rect"], rep, w["h"])
    body0 = body_size([pc for w in work.values() for pc in w["pieces"] if pc["kind"] is None])
    for w in work.values():
        others = [pc["rect"] for pc in w["pieces"] if pc["kind"]]
        for pc in w["pieces"]:
            pc["repeated_heading"] = bool(pc["kind"]) and repeated_heading(pc["text"], pc["size"], pc["rect"], pc["trigger"],
                                                                          rep, body0, others, pg)
            pc["running"] = ((pc["kind"] is not None and not pc["repeated_heading"])
                             or is_page_number(pc["text"], pc["rect"], pc["size"], body0, w["w"], w["h"]))
        w["others"] = others
    return body0


# ---------------------------------------------------------------- paragraph boxes
def next_line(a, b, size):
    """b's first line follows a's last line: baselines 0.3-2.5 font sizes apart."""
    pitch = b["baselines"][0] - a["baselines"][-1]
    return 0.3 * size < pitch <= 2.5 * size


def new_item(a, b):
    return starts_item(b["text"], a["text"])


def merge_lines(grp):
    """ep.merge_fragments' rule (same size +-0.5, same left edge +-3 pt, gap between the boxes
    up to 1.2 font sizes), top to bottom inside clusters of left edges (+-3 pt). Returns lists
    of pieces."""
    clusters = []
    for p in sorted(grp, key=lambda p: p["rect"].x0):
        if clusters and p["rect"].x0 - clusters[-1][0]["rect"].x0 <= 3:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    blocks = []
    for cl in clusters:
        cur, cur_y1 = None, None
        for p in sorted(cl, key=lambda p: (p["baselines"][0], p["rect"].x0)):
            if cur is not None:
                s = max(p["size"], 1)
                gap = p["rect"].y0 - cur_y1
                if (abs(cur[0]["size"] - p["size"]) <= 0.5 and -0.3 * s <= gap <= 1.2 * s
                        and p["baselines"][0] - cur[-1]["baselines"][-1] > 0.3 * s and p["text"] != cur[-1]["text"]):
                    cur.append(p)
                    cur_y1 = max(cur_y1, p["rect"].y1)
                    continue
            cur, cur_y1 = [p], p["rect"].y1
            blocks.append(cur)
    return blocks


def wraps_around_picture(a, b, pics):
    """b continues a's paragraph around a picture: same right edge, a does not end a paragraph,
    and a picture fills the space between the two left edges next to the indented line."""
    if abs(a["rect"].x1 - b["rect"].x1) > 3 or PARA_END.search(a["text"]):
        return False
    lo, hi = sorted((a["rect"].x0, b["rect"].x0))
    if hi - lo < 20:
        return False
    line = b["rect"] if b["rect"].x0 > a["rect"].x0 else a["rect"]
    return any(p[0] >= lo - 3 and hi - 30 <= p[2] <= hi + 3 and min(p[3], line.y1) - max(p[1], line.y0) > 0 for p in pics)


def join_blocks(blocks, pics=()):
    """Join blocks whose lines follow each other (next_line) with the same font size and the same
    left edge, the same centre (centred captions), an indented first line above full lines
    with the same right edge, the indented next lines of a list item, or a paragraph wrapping
    around a picture; never at a new item."""
    def size(bl):
        return bl[0]["size"]

    out = []
    for bl in sorted(blocks, key=lambda bl: (bl[0]["baselines"][0], bl[0]["rect"].x0)):
        target = None
        for prev in out:
            a, b = prev[-1], bl[0]
            if (abs(size(prev) - size(bl)) > 0.5 or not next_line(a, b, size(bl)) or new_item(a, b)
                    or a["text"] == b["text"]):
                continue
            same_left = abs(prev[0]["rect"].x0 - b["rect"].x0) <= 3
            same_centre = abs(a["last_cx"] - b["first_cx"]) <= 3 and ep.horizontal_overlap(a["rect"], b["rect"]) >= 0.5
            indent = (sum(x["n_lines"] for x in prev) == 1 and 0 < a["rect"].x0 - b["rect"].x0 <= 4 * size(bl)
                      and abs(a["rect"].x1 - b["rect"].x1) <= max(3, size(bl)))  # indented first line
            marker = BULLET.match(prev[0]["text"]) or ITEM_START.match(prev[0]["text"]) or HEADING_BAR.match(prev[0]["text"])
            hanging = marker and 0 < b["rect"].x0 - prev[0]["rect"].x0 <= 4 * size(bl)  # list item's next lines
            if same_left or same_centre or indent or hanging or wraps_around_picture(a, b, pics):
                target = prev
                break
        if target is None:
            out.append(list(bl))
        else:
            target += bl
    return out


def inner_diffs(m, size):
    return [d for d in (b - a for a, b in zip(m["baselines"], m["baselines"][1:])) if d > 0.3 * size]


def paragraph_runs(members, size):
    """Cut a joined block, top to bottom, where the baseline distance to the next piece is clearly
    larger than the line pitch of the paragraph so far (median distance between its distinct
    baselines; for a single line, the next piece's own pitch or the median of the whole block),
    after a short line ending a sentence, at a new list item, or between two identical texts
    (table cells with the same value in consecutive rows)."""
    if len(members) <= 1:
        return [members]
    between = [b["baselines"][0] - a["baselines"][-1] for a, b in zip(members, members[1:])]
    every = [d for m in members for d in inner_diffs(m, size)] + [g for g in between if g > 0.3 * size]
    block_pitch = statistics.median(every) if every else None
    tol = max(1.0, 0.25 * size)
    right = max(m["rect"].x1 for m in members)
    runs = [[members[0]]]
    diffs = inner_diffs(members[0], size)
    for g, m in zip(between, members[1:]):
        a = runs[-1][-1]
        ended = a["last_x1"] < right - 2 * size and SENT_END.search(a["text"])  # short line ending a sentence
        ref = statistics.median(diffs) if diffs else (statistics.median(inner_diffs(m, size)) if inner_diffs(m, size)
                                                       else block_pitch)
        if (ref is not None and g > ref + tol) or ended or new_item(a, m) or a["text"] == m["text"]:
            runs.append([m])
            diffs = inner_diffs(m, size)
        else:
            runs[-1].append(m)
            diffs = diffs + ([g] if g > 0.3 * size else []) + inner_diffs(m, size)
    return runs


def merge_pieces(w, rep, containers, pg, body0):
    """Paragraph boxes of one page: merge_lines() within each (running, containers) group, then
    join_blocks() and paragraph_runs()."""
    pieces, page_h = w["pieces"], w["h"]
    for pc in pieces:
        pc["in"] = containers_of(tuple(pc["rect"]), containers, pg["inside_container"])
    groups = collections.defaultdict(list)
    for pc in pieces:
        groups[(pc["running"], pc["in"])].append(pc)
    boxes, stats = [], collections.Counter()
    for (running, cont), grp in groups.items():
        blocks = merge_lines(grp)
        stats["merged"] += len(grp) - len(blocks)
        joined = join_blocks(blocks, w.get("pic_boxes", ()))
        stats["joined"] += len(blocks) - len(joined)
        for members in joined:
            runs = paragraph_runs(members, members[0]["size"])
            stats["cut"] += len(runs) - 1
            for run in runs:
                rect = union([x["rect"] for x in run])
                t = "".join(x["text"] for x in run)
                spans = [s for x in run for s in x["spans"]]
                kind, trig = running_kind([(t, rect)], rect, rep, page_h) if len(run) > 1 else (None, None)
                run_box = running or (kind is not None and not repeated_heading(t, run[0]["size"], rect, trig, rep, body0,
                                                                                 w["others"], pg))
                n = nchars(t)
                boxes.append({"rect": rect, "text": t, "size": run[0]["size"],
                              "bold": sum(c for _, c, bb in spans if bb) / max(1, sum(c for _, c, _ in spans)),
                              "is_running": bool(run_box), "containers": list(cont),
                              "repeated_title": any(x["repeated_heading"] for x in run),
                              "repeat_pages": rep.exact_pages(t) or max(rep.exact_pages(x["text"]) for x in run),
                              "slot_pages": max([rep.norm_slot_pages(t, rect)] + [rep.norm_slot_pages(x["raw_text"], x["raw_rect"])
                                                                                 for x in run]),
                              "n_lines": sum(x["n_lines"] for x in run), "spans": spans,
                              "baselines": sorted(b for x in run for b in x["baselines"]),
                              "chars": n, "garbled_share": n_garbled(t) / max(1, n)})
    return boxes, stats


# ---------------------------------------------------------------- font levels
def jenks(values, weights, k):
    """Optimal split of sorted values into k consecutive groups (min. weighted within-group
    sum of squares). Returns a list of index lists."""
    n = len(values)
    W, S, Q = [0.0], [0.0], [0.0]
    for v, w in zip(values, weights):
        W.append(W[-1] + w)
        S.append(S[-1] + w * v)
        Q.append(Q[-1] + w * v * v)

    def cost(i, j):  # values[i:j]
        w = W[j] - W[i]
        s = S[j] - S[i]
        return (Q[j] - Q[i]) - s * s / w if w > 0 else 0.0

    inf = float("inf")
    dp = [[inf] * (n + 1) for _ in range(k + 1)]
    back = [[0] * (n + 1) for _ in range(k + 1)]
    dp[0][0] = 0.0
    for m in range(1, k + 1):
        for j in range(m, n + 1):
            for i in range(m - 1, j):
                v = dp[m - 1][i] + cost(i, j)
                if v < dp[m][j] - 1e-12:
                    dp[m][j], back[m][j] = v, i
    groups, j = [], n
    for m in range(k, 0, -1):
        i = back[m][j]
        groups.append(list(range(i, j)))
        j = i
    return groups[::-1]


def font_hierarchy(boxes, pg):
    """Body size and heading levels of a report (see the module docstring). boxes carry page and
    cover."""
    gmax = pg["garbled_box_share"]
    usable = [b for b in boxes if not b["is_running"] and b["garbled_share"] < gmax]
    chars, bold = collections.Counter(), collections.Counter()
    for b in usable:
        for size, n, bb in b["spans"]:
            chars[half(size)] += n
            bold[half(size)] += n if bb else 0
    if not chars:
        return {"body": None, "threshold": None, "bands": [], "size_chars": {}, "body_bold_share": None,
                "method": None, "anchor": None}
    body = max(chars, key=lambda s: (chars[s], -s))
    thr = body * pg["heading_min_ratio"]
    pages, nbox, numbered = collections.defaultdict(set), collections.Counter(), collections.defaultdict(set)
    for b in usable:
        s = half(b["size"])
        if b["cover"] or s < thr or not heading_text(b["text"], pg):
            continue
        pages[s].add(b["page"])
        nbox[s] += 1
        if SECTION_NO.match(b["text"]):
            numbered[s].add(b["page"])
    sizes = sorted(s for s in pages if len(pages[s]) >= pg["heading_min_pages"])
    cand = [s for s in sizes if len(numbered[s]) >= 3]
    anchor = max(cand, key=lambda s: (len(numbered[s]), s)) if cand else None
    groups = []
    if anchor is not None:
        method = "numbered_anchor"
        tol = max(0.5, 0.05 * anchor)  # sizes within 5% of the numbered size are the same level
        for lvl, g in ((1, [s for s in sizes if s > anchor + tol]), (2, [s for s in sizes if abs(s - anchor) <= tol]),
                       (3, [s for s in sizes if s < anchor - tol])):
            if g:
                groups.append((lvl, g))
    elif sizes:
        method = "size_clusters"
        k = min(int(pg["heading_levels"]), len(sizes))
        idx = jenks([math.log(s) for s in sizes], [len(pages[s]) for s in sizes], k)
        groups = [(lvl, [sizes[i] for i in g]) for lvl, g in enumerate(idx[::-1], 1)]
    else:
        method = None
    bands = []
    for lvl, g in groups:
        bands.append({"level": lvl, "name": LEVEL_NAMES.get(lvl, f"level{lvl}"), "min": g[0], "max": g[-1],
                      "sizes": {str(s): len(pages[s]) for s in g}, "pages": len(set().union(*(pages[s] for s in g))),
                      "boxes": sum(nbox[s] for s in g)})
    for i, b in enumerate(bands):
        b["hi"] = None if i == 0 else round(math.sqrt(bands[i - 1]["min"] * b["max"]), 2)
        b["lo"] = round(thr, 2) if i == len(bands) - 1 else round(math.sqrt(b["min"] * bands[i + 1]["max"]), 2)
    total = sum(chars.values())
    return {"body": body, "threshold": round(thr, 2), "bands": bands, "method": method, "anchor": anchor,
            "body_bold_share": round(bold[body] / chars[body], 3),
            "size_chars": {str(s): n for s, n in sorted(chars.items(), key=lambda kv: -kv[1]) if n / total >= 0.001},
            "heading_size_pages": {str(s): len(pages[s]) for s in sorted(pages)},
            "section_numbered_pages": {str(s): len(numbered[s]) for s in sorted(numbered)}}


def heading_level(size, font):
    s = half(size)
    for b in font["bands"]:
        if s >= b["lo"]:
            return b["level"]
    return None


# ---------------------------------------------------------------- pictures
def visible_rects(images, pics, max_area):
    """Picture box minus the strips that a picture drawn LATER covers over the full height (or
    width) of it (rows of overlapping photos; page.get_bboxlog() order). A picture's draw index is
    the last image entry lying >= 90% inside its record box and covering >= 50% of it. Clipped
    pictures cannot be fixed this way (the log has the unclipped box)."""
    for p in pics:
        r = p["bbox_rect"]
        own = [k for k, b in images if area(b) >= 0.5 * area(tuple(r)) and inside(b, tuple(r)) >= 0.9]
        p["draw"] = max(own) if own else None
    cutters = [p for p in pics if p["draw"] is not None and p["rec"]["doc_role"] != "layout_repeat"
               and p["area_ratio"] <= max_area]
    for p in pics:
        v = pymupdf.Rect(p["bbox_rect"])
        if p["draw"] is not None:
            for q in cutters:
                if q is p or q["draw"] <= p["draw"]:
                    continue
                b = q["bbox_rect"]
                ov_w = min(v.x1, b.x1) - max(v.x0, b.x0)
                ov_h = min(v.y1, b.y1) - max(v.y0, b.y0)
                if ov_w <= 0 or ov_h <= 0:
                    continue
                if ov_h >= 0.9 * v.height:
                    if b.x0 > v.x0 and b.x1 >= v.x1 - 1:
                        v.x1 = b.x0
                    elif b.x1 < v.x1 and b.x0 <= v.x0 + 1:
                        v.x0 = b.x1
                elif ov_w >= 0.9 * v.width:
                    if b.y0 > v.y0 and b.y1 >= v.y1 - 1:
                        v.y1 = b.y0
                    elif b.y1 < v.y1 and b.y0 <= v.y0 + 1:
                        v.y0 = b.y1
        if v.width < 0.2 * p["bbox_rect"].width or v.height < 0.2 * p["bbox_rect"].height:
            v = pymupdf.Rect(p["bbox_rect"])  # mostly covered: keep the record box
        p["rect"] = v


# ---------------------------------------------------------------- roles
def caption_attached(r, size, pics):
    """Below/above a picture, or beside it and level with its top or bottom edge (within two
    font sizes); gap up to ep.CAPTION_GAP, overlap up to max(2 pt, 0.3 x box height). Not for
    text printed on the picture, nor for layout repeats."""
    for p in pics:
        if p["rec"]["doc_role"] == "layout_repeat":
            continue
        pr = p["rect"]
        if ep.inside_fraction(r, pr) > 0.5:
            continue
        gaps = []
        ox = min(r.x1, pr.x1) - max(r.x0, pr.x0)
        oy = min(r.y1, pr.y1) - max(r.y0, pr.y0)
        if ep.horizontal_overlap(r, pr) >= 0.5 and ox >= 0.6 * r.width:  # mostly under / over the picture
            gaps += [r.y0 - pr.y1, pr.y0 - r.y1]
        level = abs(r.y1 - pr.y1) <= 2 * size or abs(r.y0 - pr.y0) <= 2 * size
        if ep.vertical_overlap(r, pr) >= 0.5 and oy >= 0.6 * r.height and level:
            gaps += [r.x0 - pr.x1, pr.x0 - r.x1]
        overlap = max(2.0, 0.3 * r.height)
        if any(-overlap <= g <= ep.CAPTION_GAP for g in gaps):
            return True
    return False


def continues_paragraph(b, boxes):
    """A box of the same size ends directly above b (baselines <= 2 font sizes apart), in the same
    column (x overlap >= 50%), with the same left edge or the same right edge or without a
    paragraph end: b is the rest of that paragraph, not a caption."""
    for a in boxes:
        if a is b or a["is_running"]:
            continue
        if (abs(a["size"] - b["size"]) <= 0.5 and ep.horizontal_overlap(a["rect"], b["rect"]) >= 0.5
                and 0 < b["baselines"][0] - a["baselines"][-1] <= 2 * b["size"] and a["rect"].y1 <= b["rect"].y0 + 1
                and (abs(a["rect"].x0 - b["rect"].x0) <= 3 or abs(a["rect"].x1 - b["rect"].x1) <= 3
                     or not PARA_END.search(a["text"]))):
            return True
    return False


def box_role(b, font, pics, boxes, pg):
    t = b["text"]
    body = font["body"] or b["size"]
    if b["garbled_share"] >= pg["garbled_box_share"]:
        return "garbled"
    if t.startswith(NOTE_PREFIX):
        return "note"
    n = nchars(t)
    lvl = heading_level(b["size"], font) if font["bands"] else None
    if lvl and heading_text(t, pg):
        return f"h{lvl}"
    short_title = n <= 30 and b["size"] >= body - 0.25 and ep.digit_ratio(t) <= 0.4 and not PARA_END.search(t) and WORDY.search(t)
    bold_title = (b["bold"] >= 0.6 and b["n_lines"] <= 2 and not BULLET.match(t)
                  and (font.get("body_bold_share") or 0) <= BOLD_BODY_MAX)
    if short_title and (NUMBERED.match(t) or bold_title):
        return "h3"
    if (b["n_lines"] <= pg["caption_max_lines"] and ep.is_caption_like({"text": t, "size": b["size"], "running": False}, body)
            and not continues_paragraph(b, boxes) and caption_attached(b["rect"], b["size"], pics)):
        return "caption"
    return "body"


# ---------------------------------------------------------------- reading order
class _Columns:
    def __init__(self):
        self.items = [{"id": 0, "parent": None, "bbox": None}]

    def new(self, parent, rects):
        cid = len(self.items)
        self.items.append({"id": cid, "parent": parent,
                           "bbox": rnd((min(r.x0 for r in rects), min(r.y0 for r in rects),
                                        max(r.x1 for r in rects), max(r.y1 for r in rects)))})
        return cid


def split_gaps(items, axis, gap):
    """Groups of (rect, payload) separated by white space along x ('x', at least `gap` pt) or y."""
    lo, hi = ("x0", "x1") if axis == "x" else ("y0", "y1")
    srt = sorted(items, key=lambda t: getattr(t[0], lo))
    groups, cur_hi = [], None
    for t in srt:
        a = getattr(t[0], lo)
        if cur_hi is None or (a - cur_hi >= gap if axis == "x" else a >= cur_hi - 1.0):
            groups.append([t])
            cur_hi = getattr(t[0], hi)
        else:
            groups[-1].append(t)
            cur_hi = max(cur_hi, getattr(t[0], hi))
    return groups


SPANNER_WIDTH = 0.5  # a unit at least this share of the region's width may span columns


def v_overlap(a0, a1, b0, b1):
    return min(a1, b1) - max(a0, b0)


def spanner_bands(items, spanners):
    """Bands top to bottom: each spanner with the units overlapping it vertically by >= 50% of the
    smaller height, and the units between two such bands as one band."""
    bands = [[s[0].y0, s[0].y1, [s]] for s in sorted(spanners, key=lambda t: t[0].y0)]
    rest = [t for t in items if all(t is not s for s in spanners)]

    def joins(y0, y1, b):
        return v_overlap(y0, y1, b[0], b[1]) >= 0.5 * max(0.5, min(y1 - y0, b[1] - b[0]))

    changed = True
    while changed:
        changed = False
        bands.sort(key=lambda b: b[0])
        merged = []
        for b in bands:  # spanner bands that overlap enough become one
            if merged and joins(b[0], b[1], merged[-1]):
                merged[-1][1] = max(merged[-1][1], b[1])
                merged[-1][2] += b[2]
            else:
                merged.append(b)
        bands = merged
        keep = []
        for t in rest:
            band = next((b for b in bands if joins(t[0].y0, t[0].y1, b)), None)
            if band is None:
                keep.append(t)
            else:
                band[0], band[1] = min(band[0], t[0].y0), max(band[1], t[0].y1)
                band[2].append(t)
                changed = True
        rest = keep
    between = collections.defaultdict(list)
    for t in rest:  # index = number of bands whose centre is above the unit's centre
        yc = (t[0].y0 + t[0].y1) / 2
        between[sum(1 for b in bands if (b[0] + b[1]) / 2 < yc)].append(t)
    out = []
    for k in range(len(bands) + 1):
        if between.get(k):
            out.append(between[k])
        if k < len(bands):
            out.append(bands[k][2])
    return out


def flow_bands(items, gap):
    """Bands of a region that cannot be split into columns (see the module docstring)."""
    x0 = min(t[0].x0 for t in items)
    width = max(t[0].x1 for t in items) - x0
    rest = sorted(items, key=lambda t: -t[0].width)
    spanners = []
    while rest and rest[0][0].width >= SPANNER_WIDTH * width:
        spanners.append(rest.pop(0))
        if len(rest) >= 2 and len(split_gaps(rest, "x", gap)) > 1:
            bands = spanner_bands(items, spanners)
            if len(bands) > 1:
                return bands
            break
    return split_gaps(items, "y", gap)


def row_sort(items):
    """Rows (vertical centres within half the smaller height) top to bottom, each left to right."""
    rows = []
    for t in sorted(items, key=lambda t: ((t[0].y0 + t[0].y1) / 2, t[0].x0)):
        yc, h = (t[0].y0 + t[0].y1) / 2, t[0].height
        if rows and abs(yc - rows[-1][0]) <= 0.5 * min(h, rows[-1][1]):
            rows[-1][2].append(t)
        else:
            rows.append([yc, h, [t]])
    return [t for r in rows for t in sorted(r[2], key=lambda t: t[0].x0)]


def xy_cut(items, col, top, cols, gap, depth=0):
    if len(items) <= 1 or depth > 80:
        return [(p, col, top) for _, p in row_sort(items)]
    groups = split_gaps(items, "x", gap)
    if len(groups) > 1:
        out = []
        for g in groups:
            cid = cols.new(col, [r for r, _ in g])
            out += xy_cut(g, cid, cid if top == 0 else top, cols, gap, depth + 1)
        return out
    groups = flow_bands(items, gap)
    if len(groups) > 1:
        out = []
        for g in groups:
            out += xy_cut(g, col, top, cols, gap, depth + 1)
        return out
    return [(p, col, top) for _, p in row_sort(items)]


def make_units(items, containers, parent):
    """Reading units inside container `parent` (None = page): each child container holding at
    least two items is one unit; then each picture with the boxes printed on it; every other
    item is a unit of its own. items: {"kind": "box"|"pic", "rect", "containers", "obj"}."""
    claimed, out = set(), []
    for c in sorted((c for c in containers if c["parent"] == parent), key=lambda c: -area(c["box"])):
        mem = [it for it in items if id(it) not in claimed and c["id"] in it["containers"]]
        if len(mem) >= 2:
            claimed.update(id(it) for it in mem)
            out.append((union([pymupdf.Rect(c["box"])] + [it["rect"] for it in mem]), ("cont", c["id"], mem)))
    rest = [it for it in items if id(it) not in claimed]
    for it in rest:
        if it["kind"] == "pic" and id(it) not in claimed:
            pr = tuple(it["rect"])
            ov = [b for b in rest if b["kind"] == "box" and id(b) not in claimed and inside(tuple(b["rect"]), pr) >= OVERLAY_INSIDE]
            claimed.add(id(it))
            claimed.update(id(b) for b in ov)
            out.append((union([it["rect"]] + [b["rect"] for b in ov]), ("pic", it, ov)))
    out += [(it["rect"], ("item", it)) for it in rest if id(it) not in claimed]
    return out


def order_units(units, col, top, cols, gap, containers):
    out = []
    for payload, c, t in xy_cut(units, col, top, cols, gap):
        if payload[0] == "item":
            out.append((payload[1], c, t))
        elif payload[0] == "cont":
            out += order_units(make_units(payload[2], containers, payload[1]), c, t, cols, gap, containers)
        else:
            out.append((payload[1], c, t))
            if payload[2]:
                out += order_units([(b["rect"], ("item", b)) for b in payload[2]], c, t, cols, gap, containers)
    return out


def reading_order(boxes, pics, containers, pg):
    cols = _Columns()
    content = [b for b in boxes if not b["is_running"]]
    items = [{"kind": "box", "rect": b["rect"], "containers": b["containers"], "obj": b} for b in content]
    for p in pics:
        p["order"] = p["column"] = p["top_column"] = None
        printed = any(inside(tuple(b["rect"]), tuple(p["rect"])) >= OVERLAY_INSIDE for b in content)
        if p["rec"]["doc_role"] != "layout_repeat" and p["area_ratio"] <= pg["picture_unit_max_area"] and not printed:
            items.append({"kind": "pic", "rect": p["rect"], "containers": p["containers"], "obj": p})
    seq = order_units(make_units(items, containers, None), 0, 0, cols, pg["column_gap"], containers)
    k = 0
    for it, c, t in seq:
        o = it["obj"]
        o["order"], o["column"], o["top_column"] = k, c, t
        if it["kind"] == "box":
            k += 1
    for b in sorted((b for b in boxes if b["is_running"]), key=lambda b: (b["rect"].y0, b["rect"].x0)):
        b["order"], b["column"], b["top_column"] = k, -1, -1
        k += 1
    for p in pics:
        if p["order"] is None:
            ov = [b for b in content if inside(tuple(b["rect"]), tuple(p["rect"])) >= OVERLAY_INSIDE]
            if ov:
                first = min(ov, key=lambda b: b["order"])
                p["order"], p["column"], p["top_column"] = first["order"], first["column"], first["top_column"]
    if items:
        rs = [it["rect"] for it in items]
        cols.items[0]["bbox"] = rnd((min(r.x0 for r in rs), min(r.y0 for r in rs), max(r.x1 for r in rs), max(r.y1 for r in rs)))
    return cols.items


# ---------------------------------------------------------------- problem checks
ISSUES = ("running_title", "interleaved", "stacked_lines", "centre_inside", "doubled", "duplicate_boxes", "garbled",
          "hidden_dropped")
ITEM_LIKE = re.compile(r"^\s*(?:[•●■◆◇▶►▸▹▪◼・‧·∙○◎※➀-➓①-⑳❶-❿⚫➢➤✓✔\-–▎▍▌]|\d{1,2}[\.、\)）]|[\(（]\d|[一二三四五六七八九十]+、)")
DOUBLED = re.compile(r"^(.{2,})\1+$", re.S)


def doubled(text):
    m = DOUBLED.match(re.sub(r"\s", "", text))
    return bool(m) and (len(m.group(1)) >= 3 or not m.group(1).isdigit())  # not '2020'


def page_issues(boxes, body, cover, hidden_n, pg, page_w, page_h):
    """Problem counts and box ids of one page (see the module docstring)."""
    found = collections.defaultdict(set)
    bx = [b for b in boxes if not b["is_running"]]
    for a in bx:
        cx, cy = (a["rect"].x0 + a["rect"].x1) / 2, (a["rect"].y0 + a["rect"].y1) / 2
        for b in bx:
            if a is b:
                continue
            r = b["rect"]
            if r.x0 < cx < r.x1 and r.y0 < cy < r.y1:
                found["centre_inside"].add(a["id"])
            if (abs(a["rect"].x0 - r.x0) <= 3 and abs(a["size"] - b["size"]) <= 0.5 and len(b["baselines"]) >= 2
                    and ep.horizontal_overlap(a["rect"], r) >= 0.5
                    and any(b["baselines"][0] + 0.3 * b["size"] < y < b["baselines"][-1] - 0.3 * b["size"] for y in a["baselines"])):
                found["interleaved"].update((a["id"], b["id"]))
            if a["id"] < b["id"] and a["text"] == b["text"] and box_iou(tuple(a["rect"]), tuple(r)) >= 0.8:
                found["duplicate_boxes"].update((a["id"], b["id"]))
        if doubled(a["text"]) and nchars(a["text"]) >= 4:
            found["doubled"].add(a["id"])
        if a["garbled_share"] >= 0.2:
            found["garbled"].add(a["id"])
    srt = sorted(bx, key=lambda b: b["rect"].y0)
    used = set()
    for i, b in enumerate(srt):
        if b["id"] in used or ITEM_LIKE.match(b["text"]):
            continue
        run = [b]
        for o in srt[i + 1:]:
            last = run[-1]
            if o["id"] in used:
                continue
            if (abs(o["rect"].x0 - b["rect"].x0) <= 1.5 and abs(o["size"] - b["size"]) <= 0.3 and o["n_lines"] == 1
                    and 0.6 * b["size"] < o["rect"].y0 - last["rect"].y0 < 2.0 * b["size"] and not ITEM_LIKE.match(o["text"])
                    and not SENT_END.search(last["text"]) and o["containers"] == b["containers"]
                    and o["text"] != last["text"]):  # table cells with the same value stay apart on purpose
                run.append(o)
            elif o["rect"].y0 - last["rect"].y0 >= 2.0 * b["size"]:
                break
        if len(run) >= 3 and b["n_lines"] == 1:
            used.update(x["id"] for x in run)
            found["stacked_lines"].update(x["id"] for x in run)
    if not cover and body:  # the page's largest title-sized text is running although it is in the content area
        heads = [b for b in boxes if heading_text(b["text"], pg) and b["size"] >= pg["repeat_heading_ratio"] * body
                 and b["garbled_share"] < pg["garbled_box_share"]]
        if heads:
            top = max(heads, key=lambda b: b["size"])
            r = top["rect"]
            content_area = not (r.y1 <= 0.1 * page_h or r.y0 >= 0.9 * page_h or r.x1 <= 0.15 * page_w or r.x0 >= 0.85 * page_w)
            if top["is_running"] and content_area:
                found["running_title"].add(top["id"])
    issues = {k: len(v) for k, v in found.items() if v}
    if hidden_n:
        issues["hidden_dropped"] = hidden_n
    return issues, {k: sorted(v) for k, v in found.items() if v}


# ---------------------------------------------------------------- reading a report
def analyse_report(pdf, recs, pg, verify=False):
    """Text layer of every page that has a picture record: invisible/overprinted text removed,
    running flags, containers, paragraph boxes. Used by build_report and --sparse-stats."""
    by_page = collections.defaultdict(list)
    for r in recs:
        by_page[r["page"]].append(r)
    cache = {"pdf": pdf}
    pages_raw, work, errors = [], {}, {}
    rotated = 0
    with small_glyphs():
        doc = pymupdf.open(pdf)
        n_pages = len(doc)
        for page in doc:
            p = page.number + 1
            if p in by_page:
                try:
                    rotated += page.rotation != 0
                    drawings = page.get_drawings()
                    bboxlog = page.get_bboxlog()
                    hidden = hidden_text_spans(page, drawings, bboxlog, cache)
                    d = page.get_text("dict")
                    raw = ep.raw_text_blocks(_DictPage(d))
                    pieces, n_cut, clean, hidden_dropped = page_pieces(d, pg["split_size_jump"], hidden)
                    del d
                    containers, dropped = find_containers(page, drawings, bboxlog, pg, [r["bbox"] for r in by_page[p]])
                    images = [(k, tuple(pymupdf.Rect(e[1]) & page.rect)) for k, e in enumerate(bboxlog)
                              if e[0] in ("fill-image", "fill-imgmask")]
                    work[p] = {"pieces": pieces, "containers": containers, "dropped": dropped, "n_cut": n_cut,
                               "clean": clean, "hidden_dropped": hidden_dropped, "images": images,
                               "w": page.rect.width, "h": page.rect.height,
                               "pic_boxes": [tuple(r["bbox"]) for r in by_page[p] if r["doc_role"] != "layout_repeat"]}
                    pages_raw.append(raw)
                except Exception as e:  # broken page: keep going, report it
                    errors[p] = f"{type(e).__name__}: {e}"
                    try:
                        pages_raw.append(block_texts(page))
                    except Exception:
                        pages_raw.append([])
            else:
                pages_raw.append(block_texts(page))
        rep = Repeats(pages_raw, pg["slot_tolerance"])
        check = None
        if verify:
            full = [pages_raw[i] if (i + 1) in work else ep.raw_text_blocks(doc[i]) for i in range(n_pages)]
            rep_full = Repeats(full, pg["slot_tolerance"])
            a, b = rep.signature(), rep_full.signature()
            check = {"same": a == b, "keys": len(a["keys"]), "keys_all_dict": len(b["keys"]),
                     "slot_positions": len(a["pos"]), "slot_positions_all_dict": len(b["pos"])}
        doc.close()
        if cache.get("doc") is not None:
            cache["doc"].close()
    body0 = decide_running(work, rep, pg)
    for w in work.values():
        w["boxes"], w["merge"] = merge_pieces(w, rep, w["containers"], pg, body0)
    return {"by_page": by_page, "n_pages": n_pages, "work": work, "errors": errors, "rotated": rotated, "rep": rep,
            "check": check, "body0": body0,
            "hidden_test": {"candidate_spans": cache.get("candidates", 0), "errors": cache.get("errors", 0),
                            "last_error": cache.get("last_error")}}


def build_report(job):
    name, pdf, pairs_path, out_dir, pg, verify = (job[k] for k in ("name", "pdf", "pairs", "out", "pg", "verify"))
    t0 = time.time()
    out_dir = Path(out_dir)
    recs = read_pairs(pairs_path)
    ctx = analyse_report(pdf, recs, pg, verify)
    by_page, n_pages, work, errors, rep = ctx["by_page"], ctx["n_pages"], ctx["work"], ctx["errors"], ctx["rep"]
    pic_pages = sorted(p for p in by_page if 1 <= p <= n_pages)

    all_boxes = []
    for p, w in work.items():
        for b in w["boxes"]:
            b["page"], b["cover"] = p, p in (1, n_pages)
        all_boxes += w["boxes"]
    font = font_hierarchy(all_boxes, pg)

    lines, roles, dropped_tot, clean_tot = [], collections.Counter(), collections.Counter(), collections.Counter()
    issue_tot, issue_pages, gar = collections.Counter(), collections.Counter(), collections.Counter()
    n_boxes = n_running = n_cont = n_cut = n_para = n_join = n_merge = n_panel = n_vis = 0
    for p in pic_pages:
        if p not in work:
            lines.append({"report": name, "page": p, "error": errors.get(p, "page not read")})
            continue
        w = work[p]
        boxes, containers = w["boxes"], w["containers"]
        pics = [{"rec": r, "bbox_rect": pymupdf.Rect(r["bbox"]), "area_ratio": r["area_ratio"]}
                for r in sorted(by_page[p], key=lambda r: r["idx"])]
        visible_rects(w["images"], pics, pg["picture_unit_max_area"])
        for x in pics:
            x["containers"] = list(containers_of(tuple(x["rect"]), containers, pg["inside_container"]))
        boxes.sort(key=lambda b: (round(b["rect"].y0, 1), b["rect"].x0))
        for i, b in enumerate(boxes):
            b["id"] = f"b{i:03d}"
        for b in boxes:
            b["role"] = box_role(b, font, pics, boxes, pg)
            b["on_pictures"] = [x["rec"]["idx"] for x in pics if inside(tuple(b["rect"]), tuple(x["rect"])) >= OVERLAY_INSIDE]
        content = [b for b in boxes if not b["is_running"]]
        for c in containers:
            n_in = sum(1 for b in content if c["id"] in b["containers"])
            c["panel"] = c["ratio"] >= pg["panel_area"] or (n_in >= 2 and n_in == len(content))
        columns = reading_order(boxes, pics, containers, pg)
        issues, issue_boxes = page_issues(boxes, font["body"], p in (1, n_pages), w["clean"].get("hidden_spans", 0), pg,
                                          w["w"], w["h"])
        pictures = []
        for x in pics:
            r = x["rec"]
            pictures.append({"idx": r["idx"], "crop": r["crop"], "bbox": r["bbox"], "visible_bbox": rnd(x["rect"]),
                             "doc_role": r["doc_role"],
                             "representative": r["representative"],
                             "analysed": bool(r["representative"] and r["doc_role"] != "layout_repeat"),
                             "area_ratio": r["area_ratio"], "containers": x["containers"],
                             "overlay_boxes": [b["id"] for b in boxes if r["idx"] in b["on_pictures"]],
                             "order": x["order"], "column": x["column"], "top_column": x["top_column"]})
        box_json = []
        for b in sorted(boxes, key=lambda b: b["id"]):
            box_json.append({"id": b["id"], "bbox": rnd(b["rect"]), "text": b["text"], "size": round(b["size"], 2),
                             "chars": b["chars"], "lines": b["n_lines"], "bold": round(b["bold"], 2),
                             "garbled": round(b["garbled_share"], 2), "is_running": b["is_running"],
                             "repeat_pages": b["repeat_pages"], "slot_pages": b["slot_pages"], "role": b["role"],
                             "order": b["order"], "column": b["column"],
                             "top_column": b["top_column"], "containers": b["containers"], "on_pictures": b["on_pictures"]})
            roles["run" if b["is_running"] else b["role"]] += 1
            if b["garbled_share"] > 0:
                gar["boxes_with_garbled"] += 1
            gar["garbled_chars"] += round(b["garbled_share"] * b["chars"])
            gar["chars"] += b["chars"]
        n_boxes += len(boxes)
        n_running += sum(b["is_running"] for b in boxes)
        n_cont += len(containers)
        n_panel += sum(c["panel"] for c in containers)
        n_vis += sum(1 for x in pics if rnd(x["rect"]) != rnd(x["bbox_rect"]))
        n_cut += w["n_cut"]
        n_para += w["merge"]["cut"]
        n_join += w["merge"]["joined"]
        n_merge += w["merge"]["merged"]
        dropped_tot.update(w["dropped"])
        clean_tot.update(w["clean"])
        issue_tot.update(issues)
        issue_pages.update(issues.keys())
        lines.append({"report": name, "page": p, "n_pages": n_pages, "cover": p in (1, n_pages),
                      "width": round(w["w"], 2), "height": round(w["h"], 2), "body_size": font["body"],
                      "text_chars": sum(x["chars"] for x in box_json),
                      "text_chars_nonrunning": sum(x["chars"] for x in box_json if not x["is_running"]),
                      "boxes": box_json, "containers": [container_json(c) for c in containers], "columns": columns,
                      "pictures": pictures, "fills_dropped": dict(w["dropped"]), "text_dropped": dict(w["clean"]),
                      "hidden_text": [{"bbox": rnd(r), "text": t} for r, t in w["hidden_dropped"]],
                      "blocks_split": w["n_cut"], "lines_merged": w["merge"]["merged"], "lines_joined": w["merge"]["joined"],
                      "paragraph_cuts": w["merge"]["cut"], "issues": issues, "issue_boxes": issue_boxes})

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f"{name}.jsonl.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps(ln, ensure_ascii=False) + "\n")
    tmp.replace(out_dir / f"{name}.jsonl")
    gar["share"] = round(gar["garbled_chars"] / gar["chars"], 4) if gar["chars"] else 0.0
    summary = {"report": name, "version": VERSION, "pymupdf": pymupdf.VersionBind, "pdf": str(pdf),
               "pairs": str(pairs_path), "pairs_sha256": sha256_file(pairs_path), "params": pg,
               "params_sha256": hashlib.sha256(json.dumps(pg, sort_keys=True).encode()).hexdigest(),
               "pages": n_pages, "picture_records": len(recs), "pages_with_pictures": len(pic_pages),
               "pages_rotated": ctx["rotated"], "page_errors": errors, "running_keys": len(rep.keys),
               "running_keys_check": ctx["check"], "body_size_for_running": ctx["body0"], "font": font,
               "boxes": n_boxes, "running_boxes": n_running, "repeated_title_boxes": sum(
                   1 for w in work.values() for b in w["boxes"] if b["repeated_title"] and not b["is_running"]),
               "roles": dict(roles), "containers": n_cont, "panel_containers": n_panel,
               "pictures_visible_cut": n_vis, "fills_dropped": dict(dropped_tot),
               "text_dropped": dict(clean_tot), "hidden_test": ctx["hidden_test"], "garbled": dict(gar),
               "blocks_split": n_cut, "lines_merged": n_merge, "lines_joined": n_join, "paragraph_cuts": n_para,
               "issues": dict(issue_tot), "issue_pages": dict(issue_pages), "seconds": round(time.time() - t0, 1)}
    (out_dir / f"{name}_meta.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def done_before(job):
    m = Path(job["out"]) / f"{job['name']}_meta.json"
    if not m.exists() or not (Path(job["out"]) / f"{job['name']}.jsonl").exists():
        return None
    try:
        s = json.loads(m.read_text(encoding="utf-8"))
    except Exception:
        return None
    same = (s.get("version") == VERSION and s.get("pymupdf") == pymupdf.VersionBind
            and s.get("pairs_sha256") == sha256_file(job["pairs"])
            and s.get("params_sha256") == hashlib.sha256(json.dumps(job["pg"], sort_keys=True).encode()).hexdigest())
    return s if same else None


def run_job(job):
    try:
        return build_report(job)
    except Exception as e:  # one broken report must not stop a batch
        return {"report": job["name"], "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------- regression checks
def _page(out_dir, name, page):
    path = Path(out_dir) / f"{name}.jsonl"
    if not path.exists():
        return None
    for line in open(path, encoding="utf-8"):
        ln = json.loads(line)
        if ln["page"] == page:
            return ln
    return None


def _norm_text(t):
    return re.sub(r"\s", "", t)


def regression_checks(out_dir, names):
    """Known pages from the 10/18 review; each check runs when its report is in the run."""
    checks = []

    def add(name, page, what, fn):
        if name not in names:
            return
        ln = _page(out_dir, name, page)
        try:
            ok, detail = fn(ln) if ln else (False, "page not in output")
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        checks.append({"report": name, "page": page, "check": what, "ok": bool(ok), "detail": detail})

    def para_2610(ln):
        hit = [b for b in ln["boxes"] if "因應資訊科技迅速發展" in _norm_text(b["text"])]
        return (len(hit) == 1 and hit[0]["lines"] >= 6, [(b["id"], b["lines"], b["role"]) for b in hit])

    def no_interleave(ln):
        return (not ln["issues"].get("interleaved") and not ln["issues"].get("stacked_lines"), ln["issues"])

    def title_2882(ln):  # the 22 pt page title; the 9 pt side-menu entry with the same text stays running
        hit = [b for b in ln["boxes"] if _norm_text(b["text"]).startswith("1.1永續治理與管理")]
        big = max(hit, key=lambda b: b["size"]) if hit else None
        return (big is not None and not big["is_running"] and big["role"] in ("h1", "h2"),
                [(b["id"], b["size"], b["is_running"], b["role"]) for b in hit])

    def hidden_2882(ln):
        hit = [b for b in ln["boxes"] if "國泰永續策略藍圖發展歷程" in _norm_text(b["text"]) and b["bbox"][1] < 70]
        return (not hit and ln["text_dropped"].get("hidden_spans", 0) > 0, {"boxes": [b["id"] for b in hit],
                                                                             "text_dropped": ln["text_dropped"]})

    def visible_1101(ln):
        hit = [b for b in ln["boxes"] if b["bbox"][1] < 145 and b["bbox"][0] < 120 and b["chars"] >= 10]
        return (len(hit) >= 2 and not ln["text_dropped"].get("hidden_spans"), {"boxes": [b["id"] for b in hit],
                                                                              "text_dropped": ln["text_dropped"]})

    def pageno_2610(ln):
        bad = [b["id"] for b in ln["boxes"] if re.fullmatch(r"26(\s+26)+", b["text"].strip())]
        num = [b for b in ln["boxes"] if b["text"].strip() == "26"]
        return (not bad and bool(num) and all(b["is_running"] for b in num), {"doubled": bad, "page_no": [b["id"] for b in num]})

    def para_1707(ln):
        hit = [b for b in ln["boxes"] if "工傷事故" in _norm_text(b["text"]) and "避免類似事故再度發生" in _norm_text(b["text"])]
        return (len(hit) == 1 and hit[0]["lines"] == 4, [(b["id"], b["lines"], b["text"][:30]) for b in hit])

    def wrap_2610(ln):
        hit = [b for b in ln["boxes"] if "及便捷的服務" in _norm_text(b["text"])]
        return (len(hit) == 1 and "新機從法國" in _norm_text(hit[0]["text"]) and hit[0]["role"] != "caption",
                [(b["id"], b["lines"], b["role"], b["text"][:20]) for b in hit])

    add("2610_2025", 43, "paragraph wrapping around a photo is one box, not a caption", wrap_2610)
    add("2610_2024", 38, "7-line paragraph is one box (was one box per line)", para_2610)
    add("1707_2025", 103, "4-line paragraph under a formula with a smaller line pitch is one box", para_1707)
    add("2882_2025", 18, "no interleaved or per-line boxes", no_interleave)
    add("2882_2025", 15, "page title '1.1 永續治理與管理' is not running", title_2882)
    add("2882_2025", 18, "title under the later picture is dropped as invisible", hidden_2882)
    add("1101_2023", 9, "text under a see-through picture is kept", visible_1101)
    add("2610_2025", 26, "page number drawn three times is one running '26'", pageno_2610)
    for name in ("1707_2024", "2882_2023", "2882_2024"):
        if name in names:
            m = json.loads((Path(out_dir) / f"{name}_meta.json").read_text(encoding="utf-8"))
            share = m["font"]["body_bold_share"]
            checks.append({"report": name, "page": None, "check": "body text not counted as bold (DemiLight)",
                           "ok": share is not None and share <= BOLD_BODY_MAX, "detail": {"body_bold_share": share}})
    return checks


# ---------------------------------------------------------------- sparse-text statistics
def sparse_job(job):
    name, pdf, pairs, pg = job
    max_chars = int(pg["sparse_max_chars"])
    try:
        recs = read_pairs(pairs)
        doc = pymupdf.open(pdf)
        n = len(doc)
        analysed = [r for r in recs if r["representative"] and r["doc_role"] != "layout_repeat"]
        rest = [r for r in analysed if r["page"] not in (1, n)]
        pages = sorted({r["page"] for r in rest})
        chars = {p: nchars(doc[p - 1].get_text("text", flags=DICT_FLAGS)) for p in pages}
        doc.close()
        ctx = analyse_report(pdf, recs, pg)
        nr = {}
        for p in pages:
            w = ctx["work"].get(p)
            nr[p] = sum(b["chars"] for b in w["boxes"] if not b["is_running"]) if w else chars[p]
        return {"report": name, "pages": n, "analysed": len(analysed), "on_cover_pages": len(analysed) - len(rest),
                "not_cover": len(rest),
                "on_sparse_pages": sum(chars[r["page"]] <= max_chars for r in rest),
                "on_pages_without_text": sum(chars[r["page"]] == 0 for r in rest),
                "on_pages_le_50_chars": sum(chars[r["page"]] <= 50 for r in rest),
                "pages_with_analysed": len(chars), "sparse_pages": sum(v <= max_chars for v in chars.values()),
                "on_sparse_pages_nonrunning": sum(nr[r["page"]] <= max_chars for r in rest),
                "on_pages_without_nonrunning_text": sum(nr[r["page"]] == 0 for r in rest),
                "sparse_pages_nonrunning": sum(v <= max_chars for v in nr.values()),
                "pages_not_read": sorted(p for p in pages if p not in ctx["work"])}
    except Exception as e:
        return {"report": name, "error": f"{type(e).__name__}: {e}"}


SPARSE_KEYS = ["analysed", "on_cover_pages", "not_cover", "on_sparse_pages", "on_pages_without_text",
               "on_pages_le_50_chars", "pages_with_analysed", "sparse_pages", "on_sparse_pages_nonrunning",
               "on_pages_without_nonrunning_text", "sparse_pages_nonrunning"]


def sparse_totals(rows):
    t = {"reports": len(rows), **{k: sum(r[k] for r in rows) for k in SPARSE_KEYS}}
    for k in ("on_sparse_pages", "on_sparse_pages_nonrunning"):
        t[f"share_{k}"] = round(t[k] / t["not_cover"], 4) if t["not_cover"] else None
    return t


def run_sparse(args, cfg, pg, catalog, dl, dl_path, log):
    rows = [dict(r, name=f"{r['firm_id'].strip()}_{r['year'].strip()}") for r in catalog]
    extract = ROOT / cfg["paths"]["extract"]
    missing = [r["name"] for r in rows if not (extract / f"{r['name']}_pairs.jsonl").exists()]
    if missing:
        sys.exit(f"抽取還沒做完，缺 {len(missing)} 本：{missing[:20]}")
    not_checked = [r["name"] for r in rows if r["name"] not in dl]
    if not_checked:
        sys.exit(f"{dl_path} 沒有這些報告的 image_only：{not_checked}")
    max_chars = int(pg["sparse_max_chars"])
    stamp = run_stamp()
    path = ROOT / (args.results or cfg["paths"]["results"]) / f"page_sparse_text_{stamp}.json"
    if path.exists():
        sys.exit(f"{path} 已經存在，不覆蓋")
    path.parent.mkdir(parents=True, exist_ok=True)
    jobs = [(r["name"], str(ROOT / r["file_path"].strip()), str(extract / f"{r['name']}_pairs.jsonl"), pg) for r in rows]
    t0 = time.time()
    out = {}
    with Pool(args.workers) as pool:
        for k, res in enumerate(pool.imap_unordered(sparse_job, jobs), 1):
            out[res["report"]] = res
            if k % 25 == 0 or "error" in res:
                log(f"[sparse {k}/{len(jobs)}] {res['report']} {res.get('error', '')}")
    errors = {n: r["error"] for n, r in out.items() if "error" in r}
    per = {}
    for r in rows:
        res = out[r["name"]]
        if "error" in res:
            continue
        per[r["name"]] = dict(res, split=r["split"].strip(), image_only=bool(dl[r["name"]]["image_only"]))
    groups = {}
    for label, cond in (("with_text_layer", False), ("no_text_reports", True)):
        sub = [v for v in per.values() if v["image_only"] == cond]
        groups[label] = {"all": sparse_totals(sub),
                         **{s: sparse_totals([v for v in sub if v["split"] == s]) for s in ("dev", "test")},
                         "reports_list": sorted(v["report"] for v in sub) if cond else None}
    data_files = [cfg["paths"]["catalog"], str(dl_path)]
    result = {"meta": run_meta(args.config, data_files), "page_graph_version": VERSION,
              "headline": "totals.with_text_layer (the reports that are analysed); the no-text reports are excluded "
                          "from the study (Eden 10/18, decision 5) and listed separately",
              "definitions": {
                  "analysed": "列入分析 pictures: representative and doc_role != layout_repeat in work/extract",
                  "cover": "first and last page of the PDF; pictures there are left out of numerator and denominator",
                  "on_sparse_pages": (f"definition 1: pages with at most {max_chars} non-space characters of ALL text "
                                      "(page.get_text('text') with the get_text('dict') flags, whitespace removed; "
                                      "menus, headers, page numbers count)"),
                  "on_sparse_pages_nonrunning": (f"definition 2: pages with at most {max_chars} non-space characters in "
                                                 f"page_graph v{VERSION}'s non-running boxes (navigation, headers, page "
                                                 "numbers, invisible and overprinted text left out)")},
              "extract_dir": str(extract), "download_check": str(dl_path),
              "pairs_sha256": {n: sha256_file(extract / f"{n}_pairs.jsonl") for n in sorted(per)},
              "params": pg, "sparse_max_chars": max_chars, "seconds": round(time.time() - t0, 1), "errors": errors,
              "totals": {**groups, "all_450_incl_no_text": sparse_totals(list(per.values()))}, "per_report": per}
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    log(json.dumps({k: v["all"] if "all" in v else v for k, v in result["totals"].items()}, ensure_ascii=False))
    log(f"wrote {path}")


# ---------------------------------------------------------------- HTML
CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#666;--line:#ddd;--card:#f4f4f4;--bad:#b3261e}
@media (prefers-color-scheme: dark){:root{--bg:#161616;--fg:#eee;--mut:#aaa;--line:#333;--card:#222;--bad:#ff8a80}}
body{background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;margin:0 auto;max-width:1280px;padding:16px}
table{border-collapse:collapse;margin:8px 0 18px;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:right;white-space:nowrap;vertical-align:top}
th:first-child,td:first-child{text-align:left} td.t{text-align:left;white-space:normal;min-width:260px}
.wrap{overflow-x:auto} .mut{color:var(--mut)} .bad{color:var(--bad)}
.note{background:var(--card);border-radius:8px;padding:10px 14px;margin:10px 0}
.pg{position:relative;border:1px solid var(--line);margin:6px 0 4px;line-height:0}
.pg img{width:100%;display:block} .pg svg{position:absolute;left:0;top:0;width:100%;height:100%}
.ct0{fill:rgba(255,140,0,.22);stroke:#d97000} .ct1{fill:rgba(40,170,80,.22);stroke:#1f8a3e}
.ct2{fill:rgba(150,70,200,.22);stroke:#7b3fb0} .ct3{fill:rgba(0,160,170,.22);stroke:#00838f}
.tb{fill:none;stroke:#555;stroke-width:.8} .tr{fill:none;stroke:#999;stroke-width:.8;stroke-dasharray:3 2}
.tx{fill:rgba(220,0,0,.10);stroke:#d00000;stroke-width:1.6}
.pic{fill:none;stroke:#1565c0;stroke-width:3.2} .picl{fill:none;stroke:#1565c0;stroke-width:1;stroke-dasharray:4 3;opacity:.6}
.lb{font:600 7px system-ui,sans-serif;fill:#222;paint-order:stroke;stroke:#fff;stroke-width:2px}
.lbr{font:600 7px system-ui,sans-serif;fill:#888;paint-order:stroke;stroke:#fff;stroke-width:2px}
.lbp{font:700 9px system-ui,sans-serif;fill:#1565c0;paint-order:stroke;stroke:#fff;stroke-width:2.5px}
details{margin:2px 0 26px} summary{cursor:pointer;color:var(--mut)}
.chip{display:inline-block;width:12px;height:12px;border:1px solid var(--line);vertical-align:middle;margin-right:3px}
"""
LEGEND = ("怎麼看：每張大圖是報告的一頁（約 70 dpi）。<b>藍色粗框</b>是抽取到的圖片（照片或其他圖），框旁「P3 @12」= 圖片編號 3，"
          "在閱讀順序上排在第 12 個文字框前面（印了字的圖片跟著它上面的第一個字）；藍色細虛線是「版面元素」（3 頁以上都出現的背景、花紋，不列入分析），"
          "或是圖片原本的框（被後來畫的圖片蓋住一邊時，粗框只框看得到的部分）。"
          "<b>半透明色塊</b>是程式找到的「色塊容器」，也就是頁面上有底色的方框；橘色是最外層，綠色、紫色、青色是一層層包在裡面的。"
          "<b>灰色細框</b>是文字框，也就是標註 R 時可以點選的單位；框左上角的數字是程式排的閱讀順序，後面是角色："
          "h1、h2、h3 是字級由大到小的三層標題（報告裡有「4.1」這種節編號時，帶編號的那個字級定為 h2，比它大的是 h1、比它小的是 h3；"
          "沒有編號可參考的報告只是依字級分群，不一定對應章、節）、cap 貼著圖片上下左右的短文字（圖說型；印在圖片上的字不算）、"
          "body 內文、note 註解、garbled 亂碼；"
          "<b>虛線框</b>標 run 的是頁首、頁尾、導覽列、頁碼（同一段字在很多頁的同一個位置），不排進閱讀順序。"
          "<b>紅框</b>只出現在「問題頁」，是程式自己查到可能有問題的框。"
          "滑鼠停在框上會顯示文字。要看的是：一個灰框是不是剛好一段、色塊找得對不對、數字的順序是不是人讀這頁的順序。")
ISSUE_TEXT = {
    "running_title": "這頁最大的標題在內容區，卻被當成頁首／導覽列（run）",
    "interleaved": "兩個文字框的行交錯（一段被拆成兩個疊在一起的框）",
    "stacked_lines": "一段被切成一行一框（3 個以上單行框疊在一起）",
    "centre_inside": "文字框的中心落在另一個框裡（框疊在一起，點的時候分不清）",
    "doubled": "框裡的字重複（像「AA」）",
    "duplicate_boxes": "兩個框文字一樣、位置幾乎一樣",
    "garbled": "亂碼（PDF 的字型對照表壞掉：罕用字、私用區字元、控制字元）",
    "hidden_dropped": "被後來畫上的圖片或色塊整個蓋住、看不到的字，程式已經拿掉（請確認畫面上真的看不到）",
}


def render_page(pdf, line, dpi=70):
    doc = pymupdf.open(pdf)
    pix = doc[line["page"] - 1].get_pixmap(dpi=dpi)
    data = pix.tobytes("jpg", jpg_quality=70)
    doc.close()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def page_svg(line, bad=(), show_hidden=False):
    e = html.escape
    W, H = line["width"], line["height"]
    out = [f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">']
    for c in sorted(line["containers"], key=lambda c: c["depth"]):
        x0, y0, x1, y1 = c["bbox"]
        out.append(f'<rect class="ct{c["depth"] % 4}" x="{x0}" y="{y0}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" stroke-width="1">'
                   f'<title>{e(c["id"])} 底色 {e(c["hex"])}，占頁面 {100 * c["area_ratio"]:.1f}%，下面是 {e(c["under"])}'
                   f'{"，整頁大底板" if c.get("panel") else ""}</title></rect>')
    for p in sorted(line["pictures"], key=lambda p: p["doc_role"] != "layout_repeat"):
        x0, y0, x1, y1 = p.get("visible_bbox") or p["bbox"]
        cls = "picl" if p["doc_role"] == "layout_repeat" else "pic"
        cut = rnd(p["bbox"]) != rnd(p.get("visible_bbox") or p["bbox"])
        if cut:
            bx0, by0, bx1, by1 = p["bbox"]
            out.append(f'<rect class="picl" x="{bx0}" y="{by0}" width="{bx1 - bx0:.1f}" height="{by1 - by0:.1f}"></rect>')
        out.append(f'<rect class="{cls}" x="{x0}" y="{y0}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}">'
                   f'<title>圖片 idx {p["idx"]}（{e(p["doc_role"])}{"，列入分析" if p["analysed"] else ""}'
                   f'{"；被後畫的圖片蓋住一邊，粗框是看得到的部分，細虛線是原本的框" if cut else ""}）</title></rect>')
        if p["doc_role"] != "layout_repeat":
            lab = f'P{p["idx"]}' + (f' @{p["order"]}' if p.get("order") is not None else "")
            out.append(f'<text class="lbp" x="{x0 + 2}" y="{max(y0, 0) + 11}">{e(lab)}</text>')
    short = {"caption": "cap"}
    for b in line["boxes"]:
        x0, y0, x1, y1 = b["bbox"]
        run = b["is_running"]
        label = "run" if run else f'{b["order"]} {short.get(b["role"], b["role"])}'
        cls = "tx" if b["id"] in bad else ("tr" if run else "tb")
        out.append(f'<rect class="{cls}" x="{x0}" y="{y0}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}">'
                   f'<title>{e(b["id"])} {e(label)}｜{e(b["text"][:200])}</title></rect>'
                   f'<text class="{"lbr" if run else "lb"}" x="{x0 + 0.5}" y="{y0 + 6.5}">{e(label)}</text>')
    if show_hidden:
        for h in line.get("hidden_text", []):
            x0, y0, x1, y1 = h["bbox"]
            out.append(f'<rect class="tx" stroke-dasharray="3 2" x="{x0}" y="{y0}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}">'
                       f'<title>拿掉的字（看不到）：{e(h["text"][:200])}</title></rect>')
    out.append("</svg>")
    return "".join(out)


def box_table(line):
    e = html.escape
    rows = []
    for b in sorted(line["boxes"], key=lambda b: b["order"]):
        role = "run" if b["is_running"] else b["role"]
        rows.append(f'<tr><td>{b["order"]}</td><td>{e(b["id"])}</td><td>{e(role)}</td><td>{b["size"]}</td><td>{b["lines"]}</td>'
                    f'<td>{b["top_column"]}/{b["column"]}</td><td>{e(",".join(b["containers"]))}</td><td>{b["chars"]}</td>'
                    f'<td>{b["repeat_pages"]}</td><td class="t">{e(b["text"][:120])}</td></tr>')
    conts = "".join(f'<tr><td>{e(c["id"])}</td><td><span class="chip" style="background:{e(c["hex"])}"></span>{e(c["hex"])}</td>'
                    f'<td>{100 * c["area_ratio"]:.1f}%</td><td>{e(c["under"])}</td><td>{e(str(c["parent"] or "—"))}</td>'
                    f'<td>{"是" if c.get("panel") else ""}</td></tr>'
                    for c in line["containers"])
    pics = "".join(f'<tr><td>P{p["idx"]}</td><td>{e(p["doc_role"])}</td><td>{"是" if p["analysed"] else ""}</td>'
                   f'<td>{"—" if p["order"] is None else p["order"]}</td><td>{p["top_column"]}/{p["column"]}</td>'
                   f'<td>{e(",".join(p["overlay_boxes"]))}</td></tr>' for p in line["pictures"])
    return (f'<details><summary>這一頁的文字框（{len(line["boxes"])} 個，依閱讀順序）、圖片（{len(line["pictures"])} 張）和色塊（{len(line["containers"])} 個）</summary>'
            f'<div class="wrap"><table><tr><th>順序</th><th>編號</th><th>角色</th><th>字級</th><th>行數</th><th>欄（最上層/最內層）</th><th>所在色塊</th><th>字數</th><th>同樣的字出現幾頁</th><th>文字（前 120 字）</th></tr>{"".join(rows)}</table></div>'
            f'<div class="wrap"><table><tr><th>圖片</th><th>角色</th><th>列入分析</th><th>排在第幾個文字框前</th><th>欄</th><th>印在圖上的框</th></tr>{pics}</table></div>'
            f'<div class="wrap"><table><tr><th>色塊</th><th>底色</th><th>面積</th><th>下面是</th><th>外層</th><th>整頁大底板</th></tr>{conts}</table></div></details>')


def pick_pages(lines, seed, n=3):
    good = [ln for ln in lines if "boxes" in ln and not ln["cover"] and ln["containers"]
            and sum(not b["is_running"] for b in ln["boxes"]) >= 5 and any(p["analysed"] for p in ln["pictures"])]
    rng = random.Random(seed)
    if len(good) >= n:
        return sorted(rng.sample(good, n), key=lambda ln: ln["page"])
    rest = sorted((ln for ln in lines if "boxes" in ln and ln not in good),
                  key=lambda ln: -sum(not b["is_running"] for b in ln["boxes"]))
    return sorted(good + rest[:n - len(good)], key=lambda ln: ln["page"])


def pick_problem_pages(lines, seed, skip, n=3):
    """Up to n pages, one per problem type in the order of ISSUES (random page of that type)."""
    rng = random.Random(seed)
    chosen = []
    for issue in ISSUES:
        cand = [ln for ln in lines if ln.get("issues", {}).get(issue) and ln["page"] not in skip
                and all(ln is not c for c, _ in chosen)]
        if cand:
            chosen.append((rng.choice(cand), issue))
        if len(chosen) >= n:
            break
    return chosen


def bands_text(font):
    out = {}
    for b in font["bands"]:
        hi = f"–{b['hi']}" if b["hi"] else "+"
        out[b["level"]] = f"{b['lo']}{hi} pt（{', '.join(b['sizes'])}）"
    return out


def page_block(e, name, ln, pdf, title, bad=(), extra="", show_hidden=False):
    n_body = sum(not b["is_running"] for b in ln["boxes"])
    return (f"<h3>{e(name)} 第 {ln['page']} 頁{e(title)}</h3>{extra}<p class='mut'>文字框 {len(ln['boxes'])} 個（不含頁首頁尾 {n_body} 個）、"
            f"色塊 {len(ln['containers'])} 個、欄 {len(ln['columns']) - 1} 個、圖片 {len(ln['pictures'])} 張</p>"
            f"<div class='pg'><img src='{render_page(pdf, ln)}' alt=''>{page_svg(ln, bad, show_hidden)}</div>{box_table(ln)}")


def write_firm_html(path, firm, firm_name, summaries, pdfs, out_dir, seed, meta, skipped, checks):
    e = html.escape
    trs = []
    for s in summaries:
        bt = bands_text(s["font"])
        method = {"numbered_anchor": f"依節編號（{s['font']['anchor']} pt = h2）", "size_clusters": "依字級分群"}.get(s["font"]["method"], "—")
        trs.append(f"<tr><td>{e(s['report'])}</td><td>{s['pages']}</td><td>{s['pages_with_pictures']}</td><td>{s['picture_records']}</td>"
                   f"<td>{s['boxes']}</td><td>{s['running_boxes']}</td><td>{s['repeated_title_boxes']}</td><td>{s['containers']}</td><td>{s['font']['body']}</td>"
                   f"<td>{e(method)}</td>" + "".join(f"<td>{e(bt.get(k, '—'))}</td>" for k in (1, 2, 3))
                   + f"<td>{s['blocks_split']}</td><td>{s['lines_merged']}</td><td>{s['lines_joined']}</td><td>{s['paragraph_cuts']}</td><td>{s['seconds']}</td></tr>")
    role_rows = []
    for s in summaries:
        r = s["roles"]
        td = s["text_dropped"]
        role_rows.append(f"<tr><td>{e(s['report'])}</td>" + "".join(f"<td>{r.get(k, 0)}</td>" for k in ("h1", "h2", "h3", "caption", "body", "note", "garbled", "run"))
                         + f"<td>{td.get('hidden_spans', 0)}</td><td>{td.get('duplicate_spans', 0)}</td>"
                         + f"<td>{100 * s['garbled']['share']:.2f}%</td>"
                         + f"<td>{e(', '.join(f'{k} {v}' for k, v in sorted(s['fills_dropped'].items())))}</td></tr>")
    issue_rows = []
    for s in summaries:
        issue_rows.append(f"<tr><td>{e(s['report'])}</td>" + "".join(
            f"<td>{s['issues'].get(k, 0)}（{s['issue_pages'].get(k, 0)} 頁）</td>" for k in ISSUES) + "</tr>")
    chk = [c for c in checks if c["report"].split("_")[0] == firm]
    parts = [f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>頁面結構 {e(firm)} {e(firm_name)}</title>
<style>{CSS}</style></head><body>
<h1>頁面結構：{e(firm)} {e(firm_name)}</h1>
<p class="mut">{e(meta['date'])}｜page_graph.py v{VERSION}｜PyMuPDF {e(str(meta['packages'].get('pymupdf')))}｜git {e(str(meta['git'].get('commit')))}{'（有未 commit 的修改）' if meta['git'].get('uncommitted_changes') else ''}</p>
<div class="note">這份是 PLAN §5 步驟 2 的「頁面結構」：每一頁有圖片的頁面，程式找出文字框（一段一框）、有底色的方框（色塊容器）、
每本報告的字級層級（內文和三層標題），以及閱讀順序（文字框和圖片）。這些是之後圖文配對（步驟 4）的特徵，也是標註 R 畫面上的灰框。
每本報告先抽 3 頁一般的頁（有色塊、文字框多的頁，seed 2026），再放最多 3 頁「問題頁」（程式自己查到可能有錯的頁，每種問題一頁）。
這是第 2 版：10/18 晚上獨立審查找到的問題（一行一框、兩段交錯、頁面標題被當導覽列、看不到的字、重複的字、亂碼）已經改過。</div>
<div class="note">{LEGEND}</div>
<h2>每本報告的數字</h2>
<div class="wrap"><table><tr><th>報告</th><th>頁數</th><th>有圖的頁</th><th>圖片</th><th>文字框</th><th>其中頁首頁尾導覽列</th><th>重複出現但當標題的框</th><th>色塊</th><th>內文字級</th><th>標題怎麼分層</th><th>h1</th><th>h2</th><th>h3</th><th>切開的區塊</th><th>併成段的行</th><th>補接的行</th><th>再切開的段落</th><th>秒</th></tr>{''.join(trs)}</table></div>
<p class="mut">文字框、色塊只算有圖的頁。h1–h3 欄是字級範圍（括號裡是這個層級實際用到的字級 pt）。
「重複出現但當標題的框」= 同一段字出現在很多頁（例如每頁都印的節標題「1.1 永續治理與管理」），但字夠大、不在選單裡，所以當標題、不當導覽列。
「切開的區塊」= PDF 解析器（MuPDF）把兩欄、標題加內文、或好幾個條列項目放在同一個區塊，程式把它切開的次數；
「併成段的行」「補接的行」= 同一段的行接回一個框的次數；「再切開的段落」= 依段落間距、句號、條列符號把兩段切開的次數。</p>
<h3>角色、拿掉的字、被丟掉的填色</h3>
<div class="wrap"><table><tr><th>報告</th><th>h1</th><th>h2</th><th>h3</th><th>cap</th><th>body</th><th>note</th><th>garbled</th><th>run</th><th>看不到、拿掉的字（段）</th><th>重複印、拿掉的字（段）</th><th>亂碼字比例</th><th>沒算成色塊的填色（原因：個數）</th></tr>{''.join(role_rows)}</table></div>
<p class="mut">填色原因：same_colour_as_under = 跟底下同色（重刷底色）；page_background = 超過頁面 90%；not_rectangle = 不是矩形；hidden_later = 之後被圖片或不透明色塊整個蓋住；picture_tint = 疊在照片上、跟照片差不多大的色塊（照片的濾色）。面積小於 2% 的不列。</p>
<h3>程式自己查到的問題（框數，括號是頁數）</h3>
<div class="wrap"><table><tr><th>報告</th>{''.join(f'<th>{e(k)}</th>' for k in ISSUES)}</tr>{''.join(issue_rows)}</table></div>
<p class="mut">{'；'.join(f'{e(k)} = {e(v)}' for k, v in ISSUE_TEXT.items())}。centre_inside 多半是表格、流程圖裡的小字框疊在大框上，不一定是錯。</p>"""]
    if chk:
        parts.append("<h3>回歸檢查（審查時找到的頁，改完後再查一次）</h3><div class='wrap'><table><tr><th>報告</th><th>頁</th><th>檢查</th><th>結果</th><th>細節</th></tr>"
                     + "".join(f"<tr><td>{e(c['report'])}</td><td>{c['page'] or '—'}</td><td class='t'>{e(c['check'])}</td>"
                               f"<td class='{'' if c['ok'] else 'bad'}'>{'通過' if c['ok'] else '沒通過'}</td>"
                               f"<td class='t'>{e(json.dumps(c['detail'], ensure_ascii=False)[:300])}</td></tr>" for c in chk)
                     + "</table></div>")
    if skipped:
        parts.append(f"<p class='bad'>沒有文字層、沒做的報告：{e(', '.join(skipped))}</p>")
    for s in summaries:
        name = s["report"]
        lines = read_pairs(Path(out_dir) / f"{name}.jsonl")
        f = s["font"]
        parts.append(f"<h2>{e(name)}</h2><p>內文字級 {f['body']} pt；標題門檻 {f['threshold']} pt（內文 × {s['params']['heading_min_ratio']}）。"
                     f"字級（字數占比 ≥ 0.1%）：{e('、'.join(f'{k} pt {v} 字' for k, v in list(f['size_chars'].items())[:8]))}。"
                     f"標題字級出現的頁數：{e('、'.join(f'{k} pt {v} 頁' for k, v in f.get('heading_size_pages', {}).items()))}。</p>")
        if s["page_errors"]:
            parts.append(f"<p class='bad'>讀不了的頁：{e(json.dumps(s['page_errors'], ensure_ascii=False))}</p>")
        sample = pick_pages(lines, seed)
        for ln in sample:
            parts.append(page_block(e, name, ln, pdfs[name], ""))
        for ln, issue in pick_problem_pages(lines, seed, {x["page"] for x in sample}):
            bad = set(ln["issue_boxes"].get(issue, []))
            what = ("紅色虛線框是拿掉的字的位置（滑鼠停上去看是什麼字）" if issue == "hidden_dropped"
                    else f"紅框是「{e(issue)}」的框")
            extra = (f"<p class='bad'>問題頁：{e(ISSUE_TEXT[issue])}。這頁查到："
                     f"{e('、'.join(f'{k} {v}' for k, v in ln['issues'].items()))}。{what}。</p>")
            parts.append(page_block(e, name, ln, pdfs[name], f"（問題頁：{issue}）", bad, extra, issue == "hidden_dropped"))
    parts.append("</body></html>")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(parts), encoding="utf-8")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    pick = ap.add_mutually_exclusive_group(required=True)
    pick.add_argument("--firms", nargs="+", help="firm ids, all three years (+ one HTML per firm)")
    pick.add_argument("--reports", nargs="+", help="report names such as 1101_2025")
    pick.add_argument("--all", action="store_true")
    pick.add_argument("--sparse-stats", action="store_true",
                      help="count 列入分析 pictures on pages with <= sparse_max_chars characters, all catalog reports")
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--download-check", help="default: the newest results/download_check_*.json")
    ap.add_argument("--out", default=None, help="default: paths.page_graph in the config")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="recompute reports that are already done")
    ap.add_argument("--verify-running", action="store_true",
                    help="also read every page with get_text('dict') and compare running keys and positions")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--results", help="folder for the result JSON (default: paths.results; tests elsewhere)")
    ap.add_argument("--html-dir", help="folder for the HTML (default: paths.reports/page_graph)")
    ap.add_argument("--tag", help="label for the result file name")
    args = ap.parse_args()

    cfg = load_yaml(ROOT / args.config)
    pg = {**DEFAULTS, **(cfg.get("page_graph") or {})}
    pg.pop("heading_min_boxes", None)  # v1 key, replaced by heading_min_pages
    seed = cfg["seed"]
    out_dir = ROOT / (args.out or cfg["paths"]["page_graph"])
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = run_stamp()
    tag = args.tag or ("sparse" if args.sparse_stats else "all" if args.all else
                       f"{len(args.firms)}firms" if args.firms else f"{len(args.reports)}reports")
    log_path = out_dir / f"page_graph_{stamp}_{tag}.log"

    def log(msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    log("python " + " ".join(sys.argv))
    with open(ROOT / cfg["paths"]["catalog"], encoding="utf-8-sig", newline="") as f:
        catalog = list(csv.DictReader(f))
    dl_path = Path(args.download_check) if args.download_check else latest("download_check_*.json")
    dl = {r["name"]: r for r in json.loads(Path(dl_path).read_text(encoding="utf-8"))["reports"]}
    if args.sparse_stats:
        run_sparse(args, cfg, pg, catalog, dl, dl_path, log)
        return

    rows = []
    for r in catalog:
        name = f"{r['firm_id'].strip()}_{r['year'].strip()}"
        if args.all or (args.firms and r["firm_id"].strip() in args.firms) or (args.reports and name in args.reports):
            rows.append(dict(r, name=name))
    if not rows:
        sys.exit("沒有選到任何報告")
    not_checked = [r["name"] for r in rows if r["name"] not in dl]
    if not_checked:
        sys.exit(f"{dl_path} 沒有這些報告的 image_only：{not_checked}")
    skipped = sorted(r["name"] for r in rows if dl[r["name"]]["image_only"])
    rows = [r for r in rows if not dl[r["name"]]["image_only"]]
    extract = ROOT / cfg["paths"]["extract"]
    missing = [r["name"] for r in rows if not (extract / f"{r['name']}_pairs.jsonl").exists()]
    if missing:
        sys.exit(f"沒有抽取結果：{missing}")
    res_path = ROOT / (args.results or cfg["paths"]["results"]) / f"page_graph_{stamp}_{tag}.json"
    if res_path.exists():
        sys.exit(f"{res_path} 已經存在，不覆蓋")
    res_path.parent.mkdir(parents=True, exist_ok=True)
    if skipped:
        log(f"沒有文字層，跳過 {len(skipped)} 本：{', '.join(skipped)}")
    jobs = [{"name": r["name"], "pdf": str(ROOT / r["file_path"].strip()), "pairs": str(extract / f"{r['name']}_pairs.jsonl"),
             "out": str(out_dir), "pg": pg, "verify": args.verify_running} for r in rows]

    t0 = time.time()
    summaries, todo = {}, []
    for job in jobs:
        s = None if args.force else done_before(job)
        if s:
            summaries[job["name"]] = dict(s, reused=True)
        else:
            todo.append(job)
    if summaries:
        log(f"已經做過、沿用 {len(summaries)} 本（--force 重做）")

    def report(s):
        if "error" in s:
            log(f"[錯誤] {s['report']}: {s['error']}")
        else:
            chk = s["running_keys_check"]
            log(f"{s['report']}: 有圖的頁 {s['pages_with_pictures']}, 文字框 {s['boxes']}（導覽列等 {s['running_boxes']}）, "
                f"色塊 {s['containers']}, 內文 {s['font']['body']} pt, 標題層 {len(s['font']['bands'])}（{s['font']['method']}）, "
                f"{s['seconds']} 秒" + (f", running keys 一致={chk['same']}" if chk else ""))

    if args.workers > 1 and len(todo) > 1:
        with Pool(min(args.workers, len(todo))) as pool:
            for s in pool.imap_unordered(run_job, todo):
                summaries[s["report"]] = s
                report(s)
    else:
        for job in todo:
            s = run_job(job)
            summaries[s["report"]] = s
            report(s)
    errors = {n: s["error"] for n, s in summaries.items() if "error" in s}
    ok = {n: s for n, s in summaries.items() if "error" not in s}
    keys = ("pages_with_pictures", "picture_records", "pictures_visible_cut", "boxes", "running_boxes", "repeated_title_boxes",
            "containers", "panel_containers", "blocks_split", "lines_merged", "lines_joined", "paragraph_cuts")
    totals = {k: sum(s[k] for s in ok.values()) for k in keys}
    totals["reports"] = len(ok)
    for k in ("roles", "fills_dropped", "text_dropped", "issues", "issue_pages"):
        totals[k] = dict(sum((collections.Counter(s[k]) for s in ok.values()), collections.Counter()))
    totals["garbled_chars"] = sum(s["garbled"].get("garbled_chars", 0) for s in ok.values())
    totals["chars"] = sum(s["garbled"].get("chars", 0) for s in ok.values())
    totals["hidden_test_candidate_spans"] = sum(s["hidden_test"]["candidate_spans"] for s in ok.values())
    totals["hidden_test_errors"] = sum(s["hidden_test"]["errors"] for s in ok.values())
    checks = [s["running_keys_check"] for s in ok.values() if s.get("running_keys_check")]
    totals["running_keys_checked"] = len(checks)
    totals["running_keys_same"] = sum(c["same"] for c in checks)
    reg = regression_checks(out_dir, set(ok))
    data_files = [cfg["paths"]["catalog"], str(dl_path)] + [str(Path(j["pairs"]).relative_to(ROOT)) for j in jobs]
    meta = run_meta(args.config, data_files)
    result = {"meta": meta, "version": VERSION, "params": pg, "tag": tag, "out_dir": str(out_dir),
              "download_check": str(dl_path), "skipped_image_only": skipped, "errors": errors,
              "seconds": round(time.time() - t0, 1), "totals": totals, "regression_checks": reg,
              "per_report": {n: {k: v for k, v in s.items() if k not in ("params",)} for n, s in sorted(ok.items())}}
    res_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    log(json.dumps(totals, ensure_ascii=False))
    for c in reg:
        log(f"regression {'OK ' if c['ok'] else 'BAD'} {c['report']} p{c['page']}: {c['check']} {json.dumps(c['detail'], ensure_ascii=False)[:200]}")
    log(f"wrote {res_path}")

    if args.firms and not args.no_html:
        names = {r["firm_id"].strip(): r["firm_name"].strip() for r in rows}
        pdfs = {j["name"]: j["pdf"] for j in jobs}
        for firm in args.firms:
            sums = [ok[n] for n in sorted(ok) if n.split("_")[0] == firm]
            if not sums:
                continue
            path = (Path(args.html_dir) if args.html_dir else ROOT / cfg["paths"]["reports"] / "page_graph") / f"{firm}.html"
            write_firm_html(path, firm, names.get(firm, ""), sums, pdfs, out_dir, seed, meta,
                            [n for n in skipped if n.split("_")[0] == firm], reg)
            log(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
