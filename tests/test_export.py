"""`bookgraph migrate` (a cb-* repo -> the library) and `bookgraph export` (a library book -> an app's public/domains)."""
from __future__ import annotations

import filecmp
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from bookgraph.book import load_book
from bookgraph.cli import main
from bookgraph.exceptions import BookError
from bookgraph.export import export_book, migrate_repo

from conftest import GRAPH, Scripted


def cb_repo(tmp_path: Path) -> Path:
    """A small concept-book app repo, shaped like cb-calculus: one chapter with pages, a navigator, a stale catalog."""
    repo = tmp_path / "cb-demo"
    d = repo / "public" / "domains"
    ch = d / "demo_ch01"
    (ch / "input").mkdir(parents=True)
    (ch / "input" / "graph.yaml").write_text(GRAPH, encoding="utf-8")
    (ch / "output").mkdir()
    (ch / "output" / "graph.html").write_text("<html>navigator</html>", encoding="utf-8")
    for variant, lang in (("core.en/sonnet", ""), ("core.zh/sonnet", "_zh")):
        html = ch / "output" / variant / "html"
        html.mkdir(parents=True)
        for node in ("counting", "budget"):
            (html / f"concept_{node}{lang}.html").write_text(f"<p>{node}{lang}</p>", encoding="utf-8")
        (html / f"book_budget{lang}.html").write_text("<p>book</p>", encoding="utf-8")
    (d / "graph.yaml").write_text("# super-graph\n", encoding="utf-8")
    catalog = [{"id": "demo_ch01", "name": "Demo Ch1", "description": "The first chapter.", "capstone": "budget", "nodes": 99,
                "tags": ["math"], "default_level": "core", "i18n": {"zh": {"name": "第一章"}}, "has_navigator": True, "has_book": True,
                "books": [{"target": "budget", "file": "output/core.en/sonnet/html/book_budget.html"}],
                "generated_concepts": [{"name": "counting_zh", "label": "Counting Zh", "language": "zh", "model": "sonnet",
                                        "file": "output/core.zh/sonnet/html/concept_counting_zh.html"}]}]
    (d / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    return repo


def files(d: Path) -> set[str]:
    return {str(p.relative_to(d)) for p in d.rglob("*") if p.is_file() and not p.name.startswith("catalog.json")}


def test_migrate_then_export_reproduces_the_repo(tmp_path):
    repo = cb_repo(tmp_path)
    book = migrate_repo(repo, tmp_path / "lib")
    ch = book.chapters[0]
    assert (book.press, book.book, ch.id, book.global_id(ch)) == ("digital-duck", "demo", "ch01", "demo_ch01")
    assert book.levels == ["core"] and book.languages == ["en", "zh"] and book.models == ["claude_cli:sonnet"]
    assert ch.title == "Demo Ch1" and ch.description == "The first chapter." and ch.catalog["i18n"] == {"zh": {"name": "第一章"}}
    out = tmp_path / "out"
    export_book(book, out)
    old, new = repo / "public" / "domains", out
    assert files(old) == files(new) and all(filecmp.cmp(old / f, new / f, shallow=False) for f in files(old))
    e = json.loads((new / "catalog.json").read_text())[0]
    assert e["id"] == "demo_ch01" and e["i18n"] == {"zh": {"name": "第一章"}} and e["default_level"] == "core"
    assert e["nodes"] == 5 and e["has_navigator"] and len(e["books"]) == 2                    # counts and pages from disk
    zh = [c for c in e["generated_concepts"] if c["language"] == "zh"]
    assert {c["name"] for c in zh} == {"counting", "budget"}                                   # not `counting_zh`
    assert all(set(b) == {"target", "file", "model", "language"} for b in e["books"])


def test_export_keeps_other_entries_and_replaces_its_own_in_place(library, tmp_path):
    out = tmp_path / "domains"
    out.mkdir()
    (out / "catalog.json").write_text(json.dumps([{"id": "other_ch01", "name": "Other"}, {"id": "demo_ch01", "name": "old"}]))
    book = load_book(library / "acme" / "demo")
    export_book(book, out)
    cat = json.loads((out / "catalog.json").read_text())
    assert [e["id"] for e in cat] == ["other_ch01", "demo_ch01"] and cat[1]["name"] == "Chapter 1"
    assert cat[1]["has_book"] is False and cat[1]["has_navigator"] is True           # written by export (bookgraph.navigator)


def test_generated_pages_are_exported(library, tmp_path):
    from bookgraph.builder import generate_chapter
    book = load_book(library / "acme" / "demo")
    assert generate_chapter(book, book.chapters[0], level="core", language="en", model="claude_cli:m1", llm=Scripted(),
                            log_dir=tmp_path / "log").ok
    out = tmp_path / "domains"
    e = export_book(book, out)[0]
    assert e["has_book"] and e["books"][0]["file"] == "output/core.en/m1/html/book_budget.html"
    assert (out / "demo_ch01" / e["books"][0]["file"]).is_file() and len(e["generated_concepts"]) == 5


def test_global_ids_and_refusals(tmp_path, library):
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text().replace("graph: chapters/ch01/graph.yaml}", "graph: chapters/ch01/graph.yaml, global_id: legacy_one}"))
    assert load_book(y).global_id(load_book(y).chapters[0]) == "legacy_one"
    y.write_text(y.read_text() + "  - {id: ch02, global_id: legacy_one}\n")
    with pytest.raises(BookError, match="share a global id"):
        load_book(y)
    repo = cb_repo(tmp_path)
    migrate_repo(repo, tmp_path / "lib")
    with pytest.raises(BookError, match="already exists"):
        migrate_repo(repo, tmp_path / "lib")
    (repo / "public/domains/catalog.json").write_text("{bad")
    with pytest.raises(BookError, match="catalog.json"):
        migrate_repo(repo, tmp_path / "lib2")


def test_cli_migrate_and_export(tmp_path):
    repo = cb_repo(tmp_path)
    run = lambda *a: CliRunner().invoke(main, ["-L", str(tmp_path / "lib"), *a])
    r = run("migrate", str(repo), "--press", "acme")
    assert r.exit_code == 0 and "1 chapter(s)" in r.output and (tmp_path / "lib/acme/demo/book.yaml").is_file()
    assert run("validate", "acme/demo").exit_code == 0
    r = run("export", "acme/demo", str(tmp_path / "out"))
    assert r.exit_code == 0 and "demo_ch01: 4 concept page(s), 2 book page(s)" in r.output
    assert (tmp_path / "out" / "graph.yaml").read_text() == "# super-graph\n"
