"""Build one self-contained HTML report of the picture-text pairing run.

Sections: what was run and the counts, the judged samples, every distinct picture with
the text the script paired with it, and the layout elements left out of the analysis.
Thumbnails are embedded (halved crops). The output follows the claude.ai artifact page
contract (no <html>/<head>/<body> tags).
Usage: python -I make_report.py --outputs outputs --judgments claude_judgments.json \
           --html outputs/report_artifact.html
"""
import argparse
import base64
import collections
import html
import json
from pathlib import Path

import pymupdf

REPORT_ZH = {"grapeking": "葡萄王", "tcc": "台泥"}
PAGES = {"grapeking": 155, "tcc": 235}  # page counts of the two PDFs (pdfinfo)
POS_ZH = {"below": "圖下方", "above": "圖上方", "left": "圖左側", "right": "圖右側"}
ROLE_CLASS = {"證明圖": "evid", "形象圖": "face", "裝飾圖": "deco", "說明圖": "expl", "不分析": "none"}


def thumb_b64(path):
    pix = pymupdf.Pixmap(str(path))
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    pix.shrink(1)  # halve both sides
    return base64.b64encode(pix.tobytes("jpg", jpg_quality=70)).decode()


def esc(s, n=None):
    s = s or ""
    if n and len(s) > n:
        s = s[:n] + "…"
    return html.escape(s)


def box_iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def role_key(role):
    for k in ROLE_CLASS:
        if role.startswith(k):
            return k
    return "不分析"


def text_rows(r):
    para = r["paragraphs"][0]["text"] if r["paragraphs"] else ""
    cap = r["caption"]
    if cap and r["caption_pos"]:
        cap += f"（{POS_ZH.get(r['caption_pos'], r['caption_pos'])}）"
    rows = [("圖說", cap), ("小標題", r["local_heading"]), ("圖上文字", r["overlay_text"]), ("最近段落", para)]
    return "".join(
        f"<div class='kv'><dt>{k}</dt><dd>{esc(v, 140) if v else '<span class=\"nil\">沒抓到</span>'}</dd></div>"
        for k, v in rows)


def repeat_note(r):
    pages = "、".join(str(p) for p in r["dup_pages"])
    role = r["doc_role"]
    if role == "reused":
        return f"<p class='dup'>同一張圖在第 {pages} 頁都出現</p>"
    if role == "same_page_repeat":
        return f"<p class='dup'>同一頁裡畫了 {r['dup_count']} 次，這裡只列一次</p>"
    if role == "layout_repeat":
        return f"<p class='dup'>出現在 {len(r['dup_pages'])} 頁：第 {pages} 頁</p>"
    return ""


def card(r, crops, j=None, show_text=True):
    where = f"{REPORT_ZH.get(r['report'], r['report'])}・第 {r['page']} 頁"
    if r["full_page"]:
        where += "・<span class='flag'>整頁圖</span>"
    verdict = ""
    if j:
        rk = role_key(j["role"])
        ok_cls = {"對": "ok", "部分": "part", "錯": "bad"}.get(j["text_ok"], "part")
        verdict = (
            "<div class='verdict'>"
            f"<div class='chips'><span class='chip role-{ROLE_CLASS[rk]}'>{esc(j['role'])}</span>"
            f"<span class='chip txt-{ok_cls}'>文字配對：{esc(j['text_ok'])}</span></div>"
            f"<div class='kv'><dt>類型</dt><dd>{esc(j['type'])}</dd></div>"
            f"<div class='kv'><dt>關係</dt><dd>{esc(j['relation'])}</dd></div>"
            + (f"<div class='kv'><dt>備註</dt><dd>{esc(j['note'])}</dd></div>" if j["note"] else "")
            + "</div>")
    found = f"<dl class='found'>{text_rows(r)}</dl>" if show_text else ""
    return (f"<article class='card'><img loading='lazy' alt='{esc(where)} 的圖' "
            f"src='data:image/jpeg;base64,{thumb_b64(crops / r['crop'])}'>"
            f"<p class='where'>{where}</p>{repeat_note(r)}{verdict}{found}</article>")


