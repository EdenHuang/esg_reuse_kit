"""Figures 2-4 of the paper (PLAN §3); figure 1 (pipeline) is TikZ in paper/figs/.

  python src/make_figs.py select --split test   # pick the examples (seeded) -> paper/figs/examples.json
  python src/make_figs.py draw                  # -> paper/figs/fig2_levels.pdf, fig3_anchoring.pdf, fig4_errors.pdf

Examples come from annotation R (human labels) and the measurement tool's output on the same split
(work/measure/{split}_r-items.jsonl). Until 10/27 the development split was used (cuts chosen there, so the agreement
shown was in-sample; paper/figs/examples_dev140.json keeps those picks); from 10/28 the test split, after
configs/frozen.yaml (rule 3), with the frozen cuts. English glosses of the report text are in paper/figs/glosses.yaml,
keyed by item; the figure shows the first sentence of the annotator's describing text. Picks are seeded (2026); an
item listed under `skip` in glosses.yaml (with the reason) is passed over and the next candidate is used. Figure 3:
the OCR example is a photo the tool also judged evidence (10/28); the logo example needs the annotator's logo tick and
a tool match, and falls back to a direct photo with a tool match when no photo has both (recorded in examples.json).
"""
import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPLIT, CTX_TAG = "dev", "dev140"   # set from --split in main()
sys.path.insert(0, str(ROOT / "src"))
FIGS = ROOT / "paper" / "figs"
CROPS = ROOT / "work" / "extract" / "crops"
LEVEL_EN = {"證據": "Evidence", "示意": "Illustration", "包裝": "Packaging", "無關": "Unrelated"}
L3_EN = {"direct": "direct", "topical": "topical", "unrelated": "unrelated"}


def jl(p):
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]


def human4(r):
    if r["level"] == "direct":
        return "證據" if (r["anchor_text"] or r["anchor_logo"]) else "示意"
    return {"topical": "包裝", "unrelated": "無關"}.get(r["level"])


def first_sentence(t, n=42):
    t = " ".join(t.split())
    for stop in "。！？":
        k = t.find(stop)
        if 0 < k < n:
            return t[:k + 1]
    return t[:n] + ("…" if len(t) > n else "")


def load():
    from PIL import Image
    R = {r["item"]: r for r in jl(ROOT / "annotations" / SPLIT / "R.jsonl")}
    M = {r["item"]: r for r in jl(ROOT / "work" / "measure" / f"{SPLIT}_r-items.jsonl")}
    G = {r["item"]: r for r in jl(ROOT / "work" / "context" / f"gold_{CTX_TAG}.jsonl")}
    H = {r["item"]: r for r in jl(ROOT / "work" / "context" / f"L4_heading_{CTX_TAG}.jsonl")}
    rows = []
    for i, r in R.items():
        if r["not_photo"] or not r["level"] or i not in M or r.get("unsure"):
            continue
        g, ctx = G.get(i), "text"
        if not g or not g.get("text", "").strip():   # no describing text: the annotator compared with the section heading
            g, ctx = H.get(i), "heading"
        if not g or not g.get("text", "").strip():
            continue
        with Image.open(CROPS / M[i]["crop"]) as im:
            w, h = im.size
        if min(w, h) < 250:
            continue
        rows.append({"item": i, "crop": M[i]["crop"], "report": M[i]["report"], "page": M[i]["page"],
                     "human": human4(r), "human_level": r["level"], "anchor_text": r["anchor_text"], "anchor_logo": r["anchor_logo"],
                     "tool": M[i]["level4"], "p": M[i]["p"], "anchored_ocr": M[i]["anchored_ocr"], "anchor_org": M[i]["anchor_org"],
                     "anchor_year": M[i]["anchor_year"], "logo_found": M[i]["logo_found"],
                     "text": first_sentence(g["text"]), "context": ctx, "size": [w, h]})
    return rows


