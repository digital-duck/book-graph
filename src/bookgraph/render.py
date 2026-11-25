"""HTML rendering: Markdown (with $LaTeX$ and mermaid) to a concept page and a chapter's book index.

The converter and the page templates are the concept-book apps' own (spl/tools.py), kept as they were so existing
generated pages and new ones look alike. Only the credit line changed (SPL -> book-graph).
"""
from __future__ import annotations

import re

def _esc(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _inline_md(text: str) -> str:
    """Bold, italic, backtick-code.  Leaves $ LaTeX delimiters untouched."""
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'`([^`]+)`', lambda m: f'<code>{_esc(m.group(1))}</code>', text)
    return text


def _md_to_html(md: str) -> str:
    """Minimal Markdown → HTML.  Preserves $...$ and $$...$$ for MathJax."""
    lines = md.split('\n')
    out: list[str] = []
    in_code = False
    in_dmath = False   # inside a multi-line $$ ... $$ block
    code_buf: list[str] = []
    math_buf: list[str] = []
    para_buf: list[str] = []

    def flush_para() -> None:
        if para_buf:
            out.append(f'<p>{" ".join(para_buf)}</p>')
            para_buf.clear()

    # diagram feature state (raw HTML passthrough + mermaid fence lang tag)
    in_raw_html = False
    raw_html_buf: list[str] = []
    code_lang = ''

    for line in lines:
        # ── raw HTML passthrough (cb-figure blocks from diagram Option B) ─────
        # Only <figure class="cb-figure"> is passed through unescaped; all other
        # HTML is still escaped by _inline_md for XSS safety.
        if not in_raw_html and line.startswith('<figure'):
            flush_para()
            in_raw_html = True
            raw_html_buf = [line]
            continue
        if in_raw_html:
            raw_html_buf.append(line)
            if '</figure>' in line:
                out.append('\n'.join(raw_html_buf))
                raw_html_buf.clear()
                in_raw_html = False
            continue

        # ── fenced code blocks ────────────────────────────────────────────────
        if line.startswith('```'):
            if in_code:
                if code_lang == 'mermaid':
                    # Preserve unescaped so mermaid.js can parse the diagram.
                    out.append(f'<pre class="mermaid">{chr(10).join(code_buf)}</pre>')
                else:
                    out.append(f'<pre><code>{_esc(chr(10).join(code_buf))}</code></pre>')
                code_buf.clear()
                code_lang = ''
                in_code = False
            else:
                flush_para()
                code_lang = line[3:].strip().lower()
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
            continue

        # ── display math ($$ ... $$) ──────────────────────────────────────────
        # A line that is *only* $$ (possibly with whitespace) is a block delimiter.
        # A line like $$...$$ (content on same line) is a self-closing block.
        if re.match(r'^\s*\$\$', line):
            stripped = line.strip()
            # Self-contained: $$ ... $$ on one line (content between the delimiters)
            if stripped != '$$' and stripped.endswith('$$') and len(stripped) > 4:
                flush_para()
                out.append(line)
                continue
            # Toggle multi-line block
            if in_dmath:
                out.append('$$\n' + '\n'.join(math_buf) + '\n$$')
                math_buf.clear()
                in_dmath = False
            else:
                flush_para()
                in_dmath = True
            continue
        if in_dmath:
            math_buf.append(line)
            continue

        # ── headings ──────────────────────────────────────────────────────────
        m = re.match(r'^(#{1,6})\s+(.+)$', line)
        if m:
            flush_para()
            lvl = len(m.group(1))
            text = _inline_md(m.group(2))
            slug = re.sub(r'\W+', '-', m.group(2).lower()).strip('-')
            out.append(f'<h{lvl} id="{slug}">{text}</h{lvl}>')
            continue

        # ── list items (bullet or numbered) ───────────────────────────────────
        m = re.match(r'^(?:[-*]|\d+\.)\s+(.+)$', line)
        if m:
            flush_para()
            out.append(f'<li>{_inline_md(m.group(1))}</li>')
            continue

        # Horizontal rule
        if re.match(r'^---+$', line.strip()):
            flush_para()
            out.append('<hr>')
            continue

        # Blank line → paragraph break
        if not line.strip():
            flush_para()
            continue

        para_buf.append(_inline_md(line))

    flush_para()
    return '\n'.join(out)


