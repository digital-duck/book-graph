"""Cross-chapter concept reuse (bookgraph.registry): a node an earlier chapter introduced is recapped, not re-derived."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from bookgraph.book import load_book
from bookgraph.builder import generate_chapter
from bookgraph.graph import load_graph
from bookgraph.registry import earlier_concepts, recap_note

from conftest import Scripted

PRESS = Path(__file__).resolve().parents[2] / "concept-book-press"

CH2 = """\
domain: demo_ch02
primitives:
  counting:
    defines: Counting, as chapter two sees it.
concepts:
  multiplication:
    composed_of: [counting]
    defines: Repeated addition.
applications:
  area:
    needs: [multiplication]
    defines: Measuring a rectangle.
"""


@pytest.fixture
def two_chapters(library):
    root = library / "acme/demo"
    (root / "chapters/ch02").mkdir()
    (root / "chapters/ch02/graph.yaml").write_text(CH2)
    y = root / "book.yaml"
    y.write_text(y.read_text() + "  - {id: ch02, title: Chapter 2, graph: chapters/ch02/graph.yaml}\n")
    return load_book(y)


def test_the_registry_is_earlier_chapters_first_wins(two_chapters):
    book = two_chapters
    assert earlier_concepts(book, book.chapters[0]) == {}
    assert earlier_concepts(book, book.chapters[1]) == {"counting": {"chapter": 1, "defines": "Assigning numbers to a collection."}}


def test_the_note_is_press_text_and_comes_before_the_diagram(two_chapters):
    g = load_graph(two_chapters.graph_path(two_chapters.chapters[1]))
    note = recap_note({"chapter": 1, "defines": "Assigning numbers to a collection."})
    assert g.context("counting", note) == "Counting, as chapter two sees it." + note
    assert note.startswith(' [Already introduced in Chapter 1 as: "Assigning numbers to a collection." — treat as')


@pytest.mark.skipif(not (PRESS / "pipeline" / "publish" / "registry.py").is_file(), reason="concept-book-press is not checked out")
def test_same_context_as_press_augmented_graph(two_chapters, tmp_path):
    sys.path.insert(0, str(PRESS))
    try:
        from pipeline.publish.registry import augment_graph_for_generation
    finally:
        sys.path.remove(str(PRESS))
    book = two_chapters
    ch1 = yaml.safe_load(book.graph_path(book.chapters[0]).read_text())
    registry = {nid: {"chapter": 1, "label": nid, "defines": (a or {}).get("defines", "")}
                for s in ("primitives", "concepts", "applications") for nid, a in (ch1.get(s) or {}).items()}
    aug = tmp_path / "aug.yaml"
    aug.write_text(yaml.dump(augment_graph_for_generation(yaml.safe_load(CH2), registry), allow_unicode=True))
    ours = load_graph(book.graph_path(book.chapters[1]))
    earlier = earlier_concepts(book, book.chapters[1])
    for node in ours.G:
        mine = ours.context(node, recap_note(earlier[node]) if node in earlier else "")
        assert mine == load_graph(aug).context(node), node


def test_generation_sees_the_note_and_can_turn_it_off(two_chapters, tmp_path):
    book = two_chapters
    llm = Scripted()
    assert generate_chapter(book, book.chapters[1], level="core", language="en", llm=llm, log_dir=tmp_path / "log").ok
    counting = [p for p in llm.prompts if "## Counting" in p]
    assert counting and "Already introduced in Chapter 1" in counting[0]
    assert not any("Already introduced" in p for p in llm.prompts if "## Multiplication" in p)
    y = book.root / "book.yaml"
    y.write_text(y.read_text() + "recap_earlier_chapters: false\n")
    book = load_book(y)
    assert earlier_concepts(book, book.chapters[1]) == {}
    llm = Scripted()
    assert generate_chapter(book, book.chapters[1], level="core", language="en", llm=llm, log_dir=tmp_path / "log").ok
    assert not any("Already introduced" in p for p in llm.prompts) and len(llm.prompts) == 1     # only counting changed key
