"""E / S / G sections (PLAN §5 step 7, Table 8): which part of the report each photo's page belongs to.

Per page with kept photos: the heading chain before the first photo in reading order (context_link.heading_chain:
the last h1, h2, h3, carried over from earlier pages) and the page's running text (headers, footers, menus).
  1. keywords  the chain's headings (not the running text, which often lists every chapter) are matched against a
               fixed list per class; exactly one class with hits decides (source 'keyword').
  2. otherwise the language model (evidence.llm) picks E / S / G / 公司概況 / 其他 from the chain and the running text
               (configs/prompts/section.txt; cached in work/section/llm.jsonl; source 'llm').
10/20: on the development reports the h1 role is often a large number or callout (「降低 20 %」), so the whole chain is
used, not h1 alone. The keyword list was written before looking at any test report.
Annotation S (PLAN §6): Eden checks 50 pages (s-page: a self-contained HTML page with the page image and the
program's class preselected; s-import: his answers → agreement).

  python src/section_map.py run --split dev
  python src/section_map.py s-page --split dev
  python src/section_map.py s-import --split dev work/annotation/S/returns/S_eden_dev_....json
"""
import argparse
import base64
import collections
import csv
import hashlib
import html
import json
import os
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "work" / "models" / "hf"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(ROOT / "src"))
import yaml  # noqa: E402

import frozen  # noqa: E402
from meta import run_meta, run_stamp, sha256_file  # noqa: E402

CLASSES = ("E", "S", "G", "公司概況", "其他")
KEYWORDS = {
    "E": ("環境", "氣候", "碳", "能源", "綠色", "綠能", "水資源", "用水", "廢棄物", "污染", "汙染", "生態", "生物多樣性",
          "循環", "自然", "淨零", "環保", "溫室氣體", "節能", "減量"),
    "S": ("社會", "員工", "人才", "職場", "健康", "職業安全", "職安", "安全衛生", "人權", "社區", "公益", "共融", "共榮",
          "關懷", "志工", "教育訓練", "培育", "福利", "勞雇", "客戶", "消費者", "產品責任", "產品安全"),
    "G": ("治理", "誠信", "風險", "法規", "遵循", "資訊安全", "資安", "董事會", "倫理", "經營績效", "財務", "稅務",
          "供應鏈", "供應商"),
    "公司概況": ("關於", "公司概況", "企業概況", "經營者", "董事長的話", "總經理的話", "致詞", "永續管理", "永續發展策略",
             "利害關係人", "重大主題", "報告書", "前言"),
    "其他": ("附錄", "目錄", "CONTENTS", "索引"),
}
SEED = "2026:S"


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def norm(t):
    return " ".join((t or "").split())


def keyword_class(chain_texts):
    hits = {c: sum(1 for t in chain_texts for k in ks if k in t) for c, ks in KEYWORDS.items()}
    got = [c for c, n in hits.items() if n]
    return (got[0] if len(got) == 1 else None), hits


