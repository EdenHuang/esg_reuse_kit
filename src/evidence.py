"""Module 2: evidence grading (PLAN §5 step 5, Table 5): is the photo evidence for the text beside it?

Evidence = depicted + anchored. For each photo and its context:
  1. gate      the language model (evidence.llm) lists what the text says that a camera could show (referents:
               event / activity / object / place / person) and the anchor strings (org / event / year), as JSON
               (configs/prompts/gate.txt; a parse failure is retried once with a stricter ending, then recorded and
               treated as no referents). No referent: at most 包裝 (PLAN).
  2. questions one yes/no question per referent (configs/prompts/question.txt, TIFA); depends_on marks a question
               that only makes sense when another one is yes (DSG). If the JSON fails twice, a template question
               「照片裡有{referent}嗎？」 per referent.
  3. answers   the vision-language model (evidence.vlm) answers each question about the photo; p(yes) is the softmax
               of the logits of 「是」/「否」 at the first answer position (no sampling). A question whose parent is
               answered no counts as no (DSG). depicted = the largest p(yes); the mean is kept too.
  4. anchors   PaddleOCR (.venv_ocr, the same script as logo_refs.py) reads the photo; the text is matched against
               the firm's names (catalog, reports_index.csv and the ESG InfoHub company list: logo_refs.load_names /
               name_match), the event names from the gate, and the years from the gate plus the report year, each
               written in AD and ROC (2024 / 113年 / 民國113).
               Logos: Eden's confirmed reference logos (work/logo_refs/<firm>/refs/; 10/19 review of the 30
               development firms) found in the photo by SuperPoint + LightGlue and a homography (logo_matches).
               D15: OCR only if logo matching is not stable by 10/31.
  5. topical   when not depicted: does the photo fit the topic? (configs/prompts/topical.txt) The topic is the gate's
               short topic (gate.txt v2, 10/19: the rule-based local heading is often a sentence fragment such as
               「▪ 共 1,811 案件」), else the local heading, else the heading, else (10/20, manual v1.5) the last
               heading before the photo in reading order (context_link.heading_chain).
  6. levels    depicted & anchored → 證據; depicted → 示意; topical → 包裝; else 無關. Against annotation R's three levels,
               證據 and 示意 are 直接對應. Thresholds: evidence.depiction_threshold, evidence.topical_threshold
               (initial values, tuned on the development split only).

Context: --context rules = extract_pairs.py's rule-based context (PLAN step 4's L5), the same as baselines_evidence.py;
or a context_link.py file (work/context/{method}_{tag}.jsonl: L1-L5, M_lr = module 1 out of fold, gold = the
annotator's describing boxes), for Table 4's extrinsic comparison.
--holistic (ablation of the decomposition, Table 6): one yes/no question with the whole context
(configs/prompts/holistic.txt), cached in work/evidence/holistic.jsonl; tune_evidence.py uses it.
Photos: --crops FILE (one crop name per line) or --r-items (annotations/<split>/R_items.jsonl). The test split only
with configs/frozen.yaml and the same settings (frozen.check; CLAUDE.md rule 3).
Labels, when given (--labels: a result of `annotate_export.py r-import`, or annotations/dev/R.jsonl), are compared
with the levels: agreement, confusion, macro-F1 over 直接對應／主題相關／無關, and OCR anchors against the annotator's
「照片裡有能認出公司或活動的字」.
Every model output is cached under work/evidence/ (rule 9). Writes work/evidence/evidence_{tag}.jsonl,
results/evidence_{tag}_{stamp}.json and reports/evidence_{tag}_{stamp}.html.

  python src/evidence.py --crops annotations/qc/R_draft_crops.txt --tag draft20 --labels results/annotation_R_draft_import_20261019_1350.json
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
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "work" / "models" / "hf"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp, sha256_file  # noqa: E402

import yaml  # noqa: E402

LEVEL4 = ["證據", "示意", "包裝", "無關"]
TO3 = {"證據": "direct", "示意": "direct", "包裝": "topical", "無關": "unrelated"}
ZH3 = {"direct": "直接對應", "topical": "主題相關", "unrelated": "無關"}
PROMPTS = ("gate", "question", "vqa", "topical", "holistic")


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class Cache:
    def __init__(self, path):
        self.path = Path(path)
        self.d = {}
        if self.path.exists():
            for line in open(self.path, encoding="utf-8"):
                r = json.loads(line)
                self.d[r["key"]] = r
        self.new = 0

    def get(self, k):
        return self.d.get(k)

    def put(self, row):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.d[row["key"]] = row
        self.new += 1


def parse_json(text, kind):
    m = re.search(r"\{.*\}" if kind == "object" else r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------- models
class Models:
    def __init__(self, ev):
        self.ev = ev
        self._llm = self._vlm = None

    def device(self):
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"

    def _load(self, cls, repo, rev):
        import torch
        from huggingface_hub import snapshot_download
        path = snapshot_download(repo, revision=rev, local_files_only=True)
        try:
            m = cls.from_pretrained(path, dtype=torch.bfloat16, device_map=self.device()).eval()
        except TypeError:
            m = cls.from_pretrained(path, torch_dtype=torch.bfloat16, device_map=self.device()).eval()
        return path, m

    def llm(self):
        if self._llm is None:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            path, m = self._load(AutoModelForCausalLM, self.ev["llm"], self.ev["llm_revision"])
            self._llm = (AutoTokenizer.from_pretrained(path), m)
        return self._llm

    def vlm(self):
        if self._vlm is None:
            from transformers import AutoModelForImageTextToText, AutoProcessor
            path, m = self._load(AutoModelForImageTextToText, self.ev["vlm"], self.ev["vlm_revision"])
            proc = AutoProcessor.from_pretrained(path, max_pixels=self.ev["vlm_max_pixels"])
            tok = proc.tokenizer
            self._vlm = (proc, m, tok.encode("是", add_special_tokens=False)[0], tok.encode("否", add_special_tokens=False)[0])
        return self._vlm

    def free_llm(self):
        """The language model is only needed for the gate and the questions; free it before the VLM loads."""
        if self._llm is not None:
            import gc
            import torch
            self._llm = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def generate(self, prompt, max_new_tokens=512):
        import torch
        tok, m = self.llm()
        ids = tok.apply_chat_template([{"role": "user", "content": prompt}], add_generation_prompt=True, return_tensors="pt")
        if not hasattr(ids, "shape"):
            ids = ids["input_ids"]
        ids = ids.to(m.device)
        with torch.no_grad():
            out = m.generate(ids, max_new_tokens=max_new_tokens, do_sample=False)
        return tok.decode(out[0][ids.shape[1]:], skip_special_tokens=True)

    def p_yes(self, image, text):
        import torch
        proc, m, yes, no = self.vlm()
        chat = proc.apply_chat_template([{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": text}]}],
                                        tokenize=False, add_generation_prompt=True)
        inputs = proc(text=[chat], images=[image], return_tensors="pt").to(m.device)
        with torch.no_grad():
            lg = m(**inputs).logits[0, -1].float()
        return round(float(torch.softmax(lg[[yes, no]], dim=0)[0]), 4)


# ---------------------------------------------------------------- anchors
def year_forms(y):
    """'2024' / '113' / '113年' / '民國113年' → the AD and ROC ways of writing it."""
    m = re.search(r"\d{2,4}", str(y))
    if not m:
        return set()
    n = int(m.group(0))
    ad = n if n >= 1912 else n + 1911
    if not 1950 <= ad <= 2100:
        return set()
    roc = ad - 1911
    return {str(ad), f"{roc}年", f"民國{roc}", f"{roc}年度"}


def ocr_photos(crops, ocr_dir, lang, workers=1):
    """PaddleOCR of each crop (cached by image hash), with logo_refs.py's script in .venv_ocr. workers > 1 splits the
    photos over that many OCR processes (10/20: one process uses about 1.6 of 24 cores, about 4 s a photo)."""
    import logo_refs as lr
    ocr_dir.mkdir(parents=True, exist_ok=True)
    got, todo = {}, []
    for c in crops:
        p = ROOT / "work" / "extract" / "crops" / c
        h = hashlib.sha1(p.read_bytes() + f"|photo|{lang}".encode()).hexdigest()
        f = ocr_dir / f"{h}.json"
        if f.exists():
            got[c] = json.loads(f.read_text(encoding="utf-8"))
        else:
            todo.append((c, h, p))
    info = {"photos": len(crops), "new": len(todo)}
    if todo:
        py = ROOT / ".venv_ocr" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = dict(os.environ, PADDLE_PDX_CACHE_HOME=str(ROOT / "work" / "models" / "paddlex"))
        jobs = list({h: (c, h, p) for c, h, p in todo}.values())   # identical files (cross-year reuse) share one OCR job
        n = max(1, min(int(workers), len(jobs)))
        parts = [jobs[k::n] for k in range(n)]
        print(f"[ocr] {len(todo)} 張照片，{n} 個程序", flush=True)
        procs = []
        for k, part in enumerate(parts):
            tmp = ocr_dir / f"_run{k}"
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            (tmp / "jobs.json").write_text(json.dumps([{"id": h, "path": str(p)} for c, h, p in part]), encoding="utf-8")
            log = open(tmp / "log.txt", "w", encoding="utf-8", errors="replace")
            procs.append((tmp, log, subprocess.Popen([str(py), "-I", "-c", lr.OCR_CODE, str(tmp / "jobs.json"), str(tmp / "out.jsonl"), lang],
                                                     stdout=log, stderr=subprocess.STDOUT, env=env)))
        model, errs = None, []
        by = collections.defaultdict(list)
        for c, h, p in todo:
            by[h].append(c)
        for tmp, log, pr in procs:
            try:
                pr.wait(timeout=4 * 3600)
            except subprocess.TimeoutExpired:
                pr.kill()
            log.close()
            if (tmp / "out.jsonl").exists():
                for line in (tmp / "out.jsonl").read_text(encoding="utf-8").splitlines():
                    rec = json.loads(line)
                    if "_model" in rec:
                        model = rec["_model"]
                        continue
                    out = {"texts": rec["texts"], "scores": rec["scores"], "model": model}
                    (ocr_dir / f"{rec['id']}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
                    for c in by[rec["id"]]:
                        got[c] = out
            if pr.returncode:
                errs.append((tmp / "log.txt").read_text(encoding="utf-8", errors="replace")[-600:])
            shutil.rmtree(tmp, ignore_errors=True)
        missing = [c for c, h, p in todo if c not in got]
        if missing:
            info["error"] = f"OCR 少了 {len(missing)} 張：{(errs or [''])[0]}"
        info["model"] = model
        info["workers"] = n
    return got, info


def anchor_match(ocr, firm_names, events, years, p):
    """Which anchor strings the OCR text of the photo contains."""
    import logo_refs as lr
    out = {"org": None, "event": [], "year": []}
    if not ocr:
        return out
    out["org"] = lr.name_match({"texts": ocr["texts"], "scores": ocr["scores"]}, firm_names, p)
    lines = [t for t, s in zip(ocr["texts"], ocr["scores"]) if s >= p["ocr_min_score"] and t.strip()]
    text = lr.norm_name("".join(lines))
    raw = "".join(lines).replace(" ", "")
    for e in events:
        n = lr.norm_name(e)
        if len(n) < 2:
            continue
        bigrams = {n[i:i + 2] for i in range(len(n) - 1)}
        cover = sum(1 for b in bigrams if b in text) / len(bigrams)
        if n in text or cover >= p["name_bigram_min"]:
            out["event"].append({"name": e, "cover": round(cover, 2)})
    for y in years:
        hits = [f for f in year_forms(y) if f in raw]
        if hits:
            out["year"].append({"year": y, "found": sorted(hits)})
    return out


LOGO_VERSION = 2   # bump when the matching changes (cached per reference x photo); 2: corners kept for the shape check


def logo_matches(photos, ev, work):
    """Eden's confirmed reference logos (work/logo_refs/<firm>/refs/*.png, logo_refs.py after his review) in each
    photo: SuperPoint (2,048 points) + LightGlue matches, an OpenCV USAC_MAGSAC homography (8 px). Found when the
    inliers reach evidence.logo_min_inliers and the reference's corners project to a convex quadrilateral that covers
    0.05%-90% of the photo with no side shorter than 8 px (PLAN step 5: 投影回去的四邊形是凸的、大小合理), whose corners are all
    45-135 degrees and whose aspect ratio is within a factor of 2 of the reference's (10/19: on the R development
    photos, wrong matches projected to strongly skewed quadrilaterals, right ones to near rectangles).
    The geometry is cached; the thresholds are applied afterwards."""
    import cv2
    import numpy as np
    import torch
    from PIL import Image
    cache = Cache(work / "logo.jsonl")
    state = {}

    def models():
        if "m" not in state:
            from lightglue import LightGlue, SuperPoint
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            state["m"] = (SuperPoint(max_num_keypoints=int(ev.get("superpoint_keypoints", 2048))).eval().to(dev),
                          LightGlue(features="superpoint").eval().to(dev), dev)
        return state["m"]

    def feats(path, cache_f={}):
        if path not in cache_f:
            from lightglue.utils import numpy_image_to_torch
            ext, _, dev = models()
            with Image.open(path) as im:
                arr = np.asarray(im.convert("RGB"))
            with torch.no_grad():
                f = ext.extract(numpy_image_to_torch(arr).to(dev))
            cache_f[path] = (f, arr.shape[1], arr.shape[0])
        return cache_f[path]

    min_inl = int(ev.get("logo_min_inliers", 15))
    # geometry checks (10/22: read from configs so the paper's numbers have a source; the defaults are the values used so far)
    ang_lo, ang_hi = ev.get("logo_angle_range", [45, 135])
    area_lo, area_hi = ev.get("logo_area_range", [0.0005, 0.9])
    min_side_px = float(ev.get("logo_min_side_px", 8))
    asp_lo, asp_hi = ev.get("logo_aspect_range", [0.5, 2.0])
    for ph in photos:
        refs = sorted((ROOT / "work" / "logo_refs" / ph["firm"] / "refs").glob("*.png"))
        out = []
        for ref in refs:
            k = sha(f"{LOGO_VERSION}|{hashlib.sha1(ref.read_bytes()).hexdigest()}|{ph['crop']}")
            r = cache.get(k)
            if r is None:
                from lightglue.utils import rbd
                _, matcher, dev = models()
                f0, w0, h0 = feats(str(ref))
                f1, w1, h1 = feats(str(ROOT / "work" / "extract" / "crops" / ph["crop"]))
                with torch.no_grad():
                    m = rbd(matcher({"image0": f0, "image1": f1}))
                k0 = rbd({"k": f0["keypoints"]})["k"].cpu().numpy()[m["matches"][:, 0].cpu().numpy()]
                k1 = rbd({"k": f1["keypoints"]})["k"].cpu().numpy()[m["matches"][:, 1].cpu().numpy()]
                r = {"key": k, "ref": f"{ph['firm']}/{ref.name}", "crop": ph["crop"], "matches": int(len(k0)), "inliers": 0,
                     "convex": False, "area": 0.0, "min_side": 0.0, "angles": [], "aspect_ratio_vs_ref": None, "quad": None}
                if len(k0) >= 4:
                    H, mask = cv2.findHomography(k0, k1, cv2.USAC_MAGSAC, 8.0)
                    if H is not None and mask is not None:
                        quad = cv2.perspectiveTransform(np.float32([[[0, 0]], [[w0, 0]], [[w0, h0]], [[0, h0]]]), H).reshape(4, 2)
                        sides = [float(np.linalg.norm(quad[i] - quad[(i + 1) % 4])) for i in range(4)]
                        ang = []
                        for i in range(4):
                            u, v = quad[i - 1] - quad[i], quad[(i + 1) % 4] - quad[i]
                            c = float(np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-9))
                            ang.append(round(float(np.degrees(np.arccos(max(-1.0, min(1.0, c))))), 1))
                        asp = ((sides[0] + sides[2]) / 2) / max((sides[1] + sides[3]) / 2, 1e-6)
                        r.update(inliers=int(mask.sum()), convex=bool(cv2.isContourConvex(quad.astype(np.float32))),
                                 area=round(float(cv2.contourArea(quad.astype(np.float32))) / (w1 * h1), 5),
                                 min_side=round(min(sides), 1), angles=ang,
                                 aspect_ratio_vs_ref=round(asp / (w0 / h0), 3), quad=[[round(float(a), 1) for a in q] for q in quad])
                cache.put(r)
            shape_ok = bool(r["angles"]) and all(ang_lo <= a <= ang_hi for a in r["angles"]) and asp_lo <= (r["aspect_ratio_vs_ref"] or 0) <= asp_hi
            found = r["inliers"] >= min_inl and r["convex"] and area_lo <= r["area"] <= area_hi and r["min_side"] >= min_side_px and shape_ok
            out.append({k2: r[k2] for k2 in ("ref", "matches", "inliers", "convex", "area", "min_side", "angles", "aspect_ratio_vs_ref")}
                       | {"shape_ok": shape_ok, "found": found})
        ph["logo"] = {"available": bool(refs), "refs": len(refs), "matches": out, "found": any(x["found"] for x in out)}
    return {"new": cache.new, "min_inliers": min_inl, "version": LOGO_VERSION}


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--crops", help="a file with one crop name per line")
    src.add_argument("--r-items", choices=["dev", "test"], help="the photos of annotations/<split>/R_items.jsonl")
    ap.add_argument("--context", default="rules", help="'rules' or a context_link.py file (work/context/*.jsonl)")
    ap.add_argument("--labels", help="r-import result file, or annotations/dev/R.jsonl")
    ap.add_argument("--labelled-only", action="store_true", help="with --r-items and --labels: only the labelled photos")
    ap.add_argument("--holistic", action="store_true", help="also the one-question ablation")
    ap.add_argument("--vlm", choices=["main", "alt"], default="main", help="alt: evidence.vlm_alt (Qwen2.5-VL-7B, the model before 10/20)")
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()
    from PIL import Image
    import baselines_evidence as be
    import logo_refs as lr

    cfg = yaml.safe_load(open(ROOT / "configs" / "default.yaml", encoding="utf-8"))
    import copy
    cfg_as_frozen = copy.deepcopy(cfg)   # 10/27: what frozen.check compares (before any --vlm swap)
    ev = cfg["evidence"]
    if args.vlm == "alt":   # 10/20: the second VLM (evidence.vlm_alt); caches are keyed by revision
        if not ev.get("vlm_alt_revision"):
            sys.exit("configs/default.yaml 的 evidence.vlm_alt_revision 還沒填")
        ev["vlm"], ev["vlm_revision"] = ev["vlm_alt"], ev["vlm_alt_revision"]
    if args.crops:
        crops = Path(args.crops).read_text(encoding="utf-8").split()
        items_by_crop = {}
    else:
        rows = [json.loads(l) for l in open(ROOT / "annotations" / args.r_items / "R_items.jsonl", encoding="utf-8")]
        if args.labelled_only:
            if not (args.labels and args.labels.endswith(".jsonl")):
                sys.exit("--labelled-only 要配 --labels annotations/dev/R.jsonl")
            lab = {json.loads(l)["item"]: json.loads(l) for l in open(args.labels, encoding="utf-8")}
            rows = [r for r in rows if r["item"] in lab and not lab[r["item"]].get("not_photo") and lab[r["item"]].get("level")]
        crops = [r["crop"] for r in rows]
        items_by_crop = {r["crop"]: r["item"] for r in rows}
    filt = {}
    with open(ROOT / "work" / "photo_filter.csv", encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if d["crop"] in set(crops):
                filt[d["crop"]] = d
    if any(filt[c]["split"] != "dev" for c in crops):   # the test split: frozen settings only (CLAUDE.md rule 3)
        import frozen
        frozen.check("test", cfg_as_frozen)   # 10/27: the configuration before --vlm alt swapped the compared model in
    recs = {}
    for rep in sorted({filt[c]["report"] for c in crops}):
        for line in open(ROOT / "work" / "extract" / f"{rep}_pairs.jsonl", encoding="utf-8"):
            d = json.loads(line)
            if d["crop"] in filt:
                recs[d["crop"]] = d
    prompts = {k: (ROOT / "configs" / "prompts" / f"{k}.txt").read_text(encoding="utf-8") for k in PROMPTS}
    phash = {k: sha256_file(ROOT / "configs" / "prompts" / f"{k}.txt") for k in PROMPTS}
    work = ROOT / cfg["paths"]["evidence"]
    caches = {k: Cache(work / f"{k}.jsonl") for k in ("gate", "question", "vqa", "topical", "holistic")}
    M = Models(ev)
    t0 = time.time()
    with open(ROOT / cfg["paths"]["catalog"], encoding="utf-8-sig", newline="") as f:
        catalog = list(csv.DictReader(f))
    names, name_sources = lr.load_names(catalog, ROOT / "data" / "index")
    lp = {"ocr_min_score": cfg["logo_refs"].get("ocr_min_score", 0.5), "name_bigram_min": float(ev.get("ocr_match_threshold", 0.7)),
          "short_name_split_punct": bool(ev.get("short_name_split_punct", False))}   # 10/22 NEXT C11

    photos = []
    ctx = be.load_contexts(args.context, crops, recs, ev["context_max_chars"])
    import context_link as cl
    for c in crops:
        labelled, plain, parts = ctx[c]
        topic = recs[c].get("local_heading") or recs[c].get("heading") or ""
        if not topic.strip():   # manual v1.5: no describing text -> the smallest section heading above the photo
            pg = next((x for x in cl.load_pages(filt[c]["report"]) if x["page"] == int(filt[c]["page"])), None)
            pic = next((x for x in (pg or {}).get("pictures", []) if x["crop"] == c), None)
            chain = cl.heading_chain(filt[c]["report"], int(filt[c]["page"]), pic.get("order") or 0) if pic else []
            topic = chain[-1]["text"] if chain else ""
        photos.append({"crop": c, "item": items_by_crop.get(c), "report": filt[c]["report"], "firm": filt[c]["firm_id"],
                       "year": int(filt[c]["year"]), "page": int(filt[c]["page"]), "context": labelled, "parts": parts,
                       "topic": " ".join(topic.split())[:60]})

    # 1. gate (language model)
    for ph in photos:
        k = sha(f"{ev['llm_revision']}|{phash['gate']}|{ph['context']}")
        r = caches["gate"].get(k)
        if r is None:
            if ph["context"] == be.EMPTY_CONTEXT:
                out, parsed, tries = "", {"referents": [], "anchors": {"org": [], "event": [], "year": []}}, 0
            else:
                prompt = prompts["gate"].replace("{context}", ph["context"])
                out = M.generate(prompt)
                parsed, tries = parse_json(out, "object"), 1
                if parsed is None:
                    out = M.generate(prompt + "\n只輸出 JSON，第一個字是 {，最後一個字是 }。")
                    parsed, tries = parse_json(out, "object"), 2
            r = {"key": k, "output": out, "parsed": parsed, "tries": tries}
            caches["gate"].put(r)
        g = r["parsed"] or {}
        refs = [x for x in (g.get("referents") or []) if isinstance(x, dict) and str(x.get("text", "")).strip()]
        anc = g.get("anchors") or {}
        ph["gate"] = {"ok": r["parsed"] is not None, "tries": r["tries"], "referents": refs,
                      "anchors": {k2: [str(x) for x in (anc.get(k2) or []) if str(x).strip()] for k2 in ("org", "event", "year")},
                      "topic": " ".join(str(g.get("topic") or "").split())[:30]}
        if ph["gate"]["topic"]:   # the rule-based local heading is often a sentence fragment (10/19 draft); the gate's topic first
            ph["topic"] = ph["gate"]["topic"]
    print(f"[gate] {caches['gate'].new} 新，{time.time() - t0:.0f} 秒", flush=True)

    # 2. questions (language model)
    for ph in photos:
        refs = ph["gate"]["referents"]
        if not refs:
            ph["questions"] = []
            continue
        listing = "\n".join(f"{i + 1}. {x.get('type', '')}：{x['text']}" for i, x in enumerate(refs))
        k = sha(f"{ev['llm_revision']}|{phash['question']}|{listing}")
        r = caches["question"].get(k)
        if r is None:
            prompt = prompts["question"].replace("{referents}", listing)
            out = M.generate(prompt)
            parsed, tries = parse_json(out, "array"), 1
            if parsed is None:
                out = M.generate(prompt + "\n只輸出 JSON 陣列，第一個字是 [，最後一個字是 ]。")
                parsed, tries = parse_json(out, "array"), 2
            r = {"key": k, "output": out, "parsed": parsed, "tries": tries}
            caches["question"].put(r)
        qs = [q for q in (r["parsed"] or []) if isinstance(q, dict) and str(q.get("question", "")).strip()]
        if not qs:   # template fallback (recorded)
            qs = [{"id": i + 1, "question": f"照片裡有{x['text']}嗎？", "depends_on": None, "template": True}
                  for i, x in enumerate(refs)]
        ph["questions"] = [{"id": q.get("id", i + 1), "question": str(q["question"]).strip(), "depends_on": q.get("depends_on"),
                            "template": bool(q.get("template"))} for i, q in enumerate(qs)]
    print(f"[questions] {caches['question'].new} 新，{time.time() - t0:.0f} 秒", flush=True)
    M.free_llm()

    # 3. answers and 5. topical (vision-language model)
    for ph in photos:
        img = None

        def image():
            nonlocal img
            if img is None:
                with Image.open(ROOT / "work" / "extract" / "crops" / ph["crop"]) as im:
                    img = im.convert("RGB")
            return img
        for q in ph["questions"]:
            k = sha(f"{ev['vlm_revision']}|{phash['vqa']}|{ev['vlm_max_pixels']}|{ph['crop']}|{q['question']}")
            r = caches["vqa"].get(k)
            if r is None:
                r = {"key": k, "p_yes": M.p_yes(image(), prompts["vqa"].strip().replace("{question}", q["question"]))}
                caches["vqa"].put(r)
            q["p_yes"] = r["p_yes"]
        by_id = {q["id"]: q for q in ph["questions"]}
        for q in ph["questions"]:   # DSG: a question whose parent is answered no counts as no
            deps = q["depends_on"] if isinstance(q["depends_on"], list) else ([] if q["depends_on"] is None else [q["depends_on"]])
            pars = [by_id[d] for d in deps if isinstance(d, (int, str)) and d in by_id and by_id[d] is not q]   # one or several parents
            q["p_eff"] = 0.0 if any(par["p_yes"] < ev["depiction_threshold"] for par in pars) else q["p_yes"]
        ps = [q["p_eff"] for q in ph["questions"]]
        ph["depicted_max"] = max(ps) if ps else 0.0
        ph["depicted_mean"] = round(statistics.mean(ps), 4) if ps else 0.0
        if ph["topic"]:
            k = sha(f"{ev['vlm_revision']}|{phash['topical']}|{ev['vlm_max_pixels']}|{ph['crop']}|{ph['topic']}")
            r = caches["topical"].get(k)
            if r is None:
                r = {"key": k, "p_yes": M.p_yes(image(), prompts["topical"].strip().replace("{topic}", ph["topic"]))}
                caches["topical"].put(r)
            ph["topical_p"] = r["p_yes"]
        else:
            ph["topical_p"] = None
        ph["holistic_p"] = None
        if args.holistic and ph["context"] != be.EMPTY_CONTEXT:
            k = sha(f"{ev['vlm_revision']}|{phash['holistic']}|{ev['vlm_max_pixels']}|{ph['crop']}|{ph['context']}")
            r = caches["holistic"].get(k)
            if r is None:
                r = {"key": k, "p_yes": M.p_yes(image(), prompts["holistic"].strip().replace("{context}", ph["context"]))}
                caches["holistic"].put(r)
            ph["holistic_p"] = r["p_yes"]
    print(f"[vlm] 問答 {caches['vqa'].new} 新、主題 {caches['topical'].new} 新，{time.time() - t0:.0f} 秒", flush=True)

    # 4. anchors: OCR (logos wait for Eden's review)
    ocr, ocr_info = ocr_photos(crops, work / "ocr_photo", cfg["logo_refs"].get("ocr_lang", "chinese_cht"))
    for ph in photos:
        years = list(dict.fromkeys(ph["gate"]["anchors"]["year"] + [str(ph["year"])]))
        events = ph["gate"]["anchors"]["event"]
        m = anchor_match(ocr.get(ph["crop"]), names.get(ph["firm"], []), events, years, lp)
        ph["ocr_text"] = " / ".join(t for t, s in zip(ocr.get(ph["crop"], {}).get("texts", []), ocr.get(ph["crop"], {}).get("scores", []))
                                    if s >= lp["ocr_min_score"])[:300]
        ph["anchor_ocr"] = m
        ph["anchored_text"] = bool(m["org"] or m["event"] or m["year"])
    logo_info = logo_matches(photos, ev, work)
    for ph in photos:
        ph["anchored"] = ph["anchored_text"] or ph["logo"]["found"]
    print(f"[logo] 新比對 {logo_info['new']}，{time.time() - t0:.0f} 秒", flush=True)

    # 6. levels
    dt, tt = float(ev["depiction_threshold"]), float(ev["topical_threshold"])
    for ph in photos:
        depicted = bool(ph["questions"]) and ph["depicted_max"] >= dt
        topical = ph["topical_p"] is not None and ph["topical_p"] >= tt
        ph["level4"] = ("證據" if ph["anchored"] else "示意") if depicted else ("包裝" if topical else "無關")
        ph["level3"] = TO3[ph["level4"]]

    out_path = work / f"evidence_{args.tag}.jsonl"
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for ph in photos:
            f.write(json.dumps(ph, ensure_ascii=False) + "\n")

    res = {"tag": args.tag, "photos": len(photos), "context": be.context_name(args.context), "context_arg": args.context,
           "models": {"llm": ev["llm"], "llm_revision": ev["llm_revision"], "vlm": ev["vlm"], "vlm_revision": ev["vlm_revision"],
                      "vlm_max_pixels": ev["vlm_max_pixels"], "ocr": ocr_info.get("model")},
           "prompts_sha256": phash, "thresholds": {"depiction": dt, "topical": tt}, "name_sources": name_sources,
           "new_outputs": {k: c.new for k, c in caches.items()}, "ocr": {k: v for k, v in ocr_info.items() if k != "model"},
           "gate_failed": sum(1 for ph in photos if not ph["gate"]["ok"]),
           "no_referents": sum(1 for ph in photos if not ph["gate"]["referents"]),
           "questions": sum(len(ph["questions"]) for ph in photos),
           "template_questions": sum(1 for ph in photos for q in ph["questions"] if q["template"]),
           "levels4": dict(collections.Counter(ph["level4"] for ph in photos)),
           "anchored_by_ocr": sum(ph["anchored_text"] for ph in photos),
           "anchor_kinds": {"org": sum(1 for ph in photos if ph["anchor_ocr"]["org"]),
                            "event": sum(1 for ph in photos if ph["anchor_ocr"]["event"]),
                            "year": sum(1 for ph in photos if ph["anchor_ocr"]["year"])},
           "logo": dict(logo_info, firms_in_set=len({ph["firm"] for ph in photos}),
                        firms_with_refs=len({ph["firm"] for ph in photos if ph["logo"]["available"]}),
                        photos_with_refs=sum(ph["logo"]["available"] for ph in photos),
                        photos_logo_found=sum(ph["logo"]["found"] for ph in photos)),
           "anchored": sum(ph["anchored"] for ph in photos),
           "seconds": round(time.time() - t0, 1)}

    if args.labels:   # compare with annotation R
        lab = json.loads(Path(args.labels).read_text(encoding="utf-8")) if args.labels.endswith(".json") else \
            {"rows": [json.loads(l) for l in open(args.labels, encoding="utf-8")]}
        lab_rows = {r["item"]: r for r in lab["rows"] if r.get("status", "done") == "done"}
        if not items_by_crop:   # draft: items from the draft list
            items_by_crop = {json.loads(l)["crop"]: json.loads(l)["item"] for l in open(ROOT / "work" / "annotation" / "R" / "draft" / "items.jsonl", encoding="utf-8")}
        pairs = []
        for ph in photos:
            r = lab_rows.get(items_by_crop.get(ph["crop"]))
            if r and not r.get("not_photo") and r.get("level"):
                pairs.append((r["level"], ph["level3"], r, ph))
        conf = {a: dict(collections.Counter(b for x, b, _, _ in pairs if x == a)) for a in ZH3}
        f1s = []
        for c in ZH3:
            tp = sum(1 for a, b, _, _ in pairs if a == c and b == c)
            fp = sum(1 for a, b, _, _ in pairs if a != c and b == c)
            fn = sum(1 for a, b, _, _ in pairs if a == c and b != c)
            f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
        anc = [(bool(r.get("anchor_text")), ph["anchored_text"]) for _, _, r, ph in pairs]
        lg = [(bool(r.get("anchor_logo")), ph["logo"]["found"]) for _, _, r, ph in pairs if ph["logo"]["available"]]
        res["vs_labels"] = {"labels": args.labels, "pairs": len(pairs), "same_level": sum(a == b for a, b, _, _ in pairs),
                            "macro_f1": round(statistics.mean(f1s), 4) if pairs else None,
                            "confusion_label_by_model": conf,
                            "anchor_text": {"label_yes": sum(a for a, _ in anc), "ocr_yes": sum(b for _, b in anc),
                                            "both": sum(a and b for a, b in anc)},
                            "anchor_logo": {"photos_with_refs": len(lg), "label_yes": sum(a for a, _ in lg), "found": sum(b for _, b in lg),
                                            "both": sum(a and b for a, b in lg)}}
        for _, _, r, ph in pairs:
            ph["label"] = {"level": r["level"], "anchor_text": r.get("anchor_text"), "anchor_logo": r.get("anchor_logo")}

    stamp = run_stamp()
    rpath = ROOT / "results" / f"evidence_{args.tag}_{stamp}.json"
    k = 2
    while rpath.exists():
        rpath = ROOT / "results" / f"evidence_{args.tag}_{stamp}_{k}.json"
        k += 1
    res["per_photo"] = [{k2: ph.get(k2) for k2 in ("crop", "item", "level4", "level3", "depicted_max", "depicted_mean", "topical_p", "holistic_p",
                                                  "anchored_text", "anchored", "label")} | {"logo_found": ph["logo"]["found"]} for ph in photos]
    meta = run_meta("configs/default.yaml", [ROOT / "work" / "photo_filter.csv"] + [ROOT / "configs" / "prompts" / f"{k2}.txt" for k2 in PROMPTS]
                    + ([Path(args.labels)] if args.labels else []) + ([ROOT / args.context] if args.context != "rules" else []),
                    packages=("torch", "transformers", "pillow", "pyyaml"))
    rpath.write_text(json.dumps({"meta": meta, **res}, ensure_ascii=False, indent=1), encoding="utf-8")
    write_html(ROOT / "reports" / f"{rpath.stem}.html", photos, res)   # the same name as the result file
    print(json.dumps({k2: res[k2] for k2 in ("photos", "gate_failed", "no_referents", "questions", "template_questions", "levels4",
                                             "anchored_by_ocr", "anchor_kinds", "logo", "anchored", "seconds")} | ({"vs_labels": res["vs_labels"]} if "vs_labels" in res else {}),
                     ensure_ascii=False))
    print(f"wrote {out_path}, {rpath} and reports/{rpath.stem}.html")


def write_html(path, photos, res):
    from PIL import Image

    def thumb(c):
        with Image.open(ROOT / "work" / "extract" / "crops" / c) as im:
            im = im.convert("RGB")
            im.thumbnail((420, 420))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=82)
        return base64.b64encode(buf.getvalue()).decode()
    cards = []
    for ph in photos:
        refs = "、".join(f"{html.escape(x['text'])}" for x in ph["gate"]["referents"]) or "（沒有拍得到的對象）"
        qs = "".join(f"<li>{html.escape(q['question'])} → <b>{q['p_yes']:.2f}</b>{'（模板）' if q['template'] else ''}</li>" for q in ph["questions"])
        a = ph["anchor_ocr"]
        anc = "；".join(filter(None, [f"公司名 {html.escape(a['org']['name'])}" if a["org"] else "",
                                      "活動 " + "、".join(html.escape(e["name"]) for e in a["event"]) if a["event"] else "",
                                      "年份 " + "、".join(html.escape(y["year"]) for y in a["year"]) if a["year"] else "",
                                      "logo（" + "、".join(f'{html.escape(x["ref"])} 內點 {x["inliers"]}' for x in ph["logo"]["matches"] if x["found"]) + "）"
                                      if ph["logo"]["found"] else ""])) or ("沒有" + ("" if ph["logo"]["available"] else "（這家沒有確認過的 logo）"))
        lab = ph.get("label")
        labh = f"<div>Eden：<b>{ZH3[lab['level']]}</b>{'、有字定錨' if lab.get('anchor_text') else ''}{'、有 logo' if lab.get('anchor_logo') else ''}</div>" if lab else ""
        ctx = "".join(f'<div><span class="k">{html.escape(k)}</span>{html.escape(v[:200])}</div>' for k, v in ph["parts"]) or "（沒有文字）"
        tp = "—" if ph["topical_p"] is None else f"{ph['topical_p']:.2f}"
        cards.append(f'<section><img src="data:image/jpeg;base64,{thumb(ph["crop"])}" alt=""><div class="txt">'
                     f'<h2>{ph["report"]} 第 {ph["page"]} 頁 → <b>{ph["level4"]}</b>（{ZH3[ph["level3"]]}）</h2>{labh}'
                     f'<details><summary>上下文</summary>{ctx}</details>'
                     f'<div>拍得到的對象：{refs}</div><ol>{qs}</ol>'
                     f'<div>主題「{html.escape(ph["topic"])}」：{tp}</div>'
                     f'<div>照片裡讀到的字：{html.escape(ph["ocr_text"]) or "（沒有）"}</div><div>定錨：{anc}</div></div></section>')
    v = res.get("vs_labels")
    cmp_ = (f"<p>跟 Eden 的三級比（{v['pairs']} 張）：同一級 {v['same_level']} 張，macro-F1 {v['macro_f1']}。照片裡有字定錨：Eden 勾 {v['anchor_text']['label_yes']} 張、"
            f"OCR 找到 {v['anchor_text']['ocr_yes']} 張、兩者都有 {v['anchor_text']['both']} 張。</p>") if v else ""
    lv = "、".join(f"{k} {res['levels4'].get(k, 0)}" for k in LEVEL4)
    page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>證據分級</title><style>
body{{font-family:system-ui,"Microsoft JhengHei",sans-serif;margin:16px;background:#fafafa;color:#222;max-width:1000px}}
section{{display:flex;flex-wrap:wrap;gap:16px;border-top:1px solid #ddd;padding:16px 0}} section img{{max-width:min(420px,100%);align-self:flex-start}}
.txt{{flex:1;min-width:260px;font-size:14px;line-height:1.6}} h2{{font-size:15px;margin:0 0 6px}} .k{{display:inline-block;min-width:4.5em;color:#666}}
</style></head><body><h1 style="font-size:20px">證據分級（模組 2）：{res['photos']} 張</h1>
<p>四級：{lv}。門檻是初值（拍到 {res['thresholds']['depiction']}、主題 {res['thresholds']['topical']}），只在開發集調。logo 只用 Eden 確認過的參考圖（{res['logo']['firms_with_refs']}／{res['logo']['firms_in_set']} 家有）。</p>{cmp_}
{"".join(cards)}</body></html>"""
    path.write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
