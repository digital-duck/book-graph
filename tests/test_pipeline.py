"""The asynchronous pipeline (bookgraph.pipeline): manifests, review gates, readiness, resume, and the dispatcher."""
from __future__ import annotations

import time

import pytest
import yaml
from click.testing import CliRunner

from bookgraph import pipeline as pl
from bookgraph.book import load_book
from bookgraph.cli import main

from conftest import Scripted


def names(tasks):
    return [t.name for t in tasks]


def tick(book, library, llm=None):
    return names(pl.dispatch(load_book(book.root), library, foreground=True, llm=llm))


def approve(book, step, ch="ch01"):
    b = load_book(book.root)
    pl.review(b, step, b.chapter(ch) if step in pl.CHAPTER_STEPS else None, "approve", by="tester")


@pytest.fixture
def book(library, monkeypatch):
    """The demo book (path A: an authored graph), with build and publish stubbed (no npm, no git)."""
    def build(book, out, **kw):
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text("site")
        return out
    monkeypatch.setattr("bookgraph.build.build_site", build)
    monkeypatch.setattr("bookgraph.publish.publish_site", lambda book, site, **kw: {"summary": "pushed (stub)", "commit": "abc"})
    return load_book(library / "acme" / "demo")


def test_path_a_runs_step_by_step_and_waits_for_review_at_every_step(book, library):
    assert tick(book, library) == ["3-validate ch01"]                          # path A starts at 3-validate
    m = pl.read_manifest(book, "3-validate", book.chapters[0])
    assert m["status"] == "OK" and m["review"]["status"] == "pending" and m["inputs"]["graph"] != "missing"
    assert tick(book, library) == []                                           # waits for a human
    approve(book, "3-validate")
    assert tick(book, library, Scripted()) == ["4-generate ch01"]
    m = pl.read_manifest(book, "4-generate", book.chapters[0])
    assert m["status"] == "OK" and m["details"]["llm_calls"] == 6 and m["details"]["run_id"]
    approve(book, "4-generate")
    assert tick(book, library) == ["5-build"]
    approve(book, "5-build")
    assert tick(book, library) == ["6-publish"]                               # a no-op, approved by itself: nothing is pushed
    m = pl.read_manifest(book, "6-publish")
    assert m["status"] == "OK" and m["details"] == {"noop": True} and m["review"]["status"] == "approved"
    assert tick(book, library) == []
    path = book.root / "pipeline" / "ch01" / "3-validate.out.yaml"
    assert path.is_file() and yaml.safe_load(path.read_text())["review"]["by"] == "tester"


def test_a_changed_input_reruns_the_step_and_invalidates_what_follows(book, library):
    tick(book, library)
    approve(book, "3-validate")
    tick(book, library, Scripted())
    approve(book, "4-generate")
    g = book.graph_path(book.chapters[0])
    g.write_text(g.read_text().replace("Combining two counts.", "Putting two counts together."))   # a human edits the graph
    ready = names(pl.ready_tasks(load_book(book.root)))
    assert "3-validate ch01" in ready and "5-build" not in ready                 # validate is stale; build waits
    tick(book, library)
    assert pl.readiness(load_book(book.root), "4-generate", book.chapters[0]) == (False, "3-validate not approved (or stale)")
    approve(book, "3-validate")
    assert pl.readiness(load_book(book.root), "4-generate", book.chapters[0])[1] == "inputs changed"


def test_not_ok_waits_for_a_fix_or_a_retry(book, library):
    g = book.graph_path(book.chapters[0])
    good = g.read_text()
    g.write_text(good + "  loop_a: {needs: [loop_b], defines: x}\n  loop_b: {needs: [loop_a], defines: y}\n")
    tick(book, library)
    m = pl.read_manifest(book, "3-validate", book.chapters[0])
    assert m["status"] == "NOT_OK" and "cycle" in m["error"]
    assert tick(book, library) == []                                           # not rerun on its own
    with pytest.raises(Exception, match="only an OK step can be approved"):
        approve(book, "3-validate")
    pl.review(book, "3-validate", book.chapters[0], "retry")
    assert tick(book, library) == ["3-validate ch01"]                          # retry: runs again (still NOT_OK)
    g.write_text(good)                                                         # the fix
    assert tick(book, library) == ["3-validate ch01"]
    assert pl.read_manifest(book, "3-validate", book.chapters[0])["status"] == "OK"


def test_a_failed_generate_resumes_its_run(book, library):
    class Dies(Scripted):
        def generate(self, prompt, model=None, **opts):
            if len(self.prompts) >= 3:
                raise ValueError("model gone")
            return super().generate(prompt, model, **opts)
    tick(book, library)
    approve(book, "3-validate")
    tick(book, library, Dies())
    m = pl.read_manifest(book, "4-generate", book.chapters[0])
    assert m["status"] == "NOT_OK" and m["details"]["llm_calls"] == 4
    pl.review(book, "4-generate", book.chapters[0], "retry")
    tick(book, library, Scripted())
    m2 = pl.read_manifest(book, "4-generate", book.chapters[0])
    assert m2["status"] == "OK" and m2["details"]["resumed"] and m2["details"]["run_id"] == m["details"]["run_id"]


