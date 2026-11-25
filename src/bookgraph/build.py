"""`bookgraph build`: one book as a static site: the one web app (bookgraph/web/, concept-book-base's frontend) built
against the book, in place of a forked repo per book.

    stage   <stage>/           a copy of bookgraph/web/ (node_modules shared, installed once per package-lock)
            <stage>/public/domains/          the book, through export_book (graphs, pages, navigators, catalog.json)
            <stage>/src/config.js            from book.yaml: default level and model, the models offered, the logo
            <stage>/locales/ui.yaml          the app title, tagline and tag names from `site:`, over the shared strings
            <stage>/index.html               the page title and description from `site:`
    build   npm run build (vite), with BOOKGRAPH_BASE = site.base (default /<book>/), into `out`

book.yaml `site:` (every key optional):

    site:
      base: /cb-calculus/                    # where the site is served; GitHub Pages: /<repo>/
      title: {en: Calculus, zh: 微积分}       # or one string; default: the book's title
      tagline: {en: ..., zh: ...}
      description: ...                       # <meta name="description">
      logo: images/logo.svg                  # Header shows it instead of the title
      tags: {calculus: {en: calculus, zh: 微积分}}
      show_machine_locales: false
      models: [{value: sonnet, label: sonnet (Claude)}]   # default: the book's models

Nothing here writes outside `stage` and `out`; npm runs with the caller's environment, as `npm run build` would.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from .book import Book, model_dir_name
from .exceptions import BookError
from .export import export_book

WEB = Path(__file__).parent / "web"
CACHE = Path.home() / "book-graph" / "web"          # node_modules, one install per package-lock.json


def _lang_map(value: Any, fallback: str) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {"en": str(value)} if value else {"en": fallback}


# Adapter names as the app's built-in model list shows them ("sonnet (Claude)", "gemma4 (Ollama)").
_ADAPTER_LABEL = {"claude_cli": "Claude", "anthropic": "Claude", "claude": "Claude", "ollama": "Ollama", "openai": "OpenAI",
                  "openrouter": "OpenRouter", "gemini": "Gemini"}


def _model_label(model: str) -> str:
    folder = model_dir_name(model)
    adapter = model.split(":", 1)[0] if ":" in model else ""
    return f"{folder} ({_ADAPTER_LABEL.get(adapter, adapter)})" if adapter else folder


def config_js(book: Book) -> str:
    """src/config.js for this book (see web/src/config.js for the fields and their defaults)."""
    site = book.site or {}
    models = site.get("models")
    if models is None and book.models:                   # only the book's models: other ones have no pages on a static site
        models = [{"value": model_dir_name(m), "label": _model_label(m)} for m in book.models]
    cfg = {"logoImage": site.get("logo") or None, "showMachineLocales": bool(site.get("show_machine_locales", False)),
           "defaultLevel": book.levels[0] if book.levels else "intro",
           "defaultModel": (models[0]["value"] if models else "sonnet"), "models": models or None}
    body = ",\n".join(f"  {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in cfg.items())
    return f"// Written by `bookgraph build` from {book.root / 'book.yaml'}; edit book.yaml, not this file.\nexport const appConfig = {{\n{body},\n}}\n"


def ui_yaml(book: Book, base_text: str) -> str:
    """locales/ui.yaml with the book's title, tagline and tag names over the shared strings."""
    ui = yaml.safe_load(base_text) or {}
    site = book.site or {}
    app = ui.setdefault("app", {})
    app["title"] = _lang_map(site.get("title"), book.title)
    if site.get("tagline"):
        app["tagline"] = _lang_map(site["tagline"], "")
    tags = site.get("tags")
    if isinstance(tags, dict) and tags:
        ui["tag"] = {str(t): _lang_map(v, str(t)) for t, v in tags.items()}
    return "# Written by `bookgraph build`: bookgraph/web/locales/ui.yaml with this book's `site:` strings.\n" + \
        yaml.safe_dump(ui, allow_unicode=True, sort_keys=False, width=1000)


