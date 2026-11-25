"""Resumable book runs (bookgraph.runs): the ledger, resume, budget, stop-after-failures, a resumable --skip-cache, retries."""
from __future__ import annotations

import json
import os
import time

import pytest
from click.testing import CliRunner

from bookgraph.book import load_book
from bookgraph.builder import generate_book
from bookgraph.cli import main
from bookgraph.exceptions import BookError
from bookgraph.runs import list_runs, load

from conftest import Scripted

FULL = 6                                         # 5 sections + the payoff, per variant
# A failed attempt counts as a model call (GraphLang's meter: a failed request may still be billed), so a model that
# answers `after` prompts and then fails has spent `after + 1` calls.


class Dies(Scripted):
    """Answers `after` prompts, then fails as `error` does (a ValueError is permanent; a RuntimeError is transient)."""

    def __init__(self, after: int, error: type[Exception] = ValueError):
        super().__init__()
        self.after, self.error = after, error

    def generate(self, prompt, model=None, **opts):
        if len(self.prompts) >= self.after:
            raise self.error("the model went away")
        return super().generate(prompt, model, **opts)


def three_languages(library):
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text().replace("languages: [en]", "languages: [en, zh, fr]"))
    return load_book(y)


def test_a_failed_run_resumes_where_it_stopped(library, tmp_path):
    book = load_book(library / "acme" / "demo")
    first = generate_book(book, library, llm=Dies(3), log_dir=tmp_path / "log")
    assert first.run_status == "incomplete" and not first.ok
    run = load(book, "last")
    assert run.id == first.run_id and run.variants[0].status == "failed" and run.variants[0].llm_calls == 3 + 1
    again = generate_book(book, library, llm=Scripted(), resume="last", log_dir=tmp_path / "log")
    assert again.run_status == "done" and again.ok and again.run_id == first.run_id
    assert again.chapters[0].llm_calls == FULL - 3                        # the 3 sections already written came from the cache
    assert load(book, first.run_id).variants[0].status == "done" and len(load(book, "last").resumed) == 1
    with pytest.raises(BookError, match="already done"):
        generate_book(book, library, llm=Scripted(), resume="last", log_dir=tmp_path / "log")


def test_done_variants_are_skipped_and_failures_in_a_row_stop_the_run(library, tmp_path):
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text().replace("languages: [en]", "languages: [en, zh, fr, de]"))
    book = load_book(y)
    res = generate_book(book, library, llm=Dies(FULL), log_dir=tmp_path / "log")      # en is written, then the model dies
    assert [v.status for v in res.run.variants] == ["done", "failed", "failed", "pending"]
    assert res.run_status == "incomplete" and "stopped after 2 failed variants in a row" in res.run_reason
    done = generate_book(book, library, llm=Scripted(), resume=res.run_id, log_dir=tmp_path / "log")
    assert done.run_status == "done" and [c.language for c in done.chapters] == ["zh", "fr", "de"]     # en is not run again
    assert all(v.status == "done" for v in load(book, res.run_id).variants)


def test_budget_caps_the_whole_run_and_can_be_raised(library, tmp_path):
    book = three_languages(library)
    res = generate_book(book, library, llm=Scripted(), budget=FULL + 2, log_dir=tmp_path / "log")
    assert res.run_status == "incomplete" and "budget of 8 model calls is spent" in res.run_reason
    assert res.run.spent <= FULL + 2 and res.run.variants[0].status == "done"
    still = generate_book(book, library, llm=Scripted(), resume="last", log_dir=tmp_path / "log")
    assert still.run_status == "incomplete" and "budget" in still.run_reason          # same budget: nothing more to spend
    done = generate_book(book, library, llm=Scripted(), resume="last", budget=100, log_dir=tmp_path / "log")
    assert done.run_status == "done" and done.run.spent <= 3 * FULL


def test_skip_cache_survives_a_resume(library, tmp_path):
    book = load_book(library / "acme" / "demo")
    assert generate_book(book, library, llm=Scripted(), log_dir=tmp_path / "log").ok          # an older cache, full
    cache = library / "acme/demo/chapters/ch01/.bg_cache"
    old = time.time() - 600
    for f in cache.glob("*.json"):
        os.utime(f, (old, old))
    res = generate_book(book, library, llm=Dies(2), skip_cache=True, log_dir=tmp_path / "log")
    assert res.run_status == "incomplete" and res.run.variants[0].llm_calls == 2 + 1          # the old entries were ignored
    again = generate_book(book, library, llm=Scripted(), resume="last", log_dir=tmp_path / "log")
    assert again.ok and again.chapters[0].llm_calls == FULL - 2      # this run's 2 sections reused, the 4 older ones redone


def test_a_transient_failure_is_retried_by_the_recipe(library, tmp_path, monkeypatch):
    import graphlang.grafr.lifecycle as lifecycle
    monkeypatch.setattr(lifecycle.time, "sleep", lambda s: None)                              # the recipe waits 15 s
    flaky = Dies(2, RuntimeError)
    real = flaky.generate
    calls = {"n": 0}

    def once(prompt, model=None, **opts):
        calls["n"] += 1
        if calls["n"] == 3:                                                                   # one transient error, then fine
            raise RuntimeError("429 rate limited")
        return Scripted.generate(flaky, prompt, model, **opts)

    flaky.generate = once
    res = generate_book(load_book(library / "acme" / "demo"), library, llm=flaky, log_dir=tmp_path / "log")
    assert res.ok and res.run_status == "done" and calls["n"] == FULL + 1


def test_cli_runs_and_resume(library):
    run = lambda *a: CliRunner().invoke(main, ["-L", str(library), *a])
    r = run("generate", "acme/demo", "--llm", "mock")                         # GraphLang's offline model
    assert r.exit_code == 0 and "ok   ch01 core.en mock" in r.output and ": done (1 done;" in r.output
    rid = load(load_book(library / "acme" / "demo"), "last").id
    listing = run("runs", "acme/demo").output.split()
    assert listing[0] == rid and listing[3:6] == ["done", "1/1", "done"]
    assert "already done" in run("generate", "acme/demo", "--resume", "last").output
    r = run("generate", "acme/demo", "--resume", "last", "--lang", "zh")
    assert r.exit_code == 2 and "--resume continues a run" in r.output
    r = run("generate", "acme/demo", "--resume", "nope")
    assert r.exit_code == 1 and "no run 'nope'" in r.output
