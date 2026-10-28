"""paper/ -> reports/paper_preview.html: a readable draft without LaTeX (no TeX on this PC, 10/20).

Reads main.tex's \\input order, expands the macros of paper/numbers.tex, turns sections, paragraphs, lists,
emphasis, citations (shown as [key]), references and the booktabs tables into HTML, and marks TODO comments. A
preview only: the typeset paper comes from the Springer Nature template before submission.

  python src/paper_preview.py
"""
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPER = ROOT / "paper"


def macros():
    m = {}
    for line in (PAPER / "numbers.tex").read_text(encoding="utf-8").splitlines():
        g = re.match(r"\\newcommand\{\\(\w+)\}\{(.*?)\}\s*(%.*)?$", line)
        if g:
            m[g.group(1)] = g.group(2).replace("{,}", ",")
    return m


def inline(s, mac):
    s = re.sub(r"\\(\w+)\{\}", lambda g: mac.get(g.group(1), "\\" + g.group(1) + "{}"), s)
    s = re.sub(r"\\(" + "|".join(sorted(mac, key=len, reverse=True)) + r")\b", lambda g: mac[g.group(1)], s) if mac else s
    s = html.escape(s, quote=False)
    s = re.sub(r"\\emph\{(.*?)\}", r"<em>\1</em>", s)
    s = re.sub(r"\\textbf\{(.*?)\}", r"<b>\1</b>", s)
    s = re.sub(r"\\cite\{(.*?)\}", lambda g: "<span class=cite>[" + g.group(1) + "]</span>", s)
    s = re.sub(r"(Section|Table|Figure)~\\ref\{(.*?)\}", r"\1 <span class=ref>\2</span>", s)
    s = re.sub(r"\\ref\{(.*?)\}", r"<span class=ref>\1</span>", s)
    s = re.sub(r"\\label\{.*?\}", "", s)
    s = re.sub(r"\$([^$]*)\$", r"<i>\1</i>", s)
    s = s.replace("---", "—").replace("--", "–").replace("~", " ").replace("\\%", "%").replace("\\&", "&").replace("\\,", " ")
    s = s.replace("``", "“").replace("''", "”").replace("\\c{t}", "ţ").replace("\\ ", " ")
    s = re.sub(r"\{\\scriptsize\s*(.*?)\}", r"<small>\1</small>", s)
    s = re.sub(r"\\quad\s*", "&nbsp;&nbsp;&nbsp;&nbsp;", s)
    s = re.sub(r"\\footnotesize\s*", "", s)
    s = s.replace("{,}", ",")
    return s


def table_html(tex, mac):
    cap = re.search(r"\\caption\{(.*)\}\\label\{(.*?)\}", tex)
    body = re.search(r"\\midrule\n(.*?)\n\\bottomrule", tex, re.S)
    head = re.search(r"\\toprule\n(.*?) \\\\\n\\midrule", tex, re.S)
    note = re.search(r"\\par\\smallskip\{\\footnotesize (.*?)\}", tex, re.S)
    if not body:
        pend = re.search(r"\\caption\{(.*?)\}", tex)
        return f"<div class=pending>（表格待測試集：{html.escape(pend.group(1)) if pend else ''}）</div>"
    rows = [r for r in body.group(1).split(" \\\\\n") if r.strip() and not r.strip().startswith("\\midrule")]
    th = "".join(f"<th>{inline(c.strip(), mac)}</th>" for c in head.group(1).split(" & ")) if head else ""
    tr = "".join("<tr>" + "".join(f"<td>{inline(c.strip(), mac)}</td>" for c in r.rstrip(" \\").split(" & ")) + "</tr>" for r in rows)
    out = f"<div class=tbl><p class=cap><b>{inline(cap.group(2), mac) if cap else ''}</b>　{inline(cap.group(1), mac) if cap else ''}</p><table><tr>{th}</tr>{tr}</table>"
    if note:
        out += f"<p class=note>{inline(note.group(1), mac)}</p>"
    return out + "</div>"


