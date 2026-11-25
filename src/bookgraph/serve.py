"""`bookgraph serve`: one book's site with the local API behind Generate, Export PDF and Settings, on 127.0.0.1.

Replaces the apps' FastAPI backend (concept-book-base api/) with the same endpoints, so the web app talks to it unchanged:

    GET  /api/generate?domain&target&level&language&model[&skip_cache=true]   Server-Sent Events: started, log, done, gen_error
    GET  /api/pdf?domain&target&level&language&model                         {"file": "output/<level>.<lang>/<model>/pdf/..."}
    GET  /api/settings, PUT /api/settings                                    llm, spl_while_max_iter, spl_max_llm_calls, *_api_key_set
    GET  /api/settings/ollama-models                                         [{value, label}]
    GET  /api/domains, /api/domains/<id>/status

Generation runs `generate_chapter` (the GraphLang recipe) in this process. The site is the built web app (`bookgraph build`)
with `<base>domains/` served **live from the library**: catalog.json is recomputed per request (export.chapter_entry), and a
chapter's input/output come from its library folder, so a page generated a moment ago is there on the next request.

Kept from the old API, not relaxed (plan §5): it binds 127.0.0.1 only; settings, API keys included, live in memory only
and are never sent back (only `*_api_key_set` flags). Added: the Host header must be this server (no DNS rebinding), and
/api requests must be same-origin (Origin / Sec-Fetch-Site), so another website open in the browser cannot start a paid
generation with a crafted <img> or EventSource. Every path is checked to stay inside its root.
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .book import LEVELS, Book, Chapter, model_dir_name
from .exceptions import BookError
from .paths import lang_suffix, safe_segment, variant_dir

HOST = "127.0.0.1"
# Settings-page key fields -> the variable dd-llm's adapter reads it from. The Settings page calls Gemini "google".
KEY_ENV = {"anthropic_api_key": "ANTHROPIC_API_KEY", "openai_api_key": "OPENAI_API_KEY", "gemini_api_key": "GEMINI_API_KEY",
           "openrouter_api_key": "OPENROUTER_API_KEY"}
ADAPTERS = ("claude_cli", "ollama", "anthropic", "openai", "google", "gemini", "openrouter")


def _dd_llm(llm: str) -> str:
    """The Settings page's adapter names -> dd-llm's (`google:x` -> `gemini:x`)."""
    return "gemini:" + llm.split(":", 1)[1] if llm.startswith("google:") else llm


class BookAPI:
    """The state and the operations behind the endpoints; the HTTP handler only parses and replies."""

    def __init__(self, book: Book, site_dir: Path, *, log_dir: Path | None = None, llm: Any = None):
        self.book, self.site_dir, self.log_dir = book, Path(site_dir), log_dir
        self.llm_object = llm                       # tests: an LLM object used in place of the adapter (the model string still names the folder)
        self.llm = book.models[0] if book.models else "claude_cli"
        self.while_max_iter, self.max_llm_calls = 50, 200
        self.keys: dict[str, str] = {}
        self._env0 = {v: os.environ.get(v) for v in KEY_ENV.values()}
        self._busy: set[tuple[str, str, str, str]] = set()
        self._lock = threading.Lock()

    # ── content ─────────────────────────────────────────────────────────────────────────────────────────────────────
    def chapter(self, gid: str) -> Chapter:
        for ch in self.book.chapters:
            if self.book.global_id(ch) == gid:
                return ch
        raise BookError(f"no chapter {gid!r} in this book")

    def catalog(self) -> list[dict[str, Any]]:
        from .export import chapter_entry
        return [chapter_entry(self.book, ch) for ch in self.book.chapters]

    def domains_file(self, rel: str) -> Path | None:
        """A file under <base>domains/, from the library: catalog.json is handled by the caller."""
        parts = [p for p in rel.split("/") if p]
        if not parts or any(p in (".", "..") or "\\" in p for p in parts):
            return None
        if len(parts) == 1:                                                   # book-level files (super-graph, ...)
            return _inside(self.book.root / "domains", parts[0])
        try:
            ch = self.chapter(parts[0])
        except BookError:
            return None
        if parts[1:] == ["input", "graph.yaml"]:
            return self.book.graph_path(ch)
        if parts[1] == "input" and len(parts) == 3:
            return _inside(self.book.chapter_root(ch), parts[2])
        if parts[1] == "output":
            return _inside(self.book.chapter_root(ch) / "output", "/".join(parts[2:]))
        return None

    # ── settings ────────────────────────────────────────────────────────────────────────────────────────────────────
    def key_set(self, field: str) -> bool:
        return bool(self.keys.get(field) or os.environ.get(KEY_ENV[field]))

    def settings(self) -> dict[str, Any]:
        return {"llm": self.llm, "spl_while_max_iter": self.while_max_iter, "spl_max_llm_calls": self.max_llm_calls,
                **{f"{f}_set": self.key_set(f) for f in KEY_ENV}}

    def update_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("llm") is not None:
            llm = str(body["llm"])
            if llm.split(":", 1)[0] not in ADAPTERS:
                raise ValueError(f"Invalid llm {llm!r}: must start with one of {', '.join(ADAPTERS)} followed by ':<model>'")
            self.llm = llm
        if body.get("spl_while_max_iter") is not None:
            self.while_max_iter = max(1, int(body["spl_while_max_iter"]))      # kept for the Settings form; GraphLang loops are bounded by the graph
        if body.get("spl_max_llm_calls") is not None:
            self.max_llm_calls = max(1, int(body["spl_max_llm_calls"]))
        for field, var in KEY_ENV.items():
            if body.get(field) is not None:
                key = str(body[field]).strip()
                self.keys[field] = key
                if key:                                                        # in this process only: never written anywhere
                    os.environ[var] = key
                elif self._env0[var] is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = str(self._env0[var])
        return self.settings()

    def llm_for(self, folder: str) -> str:
        """The ADAPTER:MODEL to generate a model folder's pages with: one of the book's models, else the Settings LLM."""
        for m in self.book.models:
            if model_dir_name(m) == folder:
                return _dd_llm(m)
        if model_dir_name(self.llm) == folder:
            return _dd_llm(self.llm)
        known = ", ".join(model_dir_name(m) for m in self.book.models) or "none"
        raise BookError(f"model {folder!r} is neither one of this book's models ({known}) nor the Settings LLM ({self.llm})")

    # ── generate ────────────────────────────────────────────────────────────────────────────────────────────────────
    def generate(self, q: dict[str, str], emit) -> None:
        """Run generate_chapter for one request, reporting through emit(event, data). Never raises."""
        from .builder import generate_chapter
        try:
            gid, target = safe_segment(q.get("domain", ""), "domain"), safe_segment(q.get("target", ""), "target")
            level, lang = q.get("level", "intro"), safe_segment(q.get("language", "en"), "language")
            folder = safe_segment(q.get("model", ""), "model")
            if level not in LEVELS:
                raise BookError(f"level {level!r} is not one of {', '.join(LEVELS)}")
            ch, llm = self.chapter(gid), self.llm_for(folder)
        except BookError as e:
            emit("gen_error", {"message": str(e)})
            return
        job = (gid, level, lang, folder)
        with self._lock:
            if job in self._busy:
                emit("gen_error", {"message": f"{gid} {level}.{lang} {folder} is already being generated"})
                return
            self._busy.add(job)
        try:
            emit("started", {"domain": gid, "target": target, "model": folder})
            res = generate_chapter(self.book, ch, level=level, language=lang, model=llm, target=target, llm=self.llm_object,
                                   skip_cache=q.get("skip_cache", "").lower() in ("1", "true", "yes"),
                                   max_llm_calls=self.max_llm_calls, log_dir=self.log_dir, on_node=lambda ev: _log_node(ev, emit))
            if res.ok:
                emit("done", {"domain": gid, "target": target, "model": folder})
            else:
                emit("gen_error", {"message": res.error or "generation failed"})
        except Exception as e:                                                # a failure is an event, never a dropped stream
            emit("gen_error", {"message": f"{type(e).__name__}: {e}"})
        finally:
            with self._lock:
                self._busy.discard(job)

    # ── pdf ─────────────────────────────────────────────────────────────────────────────────────────────────────────
    def pdf(self, q: dict[str, str]) -> dict[str, str]:
        from .build import _npm, node_modules
        gid, target = safe_segment(q.get("domain", ""), "domain"), safe_segment(q.get("target", ""), "target")
        level, lang = safe_segment(q.get("level", "intro"), "level"), safe_segment(q.get("language", "en"), "language")
        folder = q.get("model", "")
        folder = safe_segment(folder, "model") if folder else ""
        root = self.book.chapter_root(self.chapter(gid))
        html_dir = variant_dir(root, level, lang, folder)
        suffix = lang_suffix(lang)
        for kind in ("concept", "book"):
            page = html_dir / f"{kind}_{target}{suffix}.html"
            if page.is_file():
                break
        else:
            raise FileNotFoundError(f"Not generated yet: concept_{target}{suffix}.html (or book_{target}{suffix}.html). Generate it first.")
        pdf = html_dir.parent / "pdf" / f"{kind}_{target}{suffix}.pdf"
        modules = node_modules(_npm(), log=lambda m: None)
        script = modules.parent / "html2pdf.mjs"                              # beside node_modules, so `playwright` resolves
        shutil.copyfile(Path(__file__).with_name("html2pdf.mjs"), script)
        done = subprocess.run(["node", str(script), "--input", str(page), "--output", str(pdf)], capture_output=True, text=True,
                              timeout=180)
        if done.returncode != 0:
            raise RuntimeError((done.stderr or done.stdout).strip()[-500:] or "html2pdf failed")
        return {"file": "/".join(pdf.relative_to(root).parts)}


def _inside(root: Path, rel: str) -> Path | None:
    p = (root / rel).resolve()
    return p if p.is_relative_to(root.resolve()) else None


def _log_node(ev: dict[str, Any], emit) -> None:
    """One recipe event -> a line in the Generate log (the steps a reader cares about)."""
    node, out = ev.get("node"), ev.get("outputs") or {}
    if node == "prepare" and isinstance(out.get("out_plan"), dict):
        plan = out["out_plan"]
        emit("log", {"message": f"teaching order: {plan.get('n')} section(s) toward {plan.get('target')}"})
    elif node == "store" and isinstance(out.get("out_stored"), dict):
        line = (out["out_stored"].get("manifest") or "").strip().splitlines()
        if line:
            emit("log", {"message": line[-1]})
    elif ev.get("type") == "GENERATE":
        emit("log", {"message": f"  {node}: model call ({ev.get('ms', 0)} ms)"})
    elif node == "index":
        emit("log", {"message": "book page written"})
    elif node == "refused":
        emit("log", {"message": "refused by policies/generate.cedar"})


def ollama_models() -> list[dict[str, str]]:
    try:
        done = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    names = [line.split()[0] for line in done.stdout.strip().splitlines()[1:] if line.split()]
    return sorted(({"value": n, "label": n} for n in names), key=lambda m: m["label"])


def make_handler(api: BookAPI, base: str, port_ref: list[int]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "bookgraph-serve"

        def log_message(self, format: str, *args: Any) -> None:               # quiet; errors still go to stderr
            pass

        # ── guards ──────────────────────────────────────────────────────────────────────────────────────────────────
        def _origins(self) -> set[str]:
            return {f"http://{HOST}:{port_ref[0]}", f"http://localhost:{port_ref[0]}"}

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in {o.split("//", 1)[1] for o in self._origins()}

        def _same_origin(self) -> bool:
            origin, site = self.headers.get("Origin"), self.headers.get("Sec-Fetch-Site")
            if origin is not None and origin not in self._origins():
                return False
            return site in (None, "same-origin", "none")

        # ── replies ─────────────────────────────────────────────────────────────────────────────────────────────────
        def _send(self, code: int, body: bytes, ctype: str, *, cache: bool = False) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, data: Any) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _file(self, path: Path | None) -> None:
            if path is None or not path.is_file():
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or path.suffix in (".js", ".json", ".yaml", ".mjs"):
                ctype += "; charset=utf-8" if "charset" not in ctype else ""
            self._send(200, path.read_bytes(), ctype, cache=True)

        # ── routes ──────────────────────────────────────────────────────────────────────────────────────────────────
        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            if not self._host_ok():
                return self._send(421, b"wrong Host for this server", "text/plain; charset=utf-8")
            url = urlsplit(self.path)
            path, q = unquote(url.path), {k: v[-1] for k, v in parse_qs(url.query).items()}
            if path.startswith("/api/"):
                if not self._same_origin():
                    return self._json(403, {"detail": "cross-origin request refused"})
                return self._api_get(path, q)
            if path in ("/", base.rstrip("/")):
                self.send_response(302)
                self.send_header("Location", base)
                self.end_headers()
                return None
            if not path.startswith(base):
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            rel = path[len(base):]
            if rel == "domains/catalog.json":
                try:
                    return self._json(200, api.catalog())
                except BookError as e:
                    return self._json(500, {"detail": str(e)})
            if rel.startswith("domains/"):
                return self._file(api.domains_file(rel[len("domains/"):]))
            return self._file(_inside(api.site_dir, rel or "index.html") if rel else api.site_dir / "index.html")

        def _api_get(self, path: str, q: dict[str, str]) -> None:
            try:
                if path == "/api/generate":
                    return self._sse(q)
                if path == "/api/pdf":
                    return self._json(200, api.pdf(q))
                if path == "/api/settings":
                    return self._json(200, api.settings())
                if path == "/api/settings/ollama-models":
                    return self._json(200, ollama_models())
                if path == "/api/domains":
                    return self._json(200, api.catalog())
                if m := re.fullmatch(r"/api/domains/([\w-]+)/status", path):
                    entry = next((e for e in api.catalog() if e["id"] == m.group(1)), None)
                    if entry is None:
                        return self._json(404, {"detail": "Domain not found"})
                    return self._json(200, {"id": entry["id"], "has_book": entry.get("has_book", False)})
            except BookError as e:
                return self._json(400, {"detail": str(e)})
            except FileNotFoundError as e:
                return self._json(404, {"detail": str(e)})
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
                return self._json(500, {"detail": str(e)})
            return self._json(404, {"detail": "not found"})

        def do_PUT(self) -> None:
            if not self._host_ok():
                return self._send(421, b"wrong Host for this server", "text/plain; charset=utf-8")
            if urlsplit(self.path).path != "/api/settings":
                return self._json(404, {"detail": "not found"})
            if not self._same_origin() or not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json(403, {"detail": "a same-origin JSON request is required"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("expected a JSON object")
                return self._json(200, api.update_settings(body))
            except (ValueError, TypeError) as e:
                return self._json(400, {"detail": str(e)})

        def _sse(self, q: dict[str, str]) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            events: queue.Queue = queue.Queue()
            worker = threading.Thread(target=api.generate, args=(q, lambda e, d: events.put((e, d))), daemon=True)
            worker.start()
            connected = True
            while worker.is_alive() or not events.empty():
                try:
                    event, data = events.get(timeout=15)
                    chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    chunk = ": keep-alive\n\n"                                # comment line: keeps proxies and the browser waiting
                if connected:
                    try:
                        self.wfile.write(chunk.encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        connected = False                                     # the reader left: generation still finishes and caches
            self.close_connection = True

    return Handler


def serve(book: Book, site_dir: Path, *, port: int = 8200, log_dir: Path | None = None, ready=None, llm: Any = None) -> None:
    """Serve until interrupted. `ready(server)` is called once listening (tests use port 0 and read server.server_port)."""
    from .build import site_base
    from .export import ensure_navigators
    ensure_navigators(book)
    port_ref = [port]
    api = BookAPI(book, site_dir, log_dir=log_dir, llm=llm)
    server = ThreadingHTTPServer((HOST, port), make_handler(api, site_base(book), port_ref))
    server.daemon_threads = True
    port_ref[0] = server.server_port
    if ready:
        ready(server)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
