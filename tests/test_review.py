"""Regressions for the Opus 5.5 review (docs/DEV/review-by-opus-5-5.md); B-numbers are its numbering."""
from __future__ import annotations

import json
from pathlib import Path
from collections import Counter

import pytest
from click.testing import CliRunner

from bookgraph.book import load_book, model_dir_name
from bookgraph.builder import generate_book, generate_chapter
from bookgraph.catalog import read_catalog, register_book
from bookgraph.cli import main
from bookgraph.exceptions import BookError
from bookgraph.graph import load_graph, retitle_heading
from bookgraph.render import _md_to_html, fill
from bookgraph.run import run_recipe
from bookgraph.steps import cache_salt, prepare, write_root_ok

from conftest import GRAPH, Scripted


def gen(library, llm, tmp_path, **kw):
    book = load_book(library / "acme" / "demo")
    return book, generate_chapter(book, book.chapters[0], level="core", language=kw.pop("language", "en"), llm=llm,
                                  log_dir=tmp_path / "runlog", **kw)


def cyclic(library):
    g = library / "acme/demo/chapters/ch01/graph.yaml"
    g.write_text(g.read_text() + "  loop_a: {needs: [loop_b], defines: x}\n  loop_b: {needs: [loop_a], defines: y}\n")
    return g


# B1
def test_every_step_after_the_payoff_runs_once(library, tmp_path):
    events = []
    book = load_book(library / "acme" / "demo")
    res = generate_chapter(book, book.chapters[0], level="core", language="en", llm=Scripted(), log_dir=tmp_path / "runlog",
                           on_node=events.append)
    assert res.ok
    seen = Counter(e["node"] for e in events)
    assert seen["p_write"] == seen["p_store"] == seen["p_final"] == seen["index"] == seen["done"] == 1


# B2
def test_policy_refuses_a_folder_outside_the_roots_with_no_model_call(library, tmp_path):
    llm = Scripted()
    run = run_recipe("generate_chapter", {"chapter_graph": str(library / "acme/demo/chapters/ch01/graph.yaml"),
                                          "output_dir": "/srv/bg-out", "cache_dir": "/srv/bg-cache"}, llm=llm, log_dir=tmp_path / "log")
    assert run.exit == "refused" and run.error_code == "E_GENERATE_REFUSED" and llm.prompts == []


def test_extra_write_roots_are_a_parameter_not_the_environment(monkeypatch):
    assert not write_root_ok("/srv/books/x")
    assert write_root_ok("/srv/books/x", "/srv/books")
    assert not write_root_ok("/srv/books/../etc", "/srv/books")


# B3
def test_subject_decides_the_rigor_note(library):
    base = {"chapter_graph": str(library / "acme/demo/chapters/ch01/graph.yaml"), "style": "core", "language": "en",
            "output_dir": "/tmp/o", "cache_dir": "/tmp/c"}
    plain = prepare({**base})["style_guide"]
    assert prepare({**base, "subject": "chemistry"})["style_guide"] != plain
    assert prepare({**base, "subject": "chinese-idioms"})["style_guide"] != prepare({**base, "subject": "chemistry"})["style_guide"]


# B4
def test_dotted_model_names_and_adapters_sharing_a_folder(library, tmp_path):
    assert model_dir_name("ollama:qwen2.5:7b") == "qwen2-5-7b"
    assert model_dir_name("openrouter:anthropic/claude-3.5-sonnet") == "anthropic-claude-3-5-sonnet"
    _, a = gen(library, Scripted(), tmp_path, model="ollama:qwen2.5:7b")
    assert a.ok and "/core.en/qwen2-5-7b/html/" in a.book
    _, b = gen(library, Scripted(), tmp_path, model="openrouter:qwen2.5:7b")
    assert b.ok and b.llm_calls == 6          # same folder, but the other adapter's cache is not served as this one's


