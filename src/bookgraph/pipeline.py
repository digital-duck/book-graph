"""The asynchronous pipeline: each step writes a manifest, and a dispatcher starts the steps whose input is ready and reviewed.

    path B (from a PDF):        1-ingest -> 2-extract -> 3-validate -> 4-generate ┐
    path A (an authored graph):                          3-validate -> 4-generate ┤ every chapter
                                                                                   └-> 5-build -> 6-publish (the book)

A chapter is on path B when book.yaml gives it `source: {pdf: FILE, chapter: N}`, else on path A (its graph.yaml, written by
an expert, a teacher or any other means, is the starting point).

Every step writes `<book>/pipeline/<chapter>/<step>.out.yaml` (`<book>/pipeline/<step>.out.yaml` for 5-build and 6-publish):

    step, chapter, run          which step, which chapter, this run's id
    status                      OK | NOT_OK | RUNNING (with pid)
    started, finished, summary, error, details
    inputs                      fingerprints of what it read: file hashes, and the run id of each step it builds on
    review                      {status: pending | approved | rejected, by, at, note}

The manifest is the proof a step completed. `bookgraph next` (dispatch) starts a step when the step before it is OK and
**approved**, and its own manifest is missing, or its inputs changed since, or it was rejected or marked for retry. Every
step waits for a human by default (`bookgraph approve`/`reject`); `pipeline: {auto_approve: [steps]}` in book.yaml lets a
step through without one. A step that failed (NOT_OK) is not run again on its own: fix its input (the fingerprint then
differs) or `bookgraph retry` it; a 4-generate that stopped resumes its run ledger (bookgraph.runs), so what it finished is
not paid for again. A rejected step runs again: 2-extract extracts again, 4-generate regenerates (skip-cache).
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from .book import Book, Chapter, load_book
from .exceptions import BookError

STEPS = ("1-ingest", "2-extract", "3-validate", "4-generate", "5-build", "6-publish")
CHAPTER_STEPS, BOOK_STEPS = STEPS[:4], STEPS[4:]
OK, NOT_OK, RUNNING = "OK", "NOT_OK", "RUNNING"


# ── where things are ────────────────────────────────────────────────────────────────────────────────────────────────

def pipeline_dir(book: Book) -> Path:
    return book.root / "pipeline"


def manifest_path(book: Book, step: str, chapter: Optional[Chapter] = None) -> Path:
    if step not in STEPS:
        raise BookError(f"unknown step {step!r}: one of {', '.join(STEPS)}")
    base = pipeline_dir(book) / chapter.id if step in CHAPTER_STEPS and chapter is not None else pipeline_dir(book)
    return base / f"{step}.out.yaml"


def log_path(book: Book, step: str, chapter: Optional[Chapter] = None) -> Path:
    return pipeline_dir(book) / "logs" / f"{chapter.id + '.' if chapter else ''}{step}.log"


def read_manifest(book: Book, step: str, chapter: Optional[Chapter] = None) -> Optional[dict[str, Any]]:
    p = manifest_path(book, step, chapter)
    try:
        m = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return m if isinstance(m, dict) else None


def write_manifest(book: Book, m: dict[str, Any], chapter: Optional[Chapter] = None) -> Path:
    p = manifest_path(book, m["step"], chapter)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        yaml.safe_dump(m, fh, sort_keys=False, allow_unicode=True, width=120)
    os.replace(tmp, p)
    return p


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ── the steps' shape: which apply, what comes before, what they read ────────────────────────────────────────────────

def path_of(chapter: Chapter) -> str:
    return "B" if chapter.source.get("pdf") else "A"


def chapter_steps(chapter: Chapter) -> tuple[str, ...]:
    return CHAPTER_STEPS if path_of(chapter) == "B" else CHAPTER_STEPS[2:]


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "missing"


def _big(path: Path) -> str:
    """A large file (a textbook PDF) by size and modification time, not by reading it."""
    try:
        st = path.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return "missing"


def _cfg(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _run_of(book: Book, step: str, chapter: Optional[Chapter] = None) -> str:
    m = read_manifest(book, step, chapter)
    return str(m.get("run")) if m else "none"


def inputs_of(book: Book, step: str, chapter: Optional[Chapter] = None) -> dict[str, str]:
    """The fingerprints of what a step reads now; a manifest whose `inputs` differ is stale."""
    if step == "1-ingest":
        return {"pdf": _big(Path(chapter.source["pdf"])), "chapter": str(chapter.source["chapter"])}
    if step == "2-extract":
        return {"chunks": _sha(book.chapter_root(chapter) / "chunks.yaml"), "1-ingest": _run_of(book, "1-ingest", chapter)}
    if step == "3-validate":
        out = {"graph": _sha(book.graph_path(chapter)), "capstone": chapter.capstone}
        if path_of(chapter) == "B":
            out["2-extract"] = _run_of(book, "2-extract", chapter)
        return out
    if step == "4-generate":
        return {"graph": _sha(book.graph_path(chapter)), "3-validate": _run_of(book, "3-validate", chapter),
                "config": _cfg([book.levels, book.languages, book.models, book.tags, chapter.capstone, book.recap_earlier_chapters])}
    if step == "5-build":
        return {"site": _cfg([book.site, book.title, book.models, book.levels]),
                **{f"4-generate:{c.id}": _run_of(book, "4-generate", c) for c in book.chapters}}
    if step == "6-publish":
        return {"publish": _cfg(book.publish), "5-build": _run_of(book, "5-build")}
    raise BookError(f"unknown step {step!r}")


def _accepted(book: Book, step: str, chapter: Optional[Chapter] = None) -> bool:
    """OK, approved, and still current: an approval stops counting once the step's own inputs change (a graph edited after
    its validation), so nothing after it runs on what was reviewed before."""
    m = read_manifest(book, step, chapter)
    return bool(m) and m.get("status") == OK and (m.get("review") or {}).get("status") == "approved" \
        and m.get("inputs") == inputs_of(book, step, chapter)


def _alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def upstream_ready(book: Book, step: str, chapter: Optional[Chapter] = None) -> tuple[bool, str]:
    """Is what this step builds on OK and approved? (True, "") or (False, what it waits for)."""
    if step in CHAPTER_STEPS:
        steps = chapter_steps(chapter)
        if step not in steps:
            return False, f"not on path {path_of(chapter)}"
        i = steps.index(step)
        if i == 0:
            return True, ""
        prev = steps[i - 1]
        return (True, "") if _accepted(book, prev, chapter) else (False, f"{prev} not approved (or stale)")
    if step == "5-build":
        waiting = [c.id for c in book.chapters if not _accepted(book, "4-generate", c)]
        return (True, "") if not waiting else (False, f"4-generate not approved (or stale) for {', '.join(waiting)}")
    return (True, "") if _accepted(book, "5-build") else (False, "5-build not approved (or stale)")


@dataclass
class Task:
    step: str
    chapter: Optional[Chapter]

    @property
    def name(self) -> str:
        return f"{self.step}{' ' + self.chapter.id if self.chapter else ''}"


def readiness(book: Book, step: str, chapter: Optional[Chapter] = None) -> tuple[bool, str]:
    """(ready, why): may `bookgraph next` start this step now?"""
    ok, why = upstream_ready(book, step, chapter)
    if not ok:
        return False, why
    m = read_manifest(book, step, chapter)
    if m is None:
        return True, "not run yet"
    if m.get("status") == RUNNING:
        if _alive(m.get("pid")):
            return False, "running"
        m.update(status=NOT_OK, error="the process ended without finishing its manifest", finished=_now())
        write_manifest(book, m, chapter)
        return False, "NOT_OK (process gone)"
    review = (m.get("review") or {}).get("status")
    if review == "rejected":
        return True, "rejected: run again"
    if m.get("retry"):
        return True, "marked for retry"
    if m.get("inputs") != inputs_of(book, step, chapter):
        return True, "inputs changed"
    if m.get("status") == NOT_OK:
        return False, "NOT_OK: fix its input, or bookgraph retry"
    return False, "awaiting review" if review == "pending" else "done"


def tasks(book: Book) -> list[Task]:
    out = [Task(s, c) for c in book.chapters for s in chapter_steps(c)]
    return out + [Task(s, None) for s in BOOK_STEPS]


def ready_tasks(book: Book) -> list[Task]:
    return [t for t in tasks(book) if readiness(book, t.step, t.chapter)[0]]


# ── running one step ────────────────────────────────────────────────────────────────────────────────────────────────

def _ingest(book: Book, ch: Chapter, prev: Optional[dict], llm: Any) -> tuple[str, str, dict]:
    from .ingest import ingest_chapter
    content = ingest_chapter(ch.source["pdf"], int(ch.source["chapter"]), attribution=ch.source.get("attribution"))
    dest = book.chapter_root(ch) / "chunks.yaml"
    content.save_yaml(dest)
    chars = sum(len(c.text) for c in content.chunks)
    return OK, f"{len(content.chunks)} section(s), {chars} characters from {Path(ch.source['pdf']).name} chapter " \
               f"{ch.source['chapter']} ({content.chapter_title})", {"chunks": str(dest), "chapter_title": content.chapter_title}


def _extract(book: Book, ch: Chapter, prev: Optional[dict], llm: Any) -> tuple[str, str, dict]:
    from .builder import extract_chapter
    graph = book.graph_path(ch)
    if prev is None and graph.exists():                       # a graph already there (made by hand): adopt it, for review
        return OK, f"kept the existing {graph.name} (not extracted): review it", {"graph": str(graph), "adopted": True}
    res = extract_chapter(book, ch, llm=llm, force=True)
    details = {"graph": res.graph, "llm_calls": res.llm_calls}
    return (OK, res.summary, details) if res.ok else (NOT_OK, res.error, details)


def _validate(book: Book, ch: Chapter, prev: Optional[dict], llm: Any) -> tuple[str, str, dict]:
    from .graph import load_graph
    g = load_graph(book.graph_path(ch))
    problems = g.validate()
    warnings: list[str] = []
    if not problems:
        more, warnings = g.check_target(ch.capstone)
        problems, warnings = more, warnings + g.warnings()
    details = {"problems": problems, "warnings": warnings, "nodes": g.G.number_of_nodes()}
    if problems:
        return NOT_OK, "; ".join(problems), details
    return OK, f"{g.G.number_of_nodes()} nodes, valid" + (f"; {len(warnings)} warning(s)" if warnings else ""), details


def _generate(book: Book, ch: Chapter, prev: Optional[dict], llm: Any) -> tuple[str, str, dict]:
    from .builder import generate_book
    from .runs import load
    resume = None
    rejected = prev is not None and (prev.get("review") or {}).get("status") == "rejected"
    if prev and prev.get("status") == NOT_OK and prev.get("inputs") == inputs_of(book, "4-generate", ch):
        rid = (prev.get("details") or {}).get("run_id")
        try:
            resume = rid if rid and load(book, rid).status != "done" else None
        except BookError:
            resume = None
    res = generate_book(book, book.root.parent.parent, chapters=None if resume else [ch.id], llm=llm, resume=resume,
                        skip_cache=rejected and not resume)
    run = res.run
    details = {"run_id": res.run_id, "variants": run.counts() if run else {}, "llm_calls": run.spent if run else 0,
               "pages": [c.book for c in res.chapters if c.ok], "resumed": bool(resume)}
    counts = ", ".join(f"{n} {s}" for s, n in sorted(details["variants"].items()))
    summary = f"run {res.run_id}: {res.run_status} ({counts}; {details['llm_calls']} model calls)"
    if res.run_status == "done" and not res.catalog_error:
        return OK, summary, details
    return NOT_OK, summary + (f": {res.run_reason}" if res.run_reason else "") + (f"; {res.catalog_error}" if res.catalog_error else ""), details


def _build(book: Book, ch: None, prev: Optional[dict], llm: Any) -> tuple[str, str, dict]:
    from .build import build_site, site_base
    out = build_site(book, book.root / "dist", log=lambda m: None)
    return OK, f"built {out} (served at {site_base(book)}): preview it with `bookgraph serve` or `npx vite preview`", \
        {"site": str(out), "base": site_base(book)}


def _publish(book: Book, ch: None, prev: Optional[dict], llm: Any) -> tuple[str, str, dict]:
    from .publish import publish_site
    res = publish_site(book, book.root / "dist")                  # the approval of 5-build is the confirmation
    return OK, res["summary"], {"commit": res["commit"]}


RUNNERS: dict[str, Callable[..., tuple[str, str, dict]]] = {
    "1-ingest": _ingest, "2-extract": _extract, "3-validate": _validate, "4-generate": _generate, "5-build": _build,
    "6-publish": _publish}


def run_step(book: Book, step: str, chapter: Optional[Chapter] = None, *, llm: Any = None) -> dict[str, Any]:
    """Run one step now (whatever its readiness) and write its manifest. Returns the manifest."""
    if (step in CHAPTER_STEPS) != (chapter is not None):
        raise BookError(f"{step} is a {'chapter' if step in CHAPTER_STEPS else 'book'} step")
    prev = read_manifest(book, step, chapter)
    if prev and prev.get("status") == RUNNING:                    # dispatch's placeholder: the run before is inside it
        prev = prev.get("previous")
    inputs = inputs_of(book, step, chapter)
    m: dict[str, Any] = {"step": step, "book": f"{book.press}/{book.book}", "chapter": chapter.id if chapter else None,
                         "run": time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2), "status": RUNNING,
                         "pid": os.getpid(), "started": _now(), "inputs": inputs}
    write_manifest(book, m, chapter)
    try:
        status, summary, details = RUNNERS[step](book, chapter, prev, llm)
        error = "" if status == OK else summary
    except Exception as e:                                         # a failure is a NOT_OK manifest, never a missing one
        status, summary, details, error = NOT_OK, f"{type(e).__name__}: {e}", {}, f"{type(e).__name__}: {e}"
    auto = step in (book.pipeline.get("auto_approve") or [])
    m.update(status=status, finished=_now(), summary=summary, error=error, details=details,
             review={"status": "approved" if status == OK and auto else "pending",
                     **({"by": "auto_approve", "at": _now()} if status == OK and auto else {})})
    m.pop("pid", None)
    write_manifest(book, m, chapter)
    return m


# ── review ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def review(book: Book, step: str, chapter: Optional[Chapter], decision: str, *, by: str = "", note: str = "") -> dict[str, Any]:
    """approve | reject a step's last run, or mark a NOT_OK one for retry."""
    m = read_manifest(book, step, chapter)
    name = f"{step}{' ' + chapter.id if chapter else ''}"
    if m is None:
        raise BookError(f"{name} has not run yet")
    if m.get("status") == RUNNING:
        raise BookError(f"{name} is still running")
    if decision == "approve" and m.get("status") != OK:
        raise BookError(f"{name} is {m.get('status')}: only an OK step can be approved (fix it, or bookgraph retry)")
    if decision == "retry":
        if m.get("status") != NOT_OK:
            raise BookError(f"{name} is {m.get('status')}: retry is for a NOT_OK step (reject an OK one to redo it)")
        m["retry"] = True
    else:
        m["review"] = {"status": "approved" if decision == "approve" else "rejected", "by": by or getpass.getuser(),
                       "at": _now(), **({"note": note} if note else {})}
    write_manifest(book, m, chapter)
    return m


