"""book.yaml: one book of one press, as a list of chapters, each with its own graph.yaml.

    press: digital-duck
    book: linalg
    title: Linear Algebra
    source: {kind: openstax, url: ..., license: ..., attribution: ...}     # optional
    levels: [core, college]
    languages: [en, zh]
    models: [claude_cli:claude-sonnet-5-5]       # an adapter:model string; its file-name form is the part after ':'
    tags: [math]
    site: {base: /cb-linalg/, brand: {}}                                  # optional, read by the web build
    publish: {repo: digital-duck/cb-linalg}       # optional: `bookgraph publish` target (branch gh-pages; see bookgraph.publish)
    legacy_cache: ../cb-linalg/spl/.spl          # optional: the SPL app's spl3 cache, read (never written) so its sections are reused
    chapters:
      - {id: ch01, title: Systems of equations, graph: chapters/ch01/graph.yaml, capstone: gaussian_elimination}
        # optional: global_id (default <book>_<id>, e.g. linalg_ch01: the id the web apps and URLs use), description,
        # catalog: {...} (extra catalog.json fields kept as they are: name, i18n, default_level, source, publisher, pdfs, ...)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .exceptions import BookError
from .paths import safe_segment

LEVELS = ("intro", "core", "college", "research")


@dataclass
class Chapter:
    id: str
    title: str
    graph: str                      # path relative to the book folder
    capstone: str = ""              # the node the chapter's book is written toward; "" = the last application
    global_id: str = ""             # the id outside the library (catalog.json, URLs, public/domains/<id>/); "" = <book>_<id>
    description: str = ""
    catalog: dict[str, Any] = field(default_factory=dict)   # extra catalog.json fields, exported unchanged (bookgraph.export)


@dataclass
class Book:
    press: str
    book: str
    title: str
    chapters: list[Chapter]
    levels: list[str] = field(default_factory=lambda: ["core"])
    languages: list[str] = field(default_factory=lambda: ["en"])
    models: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    site: dict[str, Any] = field(default_factory=dict)
    publish: dict[str, Any] = field(default_factory=dict)   # {repo | remote, branch, cname}: bookgraph.publish
    root: Path = field(default_factory=Path)          # the folder holding book.yaml
    legacy_cache: str = ""                            # absolute path of an SPL app's `spl/.spl` cache folder (bookgraph.legacy), or ""
    recap_earlier_chapters: bool = True               # a node an earlier chapter introduced gets a recap note (bookgraph.registry)

    def chapter(self, chapter_id: str) -> Chapter:
        for c in self.chapters:
            if c.id == chapter_id:
                return c
        raise BookError(f"book {self.book!r} has no chapter {chapter_id!r} (has: {', '.join(c.id for c in self.chapters)})")

    def graph_path(self, chapter: Chapter) -> Path:
        return (self.root / chapter.graph).resolve()

    def chapter_root(self, chapter: Chapter) -> Path:
        return self.root / "chapters" / chapter.id

    def global_id(self, chapter: Chapter) -> str:
        """The chapter's id outside the library: `global_id:` when given, else `<book>_<chapter>` (`calculus` + `ch01` gives
        `calculus_ch01`, the id the concept-book apps already use)."""
        return chapter.global_id or f"{self.book}_{chapter.id}"


def load_book(path: str | Path) -> Book:
    """Read and validate a book.yaml (or the folder holding one). Raises BookError."""
    p = Path(path).expanduser()
    if p.is_dir():
        p = p / "book.yaml"
    if not p.is_file():
        raise BookError(f"no book.yaml at {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise BookError(f"{p}: not valid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise BookError(f"{p}: top level must be a mapping")

    press = safe_segment(str(raw.get("press", "")), "press")
    slug = safe_segment(str(raw.get("book", "")), "book")
    chapters_raw = raw.get("chapters")
    if not isinstance(chapters_raw, list) or not chapters_raw:
        raise BookError(f"{p}: 'chapters' must be a non-empty list")
    chapters: list[Chapter] = []
    seen: set[str] = set()
    for i, c in enumerate(chapters_raw):
        if not isinstance(c, dict) or "id" not in c:
            raise BookError(f"{p}: chapters[{i}] needs an 'id'")
        cid = safe_segment(str(c["id"]), "chapter")
        if cid in seen:
            raise BookError(f"{p}: chapter {cid!r} is listed twice")
        seen.add(cid)
        extra = c.get("catalog") or {}
        if not isinstance(extra, dict):
            raise BookError(f"{p}: chapter {cid!r}: 'catalog' must be a mapping")
        chapters.append(Chapter(id=cid, title=str(c.get("title") or cid), graph=str(c.get("graph") or f"chapters/{cid}/graph.yaml"),
                                capstone=str(c.get("capstone") or ""),
                                global_id=safe_segment(str(c["global_id"]), "global_id") if c.get("global_id") else "",
                                description=str(c.get("description") or ""), catalog=dict(extra)))
    gids = [c.global_id or f"{slug}_{c.id}" for c in chapters]
    if len(set(gids)) != len(gids):
        raise BookError(f"{p}: two chapters share a global id: {', '.join(sorted({g for g in gids if gids.count(g) > 1}))}")
    levels = [str(x) for x in raw.get("levels") or ["core"]]
    for lv in levels:
        if lv not in LEVELS:
            raise BookError(f"{p}: level {lv!r} is not one of {', '.join(LEVELS)}")
    return Book(press=press, book=slug, title=str(raw.get("title") or slug), chapters=chapters, levels=levels,
                languages=[str(x) for x in raw.get("languages") or ["en"]], models=[str(x) for x in raw.get("models") or []],
                tags=[str(x) for x in raw.get("tags") or []], source=dict(raw.get("source") or {}), site=dict(raw.get("site") or {}),
                publish=dict(raw.get("publish") or {}),
                root=p.parent.resolve(),
                legacy_cache=str((p.parent / str(raw["legacy_cache"])).expanduser().resolve()) if raw.get("legacy_cache") else "",
                recap_earlier_chapters=bool(raw.get("recap_earlier_chapters", True)))


def model_dir_name(model: str) -> str:
    """The folder a model's pages live in: the model part of `ADAPTER:MODEL` made path-safe (anything but letters, digits, `_`
    and `-` becomes `-`), so `ollama:qwen2.5:7b` -> `qwen2-5-7b`; "" stays "". The folder drops the adapter to keep the names
    the existing apps use (`output/core.en/sonnet/html`); the cache key carries the whole `ADAPTER:MODEL` string."""
    part = model.split(":", 1)[1] if ":" in model else model
    return re.sub(r"[^A-Za-z0-9_-]+", "-", part).strip("-_")


NEW_BOOK = """\
press: {press}
book: {book}
title: {title}
levels: [core]
languages: [en]
models: []                 # e.g. [claude_cli:claude-sonnet-5-5]
tags: []                   # math / physics / engineering select the rigorous research style
chapters:
  - {{id: ch01, title: Chapter 1, graph: chapters/ch01/graph.yaml}}
"""

NEW_GRAPH = """\
domain: {book}_ch01
primitives:
  idea:
    label: Idea
    defines: A starting notion the chapter takes as given.
concepts:
  first_concept:
    label: First concept
    composed_of: [idea]
    defines: The first thing built from the idea.
applications:
  payoff:
    label: Payoff
    needs: [first_concept]
    defines: What the chapter's concepts are for.
"""


def scaffold_book(library: Path, press: str, book: str, title: str = "") -> Path:
    """Create <library>/<press>/<book>/ with a book.yaml and a one-chapter starter graph. Refuses to overwrite."""
    from .paths import book_dir
    root = book_dir(library, press, book)
    if (root / "book.yaml").exists():
        raise BookError(f"{root / 'book.yaml'} already exists")
    (root / "chapters" / "ch01").mkdir(parents=True, exist_ok=True)
    (root / "book.yaml").write_text(NEW_BOOK.format(press=press, book=book, title=title or book.replace("-", " ").replace("_", " ").title()),
                                    encoding="utf-8")
    (root / "chapters" / "ch01" / "graph.yaml").write_text(NEW_GRAPH.format(book=book), encoding="utf-8")
    return root
