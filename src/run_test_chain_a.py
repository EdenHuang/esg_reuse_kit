"""Test split, part A (after configs/frozen.yaml, 10/27): the labelled R test photos with the rule-based context,
three VLM families, nothing tuned (tune_evidence --apply frozen). Part B (the other contexts, Table 4) follows once the
learned pairing model can be applied to the test split. Every model output is cached (rule 9).

  python src/run_test_chain_a.py            # log: work/run_test_chain_a.log
"""
import glob
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
LOG = ROOT / "work" / "run_test_chain_a.log"
LABELS = "annotations/test/R.jsonl"
R2 = "annotations/test/R2.jsonl"


def latest(pattern):
    fs = sorted(glob.glob(str(ROOT / pattern)))
    if not fs:
        sys.exit(f"沒有 {pattern}")
    return str(Path(fs[-1]).relative_to(ROOT)).replace("\\", "/")


def run(args):
    cmd = [PY] + args
    t0 = time.time()
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n$ {' '.join(args)}\n")
        f.flush()
        r = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        f.write(f"[exit {r.returncode}, {time.time() - t0:.0f} s]\n")
    print(f"{args[0].split('/')[-1]} {args[-1]}: exit {r.returncode}, {time.time() - t0:.0f} s", flush=True)
    if r.returncode:
        sys.exit(f"失敗：{' '.join(args)}（看 {LOG}）")


def main():
    if not (ROOT / "configs" / "frozen.yaml").exists():
        sys.exit("沒有 configs/frozen.yaml（鐵則 3）")
    LOG.write_text("", encoding="utf-8")
    base = ["src/evidence.py", "--r-items", "test", "--labels", LABELS, "--labelled-only", "--holistic"]
    # 1. module-2 evidence (anchors inside), rules context: main VLM and the earlier VLM
    run(base + ["--context", "rules", "--tag", "test_ctx_rules_q3r"])
    run(["src/evidence.py", "--vlm", "alt"] + base[1:] + ["--context", "rules", "--tag", "test_ctx_rules_r"])
    # 2. compared approaches, rules context: CLIP, zero-shot, few-shot (frozen development examples)
    bb = ["src/baselines_evidence.py", "--r-items", "test", "--labels", LABELS, "--context", "rules"]
    run(bb + ["--only", "e1", "e2", "e3", "--tag", "test_ctx_rules_q3e3"])
    run(bb + ["--vlm", "alt", "--only", "e1", "e2", "--tag", "test_ctx_rules_r"])
    run(bb + ["--vlm", "alt", "--only", "e2", "--prompt", "direct_zeroshot_v1", "--tag", "test_ctx_rules_e2v1"])
    run(bb + ["--vlm", "third", "--only", "e2", "e3", "--tag", "test_ctx_rules_q3ivl"])
    # 3. frozen cuts applied (no tuning), one prediction folder per context name
    q3 = latest("results/baselines_evidence_test_ctx_rules_q3e3_2026*.json")
    run(["src/tune_evidence.py", "--split", "test", "--apply", "frozen", "--evidence", "work/evidence/evidence_test_ctx_rules_q3r.jsonl",
         "--baselines", q3, "--context", "rules_q3r"])
    run(["src/tune_evidence.py", "--split", "test", "--apply", "frozen", "--evidence", "work/evidence/evidence_test_ctx_rules_q3r.jsonl",
         "--baselines", q3, "--context", "rules_q3e3r"])
    run(["src/tune_evidence.py", "--split", "test", "--apply", "frozen", "--evidence", "work/evidence/evidence_test_ctx_rules_r.jsonl",
         "--baselines", latest("results/baselines_evidence_test_ctx_rules_r_2026*.json"), "--context", "rules_r"])
    run(["src/tune_evidence.py", "--split", "test", "--apply", "frozen", "--evidence", "work/evidence/evidence_test_ctx_rules_r.jsonl",
         "--baselines", latest("results/baselines_evidence_test_ctx_rules_e2v1_2026*.json"), "--context", "rules_e2v1r"])
    run(["src/tune_evidence.py", "--split", "test", "--apply", "frozen", "--evidence", "work/evidence/evidence_test_ctx_rules_q3r.jsonl",
         "--baselines", latest("results/baselines_evidence_test_ctx_rules_q3ivl_2026*.json"), "--context", "rules_q3ivl"])
    # 4. scores on the test split (firm-level bootstrap), with the second annotator
    run(["src/evaluate.py", "--split", "test", "--pred",
         "tool=work/eval/test_rules_q3e3r/E3_cal.jsonl", "tool_no_ocr=work/eval/test_rules_q3e3r/E3_cal_no_ocr.jsonl",
         "tool_no_logo=work/eval/test_rules_q3e3r/E3_cal_no_logo.jsonl", "E3_uncal=work/eval/test_rules_q3e3r/E3.jsonl",
         "module2=work/eval/test_rules_q3r/module2.jsonl", "E2_cal=work/eval/test_rules_q3r/E2_cal.jsonl", "E1=work/eval/test_rules_q3r/E1.jsonl",
         "q25_module2=work/eval/test_rules_r/module2.jsonl", "q25_E2_cal=work/eval/test_rules_e2v1r/E2_cal.jsonl",
         "--ref", "tool", "--second-labels", R2, "--tag", "test_tool"])
    run(["src/evaluate.py", "--split", "test", "--pred"]
        + [f"q3:{m}=work/eval/test_rules_q3r/{m}.jsonl" for m in ("module2", "m2_one_question", "m2_no_gate", "m2_no_dsg", "m2_no_topical", "m2_no_ocr", "m2_no_logo", "E1", "E2", "E2_cal")]
        + ["q3:E3=work/eval/test_rules_q3e3r/E3.jsonl", "q3:E3_cal=work/eval/test_rules_q3e3r/E3_cal.jsonl",
           "q25:module2=work/eval/test_rules_r/module2.jsonl", "q25:m2_one_question=work/eval/test_rules_r/m2_one_question.jsonl",
           "q25:E2_cal_v1=work/eval/test_rules_e2v1r/E2_cal.jsonl", "--ref", "q3:module2", "--tag", "test_methods"])
    run(["src/evaluate.py", "--split", "test", "--pred", "tool=work/eval/test_rules_q3e3r/E3_cal.jsonl",
         "ivl:E3_cal=work/eval/test_rules_q3ivl/E3_cal.jsonl", "ivl:E3=work/eval/test_rules_q3ivl/E3.jsonl",
         "ivl:E2_cal=work/eval/test_rules_q3ivl/E2_cal.jsonl", "ivl:E2=work/eval/test_rules_q3ivl/E2.jsonl",
         "--ref", "tool", "--tag", "test_third_vlm"])
    print("part A done:")
    for pat in ("eval_test_test_tool_*", "eval_test_test_methods_*", "eval_test_test_third_vlm_*"):
        print(" ", latest(f"results/{pat}.json"))


if __name__ == "__main__":
    main()