_SHARED_CSS = """\
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Georgia,serif;background:#fafaf8;color:#1a1a1a;line-height:1.7}
h2{font-size:1.45rem;color:#1e3a5f;margin-bottom:12px}
h3{font-size:1.1rem;color:#2e4a7f;margin:20px 0 8px}
h4{font-size:1rem;color:#3a5a8f;margin:16px 0 6px}
p{margin-bottom:16px;font-size:1rem}
li{margin-bottom:6px;margin-left:24px;font-size:1rem}
pre{background:#f4f4f0;border:1px solid #d8d8d0;border-radius:6px;
    padding:16px 20px;overflow-x:auto;margin:16px 0}
code{font-family:Menlo,Consolas,monospace;font-size:.87em}
p code{background:#f0f0ea;padding:1px 4px;border-radius:3px}
footer.bg-credit{margin-top:32px;padding-top:16px;border-top:1px solid #e0e0d8;
      font-family:system-ui,sans-serif;font-size:.78rem;color:#999;text-align:center}
footer.bg-credit a{color:#2563eb;text-decoration:none}
footer.bg-credit span{display:block}
footer.bg-credit .bg-credit__meta{margin-bottom:4px}
.cb-figure{margin:24px 0;text-align:center}
.cb-figure img{max-width:100%;height:auto;border:1px solid #e0e0d8;border-radius:6px}
figcaption{font-size:.82rem;color:#666;margin-top:8px;font-style:italic;text-align:center}
pre.mermaid{background:none;border:none;padding:0;overflow:visible;margin:16px 0}"""

_MATHJAX_HEAD = """\
<script>
MathJax = {
  loader: { load: ['[tex]/mathtools'] },
  tex: { inlineMath: [['$','$'],['\\\\(','\\\\)']], displayMath: [['$$','$$'],['\\\\[','\\\\]']],
         packages: { '[+]': ['mathtools'] } },
  options: { skipHtmlTags: ['script','noscript','style','textarea','pre','code'] }
};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js" async></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>mermaid.initialize({ startOnLoad: true, theme: 'neutral' });</script>"""

_CONCEPT_PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html{lang_attr}>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{concept_title} | {domain_title}</title>
""" + _MATHJAX_HEAD + """
<style>
""" + _SHARED_CSS + """
.page{max-width:780px;margin:0 auto;padding:40px 32px}
section{margin-bottom:48px;border-top:1px solid #e0e0d8;padding-top:36px}
section:first-of-type{border-top:none;padding-top:0}
</style>
</head>
<body>
<div class="page">
  <main>
    {body}
  </main>
  <footer class="bg-credit"><span class="bg-credit__meta">{footer_meta}</span><span class="bg-credit__powered">Generated by <a href="https://github.com/digital-duck/book-graph" target="_blank" rel="noopener">book-graph</a></span></footer>
</div>
</body>
</html>"""

_BOOK_INDEX_TEMPLATE = """\
<!DOCTYPE html>
<html{lang_attr}>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{book_label}: {target_title} — {domain_title}</title>
""" + _MATHJAX_HEAD + """
<style>
""" + _SHARED_CSS + """
.page{display:grid;grid-template-columns:260px 1fr;min-height:100vh}
nav.toc{position:sticky;top:0;height:100vh;overflow-y:auto;
        background:#1e3a5f;color:#e8f0fe;padding:24px 16px}
nav.toc h2{font-size:.75rem;letter-spacing:.1em;text-transform:uppercase;
           color:#90b4e8;margin-bottom:14px;font-family:system-ui,sans-serif}
nav.toc ol{list-style:decimal inside;padding:0}
nav.toc li{margin-bottom:7px;font-size:.85rem;line-height:1.4;font-family:system-ui,sans-serif}
nav.toc a{color:#a8c8f0;text-decoration:none}
nav.toc a:hover{color:#fff}
nav.toc li.toc-target{font-weight:700}
nav.toc li.toc-target a{color:#fff}
main{padding:48px 64px;max-width:860px}
h1.book-title{font-size:2rem;color:#1e3a5f;margin-bottom:4px}
.subtitle{color:#666;margin-bottom:48px;font-size:1rem;font-style:italic;font-family:system-ui,sans-serif}
section{margin-bottom:56px;border-top:1px solid #e0e0d8;padding-top:40px}
section:first-of-type{border-top:none;padding-top:0}
@media(max-width:768px){.page{grid-template-columns:1fr}
nav.toc{position:relative;height:auto}}
</style>
</head>
<body>
<div class="page">
  <nav class="toc">
    <h2>{contents_label}</h2>
    {toc}
  </nav>
  <main>
    <h1 class="book-title">{book_label}: {target_title}</h1>
    <p class="subtitle">{domain_title} &middot; Generated by book-graph</p>
    <section>
      {payoff}
    </section>
    <footer class="bg-credit"><span class="bg-credit__meta">{footer_meta}</span><span class="bg-credit__powered">Generated by <a href="https://github.com/digital-duck/book-graph" target="_blank" rel="noopener">book-graph</a></span></footer>
  </main>
</div>
</body>
</html>"""



def fill(template: str, **kwargs: str) -> str:
    """Substitute {key} placeholders; safe with the CSS/JS in the templates, which contain literal braces."""
    for k, v in kwargs.items():
        template = template.replace("{" + k + "}", v)
    return template
