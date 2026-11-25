"""`bookgraph extract`: the extract_graph recipe with a scripted model (press's two passes, normalize, validate, repair)."""
from __future__ import annotations

import yaml
from click.testing import CliRunner

from bookgraph.book import load_book
from bookgraph.builder import extract_chapter, generate_chapter
from bookgraph.cli import main
from bookgraph.extract import normalize
from bookgraph.graph import load_graph

from conftest import Scripted

CHUNKS = {"chapter_number": 2, "chapter_title": "Rates", "chunks": [
    {"section_id": "ch02_1", "section_title": "2.1 Change", "text": "A quantity changes over time. The rate of change is ..."},
    {"section_id": "ch02_2", "section_title": "2.2 Speed", "text": "Speed is the rate at which distance changes ..."}]}

# Pass 1: a bare colon inside `defines` (press's YAML repair fixes it) and labels (press's graphs dropped them).
PASS1 = """```yaml
concepts:
  - id: quantity
    label: Quantity
    defines: Something measured: a length, a time.
    kind: primitive
    section_ids: [ch02_1]
  - id: rate_of_change
    label: Rate of Change
    defines: |
      How fast a quantity changes.
    kind: concept
    section_ids: [ch02_1]
  - id: speed
    label: Speed
    defines: |
      The rate at which distance changes.
    kind: application
    section_ids: [ch02_2]
```"""

# Pass 2: `time` is a concept with no prerequisite (-> primitive), `ghost` is unknown (dropped), tiers are wrong.
PASS2 = """domain: demo_ch02
primitives:
  quantity:
    defines: |
      Something measured: a length, a time.
    tier: 3
concepts:
  time:
    defines: |
      What clocks measure.
    composed_of: []
    tier: 1
  rate_of_change:
    defines: |
      How fast a quantity changes.
    composed_of: [quantity, time, ghost]
    tier: 1
applications:
  speed:
    defines: |
      The rate at which distance changes.
    needs: [rate_of_change]
    tier: 1
"""

CYCLE = PASS2.replace("needs: [rate_of_change]", "needs: [rate_of_change]\n").replace(
    "composed_of: [quantity, time, ghost]", "composed_of: [quantity, time, speed]")


class Extractor(Scripted):
    def __init__(self, pass2=PASS2, repair=PASS2, pass1=PASS1):
        super().__init__()
        self.answers = {"Identify the key concepts": pass1, "identify which other concepts": pass2, "fails these checks": repair}

    def generate(self, prompt, model=None, **opts):
        self.prompts.append(prompt)
        for marker, answer in self.answers.items():
            if marker in prompt:
                return answer
        raise AssertionError(prompt[:200])


def chapter_two(library):
    root = library / "acme/demo"
    (root / "chapters/ch02").mkdir()
    (root / "chapters/ch02/chunks.yaml").write_text(yaml.safe_dump(CHUNKS))
    y = root / "book.yaml"
    y.write_text(y.read_text() + "  - {id: ch02, title: Rates, graph: chapters/ch02/graph.yaml}\n")
    book = load_book(y)
    return book, book.chapter("ch02")


def test_extracts_normalizes_and_writes(library, tmp_path):
    book, ch = chapter_two(library)
    llm = Extractor()
    res = extract_chapter(book, ch, llm=llm, log_dir=tmp_path / "log")
    assert res.ok, res.error
    assert res.llm_calls == 2 and "the Demo Book textbook" in llm.prompts[0] and "physics" not in llm.prompts[0]
    g = load_graph(book.graph_path(ch))
    assert g.validate() == [] and g.kind("time") == "primitive" and g.kind("speed") == "application"
    assert g.prerequisites("rate_of_change") == ["quantity", "time"]                          # ghost dropped
    assert g.label("rate_of_change") == "Rate of Change"                                       # pass 1's label kept
    assert {n: g.node(n)["tier"] for n in g.G} == {"quantity": 0, "time": 0, "rate_of_change": 1, "speed": 2}
    assert "Something measured: a length, a time." in g.node("quantity")["defines"]          # the colon survived
    assert "moved to primitives" in res.summary and "dropped unknown prerequisite(s) ghost" in res.summary
    src = yaml.safe_load((book.chapter_root(ch) / "concept_sources.yaml").read_text())
    assert src["sources"]["speed"] == {"label": "Speed", "sections": [{"id": "ch02_2", "title": "2.2 Speed"}]}
    assert generate_chapter(book, ch, level="core", language="en", llm=Scripted(), log_dir=tmp_path / "log").ok   # usable


