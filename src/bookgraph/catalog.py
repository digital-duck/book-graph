"""<library>/catalog.json: every press, book and chapter in a library, with what has been generated.

Written only through `update_catalog`, which serializes writers with an flock and replaces the file atomically, so two
generation runs in one library cannot drop each other's updates.
"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .book import Book, load_book, model_dir_name
from .exceptions import BookError
from .graph import load_graph
from .paths import chapter_dir, lang_suffix, safe_segment


@contextmanager
def _locked(lock: Path) -> Iterator[None]:
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def read_catalog(library: Path) -> dict[str, Any]:
    f = Path(library) / "catalog.json"
    try:
        text = f.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"presses": []}
    except OSError as e:
        raise BookError(f"cannot read {f}: {e}") from e
    try:
        return json.loads(text)
    except ValueError as e:                       # never treat a damaged catalog as empty: the next write would drop every other book
        raise BookError(f"{f} is not valid JSON ({e}); fix or delete it, then run `bookgraph catalog` to rebuild it") from e


def update_catalog(library: Path, fn: Callable[[dict[str, Any]], dict[str, Any] | None]) -> dict[str, Any]:
    """Read-modify-write under the lock; `fn` edits the catalog in place (or returns a new one)."""
    library = Path(library)
    with _locked(library / "catalog.json.lock"):
        cat = read_catalog(library)
        cat = fn(cat) or cat
        tmp = library / "catalog.json.tmp"
        tmp.write_text(json.dumps(cat, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, library / "catalog.json")
    return cat


def _books_in(chapter_root: Path) -> list[dict[str, Any]]:
    """The book pages found under a chapter's output/, as the catalog lists them."""
    found = []
    out = chapter_root / "output"
    if not out.is_dir():
        return found
    for f in sorted(out.glob("*/*/html/book_*.html")) + sorted(out.glob("*/html/book_*.html")):
        rel = f.relative_to(chapter_root).parts           # output/<level>.<lang>[/<model>]/html/book_<t>[_<lang>].html
        level, _, lang = rel[1].partition(".")
        model = rel[2] if len(rel) == 5 else ""
        target = f.stem[len("book_"):]
        suffix = lang_suffix(lang)
        if suffix and target.endswith(suffix):
            target = target[: -len(suffix)]
        found.append({"target": target, "level": level, "language": lang, "model": model, "file": "/".join(rel)})
    return found


def describe_book(library: Path, book: Book) -> dict[str, Any]:
    chapters = []
    for ch in book.chapters:
        entry: dict[str, Any] = {"id": ch.id, "title": ch.title, "capstone": ch.capstone}
        try:
            g = load_graph(book.graph_path(ch))
            entry.update(nodes=g.G.number_of_nodes(), edges=g.G.number_of_edges(), primitives=len(g.primitives()),
                         concepts=len((g.data.get("concepts") or {})), applications=len((g.data.get("applications") or {})))
            entry["capstone"] = ch.capstone or g.default_target()
        except BookError as e:
            entry["error"] = str(e)
        entry["books"] = _books_in(book.chapter_root(ch))
        chapters.append(entry)
    return {"book": book.book, "title": book.title, "tags": book.tags, "levels": book.levels, "languages": book.languages,
            "models": [model_dir_name(m) for m in book.models], "chapters": chapters}


def register_book(library: Path, book: Book) -> dict[str, Any]:
    """Add or refresh one book (under its press) in <library>/catalog.json."""
    entry = describe_book(library, book)

    def edit(cat: dict[str, Any]) -> dict[str, Any]:
        presses = cat.setdefault("presses", [])
        press = next((p for p in presses if p["press"] == book.press), None)
        if press is None:
            press = {"press": book.press, "books": []}
            presses.append(press)
        press["books"] = [b for b in press["books"] if b["book"] != book.book] + [entry]
        press["books"].sort(key=lambda b: b["book"])
        presses.sort(key=lambda p: p["press"])
        return cat

    return update_catalog(library, edit)


def scan_library(library: Path) -> dict[str, Any]:
    """Rebuild the whole catalog from every <press>/<book>/book.yaml under the library."""
    library = Path(library)
    cat: dict[str, Any] = {"presses": []}
    for f in sorted(library.glob("*/*/book.yaml")):
        try:
            safe_segment(f.parent.parent.name, "press")
            book = load_book(f)
        except BookError:
            continue
        if (book.press, book.book) != (f.parent.parent.name, f.parent.name):      # `press/book` must find it again
            continue
        press = next((p for p in cat["presses"] if p["press"] == book.press), None)
        if press is None:
            press = {"press": book.press, "books": []}
            cat["presses"].append(press)
        press["books"].append(describe_book(library, book))
    update_catalog(library, lambda _old: cat)
    return cat
