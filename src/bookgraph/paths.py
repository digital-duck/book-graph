"""The library tree, in one place. Build and parse paths only through this module.

    <library>/<press>/<book>/book.yaml
    <library>/<press>/<book>/chapters/<chapter>/graph.yaml
    <library>/<press>/<book>/chapters/<chapter>/output/<level>.<lang>/<model>/html/{concept,book}_<node>[_<lang>].html
    <library>/<press>/<book>/chapters/<chapter>/.bg_cache/        (the section cache)
    <library>/catalog.json                                         (every press, book and chapter)

Press > book > chapter are the traditional names; the concept-book apps called a chapter a "domain".
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .exceptions import BookError

DEFAULT_LIBRARY = Path.home() / "book-graph" / "library"
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")


def default_library() -> Path:
    return Path(os.environ.get("BOOKGRAPH_LIBRARY") or DEFAULT_LIBRARY).expanduser()


def safe_segment(value: str, what: str = "name") -> str:
    """A value that becomes one path segment: letters, digits, `_` and `-` only."""
    if not isinstance(value, str) or not _SEGMENT.match(value):
        raise BookError(f"{what} {value!r} must be letters, digits, '_' or '-' (and not start with one of the last two)")
    return value


def book_dir(library: Path, press: str, book: str) -> Path:
    return Path(library) / safe_segment(press, "press") / safe_segment(book, "book")


def chapter_dir(library: Path, press: str, book: str, chapter: str) -> Path:
    return book_dir(library, press, book) / "chapters" / safe_segment(chapter, "chapter")


def variant_dir(chapter: Path, level: str, lang: str, model: str = "") -> Path:
    """output/<level>.<lang>[/<model>]/html — one generated variant of a chapter."""
    base = Path(chapter) / "output" / f"{safe_segment(level, 'level')}.{safe_segment(lang, 'language')}"
    if model:
        base = base / safe_segment(model, "model")
    return base / "html"


def lang_suffix(lang: str) -> str:
    """English pages are unsuffixed (old links keep working); every other language gets `_<lang>`."""
    return f"_{lang}" if lang and lang != "en" else ""
