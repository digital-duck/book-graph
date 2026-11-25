"""Read-only lookup in a concept-book app's spl3 content cache, so sections the SPL apps already generated are reused and not
paid for again (plan: "never regenerate existing content during migration").

The store is `<repo>/spl/.spl/`, holding `content_cache.db` (the text) and `content_meta.db` (staleness). spl3 keys an entry as
`spl3:content:` + sha256 of `{"concept", "params", "rubric": "v1", "deps": {}}` (spl3/cache/keys.py, content_key), where
`params` is what build_concept_book.spl passed: `{language, style, model: <the model folder>, ctx: sha256(context)[:16]}`.
Entries from before the SPL added `ctx` (and concept-book's older `{domain, language, llm, lvl}` shape) are not looked up:
their text may belong to an earlier definition of the node.

The files are opened read-only and never written. Values are pickles of `str` (through dd_cache); they are read with an
unpickler that refuses every global, so only plain strings and bytes can come out of them.
"""
from __future__ import annotations

import hashlib
import io
import json
import pickle
import sqlite3
import time
from pathlib import Path
from typing import Any


def legacy_params(language: str, style: str, model_folder: str, context: str) -> dict[str, Any]:
    """The params build_concept_book.spl used for a section (and, with the target's context, for its payoff)."""
    return {"language": language, "style": style, "model": model_folder,
            "ctx": hashlib.sha256((context or "").encode("utf-8")).hexdigest()[:16]}


def legacy_key(concept: str, params: dict[str, Any]) -> str:
    payload = json.dumps({"concept": concept, "params": params, "rubric": "v1", "deps": {}}, sort_keys=True)
    return "spl3:content:" + hashlib.sha256(payload.encode()).hexdigest()


class _StringsOnly(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:            # no globals: str/bytes need none
        raise pickle.UnpicklingError(f"refused global {module}.{name}")


def _decode(raw: Any) -> str | None:
    value = raw
    for _ in range(3):                                               # dd_cache may wrap the pickled text once more
        if isinstance(value, str):
            return value
        if not isinstance(value, (bytes, bytearray)):
            return None
        try:
            value = _StringsOnly(io.BytesIO(bytes(value))).load()
        except Exception:
            return None
    return value if isinstance(value, str) else None


def _ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def legacy_get(spl_dir: str | Path, concept: str, params: dict[str, Any]) -> str | None:
    """The cached text for (concept, params) in the spl3 store at `spl_dir`, or None (a miss, a stale entry, no store)."""
    if not spl_dir:
        return None
    d = Path(spl_dir)
    store, meta = d / "content_cache.db", d / "content_meta.db"
    if not store.is_file() or not meta.is_file():
        return None
    key = legacy_key(concept, params)
    try:
        with _ro(meta) as m:
            row = m.execute("SELECT stale FROM content_meta WHERE key = ?", (key,)).fetchone()
        if row is None or row[0]:
            return None                                              # spl3 itself treats both as a miss
        with _ro(store) as s:
            hit = s.execute("SELECT value, expires_at FROM cache WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    if hit is None or (hit[1] is not None and hit[1] < time.time()):
        return None
    return _decode(hit[0])