# B5
def test_cycle_is_a_book_error_not_a_traceback(library, tmp_path):
    cyclic(library)
    with pytest.raises(BookError, match="cycle"):
        load_graph(library / "acme/demo/chapters/ch01/graph.yaml").default_target()
    r = CliRunner().invoke(main, ["-L", str(library), "order", str(library / "acme/demo/chapters/ch01/graph.yaml")])
    assert r.exit_code == 1 and "cycle" in r.output and "Traceback" not in r.output
    cat = register_book(library, load_book(library / "acme" / "demo"))                       # the catalog notes it, does not crash
    assert "cycle" in cat["presses"][0]["books"][0]["chapters"][0]["error"]


# B6
def test_backslash_in_a_label_survives():
    out = retitle_heading("## old\n\nbody", "x", r"$\alpha$ decay \d \g<0>", True)
    assert out.startswith(r"## $\alpha$ decay \d \g<0>")
    assert retitle_heading("## x\n", "x", r"\mathbb{R}^n", False).startswith(r"## \mathbb{R}^n")


# B7
def test_model_output_is_escaped():
    html = _md_to_html("Hi <script>alert(1)</script> and `a<b`\n\n```mermaid\ngraph TD\nA-->B</pre><img src=x onerror=1>\n```\n\n"
                       '<figure class="cb-figure">\n<img src="https://evil.example/x.png" alt="a" class="cb-figure-img">\n'
                       "<figcaption>c</figcaption>\n</figure>\n\n$$\n<b>x</b>\n$$\n")
    assert "<script" not in html and "<img" not in html and "<figure" not in html and "<b>" not in html
    assert "A-->B" in html and "&lt;/pre>" in html


def test_an_allowed_figure_passes_through():
    fig = ('<figure class="cb-figure">\n  <img src="https://upload.wikimedia.org/a.png" alt="A" class="cb-figure-img">\n'
           '  <figcaption>Cap. Source: <a href="https://commons.wikimedia.org/x">T</a>, CC0.</figcaption>\n</figure>')
    assert _md_to_html(fig) == fig


