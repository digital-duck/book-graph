from __future__ import annotations

import json

import pytest

from bookgraph.book import load_book
from bookgraph.builder import generate_book, generate_chapter
from bookgraph.catalog import read_catalog
from bookgraph.exceptions import BookError

from conftest import Scripted


def run(library, llm, tmp_path, **kw):
    book = load_book(library / "acme" / "demo")
    return book, generate_chapter(book, book.chapters[0], level="core", language=kw.pop("language", "en"), llm=llm,
                                  log_dir=tmp_path / "runlog", **kw)


def test_generates_pages_payoff_and_index(library, tmp_path):
    llm = Scripted()
    book, res = run(library, llm, tmp_path)
    assert res.ok, res.error
    out = library / "acme/demo/chapters/ch01/output/core.en/html"
    assert {p.name for p in out.iterdir()} == {"concept_idea.html", "concept_quantity.html", "concept_counting.html",
                                               "concept_addition.html", "concept_budget.html", "book_budget.html"}
    index = (out / "book_budget.html").read_text()
    assert "It all comes together." in index and "concept_counting.html" in index
    assert "Body of Addition" in (out / "concept_addition.html").read_text()
    assert res.llm_calls == 6                                       # 5 sections + the payoff
    assert len(res.manifest.strip().splitlines()) == 5


def test_second_run_is_all_cache(library, tmp_path):
    run(library, Scripted(), tmp_path)
    llm = Scripted()
    _, res = run(library, llm, tmp_path)
    assert res.ok and res.llm_calls == 0 and llm.prompts == []
    assert "generated" not in res.manifest
    _, res = run(library, Scripted(), tmp_path, skip_cache=True)
    assert res.llm_calls == 6


def test_language_is_its_own_cache_slot_and_suffix(library, tmp_path):
    run(library, Scripted(), tmp_path)
    llm = Scripted()
    _, res = run(library, llm, tmp_path, language="zh")
    assert res.ok and res.llm_calls == 6
    out = library / "acme/demo/chapters/ch01/output/core.zh/html"
    assert (out / "concept_counting_zh.html").exists() and "计数" in (out / "concept_counting_zh.html").read_text()
    assert any("Chinese" in p for p in llm.prompts)


def test_primitive_budget_triggers_one_refinement(library, tmp_path):
    llm = Scripted(over_budget=True)
    _, res = run(library, llm, tmp_path, primitive_budget=1)
    assert res.ok
    assert sum("Revise the section below" in p for p in llm.prompts) == 5       # every section names 2 primitives, budget 1
    assert res.llm_calls == 11                                                  # 5 written + 5 refined + the payoff


def test_non_application_target_has_no_payoff(library, tmp_path):
    llm = Scripted()
    _, res = run(library, llm, tmp_path, target="addition")
    assert res.ok and res.llm_calls == 4                                        # idea, quantity, counting, addition
    assert not any("capstone" in p for p in llm.prompts)
    assert (library / "acme/demo/chapters/ch01/output/core.en/html/book_addition.html").exists()


def test_broken_graph_stops_before_any_model_call(library, tmp_path):
    g = library / "acme/demo/chapters/ch01/graph.yaml"
    g.write_text(g.read_text() + "  loop_a: {needs: [loop_b], defines: x}\n  loop_b: {needs: [loop_a], defines: y}\n")
    llm = Scripted()
    _, res = run(library, llm, tmp_path)
    assert not res.ok and llm.prompts == []


def test_refuses_output_outside_home_and_tmp(library, tmp_path, monkeypatch):
    from bookgraph.steps import write_root_ok
    assert write_root_ok(str(tmp_path / "x")) and not write_root_ok("/etc/x") and not write_root_ok("relative/x")
    assert not write_root_ok(str(tmp_path / ".." / ".." / "etc"))


def test_generate_book_updates_catalog(library, tmp_path):
    book = load_book(library / "acme" / "demo")
    res = generate_book(book, library, llm=Scripted(), log_dir=tmp_path / "runlog")
    assert res.ok
    ch = read_catalog(library)["presses"][0]["books"][0]["chapters"][0]
    assert ch["books"][0]["target"] == "budget" and ch["books"][0]["level"] == "core" and ch["books"][0]["language"] == "en"


def test_generate_book_needs_a_model(library):
    book = load_book(library / "acme" / "demo")
    with pytest.raises(BookError, match="no model"):
        generate_book(book, library)
