"""Annotation tasks for Label Studio, and the checks after each batch (PLAN §6, annotations/guidelines/).

This round covers task A only (is it a photo?). R, B, C and D come later.

Blind labelling (README "原則", PLAN D9): the task data that Label Studio sees holds an opaque item id,
the batch number and the image, nothing else. No score, no model answer, no report or crop name.
Item ids are a salted hash (the salt is in work/annotation/A/salt.txt, not in git), so they cannot be
traced back to crop names without this computer. Known-answer check items go through exactly the same
code path as real items; checks in the development batches come from development-set firms and checks in
the test batches from test-set firms (outside the 200 real items), so the firm does not give them away.

  python src/annotate_export.py a-sample                      # 100 dev + 200 test items (after photo_filter.py --all)
  python src/annotate_export.py a-qc-candidates               # example/practice/check candidates + the page Eden confirms
  python src/annotate_export.py a-qc-import FILE.json         # Eden's confirmed answers (downloaded from that page)
  python src/annotate_export.py a-student-pages               # guidelines, examples, practice pages (local HTML, not in git)
  python src/annotate_export.py a-pages                       # one HTML page per annotator and batch + package_a1/a2.zip
  python src/annotate_export.py a-check --annotator a1        # after each batch: reads work/annotation/A/returns/
  python src/annotate_export.py a-redo --annotator a1 --batch 3   # a batch failed the check: same items, new order
  python src/annotate_export.py a-tasks                       # Label Studio import files (backup route)

Task A is done with self-contained HTML pages (Eden's decision 12, 10/18 21:29): one page per annotator and
batch, one item per screen, keys 1/2/3, progress kept in the browser, "Download answers" saves a JSON file that
the annotator emails to Eden. Each page carries a page_id (a hash of annotator, batch, version and item list);
a-check accepts an answer file only when its page_id is this annotator's current version of that batch.

Files
  configs/labelstudio/A.xml                  labelling interface for the Label Studio backup route
  annotations/{dev,test}/A_items.jsonl       the real items: item id, crop, report, page, sampling stratum
  annotations/{dev,test}/A.jsonl             labels: item, annotator, answer, not_sure, seconds, batch, version,
                                             guideline version, source file
  annotations/qc/                            candidates, Eden's answers, examples, practice sets, the batch plan
                                             (A_task_plan.json, every version), check keys and check answers
  work/annotation/A/                         salt, package_a1.zip / package_a2.zip, returns/ (answer files),
                                             redo/ (new batch versions), draft/ (before Eden confirms), not in git
  reports/annotation_A/eden/                 the answer-confirmation page (shows answers: never give it to annotators)
  reports/annotation_A/students/             guidelines, examples, practice (also inside every package)
"""
import argparse
import base64
import collections
import csv
import hashlib
import html
import io
import json
import random
import re
import secrets
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from meta import run_meta, run_stamp, sha256_file  # noqa: E402

GUIDELINE_VERSION = "v1.5"   # annotations/guidelines/A_photo_zh.md; bump together with the manual
ANNOTATORS = ("a1", "a2")
BATCH_REAL = 50              # real items per batch (README: 每批 50 題)
CHECKS_PER_BATCH = 5         # one known-answer item per 10 real items
DEV_PER_BIN, N_BINS, N_TEST = 10, 10, 200
LIMITS = {"check_error": 0.20, "median_seconds": 1.0, "same_answer_run": 30}   # README 品質控管, task A
IMG_LONG_SIDE, IMG_QUALITY = 1024, 85
ANSWERS = ("Photo", "Not a photo")
NEED = {"example": (10, 20), "practice": 40, "check_dev": 10, "check_test": 20}  # per README and the batch plan

REASONS = {  # fixed Chinese categories (decision 15); the program assigns them, Eden does not choose
    "photo_real": ("Photo", "相機拍的真實場景或人物"),
    "chart": ("Not a photo", "圖表或資訊圖表"),
    "illustration": ("Not a photo", "插畫或圖示"),
    "logo": ("Not a photo", "logo"),
    "scan": ("Not a photo", "文件或證書掃描"),
    "screenshot": ("Not a photo", "網頁截圖"),
    "design": ("Not a photo", "設計圖或 3D 圖"),
}
ANSWER_ZH = {"Photo": "照片", "Not a photo": "不是照片"}
PROMPT_REASON = {  # the non-photo prompt with the highest probability gives the default reason for "不是照片"
    "an illustration": "illustration", "a chart or infographic": "chart", "a logo or icon": "logo",
    "a scanned certificate or document": "scan", "an abstract graphic design": "design",
}
# candidates: pool (firms) x kind -> how many, and the planned roles (Eden can change every role)
PLAN = [
    ("dev", "confident_photo", 30, {"check": 6, "example": 6, "practice": 12}),
    ("dev", "confident_nonphoto", 30, {"check": 6, "example": 6, "practice": 12}),
    ("dev", "near_threshold", 40, {"example": 4, "practice": 16}),
    ("test", "confident_photo", 16, {"check": 12}),
    ("test", "confident_nonphoto", 16, {"check": 12}),
]
ROLES = ("check", "example", "practice", "spare", "drop")
LABEL_CONFIG = """<View>
  <Header value="這是不是照片？ 1＝照片、2＝不是照片、3＝不確定（另外勾，還是要選 1 或 2）。不要按跳過。"/>
  <Image name="image" value="$image" zoom="true" zoomControl="true" maxWidth="100%" maxHeight="72vh"/>
  <Choices name="answer" toName="image" choice="single-radio" required="true" showInline="true">
    <Choice value="照片" alias="Photo" hotkey="1"/>
    <Choice value="不是照片" alias="Not a photo" hotkey="2"/>
  </Choices>
  <Choices name="not_sure" toName="image" choice="multiple" showInline="true">
    <Choice value="不確定" alias="Not sure" hotkey="3"/>
  </Choices>
</View>
"""


# ---------------------------------------------------------------- helpers
def load_yaml(path):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def ctx():
    cfg = load_yaml(ROOT / "configs" / "default.yaml")
    paths = cfg["paths"]
    return {"cfg": cfg, "seed": cfg["seed"], "crop_dir": ROOT / paths["extract"] / "crops",
            "ann": ROOT / paths["annotations"], "qc": ROOT / paths["annotations"] / "qc",
            "work": ROOT / "work" / "annotation" / "A", "pages": ROOT / paths["reports"] / "annotation_A",
            "results": ROOT / paths["results"], "filter_csv": ROOT / "work" / "photo_filter.csv",
            "guide": ROOT / "annotations" / "guidelines" / "A_photo_zh.md"}


def salt(c, create=False):
    """Secret salt for item ids, kept on this computer only (work/ is not in git)."""
    p = c["work"] / "salt.txt"
    if not p.exists():
        if not create:
            sys.exit(f"沒有 {p}：先跑 a-sample")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_hex(16), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()


def item_id(c, crop):
    """Opaque Label Studio id, the same scheme for real and check items, so nothing tells them apart."""
    return "A" + hashlib.sha256(f"{salt(c)}:A:{crop}".encode()).hexdigest()[:8]


def cand_id(c, crop):
    """Id shown on Eden's confirmation page; different from the Label Studio id on purpose."""
    return "Q" + hashlib.sha256(f"{c['seed']}:A-qc:{crop}".encode()).hexdigest()[:6]


