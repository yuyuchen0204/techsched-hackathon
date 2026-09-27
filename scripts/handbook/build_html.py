"""docs/product-handbook.zh.md -> a print-styled HTML that scripts/handbook/to_pdf.mjs turns into the PDF.

The cover, the table of contents and the body are converted separately so each can get its own page.
Run through scripts/build_handbook_pdf.sh; needs `markdown` (pip install markdown).
"""
import os, pathlib, re, sys, markdown

ROOT = pathlib.Path(__file__).resolve().parents[2]
LANG = os.environ.get('HANDBOOK_LANG', 'zh')            # zh (default) | en
DOC = os.environ.get('HANDBOOK_DOC', 'product-handbook') # product-handbook | technical-design
SRC = ROOT / f'docs/{DOC}.{LANG}.md'
OUT = ROOT / f'docs/.handbook-build/{DOC}.{LANG}/handbook.html'
OUT.parent.mkdir(parents=True, exist_ok=True)
TITLES = {('product-handbook', 'zh'): 'TechSched 产品手册',
          ('product-handbook', 'en'): 'TechSched Product Handbook',
          ('technical-design', 'zh'): 'TechSched 技术设计文档',
          ('technical-design', 'en'): 'TechSched Technical Design',
          ('business-proposal', 'zh'): 'TechSched 商业计划书',
          ('business-proposal', 'en'): 'TechSched Business Proposal'}
DOC_TITLE = TITLES[(DOC, LANG)]
LANG_TAG = 'en' if LANG == 'en' else 'zh-CN'
TOC_HEAD = '| Chapter | Title |' if LANG == 'en' else '| 章节 | 标题 |'
TOC_HEAD_PG = '| Chapter | Title | Page |' if LANG == 'en' else '| 章节 | 标题 | 页码 |'
TOC_ANCHOR = '## Contents' if LANG == 'en' else '## 目录'
PART_RE = (r'<h1>(Part ([A-Za-z]+)[^<]*)</h1>' if LANG == 'en'
           else r'<h1>(第([一二三四五六七八九]+)部分[^<]*)</h1>')
APPENDIX_RE = (r'<(h1|h2)>Appendix ([A-Z])' if LANG == 'en' else r'<(h1|h2)>附录 ([A-Z])')

text = SRC.read_text()


CODE_IN_CELL = re.compile(r'(<t[dh][^>]*>)(.*?)(</t[dh]>)', re.S)
SHORT_CODE = re.compile(r'<code>([^<\s]{1,30})</code>')

def _nobreak_cells(html):
    """A short identifier in a table cell must not be hyphenated across lines: auto table layout
    otherwise squeezes e.g. `search_repair_catalog` into three broken fragments."""
    def cell(m):
        return m.group(1) + SHORT_CODE.sub(r'<code class="nb">\1</code>', m.group(2)) + m.group(3)
    return CODE_IN_CELL.sub(cell, html)

PAGES_FILE = OUT.parent / 'pages.json'


def _anchor_ids(html):
    """Stable ids on the headings the table of contents lists, so pagination can be measured."""
    html = re.sub(PART_RE, r'<h1 id="part-\2">\1</h1>', html)
    for lvl in ('h1', 'h2'):
        html = re.sub(rf'<{lvl}>(\d+)\. ',
                      lambda m, l=lvl: f'<{l} id="ch-{m.group(1)}">{m.group(1)}. ', html)
    html = re.sub(APPENDIX_RE,
                  lambda m: f'<{m.group(1)} id="app-{m.group(2)}">' + m.group(0)[len(m.group(1)) + 2:], html)
    return html


