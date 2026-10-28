"""Evidence baselines (PLAN §5 step 5, Table 5): how a photo relates to its context, in R's three levels
(direct / topical / unrelated).

  E1  Chinese CLIP ViT-L/14 (OFA-Sys/chinese-clip-vit-large-patch14, transformers) cosine between the photo and the
      context (the text encoder keeps the first 52 tokens); two thresholds give the three levels. The thresholds are
      set on annotation R (dev); until then (evidence.e1_thresholds: null) the terciles of the run's own
      similarities are used, only so that the pipeline runs end to end.
  E2  the vision-language model (evidence.vlm at evidence.vlm_revision) picks A / B / C directly, zero-shot, with the
      prompt configs/prompts/direct_zeroshot.txt (R manual v1.2's definitions). The probabilities are the softmax of
      the logits of "A", "B", "C" at the first answer position (no sampling, i.e. temperature 0); letter_mass is the
      share of the full next-token distribution on those three letters (a check that the model answers in letters).
  E3  (few-shot) comes after annotation R (dev) gives the three examples.

Context: the rule-based context of extract_pairs.py (PLAN step 4's L5): text on the photo, caption, local heading,
heading and the first two paragraphs, at most evidence.context_max_chars characters.
Photos: --crops (a file with one crop name per line; default annotations/qc/R_draft_crops.txt, the 20 R draft
photos). The test split only with configs/frozen.yaml and the same settings (frozen.check; CLAUDE.md rule 3); E3 then
uses the frozen development examples.
Every model output is cached (rule 9): work/evidence/e1_cnclip.jsonl and work/evidence/e2_direct_zeroshot.jsonl,
keyed by model revision, prompt hash, crop and context. Writes results/baselines_evidence_{tag}_{stamp}.json and
reports/baselines_evidence_{tag}_{stamp}.html.

  python src/baselines_evidence.py --tag dev20
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
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "work" / "models" / "hf"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")   # weights are already cached (check_models.py); never download here
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp, sha256_file  # noqa: E402

import yaml  # noqa: E402

LEVELS = ["direct", "topical", "unrelated"]
LEVEL_ZH = {"direct": "直接對應", "topical": "主題相關", "unrelated": "無關"}
LETTERS = {"A": "direct", "B": "topical", "C": "unrelated"}
EMPTY_CONTEXT = "（這張照片旁邊沒有文字）"


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def load_cache(path):
    out = {}
    if path.exists():
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            out[r["key"]] = r
    return out


def append_cache(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def context_of(rec, max_chars):
    """(labelled context for the VLM, plain text for CLIP, parts) from one extract_pairs record."""
    parts = [("圖上的字", rec.get("overlay_text", "")), ("圖說", rec.get("caption", "")),
             ("小標題", rec.get("local_heading", "")), ("標題", rec.get("heading", ""))]
    parts += [("內文", p.get("text", "") if isinstance(p, dict) else str(p)) for p in (rec.get("paragraphs") or [])[:2]]
    parts = [(k, " ".join(v.split())) for k, v in parts if v and v.strip()]
    labelled = "\n".join(f"{k}：{v}" for k, v in parts)[:max_chars] or EMPTY_CONTEXT
    plain = "；".join(v for _, v in parts)[:max_chars] or EMPTY_CONTEXT
    return labelled, plain, parts


def load_contexts(arg, crops, recs, max_chars):
    """crop -> (labelled, plain, parts). arg 'rules': extract_pairs' context (context_of); otherwise a module 1 file
    (context_link.py: work/context/{method}_{tag}.jsonl, with [label, text] parts); a photo missing from the file has
    no context."""
    if arg == "rules":
        return {c: context_of(recs[c], max_chars) for c in crops}
    got = {}
    for line in open(ROOT / arg if not Path(arg).is_absolute() else arg, encoding="utf-8"):
        r = json.loads(line)
        got[r["crop"]] = [(k, " ".join(v.split())) for k, v in r.get("parts") or [] if v and v.strip()]
    out = {}
    for c in crops:
        parts = got.get(c, [])
        out[c] = ("\n".join(f"{k}：{v}" for k, v in parts)[:max_chars] or EMPTY_CONTEXT,
                  "；".join(v for _, v in parts)[:max_chars] or EMPTY_CONTEXT, parts)
    return out


def context_name(arg):
    return "rules" if arg == "rules" else Path(arg).stem.rsplit("_", 1)[0]


def select_photos(args):
    """--crops FILE, or --r-items dev (with --labels: only the photos the annotator gave a level)."""
    if args.crops:
        return Path(args.crops).read_text(encoding="utf-8").split(), {}
    rows = [json.loads(l) for l in open(ROOT / "annotations" / args.r_items / "R_items.jsonl", encoding="utf-8")]
    if args.labels:
        lab = {json.loads(l)["item"]: json.loads(l) for l in open(args.labels, encoding="utf-8")}
        rows = [r for r in rows if r["item"] in lab and not lab[r["item"]].get("not_photo") and lab[r["item"]].get("level")]
    return [r["crop"] for r in rows], {r["crop"]: r["item"] for r in rows}


def snapshot(repo, rev):
    from huggingface_hub import snapshot_download
    return snapshot_download(repo, revision=rev, local_files_only=True)


def device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def gpu_peak():
    import torch
    return round(torch.cuda.max_memory_allocated() / 1e9, 2) if torch.cuda.is_available() else None


def free():
    import gc
    import torch
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def run_e1(rows, ev, cache_path):
    import torch
    from transformers import ChineseCLIPModel, ChineseCLIPProcessor
    repo, rev = ev["cnclip"], ev["cnclip_revision"]
    cache = load_cache(cache_path)
    todo = [r for r in rows if sha(f"{rev}|{r['crop']}|{r['plain']}") not in cache]
    t0 = time.time()
    if todo:
        path = snapshot(repo, rev)
        model = ChineseCLIPModel.from_pretrained(path).to(device()).eval()
        proc = ChineseCLIPProcessor.from_pretrained(path)
        for r in todo:
            with torch.no_grad():
                inputs = proc(text=[r["plain"]], images=r["image"], return_tensors="pt", padding=True, truncation=True,
                              max_length=52).to(device())
                out = model(**inputs)   # image_embeds and text_embeds come out L2-normalised
                sim = float((out.image_embeds * out.text_embeds).sum(dim=-1)[0])
            row = {"key": sha(f"{rev}|{r['crop']}|{r['plain']}"), "crop": r["crop"], "model": repo, "revision": rev,
                   "sim": round(sim, 4)}
            append_cache(cache_path, row)
            cache[row["key"]] = row
        del model
        free()
    out = {r["crop"]: cache[sha(f"{rev}|{r['crop']}|{r['plain']}")]["sim"] for r in rows}
    return out, {"model": repo, "revision": rev, "new": len(todo), "seconds": round(time.time() - t0, 1),
                 "text": "plain context, first 52 tokens"}


E3_SEED = "2026:E3"


def image_of(r):
    """The row's image, or (measure.py: thousands of photos) opened from its crop when the row has none."""
    if r.get("image") is not None:
        return r["image"]
    from PIL import Image
    with Image.open(ROOT / "work" / "extract" / "crops" / r["crop"]) as im:
        return im.convert("RGB")


