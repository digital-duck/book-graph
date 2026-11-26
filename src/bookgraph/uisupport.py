"""What `bookgraph ui` (bookgraph.ui, a Streamlit script) needs that is not drawing: the books of a library, the material a
reviewer looks at for each pipeline step, a log tail, and the preview server. Plain functions, so they are tested without Streamlit."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from .book import Book, Chapter
from .pipeline import log_path, read_manifest


def list_books(library: Path) -> list[str]:
    """`press/book` for every book.yaml two levels under the library, sorted."""
    library = Path(library)
    if not library.is_dir():
        return []
    return sorted(f"{f.parent.parent.name}/{f.parent.name}" for f in library.glob("*/*/book.yaml"))


def tail(path: Path, lines: int = 40) -> str:
    try:
        return "\n".join(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def step_log(book: Book, step: str, chapter: Optional[Chapter]) -> str:
    return tail(log_path(book, step, chapter))


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def chunk_rows(book: Book, chapter: Chapter) -> list[dict[str, Any]]:
    """1-ingest's output for review: one row per section."""
    data = _load_yaml(book.chapter_root(chapter) / "chunks.yaml") or {}
    return [{"section": c.get("section_title", ""), "pages": f"{c.get('page_start', '')}-{c.get('page_end', '')}", "characters": len(c.get("text") or "")}
            for c in data.get("chunks") or []]


def graph_rows(book: Book, chapter: Chapter) -> list[dict[str, Any]]:
    """2-extract's output for review: one row per node, with its kind, prerequisites and definition."""
    data = _load_yaml(book.graph_path(chapter)) or {}
    rows = []
    for kind in ("primitives", "concepts", "applications"):
        for nid, node in (data.get(kind) or {}).items():
            node = node if isinstance(node, dict) else {}
            deps = node.get("composed_of") or node.get("needs") or []
            rows.append({"kind": kind[:-1] if kind != "applications" else "application", "id": nid, "label": node.get("label", ""),
                         "needs": ", ".join(map(str, deps)) if isinstance(deps, list) else str(deps),
                         "defines": " ".join(str(node.get("defines") or "").split())})
    return rows


def page_files(book: Book, chapter: Chapter) -> list[Path]:
    """4-generate's output for review: the chapter's generated pages (book pages first), newest variant folders included."""
    root = book.chapter_root(chapter) / "output"
    pages = sorted(root.glob("*/*/html/*.html")) + sorted(root.glob("*/html/*.html")) if root.is_dir() else []
    return sorted(pages, key=lambda p: (not p.name.startswith("book_"), str(p)))


def built_site(book: Book) -> Optional[Path]:
    """The folder 5-build made, once its manifest says OK (a preview may come before its approval)."""
    m = read_manifest(book, "5-build")
    site = Path((m or {}).get("details", {}).get("site") or book.root / "dist")
    return site if m and m.get("status") == "OK" and (site / "index.html").exists() else None


# ── the preview server: `bookgraph serve --no-build` in a child process ─────────────────────────────────────────────

def free_port(start: int = 8200) -> int:
    for port in range(start, start + 100):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("no free port near 8200")


def alive(pid: Any) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    try:                                                                      # a zombie child is not alive
        return Path(f"/proc/{int(pid)}/stat").read_text().split(") ")[-1][0] != "Z"
    except OSError:
        return True


def start_preview(library: Path, target: str, port: int = 0, wait: float = 20.0) -> dict[str, Any]:
    """Start the preview of a built book; returns {pid, port, url_root}. Waits until it listens."""
    port = port or free_port()
    proc = subprocess.Popen([sys.executable, "-m", "bookgraph.cli", "-L", str(library), "serve", target, "--no-build", "-p", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
    end = time.time() + wait
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("the preview server exited at once: is the site built? (bgr serve TARGET --no-build shows why)")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return {"pid": proc.pid, "port": port, "root": f"http://127.0.0.1:{port}"}
        time.sleep(0.2)
    stop_preview(proc.pid)
    raise RuntimeError("the preview server did not start in time")


def stop_preview(pid: Any) -> None:
    try:
        os.killpg(int(pid), signal.SIGTERM)
    except (OSError, TypeError, ValueError):
        pass
