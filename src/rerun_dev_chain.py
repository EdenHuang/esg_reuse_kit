"""Re-run the development evaluation chain after an anchoring change (10/22, NEXT C11: short English names also match
as punctuation-bounded OCR tokens). Every model output is cached (rule 9), so the runs only recompute anchors, cuts and
scores. New tags (…_q3r, …_r) so no earlier result or prediction folder is replaced. Development split only (rule 3).

  python src/rerun_dev_chain.py            # writes work/rerun_dev_chain.log and prints the new result files
"""
import glob
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
LOG = ROOT / "work" / "rerun_dev_chain.log"
CTX = {"rules": "rules", "M_lr": "work/context/M_lr_dev140.jsonl", "L1_nearest": "work/context/L1_nearest_dev140.jsonl",
       "L2_column": "work/context/L2_column_dev140.jsonl", "L3_caption": "work/context/L3_caption_dev140.jsonl",
       "L4_heading": "work/context/L4_heading_dev140.jsonl", "gold": "work/context/gold_dev140.jsonl"}
LABELS = "annotations/dev/R.jsonl"


def latest(pattern):
    fs = sorted(glob.glob(str(ROOT / pattern)))
    if not fs:
        sys.exit(f"沒有 {pattern}")
    return str(Path(fs[-1]).relative_to(ROOT))


def run(args):
    cmd = [PY] + args
    t0 = time.time()
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n$ {' '.join(args)}\n")
        r = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        f.write(f"[exit {r.returncode}, {time.time() - t0:.0f} s]\n")
    print(f"{args[0].split('/')[-1]} {args[-1]}: exit {r.returncode}, {time.time() - t0:.0f} s", flush=True)
    if r.returncode:
        sys.exit(f"失敗：{' '.join(args)}（看 {LOG}）")


def main():
    LOG.write_text("", encoding="utf-8")
    base = ["src/evidence.py", "--r-items", "dev", "--labels", LABELS, "--labelled-only", "--holistic"]
    # 1. module-2 evidence files (anchors inside) with the main VLM, every context
    for x, ctx in CTX.items():
        run(base + ["--context", ctx, "--tag", f"dev_ctx_{x}_q3r"])
    # 2. the same for the earlier VLM, rules context only (Table 5's Qwen2.5 rows)
    run(["src/evidence.py", "--vlm", "alt"] + base[1:] + ["--context", "rules", "--tag", "dev_ctx_rules_r"])
    # 3. cuts, leave-one-firm-out, per context (the baselines files hold the cached CLIP / zero-shot / few-shot outputs)
    run(["src/tune_evidence.py", "--evidence", "work/evidence/evidence_dev_ctx_rules_q3r.jsonl",
         "--baselines", latest("results/baselines_evidence_dev_ctx_rules_q3_2026*.json"), "--context", "rules_q3r"])
    run(["src/tune_evidence.py", "--evidence", "work/evidence/evidence_dev_ctx_rules_q3r.jsonl",
         "--baselines", latest("results/baselines_evidence_dev_ctx_rules_q3e3_2026*.json"), "--context", "rules_q3e3r"])
    for x in CTX:
        if x == "rules":
            continue
        run(["src/tune_evidence.py", "--evidence", f"work/evidence/evidence_dev_ctx_{x}_q3r.jsonl",
             "--baselines", latest(f"results/baselines_evidence_dev_ctx_{x}_q3e3_2026*.json"), "--context", f"{x}_q3e3r"])
    run(["src/tune_evidence.py", "--evidence", "work/evidence/evidence_dev_ctx_rules_r.jsonl",
         "--baselines", "results/baselines_evidence_dev_ctx_rules_20261020_0213.json", "--context", "rules_r"])
    run(["src/tune_evidence.py", "--evidence", "work/evidence/evidence_dev_ctx_rules_r.jsonl",
         "--baselines", "results/baselines_evidence_dev_ctx_rules_e2v1_20261020_0217.json", "--context", "rules_e2v1r"])
    # 4. scores
    run(["src/evaluate.py", "--split", "dev", "--pred",
         "tool=work/eval/dev_rules_q3e3r/E3_cal.jsonl", "tool_no_ocr=work/eval/dev_rules_q3e3r/E3_cal_no_ocr.jsonl",
         "tool_no_logo=work/eval/dev_rules_q3e3r/E3_cal_no_logo.jsonl", "E3_uncal=work/eval/dev_rules_q3e3r/E3.jsonl",
         "module2=work/eval/dev_rules_q3r/module2.jsonl", "E2_cal=work/eval/dev_rules_q3r/E2_cal.jsonl", "E1=work/eval/dev_rules_q3r/E1.jsonl",
         "q25_module2=work/eval/dev_rules_r/module2.jsonl", "q25_E2_cal=work/eval/dev_rules_e2v1r/E2_cal.jsonl",
         "--ref", "tool", "--tag", "d15_tool3"])
    run(["src/evaluate.py", "--split", "dev", "--pred"] + [f"tool@{x}=work/eval/dev_{x}_q3e3r/E3_cal.jsonl" for x in CTX]
        + ["--ref", "tool@rules", "--tag", "d15_contexts2"])
    run(["src/evaluate.py", "--split", "dev", "--pred"]
        + [f"q3:{m}=work/eval/dev_rules_q3r/{m}.jsonl" for m in ("module2", "m2_one_question", "m2_no_gate", "m2_no_dsg", "m2_no_topical", "m2_no_ocr", "m2_no_logo", "E1", "E2", "E2_cal")]
        + ["q3:E3=work/eval/dev_rules_q3e3r/E3.jsonl", "q3:E3_cal=work/eval/dev_rules_q3e3r/E3_cal.jsonl",
           "q25:module2=work/eval/dev_rules_r/module2.jsonl", "q25:m2_one_question=work/eval/dev_rules_r/m2_one_question.jsonl",
           "q25:E2_cal_v1=work/eval/dev_rules_e2v1r/E2_cal.jsonl", "--ref", "q3:module2", "--tag", "methods_rules_q3r"])
    # 5. the measurement tool over the development photos (figures, analysis)
    run(["src/measure.py", "--split", "dev", "--photos", "r-items"])
    run(["src/measure.py", "--split", "dev", "--photos", "all"])
    run(["src/analyze.py", "--split", "dev"])
    print("chain done; new result files:")
    for pat in ("eval_dev_d15_tool3_*", "eval_dev_d15_contexts2_*", "eval_dev_methods_rules_q3r_*", "measure_dev_r-items_*", "measure_dev_all_*", "analyze_dev_*"):
        print(" ", latest(f"results/{pat}.json"))


if __name__ == "__main__":
    main()