def e3_examples(rows, labels_by_crop, per_class=1):
    """Few-shot examples for E3 from annotation R's development labels: per level, the photos in a fixed shuffled
    order (seed 2026); a query gets, for each level, the first `per_class` photos from other firms (never itself),
    so no photo is shown with an answer from its own firm. Test (after frozen.yaml): the first ones of each pool.
    Photos that have text in this context come first (10/20: a photo with no text cannot show how a photo relates to
    its text; the first draw gave a sliver of a chapter-cover photo); a level whose photos all lack text (the gold
    context: 主題相關 and 無關 are mostly photos with no describing text) still gets one."""
    import random
    pools = {L: sorted((r for r in rows if labels_by_crop.get(r["crop"]) == L), key=lambda r: r["crop"]) for L in LEVELS}
    for L in LEVELS:
        random.Random(f"{E3_SEED}:{L}").shuffle(pools[L])
        pools[L].sort(key=lambda r: not r["parts"])   # stable: the shuffled order within each group

    def pick(q):
        out = []
        for L in LEVELS:
            out += [r for r in pools[L] if r["firm"] != q["firm"] and r["crop"] != q["crop"]][:per_class]
        return out   # order: direct, topical, unrelated (the same for every query)
    return pick


def run_e2(rows, ev, prompt_tpl, prompt_hash, cache_path, examples=None, labels_by_crop=None):
    """E2 (zero-shot) or, with `examples` (e3_examples), E3: earlier user turns show each example photo and its
    context, the assistant turn its letter; the query comes last."""
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor
    repo, rev = ev["vlm"], ev["vlm_revision"]
    cache = load_cache(cache_path)
    inv = {v: k for k, v in LETTERS.items()}

    def key(r):
        ex = "|".join(f"{e['crop']}:{sha(e['labelled'])[:12]}" for e in examples(r)) if examples else ""
        return sha(f"{rev}|{prompt_hash}|{ev['vlm_max_pixels']}|{r['crop']}|{r['labelled']}|{ex}")
    todo = [r for r in rows if key(r) not in cache]
    t0, peak, per = time.time(), None, []
    if todo:
        path = snapshot(repo, rev)
        try:
            processor = AutoProcessor.from_pretrained(path, max_pixels=ev["vlm_max_pixels"])
        except TypeError:   # 10/22: a processor without Qwen's max_pixels (InternVL3) keeps its own image size rule
            processor = AutoProcessor.from_pretrained(path)
        try:
            model = AutoModelForImageTextToText.from_pretrained(path, dtype=torch.bfloat16, device_map=device()).eval()
        except TypeError:   # older transformers call it torch_dtype
            model = AutoModelForImageTextToText.from_pretrained(path, torch_dtype=torch.bfloat16, device_map=device()).eval()
        tok = processor.tokenizer
        ids = {L: tok.encode(L, add_special_tokens=False) for L in LETTERS}
        if any(len(v) != 1 for v in ids.values()):
            sys.exit(f"A、B、C 不是單一 token：{ids}")
        ids = {L: v[0] for L, v in ids.items()}
        for r in todo:
            msgs, images = [], []
            for e in (examples(r) if examples else []):
                msgs += [{"role": "user", "content": [{"type": "image"},
                                                      {"type": "text", "text": prompt_tpl.replace("{context}", e["labelled"])}]},
                         {"role": "assistant", "content": [{"type": "text", "text": inv[labels_by_crop[e["crop"]]]}]}]
                images.append(image_of(e))
            msgs.append({"role": "user", "content": [{"type": "image"},
                                                     {"type": "text", "text": prompt_tpl.replace("{context}", r["labelled"])}]})
            images.append(image_of(r))
            text = processor.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            inputs = processor(text=[text], images=images, return_tensors="pt").to(model.device)
            t1 = time.time()
            with torch.no_grad():
                logits = model(**inputs).logits[0, -1].float()
            per.append(time.time() - t1)
            full = torch.softmax(logits, dim=0)
            three = torch.softmax(logits[[ids[L] for L in LETTERS]], dim=0)
            p = {LETTERS[L]: round(float(three[k]), 4) for k, L in enumerate(LETTERS)}
            top = tok.decode([int(logits.argmax())])
            row = {"key": key(r), "crop": r["crop"], "model": repo, "revision": rev, "prompt_sha256": prompt_hash,
                   "p": p, "choice": max(p, key=p.get), "letter_mass": round(float(sum(full[ids[L]] for L in LETTERS)), 4),
                   "top_token": top, "examples": [e["crop"] for e in examples(r)] if examples else [],
                   "image_tokens": int(inputs["image_grid_thw"][-1].prod()) // 4 if "image_grid_thw" in inputs else None}
            append_cache(cache_path, row)
            cache[row["key"]] = row
        peak = gpu_peak()
        del model
        free()
    out = {r["crop"]: cache[key(r)] for r in rows}
    return out, {"model": repo, "revision": rev, "prompt": "configs/prompts/direct_zeroshot.txt", "prompt_sha256": prompt_hash,
                 "max_pixels": ev["vlm_max_pixels"], "new": len(todo), "seconds": round(time.time() - t0, 1),
                 "seconds_per_photo": round(sum(per) / len(per), 2) if per else None, "peak_gpu_gb": peak,
                 "few_shot": {"per_class": len(examples(rows[0])) // 3, "seed": E3_SEED, "other_firms_only": True} if examples else None}


def thumb(img, side=420):
    im = img.copy()
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--crops")
    src.add_argument("--r-items", choices=["dev", "test"], help="the photos of annotations/<split>/R_items.jsonl")
    ap.add_argument("--labels", help="annotations/dev/R.jsonl: with --r-items only the labelled photos; E3's examples")
    ap.add_argument("--context", default="rules", help="'rules' or a context_link.py file (work/context/*.jsonl)")
    ap.add_argument("--tag", default="dev20")
    ap.add_argument("--only", nargs="*", choices=["e1", "e2", "e3"], default=["e1", "e2"])
    ap.add_argument("--shots", type=int, default=1, help="E3: examples per level")
    ap.add_argument("--vlm", choices=["main", "alt", "third"], default="main",
                    help="alt: evidence.vlm_alt (Qwen2.5-VL-7B, the model before 10/20); third: evidence.vlm_third (10/22, NEXT C12)")
    ap.add_argument("--prompt", default="direct_zeroshot", help="E2/E3 prompt in configs/prompts/ (direct_zeroshot_v1: R manual v1.2's wording)")
    args = ap.parse_args()
    if not args.crops and not args.r_items:
        args.crops = str(ROOT / "annotations" / "qc" / "R_draft_crops.txt")
    if "e3" in args.only and not args.labels:
        sys.exit("E3 要 --labels（例子的答案來自標註 R 開發集）")
    from PIL import Image
    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))
    ev = cfg["evidence"]
    import copy
    cfg_as_frozen = copy.deepcopy(cfg)   # 10/27: what frozen.check compares (before any --vlm swap)
    if args.vlm in ("alt", "third"):   # 10/20: the second VLM (evidence.vlm_alt); 10/22: a third family; caches are keyed by revision
        if not ev.get(f"vlm_{args.vlm}_revision"):
            sys.exit(f"configs/default.yaml 的 evidence.vlm_{args.vlm}_revision 還沒填")
        ev["vlm"], ev["vlm_revision"] = ev[f"vlm_{args.vlm}"], ev[f"vlm_{args.vlm}_revision"]
    crops, _ = select_photos(args)
    filt = {}
    with open(ROOT / "work" / "photo_filter.csv", encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if d["crop"] in crops:
                filt[d["crop"]] = d
    missing = [c for c in crops if c not in filt]
    if missing:
        sys.exit(f"不在照片過濾結果裡：{missing[:5]}")
    test = [c for c in crops if filt[c]["split"] != "dev"]
    fr = None
    if test:   # the test split: frozen settings only (CLAUDE.md rule 3)
        import frozen
        fr = frozen.check("test", cfg_as_frozen)   # 10/27: the configuration before --vlm alt/third swapped the model in
    recs = {}
    for rep in sorted({filt[c]["report"] for c in crops}):
        with open(ROOT / "work" / "extract" / f"{rep}_pairs.jsonl", encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                if d["crop"] in filt:
                    recs[d["crop"]] = d
    rows = []
    ctx = load_contexts(args.context, crops, recs, ev["context_max_chars"])
    for c in crops:
        labelled, plain, parts = ctx[c]
        with Image.open(ROOT / "work" / "extract" / "crops" / c) as im:
            rows.append({"crop": c, "report": filt[c]["report"], "firm": filt[c]["firm_id"], "page": int(filt[c]["page"]),
                         "image": im.convert("RGB"), "labelled": labelled, "plain": plain, "parts": parts})
    prompt_path = ROOT / "configs" / "prompts" / f"{args.prompt}.txt"
    prompt_tpl = prompt_path.read_text(encoding="utf-8")
    prompt_hash = sha256_file(prompt_path)
    work = ROOT / cfg["paths"]["evidence"]
    res = {"tag": args.tag, "photos": len(rows),
           "crops": args.crops or f"annotations/{args.r_items}/R_items.jsonl" + (f" (labelled in {args.labels})" if args.labels else ""),
           "context": "extract_pairs rule-based (L5): overlay text, caption, local heading, heading, 2 paragraphs"
           if args.context == "rules" else f"{context_name(args.context)} ({args.context})",
           "context_name": context_name(args.context), "empty_context": sum(1 for r in rows if not r["parts"])}
    e1 = e2 = e3 = None
    if "e1" in args.only:
        e1, res["e1"] = run_e1(rows, ev, work / "e1_cnclip.jsonl")
        sims = sorted(e1.values())
        if ev.get("e1_thresholds"):
            t1, t2 = ev["e1_thresholds"]
            res["e1"]["thresholds_from"] = "configs (evidence.e1_thresholds)"
        else:
            import statistics
            t1, t2 = statistics.quantiles(sims, n=3)
            res["e1"]["thresholds_from"] = "terciles of this run (placeholder until annotation R dev)"
        res["e1"]["thresholds"] = [round(t1, 4), round(t2, 4)]
        lvl = lambda s: "direct" if s >= t2 else ("topical" if s >= t1 else "unrelated")  # noqa: E731
        res["e1"]["sim_min_median_max"] = [sims[0], sims[len(sims) // 2], sims[-1]]
        res["e1"]["levels"] = dict(collections.Counter(lvl(s) for s in sims))
    if "e2" in args.only:
        e2, res["e2"] = run_e2(rows, ev, prompt_tpl, prompt_hash, work / "e2_direct_zeroshot.jsonl")
        res["e2"]["prompt"] = f"configs/prompts/{args.prompt}.txt"
        res["e2"]["levels"] = dict(collections.Counter(v["choice"] for v in e2.values()))
        res["e2"]["letter_mass_min"] = min(v["letter_mass"] for v in e2.values())
        res["e2"]["top_tokens"] = dict(collections.Counter(v["top_token"] for v in e2.values()))
    if "e3" in args.only:
        lab = {json.loads(l)["item"]: json.loads(l) for l in open(args.labels, encoding="utf-8")}
        if fr:   # 10/27: the frozen examples are development photos; their answers are the development labels
            lab = {json.loads(l)["item"]: json.loads(l) for l in open(ROOT / "annotations" / "dev" / "R.jsonl", encoding="utf-8")}
        items = {json.loads(l)["crop"]: json.loads(l)["item"] for l in open(ROOT / "annotations" / "dev" / "R_items.jsonl", encoding="utf-8")}
        by_crop = {c: lab[i]["level"] for c, i in items.items() if i in lab and not lab[i].get("not_photo") and lab[i].get("level")}
        if fr:   # test: the frozen development examples, with the rule-based context (the measurement tool's)
            ex = fr["measure"]["e3_examples"]
            ex_filt = {}
            with open(ROOT / "work" / "photo_filter.csv", encoding="utf-8", newline="") as f:
                for d in csv.DictReader(f):
                    if d["crop"] in ex:
                        ex_filt[d["crop"]] = d
            ex_recs = {}
            for rep in sorted({ex_filt[c]["report"] for c in ex}):
                for line in open(ROOT / "work" / "extract" / f"{rep}_pairs.jsonl", encoding="utf-8"):
                    d = json.loads(line)
                    if d["crop"] in ex_filt:
                        ex_recs[d["crop"]] = d
            ex_ctx = load_contexts("rules", ex, ex_recs, ev["context_max_chars"])
            ex_rows = [{"crop": c, "firm": ex_filt[c]["firm_id"], "labelled": ex_ctx[c][0], "parts": ex_ctx[c][2],
                        "image": image_of({"crop": c})} for c in ex]
            pick = lambda q: ex_rows  # noqa: E731
        else:
            pick = e3_examples(rows, by_crop, args.shots)
        e3, res["e3"] = run_e2(rows, ev, prompt_tpl, prompt_hash, work / "e3_fewshot.jsonl", examples=pick, labels_by_crop=by_crop)
        res["e3"]["prompt"] = f"configs/prompts/{args.prompt}.txt"
        res["e3"]["levels"] = dict(collections.Counter(v["choice"] for v in e3.values()))
        res["e3"]["letter_mass_min"] = min(v["letter_mass"] for v in e3.values())
        res["e3"]["examples_used"] = dict(collections.Counter(x for v in e3.values() for x in v["examples"]))
    per = []
    for r in rows:
        d = {"crop": r["crop"], "context_chars": len(r["labelled"]) if r["parts"] else 0}
        if e1:
            d["e1_sim"], d["e1_level"] = e1[r["crop"]], lvl(e1[r["crop"]])
        if e2:
            d["e2_p"], d["e2_choice"], d["e2_letter_mass"] = e2[r["crop"]]["p"], e2[r["crop"]]["choice"], e2[r["crop"]]["letter_mass"]
        if e3:
            d["e3_p"], d["e3_choice"], d["e3_examples"] = e3[r["crop"]]["p"], e3[r["crop"]]["choice"], e3[r["crop"]]["examples"]
        per.append(d)
    res["per_photo"] = per
    if e1 and e2:
        res["e1_e2_same_level"] = sum(d["e1_level"] == d["e2_choice"] for d in per)
    res["labels"] = "outputs only; scored by tune_evidence.py / evaluate.py against annotation R"

    stamp = run_stamp()
    rpath = ROOT / "results" / f"baselines_evidence_{args.tag}_{stamp}.json"
    k = 2
    while rpath.exists():
        rpath = ROOT / "results" / f"baselines_evidence_{args.tag}_{stamp}_{k}.json"
        k += 1
    inputs = [ROOT / "work" / "photo_filter.csv", prompt_path] + [Path(p) for p in (args.crops, args.labels) if p] + \
        ([ROOT / args.context] if args.context != "rules" else [])
    meta = run_meta("configs/default.yaml", inputs, packages=("torch", "transformers", "pillow", "numpy", "pyyaml"))
    rpath.write_text(json.dumps({"meta": meta, **res}, ensure_ascii=False, indent=1), encoding="utf-8")

    cards = []
    for r, d in zip(rows, per):
        ctx = "".join(f'<div><span class="k">{html.escape(k)}</span>{html.escape(v[:300])}</div>' for k, v in r["parts"]) or f'<div class="mut">{EMPTY_CONTEXT}</div>'
        e1h = f'<div>E1 相似度 {d["e1_sim"]:.3f} → <b>{LEVEL_ZH[d["e1_level"]]}</b></div>' if e1 else ""
        e2h = ""
        if e2:
            bars = "".join(f'<div class="bar"><span>{LEVEL_ZH[L]}</span><i style="width:{100 * d["e2_p"][L]:.0f}%"></i><em>{d["e2_p"][L]:.2f}</em></div>' for L in LEVELS)
            e2h = f'<div>E2 → <b>{LEVEL_ZH[d["e2_choice"]]}</b></div>{bars}'
        cards.append(f'<section><img src="data:image/jpeg;base64,{thumb(r["image"])}" alt=""><div class="txt">'
                     f'<h2>{html.escape(r["report"])} 第 {r["page"]} 頁</h2>{ctx}<div class="out">{e1h}{e2h}</div></div></section>')
    summ = []
    if e1:
        summ.append(f'<li>E1 中文 CLIP：相似度 {res["e1"]["sim_min_median_max"][0]:.3f}～{res["e1"]["sim_min_median_max"][2]:.3f}（中位數 {res["e1"]["sim_min_median_max"][1]:.3f}）。'
                    f'兩個門檻還沒定，先用這 20 張的三分位數 {res["e1"]["thresholds"][0]:.3f}、{res["e1"]["thresholds"][1]:.3f}，所以三級一定各約三分之一，只是讓流程跑通。</li>')
    if e2:
        lv = "、".join(f'{LEVEL_ZH[L]} {res["e2"]["levels"].get(L, 0)}' for L in LEVELS)
        summ.append(f'<li>E2 視覺語言模型直接三選一：{lv}。模型的下一個字幾乎都是 A／B／C（三個字母的機率合計最低 {res["e2"]["letter_mass_min"]:.2f}）。每張約 {res["e2"]["seconds_per_photo"] or "—"} 秒。</li>')
    if e1 and e2:
        summ.append(f'<li>E1 和 E2 給同一級的：{res["e1_e2_same_level"]}／{len(rows)} 張。</li>')
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>證據分級比較對象試跑</title><style>
:root{{--bg:#fafafa;--fg:#222;--mut:#666;--line:#ddd;--bar:#1a56c4}}
body{{font-family:system-ui,"Microsoft JhengHei",sans-serif;margin:16px;background:var(--bg);color:var(--fg);max-width:1000px}}
section{{display:flex;flex-wrap:wrap;gap:16px;border-top:1px solid var(--line);padding:16px 0}}
section img{{max-width:min(420px,100%);height:auto;align-self:flex-start}} .txt{{flex:1;min-width:260px;font-size:14px;line-height:1.6}}
h2{{font-size:15px;margin:0 0 6px}} .k{{display:inline-block;min-width:4.5em;color:var(--mut)}} .mut{{color:var(--mut)}}
.out{{margin-top:8px;padding:8px;background:#fff;border:1px solid var(--line)}}
.bar{{display:flex;align-items:center;gap:6px;font-size:13px}} .bar span{{width:4.5em}} .bar i{{display:block;height:10px;background:var(--bar)}} .bar em{{font-style:normal;color:var(--mut)}}
.warn{{background:#fff4e5;border:1px solid #f0c27a;padding:8px 12px}}
</style></head><body><h1 style="font-size:20px">證據分級的比較對象 E1、E2：開發集 {len(rows)} 張試跑</h1>
<p class="warn"><b>請先標完 R 草稿題目，再看這一頁。</b>這 {len(rows)} 張就是 R 的草稿題目；先看到模型的答案會影響你的判斷。R 正式題會排除這 {len(rows)} 張。</p>
<p>上下文是現有抽取程式的規則版（圖上的字、圖說、小標題、標題、內文 2 段）。三級的意思跟 R 手冊一樣：直接對應、主題相關、無關。這一頁只看流程跑不跑得通；還沒有標準答案，所以沒有準確率。</p>
<ul>{"".join(summ)}</ul>
{"".join(cards)}</body></html>"""
    hpath = ROOT / "reports" / f"baselines_evidence_{args.tag}_{stamp}.html"
    hpath.write_text(page, encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("photos", "empty_context") if k in res} |
                     {k: {kk: vv for kk, vv in res[k].items() if kk != "revision"} for k in ("e1", "e2", "e3") if k in res} |
                     ({"e1_e2_same_level": res["e1_e2_same_level"]} if "e1_e2_same_level" in res else {}), ensure_ascii=False))
    print(f"wrote {rpath} and {hpath}")


if __name__ == "__main__":
    main()