def section_html(tex, mac):
    out = []
    todo = []
    paras = []
    buf = []
    in_list = None
    for line in tex.splitlines():
        if line.strip().startswith("%"):
            if "TODO" in line:
                todo.append(line.strip("% ").strip())
            continue
        line = re.sub(r"(?<!\\)%.*$", "", line)
        if not line.strip():
            if buf:
                paras.append((in_list, " ".join(buf)))
                buf = []
            continue
        if re.match(r"\\(begin|end)\{(enumerate|description|itemize)\}", line.strip()):
            if buf:
                paras.append((in_list, " ".join(buf)))
                buf = []
            m = re.match(r"\\(begin|end)\{(\w+)\}", line.strip())
            in_list = m.group(2) if m.group(1) == "begin" else None
            paras.append(("list-" + m.group(1), m.group(2)))
            continue
        buf.append(line.strip())
    if buf:
        paras.append((in_list, " ".join(buf)))
    for kind, text in paras:
        if kind == "list-begin":
            out.append("<ol>" if text == "enumerate" else "<dl>" if text == "description" else "<ul>")
            continue
        if kind == "list-end":
            out.append("</ol>" if text == "enumerate" else "</dl>" if text == "description" else "</ul>")
            continue
        m = re.match(r"\\section\*?\{(.*?)\}", text)
        if m:
            out.append(f"<h2>{inline(m.group(1), mac)}</h2>")
            text = text[m.end():].strip()
            text = re.sub(r"^\\label\{.*?\}", "", text).strip()
            if not text:
                continue
        m = re.match(r"\\subsection\{(.*?)\}", text)
        if m:
            out.append(f"<h3>{inline(m.group(1), mac)}</h3>")
            text = re.sub(r"^\\label\{.*?\}", "", text[m.end():]).strip()
            if not text:
                continue
        if kind in ("enumerate", "itemize", "description"):
            items = re.split(r"\\item", text)
            for it in items:
                it = it.strip()
                if not it:
                    continue
                m = re.match(r"\[(.*?)\]\s*(.*)", it, re.S)
                if kind == "description" and m:
                    out.append(f"<dt>{inline(m.group(1), mac)}</dt><dd>{inline(m.group(2), mac)}</dd>")
                else:
                    out.append(f"<li>{inline(it, mac)}</li>")
            continue
        m = re.match(r"\\paragraph\{(.*?)\}\s*(.*)", text, re.S)
        if m:
            out.append(f"<p><b>{inline(m.group(1), mac)}</b> {inline(m.group(2), mac)}</p>")
        else:
            out.append(f"<p>{inline(text, mac)}</p>")
    if todo:
        out.append("<div class=todo>待辦：" + "；".join(html.escape(t) for t in todo) + "</div>")
    return "\n".join(out)


def main():
    mac = macros()
    main_tex = (PAPER / "main.tex").read_text(encoding="utf-8")
    title = re.search(r"\\title\{(.*?)\}", main_tex).group(1)
    parts = []
    for inc in re.findall(r"\\input\{(sections/[^}]+)\}", main_tex):
        p = PAPER / f"{inc}.tex"
        tex = p.read_text(encoding="utf-8") if p.exists() else ""
        if not tex.strip() or all(l.strip().startswith("%") or not l.strip() for l in tex.splitlines()):
            parts.append(f"<h2 class=pend>{html.escape(Path(inc).name)}</h2><div class=pending>（還沒寫）</div>")
            continue
        parts.append(section_html(tex, mac))
    tabs = []
    for inc in re.findall(r"\\input\{(tables/[^}]+)\}", main_tex):
        p = PAPER / f"{inc}.tex"
        if p.exists():
            tabs.append(table_html(p.read_text(encoding="utf-8"), mac))
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paper draft preview</title><style>
:root{{--bg:#fafafa;--fg:#222;--mut:#777;--line:#ddd;--box:#fff;--warn:#fff4e5}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1b1b;--fg:#eee;--mut:#999;--line:#444;--box:#262626;--warn:#3a2c14}}}}
body{{font-family:Georgia,"Noto Serif",serif;margin:0 auto;padding:24px 16px;background:var(--bg);color:var(--fg);max-width:820px;line-height:1.65;font-size:16px}}
h1{{font-size:24px;line-height:1.3}} h2{{font-size:20px;margin-top:34px}} h3{{font-size:17px;margin-top:22px}}
.cite{{color:var(--mut);font-size:13px}} .ref{{color:var(--mut);font-size:13px}} .todo{{background:var(--warn);border-left:4px solid #e8a33d;padding:6px 10px;font-size:13px;margin:12px 0}}
.pending{{color:var(--mut);font-style:italic}} .tbl{{margin:28px 0;overflow-x:auto}} table{{border-collapse:collapse;font-size:13px;font-family:system-ui,sans-serif}}
td,th{{border-bottom:1px solid var(--line);padding:3px 8px;text-align:left;white-space:nowrap}} th{{border-bottom:2px solid var(--fg)}}
.cap{{font-size:14px}} .note{{font-size:12px;color:var(--mut)}} .banner{{background:var(--warn);padding:8px 12px;font-size:14px;font-family:system-ui,sans-serif}}
dt{{font-weight:bold;margin-top:6px}} dd{{margin-left:1.2em}}</style></head><body>
<div class=banner>草稿預覽（10/20）：沒有排版、沒有參考文獻；[key] 是引用；表格和 Dev 數字目前是開發集，凍結後換測試集。正式版用 Springer Nature 範本。</div>
<h1>{inline(title, mac)}</h1><p class=cite>Yueh-Cheng Huang</p>
{''.join(parts)}
<h2>Tables</h2>{''.join(tabs)}
</body></html>"""
    out = ROOT / "reports" / "paper_preview.html"
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
