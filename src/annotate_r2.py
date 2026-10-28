"""Annotation R2 (PLAN §6): a second annotator labels 150 of the R test photos on their own computer, the way B, C and
D work (Eden 10/20: self-contained HTML batch pages, 20 practice items and the manual).

The pages show exactly what Eden saw in Label Studio: the page image and the pre-drawn boxes of the R task files
(work/annotation/R/R_{split}_batchNN_tasks.json: the photo's blue box, one grey box per text box). Per item: click
the grey boxes that describe the photo, tick 這頁沒有描述文字 / 描述文字的框有問題 / 不是照片, pick the level, tick the
anchors (text, logo, post-production overlay) and the flags (照片框有問題, 不確定), add a note. Same manual (R v1.5).
  items     150 of the photos Eden labelled on the test split (his not-a-photo items left out; nothing else of his
            answers is read), seed 2026; three batches of 50.
  checks    one per batch: development items Eden marked 不是照片 (answer: 不是照片), mixed in like the others.
  practice  20 development photos (Eden's final labels: all 無關, 7 主題相關, 8 直接對應), each followed by his answer
            and the describing boxes in green; no pass mark (manual: 重點是看懂規則).
Page ids are opaque (annotate_bcd.iid with task R2); the plan and the check answers are in annotations/qc/.
check: reads work/annotation/R2/returns/, verifies page id, annotator and batch, reports missing items, check
errors, seconds and the R problems (as annotate_export.r_parse), and writes annotations/test/R2.jsonl in R.jsonl's
format (agreement is computed by evaluate.py --second-labels after configs/frozen.yaml).

  python src/annotate_r2.py sample
  python src/annotate_r2.py pages
  python src/annotate_r2.py check
"""
import argparse
import collections
import html
import json
import random
import re
import statistics
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import annotate_bcd as bcd  # noqa: E402
from annotate_export import CSS, R_GUIDELINE_VERSION, md_to_html, read_jsonl, write_jsonl  # noqa: E402

TASK = "R2"
ANNOTATOR = "r2"
BATCH = 50
CHECKS_PER_BATCH = 1   # 10/20: only 3 development items are 不是照片 after the calibration
PRACTICE = {"unrelated": 5, "topical": 7, "direct": 8}
LV_ZH = {"direct": "直接對應", "topical": "主題相關", "unrelated": "無關"}
bcd.TASKS[TASK] = {"zh": "標註 R2：照片的描述文字與證據", "batch": BATCH, "per_check": BATCH // CHECKS_PER_BATCH,
                   "practice": sum(PRACTICE.values()), "need": 0, "annotators": (ANNOTATOR,), "manual": "R_evidence_zh.md",
                   "limits": {"check_error": 0.5, "median_seconds": 8.0}}


def tasks_by_item(split):
    """{R item: Label Studio task} from the R task files of a split."""
    out = {}
    for f in sorted((ROOT / "work" / "annotation" / "R").glob(f"R_{split}_batch*_tasks.json")):
        if "first" in f.name:
            continue
        for t in json.loads(f.read_text(encoding="utf-8")):
            out[t["data"]["item"]] = t
    return out


def view(task):
    """{image data URI, photo box, text boxes} in % of the page, from a Label Studio task."""
    res = task["predictions"][0]["result"]
    photo = next(r["value"] for r in res if r["id"] == "photo")
    boxes = [{"b": r["id"], "x": round(r["value"]["x"], 2), "y": round(r["value"]["y"], 2), "w": round(r["value"]["width"], 2),
              "h": round(r["value"]["height"], 2)} for r in res if r["id"] != "photo"]
    return task["data"]["image"], {"x": round(photo["x"], 2), "y": round(photo["y"], 2), "w": round(photo["width"], 2),
                                   "h": round(photo["height"], 2)}, boxes


def plan_path():
    return ROOT / "annotations" / "qc" / "R2_task_plan.json"


