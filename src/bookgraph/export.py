"""Export a library book as the concept-book web apps' content tree, and import (migrate) a `cb-*` repo into the library.

The web apps (concept-book-base and its `cb-*` forks, conceptbook-app) read `public/domains/`:

    <domains>/catalog.json                          a flat list, one entry per chapter (below)
    <domains>/<global id>/input/graph.yaml
    <domains>/<global id>/output/graph.html         the navigator (vis.js, bookgraph.navigator; written when missing or stale)
    <domains>/<global id>/output/<level>.<lang>[/<model>]/html/{concept,book}_<node>[_<lang>].html
    <domains>/graph.yaml, groups.json, ...          book-level files; kept in the library as <book>/domains/

A catalog entry is the chapter's `catalog:` fields from book.yaml (kept as they are: `name`, `i18n`, `source`, ...) plus
what is computed here: `id`, `capstone`, the graph's counts, `has_navigator`, `has_book`, `books` and
`generated_concepts`, listed from the files on disk. Writes to catalog.json take `catalog.json.lock`, the lock file the
apps' own `catalog_lock.py` uses, so an export into a live app does not race its API server.

`migrate_repo` goes the other way: it reads a repo's `public/domains/` into `<library>/<press>/<book>/`, keeping each
chapter's old id as its global id, its catalog fields, its generated pages and navigator, and (when the repo has one)
pointing `legacy_cache:` at its spl3 cache so existing sections are reused. Exporting the migrated book reproduces the
repo's content tree (tests/test_export.py).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .book import LEVELS, Book, load_book
from .catalog import _locked, register_book
from .exceptions import BookError
from .graph import load_graph
from .navigator import graph_hash, navigator_stale, write_navigator
from .paths import book_dir, lang_suffix, safe_segment

# Fields computed from the graph and the files on disk; every other catalog field comes from book.yaml as it is.
COMPUTED = ("id", "capstone", "nodes", "edges", "primitives", "concepts", "applications", "has_navigator", "has_book", "books",
            "generated_concepts", "pdfs")
_ORDER = ("id", "name", "description", "capstone", "nodes", "edges", "primitives", "concepts", "applications", "tags",
          "default_level", "has_navigator", "has_book", "books", "generated_concepts")


def graph_stats(data: dict[str, Any]) -> dict[str, int]:
    """The counts the apps' catalog carries, computed as concept-book-press's sync does (from the declarations)."""
    prims, concepts, apps = (data.get(k) or {} for k in ("primitives", "concepts", "applications"))
    edges = sum(len((v or {}).get("composed_of") or []) for v in concepts.values())
    edges += sum(len((v or {}).get("needs") or []) for v in apps.values())
    return {"nodes": len(prims) + len(concepts) + len(apps), "edges": edges, "primitives": len(prims), "concepts": len(concepts),
            "applications": len(apps)}


def _pages(chapter_root: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(books, generated_concepts) as the apps' catalog lists them, from output/<level>.<lang>[/<model>]/html/."""
    books, concepts = [], []
    out = chapter_root / "output"
    files = sorted(out.glob("*/*/html/*.html")) + sorted(out.glob("*/html/*.html")) if out.is_dir() else []
    for f in files:
        rel = f.relative_to(chapter_root).parts
        lang = rel[1].partition(".")[2]
        model = rel[2] if len(rel) == 5 else ""
        stem, suffix = f.stem, lang_suffix(lang)
        kind, _, name = stem.partition("_")
        if suffix and name.endswith(suffix):
            name = name[: -len(suffix)]
        rec = {"file": "/".join(rel), "model": model, "language": lang}
        if kind == "book":
            books.append({"target": name, **rec})
        elif kind == "concept":
            concepts.append({"name": name, "label": name.replace("_", " ").title(), **rec})
    concepts.sort(key=lambda c: (c["label"], c["file"]))
    return books, concepts


def _pdfs(chapter_root: Path) -> list[dict[str, str]]:
    """The exported PDFs (`bookgraph serve`'s Export PDF), as the apps' catalog lists them: output/<level>.<lang>[/<model>]/pdf/."""
    out = chapter_root / "output"
    files = sorted(out.glob("*/*/pdf/*.pdf")) + sorted(out.glob("*/pdf/*.pdf")) if out.is_dir() else []
    found = []
    for f in files:
        rel = f.relative_to(chapter_root).parts
        level, _, lang = rel[1].partition(".")
        name = f.stem.partition("_")[2]
        suffix = lang_suffix(lang)
        if suffix and name.endswith(suffix):
            name = name[: -len(suffix)]
        found.append({"target": name, "level": level, "language": lang, "model": rel[2] if len(rel) == 5 else "", "file": "/".join(rel)})
    return found


