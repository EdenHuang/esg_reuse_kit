"""Build paper/main.pdf with the Springer Nature template (sn-jnl.cls) and Tectonic (work/tools/tectonic/; no TeX
distribution on this PC, 10/20). Runs make_tables.py first so every table and number comes from results/.

  python src/paper_build.py          # -> paper/main.pdf, then reports/paper_main.pdf (for Eden)
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TECTONIC = ROOT / "work" / "tools" / "tectonic" / "tectonic.exe"


def main():
    if not TECTONIC.exists():
        sys.exit(f"沒有 {TECTONIC}")
    subprocess.run([sys.executable, str(ROOT / "src" / "make_tables.py")], check=True, cwd=ROOT)
    subprocess.run([sys.executable, str(ROOT / "src" / "make_figs.py"), "draw"], check=True, cwd=ROOT)   # figures 2-4 (10/21)
    r = subprocess.run([str(TECTONIC), "-X", "compile", "--keep-logs", "--keep-intermediates", "main.tex"],
                       cwd=ROOT / "paper", capture_output=True, text=True, encoding="utf-8", errors="replace")
    log = ROOT / "paper" / "main.log"
    tail = (r.stderr or "")[-3000:]
    if r.returncode:
        print(tail)
        if log.exists():
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            errs = [l for l in lines if l.startswith("!") or "Error" in l]
            print("\n".join(errs[:20]))
        sys.exit(f"tectonic 失敗（{r.returncode}）")
    warn = [l for l in tail.splitlines() if "warning" in l.lower()]
    pdf = ROOT / "paper" / "main.pdf"
    out = ROOT / "reports" / "paper_main.pdf"
    shutil.copy(pdf, out)
    print(f"wrote {pdf} ({pdf.stat().st_size / 1e6:.1f} MB) and {out}；警告 {len(warn)} 行")


if __name__ == "__main__":
    main()