def cmd_sample(args):
    cfg = bcd.cfg_all()
    if plan_path().exists() and not args.force:
        sys.exit(f"{plan_path()} 已經存在（重抽加 --force，學生開始標之後不要重抽）")
    eden = [r for r in read_jsonl(ROOT / "annotations" / "test" / "R.jsonl") if r.get("status", "done") == "done" and not r.get("not_photo")]
    tasks = tasks_by_item("test")
    pool = sorted(r["item"] for r in eden if r["item"] in tasks)
    if len(pool) < args.n:
        sys.exit(f"Eden 標成照片的測試題只有 {len(pool)} 題，不到 {args.n}")
    real = random.Random(f"{cfg['seed']}:R2").sample(pool, args.n)
    dev_lab = {r["item"]: r for r in read_jsonl(ROOT / "annotations" / "dev" / "R.jsonl")}
    dev_tasks = tasks_by_item("dev")
    checks = sorted(i for i, r in dev_lab.items() if r.get("not_photo") and i in dev_tasks)
    n_batches = -(-len(real) // BATCH)
    if len(checks) < n_batches * CHECKS_PER_BATCH:
        sys.exit(f"檢查題不夠：{len(checks)}")
    rng = random.Random(f"{cfg['seed']}:R2:order")
    batches = []
    for k in range(n_batches):
        rows = [{"key": i, "role": "real"} for i in real[k * BATCH:(k + 1) * BATCH]] + \
               [{"key": i, "role": "check"} for i in checks[k * CHECKS_PER_BATCH:(k + 1) * CHECKS_PER_BATCH]]
        rng.shuffle(rows)
        for r in rows:
            r["item"] = bcd.iid(TASK, r["key"])
        batches.append({"batch": k + 1, "items": rows})
    photos = sorted((i for i, r in dev_lab.items() if not r.get("not_photo") and r.get("level") and i in dev_tasks))
    prng = random.Random(f"{cfg['seed']}:R2:practice")
    practice = []
    for lv, n in PRACTICE.items():
        practice += prng.sample([i for i in photos if dev_lab[i]["level"] == lv], n)
    prng.shuffle(practice)
    plan = {"task": TASK, "annotator": ANNOTATOR, "split": "test", "guidelines": R_GUIDELINE_VERSION, "seed": cfg["seed"],
            "pool": len(pool), "batches": batches, "practice": practice,
            "note": "pool = Eden's test items labelled as photos when sampled (only his not-a-photo flag read)"}
    plan_path().write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    write_jsonl(ROOT / "annotations" / "qc" / "R2_checks_key.jsonl",
                [{"item": r["item"], "key": r["key"], "answer": "not_photo"} for b in batches for r in b["items"] if r["role"] == "check"])
    bcd.write_result("annotation_R2_sample", {"pool": len(pool), "items": len(real), "batches": n_batches,
                                              "checks": n_batches * CHECKS_PER_BATCH, "practice": len(practice)},
                     [ROOT / "annotations" / "test" / "R.jsonl", ROOT / "annotations" / "dev" / "R.jsonl"])
    print(f"R2：{len(real)} 題（從 {len(pool)} 張抽）、{n_batches} 批、檢查題 {n_batches * CHECKS_PER_BATCH}、練習 {len(practice)}")


# ---------------------------------------------------------------- the item screen (batch pages and practice)
UI_JS = r"""
function rview(it,a,ro){
  const bx=it.boxes.map(b=>'<div class="tb'+(a.desc.includes(b.b)?' on':'')+(ro&&ro.gold&&ro.gold.includes(b.b)?' gold':'')+'" style="left:'+b.x+'%;top:'+b.y+'%;width:'+b.w+'%;height:'+b.h+'%"'+(ro?'':' onclick="tb(\''+b.b+'\')"')+'></div>').join('');
  const p=it.photo, ck=(k,lab)=>'<label><input type="checkbox"'+(a[k]?' checked':'')+(ro?' disabled':' onchange="setf(\''+k+'\',this.checked)"')+'> '+lab+'</label>';
  const lv=(x,k,lab)=>'<label class="lv'+(a.level===x?' sel':'')+'"><input type="radio" name="lv"'+(a.level===x?' checked':'')+(ro?' disabled':' onchange="lvl(\''+x+'\')"')+'> '+k+'　'+lab+'</label>';
  return '<div class="rw"><div class="pg"><img src="'+IMG[it.p]+'" alt="" draggable="false">'+bx+
    '<div class="ph" style="left:'+p.x+'%;top:'+p.y+'%;width:'+p.w+'%;height:'+p.h+'%"></div></div>'+
    '<div class="side"><p class="mut">藍框是這一題的照片。點灰框選「描述這張照片的文字」（變橘色），再點一下取消。</p>'+
    '<div class="grp">'+ck('not_photo','不是照片（勾了就不用標其他項）')+'</div>'+
    '<div class="grp"><b>描述文字</b>'+ck('no_text','這頁沒有描述文字')+ck('text_box_problem','描述文字的框有問題')+'</div>'+
    '<div class="grp"><b>三級</b>'+lv('direct','1','直接對應')+lv('topical','2','主題相關')+lv('unrelated','3','無關')+'</div>'+
    '<div class="grp"><b>定錨（只看照片裡面）</b>'+ck('anchor_text','照片裡有能認出公司或活動的字')+ck('anchor_logo','照片裡有這家公司的 logo')+ck('overlay','後製疊上的字或 logo（不算定錨）')+'</div>'+
    '<div class="grp"><b>其他</b>'+ck('photo_box_problem','照片框有問題')+ck('unsure','不確定')+
    '<textarea id="note" rows="2" placeholder="備註（可以不填）"'+(ro?' disabled':' oninput="nt(this.value)"')+'>'+(a.note||'').replace(/</g,'&lt;')+'</textarea></div></div></div>';
}
function rblank(){ return {desc:[], not_photo:false, no_text:false, text_box_problem:false, level:null, anchor_text:false, anchor_logo:false, overlay:false, photo_box_problem:false, unsure:false, note:''}; }
function rproblem(a){
  if(a.not_photo) return '';
  if(!a.level) return '選三級（1 直接對應、2 主題相關、3 無關）。';
  if(!a.desc.length && !a.no_text && !a.text_box_problem) return '點描述文字的灰框，或勾「這頁沒有描述文字」或「描述文字的框有問題」。';
  if(a.no_text && a.level==='direct') return '勾了「這頁沒有描述文字」，三級只能選主題相關或無關。';
  return '';
}
"""

R2_TASK_JS = UI_JS + r"""
const TASK={
  blank:rblank,
  html:(it,a)=>rview(it,a,null),
  done:a=>!rproblem(a),
  get need(){ return rproblem(rec(ITEMS[S.idx].item)); },
  out:a=>({not_photo:!!a.not_photo, describing_boxes:a.desc.slice().sort(), no_text:!!a.no_text, text_box_problem:!!a.text_box_problem,
           level:a.not_photo?null:a.level, anchor_text:!!a.anchor_text, anchor_logo:!!a.anchor_logo, overlay:!!a.overlay,
           photo_box_problem:!!a.photo_box_problem, unsure:!!a.unsure, note:a.note||''}),
  key:ev=>{ if(ev.target.tagName==='TEXTAREA') return true;
            const m={'1':'direct','2':'topical','3':'unrelated'}; if(m[ev.key]){ lvl(m[ev.key]); return true; } return false; }
};
function A(){ return rec(ITEMS[S.idx].item); }
function tb(b){ const a=A(), i=a.desc.indexOf(b); if(i>=0) a.desc.splice(i,1); else a.desc.push(b); touched(a); render(); }
function setf(k,v){ const a=A(); a[k]=v; touched(a); render(); }
function lvl(x){ const a=A(); a.level=x; touched(a); render(); }
function nt(v){ const a=A(); a.note=v; save(); }
"""

R2_CSS = """
/* 10/20 Eden: boxes were offset and the page too small. The shared #stage img rule (margin 12px auto, max-height)
   moved and shrank the image inside .pg while the boxes stayed; the flex item also grew wider than the image.
   Now: the page image fills the full width (up to its 1400 px), no margin, no height cap; controls below it. */
body{max-width:1460px}
.rw{display:block}
.pg{position:relative;display:block;width:100%;max-width:1400px;margin:0 auto;user-select:none;line-height:0}
.pg img, #stage .pg img{display:block;width:100%;height:auto;max-height:none;margin:0;border-radius:0}
.tb{position:absolute;box-sizing:border-box;border:1px solid #888;background:#8881;cursor:pointer} .tb:hover{background:#8883}
.tb.on{border:2px solid #e8590c;background:#e8590c33} .tb.gold{outline:3px solid #137333;outline-offset:1px}
.ph{position:absolute;box-sizing:border-box;border:3px solid #1a56c4;pointer-events:none}
.side{font-size:16px;line-height:1.5;max-width:1400px;margin:10px auto 0;display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:4px 24px}
.side .mut{grid-column:1/-1} .grp{border-top:1px solid var(--line);padding:6px 0} .grp b{display:block;margin-bottom:2px}
.grp label{display:block;margin:3px 0} .lv.sel{font-weight:bold;color:var(--acc)} textarea{width:100%;font-size:14px;margin-top:4px}
"""


def item_view_rows(rows, tasks):
    items, imgs = [], {}
    for r in rows:
        uri, photo, boxes = view(tasks[r["key"]])
        k = "p" + bcd.hashlib.sha1(r["key"].encode()).hexdigest()[:10]
        imgs[k] = uri
        items.append({"item": r["item"], "p": k, "photo": photo, "boxes": boxes})
    return items, imgs


def batch_html(e, items, imgs):
    bcd.TASK_JS[TASK] = R2_TASK_JS
    page = bcd.batch_page(TASK, e, items, imgs)
    return page.replace("</style>", R2_CSS + "</style>", 1)


def practice_html(data, imgs):
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>標註 R2 練習</title>
<style>{CSS}{bcd.TASK_CSS}{R2_CSS}</style></head><body>
<h1>標註 R2：練習（{len(data)} 題）</h1>
<p>跟正式題一樣標，按「送出」後會看到黃老師（Eden）的答案：<b>綠框</b>是他點的描述文字。沒有及格線，重點是看懂規則；跟他不一樣的地方，回去對照手冊。</p>
<div id="stage"></div>
<script>
const DATA={json.dumps(data, ensure_ascii=False)};
const IMG={json.dumps(imgs)};
{UI_JS}
let i=0, cur=rblank(), shown=false; const log=[];
function A(){{ return cur; }}
function tb(b){{ if(shown) return; const k=cur.desc.indexOf(b); if(k>=0) cur.desc.splice(k,1); else cur.desc.push(b); show(); }}
function setf(k,v){{ cur[k]=v; show(); }}
function lvl(x){{ cur.level=x; show(); }}
function nt(v){{ cur.note=v; }}
const ZH={json.dumps(LV_ZH, ensure_ascii=False)};
function show(){{
  const s=document.getElementById('stage');
  if(i>=DATA.length){{
    const same=log.filter(x=>x.same_level).length;
    s.innerHTML='<h2>做完了：三級跟 Eden 一樣 '+same+' / '+DATA.length+' 題</h2><p>這只是讓你看懂規則，沒有及格線。告訴黃老師你做完了，然後開始第 1 批。</p><button onclick="dl()">存下我的練習</button>';
    return;
  }}
  const d=DATA[i];
  let fb='';
  if(shown){{
    const g=d.gold, anc=[g.anchor_text?'照片裡的字':'', g.anchor_logo?'logo':'', g.overlay?'後製疊上的字':''].filter(x=>x).join('、')||'沒有';
    fb='<div class="fbk"><p class="'+(cur.level===g.level?'ok':'bad')+'">Eden 的答案：<b>'+ZH[g.level]+'</b>'+(g.no_text?'（這頁沒有描述文字）':'')+'；定錨：'+anc+'。綠框是他點的描述文字。</p>'+
       (g.why?'<p>'+g.why+'</p>':'')+'<button onclick="nx()">下一題（Enter）</button></div>';
  }}
  s.innerHTML='<p class="mut">第 '+(i+1)+' 題，共 '+DATA.length+' 題</p>'+rview(d, cur, shown?{{gold:d.gold.describing_boxes}}:null)+
    (shown?fb:'<p class="row"><button class="big" onclick="send()">送出</button></p><p id="msg" class="bad row"></p>');
}}
function send(){{ const p=rproblem(cur); if(p){{ document.getElementById('msg').textContent=p; return; }}
  const g=DATA[i].gold; log.push({{id:DATA[i].id, level:cur.level, gold_level:g.level, same_level:cur.level===g.level}}); shown=true; show(); }}
function nx(){{ if(!shown) return; i++; cur=rblank(); shown=false; show(); window.scrollTo(0,0); }}
document.addEventListener('keydown',ev=>{{ if(ev.target.tagName==='TEXTAREA') return; const m={{'1':'direct','2':'topical','3':'unrelated'}};
  if(!shown&&m[ev.key]) lvl(m[ev.key]); else if(ev.key==='Enter'){{ ev.preventDefault(); shown?nx():send(); }} }});
function dl(){{
  const blob=new Blob([JSON.stringify({{task:'R2', practice:true, annotator:'r2', same_level:log.filter(x=>x.same_level).length, of:DATA.length, answers:log}},null,1)],{{type:'application/json'}});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='R2_practice_r2.json'; a.click();
}}
show();
</script></body></html>"""


def cmd_pages(args):
    plan = json.loads(plan_path().read_text(encoding="utf-8"))
    test_tasks, dev_tasks = tasks_by_item("test"), tasks_by_item("dev")
    out = bcd.work(TASK) / "pages"
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for b in plan["batches"]:
        rows = b["items"]
        all_tasks = {**test_tasks, **dev_tasks}
        items, imgs = item_view_rows(rows, all_tasks)
        e = {"task": TASK, "annotator": ANNOTATOR, "batch": b["batch"], "version": 1, "items": [r["item"] for r in rows]}
        e["page_id"] = bcd.page_id(e)
        b["page_id"] = e["page_id"]
        name = f"R2_{ANNOTATOR}_batch{b['batch']:02d}.html"
        (out / name).write_text(batch_html(e, items, imgs), encoding="utf-8")
        files.append(name)
    plan_path().write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    dev_lab = {r["item"]: r for r in read_jsonl(ROOT / "annotations" / "dev" / "R.jsonl")}
    data, imgs = [], {}
    for k in plan["practice"]:
        uri, photo, boxes = view(dev_tasks[k])
        key = "p" + bcd.hashlib.sha1(k.encode()).hexdigest()[:10]
        imgs[key] = uri
        g = dev_lab[k]
        data.append({"id": bcd.iid(TASK, "practice|" + k), "p": key, "photo": photo, "boxes": boxes,
                     "gold": {f: g.get(f) for f in ("level", "no_text", "describing_boxes", "anchor_text", "anchor_logo", "overlay")}})
    (out / "R2_practice.html").write_text(practice_html(data, imgs), encoding="utf-8")
    manual = (ROOT / "annotations" / "guidelines" / "R_evidence_zh.md").read_text(encoding="utf-8")
    (out / "R2_guidelines.html").write_text(
        f"<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>標註 R 手冊</title><style>{CSS}</style></head><body>{md_to_html(manual)}</body></html>", encoding="utf-8")
    links = "".join(f'<li><a href="{f}">第 {k + 1} 批</a></li>' for k, f in enumerate(files))
    (out / "index.html").write_text(f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>標註 R2</title><style>{CSS}</style></head><body><h1>標註 R2：照片的描述文字與證據</h1>
<p>請用<b>筆電或桌機</b>的瀏覽器（不要用手機），照順序做：</p>
<ol><li><a href="R2_guidelines.html">手冊</a>：先從頭讀一遍（約 10 分鐘）。</li>
<li><a href="R2_practice.html">練習 {len(data)} 題</a>：每題送出後會看到黃老師的答案，對照手冊看懂規則。做完按「存下我的練習」寄給黃老師。</li>
<li>正式題，每批約 {BATCH} 題（一題約半分鐘）：<ol>{links}</ol></li></ol>
<p>每批做完按「下載答案」，檔名開頭是 <b>R2_r2_batch</b>，不要改檔名或內容，馬上 email 給黃老師；他確認後再做下一批。</p>
<p class="mut">規則：自己判斷，不要跟別人討論，也不要上網查；看不準就勾「不確定」，在備註寫原因。進度存在這個瀏覽器裡，關掉再開（同一台電腦、同一個瀏覽器）可以接著做。</p></body></html>""",
                                      encoding="utf-8")
    zp = bcd.work(TASK) / f"package_{ANNOTATOR}.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in ["index.html", "R2_guidelines.html", "R2_practice.html"] + files:
            z.write(out / f, f"R2_{ANNOTATOR}/{f}")
    print(f"{len(files)} 批、練習 {len(data)} 題 → {zp}（{zp.stat().st_size / 1e6:.1f} MB）")