def cmd_select(args):
    import yaml
    gl = yaml.safe_load(open(FIGS / "glosses.yaml", encoding="utf-8")) if (FIGS / "glosses.yaml").exists() else {}
    skip = set((gl or {}).get("skip", {}) or {})
    rows = [r for r in load() if r["item"] not in skip]

    def pick(tag, cond, n=1):
        c = sorted((r for r in rows if cond(r)), key=lambda r: r["item"])
        random.Random(f"2026:{tag}").shuffle(c)
        return [dict(r, role=tag) for r in c[:n]], len(c)

    out, pools = [], {}
    for lv in ("證據", "示意", "包裝", "無關"):
        got, pools[f"fig2:{lv}"] = pick(f"fig2:{lv}", lambda r, lv=lv: r["human"] == lv and r["tool"] == lv)
        out += got
    got, pools["fig3:logo"] = pick("fig3:logo", lambda r: r["anchor_logo"] and r["logo_found"])
    if not got:   # 10/28 (test split): no photo has both the annotator's logo tick and a tool match
        got, pools["fig3:logo_fallback"] = pick("fig3:logo", lambda r: r["logo_found"] and r["human_level"] == "direct")
        got = [dict(r, fallback="logo found by the tool; the annotator anchored the photo by text") for r in got]
    out += got
    logo_items = {r["item"] for r in got}   # 10/22: (a) and (b) must not show the same photo
    got, pools["fig3:ocr"] = pick("fig3:ocr", lambda r: r["anchor_text"] and r["anchored_ocr"] and r["anchor_org"] and r["tool"] == "證據"
                                  and r["item"] not in logo_items)   # 10/28: the anchoring shown leads to the level shown
    out += got
    errs = [("fig4:depiction_miss", lambda r: r["human"] in ("證據", "示意") and r["tool"] == "無關"),
            ("fig4:anchor_miss", lambda r: r["human"] == "證據" and r["tool"] == "示意"),
            ("fig4:too_generous", lambda r: r["human"] in ("包裝", "無關") and r["tool"] in ("證據", "示意")),
            ("fig4:packaging_dropped", lambda r: r["human"] == "包裝" and r["tool"] == "無關")]
    for tag, cond in errs:
        got, pools[tag] = pick(tag, cond)
        out += got
    FIGS.mkdir(parents=True, exist_ok=True)
    (FIGS / "examples.json").write_text(json.dumps({"split": SPLIT, "pools": pools, "examples": out}, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    for r in out:
        print(f"{r['role']:24s} {r['item']} {r['crop']:28s} 人 {r['human']} 工具 {r['tool']} p={r['p']}｜{r['text']}")
    print("candidates per role:", pools)


def logo_detail(crop, firm):
    """The cached homography of the reference logo that matched (evidence.logo_matches), largest inlier count."""
    best = None
    for r in jl(ROOT / "work" / "evidence" / "logo.jsonl"):
        if r["crop"] == crop and r.get("quad") and (best is None or r["inliers"] > best["inliers"]):
            best = r
    return best


def ocr_lines(crop):
    import hashlib
    import yaml
    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))
    lang = cfg["logo_refs"].get("ocr_lang", "chinese_cht")
    h = hashlib.sha1((CROPS / crop).read_bytes() + f"|photo|{lang}".encode()).hexdigest()
    for d in (ROOT / "work" / "measure" / "ocr_photo", ROOT / "work" / "evidence" / "ocr_photo"):
        f = d / f"{h}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))["texts"]
    return []


