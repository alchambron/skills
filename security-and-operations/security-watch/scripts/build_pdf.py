#!/usr/bin/env python3
"""Turn a run directory's report-en.html / report-fr.html into print PDFs.

Usage: python3 -I build_pdf.py <run-dir>

<run-dir>/meta.json supplies the print header and footer, for example:
{
  "author": "Alexis Chambron",
  "rev": 1,
  "en": {"period": "15 – 22 Oct 2026", "issued": "22 Oct 2026",
         "pdf": "EnerZam-Security-Watch-2026-10-22.pdf"},
  "fr": {"period": "15 – 22 oct. 2026", "issued": "22 oct. 2026",
         "pdf": "EnerZam-Veille-securite-2026-10-22.pdf"}
}
A language whose report-<lang>.html is missing is skipped.
"""
import json
import re
import os
import subprocess
import sys

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

LABELS = {
    "en": dict(internal="Internal", title="Weekly security watch: JouleMV &amp; WebSynco",
               foot="Security watch", period="Period", issued="Issued", rev="rev.",
               by="Prepared by", page='"Page " counter(page) " of " counter(pages)'),
    "fr": dict(internal="Interne", title="Veille sécurité hebdomadaire : JouleMV et WebSynco",
               foot="Veille sécurité", period="Période", issued="Émis le", rev="rév.",
               by="Préparé par", page='"Page " counter(page) " de " counter(pages)'),
}

# Light, print-only theme. The HTML reports rely on T3's injected CSS variables,
# which do not exist when Chrome prints the file, so they are defined here.
PRINT_CSS = """
  :root { --background:#fff; --foreground:#0f172a; --muted-foreground:#5b6475; --card:#f8fafc; --border:#e2e8f0;
    --secondary:#eef1f5; --code-background:#eef1f5; --code-foreground:#1e293b;
    --success:#15803d; --warning:#b45309; --destructive:#b91c1c; --info:#1d4ed8; --accent:#4f46e5; --radius:8px;
    --font-sans:-apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif; --font-mono:"SF Mono", Menlo, Consolas, monospace; }
  @page { size: Letter; margin: 14mm 13mm 16mm;
    @bottom-left { content: "FOOT"; font: 8pt -apple-system, Helvetica, sans-serif; color: #5b6475; }
    @bottom-right { content: PAGE; font: 8pt -apple-system, Helvetica, sans-serif; color: #5b6475; } }
  html, body { background:#fff; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body { font-size: 12.5px; }
  tr, .card .finding, ol.actions li, .explain, .verdict, .tile, .method { break-inside: avoid; }
  thead { display: table-header-group; } h2 { break-after: avoid; }
  .page-start { break-before: page; } .verdict .kicker { display: none; }
  .finding { font-size: 11.5px; } .finding .d { font-size: 11px; } .card { gap: 8px; } .finding .mono { font-size: 10.5px; }
  .sources { columns: 1 !important; } .sources a { color: #1d4ed8; text-decoration: none; }
  .sources a::after { content: " — " attr(href); color: #5b6475; font-family: var(--font-mono); font-size: 9.5px; word-break: break-all; }
  .doc-head { display:flex; justify-content:space-between; align-items:flex-end; border-bottom:2px solid var(--foreground); padding-bottom:8px; margin-bottom:-8px; }
  .doc-head .title { font-size:20px; font-weight:700; }
  .doc-head .meta { font-size:11px; color:var(--muted-foreground); text-align:right; line-height:1.5; }
"""


def french_spacing(body):
    # Non-breaking space before French high punctuation and inside guillemets,
    # so a line never starts with ":" or ";".
    for a, b in ((" :", " :"), (" ;", " ;"), ("« ", "« "), (" »", " »")):
        body = body.replace(a, b)
    return body


def build(run_dir, lang, meta):
    src_path = os.path.join(run_dir, f"report-{lang}.html")
    if not os.path.exists(src_path):
        return None
    lab, m = LABELS[lang], meta[lang]
    html = open(src_path, encoding="utf-8").read()
    head, body = html.split("</style>", 1)
    if lang == "fr":
        body = french_spacing(body)
    foot = f"EnerZam · {lab['foot']} {m['period'].replace(' – ', '–')} · {lab['internal']}"
    css = PRINT_CSS.replace("FOOT", foot).replace("PAGE", lab["page"])
    doc_head = (
        f'<div class="wrap">\n  <div class="doc-head"><div><div class="kicker">EnerZam · {lab["internal"]}</div>'
        f'<div class="title">{lab["title"]}</div></div><div class="meta">{lab["period"]} : {m["period"]}<br>'
        f'{lab["issued"]} : {m["issued"]} · {lab["rev"]} {meta["rev"]}<br>{lab["by"]} : {meta["author"]}</div></div>'
    )
    if lang == "en":
        doc_head = doc_head.replace(" :", ":")
    assert '<div class="wrap">' in body, "report body must start with <div class=\"wrap\">"
    assert body.count('class="page-start"') >= 2, "sections 2 and 3 need class=\"page-start\""
    body = body.replace('<div class="wrap">', doc_head, 1)
    print_path = os.path.join(run_dir, f"print-{lang}.html")
    open(print_path, "w", encoding="utf-8").write(head + css + "</style>" + body)
    pdf_path = os.path.join(run_dir, m["pdf"])
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf_path}", "file://" + os.path.abspath(print_path)],
        check=True, capture_output=True,
    )
    pages = len(re.findall(rb"/Type\s*/Page(?!s)", open(pdf_path, "rb").read()))
    return pdf_path, pages


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    run_dir = sys.argv[1]
    meta = json.load(open(os.path.join(run_dir, "meta.json"), encoding="utf-8"))
    for lang in ("en", "fr"):
        result = build(run_dir, lang, meta)
        if result:
            print(f"{lang}\t{result[1]} pages\t{result[0]}")


if __name__ == "__main__":
    main()
