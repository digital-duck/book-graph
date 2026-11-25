"""The section cache: one JSON file per (node, params) under <chapter>/.bg_cache/. A hit costs no model call.

The key is the node id plus the params json (language, style, model, a hash of the node's `defines` — graph.section_params).
Existing spl3 caches are not read: their keys were not recorded in a form that can be recomputed here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _file(cache_dir: str | Path, key: str, params_json: str) -> Path:
    digest = hashlib.sha256(f"{key}\n{params_json}".encode("utf-8")).hexdigest()[:32]
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in key)[:60]
    return Path(cache_dir) / f"{safe}-{digest}.json"


def cache_get(cache_dir: str | Path, key: str, params_json: str) -> str | None:
    f = _file(cache_dir, key, params_json)
    try:
        return json.loads(f.read_text(encoding="utf-8"))["text"]
    except (OSError, ValueError, KeyError):
        return None


def cache_put(cache_dir: str | Path, key: str, params_json: str, text: str) -> str:
    f = _file(cache_dir, key, params_json)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"key": key, "params": json.loads(params_json), "text": text}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)
    return f.name