def _toc_with_pages(md):
    """Pass 2: add a 页码 column to the contents table from the measured page map."""
    import json
    if not PAGES_FILE.exists():
        return md
    pages = json.loads(PAGES_FILE.read_text())
    if not pages:
        return md
    out = []
    for line in md.split('\n'):
        if line.startswith(TOC_HEAD):
            out.append(TOC_HEAD_PG); continue
        if line.startswith('|---|---|') and out and out[-1].startswith(TOC_HEAD):
            out.append('|---|---|---:|'); continue
        m = re.match(r'^\| (\*\*)?([^|]+?)(\*\*)? \| (.+) \|$', line)
        if m and not line.startswith(TOC_HEAD[:8]):
            key = m.group(2).strip()
            pg = None
            if key.isdigit():
                pg = pages.get(f'ch-{key}')
            elif key.startswith('第') and key.endswith('部分'):
                pg = pages.get('part-' + key[1:-2])
            elif key.startswith('Part '):
                pg = pages.get('part-' + key[5:])
            elif len(key) == 1 and key.isalpha() and key.isupper():
                pg = pages.get(f'app-{key}')
            if pg:
                out.append(line + f' {pg} |'); continue
            out.append(line + ' |' if line.count('|') == 3 else line); continue
        out.append(line)
    return '\n'.join(out)


i_toc = text.index(TOC_ANCHOR)
i_body = min(i for i in (text.find('## 0. '), text.find('# 1. '), text.find('## 1. ')) if i > i_toc)
cover_md, toc_md, body_md = text[:i_toc], _toc_with_pages(text[i_toc:i_body]), text[i_body:]


def md2html(s):
    m = markdown.Markdown(extensions=['tables', 'fenced_code', 'attr_list', 'md_in_html', 'sane_lists'])
    return _anchor_ids(_nobreak_cells(m.convert(s)))