def test_reject_reruns_and_auto_approve_lets_a_step_through(book, library):
    tick(book, library)
    pl.review(book, "3-validate", book.chapters[0], "reject", note="check the capstone", by="tester")
    assert pl.read_manifest(book, "3-validate", book.chapters[0])["review"]["note"] == "check the capstone"
    assert tick(book, library) == ["3-validate ch01"]
    y = book.root / "book.yaml"
    y.write_text(y.read_text() + "pipeline: {auto_approve: [3-validate]}\n")
    pl.review(load_book(y), "3-validate", book.chapters[0], "reject")
    tick(book, library)
    m = pl.read_manifest(book, "3-validate", book.chapters[0])
    assert m["review"] == {"status": "approved", "by": "auto_approve", "at": m["review"]["at"]}
    assert names(pl.ready_tasks(load_book(y))) == ["4-generate ch01"]


def test_path_b_ingests_then_extracts(library, tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    from test_extract import Extractor
    pdf = tmp_path / "rates.pdf"
    doc = pymupdf.open()
    for text in ("Chapter 1 Rates\nA quantity changes.", "Speed is a rate."):
        doc.new_page().insert_text((72, 72), text)
    doc.set_toc([[1, "Chapter 1 Rates", 1], [2, "1.1 Change", 1], [2, "1.2 Speed", 2]])
    doc.save(str(pdf))
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text() + f"  - {{id: ch02, title: Rates, graph: chapters/ch02/graph.yaml, source: {{pdf: {pdf}, chapter: 1}}}}\n")
    book = load_book(y)
    assert pl.path_of(book.chapter("ch02")) == "B" and pl.chapter_steps(book.chapter("ch02"))[0] == "1-ingest"
    assert "1-ingest ch02" in tick(book, library)
    assert (book.chapter_root(book.chapter("ch02")) / "chunks.yaml").is_file()
    approve(book, "1-ingest", "ch02")
    assert tick(book, library, Extractor()) == ["2-extract ch02"]
    m = pl.read_manifest(book, "2-extract", book.chapter("ch02"))
    assert m["status"] == "OK" and m["details"]["llm_calls"] == 2 and book.graph_path(book.chapter("ch02")).is_file()
    approve(book, "2-extract", "ch02")
    assert tick(book, library) == ["3-validate ch02"]
    assert pl.read_manifest(book, "3-validate", book.chapter("ch02"))["inputs"]["2-extract"] == m["run"]


def test_background_dispatch_and_the_cli(library):
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text().replace("models: []", "models: [mock]"))      # GraphLang's offline model
    run = lambda *a: CliRunner().invoke(main, ["-L", str(library), *a])
    assert "would start 3-validate ch01" in run("next", "acme/demo", "--dry-run").output
    r = run("next", "acme/demo")
    assert r.exit_code == 0 and "started 3-validate ch01" in r.output
    book = load_book(y)
    for _ in range(60):                                                        # a detached process writes the manifest
        m = pl.read_manifest(book, "3-validate", book.chapters[0])
        if m and m["status"] != "RUNNING":
            break
        time.sleep(0.5)
    assert m["status"] == "OK" and m["run"] != "starting"
    out = run("status", "acme/demo").output
    assert "3-validate" in out and "awaiting review" in out
    assert "nothing ready" in run("next", "acme/demo").output
    assert run("approve", "acme/demo", "3-validate", "-c", "ch01", "-m", "looks right").exit_code == 0
    assert "READY" in run("status", "acme/demo").output.splitlines()[1]
    assert run("approve", "acme/demo", "5-build", "-c", "ch01").exit_code == 2         # a book step takes no -c
    assert run("approve", "acme/demo", "4-generate", "-c", "ch01").exit_code == 1      # has not run


def test_run_pipeline_runs_every_step_and_approves_each_ok_step(book, library):
    res = pl.run_pipeline(book.root, llm=Scripted())
    assert res["ok"] and [r[0] for r in res["ran"]] == ["3-validate ch01", "4-generate ch01", "5-build", "6-publish"]
    assert res["stopped"] == "nothing more is ready"
    b = load_book(book.root)
    assert all(pl.readiness(b, t.step, t.chapter) == (False, "done") for t in pl.tasks(b))
    assert pl.read_manifest(b, "3-validate", b.chapters[0])["review"]["by"] == "auto (bookgraph pipeline)"
    again = pl.run_pipeline(book.root, llm=Scripted())                         # nothing is repeated
    assert again["ran"] == []


def test_run_pipeline_without_auto_approve_stops_at_the_first_review(book, library):
    res = pl.run_pipeline(book.root, auto_approve=False)
    assert [r[0] for r in res["ran"]] == ["3-validate ch01"]
    assert "waiting for review" in res["stopped"] and "3-validate ch01" in res["stopped"]


def test_run_pipeline_until_and_failure(book, library):
    res = pl.run_pipeline(book.root, until="3-validate")
    assert [r[0] for r in res["ran"]] == ["3-validate ch01"]
    g = book.graph_path(book.chapters[0])
    g.write_text(g.read_text() + "  loop:\n    needs: [loop]\n    defines: x\n")
    bad = pl.run_pipeline(book.root, llm=Scripted())
    assert not bad["ok"] and bad["stopped"] == "a step failed" and [r[1] for r in bad["ran"]] == ["NOT_OK"]
    with pytest.raises(Exception, match="unknown step"):
        pl.run_pipeline(book.root, until="7-nope")


def test_cli_pipeline(book, library):
    r = CliRunner().invoke(main, ["-L", str(library), "pipeline", "acme/demo", "--no-auto-approve"])
    assert r.exit_code == 0 and "run 3-validate ch01" in r.output and "waiting for review" in r.output
    r = CliRunner().invoke(main, ["-L", str(library), "approve", "acme/demo", "3-validate", "-c", "ch01"])
    r = CliRunner().invoke(main, ["-L", str(library), "pipeline", "acme/demo", "--until", "3-validate"])
    assert r.exit_code == 0 and "0 step(s) run" in r.output