def cmd_draw(args):
    import textwrap
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import Polygon
    from PIL import Image
    import yaml
    cjk = font_manager.FontProperties(fname="C:/Windows/Fonts/msjh.ttc")
    plt.rcParams.update({"pdf.fonttype": 42, "font.family": "DejaVu Sans", "font.size": 6.5})
    ex = json.loads((FIGS / "examples.json").read_text(encoding="utf-8"))["examples"]
    gl = yaml.safe_load(open(FIGS / "glosses.yaml", encoding="utf-8"))["gloss"]
    by = {}
    for r in ex:
        by.setdefault(r["role"].split(":")[0], []).append(r)
    FS, LH = 6.2, 0.083          # font size (pt) and line height (axes fraction of a 1.0-high text box)

    def cjk_lines(t, n):
        t = t.replace(" / ", "／")
        return [t[i:i + n] for i in range(0, len(t), n)][:4]

    def text_block(ax, blocks, width_in):
        """blocks: (kind, text) with kind cjk / en / en_i; wraps by hand (matplotlib does not break Chinese) and spaces
        lines in points from the top of the axes, so the layout does not depend on the axes height."""
        ax.axis("off")
        n_cjk = max(8, int(width_in * 72 / (FS * 1.25)))
        n_en = max(16, int(width_in * 72 / (FS * 0.64)))
        y = 0.0
        for kind, t in blocks:
            if kind == "cjk" and t.isascii():
                kind = "en"
            lines = cjk_lines(t, n_cjk) if kind == "cjk" else textwrap.wrap(t, n_en - (3 if kind == "en_i" else 0))[:6]
            for ln in lines:
                ax.annotate(ln, (0, 1), xycoords="axes fraction", xytext=(0, y), textcoords="offset points", va="top",
                            fontproperties=cjk if kind == "cjk" else None, fontsize=FS, style="italic" if kind == "en_i" else "normal")
                y -= FS * 1.3
            y -= FS * 0.45

    def photo(ax, path, title, rgba=False, box=4 / 3):
        with Image.open(path) as im:
            im = im.convert("RGBA" if rgba else "RGB")
            if not rgba and box:   # letterbox to one aspect ratio so the panels line up (nothing is cropped away)
                w, h = im.size
                W, H = (max(w, round(h * box)), max(h, round(w / box)))
                pad = Image.new("RGB", (W, H), "white")
                pad.paste(im, ((W - w) // 2, (H - h) // 2))
                im = pad
            ax.imshow(im)
        ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values():
            s.set_linewidth(0.4)
        ax.set_title(title, fontsize=7, fontweight="bold", loc="left", pad=3)

    def source(r):
        f, y = r["report"].split("_")
        return f"Source: firm {f}, {y} report, p. {r['page']}"

    def context(r):
        return ([("en", "Section heading (no describing text):")] if r["context"] == "heading" else []) + \
               [("cjk", r["text"]), ("en_i", "\u201c" + gl[r["item"]] + "\u201d")]

    def row_figure(rows, name, title_of, lines_of):
        n = len(rows)
        fig = plt.figure(figsize=(7.1, 3.25))
        gs = fig.add_gridspec(2, n, height_ratios=[1.0, 1.05], hspace=0.04, wspace=0.10)
        w_in = 7.1 / n - 0.12
        for k, r in enumerate(rows):
            photo(fig.add_subplot(gs[0, k]), CROPS / r["crop"], title_of(k, r))
            text_block(fig.add_subplot(gs[1, k]), context(r) + [("en", x) for x in lines_of(r)], w_in)
        fig.savefig(FIGS / name, bbox_inches="tight", pad_inches=0.02)
        plt.close(fig)
        print("wrote", FIGS / name)

    def anchored_by(r):
        kinds = [k for k, v in (("text", r["anchor_text"]), ("logo", r["anchor_logo"])) if v]
        return f"anchored ({' and '.join(kinds)})" if kinds else "not anchored"

    order = ["證據", "示意", "包裝", "無關"]
    f2 = sorted(by["fig2"], key=lambda r: order.index(r["human"]))
    row_figure(f2, "fig2_levels.pdf", lambda k, r: f"({'abcd'[k]}) {LEVEL_EN[r['human']]}",
               lambda r: [f"Annotator: {L3_EN[r['human_level']]}" + (f", {anchored_by(r)}" if r["human_level"] == "direct" else ""),
                          source(r)])

    kind = {"depiction_miss": "Depiction missed", "anchor_miss": "Anchor missed", "too_generous": "Too generous",
            "packaging_dropped": "Topic missed"}
    f4 = sorted(by["fig4"], key=lambda r: list(kind).index(r["role"].split(":")[1]))
    row_figure(f4, "fig4_errors.pdf", lambda k, r: f"({'abcd'[k]}) {kind[r['role'].split(':')[1]]}",
               lambda r: [f"Annotator: {LEVEL_EN[r['human']]}; tool: {LEVEL_EN[r['tool']]}",
                          f"p(direct) {r['p']['direct']:.4f}, p(unrelated) {r['p']['unrelated']:.4f}", source(r)])

    # figure 3: (a) text anchor found by OCR, (b) logo anchor found by matching the report's own logo
    f3 = {r["role"].split(":")[1]: r for r in by["fig3"]}
    fig = plt.figure(figsize=(7.1, 2.6))
    gs = fig.add_gridspec(2, 3, width_ratios=[1.35, 0.4, 1.35], height_ratios=[1.0, 0.5], hspace=0.05, wspace=0.10)
    r = f3["ocr"]
    photo(fig.add_subplot(gs[0, 0]), CROPS / r["crop"], "(a) Text anchor found by OCR", box=None)
    hit = [t for t in ocr_lines(r["crop"]) if r["anchor_org"] and r["anchor_org"] in t]
    text_block(fig.add_subplot(gs[1, 0]), [("en", "OCR line in the photo:"), ("cjk", hit[0] if hit else ""),
                                           ("en", f"= the firm's {gl.get(r['item'] + ':org', 'name')}, so the photo is anchored."), ("en", source(r))], 2.8)
    r = f3["logo"]
    det = logo_detail(r["crop"], r["firm"] if "firm" in r else r["report"].split("_")[0])
    firm, ref = det["ref"].split("/")
    photo(fig.add_subplot(gs[0, 1]), ROOT / "work" / "logo_refs" / firm / "refs" / ref, "Reference logo", rgba=True)
    axp = fig.add_subplot(gs[0, 2])
    photo(axp, CROPS / r["crop"], "(b) Logo anchor found by matching", box=None)   # no padding: the quad is in photo pixels
    axp.add_patch(Polygon(det["quad"], closed=True, fill=False, edgecolor="#00c853", linewidth=1.5))
    text_block(fig.add_subplot(gs[1, 1]), [("en", "Taken from the report itself (repeated small images and covers).")], 0.75)
    text_block(fig.add_subplot(gs[1, 2]), [("en", f"SuperPoint + LightGlue, MAGSAC homography: {det['inliers']} inliers "
                                                  f"(threshold {args.min_inliers}); green = the reference projected into the photo."),
                                           ("en", source(r))], 2.8)
    fig.savefig(FIGS / "fig3_anchoring.pdf", bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    print("wrote", FIGS / "fig3_anchoring.pdf")
    sources = {"generated_by": "src/make_figs.py", "examples": "paper/figs/examples.json", "glosses": "paper/figs/glosses.yaml",
               "split": SPLIT,
               "inputs": [f"annotations/{SPLIT}/R.jsonl", f"work/measure/{SPLIT}_r-items.jsonl", f"work/context/gold_{CTX_TAG}.jsonl",
                          f"work/context/L4_heading_{CTX_TAG}.jsonl", "work/evidence/logo.jsonl", "work/evidence/ocr_photo/"]}
    (FIGS / "sources.yaml").write_text(yaml.safe_dump(sources, allow_unicode=True, sort_keys=False), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--split", choices=["dev", "test"], default="dev", help="10/27: test after configs/frozen.yaml")
    p = sub.add_parser("draw")
    p.add_argument("--min-inliers", type=int, default=40)
    p.add_argument("--split", choices=["dev", "test"], default=None, help="default: the split recorded in examples.json")
    args = ap.parse_args()
    global SPLIT, CTX_TAG
    if args.cmd == "draw" and args.split is None:
        try:
            args.split = json.loads((ROOT / "paper" / "figs" / "examples.json").read_text(encoding="utf-8")).get("split", "dev")
        except Exception:
            args.split = "dev"
    SPLIT = args.split
    CTX_TAG = {"dev": "dev140", "test": "test340"}[SPLIT]
    if SPLIT == "test" and not (ROOT / "configs" / "frozen.yaml").exists():
        sys.exit("測試集要等 configs/frozen.yaml（鐵則 3）")
    {"select": cmd_select, "draw": cmd_draw}[args.cmd](args)


if __name__ == "__main__":
    main()
