"""Writing a chapter's pages: concept_<node>.html for each section and book_<target>.html, the index linking them."""
from __future__ import annotations

import re
from pathlib import Path

from .graph import ChapterGraph, retitle_heading
from .paths import lang_suffix
from .render import _BOOK_INDEX_TEMPLATE, _CONCEPT_PAGE_TEMPLATE, _esc, _md_to_html, fill

# Fixed strings on the book index, per language; English where a language has none.
BOOK_UI = {
    "book": {"en": "Concept Book", "zh": "概念书", "fr": "Livre de concepts", "es": "Libro de conceptos", "de": "Konzeptbuch"},
    "contents": {"en": "Contents", "zh": "目录", "fr": "Sommaire", "es": "Contenido", "de": "Inhalt"},
    "payoff": {"en": "Payoff", "zh": "成果", "fr": "Aboutissement", "es": "Resultado", "de": "Ergebnis"},
}


def _ui(key: str, lang: str) -> str:
    return BOOK_UI[key].get(lang) or BOOK_UI[key]["en"]


def _lang_attr(lang: str) -> str:
    return f' lang="{lang or "en"}"'


def _footer(chapter_title: str, level: str, lang: str, model: str) -> str:
    bits = [b for b in (f"Level: {_esc(level.title())}" if level else "", f"Language: {_esc(lang.upper())}" if lang else "",
                        f"Model: {_esc(model)}" if model else "") if b]
    detail = " &middot; ".join(bits)
    title = _esc(chapter_title)
    return f"{title} ({detail})" if title and detail else title or detail


def write_concept_page(g: ChapterGraph, node: str, section: str, out_dir: str | Path, lang: str, *,
                       chapter_title: str = "", level: str = "", model: str = "") -> Path:
    """Write <out_dir>/concept_<node>[_<lang>].html. English is unsuffixed so old links keep working."""
    label = g.label(node, lang)
    section = retitle_heading(section, node, label, g.has_label(node, lang))
    title = chapter_title or g.chapter_id.replace("_", " ").title()
    html = fill(_CONCEPT_PAGE_TEMPLATE, lang_attr=_lang_attr(lang), concept_title=_esc(label), domain_title=_esc(title),
                body=_md_to_html(section), footer_meta=_footer(title, level, lang, model))
    out = Path(out_dir) / f"concept_{node}{lang_suffix(lang)}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def write_book_index(g: ChapterGraph, order: list[str], target: str, payoff: str, out_dir: str | Path, lang: str, *,
                     chapter_title: str = "", level: str = "", model: str = "") -> Path:
    """Write <out_dir>/book_<target>[_<lang>].html: a TOC over `order` plus the payoff (when there is one)."""
    suffix = lang_suffix(lang)
    title = chapter_title or g.chapter_id.replace("_", " ").title()
    items = []
    for node in order:
        cls = ' class="toc-target"' if node == target else ""
        items.append(f'<li{cls}><a href="concept_{node}{suffix}.html">{_esc(g.label(node, lang))}</a></li>')
    toc = "<ol>\n" + "\n".join(items) + "\n</ol>"
    heading = f"## {_ui('payoff', lang)}"
    payoff = re.sub(r"\A\s*##[ \t]+Payoff\b[^\n]*", lambda _m: heading, payoff)
    payoff_html = f"<section>\n      {_md_to_html(payoff)}\n    </section>" if payoff.strip() else ""
    html = fill(_BOOK_INDEX_TEMPLATE, lang_attr=_lang_attr(lang), domain_title=_esc(title), target_title=_esc(g.label(target, lang)),
                book_label=_esc(_ui("book", lang)), contents_label=_esc(_ui("contents", lang)), toc=toc, payoff=payoff_html,
                footer_meta=_footer(title, level, lang, model))
    out = Path(out_dir) / f"book_{target}{suffix}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
