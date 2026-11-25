"""`bookgraph validate`: the capstone check (an error) and the warnings (a capstone that is not an application, tiers)."""
from __future__ import annotations

import json

from click.testing import CliRunner

from bookgraph.cli import main
from bookgraph.graph import load_graph


def run(library, *a):
    return CliRunner().invoke(main, ["-L", str(library), "validate", "acme/demo", *a])


def set_capstone(library, node):
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text().replace("graph: chapters/ch01/graph.yaml}", f"graph: chapters/ch01/graph.yaml, capstone: {node}}}"))


def test_a_capstone_that_is_not_a_node_fails(library):
    set_capstone(library, "ghost")
    r = run(library)
    assert r.exit_code == 1 and "capstone 'ghost' is not a node of this graph" in r.output


def test_a_concept_capstone_is_a_warning_unless_strict(library):
    set_capstone(library, "addition")
    r = run(library)
    assert r.exit_code == 0 and "warning: capstone 'addition' is a concept, not an application" in r.output
    assert run(library, "--strict").exit_code == 1
    data = json.loads(run(library, "--format", "json").output)[0]
    assert data["ok"] and data["problems"] == [] and len(data["warnings"]) == 1


def test_tiers_that_do_not_rise_along_an_edge(library):
    g = library / "acme/demo/chapters/ch01/graph.yaml"
    g.write_text(g.read_text().replace("    defines: Assigning numbers to a collection.", "    defines: Assigning numbers to a collection.\n    tier: 2")
                 .replace("    defines: Combining two counts.", "    defines: Combining two counts.\n    tier: 2"))
    assert load_graph(g).warnings() == ["1 prerequisite(s) at the same or a higher tier than the node that needs them: "
                                        "counting (tier 2) -> addition (tier 2)"]
    assert "warning: 1 prerequisite(s)" in run(library).output and run(library).exit_code == 0
    g.write_text(g.read_text().replace("    defines: Combining two counts.\n    tier: 2", "    defines: Combining two counts.\n    tier: 3"))
    assert load_graph(g).warnings() == []


def test_the_starter_book_is_clean(library):
    r = run(library, "--strict")
    assert r.exit_code == 0 and "warning" not in r.output
