"""`bookgraph ingest`: concept-book-press's PDF ingester, on a PDF made here and (when present) on press's own source PDF."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

pymupdf = pytest.importorskip("pymupdf", reason="bookgraph ingest needs PyMuPDF (pip install 'book-graph[ingest]')")

from bookgraph.book import add_chapter, load_book  # noqa: E402
from bookgraph.cli import main  # noqa: E402
from bookgraph.ingest import ingest_chapter, list_chapters  # noqa: E402

PRESS = Path(__file__).resolve().parents[2] / "concept-book-press"


def make_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    pages = ["Chapter 1 Basics\nA quantity is something measured.", "7\nRates compare two quantities.",
             "Chapter 2 More\nSpeed is a rate.\nAccess for free at openstax.org"]
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    doc.set_toc([[1, "Preface", 1], [1, "Chapter 1 Basics", 1], [2, "1.1 Quantities", 1], [2, "1.2 Rates", 2], [2, "Glossary", 2],
                 [1, "Chapter 2 More", 3], [2, "2.1 Speed", 3]])
    doc.save(str(path))
    return path


def test_chapters_sections_and_cleaning(tmp_path):
    pdf = make_pdf(tmp_path / "book.pdf")
    assert [(n, t) for n, t, _ in list_chapters(pdf)] == [(1, "Chapter 1 Basics"), (2, "Chapter 2 More")]
    c = ingest_chapter(pdf, 1, attribution="test").to_dict()
    assert c["chapter_title"] == "Chapter 1 Basics" and c["source_attribution"] == "test"
    assert [(x["section_id"], x["page_start"], x["page_end"]) for x in c["chunks"]] == [("ch01_s1_1", 1, 1), ("ch01_s1_2", 2, 2)]
    assert "7" not in c["chunks"][1]["text"].split() and "Rates compare" in c["chunks"][1]["text"]        # page number dropped
    two = ingest_chapter(pdf, 2).to_dict()
    assert "Access for free" not in two["chunks"][0]["text"] and "OpenStax" in two["source_attribution"]
    with pytest.raises(ValueError, match="Chapter 9 not found"):
        ingest_chapter(pdf, 9)


def test_cli_ingests_into_the_book(library, tmp_path):
    pdf = make_pdf(tmp_path / "book.pdf")
    run = lambda *a: CliRunner().invoke(main, ["-L", str(library), "ingest", "acme/demo", "--pdf", str(pdf), *a])
    assert "Chapter 2 More" in run("--list").output
    r = run("--chapter", "2")
    assert r.exit_code == 0 and "added chapter ch02 (Chapter 2 More)" in r.output
    book = load_book(library / "acme" / "demo")
    assert [c.id for c in book.chapters] == ["ch01", "ch02"] and book.chapter("ch02").title == "Chapter 2 More"
    chunks = yaml.safe_load((book.chapter_root(book.chapter("ch02")) / "chunks.yaml").read_text())
    assert chunks["chunks"][0]["section_id"] == "ch02_s2_1"
    assert run("--chapter", "2").exit_code == 1                                                  # no silent overwrite
    r = run("--chapter", "1")
    assert r.exit_code == 0 and "already in book.yaml" in r.output


def test_add_chapter_only_appends_to_a_last_chapters_block(library):
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text() + "site: {title: Demo}\n")
    before = y.read_text()
    assert add_chapter(y, "ch09", "Nine") is False and y.read_text() == before


@pytest.mark.skipif(not (PRESS / "input" / "college-physics-2e.pdf").is_file()
                    or not (PRESS / "output" / "college-physics-2e" / "ch1" / "chunks.yaml").is_file(),
                    reason="concept-book-press's source PDF and chunks are not here")
def test_same_chunks_as_press_but_for_whitespace():
    ours = ingest_chapter(PRESS / "input" / "college-physics-2e.pdf", 1).to_dict()
    press = yaml.safe_load((PRESS / "output" / "college-physics-2e" / "ch1" / "chunks.yaml").read_text(encoding="utf-8"))
    norm = lambda s: re.sub(r"\s+", "", s)
    assert {k: v for k, v in ours.items() if k != "chunks"} == {k: v for k, v in press.items() if k != "chunks"}
    assert [(c["section_id"], c["page_start"], c["page_end"], norm(c["text"])) for c in ours["chunks"]] == \
           [(c["section_id"], c["page_start"], c["page_end"], norm(c["text"])) for c in press["chunks"]]
