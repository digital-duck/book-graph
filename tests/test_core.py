from __future__ import annotations

import pytest

from bookgraph.book import load_book, model_dir_name, scaffold_book
from bookgraph.cache import cache_get, cache_put
from bookgraph.catalog import register_book, scan_library
from bookgraph.exceptions import BookError
from bookgraph.graph import load_graph, section_params
from bookgraph.levels import resolve_style
from bookgraph.paths import lang_suffix, safe_segment, variant_dir
from bookgraph.render import _md_to_html


def test_safe_segment_rejects_traversal():
    assert safe_segment("ok_name-1") == "ok_name-1"
    for bad in ("", "..", "a/b", "-x", "a b"):
        with pytest.raises(BookError):
            safe_segment(bad)


def test_variant_dir_and_suffix(tmp_path):
    assert variant_dir(tmp_path, "core", "en", "m1") == tmp_path / "output" / "core.en" / "m1" / "html"
    assert lang_suffix("en") == "" and lang_suffix("zh") == "_zh"
    assert model_dir_name("claude_cli:claude-sonnet-5-5") == "claude-sonnet-5-5"


def test_scaffold_loads_and_refuses_overwrite(tmp_path):
    root = scaffold_book(tmp_path, "acme", "demo")
    book = load_book(root)
    assert (book.press, book.book, [c.id for c in book.chapters]) == ("acme", "demo", ["ch01"])
    assert load_graph(book.graph_path(book.chapters[0])).validate() == []
    with pytest.raises(BookError):
        scaffold_book(tmp_path, "acme", "demo")


def test_book_yaml_errors(tmp_path):
    (tmp_path / "book.yaml").write_text("press: a\nbook: b\nchapters: []\n")
    with pytest.raises(BookError, match="non-empty"):
        load_book(tmp_path)
    (tmp_path / "book.yaml").write_text("press: a\nbook: b\nlevels: [grad]\nchapters: [{id: c1}]\n")
    with pytest.raises(BookError, match="level"):
        load_book(tmp_path)


def test_graph_order_and_validation(library):
    g = load_graph(library / "acme/demo/chapters/ch01/graph.yaml")
    assert g.validate() == []
    assert g.chapter_id == "ch01"
    assert g.default_target() == "budget"
    order = g.plan("budget")
    assert order[-1] == "budget" and order.index("idea") < order.index("counting") < order.index("addition")
    assert g.label("counting", "zh") == "计数" and g.label("addition") == "Addition"


def test_graph_problems(tmp_path):
    p = tmp_path / "graph.yaml"
    p.write_text("primitives: {a: {defines: x}}\nconcepts:\n  b: {composed_of: [a, ghost], defines: y}\n  c: {composed_of: [d], defines: z}\n  d: {composed_of: [c], defines: w}\n")
    problems = load_graph(p).validate()
    assert any("cycle" in m for m in problems) and any("ghost" in m for m in problems)


def test_not_a_graph(tmp_path):
    (tmp_path / "x.yaml").write_text("hello: 1\n")
    with pytest.raises(BookError):
        load_graph(tmp_path / "x.yaml")


def test_cache_roundtrip_keyed_by_params(tmp_path):
    a, b = section_params("en", "core", "m", "ctx"), section_params("zh", "core", "m", "ctx")
    cache_put(tmp_path, "node", a, "english")
    assert cache_get(tmp_path, "node", a) == "english" and cache_get(tmp_path, "node", b) is None
    assert section_params("en", "core", "m", "other ctx") != a


def test_resolve_style():
    assert resolve_style("intro") == "feynman"
    assert resolve_style("research", ["math"]) == "research"
    assert resolve_style("research", ["history"]) == "research_applied"


def test_md_to_html_keeps_math():
    html = _md_to_html("## T\n\nSee $x^2$.\n\n$$\ny\n$$\n")
    assert "<h2" in html and "$x^2$" in html and "$$\ny\n$$" in html


def test_catalog(library):
    book = load_book(library / "acme" / "demo")
    cat = register_book(library, book)
    ch = cat["presses"][0]["books"][0]["chapters"][0]
    assert ch["id"] == "ch01" and ch["concepts"] == 2 and ch["capstone"] == "budget" and ch["books"] == []
    assert scan_library(library)["presses"][0]["press"] == "acme"


def test_md_table_becomes_a_table():
    """A model's Markdown pipe table (any width) renders as a <table>, not one flattened paragraph."""
    for ncols in (2, 3, 5):
        head = "| " + " | ".join(f"H{c}" for c in range(ncols)) + " |"
        sep = "|" + "---|" * ncols
        rows = "\n".join("| " + " | ".join(f"r{r}c{c}" for c in range(ncols)) + " |" for r in range(2))
        html = _md_to_html(f"Intro:\n\n{head}\n{sep}\n{rows}\n\nAfter.")
        assert "---" not in html and html.count("<th ") == ncols and html.count("<td ") == 2 * ncols
        assert "<p>Intro:</p>" in html and "<p>After.</p>" in html


def test_md_table_keeps_inline_markup_and_alignment_and_skips_ragged():
    html = _md_to_html("| A | B |\n|:---|---:|\n| **x** | `a\\|b` |\n")
    assert "<strong>x</strong>" in html and "text-align: left" in html and "text-align: right" in html
    ragged = _md_to_html("| A | B |\n|---|---|\n| only one |\n")
    assert "<table" not in ragged                       # left as written, never guessed at


def test_md_table_inside_code_is_untouched():
    html = _md_to_html("```\n| A | B |\n|---|---|\n| 1 | 2 |\n```\n")
    assert "<table" not in html and "|---|---|" in html