def cmd_check(args):
    plan = json.loads(plan_path().read_text(encoding="utf-8"))
    key = {r["item"]: r["answer"] for r in read_jsonl(ROOT / "annotations" / "qc" / "R2_checks_key.jsonl")}
    by_batch = {b["batch"]: b for b in plan["batches"]}
    latest = {}
    for f in sorted((bcd.work(TASK) / "returns").glob("R2_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        b = by_batch.get(d.get("batch"))
        if d.get("task") != TASK or d.get("annotator") != ANNOTATOR or not b or d.get("page_id") != b.get("page_id"):
            print(f"略過 {f.name}：不是這份題目的答案檔")
            continue
        if d["batch"] not in latest or d["downloaded_at"] > latest[d["batch"]][0]["downloaded_at"]:
            latest[d["batch"]] = (d, f.name)
    rows, report = [], []
    for bno, (d, fname) in sorted(latest.items()):
        roles = {r["item"]: r for r in by_batch[bno]["items"]}
        ans = {a["item"]: a for a in d["answers"]}
        missing = [i for i in roles if i not in ans or (not ans[i].get("not_photo") and not ans[i].get("level"))]
        checks = [i for i, r in roles.items() if r["role"] == "check"]
        wrong = [i for i in checks if i in ans and not ans[i].get("not_photo")]
        secs = [a["seconds"] for a in ans.values() if a.get("seconds")]
        problems = collections.Counter()
        for i, r in roles.items():
            a = ans.get(i)
            if r["role"] != "real" or not a:
                continue
            if not a.get("not_photo") and not a.get("describing_boxes") and not a.get("no_text") and not a.get("text_box_problem"):
                problems["沒點描述文字"] += 1
            if a.get("no_text") and a.get("level") == "direct":
                problems["沒有描述文字卻選直接對應"] += 1
            rows.append({"item": r["key"], "status": "done", "not_photo": bool(a.get("not_photo")),
                         "describing_boxes": a.get("describing_boxes") or [], "drawn_boxes": [], "stray_boxes": 0,
                         "no_text": bool(a.get("no_text")), "level": a.get("level"), "anchor_text": bool(a.get("anchor_text")),
                         "anchor_logo": bool(a.get("anchor_logo")), "overlay": bool(a.get("overlay")),
                         "text_box_problem": bool(a.get("text_box_problem")), "photo_box_problem": bool(a.get("photo_box_problem")),
                         "unsure": bool(a.get("unsure")), "note": a.get("note", ""), "seconds": a.get("seconds"),
                         "guidelines": plan["guidelines"], "annotator": ANNOTATOR, "batch": bno})
        med = round(statistics.median(secs), 1) if secs else None
        lim = bcd.TASKS[TASK]["limits"]
        alerts = ([f"檢查題錯 {len(wrong)}／{len(checks)}"] if checks and len(wrong) / len(checks) > lim["check_error"] else []) + \
                 ([f"每題秒數中位數 {med}"] if med is not None and med < lim["median_seconds"] else []) + \
                 ([f"沒答 {len(missing)} 題"] if missing else [])
        report.append({"batch": bno, "file": fname, "items": len(roles), "missing": len(missing), "checks": len(checks),
                       "check_wrong": len(wrong), "median_seconds": med, "problems": dict(problems), "alerts": alerts})
        print(f"第 {bno} 批（{fname}）：沒答 {len(missing)}、檢查題錯 {len(wrong)}/{len(checks)}、秒數中位數 {med}、"
              f"問題 {dict(problems) or '無'}" + (f"　← 提醒：{'；'.join(alerts)}" if alerts else ""))
    if not rows:
        sys.exit("還沒有答案檔（work/annotation/R2/returns/）")
    if args.dry_run:
        print("（--dry-run：沒有寫檔）")
        return
    write_jsonl(ROOT / "annotations" / "test" / "R2.jsonl", rows)
    bcd.write_result("annotation_R2_check", {"batches": report, "items": len(rows)},
                     [plan_path(), ROOT / "annotations" / "qc" / "R2_checks_key.jsonl"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample")
    p.add_argument("--n", type=int, default=150)
    p.add_argument("--force", action="store_true")
    sub.add_parser("pages")
    p = sub.add_parser("check")
    p.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    {"sample": cmd_sample, "pages": cmd_pages, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    main()