def index_html(book: Book, base_text: str) -> str:
    site = book.site or {}
    title = _lang_map(site.get("title"), book.title).get("en") or book.title
    desc = str(site.get("description") or f"{book.title}: a concept book, explored through its concept graph.")
    out = re.sub(r"<title>.*?</title>", lambda _m: f"<title>{html.escape(title)}</title>", base_text, count=1)
    return re.sub(r'(<meta name="description" content=")[^"]*(")', lambda m: m.group(1) + html.escape(desc, quote=True) + m.group(2),
                  out, count=1)


def _npm() -> str:
    npm = shutil.which("npm")
    if not npm:
        raise BookError("npm is not on PATH: `bookgraph build` needs Node.js (npm) to run the web app's vite build")
    return npm


def node_modules(npm: str, *, log=print) -> Path:
    """The web app's node_modules, installed with `npm ci` once per package-lock.json (shared by every book)."""
    lock = (WEB / "package-lock.json").read_bytes()
    home = CACHE / hashlib.sha256(lock).hexdigest()[:16]
    if not (home / "node_modules" / ".package-lock.json").is_file():
        home.mkdir(parents=True, exist_ok=True)
        for f in ("package.json", "package-lock.json"):
            shutil.copyfile(WEB / f, home / f)
        log(f"installing the web app's dependencies into {home} (npm ci, once per package-lock.json)")
        done = subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=home, capture_output=True, text=True)
        if done.returncode != 0:
            raise BookError(f"npm ci failed in {home}:\n{(done.stderr or done.stdout)[-2000:]}")
    return home / "node_modules"


def stage_site(book: Book, stage: Path) -> Path:
    """Copy the web app into `stage` and put the book into it. Returns `stage`."""
    if stage.exists():
        for child in stage.iterdir():                                       # node_modules is a link: never followed
            if child.is_symlink() or child.is_file():
                child.unlink()
            else:
                shutil.rmtree(child)
    shutil.copytree(WEB, stage, dirs_exist_ok=True, ignore=shutil.ignore_patterns("node_modules", "dist"))
    export_book(book, stage / "public" / "domains")
    (stage / "public" / "domains" / "catalog.json.lock").unlink(missing_ok=True)      # a writers' lock, not site content
    logo = (book.site or {}).get("logo")
    if logo and not re.match(r"^(https?:|data:|/)", str(logo)):
        src = (book.root / str(logo)).resolve()
        if not src.is_file() or not src.is_relative_to(book.root):
            raise BookError(f"site.logo {logo!r}: no such file inside the book folder")
        (stage / "public" / Path(str(logo)).parent).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, stage / "public" / str(logo))
    (stage / "src" / "config.js").write_text(config_js(book), encoding="utf-8")
    (stage / "locales" / "ui.yaml").write_text(ui_yaml(book, (WEB / "locales" / "ui.yaml").read_text(encoding="utf-8")),
                                               encoding="utf-8")
    (stage / "index.html").write_text(index_html(book, (WEB / "index.html").read_text(encoding="utf-8")), encoding="utf-8")
    return stage


def site_base(book: Book) -> str:
    base = str((book.site or {}).get("base") or f"/{book.book}/")
    return base if base.endswith("/") else base + "/"


def build_site(book: Book, out: str | Path, *, stage: str | Path | None = None, log=print) -> Path:
    """Build the book's static site into `out` and return it."""
    npm = _npm()
    out = Path(out).expanduser().resolve()
    stage = Path(stage).expanduser().resolve() if stage else Path.home() / "book-graph" / "build" / book.press / book.book
    stage.mkdir(parents=True, exist_ok=True)
    modules = node_modules(npm, log=log)
    stage_site(book, stage)
    link = stage / "node_modules"
    if not link.exists():
        link.symlink_to(modules, target_is_directory=True)
    env = {**os.environ, "BOOKGRAPH_BASE": site_base(book)}
    done = subprocess.run([npm, "run", "build", "--", "--outDir", str(out), "--emptyOutDir"], cwd=stage, env=env,
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise BookError(f"vite build failed in {stage}:\n{(done.stderr or done.stdout)[-3000:]}")
    return out