def pages_of(split):
    rows = collections.defaultdict(list)
    with open(ROOT / "work" / "photo_filter.csv", encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if d["split"] == split and d["keep"] == "1":
                rows[(d["report"], int(d["page"]))].append(d["crop"])
    return rows


def cmd_run(args, cfg):
    import context_link as cl
    import evidence as E
    t0 = time.time()
    pages = pages_of(args.split)
    prompt_path = ROOT / "configs" / "prompts" / "section.txt"
    tpl, phash = prompt_path.read_text(encoding="utf-8"), sha256_file(prompt_path)
    ev = cfg["evidence"]
    cache = E.Cache(ROOT / "work" / "section" / "llm.jsonl")
    M = E.Models(ev)
    out_pages, out_photos = [], []
    for (rep, p), crops in sorted(pages.items()):
        pg = next((x for x in cl.load_pages(rep) if x["page"] == p), None)
        pics = sorted((x for x in (pg or {}).get("pictures", []) if x["crop"] in set(crops)), key=lambda x: x.get("order") or 0)
        chain = cl.heading_chain(rep, p, (pics[0].get("order") or 0) if pics else 0) if pg else []
        chain_t = [norm(b["text"])[:60] for b in chain]
        running = list(dict.fromkeys(norm(b["text"])[:40] for b in (pg or {}).get("boxes", []) if b.get("is_running") and norm(b["text"])))
        running_t = "｜".join(running)[:200]
        cls, hits = keyword_class(chain_t)
        source = "keyword"
        if cls is None:
            text = tpl.replace("{chain}", " > ".join(chain_t) or "（沒有）").replace("{running}", running_t or "（沒有）")
            k = sha(f"{ev['llm_revision']}|{phash}|{text}")
            r = cache.get(k)
            if r is None:
                r = {"key": k, "output": M.generate(text, max_new_tokens=8)}
                cache.put(r)
            ans = r["output"].strip()
            cls = next((c for c in ("公司概況", "其他", "E", "S", "G") if c in ans), "其他")
            source = "llm"
        out_pages.append({"report": rep, "page": p, "firm": rep.split("_")[0], "chain": chain_t, "running": running_t,
                          "class": cls, "source": source, "keyword_hits": {c: n for c, n in hits.items() if n}, "photos": len(crops)})
        out_photos += [{"crop": c, "report": rep, "page": p, "class": cls} for c in crops]
    sdir = ROOT / "work" / "section"
    sdir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("pages", out_pages), ("photos", out_photos)):
        with open(sdir / f"{args.split}_{name}.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    res = {"split": args.split, "pages": len(out_pages), "photos": len(out_photos),
           "pages_by_class": dict(collections.Counter(r["class"] for r in out_pages)),
           "photos_by_class": dict(collections.Counter(r["class"] for r in out_photos)),
           "source": dict(collections.Counter(r["source"] for r in out_pages)),
           "llm": {"model": ev["llm"], "revision": ev["llm_revision"], "new": cache.new, "prompt_sha256": phash},
           "keywords": {c: list(k) for c, k in KEYWORDS.items()}, "seconds": round(time.time() - t0, 1)}
    write_result(f"section_map_{args.split}", res, [prompt_path])
    print(json.dumps({k: res[k] for k in ("pages", "photos", "pages_by_class", "photos_by_class", "source", "seconds")}, ensure_ascii=False))


