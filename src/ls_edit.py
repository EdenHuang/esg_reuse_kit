"""Change submitted answers in Eden's Label Studio from an edit list, when Eden has approved the changes (run with
.venv_ls). 10/20: Eden approved every change on reports/R_calibration_dev.html ("我都同意 你直接改 Label Studio 結果").

Never touches accounts or passwords. Like ls_load.py it backs up the database first (work/labelstudio_{annotator}/
backups/) and goes through Label Studio's own models, so each changed answer gets a new updated_at (annotate_export.py
stamps the manual version from it). Only the latest submitted answer of each task is changed; nothing is deleted.
Every change is written to --log (one line per task: the answer before and after, in the shape r-import reads).

Edit list (JSON): [{"task": 21, "level": "direct", "no_text": false, "not_photo": false, "describes": ["b005"],
"add": {"anchor": ["text"], "flags": ["photo_box_problem"]}, "why": "..."}]; a key left out stays as it is.
"describes" replaces the set of describing boxes (boxes taken off go back to the plain text label).

  .venv_ls\\Scripts\\python.exe src\\ls_edit.py --project 2 --edits work\\annotation\\R\\claude_edits_20261020.json ^
      --log annotations\\qc\\R_dev_claude_edits.jsonl
"""
import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ls_load import backup  # noqa: E402

CHOICE_NAMES = ("not_photo", "no_text", "level", "anchor", "flags")


def summary(result):
    """The answer in r-import's terms (aliases as stored by Label Studio)."""
    ch = {}
    for r in result:
        if r.get("type") == "choices":
            ch.setdefault(r["from_name"], []).extend((r.get("value") or {}).get("choices", []))
    desc = sorted(r["id"] for r in result if r.get("type") == "rectanglelabels"
                  and "describes" in (r.get("value") or {}).get("rectanglelabels", []))
    return {"describes": desc, **{k: sorted(ch.get(k, [])) for k in CHOICE_NAMES}}


def set_choice(result, name, values, task_id):
    """Replace the choices of `name` (an empty list removes them)."""
    out = [r for r in result if not (r.get("type") == "choices" and r.get("from_name") == name)]
    if values:
        rid = "cl" + hashlib.sha1(f"{task_id}:{name}".encode()).hexdigest()[:8]
        out.append({"id": rid, "from_name": name, "to_name": "page", "type": "choices", "origin": "manual",
                    "value": {"choices": list(values)}})
    return out


def apply_edit(result, pred, e, task_id):
    res = copy.deepcopy(result)
    cur = summary(res)
    if "not_photo" in e:
        res = set_choice(res, "not_photo", ["not_photo"] if e["not_photo"] else [], task_id)
    if "no_text" in e:
        res = set_choice(res, "no_text", ["no_text"] if e["no_text"] else [], task_id)
    if "level" in e:
        res = set_choice(res, "level", [e["level"]], task_id)
    for name, vals in (e.get("add") or {}).items():
        res = set_choice(res, name, sorted(set(cur.get(name, [])) | set(vals)), task_id)
    if "describes" in e:
        want = set(e["describes"])
        have = {r["id"]: r for r in res if r.get("type") == "rectanglelabels"}
        missing = want - set(have)
        for p in pred:   # a box Eden never touched may be missing from his answer: take it from the pre-drawn boxes
            if p.get("id") in missing:
                q = copy.deepcopy(p)
                q.pop("readonly", None)
                res.append(q)
                have[q["id"]] = q
        if want - set(have):
            raise SystemExit(f"task {task_id}：找不到框 {sorted(want - set(have))}")
        for bid, r in have.items():
            if bid == "photo":
                continue
            labs = r["value"]["rectanglelabels"]
            if bid in want and labs != ["describes"]:
                r["value"]["rectanglelabels"], r["origin"] = ["describes"], "prediction-changed"
            elif bid not in want and labs == ["describes"]:
                r["value"]["rectanglelabels"] = ["text"]
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--annotator", default="eden", choices=["eden", "a1", "a2"])
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--edits", required=True)
    ap.add_argument("--log", required=True, help="JSONL of the answers before and after (not overwritten)")
    ap.add_argument("--data-dir", help="another Label Studio data folder (tests on a copy)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    data_dir = Path(args.data_dir) if args.data_dir else ROOT / "work" / f"labelstudio_{args.annotator}"
    db = data_dir / "label_studio.sqlite3"
    if not db.exists():
        sys.exit(f"找不到 {db}")
    log = Path(args.log)
    if log.exists() and not args.dry_run:
        sys.exit(f"{log} 已經存在，不覆蓋")
    edits = json.loads(Path(args.edits).read_text(encoding="utf-8"))
    if len({e["task"] for e in edits}) != len(edits):
        sys.exit("同一題出現兩次：合併成一筆")

    if not args.dry_run:
        print(f"備份：{backup(db, data_dir / 'backups')}")
    os.environ["LABEL_STUDIO_BASE_DATA_DIR"] = str(data_dir)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.label_studio")
    os.environ["LABEL_STUDIO_LATEST_VERSION_CHECK"] = "false"
    import label_studio
    sys.path.insert(0, os.path.dirname(label_studio.__file__))
    import django
    django.setup()
    from django.db import transaction
    from label_studio_sdk.label_interface import LabelInterface
    from projects.models import Project
    from tasks.models import Annotation, Prediction, Task
    from users.models import User

    users = list(User.objects.all())
    if len(users) != 1:
        sys.exit(f"這個 Label Studio 有 {len(users)} 個帳號；這支程式只處理只有你一個帳號的情形")
    project = Project.objects.get(id=args.project)
    li = LabelInterface(project.label_config)
    rows = []
    with transaction.atomic():
        for e in edits:
            task = Task.objects.get(id=e["task"], project=project)
            anns = list(Annotation.objects.filter(task=task, was_cancelled=False).order_by("-updated_at", "-id"))
            if not anns:
                sys.exit(f"task {e['task']} 沒有送出的答案")
            ann = anns[0]
            pred = (Prediction.objects.filter(task=task).order_by("-id").values_list("result", flat=True) or [[]])[0]
            new = apply_edit(ann.result, pred, e, task.id)
            errs = li.validate_prediction({"result": new}, return_errors=True) or []
            if errs:
                sys.exit(f"task {e['task']}：改完的答案跟畫面設定對不上：{errs[:3]}")
            before, after = summary(ann.result), summary(new)
            rows.append({"task": task.id, "item": task.data.get("item"), "annotation": ann.id, "why": e.get("why", ""),
                         "before": before, "after": after})
            print(f"task {task.id} {task.data.get('item')}：" + "；".join(
                f"{k} {before[k]} → {after[k]}" for k in before if before[k] != after[k]))
            if not args.dry_run:
                ann.result = new
                ann.updated_by = users[0]
                ann.save()
        if args.dry_run:
            transaction.set_rollback(True)
    if args.dry_run:
        print(f"（--dry-run：{len(rows)} 題，沒有寫入）")
        return
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"改了 {len(rows)} 題 → {log}。瀏覽器重新整理就看得到。")


if __name__ == "__main__":
    main()