STYLE = """
<title>報告書圖文配對試跑</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;600;700&family=Noto+Serif+TC:wght@600;700&display=swap">
<style>
/* Layout: one reading column for the summary, then wrapping card grids; cards are the only boxed objects */
:root{
  --bg:#f5f6f8; --surface:#ffffff; --thumb:#eceef2; --fg:#1a2029; --muted:#5b6471; --line:#dbe0e6;
  --accent:#2a4d78;
  --evid:#2c7650; --face:#2b5c98; --deco:#a3611a; --expl:#6a4ea0; --none:#6c7480;
  --ok:#2c7650; --part:#946613; --bad:#b03a3a;
  --font-display:"Noto Serif TC","Songti TC","PMingLiU",serif;
  --font-body:"Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#13171c; --surface:#1b2027; --thumb:#252b33; --fg:#e6e9ee; --muted:#9aa3ae; --line:#2e353e;
  --accent:#8fb3df; --evid:#6cc196; --face:#86ade3; --deco:#e0a35e; --expl:#b59be4; --none:#9aa3ae;
  --ok:#6cc196; --part:#dbb25a; --bad:#ec8a8a; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#13171c; --surface:#1b2027; --thumb:#252b33; --fg:#e6e9ee; --muted:#9aa3ae; --line:#2e353e;
  --accent:#8fb3df; --evid:#6cc196; --face:#86ade3; --deco:#e0a35e; --expl:#b59be4; --none:#9aa3ae;
  --ok:#6cc196; --part:#dbb25a; --bad:#ec8a8a; color-scheme:dark}
body{background:var(--bg);color:var(--fg);font-family:var(--font-body);font-size:15px;line-height:1.65}
.wrap{max-width:1180px;margin:0 auto;padding-left:max(16px,env(safe-area-inset-left,0px));
  padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:28px 56px;display:grid;gap:36px}
h1,h2{font-family:var(--font-display);text-wrap:balance;margin:0;line-height:1.3}
h1{font-size:28px}h2{font-size:21px}h3{font-size:16px;margin:0}
.lead{color:var(--muted);max-width:65ch;margin:6px 0 0}
section{display:grid;gap:14px}
.prose{max-width:68ch;display:grid;gap:10px}.prose p,.prose ol,.prose ul{margin:0}
.prose li+li{margin-top:4px}
nav{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:14px}
nav a{color:var(--accent);text-decoration:none;border-bottom:1px solid var(--line)}
nav a:focus-visible,nav a:hover{border-bottom-color:var(--accent)}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;font-variant-numeric:tabular-nums;min-width:420px}
caption{text-align:left;font-weight:600;padding-bottom:6px;color:var(--fg)}
th,td{padding:7px 14px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
thead th{color:var(--muted);font-weight:600;font-size:13px}
.fix{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.fix div{border-top:2px solid var(--accent);padding-top:8px;min-width:0}
.fix p{margin:4px 0 0;color:var(--fg)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:10px;
  display:grid;gap:8px;align-content:start;min-width:0}
.card img{width:100%;height:190px;object-fit:contain;background:var(--thumb);border-radius:3px;display:block}
.where{margin:0;font-size:13px;color:var(--muted)}
.flag{color:var(--deco);font-weight:600}
.dup{margin:0;font-size:12.5px;color:var(--deco);font-weight:600}
dl{margin:0;display:grid;gap:3px}
.kv{display:grid;grid-template-columns:4.6em 1fr;gap:6px;font-size:13px;line-height:1.55}
.kv dt{color:var(--muted);font-weight:600}.kv dd{margin:0;min-width:0;overflow-wrap:anywhere}
.nil{color:var(--muted);opacity:.75}
.verdict{display:grid;gap:4px;padding:8px;border:1px solid var(--line);border-radius:4px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:12.5px;font-weight:700;padding:1px 8px;border-radius:999px;border:1.5px solid currentColor}
.role-evid{color:var(--evid)}.role-face{color:var(--face)}.role-deco{color:var(--deco)}
.role-expl{color:var(--expl)}.role-none{color:var(--none)}
.txt-ok{color:var(--ok)}.txt-part{color:var(--part)}.txt-bad{color:var(--bad)}
.legend{display:grid;gap:4px;font-size:14px;max-width:68ch}.legend p{margin:0}
@media (prefers-reduced-motion: reduce){*{scroll-behavior:auto}}
</style>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs", required=True)
    ap.add_argument("--judgments", default="claude_judgments.json")
    ap.add_argument("--html", required=True)
    args = ap.parse_args()
    out = Path(args.outputs)
    crops = out / "crops"

    by_report = {}
    for f in sorted(out.glob("*_pairs.jsonl")):
        recs = [json.loads(l) for l in open(f, encoding="utf-8")]
        if recs:
            by_report[recs[0]["report"]] = recs
    all_recs = [r for rs in by_report.values() for r in rs]

    def analysed(rs):  # one card per distinct picture, layout elements left out
        return [r for r in rs if r["representative"] and r["doc_role"] != "layout_repeat"]

    def layout(rs):
        return [r for r in rs if r["representative"] and r["doc_role"] == "layout_repeat"]

    judgments = json.load(open(args.judgments, encoding="utf-8"))
    for j in judgments:  # match by page and position, since crop numbering can change between runs
        cands = [r for r in all_recs if r["report"] == j["report"] and r["page"] == j["page"]]
        j["_rec"] = max(cands, key=lambda r: box_iou(r["bbox"], j["bbox"]), default=None)

    def count(rs, pred):
        return sum(1 for r in rs if pred(r))

    flow_rows, text_rows_html = [], []
    for key, rs in by_report.items():
        lay = layout(rs)
        an = analysed(rs)
        flow_rows.append(
            f"<tr><td>{REPORT_ZH.get(key, key)}</td><td>{PAGES.get(key, '')}</td>"
            f"<td>{sum(len(r['layers']) for r in rs)}</td><td>{len(rs)}</td>"
            f"<td>{count(rs, lambda r: r['representative'])}</td>"
            f"<td>{len(lay)} 組 / {sum(r['dup_count'] for r in lay)} 處</td><td>{len(an)}</td></tr>")
        text_rows_html.append(
            f"<tr><td>{REPORT_ZH.get(key, key)}</td><td>{len(an)}</td>"
            f"<td>{count(an, lambda r: r['caption'])}</td><td>{count(an, lambda r: r['local_heading'])}</td>"
            f"<td>{count(an, lambda r: r['overlay_text'])}</td><td>{count(an, lambda r: r['paragraphs'])}</td>"
            f"<td>{count(an, lambda r: r['full_page'])}</td></tr>")

    raw_total = sum(len(r["layers"]) for r in all_recs)
    an_total = sum(len(analysed(rs)) for rs in by_report.values())
    ok = collections.Counter(j["text_ok"] for j in judgments)
    roles = collections.Counter(role_key(j["role"]) for j in judgments)
    role_txt = "、".join(f"{k} {roles[k]} 張" for k in ["證明圖", "形象圖", "裝飾圖", "說明圖", "不分析"] if roles[k])

    parts = [STYLE, "<main class='wrap'>"]
    parts.append(
        "<header><h1>報告書圖文配對試跑</h1>"
        "<p class='lead'>用葡萄王 2025 永續報告書和台泥 2025 年度永續報告書，測試程式能不能把報告書裡的圖抓出來，"
        f"並找到每張圖旁邊的文字。兩本原本抓到 {raw_total} 個圖片位置，合併圖層、去掉重複和版面元素後，"
        f"剩 {an_total} 張不同的圖列入分析。</p></header>"
        "<nav><a href='#summary'>結果摘要</a><a href='#samples'>抽樣 12 張的判斷</a>"
        + "".join(f"<a href='#{k}'>{REPORT_ZH.get(k, k)} {len(analysed(rs))} 張</a>" for k, rs in by_report.items())
        + "<a href='#layout'>版面元素（不分析）</a></nav>")

    parts.append(
        "<section id='summary'><h2>結果摘要</h2>"
        "<div class='prose'><p>程式做四件事：</p><ol>"
        "<li>找出每一頁上畫出來的圖，濾掉短邊小於 150 像素、或在版面上小於 1 英寸的小圖示和 logo。</li>"
        "<li>同一頁上重疊超過六成的圖（同一張圖錯開畫好幾次、照片上疊漸層或遮罩）合併成一張；"
        "同一張圖出現在好幾頁的歸成一組，出現 3 頁以上的當成版面元素（例如章節背景），不列入分析。</li>"
        "<li>把讀者看到的範圍裁下來（包含裁切、遮罩和壓在圖上的字）。</li>"
        "<li>找出圖旁邊的四種文字：圖說、圖上方的小標題、壓在圖上的字、最近的一段內文。每頁都重複出現的導覽列和頁首頁尾先排除。</li>"
        "</ol></div>"
        "<div class='tablewrap'><table><caption>圖的數量：從抓到到列入分析</caption><thead><tr><th>報告</th><th>頁數</th>"
        "<th>原始圖片位置</th><th>合併圖層後</th><th>不同的圖</th><th>版面元素（排除）</th><th>列入分析</th></tr></thead>"
        f"<tbody>{''.join(flow_rows)}</tbody></table></div>"
        "<div class='tablewrap'><table><caption>列入分析的圖，有抓到哪種文字</caption><thead><tr><th>報告</th>"
        "<th>圖</th><th>有圖說</th><th>有小標題</th><th>有圖上文字</th><th>有最近段落</th><th>整頁圖</th></tr></thead>"
        f"<tbody>{''.join(text_rows_html)}</tbody></table></div>"
        "<div class='prose'>"
        f"<p><b>抽樣 12 張由 Claude 看圖判斷。</b>文字配對：對 {ok['對']} 張、部分對 {ok['部分']} 張、錯 {ok['錯']} 張。"
        f"角色：{role_txt}。最可靠的是圖上方的小標題；圖說偶爾會抓到別段文字的片段；最近的段落常常在講別的事。</p></div>"
        "<div class='fix'>"
        "<div><h3>要修的地方一：一半不是照片</h3><p>12 張裡有 6 張不是照片：2 張封面、版面底圖、資訊圖表、獎項圖、插畫各 1 張。"
        "分析前要先分出是不是照片，這就是標註第一欄「圖片類型」要做的事；封面可以直接用頁碼排除。</p></div>"
        "<div><h3>要修的地方二：「看得出是這家公司」太嚴</h3><p>台泥徵才說明會那張明顯是現場拍的，但畫面裡沒有公司標誌，"
        "照現在的規則只能算說明圖。建議第一欄改成「現場照」（看得出是在某個場合實際拍的）和「情境照」（看不出時間地點，像圖庫照片）。</p></div>"
        "</div>"
        "<div class='legend'><p><b>卡片上的四種文字</b></p>"
        "<p>圖說：緊貼在圖下方、上方或左右兩側的短文字。小標題：圖上方同一欄裡最近的標題。"
        "圖上文字：印在圖範圍裡的字。最近段落：同一欄裡離圖最近的一段內文。顯示「沒抓到」代表程式沒找到這種文字。"
        "橘色字標示這張圖在報告裡重複出現的情形。</p></div>"
        "</section>")

    parts.append("<section id='samples'><h2>抽樣 12 張的判斷</h2>"
                 "<p class='lead'>每張卡片上面是 Claude 看圖後的判斷（角色、類型、跟旁邊文字的關係），下面是程式抓到的文字。"
                 "這是 AI 判斷，不是標準答案。</p><div class='grid'>")
    parts += [card(j["_rec"], crops, j) for j in judgments if j["_rec"] is not None]
    parts.append("</div></section>")

    for key, rs in by_report.items():
        an = analysed(rs)
        parts.append(f"<section id='{key}'><h2>{REPORT_ZH.get(key, key)}：列入分析的 {len(an)} 張</h2>"
                     "<p class='lead'>依頁碼排列，每張不同的圖只列一次。只有程式抓到的文字，沒有判斷。</p><div class='grid'>")
        parts += [card(r, crops) for r in an]
        parts.append("</div></section>")

    lay_all = [r for rs in by_report.values() for r in layout(rs)]
    parts.append("<section id='layout'><h2>版面元素（不列入分析）</h2>"
                 "<p class='lead'>同一張圖出現在 3 頁以上，多半是章節背景或固定的版面圖。每組只列一張，下面寫出它出現的頁數。</p>"
                 "<div class='grid'>")
    parts += [card(r, crops, show_text=False) for r in lay_all]
    parts.append("</div></section></main>")
    Path(args.html).write_text("".join(parts), encoding="utf-8")
    print("wrote", args.html, round(Path(args.html).stat().st_size / 1e6, 1), "MB")


if __name__ == "__main__":
    main()