# ── dispatch ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _running(book: Book) -> int:
    return sum(1 for t in tasks(book) if (read_manifest(book, t.step, t.chapter) or {}).get("status") == RUNNING
               and _alive((read_manifest(book, t.step, t.chapter) or {}).get("pid")))


def dispatch(book: Book, library: Path, *, parallel: int = 2, foreground: bool = False, dry_run: bool = False,
             llm: Any = None) -> list[Task]:
    """Start every ready step (up to `parallel` running at once) and return them. In the background unless `foreground`.

    One pass: what a started step makes ready is started by the next `dispatch` (bookgraph next, or next --watch)."""
    started: list[Task] = []
    slots = max(0, parallel - _running(book))
    for t in ready_tasks(book):
        if not foreground and len(started) >= slots:
            break
        started.append(t)
        if dry_run:
            continue
        if foreground:
            run_step(book, t.step, t.chapter, llm=llm)
            continue
        log = log_path(book, t.step, t.chapter)
        log.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "bookgraph.cli", "-L", str(library), "pipeline-step", str(book.root), t.step]
        if t.chapter:
            cmd += ["-c", t.chapter.id]
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"\n== {_now()} {t.name}\n")
            fh.flush()
            proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        prev = read_manifest(book, t.step, t.chapter)
        write_manifest(book, {"step": t.step, "book": f"{book.press}/{book.book}", "chapter": t.chapter.id if t.chapter else None,
                              "run": "starting", "status": RUNNING, "pid": proc.pid, "started": _now(),
                              "inputs": inputs_of(book, t.step, t.chapter), "previous": prev}, t.chapter)
    return started


def status_rows(book: Book) -> list[dict[str, Any]]:
    """For `bookgraph status`: every step of every chapter, then the book's steps."""
    rows = []
    for t in tasks(book):
        m = read_manifest(book, t.step, t.chapter)
        ready, why = readiness(book, t.step, t.chapter)
        rows.append({"task": t, "manifest": m, "ready": ready, "why": why})
    return rows
