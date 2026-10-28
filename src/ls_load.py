"""Load annotation items into Eden's own Label Studio without clicking through the web pages (run with .venv_ls).

Eden registers his account himself (start_labelstudio.ps1 -AllowSignup); this script never touches accounts or
passwords. It works on the Label Studio database in work/labelstudio_{annotator}/ through Label Studio's own code
(the same serializer and counter updates as its import API), so the result is what the Import button gives:

  1. backs up the database to work/labelstudio_{annotator}/backups/ (sqlite backup API);
  2. sets the project's labelling screen from --config (validated by Label Studio), its title, sequential order,
     and "use predictions to prelabel" with the model version of the pre-drawn boxes (page_graph);
  3. imports --tasks (with their pre-drawn boxes), after checking every prediction against the screen.

It stops when the project already has tasks (no duplicates); --append adds anyway, skipping items already there.
The project is --project ID (an existing one, e.g. the empty one Eden made) or --new "title" (owned by the only
account in that Label Studio). Label Studio may keep running; reload the page afterwards.

  .venv_ls\\Scripts\\python.exe src\\ls_load.py --annotator eden --project 1 --title "R – 草稿" ^
      --config configs\\labelstudio\\R.xml --tasks work\\annotation\\R\\draft\\R_draft_tasks.json
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def backup(db, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"label_studio_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite3"
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with sqlite3.connect(dst) as d:
        src.backup(d)
    src.close()
    return dst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--annotator", default="eden", choices=["eden", "a1", "a2"])
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--project", type=int, help="id of an existing project")
    who.add_argument("--new", metavar="TITLE", help="create a new project with this title")
    ap.add_argument("--title", help="rename the project")
    ap.add_argument("--config", required=True, help="labelling screen (XML)")
    ap.add_argument("--tasks", required=True, help="tasks JSON (annotate_export.py r-draft / r-sample)")
    ap.add_argument("--model-version", default="page_graph")
    ap.add_argument("--append", action="store_true", help="add to a project that already has tasks")
    ap.add_argument("--data-dir", help="another Label Studio data folder (tests on a copy)")
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else ROOT / "work" / f"labelstudio_{args.annotator}"
    db = data_dir / "label_studio.sqlite3"
    if not db.exists():
        sys.exit(f"找不到 {db}：先用 start_labelstudio.ps1 開過、註冊好帳號")
    config = Path(args.config).read_text(encoding="utf-8")
    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or not tasks:
        sys.exit(f"{args.tasks} 沒有題目")

    bk = backup(db, data_dir / "backups")
    print(f"備份：{bk}")

    os.environ["LABEL_STUDIO_BASE_DATA_DIR"] = str(data_dir)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.label_studio")
    os.environ["LABEL_STUDIO_LATEST_VERSION_CHECK"] = "false"   # no call to pypi.org
    import label_studio
    sys.path.insert(0, os.path.dirname(label_studio.__file__))
    import django
    django.setup()
    from django.db import transaction
    from label_studio_sdk.label_interface import LabelInterface
    from data_import.serializers import ImportApiSerializer
    from projects.models import Project
    from users.models import User

    users = list(User.objects.all())
    if len(users) != 1:
        sys.exit(f"這個 Label Studio 有 {len(users)} 個帳號；這支程式只處理只有你一個帳號的情形")
    user = users[0]

    li = LabelInterface(config)
    errors = [f"題 {i}：{e}" for i, t in enumerate(tasks) for p in t.get("predictions", [])
              for e in (li.validate_prediction(p, return_errors=True) or [])]
    if errors:
        sys.exit("預畫的框跟畫面設定對不上：\n" + "\n".join(errors[:10]))

    with transaction.atomic():
        if args.project:
            project = Project.objects.filter(id=args.project).first()
            if project is None:
                sys.exit(f"沒有編號 {args.project} 的專案")
        else:
            project = Project.objects.create(title=args.new, created_by=user, organization=user.active_organization)
        have = {t.data.get("item") for t in project.tasks.all()}
        if have and not args.append:
            sys.exit(f"專案「{project.title}」已經有 {len(have)} 題；要再加請用 --append（已有的題號會跳過）")
        tasks = [t for t in tasks if t["data"].get("item") not in have]
        project.validate_config(config)
        project.label_config = config
        if args.title:
            project.title = args.title
        project.sampling = Project.SEQUENCE
        project.show_collab_predictions = True
        project.model_version = args.model_version
        project.save()

        ser = ImportApiSerializer(data=tasks, many=True, context={"project": project, "user": user})
        ser.is_valid(raise_exception=True)
        created = ser.save(project_id=project.id)
        project.update_tasks_counters_and_task_states(
            tasks_queryset=created, maximum_annotations_changed=False, overlap_cohort_percentage_changed=False,
            tasks_number_changed=True,
            recalculate_stats_counts={"task_count": len(created), "annotation_count": len(ser.db_annotations),
                                      "prediction_count": len(ser.db_predictions)})
        project.summary.update_data_columns(tasks)

    print(f"專案 {project.id}「{project.title}」：新增 {len(created)} 題、預畫框 {len(ser.db_predictions)} 組；"
          f"現在共 {project.tasks.count()} 題。瀏覽器重新整理就看得到。")


if __name__ == "__main__":
    main()