def test_a_cycle_is_repaired_with_one_more_call(library, tmp_path):
    book, ch = chapter_two(library)
    llm = Extractor(pass2=CYCLE, repair=PASS2)
    res = extract_chapter(book, ch, llm=llm, log_dir=tmp_path / "log")
    assert res.ok and res.llm_calls == 3
    repair = [p for p in llm.prompts if "fails these checks" in p][0]
    assert "cycle" in repair and "<GRAPH>" in repair


def test_still_invalid_keeps_a_draft_and_unreadable_stops(library, tmp_path):
    book, ch = chapter_two(library)
    res = extract_chapter(book, ch, llm=Extractor(pass2=CYCLE, repair=CYCLE), log_dir=tmp_path / "log")
    assert not res.ok and "exit invalid" in res.error and "graph.draft.yaml" in res.error
    assert (book.chapter_root(ch) / "graph.draft.yaml").is_file() and not book.graph_path(ch).exists()
    res = extract_chapter(book, ch, llm=Extractor(pass1="I cannot help with that."), log_dir=tmp_path / "log")
    assert not res.ok and "exit unreadable" in res.error and res.llm_calls == 1


def test_an_existing_graph_is_not_replaced_without_force(library, tmp_path):
    book = load_book(library / "acme" / "demo")
    ch = book.chapters[0]
    (book.chapter_root(ch) / "chunks.yaml").write_text(yaml.safe_dump(CHUNKS))
    res = extract_chapter(book, ch, llm=Extractor(), log_dir=tmp_path / "log")
    assert not res.ok and "already exists" in res.error and res.llm_calls == 0
    assert extract_chapter(book, ch, llm=Extractor(), force=True, log_dir=tmp_path / "log").ok


def test_normalize_promotes_and_demotes():
    g, notes = normalize({"primitives": {"a": {"defines": "x", "composed_of": ["b"]}}, "concepts": {"b": {"defines": "y"}}}, {}, "d")
    assert set(g["concepts"]) == {"a"} and set(g["primitives"]) == {"b"} and g["concepts"]["a"]["composed_of"] == ["b"]
    assert any("moved to concepts" in n for n in notes) and any("moved to primitives" in n for n in notes)


def test_cli_extract_needs_chunks(library):
    r = CliRunner().invoke(main, ["-L", str(library), "extract", "acme/demo", "-c", "ch01", "--force", "--llm", "mock"])
    assert r.exit_code == 1 and "no chunks" in r.output


def test_the_repair_fixes_a_bare_colon_and_leaves_valid_yaml_alone():
    """Two fixes to press's _repair_yaml: it let `a: b` through (it parses, as a mapping), and it broke valid YAML in which
    PyYAML had wrapped a quoted value onto two lines (31 of press's 190 extracted graphs)."""
    from bookgraph.extract import _read_yaml
    data, err = _read_yaml("concepts:\n  - id: q\n    defines: Something measured: a length, a time.\n")
    assert err == "" and data["concepts"][0]["defines"] == "Something measured: a length, a time."
    wrapped = yaml.safe_dump({"concepts": {"x": {"defines": "The recipe for a reduction from X to Y: transform, solve, "
                                                            "and map back, " * 3}}}, width=60)
    assert "'" in wrapped and _read_yaml(wrapped)[0] == yaml.safe_load(wrapped)