# B8
def test_duplicate_ids_and_string_deps(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("primitives: {x: {defines: a}}\nconcepts: {x: {composed_of: [], defines: b}}\n")
    assert any("both primitives and concepts" in m for m in load_graph(p).validate())
    p.write_text("primitives: {a: {defines: a}}\nconcepts: {c: {composed_of: a, defines: b}}\n")
    with pytest.raises(BookError, match="list"):
        load_graph(p)


# B9
def test_target_node_needs_a_chapter(library):
    r = CliRunner().invoke(main, ["-L", str(library), "generate", "acme/demo", "--llm", "mock:m", "--target-node", "budget"])
    assert r.exit_code == 2 and "-c" in r.output


# B10
def test_a_bad_chapter_does_not_abort_the_book_or_the_catalog(library, tmp_path):
    book_yaml = library / "acme/demo/book.yaml"
    book_yaml.write_text(book_yaml.read_text() + "  - {id: ch02, title: Two, graph: chapters/ch02/graph.yaml}\n")
    book = load_book(library / "acme" / "demo")
    res = generate_book(book, library, llm=Scripted(), log_dir=tmp_path / "runlog")
    assert [c.ok for c in res.chapters] == [True, False] and "no graph" in res.chapters[1].error
    assert read_catalog(library)["presses"][0]["books"][0]["chapters"][0]["books"]


# B11
def test_primitive_check_is_whole_word_and_names_the_offenders(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("primitives:\n  set: {defines: a}\n  rate: {defines: b, labels: {zh: 速率}}\nconcepts:\n  c: {composed_of: [], defines: x}\n")
    g = load_graph(p)
    assert g.new_primitives("the settings page", "c") == []                 # `set` is not a word in "settings"
    assert g.new_primitives("a set of things", "c") == ["set"]
    assert g.new_primitives("速率很重要", "c", "zh") == ["rate"]            # the page language's label
    assert g.new_primitives("a set", "set") == []                            # the node itself


# B12
def test_cache_misses_when_the_prompt_inputs_change(library, tmp_path):
    gen(library, Scripted(), tmp_path)
    assert cache_salt("guide A", 2) != cache_salt("guide B", 2) != cache_salt("guide A", 3)
    g = library / "acme/demo/chapters/ch01/graph.yaml"
    g.write_text(g.read_text().replace("Combining two counts.", "Putting two counts together."))
    llm = Scripted()
    _, res = gen(library, llm, tmp_path)
    assert res.llm_calls == 1          # only addition's definition changed; the payoff prompt carries budget's own text and its prerequisites' labels


def test_a_run_with_some_sections_cached(library, tmp_path):
    gen(library, Scripted(), tmp_path)
    cache = library / "acme/demo/chapters/ch01/.bg_cache"
    next(cache.glob("addition-*.json")).unlink()
    _, res = gen(library, Scripted(), tmp_path)
    assert res.ok and res.llm_calls == 1


# B13
def test_corrupt_catalog_is_never_overwritten(library):
    (library / "catalog.json").write_text("{not json")
    with pytest.raises(BookError, match="not valid JSON"):
        register_book(library, load_book(library / "acme" / "demo"))
    assert (library / "catalog.json").read_text() == "{not json"


def test_fill_is_one_pass():
    assert fill("{a} {b}", a="{b}", b="X") == "{b} X"


def test_library_entry_beats_a_folder_of_the_same_name(library, tmp_path, monkeypatch):
    other = tmp_path / "cwd" / "acme" / "demo"
    other.mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "cwd")
    r = CliRunner().invoke(main, ["-L", str(library), "validate", "acme/demo"])
    assert r.exit_code == 0 and "ok" in r.output


# CLI
def test_cli_commands_and_exit_codes(library):
    run = lambda *a: CliRunner().invoke(main, ["-L", str(library), *a])
    assert run("validate", "acme/demo").exit_code == 0
    assert run("order", str(library / "acme/demo/chapters/ch01/graph.yaml")).output.split() == ["idea", "quantity", "counting", "addition", "budget"]
    assert run("catalog").exit_code == 0 and run("list").output.split()[:2] == ["acme", "demo"]
    r = run("validate", "acme/nope")
    assert r.exit_code == 1 and "Error" in r.output
    new = run("new", "acme", "second")
    assert new.exit_code == 0 and run("new", "acme", "second").exit_code == 1
    bad = library / "acme/demo/chapters/ch01/graph.yaml"
    bad.write_text("primitives: {a: {}}\n")
    r = run("validate", "acme/demo", "--format", "json")
    assert r.exit_code == 1 and json.loads(r.output)[0]["ok"] is False


# §8.2 follow-ups
def test_primitives_already_taught_are_not_new(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("primitives: {idea: {defines: a}, quantity: {defines: b}, rate: {defines: r}}\nconcepts:\n"
                 "  counting: {composed_of: [idea, quantity], defines: c}\n  addition: {composed_of: [counting], defines: d}\n")
    g = load_graph(p)
    assert g.new_primitives("addition builds on the idea of a quantity", "addition") == []        # ancestors, taught earlier
    assert g.new_primitives("addition at a rate", "addition") == ["rate"]                         # not an ancestor: new


def test_unterminated_figure_does_not_swallow_the_section():
    html = _md_to_html('Intro\n\n<figure class="cb-figure">\n<img src=x>\n\nMore text after.')
    assert "<p>More text after.</p>" in html and "<img" not in html and "&lt;img src=x&gt;" in html
    assert "&lt;figure" in _md_to_html('<figure class="cb-figure">\n<img src=x>')                # still open at the end


def test_local_path_with_a_dot_still_works(library, tmp_path, monkeypatch):
    import shutil
    cwd = tmp_path / "cwd"
    shutil.copytree(library / "acme" / "demo", cwd / "books.v2" / "demo")
    monkeypatch.chdir(cwd)
    r = CliRunner().invoke(main, ["-L", str(library), "validate", "books.v2/demo"])
    assert r.exit_code == 0 and "ok" in r.output


# §9.2 follow-ups
def _spl3_store(spl_dir, entries):
    """A store laid out as spl3 writes it: content_cache.db (pickled text, as dd_cache does) and content_meta.db."""
    import pickle, sqlite3
    from bookgraph.legacy import legacy_key
    spl_dir.mkdir(parents=True)
    s = sqlite3.connect(spl_dir / "content_cache.db")
    s.execute("CREATE TABLE cache (key TEXT PRIMARY KEY, value BLOB NOT NULL, expires_at REAL)")
    m = sqlite3.connect(spl_dir / "content_meta.db")
    m.execute("CREATE TABLE content_meta (key TEXT PRIMARY KEY, concept TEXT, params TEXT, stale INTEGER DEFAULT 0)")
    for concept, params, text, stale in entries:
        k = legacy_key(concept, params)
        s.execute("INSERT INTO cache VALUES (?, ?, NULL)", (k, pickle.dumps(pickle.dumps(text))))
        m.execute("INSERT INTO content_meta VALUES (?, ?, ?, ?)", (k, concept, json.dumps(params), stale))
    s.commit(); m.commit(); s.close(); m.close()


def test_legacy_spl3_cache_is_reused_and_copied(library, tmp_path):
    from bookgraph.legacy import legacy_params
    g = load_graph(library / "acme/demo/chapters/ch01/graph.yaml")
    p = lambda node: legacy_params("en", "core", "m1", g.context(node))
    spl = tmp_path / "cb-demo" / "spl" / ".spl"
    _spl3_store(spl, [("addition", p("addition"), "## Addition\n\nFrom the SPL app.", 0),
                      ("counting", p("counting"), "## Counting\n\nStale text.", 1),               # stale: a miss, as in spl3
                      ("budget__payoff", p("budget"), "## Payoff\n\nOld payoff.", 0)])
    book_yaml = library / "acme/demo/book.yaml"
    book_yaml.write_text(book_yaml.read_text() + f"legacy_cache: {spl}\n")
    book = load_book(library / "acme" / "demo")
    run1 = generate_chapter(book, book.chapters[0], level="core", language="en", model="ollama:m1", llm=Scripted(),
                            log_dir=tmp_path / "runlog")
    assert run1.ok and run1.llm_calls == 4                       # 5 sections + payoff, minus addition and the payoff
    assert "addition: legacy -> " in run1.manifest and "counting: generated" in run1.manifest
    out = library / "acme/demo/chapters/ch01/output/core.en/m1/html"
    assert "From the SPL app." in (out / "concept_addition.html").read_text()
    assert "Old payoff." in (out / "book_budget.html").read_text()
    book_yaml.write_text(book_yaml.read_text().replace(f"legacy_cache: {spl}\n", ""))       # copied: no longer needed
    run2 = generate_chapter(load_book(library / "acme" / "demo"), book.chapters[0], level="core", language="en",
                            model="ollama:m1", llm=Scripted(), log_dir=tmp_path / "runlog")
    assert "addition: cache -> " in run2.manifest and run2.llm_calls == 1   # only the payoff, which is read, not copied


def test_legacy_reader_refuses_pickled_objects(tmp_path):
    import pickle, sqlite3
    from bookgraph.legacy import legacy_get, legacy_key
    _spl3_store(tmp_path / "s", [])
    s = sqlite3.connect(tmp_path / "s" / "content_cache.db")
    s.execute("INSERT INTO cache VALUES (?, ?, NULL)", (legacy_key("x", {}), pickle.dumps(Path("/etc/passwd"))))
    s.commit()
    m = sqlite3.connect(tmp_path / "s" / "content_meta.db")
    m.execute("INSERT INTO content_meta VALUES (?, 'x', '{}', 0)", (legacy_key("x", {}),)); m.commit()
    assert legacy_get(tmp_path / "s", "x", {}) is None and legacy_get(tmp_path / "missing", "x", {}) is None


def test_footer_keeps_the_spl_credit_class(library, tmp_path):
    _, res = gen(library, Scripted(), tmp_path)
    page = (library / "acme/demo/chapters/ch01/output/core.en/html/concept_addition.html").read_text()
    assert 'class="spl-credit bg-credit"' in page                 # conceptbook-app strips `.spl-credit` before reading text
