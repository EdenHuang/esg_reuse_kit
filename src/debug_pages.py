"""Draw the pairing result on selected pages so it can be checked by eye.

Red = picture, green = caption, orange = text printed on the picture, blue = nearest paragraph.
Usage: python -I debug_pages.py --pdf REPORT.pdf --pairs outputs/x_pairs.jsonl --pages 31 58 --out outputs/debug
"""
import argparse
import json
from pathlib import Path

import pymupdf


def find_block_rect(page, text):
    """Locate a text block by its text (first 20 characters) to draw it."""
    key = text[:20]
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        t = "".join(s["text"] for l in b["lines"] for s in l["spans"]).strip()
        if t and t.replace(" ", "")[:20].startswith(key.replace(" ", "")[:12]):
            return pymupdf.Rect(b["bbox"])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--pages", type=int, nargs="+", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    recs = [json.loads(l) for l in open(args.pairs, encoding="utf-8")]
    doc = pymupdf.open(args.pdf)
    for pno in args.pages:
        page = doc[pno - 1]
        for r in (r for r in recs if r["page"] == pno):
            page.draw_rect(pymupdf.Rect(r["bbox"]), color=(1, 0, 0), width=3)
            if r["caption"]:
                rr = find_block_rect(page, r["caption"])
                if rr:
                    page.draw_rect(rr, color=(0, 0.7, 0), width=2)
            for t in r["overlay_text"].split(" / "):
                if t:
                    rr = find_block_rect(page, t)
                    if rr:
                        page.draw_rect(rr, color=(1, 0.5, 0), width=2)
            if r.get("local_heading"):
                rr = find_block_rect(page, r["local_heading"])
                if rr:
                    page.draw_rect(rr, color=(0.6, 0, 0.8), width=2)
            if r["paragraphs"]:
                rr = find_block_rect(page, r["paragraphs"][0]["text"])
                if rr:
                    page.draw_rect(rr, color=(0, 0, 1), width=2)
        name = Path(args.pairs).stem.replace("_pairs", "")
        page.get_pixmap(dpi=55).save(str(out / f"{name}_p{pno:03d}.png"))
    print("done")


if __name__ == "__main__":
    main()