def image_data_uri(crop_path, long_side=IMG_LONG_SIDE, quality=IMG_QUALITY):
    """Every task image goes through here: same size limit and JPEG settings for real and check items
    (smaller sizes only for pages that are not tasks, e.g. Eden's confirmation page)."""
    from PIL import Image
    with Image.open(crop_path) as im:
        im = im.convert("RGB")
        im.thumbnail((long_side, long_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def candidates(c):
    """Crops the photo filter gives to the model (no exclusion before the model): the population of task A."""
    if not c["filter_csv"].exists():
        sys.exit(f"沒有 {c['filter_csv']}：先跑 python src/photo_filter.py --all")
    pre = {"image_only_report", "cover_page", "vector_shading", "layout_repeat", "duplicate"}
    out = []
    with open(c["filter_csv"], encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if d["photo_score"] in ("", None) or set(d["exclude"].split("|")) & pre:
                continue
            d["photo_score"] = float(d["photo_score"])
            out.append(d)
    return out


def dhash_of(c, crop):
    import extract_pairs as ep
    return ep.dhash(c["crop_dir"] / crop)


def near_duplicate(h, hashes, bits=4):
    return any(bin(h ^ x).count("1") <= bits for x in hashes)


def all_real_items(c):
    return read_jsonl(c["ann"] / "dev" / "A_items.jsonl") + read_jsonl(c["ann"] / "test" / "A_items.jsonl")


def write_result(c, name, summary, inputs):
    c["results"].mkdir(exist_ok=True)
    stamp = run_stamp()
    path = c["results"] / f"{name}_{stamp}.json"
    k = 2
    while path.exists():  # never overwrite an old result (CLAUDE.md rule 2), even within the same minute
        path = c["results"] / f"{name}_{stamp}_{k}.json"
        k += 1
    path.write_text(json.dumps({"meta": run_meta("configs/default.yaml", [str(p) for p in inputs]), **summary},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {path}")
    return path


# ---------------------------------------------------------------- a-sample
def cmd_sample(args):
    c = ctx()
    for split in ("dev", "test"):
        if (c["ann"] / split / "A_items.jsonl").exists() and not args.force:
            sys.exit(f"annotations/{split}/A_items.jsonl 已經存在；要重抽請加 --force（會換掉題目）")
    if args.force and any(read_jsonl(c["ann"] / s / "A.jsonl") for s in ("dev", "test")):
        sys.exit("已經有標註結果（annotations/*/A.jsonl），不能重抽題目")
    salt(c, create=True)
    cand = candidates(c)
    rng = random.Random(f"{c['seed']}:A:sample")
    dev = sorted((d for d in cand if d["split"] == "dev"), key=lambda d: d["crop"])
    test = sorted((d for d in cand if d["split"] == "test"), key=lambda d: d["crop"])
    bins = [[d for d in dev if min(int(d["photo_score"] * N_BINS), N_BINS - 1) == b] for b in range(N_BINS)]
    for b in bins:
        rng.shuffle(b)
    take = [[(b, d) for d in bins[b][:DEV_PER_BIN]] for b in range(N_BINS)]
    used = {d["crop"] for t in take for _, d in t}
    short = {b: DEV_PER_BIN - len(t) for b, t in enumerate(take) if len(t) < DEV_PER_BIN}
    for b, need in short.items():  # README: 不夠的從相鄰層補 (nearest bins first, the lower one first)
        for dist in range(1, N_BINS):
            for nb in (b - dist, b + dist):
                if need and 0 <= nb < N_BINS:
                    for d in [d for d in bins[nb] if d["crop"] not in used][:need]:
                        take[b].append((nb, d))
                        used.add(d["crop"])
                        need -= 1
            if not need:
                break
    dev_items = [dict(d, stratum=f"bin{b}", **({"from_bin": nb} if nb != b else {}))
                 for b, t in enumerate(take) for nb, d in t]
    test_items = [dict(d, stratum="random") for d in rng.sample(test, min(N_TEST, len(test)))]
    for split, items in (("dev", dev_items), ("test", test_items)):
        rows = [{"item": item_id(c, d["crop"]), "crop": d["crop"], "report": d["report"], "page": int(d["page"]),
                 "split": split, "stratum": d["stratum"], **({"from_bin": d["from_bin"]} if "from_bin" in d else {})}
                for d in items]
        write_jsonl(c["ann"] / split / "A_items.jsonl", rows)
    summary = {"dev_candidates": len(dev), "test_candidates": len(test), "dev_bin_sizes": [len(b) for b in bins],
               "dev_items": len(dev_items), "dev_items_per_bin": [len(t) for t in take], "dev_short_bins": short,
               "test_items": len(test_items), "dev_reports": len({d["report"] for d in dev_items}),
               "test_reports": len({d["report"] for d in test_items}),
               "note": "dev is stratified by photo score: weight each bin by its size (dev_bin_sizes) when choosing the threshold"}
    write_result(c, "annotation_A_sample", summary, [c["filter_csv"]])
    print(json.dumps(summary, ensure_ascii=False))


# ---------------------------------------------------------------- a-qc-candidates
def cmd_qc_candidates(args):
    c = ctx()
    real = all_real_items(c)
    if not real:
        sys.exit("先跑 a-sample：候選要避開正式題")
    if (c["qc"] / "A_candidates.jsonl").exists() and not args.force:
        sys.exit("annotations/qc/A_candidates.jsonl 已經存在；要重挑請加 --force")
    if args.force and (c["qc"] / "A_confirmed.jsonl").exists():
        sys.exit("Eden 已經確認過答案（annotations/qc/A_confirmed.jsonl）；不能重挑候選")
    cand = candidates(c)
    real_crops = {r["crop"] for r in real}
    seen_hash = [dhash_of(c, r["crop"]) for r in real]
    thr = float(c["cfg"]["photo_filter"]["photo_threshold"])
    picked = []
    for pool_split, kind, n, plan in PLAN:
        rng = random.Random(f"{c['seed']}:A:qc:{pool_split}:{kind}")
        base = [d for d in cand if d["split"] == pool_split and d["crop"] not in real_crops]
        pool = {"confident_photo": [d for d in base if d["photo_score"] >= 0.9],
                "confident_nonphoto": [d for d in base if d["photo_score"] <= 0.02],
                "near_threshold": [d for d in base if 0.2 <= d["photo_score"] < 0.8]}[kind]
        pool = sorted(pool, key=lambda d: d["crop"])
        rng.shuffle(pool)
        if kind == "confident_nonphoto":  # spread over the non-photo prompts (charts, logos, scans, ...)
            by = collections.defaultdict(list)
            for d in pool:
                by[d["top_prompt"]].append(d)
            pool = [d for group in zip(*by.values()) for d in group] + pool
        got = []
        for d in pool:
            if len(got) == n:
                break
            if d["crop"] in {g["crop"] for g in got} or d["crop"] in {p["crop"] for p in picked}:
                continue
            h = dhash_of(c, d["crop"])
            if near_duplicate(h, seen_hash):  # the same picture elsewhere (e.g. reused the next year)
                continue
            seen_hash.append(h)
            got.append(d)
        if len(got) < n:
            sys.exit(f"{pool_split}/{kind} 只找到 {len(got)} 張候選（要 {n}）")
        roles = [role for role, k in plan.items() for _ in range(k)]
        roles += ["spare"] * (len(got) - len(roles))
        for d, role in zip(got, roles):
            confident = kind != "near_threshold"
            ans = ("Photo" if d["photo_score"] >= thr else "Not a photo") if confident else None
            nonphoto = PROMPT_REASON[max(PROMPT_REASON, key=lambda q: float(d.get(f"p[{q}]") or 0))]
            picked.append({"cand": cand_id(c, d["crop"]), "crop": d["crop"], "report": d["report"], "page": int(d["page"]),
                           "pool": pool_split, "kind": kind, "planned_role": role,
                           "proposed_answer": ans, "nonphoto_reason": nonphoto})
    write_jsonl(c["qc"] / "A_candidates.jsonl", picked)
    page = write_confirm_page(c, picked)
    summary = {"candidates": len(picked),
               "by_pool_kind": dict(collections.Counter(f"{p['pool']}/{p['kind']}" for p in picked)),
               "by_planned_role": dict(collections.Counter(f"{p['pool']}/{p['planned_role']}" for p in picked)),
               "page": str(page)}
    write_result(c, "annotation_A_qc_candidates", summary, [c["filter_csv"]])
    print(json.dumps(summary, ensure_ascii=False))


CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#666;--line:#ddd;--card:#f5f5f5;--ok:#137333;--bad:#b3261e;--acc:#1a56c4}
@media (prefers-color-scheme: dark){:root{--bg:#161616;--fg:#eee;--mut:#aaa;--line:#333;--card:#222;--ok:#6dd58c;--bad:#ff8a80;--acc:#8ab4f8}}
*{box-sizing:border-box} body{background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,"Noto Sans TC","Microsoft JhengHei",sans-serif;margin:0 auto;max-width:1200px;padding:16px}
.mut{color:var(--mut)} .ok{color:var(--ok);font-weight:600} .bad{color:var(--bad);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.card{background:var(--card);border-radius:10px;padding:10px;border:2px solid transparent} .card.err{border-color:var(--bad)}
.card img{width:100%;height:220px;object-fit:contain;background:#0001;border-radius:6px}
.card select,.card label{font-size:14px} .card select{width:100%;margin:4px 0}
button{font:inherit;padding:8px 16px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
button.big{font-size:20px;padding:14px 28px;margin:6px} .bar{position:sticky;top:0;background:var(--bg);padding:8px 0;border-bottom:1px solid var(--line);z-index:2}
#stage img{max-width:100%;max-height:65vh;display:block;margin:12px auto;border-radius:8px}
"""


def write_confirm_page(c, picked):
    """Eden's page (decision 15): all Chinese, one question per image, three choices (照片／不是照片／不要用).
    No model scores, no reason or role menus: the program assigns roles and reasons. Pre-filled answers are
    marked for confirmation; the near-threshold ones must be chosen. Kept in localStorage; one column."""
    cards = []
    for p in picked:
        src = image_data_uri(c["crop_dir"] / p["crop"], TASK_LONG_SIDE, 80)
        pre = p["proposed_answer"]
        tag = '<span class="pre">預填，請確認</span>' if pre else '<span class="ask">請你選</span>'
        radios = "".join(f'<label class="opt"><input type="radio" name="{p["cand"]}" value="{v}"{" checked" if v == pre else ""}> {t}</label>'
                         for v, t in (("Photo", "照片"), ("Not a photo", "不是照片"), ("drop", "不要用")))
        cards.append(f'<div class="cc" data-cand="{p["cand"]}"><p class="mut">第 {len(cards) + 1} 張　{tag}</p>'
                     f'<img src="{src}" alt=""><p class="opts">{radios}</p></div>')
    page = c["pages"] / "eden" / "A_confirm_answers.html"  # never in students/: it shows the check items' answers
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>確認 132 張圖的答案</title><style>{CSS}
body{{max-width:680px}} .cc{{background:var(--card);border-radius:12px;padding:12px;margin:14px 0;border:2px solid transparent}}
.cc.todo{{border-color:var(--bad)}} .cc img{{width:100%;max-height:70vh;object-fit:contain;display:block;border-radius:8px;background:#0001}}
.opts{{display:flex;gap:8px}} .opt{{flex:1;white-space:nowrap;font-size:17px;padding:12px 4px;border:1px solid var(--line);border-radius:10px;text-align:center;cursor:pointer}}
.opt:has(input:checked){{border-color:var(--acc);outline:2px solid var(--acc)}}
.pre{{background:#fff3c4;color:#5b4500;border-radius:6px;padding:1px 8px}} .ask{{background:#ffd9d6;color:#7a1410;border-radius:6px;padding:1px 8px}}
</style></head><body>
<h1>確認 {len(picked)} 張圖的答案</h1>
<p>這 {len(picked)} 張圖要拿來做學生的例子、練習題和檢查題，需要你確認正確答案。</p>
<p>每張看一眼：是相機拍的真實照片就選「照片」，圖表、插畫、logo、文件掃描、設計圖選「不是照片」，看不準選「不要用」。</p>
<p>全部選完按「下載答案」，把檔案路徑告訴我。</p>
<div class="bar"><span id="count"></span> <button onclick="dl()">下載答案</button> <span id="msg" class="bad"></span></div>
{''.join(cards)}
<p><button class="big" onclick="dl()">下載答案</button></p>
<p class="mut">你的選擇會自動存在這個瀏覽器裡，關掉再開還在。這頁有學生檢查題的答案，不要給學生看。</p>
<script>
const KEY='A_confirm_answers_v3';
function collect(){{return [...document.querySelectorAll('.cc')].map(cc=>{{const a=cc.querySelector('input:checked'); return {{cand:cc.dataset.cand, choice:a?a.value:null}};}});}}
function upd(){{
  const a=collect(); try{{localStorage.setItem(KEY, JSON.stringify(a));}}catch(err){{}}
  document.querySelectorAll('.cc').forEach(cc=>cc.classList.toggle('todo', !cc.querySelector('input:checked')));
  const left=a.filter(x=>!x.choice).length;
  document.getElementById('count').textContent=left?('還有 '+left+' 張沒選'):'全部選好了';
}}
try{{JSON.parse(localStorage.getItem(KEY)||'[]').forEach(s=>{{const cc=document.querySelector('.cc[data-cand="'+s.cand+'"]'); if(!cc||!s.choice) return;
  cc.querySelectorAll('input').forEach(r=>r.checked=(r.value===s.choice));}});}}catch(err){{}}
document.addEventListener('change',upd); upd();
function dl(){{
  const a=collect(), left=a.filter(x=>!x.choice);
  if(left.length){{document.getElementById('msg').textContent='還有 '+left.length+' 張沒選（紅框）'; document.querySelector('.cc.todo').scrollIntoView(); return;}}
  document.getElementById('msg').textContent='';
  const blob=new Blob([JSON.stringify({{confirmed_by:'Eden', page:KEY, answers:a}},null,1)],{{type:'application/json'}});
  const l=document.createElement('a'); l.href=URL.createObjectURL(blob); l.download='A_confirmed_answers.json'; l.click();
}}
</script></body></html>""", encoding="utf-8")
    return page


# ---------------------------------------------------------------- a-qc-import
def cmd_qc_import(args):
    """Eden's answers (照片／不是照片／不要用) → roles and reasons by the program (decision 15): every candidate keeps its
    planned role unless Eden dropped it; a dropped one is replaced by a spare of the same pool (the same kind first);
    the reason is "photo_real" for a photo, else the default non-photo category of that image."""
    c = ctx()
    cands = {p["cand"]: p for p in read_jsonl(c["qc"] / "A_candidates.jsonl")}
    got = json.loads(Path(args.file).read_text(encoding="utf-8"))
    answers = got["answers"] if isinstance(got, dict) else got
    ids = [a.get("cand") for a in answers]
    if sorted(ids) != sorted(cands) or len(set(ids)) != len(ids):
        sys.exit(f"答案檔要對每一張候選剛好一筆：檔案 {len(ids)} 筆（不重複 {len(set(ids))}），候選 {len(cands)} 張")
    bad = [a["cand"] for a in answers if a.get("choice") not in ("Photo", "Not a photo", "drop")]
    if bad:
        sys.exit(f"{len(bad)} 張沒有選，或選項不對：{bad[:10]}")
    rows = []
    for a in answers:
        p = cands[a["cand"]]
        drop = a["choice"] == "drop"
        ans = None if drop else a["choice"]
        rows.append({**{k: p[k] for k in ("cand", "crop", "report", "page", "pool", "kind", "planned_role")},
                     "answer": ans, "reason": None if drop else ("photo_real" if ans == "Photo" else p["nonphoto_reason"]),
                     "drop": drop, "changed_from_proposal": bool(p["proposed_answer"]) and ans != p["proposed_answer"],
                     "role": "drop" if drop else p["planned_role"]})
    for r in rows:  # replace dropped items: a spare of the same pool, the same kind first
        if not r["drop"] or r["planned_role"] == "spare":
            continue
        spares = sorted((x for x in rows if x["role"] == "spare" and x["pool"] == r["pool"]),
                        key=lambda x: (x["kind"] != r["kind"], x["cand"]))
        if spares:
            spares[0]["role"] = r["planned_role"]
    n = collections.Counter((r["pool"], r["role"]) for r in rows)
    short = []
    if not NEED["example"][0] <= n[("dev", "example")] <= NEED["example"][1]:
        short.append(f"例子 {n[('dev', 'example')]} 張（要 {NEED['example'][0]}–{NEED['example'][1]}）")
    if n[("dev", "practice")] < NEED["practice"]:
        short.append(f"練習題 {n[('dev', 'practice')]} 張（要 {NEED['practice']}）")
    if n[("dev", "check")] < NEED["check_dev"]:
        short.append(f"開發批檢查題 {n[('dev', 'check')]} 張（要 {NEED['check_dev']}）")
    if n[("test", "check")] < NEED["check_test"]:
        short.append(f"測試批檢查題 {n[('test', 'check')]} 張（要 {NEED['check_test']}）")
    if short:
        sys.exit("「不要用」太多，備用的圖補不夠：" + "；".join(short) + "。請 Claude 加挑候選（a-qc-candidates 加量）")
    write_jsonl(c["qc"] / "A_confirmed.jsonl", rows)
    summary = {"answers": len(rows), "dropped": sum(r["drop"] for r in rows),
               "changed_from_proposal": sum(r["changed_from_proposal"] for r in rows),
               "roles": {f"{k[0]}/{k[1]}": v for k, v in sorted(n.items())},
               "answer_by_role": {role: dict(collections.Counter(r["answer"] for r in rows if r["role"] == role))
                                  for role in ("check", "example", "practice")},
               "reasons": dict(collections.Counter(r["reason"] for r in rows if r["role"] in ("example", "practice")))}
    write_result(c, "annotation_A_qc_confirmed", summary, [args.file])
    print(json.dumps(summary, ensure_ascii=False))


def qc_items(c, role, draft=False, pool=None):
    """Confirmed items for a role; with draft=True and no confirmation yet, the proposals (dry runs only)."""
    conf = read_jsonl(c["qc"] / "A_confirmed.jsonl")
    if conf:
        return [r for r in conf if r["role"] == role and (pool is None or r["pool"] == pool)], False
    if not draft:
        sys.exit("還沒有 Eden 確認的答案（annotations/qc/A_confirmed.jsonl）：先請 Eden 看確認頁，再跑 a-qc-import")
    cands = read_jsonl(c["qc"] / "A_candidates.jsonl")
    out = []
    for p in cands:
        if p["planned_role"] == role and (pool is None or p["pool"] == pool):
            ans = p["proposed_answer"] or "Photo"  # dry run only: near-threshold items have no proposal
            out.append(dict(p, answer=ans, reason="photo_real" if ans == "Photo" else p["nonphoto_reason"]))
    return out, True


# ---------------------------------------------------------------- a-student-pages
def md_to_html(md):
    """Enough Markdown for the guidelines: headings, bullet and numbered lists, bold, paragraphs."""
    out, in_list = [], None
    for line in md.splitlines():
        s = line.strip()
        m_b, m_n = re.match(r"^[-*]\s+(.*)", s), re.match(r"^\d+\.\s+(.*)", s)
        kind = "ul" if m_b else ("ol" if m_n else None)
        if in_list and kind != in_list:
            out.append(f"</{in_list}>")
            in_list = None
        text = html.escape((m_b or m_n).group(1) if kind else s)
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        if kind:
            if not in_list:
                out.append(f"<{kind}>")
                in_list = kind
            out.append(f"<li>{text}</li>")
        elif s.startswith("#"):
            level = min(len(s) - len(s.lstrip("#")) + 1, 4)
            out.append(f"<h{level}>{html.escape(s.lstrip('#').strip())}</h{level}>")
        elif s:
            out.append(f"<p>{text}</p>")
    if in_list:
        out.append(f"</{in_list}>")
    return "\n".join(out)


def build_student_pages(c, draft_ok, out):
    """Guidelines, examples and the two practice sets into `out` (no check items there).
    Returns (examples, practice sets, draft)."""
    ex, draft1 = qc_items(c, "example", draft_ok, "dev")
    pr, draft2 = qc_items(c, "practice", draft_ok, "dev")
    draft = draft1 or draft2
    rng = random.Random(f"{c['seed']}:A:practice")
    ex = sorted(ex, key=lambda r: (r["answer"] != "Photo", r["reason"], r["cand"]))
    # two sets with the same mix: deal the near-threshold and the confident items alternately
    sets = [[], []]
    for kind in ("near_threshold", "confident_photo", "confident_nonphoto"):
        group = sorted((r for r in pr if r["kind"] == kind), key=lambda r: r["cand"])
        rng.shuffle(group)
        offset = int(len(sets[0]) > len(sets[1]))  # an odd group starts with the smaller set
        for i, r in enumerate(group):
            sets[(i + offset) % 2].append(r)
    for s in sets:
        rng.shuffle(s)
    out.mkdir(parents=True, exist_ok=True)
    banner = DRAFT_BANNER if draft else ""
    e = html.escape
    guide = md_to_html(c["guide"].read_text(encoding="utf-8"))
    (out / "A_guidelines.html").write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>標註 A 手冊</title><style>{CSS}</style></head><body>
{guide}<p class="mut">手冊 {GUIDELINE_VERSION}</p></body></html>""", encoding="utf-8")
    cards = "".join(f'<div class="card"><img src="{image_data_uri(c["crop_dir"] / r["crop"], TASK_LONG_SIDE)}" alt="">'
                    f'<p><b>{e(reason_text(r))}</b></p></div>' for r in ex)
    (out / "A_examples.html").write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>標註 A 例子</title><style>{CSS}</style></head><body>
<h1>標註 A：這是不是照片？例子</h1>{banner}
<p>這些是報告書裡真的圖，附正確答案和理由。先看<a href="A_guidelines.html">手冊</a>，再把這些例子全部看過。</p>
<div class="grid">{cards}</div></body></html>""", encoding="utf-8")
    for k, items in enumerate(sets, 1):
        data = [{"img": image_data_uri(c["crop_dir"] / r["crop"], TASK_LONG_SIDE), "answer": r["answer"],
                 "reason": reason_text(r), "id": r["cand"]} for r in items]
        (out / f"A_practice_{k}.html").write_text(practice_html(k, data, banner), encoding="utf-8")
    return ex, sets, draft


DRAFT_BANNER = '<p class="bad">草稿：答案還沒經老師確認，不要給學生用。</p>'


def reason_text(r):
    return f"{ANSWER_ZH[r['answer']]}：{REASONS[r['reason']][1]}"


def cmd_student_pages(args):
    c = ctx()
    out = c["pages"] / "students"
    ex, sets, draft = build_student_pages(c, args.draft, out)
    (out / "index.html").write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>標註 A</title><style>{CSS}</style></head><body>
<h1>標註 A：這是不是照片？</h1>{DRAFT_BANNER if draft else ''}<ol><li><a href="A_guidelines.html">手冊</a></li><li><a href="A_examples.html">例子</a></li>
<li><a href="A_practice_1.html">練習第 1 組</a></li><li><a href="A_practice_2.html">練習第 2 組</a>（第 1 組沒過才做）</li></ol>
</body></html>""", encoding="utf-8")
    c["work"].mkdir(parents=True, exist_ok=True)  # for the Label Studio backup route
    (c["work"] / "A_instructions_for_labelstudio.html").write_text(
        md_to_html(c["guide"].read_text(encoding="utf-8"))
        + "\n<p><b>不要按跳過（Skip）。</b>拿不定主意時，選你覺得比較對的答案，另外勾「不確定」。</p>\n", encoding="utf-8")
    if not draft:  # the record of what the annotators saw; only with Eden's confirmed answers
        write_jsonl(c["qc"] / "A_examples.jsonl", [{"cand": r["cand"], "crop": r["crop"], "answer": r["answer"], "reason": r["reason"]} for r in ex])
        write_jsonl(c["qc"] / "A_practice.jsonl", [{"set": k, "cand": r["cand"], "crop": r["crop"], "kind": r["kind"], "answer": r["answer"],
                                                    "reason": r["reason"]} for k, items in enumerate(sets, 1) for r in items])
    print(f"examples {len(ex)}, practice sets {[len(s) for s in sets]} "
          f"(near-threshold {[sum(r['kind'] == 'near_threshold' for r in s) for s in sets]})"
          f"{'（草稿：答案還沒確認）' if draft else ''} → {out}")


def practice_html(k, data, banner):
    need = 18 if len(data) >= 20 else max(1, round(0.9 * len(data)))
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>標註 A 練習第 {k} 組</title><style>{CSS}</style></head><body>
<h1>標註 A 練習第 {k} 組</h1>{banner}
<p>用按鈕或按鍵作答：<b>1</b>＝照片、<b>2</b>＝不是照片。每答一題就會看到正確答案和理由，按 <b>Enter</b> 或「下一題」繼續。{len(data)} 題要答對 <b>{need} 題</b>，才能開始正式題。</p>
<div id="stage"></div>
<script>
const DATA={json.dumps(data, ensure_ascii=False)};
const LABEL={{'Photo':'照片','Not a photo':'不是照片'}};
const NEED={need}; let i=0, right=0, answered=false; const log=[];
function show(){{
  const s=document.getElementById('stage');
  if(i>=DATA.length){{
    const ok=right>=NEED;
    s.innerHTML='<h2>成績：'+right+' / '+DATA.length+'</h2><p class="'+(ok?'ok':'bad')+'">'+(ok?'通過了。告訴黃老師，然後開始第 1 批。':'還沒通過。再看一次例子，然後做另一組練習。')+'</p><button onclick="dl()">存下我的成績</button>';
    return;
  }}
  answered=false;
  s.innerHTML='<p class="mut">第 '+(i+1)+' 題，共 '+DATA.length+' 題</p><img src="'+DATA[i].img+'" alt="">'+
    '<p><button class="big" onclick="ans(\\'Photo\\')">1　照片</button><button class="big" onclick="ans(\\'Not a photo\\')">2　不是照片</button></p><div id="fb"></div>';
}}
function ans(a){{
  if(answered) return; answered=true;
  const d=DATA[i], ok=a===d.answer; if(ok) right++;
  log.push({{id:d.id, answer:a, correct:ok}});
  document.getElementById('fb').innerHTML='<p class="'+(ok?'ok':'bad')+'">'+(ok?'答對了。':'不對，答案是：'+LABEL[d.answer]+'。')+'</p><p>'+d.reason+'</p><button onclick="next()">下一題（Enter）</button>';
}}
function next(){{ if(answered){{ i++; show(); }} }}
document.addEventListener('keydown',ev=>{{
  if(ev.key==='1') ans('Photo'); else if(ev.key==='2') ans('Not a photo'); else if(ev.key==='Enter') next();
}});
function dl(){{
  let code=(prompt('輸入黃老師給你的代號（a1 或 a2），不要輸入名字：')||'').trim().toLowerCase();
  if(!/^a[12]$/.test(code)) code='unknown';
  const blob=new Blob([JSON.stringify({{set:{k}, annotator:code, score:right, of:DATA.length, need:NEED, answers:log}},null,1)],{{type:'application/json'}});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='A_practice_{k}_'+code+'.json'; a.click();
}}
show();
</script></body></html>"""


# ---------------------------------------------------------------- the batch plan (shared by the HTML pages and Label Studio)
PAGE_VERSION = "A-html-1"    # the batch HTML page; recorded in every answer file
TASK_LONG_SIDE = 600         # images in the batch pages, examples and practice (work order 10/18 21:40)
BLIND_KEYS = {"image", "item", "batch"}


def plan_files(c, draft):
    if draft:
        d = c["work"] / "draft"
        return {"plan": d / "plan.json", "key": d / "checks_key.jsonl", "pages": d / "pages", "tasks": d / "tasks_ls"}
    return {"plan": c["qc"] / "A_task_plan.json", "key": c["qc"] / "A_checks_key.jsonl",
            "pages": c["work"] / "pages", "tasks": c["work"] / "tasks"}


def page_id(entry):
    s = json.dumps({k: entry[k] for k in ("annotator", "batch", "version", "items")}, sort_keys=True)
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def interleave(real_ids, check_ids, rng):
    """One check in each block of about ten real items, at a random place inside the block."""
    n = len(check_ids)
    blocks = [real_ids[i * len(real_ids) // n:(i + 1) * len(real_ids) // n] for i in range(n)]
    out = []
    for blk, cid in zip(blocks, check_ids):
        blk = list(blk)
        blk.insert(rng.randint(0, len(blk)), cid)
        out += blk
    return out


def build_plan(c, draft):
    """Both annotators' batches: the real items in each annotator's own shuffled order (development items first),
    five checks per batch from the matching pool. Version 1 of every batch."""
    chk_dev, is_draft = qc_items(c, "check", draft, "dev")
    chk_test, _ = qc_items(c, "check", draft, "test")
    real = all_real_items(c)
    if not real:
        sys.exit("先跑 a-sample")
    dev = sorted((r for r in real if r["split"] == "dev"), key=lambda r: r["item"])
    test = sorted((r for r in real if r["split"] == "test"), key=lambda r: r["item"])
    checks = {item_id(c, r["crop"]): dict(r, pool=r.get("pool", "dev")) for r in chk_dev + chk_test}
    plan, key = [], []
    for ann in ANNOTATORS:
        rng = random.Random(f"{c['seed']}:A:order:{ann}")
        d, t = dev[:], test[:]
        rng.shuffle(d)
        rng.shuffle(t)
        pools = {}
        for p in ("dev", "test"):
            pools[p] = sorted(i for i, r in checks.items() if r["pool"] == p)
            rng.shuffle(pools[p])
        # the development items first (their labels set the threshold, 10/22); a batch never mixes the two
        batches = [("dev", d[i:i + BATCH_REAL]) for i in range(0, len(d), BATCH_REAL)] + \
                  [("test", t[i:i + BATCH_REAL]) for i in range(0, len(t), BATCH_REAL)]
        for b, (split, items) in enumerate(batches, 1):
            n_chk = min(CHECKS_PER_BATCH, max(1, round(len(items) / 10)))
            if len(pools[split]) < n_chk:
                sys.exit(f"{ann} 第 {b} 批的檢查題不夠（{split} 只剩 {len(pools[split])} 張）")
            chk = [pools[split].pop() for _ in range(n_chk)]
            entry = {"annotator": ann, "batch": b, "split": split, "version": 1,
                     "items": interleave([r["item"] for r in items], chk, rng), "real": len(items), "checks": n_chk}
            entry["page_id"] = page_id(entry)
            plan.append(entry)
            key += [{"annotator": ann, "batch": b, "item": i, "crop": checks[i]["crop"], "answer": checks[i]["answer"],
                     "pool": checks[i]["pool"]} for i in chk]
    crops = {r["item"]: r["crop"] for r in real}
    crops.update({i: r["crop"] for i, r in checks.items()})
    return plan, key, crops, is_draft


def load_or_build_plan(c, draft, force=False):
    f = plan_files(c, draft)
    if f["plan"].exists() and not force:
        plan = json.loads(f["plan"].read_text(encoding="utf-8"))
        key = read_jsonl(f["key"])
        real = all_real_items(c)
        crops = {r["item"]: r["crop"] for r in real}
        crops.update({k["item"]: k["crop"] for k in key})
        return plan, key, crops, draft
    if not draft and force and any(read_jsonl(c["ann"] / s / "A.jsonl") for s in ("dev", "test")):
        sys.exit("已經有標註結果（annotations/*/A.jsonl），不能重產題目表")
    plan, key, crops, is_draft = build_plan(c, draft)
    if is_draft != draft:
        sys.exit("Eden 已經確認過答案（annotations/qc/A_confirmed.jsonl）：不用 --draft，直接產正式版")
    f["plan"].parent.mkdir(parents=True, exist_ok=True)
    f["plan"].write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    write_jsonl(f["key"], key)
    return plan, key, crops, draft


def current(plan):
    """The newest version of every (annotator, batch)."""
    cur = {}
    for e in plan:
        k = (e["annotator"], e["batch"])
        if k not in cur or e["version"] > cur[k]["version"]:
            cur[k] = e
    return cur


def page_name(e):
    return f"A_{e['annotator']}_batch{e['batch']:02d}" + (f"_v{e['version']}" if e["version"] > 1 else "") + ".html"


def assert_blind(tasks):
    """The task data may hold only the image, the opaque id and the batch number (README 原則)."""
    for t in tasks:
        d = t["data"]
        if set(d) - BLIND_KEYS or not re.fullmatch(r"A[0-9a-f]{8}", str(d["item"])) \
                or not str(d["image"]).startswith("data:image/jpeg;base64,") or not isinstance(d["batch"], int):
            raise SystemExit(f"題目資料有不該有的欄位或格式：{sorted(d)} {d.get('item')}")


BATCH_CSS = """
#top{display:flex;gap:16px;align-items:baseline;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:6px}
#stage img{max-width:100%;max-height:62vh;display:block;margin:12px auto;border-radius:8px}
button.sel{outline:3px solid var(--acc);background:var(--acc);color:var(--bg)} button:disabled{opacity:.45;cursor:default}
.ns{font-size:18px;margin-left:12px} .row{text-align:center}
"""


def batch_html(e, items, draft):
    meta = {"annotator": e["annotator"], "batch": e["batch"], "version": e["version"], "page_id": e["page_id"],
            "page_version": PAGE_VERSION, "n": len(items)}
    banner = DRAFT_BANNER if draft else ""
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>標註 A – {e['annotator']} 第 {e['batch']} 批</title>
<style>{CSS}{BATCH_CSS}</style></head><body>
<div id="top"><b>標註 A：這是不是照片？</b><span class="mut">標註者 {e['annotator']}・第 {e['batch']} 批{'' if e['version'] == 1 else '（重做版 ' + str(e['version']) + '）'}</span><span id="prog" class="mut"></span></div>
{banner}<p id="phone" class="bad" hidden>請用筆電或桌機的瀏覽器開這個檔案，不要用手機。</p>
<div id="stage"></div>
<p class="mut">按鍵：1＝照片、2＝不是照片、3＝不確定（另外勾，還是要選 1 或 2）；Enter 或 →＝下一題，←＝上一題。進度存在這個瀏覽器裡：關掉再開（同一台電腦、同一個瀏覽器）可以接著做。</p>
<script>
const META={json.dumps(meta)};
const ITEMS={json.dumps(items)};
const KEY='taskA-'+META.page_id;
let S={{idx:0, seq:0, ans:{{}}, sec:{{}}, downloads:0}};
try{{const s=JSON.parse(localStorage.getItem(KEY)||'null'); if(s&&s.page_id===META.page_id&&s.state) S=s.state;}}catch(err){{}}
function save(){{try{{localStorage.setItem(KEY, JSON.stringify({{page_id:META.page_id, state:S}}));}}catch(err){{}}}}
let t0=null;
function startClock(){{ if(S.idx<ITEMS.length && !document.hidden) t0=performance.now(); }}
function stopClock(){{ if(t0!==null && S.idx<ITEMS.length){{ const id=ITEMS[S.idx].item; S.sec[id]=(S.sec[id]||0)+(performance.now()-t0)/1000; }} t0=null; save(); }}
document.addEventListener('visibilitychange',()=>{{ if(document.hidden) stopClock(); else startClock(); }});
window.addEventListener('pagehide', stopClock);
function rec(id){{ return S.ans[id]||(S.ans[id]={{answer:null, not_sure:false, seq:null, changes:0}}); }}
function answered(){{ return ITEMS.filter(it=>S.ans[it.item]&&S.ans[it.item].answer).length; }}
function render(){{
  document.getElementById('prog').textContent='已答 '+answered()+' / '+ITEMS.length;
  if(S.idx>=ITEMS.length){{ finish(); return; }}
  const it=ITEMS[S.idx], a=S.ans[it.item]||{{}};
  document.getElementById('stage').innerHTML='<p class="mut">第 '+(S.idx+1)+' 題，共 '+ITEMS.length+' 題</p><img src="'+it.img+'" alt="">'+
    '<p class="row"><button class="big'+(a.answer==='Photo'?' sel':'')+'" onclick="answer(\\'Photo\\')">1　照片</button>'+
    '<button class="big'+(a.answer==='Not a photo'?' sel':'')+'" onclick="answer(\\'Not a photo\\')">2　不是照片</button>'+
    '<label class="ns"><input type="checkbox" id="ns"'+(a.not_sure?' checked':'')+' onchange="notSure(this.checked)"> 3　不確定</label></p>'+
    '<p class="row"><button onclick="back()"'+(S.idx===0?' disabled':'')+'>&larr; 上一題</button> '+
    '<button onclick="next()"'+(a.answer?'':' disabled')+'>下一題 &rarr;（Enter）</button></p><p id="msg" class="bad row"></p>';
  startClock();
}}
function answer(x){{ const a=rec(ITEMS[S.idx].item); if(a.answer===null) a.seq=++S.seq; else if(a.answer!==x) a.changes++; a.answer=x; stopClock(); render(); }}
function notSure(v){{ rec(ITEMS[S.idx].item).not_sure=v; save(); }}
function next(){{ if(S.idx>=ITEMS.length) return; const a=S.ans[ITEMS[S.idx].item];
  if(!(a&&a.answer)){{ document.getElementById('msg').textContent='先選 1（照片）或 2（不是照片）。'; return; }}
  stopClock(); S.idx++; save(); render(); }}
function back(){{ if(S.idx>0){{ stopClock(); S.idx--; save(); render(); }} }}
function finish(){{
  const n=ITEMS.length, done=answered();
  document.getElementById('stage').innerHTML='<h2>第 '+META.batch+' 批：已答 '+done+' / '+n+'</h2>'+
    (done<n?'<p class="bad">還有題目沒答，按「上一題」回去補。</p>':'')+
    '<p class="row"><button class="big" onclick="dl()">下載答案</button></p>'+
    '<p>下載後，把檔案（檔名開頭是 <b>A_'+META.annotator+'_batch'+pad(META.batch)+'_</b>）用 email 寄給黃老師，不要改檔名或內容。'+
    '可以接著做下一批；如果老師請你重做這一批，會寄新的檔案給你。</p>'+
    '<p class="row"><button onclick="back()">&larr; 上一題</button></p>';
}}
function pad(x){{ return String(x).padStart(2,'0'); }}
function dl(){{
  const d=new Date(), stamp=d.getFullYear()+pad(d.getMonth()+1)+pad(d.getDate())+'_'+pad(d.getHours())+pad(d.getMinutes())+pad(d.getSeconds());
  const answers=ITEMS.map((it,i)=>{{ const a=S.ans[it.item]||{{}};
    return {{item:it.item, position:i+1, answer:a.answer||null, not_sure:!!a.not_sure, seconds:Math.round((S.sec[it.item]||0)*10)/10,
             answered_seq:a.seq||null, changes:a.changes||0}}; }});
  S.downloads++; save();
  const out={{page_version:META.page_version, annotator:META.annotator, batch:META.batch, version:META.version, page_id:META.page_id,
             downloaded_at:d.toISOString(), download_no:S.downloads, n_items:ITEMS.length, answers:answers}};
  const blob=new Blob([JSON.stringify(out,null,1)],{{type:'application/json'}});
  const l=document.createElement('a'); l.href=URL.createObjectURL(blob); l.download='A_'+META.annotator+'_batch'+pad(META.batch)+'_'+stamp+'.json'; l.click();
}}
document.addEventListener('keydown',ev=>{{
  if(ev.target.tagName==='INPUT' && ev.key===' ') return;
  if(S.idx>=ITEMS.length){{ if(ev.key==='ArrowLeft'||ev.key==='Backspace'){{ ev.preventDefault(); back(); }} return; }}
  if(ev.key==='1') answer('Photo');
  else if(ev.key==='2') answer('Not a photo');
  else if(ev.key==='3'){{ const box=document.getElementById('ns'); box.checked=!box.checked; notSure(box.checked); }}
  else if(ev.key==='Enter'||ev.key==='ArrowRight'){{ ev.preventDefault(); next(); }}
  else if(ev.key==='ArrowLeft'||ev.key==='Backspace'){{ ev.preventDefault(); back(); }}
}});
try{{ if(window.matchMedia('(pointer:coarse)').matches && Math.min(screen.width, screen.height)<700) document.getElementById('phone').hidden=false; }}catch(err){{}}
render();
</script></body></html>"""


def write_batch_page(c, e, crops, out_dir, draft):
    items = [{"item": i, "img": image_data_uri(c["crop_dir"] / crops[i], TASK_LONG_SIDE)} for i in e["items"]]
    for it in items:  # nothing but the opaque id and the image (the batch number is in the page header)
        if set(it) != {"item", "img"} or not re.fullmatch(r"A[0-9a-f]{8}", it["item"]):
            raise SystemExit(f"題目資料有不該有的欄位：{sorted(it)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / page_name(e)
    path.write_text(batch_html(e, items, draft), encoding="utf-8")
    return path


def package_index(ann, batches, draft):
    rows = "".join(f'<li><a href="{p.name}">第 {e["batch"]} 批</a></li>' for e, p in batches)
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>標註 A – {ann}</title><style>{CSS}</style></head><body>
<h1>標註 A：這是不是照片？</h1>{DRAFT_BANNER if draft else ''}
<p>你的代號是 <b>{ann}</b>。請用筆電或桌機的瀏覽器開這些檔案，不要用手機。檔案要放在同一個資料夾裡，頁面之間有連結。</p>
<ol>
<li>看<a href="A_guidelines.html">手冊</a>。</li>
<li>看<a href="A_examples.html">例子</a>。</li>
<li>做<a href="A_practice_1.html">練習第 1 組</a>，20 題要答對 18 題。沒過就再看一次例子，做<a href="A_practice_2.html">練習第 2 組</a>。做完按「存下我的成績」，把檔案寄給黃老師。</li>
<li>照順序做下面 6 批，每批大約 5–10 分鐘。每批做完按「下載答案」，馬上把檔案 email 給黃老師。
<ol>{rows}</ol></li>
</ol>
<p class="mut">一張大約 5 秒。不要跟另一位標註者討論題目。有問題問黃老師。</p>
</body></html>"""


def cmd_pages(args):
    """Every annotator's batch pages (self-contained HTML) and one zip per annotator (work order 10/18 21:40)."""
    import zipfile
    c = ctx()
    plan, key, crops, draft = load_or_build_plan(c, args.draft, args.force)
    f = plan_files(c, draft)
    cur = current(plan)
    anns = ("a1",) if draft and not args.both else ANNOTATORS
    made = []
    for ann in anns:
        stage = c["work"] / ("draft" if draft else "") / f"package_{ann}"
        if stage.exists():
            for old in stage.iterdir():
                old.unlink()
        stage.mkdir(parents=True, exist_ok=True)
        build_student_pages(c, draft, stage)
        batches = []
        for b in sorted(b for (a, b) in cur if a == ann):
            e = cur[(ann, b)]
            p = write_batch_page(c, e, crops, stage, draft)
            batches.append((e, p))
            made.append({"annotator": ann, "batch": b, "version": e["version"], "page": p.name, "page_id": e["page_id"],
                         "sha256": sha256_file(p), "bytes": p.stat().st_size, "items": len(e["items"]), "checks": e["checks"]})
        (stage / "index.html").write_text(package_index(ann, batches, draft), encoding="utf-8")
        z = c["work"] / f"package_{ann}{'_draft' if draft else ''}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(stage.iterdir()):
                zf.write(p, f"task_A_{ann}/{p.name}")
        print(f"{ann}: {len(batches)} batches → {z} ({z.stat().st_size / 1e6:.1f} MB)")
    (f["pages"]).mkdir(parents=True, exist_ok=True)
    (f["pages"] / "pages_made.json").write_text(json.dumps(made, ensure_ascii=False, indent=1), encoding="utf-8")
    if not draft:
        write_result(c, "annotation_A_pages", {"page_version": PAGE_VERSION, "pages": made}, [f["plan"]])
    if draft:
        print("草稿：檢查題的答案還沒經 Eden 確認，只給 Eden 試開，不要給學生。")


def cmd_redo(args):
    """A new version of one batch for one annotator: the same items, a new order (after a batch fails the check)."""
    c = ctx()
    f = plan_files(c, False)
    if not f["plan"].exists():
        sys.exit("沒有題目表：先跑 a-pages")
    plan = json.loads(f["plan"].read_text(encoding="utf-8"))
    key = read_jsonl(f["key"])
    old = current(plan).get((args.annotator, args.batch))
    if not old:
        sys.exit(f"{args.annotator} 沒有第 {args.batch} 批")
    checks = [k["item"] for k in key if k["annotator"] == args.annotator and k["batch"] == args.batch]
    real = [i for i in old["items"] if i not in checks]
    v = old["version"] + 1
    rng = random.Random(f"{c['seed']}:A:redo:{args.annotator}:{args.batch}:{v}")
    rng.shuffle(real)
    chk = checks[:]
    rng.shuffle(chk)
    new = dict(old, version=v, items=interleave(real, chk, rng))
    new["page_id"] = page_id(new)
    plan.append(new)
    f["plan"].write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    crops = {r["item"]: r["crop"] for r in all_real_items(c)}
    crops.update({k["item"]: k["crop"] for k in key})
    p = write_batch_page(c, new, crops, c["work"] / "redo", False)
    write_result(c, "annotation_A_redo", {"annotator": args.annotator, "batch": args.batch, "version": v,
                                          "page": p.name, "page_id": new["page_id"], "sha256": sha256_file(p)}, [f["plan"]])
    print(f"新版本 {v}：{p}（寄給 {args.annotator}；舊版本的答案檔之後不會再採用）")


def cmd_extra(args):
    """An extra development batch of low-score images (Eden 10/19: the VLM check said the photo filter drops many
    photos below the threshold, and the 100 development items hold only 10 such images). Simple random among the
    development images given to the photo model with a photo score below --below, leaving out every image already in
    task A (items, examples, practice, checks, spares); the same images for both annotators, each in their own order,
    with five checks from Eden's confirmed development spares (never shown to the students). The items join
    annotations/dev/A_items.jsonl in their score band (bin0 for --below 0.1), so photo_threshold.py weights them with
    the other items of that band. One new batch number for both annotators; pages in work/annotation/A/extra/."""
    c = ctx()
    f = plan_files(c, False)
    if not f["plan"].exists():
        sys.exit("沒有題目表：先跑 a-pages")
    plan = json.loads(f["plan"].read_text(encoding="utf-8"))
    key = read_jsonl(f["key"])
    b = max(e["batch"] for e in plan) + 1
    if args.batch and args.batch != b:
        sys.exit(f"下一批的編號是 {b}，不是 {args.batch}")
    real = all_real_items(c)
    used = {r["crop"] for r in real} | {r["crop"] for r in read_jsonl(c["qc"] / "A_candidates.jsonl")} | {k["crop"] for k in key}
    pool = sorted((d for d in candidates(c) if d["split"] == "dev" and d["photo_score"] < args.below and d["crop"] not in used),
                  key=lambda d: d["crop"])
    rng = random.Random(f"{c['seed']}:A:extra:{b}")
    pick = rng.sample(pool, min(args.n, len(pool)))
    conf = read_jsonl(c["qc"] / "A_confirmed.jsonl")
    shown = {r["crop"] for r in real} | {k["crop"] for k in key}   # spares are candidates themselves: only these are out
    spares = sorted((r for r in conf if r["role"] == "spare" and r["pool"] == "dev" and r["answer"] and r["crop"] not in shown),
                    key=lambda r: r["cand"])
    n_chk = min(CHECKS_PER_BATCH, max(1, round(len(pick) / 10)))
    if len(spares) < n_chk:
        sys.exit(f"開發集備用的已知答案圖只剩 {len(spares)} 張，不夠 {n_chk} 題檢查題")
    rng.shuffle(spares)
    chk = spares[:n_chk]
    rows = [{"item": item_id(c, d["crop"]), "crop": d["crop"], "report": d["report"], "page": int(d["page"]), "split": "dev",
             "stratum": f"bin{min(int(d['photo_score'] * N_BINS), N_BINS - 1)}", "extra_batch": b} for d in pick]
    write_jsonl(c["ann"] / "dev" / "A_items.jsonl", read_jsonl(c["ann"] / "dev" / "A_items.jsonl") + rows)
    crops = {r["item"]: r["crop"] for r in rows}
    made = []
    for ann in ANNOTATORS:
        r2 = random.Random(f"{c['seed']}:A:extra:{b}:order:{ann}")
        ids = [r["item"] for r in rows]
        r2.shuffle(ids)
        cids = [item_id(c, s["crop"]) for s in chk]
        r2.shuffle(cids)
        entry = {"annotator": ann, "batch": b, "split": "dev", "version": 1, "items": interleave(ids, cids, r2),
                 "real": len(ids), "checks": len(cids), "extra": f"photo score < {args.below}"}
        entry["page_id"] = page_id(entry)
        plan.append(entry)
        key += [{"annotator": ann, "batch": b, "item": item_id(c, s["crop"]), "crop": s["crop"], "answer": s["answer"],
                 "pool": "dev"} for s in chk]
        crops.update({item_id(c, s["crop"]): s["crop"] for s in chk})
        p = write_batch_page(c, entry, crops, c["work"] / "extra", False)
        made.append({"annotator": ann, "batch": b, "page": p.name, "page_id": entry["page_id"], "sha256": sha256_file(p),
                     "bytes": p.stat().st_size, "items": len(entry["items"])})
    f["plan"].write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    write_jsonl(f["key"], key)
    write_result(c, "annotation_A_extra", {"batch": b, "below": args.below, "pool": len(pool), "items": len(rows),
                                           "checks": n_chk, "check_answers": dict(collections.Counter(s["answer"] for s in chk)),
                                           "pages": made}, [c["filter_csv"], f["plan"]])
    for m in made:
        print(f"{m['annotator']}：{c['work'] / 'extra' / m['page']}（{m['items']} 題，{m['bytes'] / 1e6:.1f} MB）")


def cmd_tasks(args):
    """Label Studio import files (the backup route for task A; R and D use Label Studio)."""
    c = ctx()
    plan, key, crops, draft = load_or_build_plan(c, args.draft, args.force)
    f = plan_files(c, draft)
    f["tasks"].mkdir(parents=True, exist_ok=True)
    (ROOT / "configs" / "labelstudio").mkdir(parents=True, exist_ok=True)
    (ROOT / "configs" / "labelstudio" / "A.xml").write_text(LABEL_CONFIG, encoding="utf-8")
    uri_cache = {}
    for (ann, b), e in sorted(current(plan).items()):
        tasks = []
        for i in e["items"]:
            if i not in uri_cache:
                uri_cache[i] = image_data_uri(c["crop_dir"] / crops[i])
            tasks.append({"data": {"image": uri_cache[i], "item": i, "batch": b}})
        assert_blind(tasks)
        path = f["tasks"] / f"A_{ann}_batch{b:02d}.json"
        path.write_text(json.dumps(tasks), encoding="utf-8")
        print(f"{ann} batch {b}: {e['real']} {e['split']} + {e['checks']} check → {path}")


# ---------------------------------------------------------------- a-check
def parse_export(path):
    """Label Studio JSON export → per task: the latest completed annotation, skipped ones, duplicates."""
    tasks = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = []
    for t in tasks:
        done = [a for a in t.get("annotations", []) if not a.get("was_cancelled")]
        skipped = [a for a in t.get("annotations", []) if a.get("was_cancelled")]
        latest = max(done, key=lambda a: (a.get("updated_at") or a.get("created_at") or "", a.get("id") or 0)) if done else None
        ans, unsure = None, False
        for r in (latest or {}).get("result", []):
            ch = (r.get("value") or {}).get("choices") or []
            if r.get("from_name") == "answer" and ch:
                ans = ch[0]
            if r.get("from_name") == "not_sure" and "Not sure" in ch:
                unsure = True
        who = (latest or {}).get("completed_by")
        rows.append({"item": t["data"]["item"], "batch": t["data"].get("batch"), "answer": ans, "not_sure": unsure,
                     "seconds": (latest or {}).get("lead_time"), "order_key": (latest or {}).get("created_at") or "~",
                     "completed_by": who.get("id") if isinstance(who, dict) else who, "ls_task": t.get("id"),
                     "status": "done" if latest and ans else ("skipped" if skipped else "missing"),
                     "duplicates": max(0, len(done) - 1), "source": str(path)})
    return rows


def read_returns(paths, annotator, plan):
    """The answer files the annotator sent back (JSON from the batch pages). Every file must be one of this
    annotator's batch pages (page_id in the plan). Per batch the newest version counts; within it the newest file."""
    files = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            sys.exit(f"找不到 {p}（答案檔要放在這個資料夾，或直接給檔案路徑）")
        files += sorted(p.glob(f"A_{annotator}_batch*.json")) if p.is_dir() else [p]
    if not files:
        sys.exit(f"沒有找到 {annotator} 的答案檔（A_{annotator}_batchNN_….json）")
    by_pid = {e["page_id"]: e for e in plan}
    cur = current(plan)
    got, notes = {}, []
    for fp in files:
        try:
            d = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:
            sys.exit(f"{fp} 不是答案檔（讀不出 JSON）")
        if not (isinstance(d, dict) and str(d.get("page_version", "")).startswith("A-html") and isinstance(d.get("answers"), list)):
            sys.exit(f"{fp} 不是學生交回的答案檔（是不是選到題目檔或別的檔？）")
        e = by_pid.get(d.get("page_id"))
        if e is None or d.get("annotator") != annotator or e["annotator"] != annotator or e["batch"] != d.get("batch"):
            sys.exit(f"{fp} 不是 {annotator} 的題目頁交回來的（學生代號 {d.get('annotator')}、第 {d.get('batch')} 批）：拒收")
        if e is not cur[(annotator, e["batch"])]:
            notes.append(f"{Path(fp).name}：第 {e['batch']} 批的舊版本（{e['version']}），已經重產過，不採用")
            continue
        got.setdefault(e["batch"], []).append((d.get("downloaded_at") or "", fp, d, e))
    rows = []
    for b, lst in got.items():
        lst.sort()
        _, fp, d, e = lst[-1]
        if len(lst) > 1:
            notes.append(f"第 {b} 批有 {len(lst)} 個答案檔，採用最新的 {Path(fp).name}")
        seen = collections.Counter(a.get("item") for a in d["answers"])
        if set(seen) - set(e["items"]):
            sys.exit(f"{fp} 裡有不在這一批的題號：拒收")
        last = {a.get("item"): a for a in d["answers"]}  # an item listed twice (the page never does that): keep the last
        for a in last.values():
            ok = a.get("answer") in ANSWERS
            rows.append({"item": a["item"], "batch": b, "answer": a.get("answer") if ok else None,
                         "not_sure": bool(a.get("not_sure")), "seconds": a.get("seconds"),
                         "order_key": f"{a['answered_seq']:06d}" if isinstance(a.get("answered_seq"), int) else "~",
                         "completed_by": annotator, "ls_task": a.get("position"), "status": "done" if ok else "missing",
                         "duplicates": seen[a["item"]] - 1,
                         "source": Path(fp).name})
    return rows, notes


def longest_run(answers):
    best = cur = 0
    prev = object()
    for a in answers:
        cur = cur + 1 if a == prev else 1
        prev = a
        best = max(best, cur)
    return best


def cmd_check(args):
    c = ctx()
    f = plan_files(c, False)
    if not f["key"].exists() or not f["plan"].exists():
        sys.exit("沒有 annotations/qc/A_checks_key.jsonl 或 A_task_plan.json：先跑 a-pages（Eden 確認答案之後）")
    plan_all = json.loads(f["plan"].read_text(encoding="utf-8"))
    key = {(r["annotator"], r["item"]): r for r in read_jsonl(f["key"])}
    plan = {b: e for (a, b), e in current(plan_all).items() if a == args.annotator}
    items = {r["item"]: r for r in all_real_items(c)}
    notes = []
    if not args.export:  # the answer files sent back from the batch pages
        paths = args.returns or [c["work"] / "returns"]
        rows, notes = read_returns(paths, args.annotator, plan_all)
        source = [str(p) for p in paths]
    else:  # Label Studio export (the backup route)
        rows = parse_export(args.export)
        source = [str(args.export)]
        assigned = {iid: b for b, e in plan.items() for iid in e["items"]}
        foreign = [r["item"] for r in rows if assigned.get(r["item"]) != r["batch"]]
        if foreign:
            sys.exit(f"{len(foreign)} 題不在 {args.annotator} 的題目表裡（或批次不同）：這不是 {args.annotator} 的匯出檔？例如 {foreign[:3]}")
    done = [r for r in rows if r["status"] == "done"]
    if not done:
        sys.exit("沒有任何作答（是不是選到題目檔，而不是交回來的答案檔或匯出檔？）")
    people = {r["completed_by"] for r in done}
    if len(people) > 1:
        sys.exit(f"有 {len(people)} 個不同的標註者帳號（{sorted(map(str, people))}）：一個專案應該只有一位學生")
    old_n = sum(1 for s in ("dev", "test") for r in read_jsonl(c["ann"] / s / "A.jsonl") if r["annotator"] == args.annotator)
    new_n = sum(1 for r in done if r["item"] in items)
    if new_n < old_n and not args.force and not args.dry_run:
        sys.exit(f"這次只有 {new_n} 題正式題的答案，比已經存的 {old_n} 題少：是不是少了答案檔？確定要用請加 --force")
    by_batch = collections.defaultdict(list)
    for r in sorted(rows, key=lambda r: (r["order_key"], r["ls_task"] or 0)):
        by_batch[r["batch"]].append(r)
    present = sorted(b for b in by_batch if b is not None)
    check_batches = present if args.all_batches else ([args.batch] if args.batch else present[-1:])
    report, alerts = [], []
    for b in present:
        rs = by_batch[b]
        expected = plan[b]["items"]
        got_items = {r["item"] for r in rs}
        not_imported = [i for i in expected if i not in got_items]
        skipped = [r["item"] for r in rs if r["status"] == "skipped"]
        missing = [r["item"] for r in rs if r["status"] == "missing"] + not_imported
        chk_expected = [i for i in expected if (args.annotator, i) in key]
        status = {r["item"]: r for r in rs}
        wrong = [i for i in chk_expected if status.get(i, {}).get("answer") != key[(args.annotator, i)]["answer"]]
        answered = [r for r in rs if r["status"] == "done"]
        secs = [r["seconds"] for r in answered if r["seconds"] is not None]
        med = statistics.median(secs) if secs else None
        run = longest_run([r["answer"] for r in answered])
        flags = []
        if skipped:
            flags.append(f"{len(skipped)} 題被跳過（Skip）：請學生回去作答（跳過的檢查題算錯）")
        if missing:
            flags.append(f"{len(missing)} 題沒有答案：這一批還沒完成（沒答的檢查題算錯）")
        if chk_expected and len(wrong) / len(chk_expected) > LIMITS["check_error"]:
            flags.append(f"檢查題錯 {len(wrong)}/{len(chk_expected)}（超過 2 成，跳過和沒答的算錯）：這一批退回重標")
        if answered and not secs:
            flags.append("這一批都沒有記錄秒數：沒辦法檢查速度")
        if med is not None and med < LIMITS["median_seconds"]:
            flags.append(f"每題秒數中位數 {med:.2f} 秒（不到 1 秒）：先停，跟標註者確認")
        if run >= LIMITS["same_answer_run"]:
            flags.append(f"連續同一個答案 {run} 題（30 題以上）：先停，跟標註者確認")
        dups = sum(r["duplicates"] for r in rs)
        if dups:
            flags.append(f"{dups} 題在同一個檔裡出現兩次以上（檔案被改過？）：只用最後一個答案")
        report.append({"batch": b, "split": plan[b]["split"], "version": plan[b]["version"], "expected": len(expected),
                       "done": len(answered), "skipped": skipped, "missing": missing, "checks": len(chk_expected),
                       "checks_wrong": wrong, "median_seconds": med, "longest_same_answer": run,
                       "not_sure": sum(r["not_sure"] for r in answered), "answers": dict(collections.Counter(r["answer"] for r in answered)),
                       "duplicates": dups, "flags": flags, "checked_now": b in check_batches,
                       "sources": sorted({r["source"] for r in rs})})
        if b in check_batches:
            alerts += [f"batch {b}: {x}" for x in flags]
    for n in notes:
        print("注意：" + n)
    if args.dry_run:  # e.g. Eden's trial run: report only, write nothing
        for r in report:
            print(f"batch {r['batch']}: 完成 {r['done']}/{r['expected']}｜跳過 {len(r['skipped'])}｜沒答 {len(r['missing'])}｜"
                  f"檢查題錯 {len(r['checks_wrong'])}/{r['checks']}")
        print("（--dry-run：沒有寫任何檔）\n" + ("提醒：\n  " + "\n  ".join(alerts) if alerts else "沒有提醒。"))
        sys.exit(0)
    # labels of the real items (checks are kept apart, README: 不算進結果)
    for split in ("dev", "test"):
        path = c["ann"] / split / "A.jsonl"
        old = [r for r in read_jsonl(path) if r["annotator"] != args.annotator]
        new = [{"item": r["item"], "annotator": args.annotator, "answer": r["answer"], "not_sure": r["not_sure"],
                "seconds": r["seconds"], "batch": r["batch"], "version": plan[r["batch"]]["version"],
                "guidelines": GUIDELINE_VERSION, "source": r["source"]}
               for r in done if r["item"] in items and items[r["item"]]["split"] == split]
        write_jsonl(path, sorted(old + new, key=lambda r: (r["annotator"], r["item"])))
    write_jsonl(c["qc"] / f"A_check_answers_{args.annotator}.jsonl",
                [dict(r, correct=r["answer"] == key[(args.annotator, r["item"])]["answer"])
                 for r in rows if (args.annotator, r["item"]) in key])
    inputs = [p for s in source for p in ([s] if Path(s).is_file() else sorted(Path(s).glob(f"A_{args.annotator}_batch*.json")))]
    write_result(c, f"annotation_A_batch_check_{args.annotator}",
                 {"annotator": args.annotator, "sources": source, "notes": notes, "limits": LIMITS,
                  "checked_batches": check_batches, "batches": report, "alerts": alerts}, inputs)
    for r in report:
        mark = "（這次檢查）" if r["checked_now"] else ""
        print(f"batch {r['batch']}{mark}: 完成 {r['done']}/{r['expected']}｜跳過 {len(r['skipped'])}｜沒答 {len(r['missing'])}｜"
              f"檢查題錯 {len(r['checks_wrong'])}/{r['checks']}｜秒數中位數 "
              f"{'—' if r['median_seconds'] is None else round(r['median_seconds'], 1)}｜連續同答案最長 {r['longest_same_answer']}｜"
              f"Not sure {r['not_sure']}")
    print("提醒 Eden：\n  " + "\n  ".join(alerts) if alerts else "這次檢查的批次沒有超過門檻的情形。")
    sys.exit(3 if alerts else 0)


# ---------------------------------------------------------------- task R (Label Studio, Eden's computer only)
R_GUIDELINE_VERSION = "v1.5"   # annotations/guidelines/R_evidence_zh.md
# each answer gets the version in force when it was last saved (Label Studio's updated_at, UTC): v1.3 from commit
# f366258 (10/19 14:08 Taipei), v1.4 from 10/19 22:30 Taipei, when Eden started rechecking his 8 'topical' dev items,
# v1.5 (what 'theme' is) from 10/20 00:55 Taipei
R_GUIDELINE_SINCE = (("v1.2", ""), ("v1.3", "2026-10-19 06:08:10"), ("v1.4", "2026-10-19 14:30:00"),
                     (R_GUIDELINE_VERSION, "2026-10-19 16:55:00"))


def r_guideline(a):
    t = str(a.get("updated_at") or a.get("created_at") or "").replace("T", " ").rstrip("Z")
    return [v for v, since in R_GUIDELINE_SINCE if t >= since][-1]
R_PAGE_LONG_SIDE = 1400        # rendered page, pixels
R_LABEL_CONFIG = """<View>
  <Header value="藍框是這一題的照片。描述它的文字框：點灰框，再按 d 改成「描述文字」（可以選好幾個）。"/>
  <Image name="page" value="$image" zoom="true" zoomControl="true" maxWidth="100%"/>
  <RectangleLabels name="box" toName="page" showInline="true" strokeWidth="2" opacity="0.15">
    <Label value="照片" alias="photo" background="#1a56c4"/>
    <Label value="文字框" alias="text" background="#8a8a8a"/>
    <Label value="描述文字" alias="describes" background="#e8590c" hotkey="d"/>
  </RectangleLabels>
  <Choices name="not_photo" toName="page" choice="multiple" showInline="true">
    <Choice value="不是照片（勾了就不用標其他項）" alias="not_photo"/>
  </Choices>
  <Choices name="no_text" toName="page" choice="multiple" showInline="true">
    <Choice value="這頁沒有描述文字" alias="no_text"/>
  </Choices>
  <Header value="三級（單選）" size="6"/>
  <Choices name="level" toName="page" choice="single-radio" showInline="true">
    <Choice value="直接對應" alias="direct" hotkey="1"/>
    <Choice value="主題相關" alias="topical" hotkey="2"/>
    <Choice value="無關" alias="unrelated" hotkey="3"/>
  </Choices>
  <Header value="定錨（看得到就勾）" size="6"/>
  <Choices name="anchor" toName="page" choice="multiple" showInline="true">
    <Choice value="照片裡有能認出公司或活動的字" alias="text"/>
    <Choice value="照片裡有這家公司的 logo" alias="logo"/>
    <Choice value="後製疊上的字或 logo（不算定錨）" alias="overlay"/>
  </Choices>
  <Choices name="flags" toName="page" choice="multiple" showInline="true">
    <Choice value="描述文字的框有問題" alias="text_box_problem"/>
    <Choice value="照片框有問題" alias="photo_box_problem"/>
    <Choice value="不確定" alias="unsure"/>
  </Choices>
  <TextArea name="note" toName="page" placeholder="備註（可以不填）" rows="2" maxSubmissions="1" editable="true"/>
</View>
"""
R_LABELS = {"照片": "photo", "文字框": "text", "描述文字": "describes"}
R_STRAY_AREA = 1.0   # a drawn box under 1 (% of page width x % of page height), e.g. 1% x 1%, is a stray click


def r_ctx():
    c = ctx()
    c["r_work"] = ROOT / "work" / "annotation" / "R"
    c["page_graph"] = ROOT / c["cfg"]["paths"]["page_graph"]
    return c


def r_salt(c):
    p = c["r_work"] / "salt.txt"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_hex(16), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()


def r_item_id(c, crop):
    return "R" + hashlib.sha256(f"{r_salt(c)}:R:{crop}".encode()).hexdigest()[:8]


def page_lines(c, report):
    p = c["page_graph"] / f"{report}.jsonl"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return {json.loads(l)["page"]: json.loads(l) for l in f if l.strip()}


def r_photos(c, split, reports=None):
    """Photos the photo filter keeps (keep == 1) in the split, optionally only some reports."""
    out = []
    with open(c["filter_csv"], encoding="utf-8", newline="") as f:
        for d in csv.DictReader(f):
            if d["split"] == split and d["keep"] == "1" and (reports is None or d["report"] in reports):
                out.append({"crop": d["crop"], "report": d["report"], "page": int(d["page"]), "firm": d["firm_id"]})
    return sorted(out, key=lambda d: d["crop"])


def r_task(c, it, lines):
    """One Label Studio task: the whole page, the photo box (fixed) and every non-running text box, pre-drawn."""
    import pymupdf
    line = lines[it["page"]]
    pic = next((p for p in line["pictures"] if p["crop"] == it["crop"]), None)
    if pic is None:
        raise ValueError(f"{it['crop']} 不在 page_graph 第 {it['page']} 頁")
    pdf = ROOT / "data" / "raw" / f"{it['report']}.pdf"
    with pymupdf.open(pdf) as doc:
        page = doc[it["page"] - 1]
        zoom = R_PAGE_LONG_SIDE / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        img = "data:image/jpeg;base64," + base64.b64encode(pix.tobytes("jpg", jpg_quality=85)).decode()
        W, H = page.rect.width, page.rect.height
        iw, ih = pix.width, pix.height

    def region(rid, bbox, label, readonly):
        # clip to the page: a photo bleeding off the edge gives width 100.004%, which Label Studio rejects
        x0, y0, x1, y1 = max(0.0, bbox[0]), max(0.0, bbox[1]), min(W, bbox[2]), min(H, bbox[3])
        if x1 - x0 <= 0.5 or y1 - y0 <= 0.5:   # 10/24: a box outside the page (test batch 6) would get a negative size
            return None
        return {"id": rid, "from_name": "box", "to_name": "page", "type": "rectanglelabels",
                "original_width": iw, "original_height": ih, "image_rotation": 0, "readonly": readonly,
                "value": {"x": 100 * x0 / W, "y": 100 * y0 / H, "width": 100 * (x1 - x0) / W, "height": 100 * (y1 - y0) / H,
                          "rotation": 0, "rectanglelabels": [label]}}
    boxes = [b for b in line["boxes"] if not b["is_running"]]
    # labels by alias: Label Studio stores and validates the alias, not the Chinese value
    result = [region("photo", pic.get("visible_bbox") or pic["bbox"], "photo", True)]
    kept = [(b, region(b["id"], b["bbox"], "text", False)) for b in boxes]
    result += [r for _, r in kept if r]
    return {"data": {"image": img, "item": it["item"], "batch": it.get("batch", 0)},
            "predictions": [{"model_version": "page_graph", "result": [r for r in result if r]}]}, [b["id"] for b, r in kept if r]


def write_r_tasks(c, items, out_path):
    tasks, kept, missing = [], [], []
    cache = {}
    for it in items:
        if it["report"] not in cache:
            cache[it["report"]] = page_lines(c, it["report"])
        lines = cache[it["report"]]
        if not lines or it["page"] not in lines:
            missing.append(it["crop"])
            continue
        t, box_ids = r_task(c, it, lines)
        if set(t["data"]) - BLIND_KEYS:
            raise SystemExit("R 題目資料有不該有的欄位")
        tasks.append(t)
        kept.append(dict(it, text_boxes=box_ids))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(tasks), encoding="utf-8")
    (ROOT / "configs" / "labelstudio").mkdir(parents=True, exist_ok=True)
    (ROOT / "configs" / "labelstudio" / "R.xml").write_text(R_LABEL_CONFIG, encoding="utf-8")
    return kept, missing, tasks


def r_preview_html(tasks, title):
    """The pages with the same pre-drawn boxes as the Label Studio tasks (to check the boxes without Label Studio)."""
    cards = []
    for t in tasks:
        boxes = []
        for r in t["predictions"][0]["result"]:
            v = r["value"]
            photo = v["rectanglelabels"] == ["photo"]
            boxes.append(f'<div class="b {"p" if photo else "t"}" style="left:{v["x"]:.2f}%;top:{v["y"]:.2f}%;'
                         f'width:{v["width"]:.2f}%;height:{v["height"]:.2f}%" title="{html.escape(r["id"])}"></div>')
        cards.append(f'<section><h2>{html.escape(t["data"]["item"])}（{len(boxes) - 1} 個文字框）</h2>'
                     f'<div class="pg"><img src="{t["data"]["image"]}" alt="">{"".join(boxes)}</div></section>')
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{font-family:system-ui,"Microsoft JhengHei",sans-serif;margin:16px;background:#fafafa;color:#222}}
section{{margin:0 0 32px}} h2{{font-size:15px;font-weight:600}}
.pg{{position:relative;display:inline-block;max-width:100%}} .pg img{{display:block;max-width:100%}}
.b{{position:absolute;box-sizing:border-box}} .t{{border:1.5px solid #8a8a8a;background:rgba(138,138,138,.12)}}
.p{{border:3px solid #1a56c4;background:rgba(26,86,196,.10)}}
</style></head><body><h1 style="font-size:20px">{html.escape(title)}</h1>
<p>藍框是這一題的照片，灰框是程式從 PDF 切出來的文字框（導覽列、頁首頁尾不畫）。在 Label Studio 裡看到的框跟這裡一樣。這頁只是預覽，不能標。</p>
{"".join(cards)}</body></html>"""


def cmd_r_draft(args):
    """Draft R tasks for Eden to look at the screen (decision: real items wait for the A threshold). Only reports
    that already have page_graph output; the photo filter's current (initial) threshold."""
    c = r_ctx()
    reports = sorted(p.stem for p in c["page_graph"].glob("*.jsonl"))
    pool = r_photos(c, "dev", set(reports))
    if not pool:
        sys.exit("沒有 page_graph 的開發集報告：先跑 page_graph.py")
    rng = random.Random(f"{c['seed']}:R:draft")
    pick = rng.sample(pool, min(args.n, len(pool)))
    items = [dict(p, item=r_item_id(c, p["crop"]), split="dev", batch=0) for p in pick]
    out = c["r_work"] / "draft" / "R_draft_tasks.json"
    kept, missing, tasks = write_r_tasks(c, items, out)
    write_jsonl(c["r_work"] / "draft" / "items.jsonl", kept)
    # Eden labels these as a draft and sees model outputs on them (baselines_evidence.py trial): r-sample leaves them out
    (c["ann"] / "qc").mkdir(parents=True, exist_ok=True)
    (c["ann"] / "qc" / "R_draft_crops.txt").write_text("".join(k["crop"] + "\n" for k in sorted(kept, key=lambda k: k["crop"])),
                                                       encoding="utf-8")
    prev = ROOT / "reports" / "annotation_R" / "R_draft_preview.html"
    prev.parent.mkdir(parents=True, exist_ok=True)
    prev.write_text(r_preview_html(tasks, "標註 R 草稿題目預覽（20 題）"), encoding="utf-8")
    print(f"草稿 {len(kept)} 題（{len({k['report'] for k in kept})} 本，來自有 page_graph 的 {len(reports)} 本）→ {out}"
          f"（{out.stat().st_size / 1e6:.1f} MB）；設定 configs/labelstudio/R.xml；預覽 {prev}"
          + (f"；{len(missing)} 張沒有頁面結構" if missing else ""))


def cmd_r_sample(args):
    """The real R items (README 怎麼抽題 R): simple random among the photos the photo filter keeps, in the split's
    firms, n plus spares in draw order (a 'not a photo' item is skipped and the next one fills in). Run it after
    the photo threshold is set from annotation A (dev) and page_graph has run on the reports."""
    c = r_ctx()
    path = c["ann"] / args.split / "R_items.jsonl"
    if path.exists() and not args.force:
        sys.exit(f"{path} 已經存在；要重抽請加 --force")
    draft_list = c["ann"] / "qc" / "R_draft_crops.txt"
    draft = set(draft_list.read_text(encoding="utf-8").split()) if draft_list.exists() else set()
    pool = [p for p in r_photos(c, args.split) if p["crop"] not in draft]   # draft items: labelled once, model outputs seen
    rng = random.Random(f"{c['seed']}:R:{args.split}")
    rng.shuffle(pool)
    n = args.n + args.spares
    pick = pool[:n]
    need = sorted({p["report"] for p in pick if not (c["page_graph"] / f"{p['report']}.jsonl").exists()})
    if need:
        sys.exit(f"{len(need)} 本還沒有 page_graph（例如 {need[:5]}）：先跑 page_graph.py --all")
    items = [dict(p, item=r_item_id(c, p["crop"]), split=args.split, order=k + 1, spare=k >= args.n,
                  batch=1 + k // args.batch) for k, p in enumerate(pick)]
    kept, missing = [], []
    for b in sorted({it["batch"] for it in items}):   # one import file per batch: the next one only after a check
        k, m, _ = write_r_tasks(c, [it for it in items if it["batch"] == b], c["r_work"] / f"R_{args.split}_batch{b:02d}_tasks.json")
        kept += k
        missing += m
    write_jsonl(path, [{k: v for k, v in it.items() if k != "text_boxes"} | {"n_text_boxes": len(it["text_boxes"])} for it in kept])
    print(f"{len(kept)} 題（{args.n} 題＋備用 {args.spares}），每批 {args.batch} 題 → {c['r_work']}")
    write_result(c, f"annotation_R_sample_{args.split}", {"pool": len(pool), "n": args.n, "items": len(kept), "spares": args.spares,
                                                         "batch": args.batch, "excluded_draft_items": len(draft),
                                                         "missing_page_graph": missing}, [c["filter_csv"], draft_list])


def r_parse(path, items):
    """Label Studio export (JSON) of the R project → one row per task (the latest completed annotation)."""
    if not Path(path).is_file():
        sys.exit(f"找不到 {path}")
    try:
        tasks = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        sys.exit(f"{path} 不是 JSON：Label Studio 匯出時選 JSON")
    if not isinstance(tasks, list) or not all(isinstance(t, dict) and isinstance(t.get("data"), dict) for t in tasks):
        sys.exit(f"{path} 不是 Label Studio 的匯出檔（要一串題目，每題有 data）")
    rows = []
    for t in tasks:
        iid = t["data"].get("item")
        it = items.get(iid)
        done = [a for a in t.get("annotations", []) if not a.get("was_cancelled")]
        if not it:
            raise SystemExit(f"題號 {iid} 不在題目表裡：這不是 R 專案的匯出檔？")
        if not done:
            rows.append({"item": iid, "status": "missing"})
            continue
        a = max(done, key=lambda a: (a.get("updated_at") or a.get("created_at") or "", a.get("id") or 0))
        ch, desc, regions = collections.defaultdict(list), [], 0
        note = ""
        for r in a.get("result", []):
            v = r.get("value") or {}
            if r.get("type") == "rectanglelabels":
                regions += 1
                labs = [R_LABELS.get(x, x) for x in v.get("rectanglelabels", [])]
                if "describes" in labs:
                    desc.append(r.get("id"))
            elif r.get("type") == "choices":
                ch[r["from_name"]] += v.get("choices", [])
            elif r.get("type") == "textarea":
                note = " ".join(v.get("text", []))
        alias = {"不是照片（勾了就不用標其他項）": "not_photo", "這頁沒有描述文字": "no_text", "直接對應": "direct", "主題相關": "topical",
                 "無關": "unrelated", "照片裡有能認出公司或活動的字": "text", "照片裡有這家公司的 logo": "logo",
                 "後製疊上的字或 logo（不算定錨）": "overlay", "描述文字的框有問題": "text_box_problem", "照片框有問題": "photo_box_problem",
                 "不確定": "unsure"}
        A = {k: [alias.get(x, x) for x in v] for k, v in ch.items()}
        level = (A.get("level") or [None])[0]
        not_photo = "not_photo" in A.get("not_photo", [])
        # the pre-drawn boxes: the item's list when it has one (draft items); the real items (r-sample) keep only
        # the count, so a page_graph id (b + digits; Label Studio makes random ids for drawn boxes) counts as one.
        # 10/19: without this, every grey box Eden relabelled in R dev batch 1 was taken for a drawn box.
        ids = {r.get("id") for r in a.get("result", []) if r.get("type") == "rectanglelabels"}
        known = (set(it["text_boxes"]) if "text_boxes" in it else {i for i in ids if re.fullmatch(r"b\d+", str(i))}) | {"photo"}
        problems = []
        if not not_photo and not level:
            problems.append("沒選三級")
        if not not_photo and not desc and "no_text" not in A.get("no_text", []) and "text_box_problem" not in A.get("flags", []):
            problems.append("沒點描述文字，也沒勾「這頁沒有描述文字」或「描述文字的框有問題」")
        if "no_text" in A.get("no_text", []) and level == "direct":
            problems.append("勾了「這頁沒有描述文字」卻選直接對應（手冊：只能選主題相關或無關）")
        # boxes Eden drew (the manual says ticking text_box_problem is enough); a tiny one is a stray click with the
        # 描述文字 label active (10/19 draft: 0.2% x 0.4% of the page), not a box: dropped and counted
        area = {r.get("id"): (r.get("value") or {}).get("width", 0) * (r.get("value") or {}).get("height", 0)
                for r in a.get("result", []) if r.get("type") == "rectanglelabels"}
        stray = sorted(i for i in set(desc) - known if area.get(i, 0) < R_STRAY_AREA)
        desc = [i for i in desc if i not in stray]
        drawn = sorted(set(desc) - known)
        if drawn and "text_box_problem" not in A.get("flags", []):
            problems.append(f"畫了 {len(drawn)} 個新框，卻沒勾「描述文字的框有問題」")
        rows.append({"item": iid, "status": "done", "not_photo": not_photo, "describing_boxes": sorted(set(desc) & known),
                     "drawn_boxes": [{k: round(r["value"][k], 2) for k in ("x", "y", "width", "height")}
                                     for r in a.get("result", []) if r.get("id") in drawn],
                     "stray_boxes": len(stray), "no_text": "no_text" in A.get("no_text", []), "level": level, "anchor_text": "text" in A.get("anchor", []),
                     "anchor_logo": "logo" in A.get("anchor", []), "overlay": "overlay" in A.get("anchor", []),
                     "text_box_problem": "text_box_problem" in A.get("flags", []),
                     "photo_box_problem": "photo_box_problem" in A.get("flags", []), "unsure": "unsure" in A.get("flags", []),
                     "note": note, "seconds": a.get("lead_time"), "regions": regions, "problems": problems,
                     "guidelines": r_guideline(a)})
    return rows


def cmd_r_import(args):
    c = r_ctx()
    items_path = c["r_work"] / "draft" / "items.jsonl" if args.draft else c["ann"] / args.split / "R_items.jsonl"
    items = {r["item"]: r for r in read_jsonl(items_path)}
    if not items:
        sys.exit(f"沒有題目表 {items_path}")
    rows = r_parse(args.export, items)
    done = [r for r in rows if r["status"] == "done"]
    bad = [r for r in done if r["problems"]]
    for r in bad[:20]:
        print(f"{r['item']}：{'；'.join(r['problems'])}")
    summary = {"tasks": len(rows), "done": len(done), "missing": len(rows) - len(done), "with_problems": len(bad),
               "not_photo": sum(r["not_photo"] for r in done),
               "levels": dict(collections.Counter(r["level"] for r in done if not r["not_photo"])),
               "anchor_text": sum(r["anchor_text"] for r in done), "anchor_logo": sum(r["anchor_logo"] for r in done),
               "overlay": sum(r["overlay"] for r in done), "text_box_problem": sum(r["text_box_problem"] for r in done),
               "median_seconds": statistics.median([r["seconds"] for r in done if r.get("seconds") is not None] or [0]),
               "guidelines": dict(collections.Counter(r["guidelines"] for r in done))}
    if not args.draft:   # 'not a photo' items are skipped; spares in draw order fill up to n photos
        n = sum(not it.get("spare") for it in items.values())
        photos = sum(not r["not_photo"] for r in done)
        summary |= {"n": n, "photos": photos, "still_needed": max(0, n - photos)}
        nxt = sorted((it for it in items.values() if it["item"] not in {r["item"] for r in done}), key=lambda it: it["order"])
        print(f"已標 {photos} 張照片（要 {n} 張）" + (f"，還差 {n - photos} 張：接著標第 {nxt[0]['batch']} 批" if photos < n and nxt else ""))
    summary["stray_boxes"] = sum(r.get("stray_boxes", 0) for r in done)
    if args.baselines:   # E1 / E2 outputs (baselines_evidence.py) on the same photos, against these levels
        base = json.loads(Path(args.baselines).read_text(encoding="utf-8"))
        per = {d["crop"]: d for d in base["per_photo"]}
        crop = {k: v["crop"] for k, v in items.items()}
        pairs = [(r["level"], per[crop[r["item"]]]) for r in done
                 if not r["not_photo"] and r["level"] and crop[r["item"]] in per]
        cmp_ = {"baselines_file": args.baselines, "photos_compared": len(pairs)}
        for key, name in (("e1_level", "e1"), ("e2_choice", "e2")):
            got = [(lv, d[key]) for lv, d in pairs if key in d]
            if got:
                f1s = []
                for lv in ("direct", "topical", "unrelated"):
                    tp = sum(1 for a, b in got if a == lv and b == lv)
                    fp = sum(1 for a, b in got if a != lv and b == lv)
                    fn = sum(1 for a, b in got if a == lv and b != lv)
                    f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
                cmp_[name] = {"same_level": sum(a == b for a, b in got), "of": len(got), "macro_f1": round(statistics.mean(f1s), 4),
                              "confusion_label_by_model": {lv: dict(collections.Counter(b for a, b in got if a == lv))
                                                           for lv in ("direct", "topical", "unrelated")}}
        summary["vs_baselines"] = cmp_
        print(json.dumps(cmp_, ensure_ascii=False))
    print(json.dumps({k: v for k, v in summary.items() if k != "vs_baselines"}, ensure_ascii=False))
    if args.dry_run:
        print("（--dry-run：沒有寫任何檔）")
        return
    if args.draft:   # the draft is never written to annotations/; its summary and rows go to results/
        write_result(c, "annotation_R_draft_import", dict(summary, rows=rows), [args.export, items_path]
                     + ([args.baselines] if args.baselines else []))
        print("（草稿：沒有寫進 annotations/）")
        return
    write_jsonl(c["ann"] / args.split / f"{args.name}.jsonl", [dict(r, annotator=args.annotator) for r in done])
    write_result(c, f"annotation_{args.name}_import_{args.split}", summary, [args.export])


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("a-sample")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("a-qc-candidates")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("a-qc-import")
    p.add_argument("file")
    p = sub.add_parser("a-student-pages")
    p.add_argument("--draft", action="store_true", help="use the proposed answers before Eden confirms (pages say DRAFT)")
    p = sub.add_parser("a-pages", help="the batch HTML pages and one zip per annotator")
    p.add_argument("--draft", action="store_true", help="before Eden confirms: a1 only, into work/annotation/A/draft/")
    p.add_argument("--both", action="store_true", help="with --draft: both annotators")
    p.add_argument("--force", action="store_true", help="rebuild the batch plan (only before any answers are stored)")
    p = sub.add_parser("a-redo", help="a new version of one batch (same items, new order)")
    p.add_argument("--annotator", required=True, choices=ANNOTATORS)
    p.add_argument("--batch", required=True, type=int)
    p = sub.add_parser("a-extra", help="an extra development batch of low-score images (10/19)")
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--below", type=float, default=0.1, help="photo score below this")
    p.add_argument("--batch", type=int, help="the expected new batch number (a guard)")
    p = sub.add_parser("a-tasks", help="Label Studio import files (backup route)")
    p.add_argument("--draft", action="store_true", help="dry run before Eden confirms")
    p.add_argument("--force", action="store_true", help="rebuild the batch plan (only before any answers are stored)")
    p = sub.add_parser("a-check")
    p.add_argument("--annotator", required=True, choices=ANNOTATORS)
    src = p.add_mutually_exclusive_group()
    src.add_argument("--returns", nargs="*", help="answer files or folders (default: the folder work/annotation/A/returns)")
    src.add_argument("--export", help="Label Studio export (JSON) of that annotator's project (backup route)")
    p.add_argument("--batch", type=int, help="the batch to judge (default: the newest batch present)")
    p.add_argument("--all-batches", action="store_true", help="judge every batch again")
    p.add_argument("--force", action="store_true", help="accept fewer answers than already stored")
    p.add_argument("--dry-run", action="store_true", help="check only; write no labels or results")
    p = sub.add_parser("r-draft", help="draft R tasks (Label Studio) for Eden to see the screen")
    p.add_argument("--n", type=int, default=20)
    p = sub.add_parser("r-sample", help="the real R items (after the photo threshold and page_graph --all)")
    p.add_argument("--split", required=True, choices=["dev", "test"])
    p.add_argument("--n", type=int, required=True, help="100 for dev, 300 for test (README)")
    p.add_argument("--spares", type=int, default=40)
    p.add_argument("--batch", type=int, default=50)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("r-import", help="Label Studio export of the R project → annotations/{split}/R.jsonl")
    p.add_argument("--export", required=True)
    p.add_argument("--split", choices=["dev", "test"], default="dev")
    p.add_argument("--name", default="R", choices=["R", "R2"])
    p.add_argument("--annotator", default="eden")
    p.add_argument("--draft", action="store_true", help="the draft tasks (nothing goes to annotations/; a result file)")
    p.add_argument("--baselines", help="a baselines_evidence result file: compare E1 / E2 with these levels")
    p.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    {"a-sample": cmd_sample, "a-qc-candidates": cmd_qc_candidates, "a-qc-import": cmd_qc_import,
     "a-student-pages": cmd_student_pages, "a-pages": cmd_pages, "a-redo": cmd_redo, "a-tasks": cmd_tasks,
     "a-check": cmd_check, "a-extra": cmd_extra, "r-draft": cmd_r_draft, "r-sample": cmd_r_sample,
     "r-import": cmd_r_import}[args.cmd](args)


if __name__ == "__main__":
    main()
