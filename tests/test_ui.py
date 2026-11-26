"""`bookgraph ui`: the helpers (bookgraph.uisupport) and the Streamlit script, driven by Streamlit's AppTest (no browser)."""
from __future__ import annotations

import pytest

from bookgraph import pipeline as pl
from bookgraph import uisupport as ui
from bookgraph.book import load_book

from conftest import Scripted


@pytest.fixture
def book(library, monkeypatch):
    def build(book, out, **kw):
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("site")
        return out
    monkeypatch.setattr("bookgraph.build.build_site", build)
    return load_book(library / "acme" / "demo")


def test_list_books_and_review_material(book, library):
    assert ui.list_books(library) == ["acme/demo"] and ui.list_books(library / "none") == []
    ch = book.chapters[0]
    rows = ui.graph_rows(book, ch)
    assert [r["kind"] for r in rows] == ["primitive", "primitive", "concept", "concept", "application"]
    assert rows[2]["needs"] == "idea, quantity" and rows[4]["id"] == "budget"
    assert ui.page_files(book, ch) == [] and ui.built_site(book) is None


def test_pages_and_built_site_after_a_run(book, library):
    pl.run_pipeline(book.root, llm=Scripted(), until="5-build")
    b = load_book(book.root)
    pages = ui.page_files(b, b.chapters[0])
    assert pages and pages[0].name.startswith("book_")
    assert ui.built_site(b) == b.root / "dist"
    assert "run 5-build" not in ui.step_log(b, "5-build", None)                  # no log: empty, not an error


def test_app_renders_and_approves_a_step(book, library, monkeypatch):
    AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
    from bookgraph import ui as ui_script
    monkeypatch.setenv("BOOKGRAPH_LIBRARY", str(library))
    pl.run_pipeline(book.root, auto_approve=False)                               # 3-validate OK, review pending
    at = AppTest.from_file(ui_script.__file__, default_timeout=30).run()
    assert not at.exception
    assert at.title[0].value == "Demo Book"
    approve = next(b for b in at.button if b.label == "Approve")
    assert not approve.disabled
    approve.click().run()
    assert not at.exception
    assert pl.read_manifest(load_book(book.root), "3-validate", book.chapters[0])["review"]["status"] == "approved"
