"""Wall-clock throughput of the measurement tool's depiction step (NEXT 10/22, A7): run the few-shot direct judgement
(baselines_evidence.py E3, Qwen3-VL) on the development R photos with its cache set aside, time it, write
results/throughput_e3_dev_{stamp}.json, then put the original cache back (the fresh outputs are kept beside it).
Development split only (rule 3)."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from annotate_bcd import write_result  # noqa: E402

CACHE = ROOT / "work" / "evidence" / "e3_fewshot.jsonl"


def main():
    bak = CACHE.with_suffix(".jsonl.before_timing")
    if CACHE.exists():
        CACHE.rename(bak)
    t0 = time.time()
    cmd = [sys.executable, str(ROOT / "src" / "baselines_evidence.py"), "--r-items", "dev", "--labels", "annotations/dev/R.jsonl",
           "--context", "rules", "--only", "e3", "--tag", "dev_ctx_rules_timing_e3"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    secs = time.time() - t0
    fresh = CACHE.with_suffix(".jsonl.timing_run")
    n_new = 0
    if CACHE.exists():
        n_new = sum(1 for l in CACHE.read_text(encoding="utf-8").splitlines() if l.strip())
        CACHE.rename(fresh)
    if bak.exists():
        bak.rename(CACHE)
    tail = (r.stdout or "")[-1500:]
    photos = None
    for line in tail.splitlines():
        if '"photos"' in line:
            try:
                photos = json.loads(line).get("photos")
            except Exception:
                pass
    out = {"split": "dev", "step": "E3 few-shot direct judgement (Qwen3-VL-8B), rule-based context, cache set aside",
           "command": " ".join(cmd[1:]), "returncode": r.returncode, "wall_seconds": round(secs, 1),
           "cache_rows_written": n_new, "photos_reported": photos, "includes_model_load": True,
           "stdout_tail": tail[-600:], "stderr_tail": (r.stderr or "")[-600:]}
    write_result("throughput_e3_dev", out, [ROOT / "annotations" / "dev" / "R.jsonl"])
    print(json.dumps({k: out[k] for k in ("returncode", "wall_seconds", "cache_rows_written", "photos_reported")}))


if __name__ == "__main__":
    main()