def chapter_entry(book: Book, ch: Any) -> dict[str, Any]:
    """One chapter's catalog.json entry."""
    entry: dict[str, Any] = dict(ch.catalog)
    entry.setdefault("name", ch.title)
    if ch.description:
        entry.setdefault("description", ch.description)
    entry.setdefault("tags", list(book.tags))
    entry.setdefault("default_level", book.levels[0])
    if book.source:
        entry.setdefault("source", book.source)
    g = load_graph(book.graph_path(ch))
    books, concepts = _pages(book.chapter_root(ch))
    entry.update(id=book.global_id(ch), capstone=ch.capstone or g.default_target(), **graph_stats(g.data),
                 has_navigator=(book.chapter_root(ch) / "output" / "graph.html").is_file(), has_book=bool(books), books=books,
                 generated_concepts=concepts)
    if pdfs := _pdfs(book.chapter_root(ch)):
        entry["pdfs"] = pdfs
    else:
        entry.pop("pdfs", None)
    return {**{k: entry[k] for k in _ORDER if k in entry}, **{k: v for k, v in entry.items() if k not in _ORDER}}


def _write_list(path: Path, catalog: list[dict[str, Any]]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, ensure_ascii=False, indent=2)            # as the apps' catalog_lock.py writes it
        fh.write("\n")
    os.replace(tmp, path)


def ensure_navigators(book: Book, chapters: list[Any] | None = None, *, force: bool = False) -> list[Path]:
    """Write <chapter>/output/graph.html (bookgraph.navigator) where it is missing or made from another version of the
    chapter's graph.yaml (the hash in <chapter>/.navigator), or everywhere with `force`. A migrated chapter keeps the
    navigator it came with until its graph is edited."""
    written = []
    for ch in chapters if chapters is not None else book.chapters:
        root = book.chapter_root(ch)
        out, stamp = root / "output" / "graph.html", root / ".navigator"
        if force or navigator_stale(book.graph_path(ch), out, stamp):
            written.append(write_navigator(book.graph_path(ch), out, stamp))
    return written


def export_book(book: Book, domains: str | Path) -> list[dict[str, Any]]:
    """Write the book's chapters into `domains` (an app's public/domains) and merge their entries into its catalog.json.

    Entries of other chapters are kept, in place; a chapter already listed is replaced where it stands. Returns the entries
    written. Raises BookError for a chapter whose graph cannot be read, before anything is written."""
    domains = Path(domains)
    for ch in book.chapters:                                               # every graph is read before any write
        load_graph(book.graph_path(ch))
    ensure_navigators(book)
    entries = [(ch, chapter_entry(book, ch)) for ch in book.chapters]
    for ch, entry in entries:
        dest = domains / safe_segment(entry["id"], "global id")
        (dest / "input").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(book.graph_path(ch), dest / "input" / "graph.yaml")
        src = book.chapter_root(ch)
        if (src / "concept_sources.yaml").is_file():
            shutil.copyfile(src / "concept_sources.yaml", dest / "input" / "concept_sources.yaml")
        if (src / "output").is_dir():
            shutil.copytree(src / "output", dest / "output", dirs_exist_ok=True)
    shared = book.root / "domains"                                     # book-level files (super-graph, groups.json, ...)
    for f in sorted(shared.iterdir()) if shared.is_dir() else []:
        if f.is_file():
            domains.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, domains / f.name)
    path = domains / "catalog.json"
    with _locked(domains / "catalog.json.lock"):
        try:
            catalog = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except ValueError as e:
            raise BookError(f"{path} is not valid JSON ({e}); fix it first, it is never overwritten") from e
        if not isinstance(catalog, list):
            raise BookError(f"{path}: expected the apps' flat list of chapters")
        at = {e.get("id"): i for i, e in enumerate(catalog)}
        for _, entry in entries:
            if entry["id"] in at:
                catalog[at[entry["id"]]] = entry
            else:
                at[entry["id"]] = len(catalog)
                catalog.append(entry)
        _write_list(path, catalog)
    return [e for _, e in entries]


def _variants(domains: Path, ids: list[str]) -> tuple[list[str], list[str], list[str]]:
    levels, langs, models = set(), [], []
    for gid in ids:
        out = domains / gid / "output"
        for v in sorted(out.glob("*.*")) if out.is_dir() else []:
            if not v.is_dir():
                continue
            level, _, lang = v.name.partition(".")
            levels.add(level)
            if lang not in langs:
                langs.append(lang)
            for m in sorted(v.iterdir()):
                if m.is_dir() and m.name != "html" and m.name not in models:
                    models.append(m.name)
    return [lv for lv in LEVELS if lv in levels], sorted(langs, key=lambda x: (x != "en", langs.index(x))), models


