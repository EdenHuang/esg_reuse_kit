"""Ask the vision-language model "is this a camera photo?" (configs/prompts/is_photo.txt) for development images,
to check the photo filter (PLAN §5 step 3: if OpenCLIP misses the target, a vision-language model answers instead).

Two sets, development split only (CLAUDE.md rule 3), plus with --all every image given to the photo model in both
splits, so that a filter chosen on the development labels can be applied later (test: answers cached and counted,
never scored against labels before configs/frozen.yaml):
  labelled  the 100 annotation-A development items: how well the model itself agrees with the annotators
  low       every development image given to the photo model whose photo score is below the current threshold:
            how many photos the filter may be dropping (the 10/19 threshold rests on 10 labelled images there)
p(yes) is the softmax of the logits of 「是」 and 「否」 at the first answer position (no sampling), as in
check_models.py. Cached in work/evidence/vlm_is_photo.jsonl (model revision, prompt hash, crop). Writes
results/photo_vlm_dev_{stamp}.json and reports/photo_vlm_dev_{stamp}.html (the low-score images the model calls
photos, highest first, for a look by eye).

  python src/photo_vlm.py
"""
import argparse
import base64
import collections
import csv
import hashlib
import html
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "work" / "models" / "hf"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp, sha256_file  # noqa: E402

import yaml  # noqa: E402

