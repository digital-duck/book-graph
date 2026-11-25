"""Book runs that can be resumed: a ledger per `bookgraph generate` run, at <book>/.bg_runs/<run id>.json.

A book run is every chapter x level x language x model asked for; each is one `generate_chapter` (the recipe). The ledger
lists them with their status (pending, running, done, failed, interrupted), model calls and error, and is rewritten after
each one. `bookgraph generate <book> --resume last` runs again what is not done, with the same options.

Resuming is cheap at two levels:

- across variants: the ledger skips what is done;
- inside a variant: every section is cached as soon as it is written (steps.store), so a variant that stopped half-way
  pays only for the sections it had not reached. GraphLang's own checkpoints are not used: a LOOP is one checkpoint step,
  so a failure inside the section loop would re-run the whole loop anyway, and the cache already skips its finished rounds.

With `--skip-cache`, the run ignores cache entries written before it started (`fresh_after`), so a resumed run reuses what
it regenerated itself and nothing older. A transient model failure is retried by the recipe (twice, 15 s apart); a run
stops after `stop_after` variants fail in a row (the model is likely down or rate-limited) and leaves the rest pending.
`budget` caps the model calls of the whole run, across resumes.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .book import Book
from .exceptions import BookError

RUNS = ".bg_runs"


@dataclass
class Variant:
    chapter: str
    level: str
    language: str
    model: str
    status: str = "pending"          # pending | running | done | failed | interrupted
    llm_calls: int = 0               # across every attempt of this run
    error: str = ""
    book: str = ""
    finished: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.chapter} {self.level}.{self.language} {self.model or '-'}"


@dataclass
class Run:
    id: str
    started: float
    options: dict[str, Any]
    variants: list[Variant]
    status: str = "running"          # running | done | incomplete
    reason: str = ""
    resumed: list[float] = field(default_factory=list)

    @property
    def spent(self) -> int:
        return sum(v.llm_calls for v in self.variants)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.variants:
            out[v.status] = out.get(v.status, 0) + 1
        return out


def runs_dir(book: Book) -> Path:
    return book.root / RUNS


def save(book: Book, run: Run) -> Path:
    d = runs_dir(book)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{run.id}.json"
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(asdict(run), fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def _read(path: Path) -> Run:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Run(id=raw["id"], started=raw["started"], options=raw["options"], variants=[Variant(**v) for v in raw["variants"]],
               status=raw.get("status", "running"), reason=raw.get("reason", ""), resumed=raw.get("resumed", []))


def list_runs(book: Book) -> list[Run]:
    d = runs_dir(book)
    out = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(_read(p))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(out, key=lambda r: r.started)


def load(book: Book, run_id: str) -> Run:
    """A run by id, or `last` (the most recent one)."""
    runs = list_runs(book)
    if not runs:
        raise BookError(f"no runs recorded for {book.press}/{book.book} (in {runs_dir(book)})")
    if run_id == "last":
        return runs[-1]
    for r in runs:
        if r.id == run_id:
            return r
    raise BookError(f"no run {run_id!r} for {book.press}/{book.book} (runs: {', '.join(r.id for r in runs[-5:])})")


def new_run(book: Book, options: dict[str, Any], variants: list[Variant]) -> Run:
    rid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    run = Run(id=rid, started=time.time(), options=options, variants=variants)
    save(book, run)
    return run