def _site_strings(repo: Path) -> dict[str, Any]:
    """The per-book strings of a repo's frontend, as book.yaml `site:` keys: index.html's title and description, and
    locales/ui.yaml's app title, tagline and tag names (the only things a current-version repo customizes there)."""
    import re
    out: dict[str, Any] = {}
    page = repo / "index.html"
    if page.is_file():
        text = page.read_text(encoding="utf-8")
        if m := re.search(r'<meta name="description" content="([^"]*)"', text):
            out["description"] = __import__("html").unescape(m.group(1))
        if m := re.search(r"<title>(.*?)</title>", text, re.S):
            out["title"] = {"en": __import__("html").unescape(m.group(1).strip())}
    ui_file = repo / "locales" / "ui.yaml"
    if ui_file.is_file():
        try:
            ui = yaml.safe_load(ui_file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            ui = {}
        app = ui.get("app") or {}
        if isinstance(app.get("title"), dict):
            out["title"] = {str(k): str(v) for k, v in app["title"].items()}
        if isinstance(app.get("tagline"), dict):
            out["tagline"] = {str(k): str(v) for k, v in app["tagline"].items()}
        if isinstance(ui.get("tag"), dict) and ui["tag"]:
            out["tags"] = ui["tag"]
    return out


def migrate_repo(repo: str | Path, library: Path, *, press: str = "digital-duck", book: str = "", title: str = "",
                 adapter: str = "claude_cli") -> Book:
    """Import a concept-book app repo (`cb-calculus`, ...) into `<library>/<press>/<book>/`. Refuses to overwrite a book.

    Each catalog entry becomes a chapter: id `ch01` when the old id is `<book>_ch01`, else the old id; the old id is kept as
    the global id either way, so exports and URLs do not change. Models are written as `<adapter>:<folder>` so their page
    folders (and the legacy cache's model key) stay the same; check the adapter in book.yaml."""
    repo = Path(repo).expanduser().resolve()
    domains = repo / "public" / "domains"
    try:
        catalog = json.loads((domains / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BookError(f"{repo}: no readable public/domains/catalog.json ({e})") from e
    slug = safe_segment(book or repo.name.removeprefix("cb-").removeprefix("cb_"), "book")
    root = book_dir(library, press, slug)
    if (root / "book.yaml").exists():
        raise BookError(f"{root / 'book.yaml'} already exists")
    chapters, tags = [], []
    for e in catalog:
        gid = safe_segment(str(e.get("id", "")), "chapter id")
        src = domains / gid
        if not (src / "input" / "graph.yaml").is_file():
            raise BookError(f"{src}: no input/graph.yaml for catalog entry {gid!r}")
        cid = gid[len(slug) + 1:] if gid.startswith(slug + "_") and len(gid) > len(slug) + 1 else gid
        dest = root / "chapters" / cid
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / "input" / "graph.yaml", dest / "graph.yaml")
        if (src / "input" / "concept_sources.yaml").is_file():
            shutil.copyfile(src / "input" / "concept_sources.yaml", dest / "concept_sources.yaml")
        if (src / "output").is_dir():
            shutil.copytree(src / "output", dest / "output", dirs_exist_ok=True)
        if (dest / "output" / "graph.html").is_file():          # the repo's navigator matches the graph copied with it
            (dest / ".navigator").write_text(graph_hash(dest / "graph.yaml") + "\n", encoding="utf-8")
        ch: dict[str, Any] = {"id": cid, "title": str(e.get("name") or gid), "graph": f"chapters/{cid}/graph.yaml"}
        if e.get("capstone"):
            ch["capstone"] = e["capstone"]
        if f"{slug}_{cid}" != gid:
            ch["global_id"] = gid
        if e.get("description"):
            ch["description"] = e["description"]
        extra = {k: v for k, v in e.items() if k not in COMPUTED and k not in ("description",)}
        if extra.get("name") == ch["title"]:
            extra.pop("name")
        if extra:
            ch["catalog"] = extra
        chapters.append(ch)
        tags += [t for t in e.get("tags") or [] if t not in tags]
    ids = {str(e["id"]) for e in catalog}
    for f in sorted(domains.iterdir()):                                # book-level files, exported back unchanged
        if f.is_file() and f.name not in ("catalog.json", "catalog.json.lock") and f.name not in ids:
            (root / "domains").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, root / "domains" / f.name)
    levels, langs, models = _variants(domains, [str(e["id"]) for e in catalog])
    spec: dict[str, Any] = {"press": press, "book": slug, "title": title or slug.replace("-", " ").replace("_", " ").title(),
                            "levels": levels or ["core"], "languages": langs or ["en"],
                            "models": [f"{adapter}:{m}" for m in models], "tags": tags, "site": {"base": f"/{repo.name}/"}}
    spec["site"].update(_site_strings(repo))
    origin = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    if m := re.match(r"^(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?$", origin):
        spec["publish"] = {"repo": m.group(1)}                   # the repo's own GitHub Pages, as `npm run deploy` used it
    spl = repo / "spl" / ".spl"
    if (spl / "content_meta.db").is_file():
        spec["legacy_cache"] = str(spl)
    spec["chapters"] = chapters
    root.mkdir(parents=True, exist_ok=True)
    header = (f"# Migrated from {repo} by `bookgraph migrate`. Models are `{adapter}:<folder>`, so the page folders and the\n"
              f"# legacy cache's model key stay the same: check the adapter. Chapters keep their old ids as global_id.\n")
    (root / "book.yaml").write_text(header + yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    b = load_book(root)
    register_book(library, b)
    return b