BEFORE_MODEL = {"image_only_report", "cover_page", "vector_shading", "layout_repeat", "duplicate"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", type=int, default=80, help="low-score images shown in the HTML")
    ap.add_argument("--all", action="store_true", help="also every image given to the photo model in both splits "
                    "(answers cached for a later filter; only counts are reported for the test split, no scores)")
    args = ap.parse_args()
    import torch
    from PIL import Image
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForImageTextToText, AutoProcessor

    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))
    ev, thr = cfg["evidence"], float(cfg["photo_filter"]["photo_threshold"])
    prompt_path = ROOT / "configs" / "prompts" / "is_photo.txt"
    prompt, phash = prompt_path.read_text(encoding="utf-8").strip(), sha256_file(prompt_path)
    rows, every = {}, {}
    with open(ROOT / "work" / "photo_filter.csv", encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if not (set(filter(None, d["exclude"].split("|"))) & BEFORE_MODEL):
                every[d["crop"]] = d
            if d["split"] == "dev":
                rows[d["crop"]] = d
    items = [json.loads(l) for l in open(ROOT / "annotations" / "dev" / "A_items.jsonl", encoding="utf-8")]
    labels = {}
    for l in open(ROOT / "annotations" / "dev" / "A.jsonl", encoding="utf-8"):
        r = json.loads(l)
        labels.setdefault(r["item"], []).append(r["answer"])
    labelled = [(it["crop"], labels[it["item"]][0]) for it in items if it["item"] in labels and len(set(labels[it["item"]])) == 1]
    judged = [d for d in rows.values() if not (set(filter(None, d["exclude"].split("|"))) & BEFORE_MODEL)]
    low = sorted(d["crop"] for d in judged if float(d["photo_score"]) < thr)
    todo_all = sorted({c for c, _ in labelled} | set(low) | (set(every) if args.all else set()))

    cache_path = ROOT / "work" / "evidence" / "vlm_is_photo.jsonl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = {}
    if cache_path.exists():
        for l in open(cache_path, encoding="utf-8"):
            r = json.loads(l)
            cache[r["key"]] = r
    rev = ev["vlm_revision"]
    key = lambda c: hashlib.sha256(f"{rev}|{phash}|{ev['vlm_max_pixels']}|{c}".encode()).hexdigest()  # noqa: E731
    todo = [c for c in todo_all if key(c) not in cache]
    t0 = time.time()
    if todo:
        path = snapshot_download(ev["vlm"], revision=rev, local_files_only=True)
        proc = AutoProcessor.from_pretrained(path, max_pixels=ev["vlm_max_pixels"])
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            model = AutoModelForImageTextToText.from_pretrained(path, dtype=torch.bfloat16, device_map=dev).eval()
        except TypeError:
            model = AutoModelForImageTextToText.from_pretrained(path, torch_dtype=torch.bfloat16, device_map=dev).eval()
        tok = proc.tokenizer
        yes, no = tok.encode("是", add_special_tokens=False)[0], tok.encode("否", add_special_tokens=False)[0]
        text = proc.apply_chat_template([{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt}]}],
                                        tokenize=False, add_generation_prompt=True)
        with open(cache_path, "a", encoding="utf-8", newline="\n") as out:
            for n, c in enumerate(todo, 1):
                with Image.open(ROOT / "work" / "extract" / "crops" / c) as im:
                    inputs = proc(text=[text], images=[im.convert("RGB")], return_tensors="pt").to(model.device)
                with torch.no_grad():
                    lg = model(**inputs).logits[0, -1].float()
                p = float(torch.softmax(lg[[yes, no]], dim=0)[0])
                r = {"key": key(c), "crop": c, "p_yes": round(p, 4), "revision": rev, "prompt_sha256": phash}
                out.write(json.dumps(r) + "\n")
                cache[r["key"]] = r
                if n % 500 == 0:
                    out.flush()
                    print(f"[vlm] {n}/{len(todo)}，{time.time() - t0:.0f} 秒", flush=True)
    p = {c: cache[key(c)]["p_yes"] for c in todo_all}

    lab = [(p[c], a == "Photo") for c, a in labelled]
    agree = sum((q >= 0.5) == y for q, y in lab)
    tp = sum(q >= 0.5 and y for q, y in lab)
    fp = sum(q >= 0.5 and not y for q, y in lab)
    fn = sum(q < 0.5 and y for q, y in lab)
    low_yes = sorted((c for c in low if p[c] >= 0.5), key=lambda c: -p[c])
    hist = collections.Counter(min(9, int(p[c] * 10)) for c in low)
    res = {"threshold": thr, "prompt": "configs/prompts/is_photo.txt", "prompt_sha256": phash, "model": ev["vlm"],
           "revision": rev, "new_answers": len(todo), "seconds": round(time.time() - t0, 1),
           "labelled": {"items": len(lab), "agree_at_0.5": agree, "photo_precision": round(tp / (tp + fp), 4) if tp + fp else None,
                        "photo_recall": round(tp / (tp + fn), 4) if tp + fn else None, "tp": tp, "fp": fp, "fn": fn},
           "low": {"images": len(low), "vlm_says_photo": len(low_yes), "share": round(len(low_yes) / len(low), 4) if low else None,
                   "p_yes_histogram_by_tenth": {f"{k / 10:.1f}": hist[k] for k in range(10)}},
           "all": ({sp: {"images": sum(1 for d in every.values() if d["split"] == sp),
                         "vlm_ge_0.5": sum(1 for c, d in every.items() if d["split"] == sp and p[c] >= 0.5),
                         "vlm_ge_0.9": sum(1 for c, d in every.items() if d["split"] == sp and p[c] >= 0.9)}
                    for sp in ("dev", "test")} if args.all else None),
           "low_vlm_photos": [{"crop": c, "p_yes": p[c], "photo_score": float(rows[c]["photo_score"])} for c in low_yes]}
    stamp = run_stamp()
    rpath = ROOT / "results" / f"photo_vlm_dev_{stamp}.json"
    k = 2
    while rpath.exists():   # never overwrite a result (CLAUDE.md rule 2)
        rpath = ROOT / "results" / f"photo_vlm_dev_{stamp}_{k}.json"
        k += 1
    rpath.write_text(json.dumps({"meta": run_meta("configs/default.yaml", [prompt_path, ROOT / "work" / "photo_filter.csv",
                                                                         ROOT / "annotations" / "dev" / "A.jsonl"],
                                                  packages=("torch", "transformers", "pillow")), **res},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    cards = "".join(f'<figure><img loading="lazy" src="data:image/jpeg;base64,{base64.b64encode((ROOT / "work" / "extract" / "crops" / c).read_bytes()).decode()}" alt="">'
                    f'<figcaption>{html.escape(c)}｜模型說是照片 {p[c]:.2f}｜照片分數 {float(rows[c]["photo_score"]):.3f}</figcaption></figure>'
                    for c in low_yes[:args.show])
    L = res["labelled"]
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>照片過濾漏掉的照片</title><style>
body{{font-family:system-ui,"Microsoft JhengHei",sans-serif;margin:16px;background:#fafafa;color:#222;max-width:1100px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}} figure{{margin:0}}
figure img{{width:100%;height:180px;object-fit:contain;background:#0001}} figcaption{{font-size:12px;color:#666;word-break:break-all}}
</style></head><body><h1 style="font-size:20px">照片過濾會不會漏掉照片（開發集）</h1>
<p>用視覺語言模型問每張圖「是不是相機拍的真實照片」。先在學生標過的 {L["items"]} 張上看它準不準：跟學生一致 {L["agree_at_0.5"]} 張，它說是照片的裡面真的是照片的比例 {L["photo_precision"]}，學生說是照片的它抓到 {L["photo_recall"]}。</p>
<p>照片分數低於門檻 {thr:g}、被照片過濾丟掉的開發集圖片有 {len(low):,} 張，模型說其中 <b>{len(low_yes):,} 張</b>是照片。下面列模型最有把握的 {min(args.show, len(low_yes))} 張，看看是不是真的照片。</p>
<div class="grid">{cards}</div></body></html>"""
    hpath = ROOT / "reports" / f"photo_vlm_dev_{stamp}.html"
    hpath.write_text(page, encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("labelled", "low", "new_answers", "seconds")}, ensure_ascii=False))
    print(f"wrote {rpath} and {hpath}")


if __name__ == "__main__":
    main()
