"""generate_chapter() / generate_book(): inputs, call cap and a result the CLI prints, over the generate_chapter recipe."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .book import Book, Chapter, model_dir_name
from .catalog import register_book
from .exceptions import BookError
from .graph import load_graph
from .levels import resolve_style
from .paths import variant_dir
from .run import RecipeError, run_recipe
from .runs import Run, Variant, load, new_run, save
from .steps import write_roots_from_env


@dataclass
class ChapterResult:
    chapter: str
    level: str
    language: str
    model: str
    ok: bool
    book: str = ""
    manifest: str = ""
    llm_calls: int = 0
    log_path: str = ""
    error: str = ""


@dataclass
class BookResult:
    chapters: list[ChapterResult] = field(default_factory=list)      # the variants run this time
    run_id: str = ""
    run_status: str = ""                                              # done | incomplete (see run_reason)
    run_reason: str = ""
    run: Optional[Run] = None
    catalog_error: str = ""

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.chapters) and not self.catalog_error and self.run_status in ("", "done")


def generate_chapter(book: Book, chapter: Chapter, *, level: str, language: str, model: str = "", target: str = "",
                     skip_cache: bool = False, primitive_budget: int = 2, llm: Any = None, max_llm_calls: Optional[int] = 200,
                     max_steps: int = 5000, log_dir: Optional[Path] = None, checkpoint: Optional[Path] = None, resume: Optional[Path] = None,
                     on_node: Optional[Callable[[dict], None]] = None, fresh_after: float = 0.0) -> ChapterResult:
    """Generate one chapter at one level, language and model. `model` is an `ADAPTER[:MODEL]` string (or empty with `llm` an object).
    `fresh_after` (a time.time()) counts only cache entries written after it, so a resumed `--skip-cache` run reuses what it
    already regenerated and nothing older; `skip_cache` ignores the cache altogether."""
    folder = model_dir_name(model) if isinstance(model, str) else ""
    res = ChapterResult(chapter.id, level, language, folder, ok=False)
    try:
        graph = book.graph_path(chapter)
        load_graph(graph)                                # a missing or unreadable graph is this chapter's failure, not the book's
        root = book.chapter_root(chapter).resolve()
        out = variant_dir(root, level, language, folder).resolve()
        style = resolve_style(level, book.tags)
    except (BookError, ValueError) as e:
        res.error = str(e)
        return res
    inputs = {"chapter_graph": str(graph), "target": target or chapter.capstone, "style": style,
              "level": level, "language": language, "model": model if isinstance(model, str) else "", "chapter_title": chapter.title,
              "output_dir": str(out), "cache_dir": str(root / ".bg_cache"), "skip_cache": skip_cache,
              "primitive_budget": primitive_budget, "subject": f"{book.book} {' '.join(book.tags)}".strip(),
              "write_roots": write_roots_from_env(), "legacy_cache": book.legacy_cache, "fresh_after": float(fresh_after)}
    kw: dict[str, Any] = {}
    if log_dir is not None:
        kw["log_dir"] = log_dir
    try:
        run = run_recipe("generate_chapter", inputs, llm=llm if llm is not None else (model or "claude_cli"), max_llm_calls=max_llm_calls,
                         max_steps=max_steps, checkpoint=checkpoint, resume=resume, on_node=on_node, **kw)
    except RecipeError as e:
        res.error = str(e)
        res.llm_calls = int(((e.summary or {}).get("llm") or {}).get("calls") or 0)       # what it spent before it stopped
        res.log_path = str(e.log_path or "")
        return res
    res.llm_calls, res.log_path = run.llm_calls, str(run.log_path or "")
    if run.exit:
        res.error = f"exit {run.exit}: {run.outputs.get('book', '')}"
        return res
    res.ok, res.book, res.manifest = True, run.outputs.get("book", ""), run.outputs.get("manifest", "")
    return res


def generate_book(book: Book, library: Path, *, chapters: Optional[list[str]] = None, levels: Optional[list[str]] = None,
                  languages: Optional[list[str]] = None, models: Optional[list[str]] = None, llm: Any = None,
                  resume: Optional[str] = None, budget: Optional[int] = None, stop_after: int = 2, skip_cache: bool = False,
                  target: str = "", max_llm_calls: Optional[int] = 200, primitive_budget: int = 2, log_dir: Optional[Path] = None,
                  on_variant: Optional[Callable[[Variant, ChapterResult], None]] = None) -> BookResult:
    """Every chapter x level x language x model the book lists (or the subset asked for), as a resumable run (bookgraph.runs);
    the catalog is refreshed at the end. With `resume` (a run id or `last`), only what that run has not finished runs, with
    its own options (the selection and option arguments are then ignored)."""
    if resume:
        run = load(book, resume)
        if run.status == "done":
            raise BookError(f"run {run.id} is already done ({len(run.variants)} variant(s)); nothing to resume")
        run.resumed.append(time.time())
        run.status, run.reason = "running", ""
        if budget is not None:                                                   # a spent budget can be raised to go on
            run.options["budget"] = budget
    else:
        todo = [book.chapter(c) for c in chapters] if chapters else book.chapters
        model_list = (models if models is not None else book.models) or [""]
        if any(not m for m in model_list) and llm is None:
            raise BookError("no model: list one under 'models:' in book.yaml or pass --llm ADAPTER:MODEL")
        variants = [Variant(ch.id, lv, lang, m) for ch in todo for lv in levels or book.levels
                    for lang in languages or book.languages for m in model_list]
        run = new_run(book, {"skip_cache": skip_cache, "target": target, "max_llm_calls": max_llm_calls,
                             "primitive_budget": primitive_budget, "budget": budget}, variants)
    opts = run.options
    fresh_after = run.started - 2.0 if opts.get("skip_cache") else 0.0        # a margin for coarse file-system clocks
    out = BookResult(run_id=run.id)
    failures = 0
    try:
        for v in run.variants:
            if v.status == "done":
                continue
            cap = opts.get("max_llm_calls")
            if opts.get("budget"):
                left = int(opts["budget"]) - run.spent
                if left <= 0:
                    run.status, run.reason = "incomplete", f"the run's budget of {opts['budget']} model calls is spent"
                    break
                cap = min(cap, left) if cap else left
            v.status, v.error = "running", ""
            save(book, run)
            try:
                res = generate_chapter(book, book.chapter(v.chapter), level=v.level, language=v.language, model=v.model, llm=llm,
                                       target=opts.get("target") or "", primitive_budget=int(opts.get("primitive_budget") or 2),
                                       max_llm_calls=cap, log_dir=log_dir, fresh_after=fresh_after)
            except KeyboardInterrupt:
                v.status = "interrupted"
                run.status, run.reason = "incomplete", "interrupted"
                raise
            v.llm_calls += res.llm_calls
            v.status, v.error, v.book, v.finished = ("done" if res.ok else "failed"), res.error, res.book, time.time()
            out.chapters.append(res)
            if on_variant:
                on_variant(v, res)
            failures = 0 if res.ok else failures + 1
            save(book, run)
            if stop_after and failures >= stop_after:
                run.status = "incomplete"
                run.reason = f"stopped after {failures} failed variants in a row (last: {res.error[:200]})"
                break
        else:
            run.status = "done" if all(v.status == "done" for v in run.variants) else "incomplete"
            if run.status != "done":
                run.reason = "some variants failed"
    finally:
        if run.status == "running":
            run.status, run.reason = "incomplete", run.reason or "stopped"
        save(book, run)
        out.run_status, out.run_reason, out.run = run.status, run.reason, run
        try:
            register_book(library, book)
        except BookError as e:
            out.catalog_error = str(e)
    return out
