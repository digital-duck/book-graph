"""`bookgraph build`: staging the one web app against a book (offline), and an opt-in browser smoke test of a real build
(BOOKGRAPH_WEB_TESTS=1: needs npm, network for the first `npm ci`, and Playwright's chromium)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from bookgraph.book import load_book
from bookgraph.build import WEB, build_site, config_js, index_html, site_base, stage_site, ui_yaml

SITE = """site:
  base: /cb-demo
  title: {en: Demo Book, zh: 演示}
  tagline: {en: Counting things}
  description: A <small> demo.
  tags: {math: {en: math, zh: 数学}}
"""


def book_with_site(library: Path):
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text().replace("models: []", "models: [claude_cli:sonnet, ollama:qwen2.5:7b]") + SITE)
    return load_book(y)


def test_web_app_is_the_shipped_copy_with_its_defaults():
    cfg = (WEB / "src" / "config.js").read_text()
    assert "defaultLevel: 'intro'" in cfg and "defaultModel: 'sonnet'" in cfg and "models: null" in cfg
    assert "process.env.BOOKGRAPH_BASE" in (WEB / "vite.config.js").read_text()
    assert not (WEB / "public" / "domains").exists() and not (WEB / "node_modules").exists()


def test_config_ui_and_index_come_from_book_yaml(library):
    book = book_with_site(library)
    cfg = config_js(book)
    assert 'defaultLevel: "core"' in cfg and 'defaultModel: "sonnet"' in cfg
    assert '{"value": "sonnet", "label": "sonnet (Claude)"}' in cfg and '{"value": "qwen2-5-7b", "label": "qwen2-5-7b (Ollama)"}' in cfg
    ui = yaml.safe_load(ui_yaml(book, (WEB / "locales" / "ui.yaml").read_text()))
    assert ui["app"]["title"] == {"en": "Demo Book", "zh": "演示"} and ui["app"]["tagline"] == {"en": "Counting things"}
    assert ui["tag"] == {"math": {"en": "math", "zh": "数学"}} and ui["nav"]["about"]["zh"]            # shared strings kept
    page = index_html(book, (WEB / "index.html").read_text())
    assert "<title>Demo Book</title>" in page and 'content="A &lt;small&gt; demo."' in page
    assert site_base(book) == "/cb-demo/"


def test_defaults_without_a_site_block(library):
    book = load_book(library / "acme" / "demo")
    assert site_base(book) == "/demo/" and 'models: null' in config_js(book)
    assert yaml.safe_load(ui_yaml(book, (WEB / "locales" / "ui.yaml").read_text()))["app"]["title"] == {"en": "Demo Book"}


def test_stage_holds_the_app_and_the_book(library, tmp_path):
    book = book_with_site(library)
    stage = stage_site(book, tmp_path / "stage")
    assert (stage / "src" / "main.js").is_file() and (stage / "package-lock.json").is_file()
    cat = json.loads((stage / "public/domains/catalog.json").read_text())
    assert cat[0]["id"] == "demo_ch01" and (stage / "public/domains/demo_ch01/output/graph.html").is_file()
    assert not (stage / "public/domains/catalog.json.lock").exists()
    (stage / "src" / "stale.js").write_text("x")                                 # a restage starts clean
    stage_site(book, stage)
    assert not (stage / "src" / "stale.js").exists()


def test_logo_must_be_inside_the_book(library, tmp_path):
    from bookgraph.exceptions import BookError
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text() + "site: {logo: ../../../etc/passwd}\n")
    with pytest.raises(BookError, match="logo"):
        stage_site(load_book(y), tmp_path / "stage")
    (library / "acme/demo/logo.svg").write_text("<svg/>")
    y.write_text(y.read_text().replace("../../../etc/passwd", "logo.svg"))
    stage = stage_site(load_book(y), tmp_path / "stage")
    assert (stage / "public/logo.svg").read_text() == "<svg/>" and 'logoImage: "logo.svg"' in (stage / "src/config.js").read_text()


@pytest.mark.skipif(os.environ.get("BOOKGRAPH_WEB_TESTS") != "1" or not shutil.which("npm"),
                    reason="set BOOKGRAPH_WEB_TESTS=1 (needs npm and Playwright's chromium)")
def test_built_site_works_in_a_browser(library, tmp_path):
    from conftest import Scripted
    from bookgraph.builder import generate_chapter
    book = book_with_site(library)
    assert generate_chapter(book, book.chapters[0], level="core", language="en", model="claude_cli:sonnet", llm=Scripted(),
                            log_dir=tmp_path / "log").ok
    stage = tmp_path / "stage"
    root = tmp_path / "serve"
    build_site(book, root / "cb-demo", stage=stage, log=lambda m: None)
    shutil.copyfile(Path(__file__).parent / "web" / "smoke.mjs", stage / "smoke.mjs")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        out = tmp_path / "smoke.json"
        done = subprocess.run(["node", "smoke.mjs", f"http://127.0.0.1:{server.server_port}/cb-demo/", str(out)], cwd=stage,
                              capture_output=True, text=True, timeout=180)
        assert done.returncode == 0, done.stderr[-2000:]
    finally:
        server.shutdown()
    r = json.loads(out.read_text())
    assert r["errors"] == [] and r["title"] == "Demo Book" and r["cards"] == ["Chapter 1"] and r["graphNodes"] == 5
    assert [f["url"].rsplit("/", 1)[-1] for f in r["contentFrames"]] == ["concept_budget.html"]
    assert "Body of Budget" in r["contentFrames"][0]["text"]