CSS = r"""
@page { size: A4; margin: 15mm 14mm 14mm 14mm; }
:root{ --ink:#141a22; --muted:#5d6775; --line:#dbe1e9; --accent:#2c5be6; --accent-soft:#eaf0ff;
       --amber:#c27505; --amber-soft:#fff5df; --green:#159a67; --pink:#d6336c; }
*{ box-sizing:border-box; }
html{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }
body{ font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Source Han Sans SC",
        -apple-system,"Helvetica Neue",Arial,sans-serif;
      font-size:8.9pt; line-height:1.56; color:var(--ink); margin:0;
      text-align:justify; text-justify:inter-ideograph; }
p{ margin:.42em 0; orphans:3; widows:3; }
strong{ font-weight:650; }
a{ color:var(--accent); text-decoration:none; word-break:break-all; }
hr{ display:none; }

/* ---------- cover ---------- */
.cover{ break-after:page; padding-top:24mm; text-align:left; }
.cover h1{ font-size:30pt; line-height:1.2; margin:0 0 6mm; letter-spacing:-.01em;
           border:0; padding:0; page-break-before:auto; }
.cover h1::after{ content:""; display:block; width:26mm; height:3px; background:var(--accent); margin-top:5mm; }
.cover blockquote{ margin:0 0 12mm; padding:0; border:0; font-size:11.5pt; line-height:1.75;
                   color:var(--muted); max-width:150mm; }
.cover blockquote p{ margin:0; }
.cover table{ font-size:9.6pt; }
.cover table td:first-child, .cover table th:first-child{ width:34mm; color:var(--muted); }
.cover h2{ font-size:11.5pt; margin:7mm 0 2mm; padding-left:2.6mm; border-left:3px solid var(--accent); }
.cover ol{ font-size:9pt; }

/* ---------- toc ---------- */
.toc{ break-after:page; }
.toc h2{ font-size:16pt; border:0; padding:0; margin:0 0 6mm; }
.toc table{ font-size:9.6pt; }
.toc table td:first-child{ width:26mm; color:var(--muted); }
.toc table tr td strong{ color:var(--ink); }

/* ---------- headings ---------- */
h1{ font-size:17.5pt; margin:0 0 6mm; padding:0 0 2.5mm; border-bottom:2.5px solid var(--accent);
    break-before:page; break-after:avoid; letter-spacing:-.005em; }
h2{ font-size:12.5pt; margin:7mm 0 2.8mm; padding-left:3.2mm; border-left:3.5px solid var(--accent);
    break-after:avoid; }
h3{ font-size:10.4pt; margin:4.6mm 0 1.6mm; color:#243044; break-after:avoid; }
h4{ font-size:9.4pt; margin:3.5mm 0 1.2mm; color:#243044; break-after:avoid; }

/* ---------- tables ---------- */
table{ width:100%; border-collapse:collapse; margin:2.8mm 0 4mm; font-size:7.7pt; line-height:1.42;
       break-inside:auto; }
thead{ display:table-header-group; }
tr{ break-inside:avoid; }
th{ background:#f2f5f9; text-align:left; font-weight:650; color:#2a3546; }
th,td{ border:.5px solid var(--line); padding:1.1mm 1.6mm; vertical-align:top;
       word-break:normal; overflow-wrap:break-word; text-align:left; }
th:first-child,td:first-child{ min-width:6mm; }
code.nb{ white-space:nowrap; }
sup{ font-size:.66em; vertical-align:super; line-height:0; color:var(--accent); font-weight:650; padding:0 .1em; }
.footnotes{ margin:5mm 0 3mm; padding:3mm 4mm; background:#f7f9fc; border-left:3px solid var(--accent);
            border-radius:3px; font-size:8.2pt; line-height:1.55; break-inside:avoid; }
.footnotes p{ margin:.3em 0; }
.footnotes ol{ margin:.3em 0 .3em; padding-left:5mm; }
.footnotes li{ margin:.35em 0; }
.footnotes a{ word-break:break-all; }
.nw{ white-space:nowrap; }

td{ hyphens:none; }
td code, th code{ font-size:7.4pt; }

/* ---------- code ---------- */
code{ font-family:"SF Mono",Menlo,Consolas,"Liberation Mono",monospace; font-size:7.8pt;
      background:#f1f4f8; padding:.4mm 1mm; border-radius:2px; word-break:break-word; }
pre{ background:#f7f9fc; border:.5px solid var(--line); border-left:3px solid var(--accent);
     border-radius:3px; padding:2.4mm 3mm; margin:2.8mm 0 4mm; font-size:6.7pt; line-height:1.38;
     white-space:pre-wrap; overflow-wrap:anywhere; break-inside:avoid; }
pre code{ background:none; padding:0; font-size:inherit; }

/* ---------- lists ---------- */
ul,ol{ margin:.35em 0 .6em; padding-left:5mm; }
li{ margin:.2em 0; }

/* ---------- blockquote ---------- */
blockquote{ margin:2.8mm 0; padding:2mm 3.4mm; background:var(--amber-soft);
            border-left:3px solid var(--amber); color:#4a3a15; font-size:8.4pt; break-inside:avoid; }
blockquote p{ margin:.25em 0; }

/* ---------- figures ---------- */
figure.fig{ margin:4mm 0 4.5mm; break-inside:avoid; text-align:center; }
figure.fig img{ max-width:100%; height:auto; border:.5px solid var(--line); border-radius:3px;
                box-shadow:0 1px 3px rgba(20,26,34,.08); }
figure.fig figcaption{ font-size:7.4pt; color:var(--muted); line-height:1.55; margin-top:2mm;
                       text-align:left; padding:0 2mm; }
figure.fig-narrow img{ max-width:58mm; }
figure.fig-panel img{ max-width:112mm; }
figure.fig-wide img{ max-width:100%; }
figure.fig-diagram{ margin:5mm 0 7mm; }
figure.fig-diagram img{ max-width:100%; }
figure.fig-chart img{ max-width:100%; border:0; box-shadow:none; }
figure.fig-chart{ margin:4mm 0 5mm; }
"""

HTML = f"""<!doctype html>
<html lang="{LANG_TAG}"><head><meta charset="utf-8">
<title>{DOC_TITLE}</title>
<base href="file://{ROOT}/docs/">
<style>{CSS}</style></head>
<body>
<section class="cover">{md2html(cover_md)}</section>
<section class="toc">{md2html(toc_md)}</section>
<section class="body">{md2html(body_md)}</section>
</body></html>"""

OUT.write_text(HTML)
print('html written', OUT, len(HTML), 'bytes')