def write_result(name, res, inputs):
    stamp = run_stamp()
    rp = ROOT / "results" / f"{name}_{stamp}.json"
    k = 2
    while rp.exists():
        rp = ROOT / "results" / f"{name}_{stamp}_{k}.json"
        k += 1
    rp.write_text(json.dumps({"meta": run_meta("configs/default.yaml", [ROOT / "work" / "photo_filter.csv"] + list(inputs)), **res},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {rp}")
    return rp


def cmd_s_page(args, cfg):
    """The 50 pages for annotation S as one self-contained HTML page (page images inline, answers downloaded)."""
    import pymupdf
    rows = [json.loads(l) for l in open(ROOT / "work" / "section" / f"{args.split}_pages.jsonl", encoding="utf-8")]
    pick = random.Random(SEED).sample(rows, min(args.n, len(rows)))
    cards = []
    for i, r in enumerate(pick):
        doc = pymupdf.open(ROOT / "data" / "raw" / f"{r['report']}.pdf")
        pix = doc[r["page"] - 1].get_pixmap(dpi=50)
        img = base64.b64encode(pix.tobytes("jpeg")).decode()
        doc.close()
        opts = "".join(f'<label><input type="radio" name="q{i}" value="{c}"{" checked" if c == r["class"] else ""}> {c}</label>'
                       for c in CLASSES)
        cards.append(f'<section data-id="{r["report"]}|{r["page"]}"><img src="data:image/jpeg;base64,{img}" alt="">'
                     f'<div class="txt"><h2>{i + 1}. {html.escape(r["report"])} 第 {r["page"]} 頁</h2>'
                     f'<div class="mut">章節標題：{html.escape(" > ".join(r["chain"]) or "（沒有）")}</div><div class="opts">{opts}</div></div></section>')
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>章節歸類抽查</title><style>
:root{{--bg:#fafafa;--fg:#222;--mut:#666;--line:#ddd}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1b1b;--fg:#eee;--mut:#aaa;--line:#444}}}}
body{{font-family:system-ui,"Microsoft JhengHei",sans-serif;margin:0 auto;padding:16px;background:var(--bg);color:var(--fg);max-width:1000px;line-height:1.6}}
section{{display:flex;flex-wrap:wrap;gap:14px;border-top:1px solid var(--line);padding:12px 0}} section img{{max-width:min(420px,100%);height:auto;align-self:flex-start}}
.txt{{flex:1;min-width:240px}} h2{{font-size:15px;margin:0 0 4px}} .mut{{color:var(--mut);font-size:13px}} .opts label{{display:inline-block;margin:6px 10px 0 0;font-size:15px}}
button{{font-size:16px;padding:8px 16px;margin:16px 0}}</style></head><body>
<h1 style="font-size:20px">章節歸類抽查（{len(pick)} 頁）</h1>
<p>每一頁屬於哪一類？E 環境、S 社會、G 治理（含供應鏈、資安）、公司概況（關於本公司、經營者的話、永續策略、利害關係人與重大主題）、其他（目錄、附錄、看不出來）。
已經選好的是程式的答案，<b>對就不用動，錯就改</b>。全部看完按最下面的「下載答案」，把檔案交給本機 Claude。</p>
{''.join(cards)}
<button id="dl">下載答案</button>
<script>
document.getElementById('dl').onclick = () => {{
  const out = [...document.querySelectorAll('section')].map((s, i) => {{
    const [report, page] = s.dataset.id.split('|');
    const c = s.querySelector('input:checked');
    return {{report, page: +page, class: c ? c.value : null}};
  }});
  const t = new Date(), p = n => String(n).padStart(2, '0');
  const name = `S_eden_{args.split}_${{t.getFullYear()}}${{p(t.getMonth() + 1)}}${{p(t.getDate())}}_${{p(t.getHours())}}${{p(t.getMinutes())}}.json`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify({{task: 'S', split: '{args.split}', answers: out}}, null, 1)], {{type: 'application/json'}}));
  a.download = name; a.click();
}};
</script></body></html>"""
    out = ROOT / "reports" / "annotation_S" / f"S_check_{args.split}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out}（{len(pick)} 頁）")


def cmd_s_import(args, cfg):
    got = json.loads(Path(args.file).read_text(encoding="utf-8"))
    prog = {(r["report"], r["page"]): r for r in (json.loads(l) for l in open(ROOT / "work" / "section" / f"{args.split}_pages.jsonl", encoding="utf-8"))}
    ans = [a for a in got["answers"] if a.get("class")]
    pairs = [(a["class"], prog[(a["report"], a["page"])]["class"], prog[(a["report"], a["page"])]["source"]) for a in ans]
    out_dir = ROOT / "annotations" / args.split
    with open(out_dir / "S.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for a in ans:
            f.write(json.dumps({"report": a["report"], "page": a["page"], "class": a["class"]}, ensure_ascii=False) + "\n")
    res = {"split": args.split, "file": args.file, "pages": len(pairs), "same": sum(a == b for a, b, _ in pairs),
           "accuracy": round(sum(a == b for a, b, _ in pairs) / len(pairs), 4) if pairs else None,
           "by_source": {s: {"pages": sum(1 for *_, x in pairs if x == s), "same": sum(1 for a, b, x in pairs if x == s and a == b)}
                         for s in ("keyword", "llm")},
           "confusion_eden_by_program": {c: dict(collections.Counter(b for a, b, _ in pairs if a == c)) for c in CLASSES}}
    write_result(f"section_check_{args.split}", res, [Path(args.file)])
    print(json.dumps({k: res[k] for k in ("pages", "same", "accuracy", "by_source")}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "s-page", "s-import"):
        p = sub.add_parser(name)
        p.add_argument("--split", choices=["dev", "test"], required=True)
        if name == "s-page":
            p.add_argument("--n", type=int, default=50)
        if name == "s-import":
            p.add_argument("file")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))
    frozen.check(args.split, cfg)
    {"run": cmd_run, "s-page": cmd_s_page, "s-import": cmd_s_import}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
