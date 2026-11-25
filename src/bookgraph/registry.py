"""Cross-chapter concept reuse: a concept an earlier chapter already introduced is recapped, not re-derived.

A book's chapters are extracted independently, so a concept like `atom` reappears, defined from scratch, in every chapter
that needs it. concept-book-press's generate stage (pipeline/publish/registry.py) annotated such a node's `defines`
with the earlier chapter's own definition before generating; this is the same rule, computed from book.yaml:

- chapters in book.yaml order; for chapter k, the registry is every node id of chapters 0..k-1, the first chapter to
  declare an id winning (plain id equality, no fuzzy matching, as in press);
- the note is press's text, word for word (`recap_note`), appended to `defines` before the diagram hint
  (ChapterGraph.context), so the model sees what the SPL did;
- "Chapter N" is the number in the chapter id (`ch03`, `calculus_ch03` -> 3), else its position in book.yaml.

The note reaches the prompt and this library's cache key; the legacy (SPL) cache is still looked up with the plain
context, because the SPL apps wrote their sections without it. Off for a book with `recap_earlier_chapters: false`.
"""
from __future__ import annotations

import re
from typing import Any

from .book import Book, Chapter
from .exceptions import BookError
from .graph import SECTIONS, load_graph


def chapter_number(book: Book, chapter: Chapter) -> int:
    m = re.search(r"(\d+)$", chapter.id)
    return int(m.group(1)) if m else book.chapters.index(chapter) + 1


def recap_note(earlier: dict[str, Any]) -> str:
    """concept-book-press's annotation, verbatim (pipeline/publish/registry.py, augment_graph_for_generation)."""
    return (f" [Already introduced in Chapter {earlier['chapter']} as: "
            f"\"{earlier['defines']}\" — treat as established prerequisite "
            f"knowledge here; give a brief recap rather than a full "
            f"re-derivation, and feel free to reference that chapter by name.]")


def earlier_concepts(book: Book, chapter: Chapter) -> dict[str, dict[str, Any]]:
    """{node id: {chapter, defines}} for the nodes of `chapter` that an earlier chapter of the book already declares."""
    if not book.recap_earlier_chapters or chapter not in book.chapters:
        return {}
    idx = book.chapters.index(chapter)
    if idx == 0:
        return {}
    try:
        own = set(load_graph(book.graph_path(chapter)).G)
    except BookError:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for ch in book.chapters[:idx]:
        try:
            g = load_graph(book.graph_path(ch))
        except BookError:
            continue
        for section in SECTIONS:
            for nid, attrs in (g.data.get(section) or {}).items():
                if nid in own and nid not in found:
                    found[nid] = {"chapter": chapter_number(book, ch), "defines": (attrs or {}).get("defines", "")}
    return found
