"""Read Eden's answers out of his Label Studio database (read-only) in the shape of Label Studio's JSON export,
so he does not have to click Export (annotate_export.py r-import reads the file).

Opens work/labelstudio_{annotator}/label_studio.sqlite3 read-only; never writes to Label Studio. Each task keeps
its data (image left out unless --with-images) and its annotations (id, result, was_cancelled, lead_time,
created_at, updated_at, completed_by); unsubmitted drafts are counted and reported, not exported.

  python src/ls_export.py --annotator eden --project 1 --out work/annotation/R/exports/R_draft_export_20261019_1300.json
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--annotator", default="eden", choices=["eden", "a1", "a2"])
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--with-images", action="store_true")
    ap.add_argument("--data-dir", help="another Label Studio data folder (tests on a copy)")
    args = ap.parse_args()
    db = (Path(args.data_dir) if args.data_dir else ROOT / "work" / f"labelstudio_{args.annotator}") / "label_studio.sqlite3"
    if not db.exists():
        sys.exit(f"找不到 {db}")
    out = Path(args.out)
    if out.exists():
        sys.exit(f"{out} 已經存在，不覆蓋")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    tasks = con.execute("select id, data from task where project_id = ? order by id", (args.project,)).fetchall()
    if not tasks:
        sys.exit(f"專案 {args.project} 沒有題目")
    anns = {}
    for a in con.execute("select id, task_id, result, was_cancelled, lead_time, created_at, updated_at, completed_by_id "
                         "from task_completion where task_id in (select id from task where project_id = ?)", (args.project,)):
        anns.setdefault(a["task_id"], []).append(
            {"id": a["id"], "result": json.loads(a["result"] or "[]"), "was_cancelled": bool(a["was_cancelled"]),
             "lead_time": a["lead_time"], "created_at": a["created_at"], "updated_at": a["updated_at"],
             "completed_by": a["completed_by_id"]})
    drafts = con.execute("select count(*) from tasks_annotationdraft where task_id in "
                         "(select id from task where project_id = ?)", (args.project,)).fetchone()[0]
    rows = []
    for t in tasks:
        data = json.loads(t["data"])
        if not args.with_images:
            data.pop("image", None)
        rows.append({"id": t["id"], "data": data, "annotations": anns.get(t["id"], [])})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    done = sum(1 for r in rows if any(not a["was_cancelled"] for a in r["annotations"]))
    print(f"{len(rows)} 題，已送出 {done} 題；還沒送出的草稿 {drafts} 個 → {out}")


if __name__ == "__main__":
    main()
