"""Number audit for the paper (NEXT 10/22, E15; CLAUDE.md rule 1): every numeric token typed into the .tex sources
must come from paper/numbers.tex (make_tables.py macros), from configs/default.yaml (method constants), or be a
structural number (years, section/figure numbers, model sizes). Everything else is listed for a human to check.

  python src/audit_numbers.py            # prints the list; exit code 1 when the list is not empty
  python src/audit_numbers.py --json     # also writes results/audit_numbers_{stamp}.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from annotate_bcd import write_result  # noqa: E402

FILES = [ROOT / "paper" / "main.tex"] + sorted((ROOT / "paper" / "sections").glob("*.tex")) + sorted((ROOT / "paper" / "figs").glob("*.tex"))
# numbers that are structure, not results: years, calendar, section and table numbers, model sizes, hardware
STRUCTURAL = {"2023", "2024", "2025", "2026", "2027", "113", "114", "1911", "7", "8", "7B", "8B", "48", "150", "250", "20", "16", "1", "2",
              "3", "4", "5", "6", "9", "10", "12", "0", "100", "30", "120", "200", "300", "400", "1024", "0c351dd", "2048", "28"}
NUM = re.compile(r"(?<![A-Za-z\\{])(?:\d{1,3}(?:[,{}]*\d{3})+|\d+(?:\.\d+)?)(?![\w}])")


def macro_values():
    vals = set()
    for line in (ROOT / "paper" / "numbers.tex").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\\newcommand\{\\\w+\}\{(.*?)\}\s*%", line)
        if m:
            for n in re.findall(r"\d+(?:[,{}]*\d{3})*(?:\.\d+)?", m.group(1)):
                vals.add(n.replace("{,}", ",")); vals.add(n.replace("{,}", "").replace(",", ""))
    return vals


def config_values():
    vals = set()
    for line in (ROOT / "configs" / "default.yaml").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0]
        for n in re.findall(r"\d+(?:\.\d+)?", line):
            vals.add(n)
    return vals


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    ok = macro_values() | config_values()
    found = []
    for f in FILES:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.replace("\\%", " ").split("%")[0]                                      # \% is a percent sign, not a comment
            code = re.sub(r"\\(?:ref|cite|label|input|includegraphics|url|href)\{[^}]*\}", "", code)   # labels, keys and URLs (ORCID) are not numbers
            code = re.sub(r"\\[A-Za-z]+\{\}", "", code)                                         # macros carry their own numbers
            for m in NUM.finditer(code):
                after = code[m.end():m.end() + 2]
                if (m.start() and code[m.start() - 1] == "!") or re.match(r"(mm|pt|cm|px|vh|em)", after):
                    continue                                                                     # TikZ / CSS lengths and colours
                tok = m.group(0).replace("{,}", ",")
                plain = tok.replace(",", "")
                if tok in STRUCTURAL or plain in STRUCTURAL or tok in ok or plain in ok:
                    continue
                found.append({"file": str(f.relative_to(ROOT)), "line": i, "number": tok, "text": code.strip()[:110]})
    for r in found:
        print(f"{r['file']}:{r['line']}: {r['number']}  |  {r['text']}")
    print(f"{len(found)} typed numbers without a source" if found else "every number comes from numbers.tex, configs or structure")
    if args.json:
        write_result("audit_numbers", {"files": [str(f.relative_to(ROOT)) for f in FILES], "unsourced": found, "count": len(found)},
                     [ROOT / "paper" / "numbers.tex", ROOT / "configs" / "default.yaml"])
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
