"""Describe the reports that have no text layer (image_only in results/download_check_*.json).

A text layer is the text in a PDF that a computer can select, copy and search.
check_downloads.py marks a report image_only when no page has more than 20 non-space
characters. This script checks every page of those reports for any text at all, measures
12 evenly spaced pages of each (fonts, text operators, image placements, Bezier curves)
to say what kind of file it is, and bins all reports by their share of text pages to show
whether the image_only cut is clean.

    python src/inspect_no_text.py --download-check results/download_check_20261018_1603.json
        [--vector-check results/photo_filter_vector_20261018_1628.json]

Writes results/no_text_reports_{stamp}.json and reports/no_text_reports_{stamp}.html
(the HTML has small page pictures, so it stays in reports/, which is not in git).
"""
import argparse
import base64
import html
import io
import json
import re
import statistics as st
from collections import Counter
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

from meta import run_meta, run_stamp

N_SAMPLE = 12      # pages measured per report
MIN_PX = 150       # same size floor as the extraction
THUMB_DPI = 40
BINS = ("0%", "0–50%", "50–80%", "80–95%", "95–100%")
KIND_LABEL = {"outlines": "字轉成向量外框：畫面上看得到字，但字是線條畫的，選不到也搜不到",
              "full_page_image": "每頁是一張整頁圖片：字和照片都在圖片裡",
              "tiles": "每頁切成很多小圖塊：字和照片都在圖塊裡"}


def share_bin(share):
    if share == 0:
        return BINS[0]
    for hi, label in ((0.5, BINS[1]), (0.8, BINS[2]), (0.95, BINS[3])):
        if share < hi:
            return label
    return BINS[4]


def measure(page):
    area = page.rect.get_area()
    infos = page.get_image_info()
    shares = [(pymupdf.Rect(x["bbox"]) & page.rect).get_area() / area for x in infos]
    return {"fonts_listed": len(page.get_fonts()),
            "text_operators": len(re.findall(rb"\bBT\b", page.read_contents())),
            "images": len(infos),
            "small_images": sum(1 for x in infos if min(x["width"], x["height"]) < MIN_PX),
            "largest_image_share": max(shares, default=0.0),
            "image_cover_share": min(1.0, sum(shares)),
            "bezier_curves": sum(1 for g in page.get_drawings() for it in g["items"] if it[0] == "c")}


def kind_of(med):
    if med["bezier_curves"] >= 100:
        return "outlines"
    if med["largest_image_share"] >= 0.9:
        return "full_page_image"
    return "tiles"


def thumbnail(page):
    """Small picture of the page with every image placement outlined in blue."""
    img = Image.open(io.BytesIO(page.get_pixmap(dpi=THUMB_DPI).tobytes("png"))).convert("RGB")
    draw, s = ImageDraw.Draw(img), THUMB_DPI / 72
    for x in page.get_image_info():
        r = pymupdf.Rect(x["bbox"]) & page.rect
        if r.is_empty:  # placed off the page
            continue
        draw.rectangle([r.x0 * s, r.y0 * s, r.x1 * s, r.y1 * s], outline=(0, 90, 220), width=1)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()


