"""Test split, part B (after run_test_chain_a.py): the other context sources for Table 4's extrinsic comparison
(evidence + few-shot per context, frozen cuts, scores), then the corpus: the measurement tool over the labelled test
photos and over every test photo, section mapping, the corpus analysis (Table 8) and the figure examples. GPU-heavy;
every model output is cached (rule 9). Log: work/run_test_chain_b.log.

  python src/run_test_chain_b.py            # all of it
  python src/run_test_chain_b.py --skip-contexts   # corpus only
"""
import argparse
import glob
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
LOG = ROOT / "work" / "run_test_chain_b.log"
CTX = {"M_lr": "work/context/M_lr_test340.jsonl", "L1_nearest": "work/context/L1_nearest_test340.jsonl",
       "L2_column": "work/context/L2_column_test340.jsonl", "L3_caption": "work/context/L3_caption_test340.jsonl",
       "L4_heading": "work/context/L4_heading_test340.jsonl", "gold": "work/context/gold_test340.jsonl"}
LABELS = "annotations/test/R.jsonl"


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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-contexts", action="store_true")
    args = ap.parse_args()
    if not (ROOT / "configs" / "frozen.yaml").exists():
        sys.exit("沒有 configs/frozen.yaml（鐵則 3）")
    LOG.write_text("", encoding="utf-8")
    if not args.skip_contexts:
        base = ["src/evidence.py", "--r-items", "test", "--labels", LABELS, "--labelled-only", "--holistic"]
        for x, ctx in CTX.items():
            run(base + ["--context", ctx, "--tag", f"test_ctx_{x}_q3r"])
            run(["src/baselines_evidence.py", "--r-items", "test", "--labels", LABELS, "--context", ctx, "--only", "e3", "--tag", f"test_ctx_{x}_q3e3"])
            run(["src/tune_evidence.py", "--split", "test", "--apply", "frozen", "--evidence", f"work/evidence/evidence_test_ctx_{x}_q3r.jsonl",
                 "--baselines", latest(f"results/baselines_evidence_test_ctx_{x}_q3e3_2026*.json"), "--context", f"{x}_q3e3r"])
        run(["src/evaluate.py", "--split", "test", "--pred", "tool@rules=work/eval/test_rules_q3e3r/E3_cal.jsonl"]
            + [f"tool@{x}=work/eval/test_{x}_q3e3r/E3_cal.jsonl" for x in CTX] + ["--ref", "tool@rules", "--tag", "test_contexts"])
    # the corpus
    run(["src/measure.py", "--split", "test", "--photos", "r-items"])
    run(["src/measure.py", "--split", "test", "--photos", "all"])
    run(["src/section_map.py", "run", "--split", "test"])
    run(["src/analyze.py", "--split", "test"])
    print("part B done:")
    for pat in ("eval_test_test_contexts_*", "measure_test_r-items_*", "measure_test_all_*", "section_map_test_*", "analyze_test_*"):
        fs = sorted(glob.glob(str(ROOT / f"results/{pat}.json")))
        print(" ", fs[-1] if fs else f"(no {pat})")


if __name__ == "__main__":
    main()
