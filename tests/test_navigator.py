"""The chapter navigator (output/graph.html): the ported concept-book-base page, written by `export` and `bgr navigator`."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from bookgraph.book import load_book
from bookgraph.cli import main
from bookgraph.export import ensure_navigators, export_book
from bookgraph.navigator import render_navigator

BASE_SCRIPT = Path(__file__).resolve().parents[2] / "concept-book-base" / "scripts" / "concept_graph.py"


def graph_of(library: Path) -> Path:
    return library / "acme/demo/chapters/ch01/graph.yaml"


def test_page_carries_what_graphviewer_reads(library):
    html = render_navigator(graph_of(library))
    assert "<title>Concept Graph — graph</title>" in html and "vis-network@9.1.9" in html
    raw = json.loads(re.search(r"^const RAW = (\{.*\});$", html, re.M).group(1))
    nodes = {n["id"]: n for n in raw["nodes"]}
    assert nodes["counting"]["labels"] == {"zh": "计数"}                  # every language, so the app relabels in place
    assert nodes["budget"]["kind"] == "application" and {"from": "addition", "to": "budget"} in raw["edges"]


@pytest.mark.skipif(not BASE_SCRIPT.is_file(), reason="concept-book-base is not checked out next to book-graph")
def test_byte_identical_to_concept_book_base(library):
    spec = importlib.util.spec_from_file_location("cg_base", BASE_SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    g = graph_of(library)
    assert render_navigator(g) == m._to_html(m._load_yaml_graph(g), domain_name="graph")


def test_export_writes_missing_and_stale_navigators_only(library, tmp_path):
    book = load_book(library / "acme" / "demo")
    nav = book.chapter_root(book.chapters[0]) / "output" / "graph.html"
    e = export_book(book, tmp_path / "domains")[0]
    assert e["has_navigator"] and nav.is_file()
    assert (tmp_path / "domains/demo_ch01/output/graph.html").read_text() == nav.read_text()
    nav.write_text("<html>kept</html>")                                  # e.g. migrated with the repo: same graph, kept
    assert ensure_navigators(book) == [] and nav.read_text() == "<html>kept</html>"
    g = graph_of(library)
    g.write_text(g.read_text().replace("Combining two counts.", "Putting two counts together."))   # the graph was edited
    assert ensure_navigators(book) == [nav] and "Putting two counts together." in nav.read_text()
    assert not (tmp_path / "domains/demo_ch01/.navigator").exists()      # the stamp stays in the library


def test_cli_navigator(library):
    run = lambda *a: CliRunner().invoke(main, ["-L", str(library), *a])
    r = run("navigator", "acme/demo")
    assert r.exit_code == 0 and "1 of 1 navigator(s) written" in r.output
    assert "0 of 1" in run("navigator", "acme/demo").output
    assert "1 of 1" in run("navigator", "acme/demo", "-c", "ch01", "--force").output