def inspect(path):
    with pymupdf.open(path) as doc:
        n = doc.page_count
        idx = sorted({round(i * (n - 1) / (N_SAMPLE - 1)) for i in range(N_SAMPLE)})
        per = [measure(doc[i]) for i in idx]
        med = {k: round(st.median(m[k] for m in per), 2) for k in per[0]}
        show = idx[len(idx) // 2]
        rec = {"pages": n, "pages_with_any_text": sum(1 for p in doc if p.get_text().strip()),
               "sampled_pages": [i + 1 for i in idx], "median_per_sampled_page": med,
               "text_operators_in_sampled_pages": sum(m["text_operators"] for m in per),
               "kind": kind_of(med), "producer": doc.metadata.get("producer") or "",
               "creator": doc.metadata.get("creator") or "", "picture_page": show + 1}
        return rec, thumbnail(doc[show])


def write_html(path, summary, recs, thumbs):
    e = html.escape
    rows = "".join(f"<tr><td>{e(b)}</td><td>{n}</td></tr>" for b, n in summary["text_page_share_bins"].items())
    low = "、".join(f"{x['report']}（{x['share']:.0%}）" for x in summary["lowest_nonzero_text_share"])
    cards = []
    for r in recs:
        m = r["median_per_sampled_page"]
        cards.append(
            f"<div class=card><img src='data:image/jpeg;base64,{thumbs[r['report']]}'>"
            f"<p><b>{e(r['report'])} {e(r['firm'])}</b>（{e(r['split'])}），第 {r['picture_page']} 頁；藍框是 PDF 裡的圖片</p>"
            f"<p>{e(KIND_LABEL[r['kind']])}</p>"
            f"<p>{r['pages']} 頁，有字的頁 {r['pages_with_any_text']} 頁。抽查的 {len(r['sampled_pages'])} 頁中位數：圖片 {m['images']:g} 張"
            f"（短邊不到 {MIN_PX} px 的 {m['small_images']:g} 張），最大一張占頁面 {m['largest_image_share']:.0%}；"
            f"曲線 {m['bezier_curves']:,.0f} 條；列出的字型 {m['fonts_listed']:g} 個，寫字的指令 {m['text_operators']:g} 個</p>"
            f"<p>製作工具：{e(r['producer'] or '（沒寫）')}</p></div>")
    page = f"""<!doctype html><meta charset=utf-8><title>沒有文字層的報告</title>
<style>body{{font-family:sans-serif;max-width:1100px;margin:20px auto;padding:0 16px;line-height:1.5}}
table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:4px 10px}}
.grid{{display:flex;flex-wrap:wrap;gap:20px}}.card{{width:340px}}.card img{{max-width:340px;border:1px solid #999}}</style>
<h1>沒有文字層的報告：{summary['no_text_layer']} / {summary['reports']} 本</h1>
<p>文字層就是 PDF 裡電腦選得到、複製得到、搜得到的字。下載檢查把「沒有任何一頁超過 20 個字」的報告標成沒有文字層；這裡每一頁再查一次，連 1 個字都沒有。</p>
<h2>全部報告：有字的頁占全書幾成</h2><table><tr><th>有字的頁占全書</th><th>報告數</th></tr>{rows}</table>
<p>有字的報告裡，比例最低的是：{low}</p>
<h2>這幾本是什麼樣的檔案</h2><div class=grid>{''.join(cards)}</div>"""
    path.parent.mkdir(exist_ok=True)
    path.write_text(page, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--download-check", required=True, help="results/download_check_*.json")
    ap.add_argument("--vector-check", help="results/photo_filter_vector_*.json, to count the images these reports add")
    ap.add_argument("--raw", default="data/raw")
    args = ap.parse_args()
    dl = json.loads(Path(args.download_check).read_text(encoding="utf-8"))["reports"]
    no_text = [r for r in dl if r["image_only"]]
    pdfs = [str(Path(args.raw) / f"{r['name']}.pdf") for r in no_text]
    out, thumbs = [], {}
    for r, pdf in zip(no_text, pdfs):
        rec, thumbs[r["name"]] = inspect(pdf)
        out.append({"report": r["name"], "firm": r["firm_name"], "split": r["split"], **rec})
    bins = Counter(share_bin(r["text_pages"] / r["pages"]) for r in dl)
    lowest = sorted((r for r in dl if r["text_pages"]), key=lambda r: r["text_pages"] / r["pages"])[:5]
    usable = Counter(r["firm_id"] for r in dl if not r["image_only"])
    summary = {
        "reports": len(dl), "firms": len({r["firm_id"] for r in dl}), "no_text_layer": len(no_text),
        "no_text_firms": sorted({r["firm_id"] for r in no_text}),
        "no_text_by_split": dict(Counter(r["split"] for r in no_text)),
        "no_text_by_kind": {k: [x["report"] for x in out if x["kind"] == k] for k in KIND_LABEL},
        "no_text_with_any_text_page": [x["report"] for x in out if x["pages_with_any_text"]],
        "firms_with_3_usable_years": sum(v == 3 for v in usable.values()),
        "text_page_share_bins": {b: bins.get(b, 0) for b in BINS},
        "lowest_nonzero_text_share": [{"report": r["name"], "text_pages": r["text_pages"], "pages": r["pages"],
                                       "share": round(r["text_pages"] / r["pages"], 3)} for r in lowest],
    }
    data_files = [args.download_check] + pdfs
    if args.vector_check:
        v = json.loads(Path(args.vector_check).read_text(encoding="utf-8"))
        summary["extracted_images"] = {k: {"these_reports": sum(v["per_report"][r["name"]][k] for r in no_text),
                                           "all_reports": v["totals"][k]} for k in ("records", "analysed")}
        data_files.insert(1, args.vector_check)
    stamp = run_stamp()
    res = {"meta": run_meta(None, data_files, ("pymupdf", "pillow")),
           "rule": ("check_downloads.py counts a page as a text page when it has more than 20 non-space "
                    "characters and marks a report image_only when no page is a text page. "
                    "pages_with_any_text counts pages with any text at all."),
           "method": (f"Every page checked for text; {N_SAMPLE} evenly spaced pages per report measured. "
                      "kind: outlines when the median page has >= 100 Bezier curves; otherwise full_page_image "
                      "when the largest image covers >= 90% of the median page; otherwise tiles."),
           "summary": summary, "reports": out}
    Path("results").mkdir(exist_ok=True)
    jp = Path("results") / f"no_text_reports_{stamp}.json"
    jp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    hp = Path("reports") / f"no_text_reports_{stamp}.html"
    write_html(hp, summary, out, thumbs)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for x in out:
        print(x["report"], x["kind"], x["pages_with_any_text"], x["median_per_sampled_page"], x["producer"])
    print(jp, hp)


if __name__ == "__main__":
    main()
