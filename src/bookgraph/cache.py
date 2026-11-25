"""The section cache: one JSON file per (node, params) under <chapter>/.bg_cache/. A hit costs no model call.

The key is the node id plus the params json (graph.section_params). An SPL app's spl3 cache has its own keys, which can be
recomputed: bookgraph.legacy reads it (read-only) when book.yaml names it as `legacy_cache`, and a section found there is
copied into this cache.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def _file(cache_dir: str | Path, key: str, params_json: str) -> Path:
    digest = hashlib.sha256(f"{key}\n{params_json}".encode("utf-8")).hexdigest()[:32]
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in key)[:60]
    return Path(cache_dir) / f"{safe}-{digest}.json"


def cache_get(cache_dir: str | Path, key: str, params_json: str, fresh_after: float = 0.0) -> str | None:
    """The cached text, or None. With `fresh_after` (a time.time()), an entry written before then is a miss."""
    f = _file(cache_dir, key, params_json)
    try:
        if fresh_after and f.stat().st_mtime < fresh_after:
            return None
        return json.loads(f.read_text(encoding="utf-8"))["text"]
    except (OSError, ValueError, KeyError):
        return None


def cache_put(cache_dir: str | Path, key: str, params_json: str, text: str) -> str:
    f = _file(cache_dir, key, params_json)
    f.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=f.parent, suffix=".tmp")           # a name of its own: concurrent runs of one variant cannot race
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"key": key, "params": json.loads(params_json), "text": text}, ensure_ascii=False))
    os.replace(tmp, f)
    return f.name
