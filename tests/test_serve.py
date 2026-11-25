"""`bookgraph serve`: the local API and the live site, over real HTTP on 127.0.0.1 (a scripted model, no npm)."""
from __future__ import annotations

import http.client
import json
import os
import threading
from pathlib import Path

import shutil
import subprocess

import pytest

from bookgraph.book import load_book
from bookgraph.serve import serve

from conftest import Scripted


@pytest.fixture
def server(library, tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text().replace("models: []", "models: [claude_cli:sonnet]"))
    site = tmp_path / "site"
    (site / "assets").mkdir(parents=True)
    (site / "index.html").write_text("<html>app</html>")
    (site / "assets" / "app.js").write_text("console.log(1)")
    holder = {}
    ready = threading.Event()
    t = threading.Thread(target=serve, args=(load_book(y), site), daemon=True,
                         kwargs={"port": 0, "log_dir": tmp_path / "log", "llm": Scripted(),
                                 "ready": lambda s: (holder.update(s=s), ready.set())})
    t.start()
    assert ready.wait(10)
    yield holder["s"].server_port
    holder["s"].shutdown()
    for var in ("ANTHROPIC_API_KEY",):
        os.environ.pop(var, None)


def req(port, method, path, *, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def sse(port, path, headers=None) -> list[tuple[str, dict]]:
    status, _, raw = req(port, "GET", path, headers=headers)
    assert status == 200
    events = []
    for block in raw.decode().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.splitlines() if l and not l.startswith(":"))
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_site_and_live_domains(server):
    assert req(server, "GET", "/")[0] == 302 and req(server, "GET", "/demo/")[2] == b"<html>app</html>"
    assert req(server, "GET", "/demo/assets/app.js")[1]["Content-Type"].startswith("text/javascript")
    cat = json.loads(req(server, "GET", "/demo/domains/catalog.json")[2])
    assert cat[0]["id"] == "demo_ch01" and cat[0]["has_navigator"] and not cat[0]["has_book"]
    assert b"budget" in req(server, "GET", "/demo/domains/demo_ch01/input/graph.yaml")[2]
    assert b"vis-network" in req(server, "GET", "/demo/domains/demo_ch01/output/graph.html")[2]   # written at start
    for bad in ("/demo/domains/demo_ch01/output/../../../book.yaml", "/demo/domains/../book.yaml", "/demo/../etc/passwd",
                "/demo/domains/demo_ch01/input/../../../book.yaml", "/demo/domains/nope/input/graph.yaml"):
        assert req(server, "GET", bad)[0] == 404, bad


def test_generate_streams_and_the_pages_appear_live(server):
    events = sse(server, "/api/generate?domain=demo_ch01&target=budget&level=core&language=en&model=sonnet")
    kinds = [e for e, _ in events]
    assert kinds[0] == "started" and kinds[-1] == "done" and "gen_error" not in kinds
    logs = [d["message"] for e, d in events if e == "log"]
    assert logs[0] == "teaching order: 5 section(s) toward budget" and "budget: generated -> concept_budget.html" in logs
    cat = json.loads(req(server, "GET", "/demo/domains/catalog.json")[2])[0]
    assert cat["has_book"] and cat["books"][0]["file"] == "output/core.en/sonnet/html/book_budget.html"
    page = req(server, "GET", "/demo/domains/demo_ch01/output/core.en/sonnet/html/concept_budget.html")[2]
    assert b"Body of Budget" in page
    again = sse(server, "/api/generate?domain=demo_ch01&target=budget&level=core&language=en&model=sonnet")
    assert "budget: cache -> concept_budget.html" in [d["message"] for e, d in again if e == "log"]


@pytest.mark.parametrize("query, message", [
    ("domain=nope&target=budget&model=sonnet", "no chapter 'nope'"),
    ("domain=demo_ch01&target=budget&model=opus", "neither one of this book's models (sonnet)"),
    ("domain=demo_ch01&target=../x&model=sonnet", "target"),
    ("domain=demo_ch01&target=budget&level=grad&model=sonnet", "level 'grad'"),
])
def test_generate_errors_are_events(server, query, message):
    events = sse(server, f"/api/generate?{query}")
    assert events[-1][0] == "gen_error" and message in events[-1][1]["message"]


def test_cross_origin_and_wrong_host_are_refused(server):
    q = "/api/generate?domain=demo_ch01&target=budget&level=core&language=en&model=sonnet"
    assert req(server, "GET", q, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403          # e.g. an <img> on another site
    assert req(server, "GET", q, headers={"Origin": "https://evil.example"})[0] == 403
    assert req(server, "GET", "/demo/", headers={"Host": "evil.example:80"})[0] == 421          # DNS rebinding
    assert not json.loads(req(server, "GET", "/demo/domains/catalog.json")[2])[0]["has_book"]  # nothing ran


def test_settings_keep_keys_in_memory_and_never_echo_them(server):
    s = json.loads(req(server, "GET", "/api/settings")[2])
    assert s["llm"] == "claude_cli:sonnet" and s["anthropic_api_key_set"] is False
    put = lambda body, **h: req(server, "PUT", "/api/settings", body=json.dumps(body),
                                headers={"Content-Type": "application/json", **h})
    assert req(server, "PUT", "/api/settings", body="{}", headers={"Content-Type": "text/plain"})[0] == 403
    assert put({"llm": "x"}, Origin="https://evil.example")[0] == 403
    assert put({"llm": "nope:m"})[0] == 400
    status, _, body = put({"llm": "google:gemini-2.5-pro", "anthropic_api_key": "sk-secret", "spl_max_llm_calls": 7})
    assert status == 200 and b"sk-secret" not in body
    s = json.loads(body)
    assert s["anthropic_api_key_set"] and s["spl_max_llm_calls"] == 7 and os.environ["ANTHROPIC_API_KEY"] == "sk-secret"
    assert json.loads(put({"anthropic_api_key": ""})[2])["anthropic_api_key_set"] is False and "ANTHROPIC_API_KEY" not in os.environ
    events = sse(server, "/api/generate?domain=demo_ch01&target=budget&level=core&language=en&model=gemini-2-5-pro")
    assert events[-1][0] == "done"                                                              # the Settings LLM's folder


def test_domains_api_and_pdf_not_found(server):
    assert json.loads(req(server, "GET", "/api/domains")[2])[0]["id"] == "demo_ch01"
    assert json.loads(req(server, "GET", "/api/domains/demo_ch01/status")[2]) == {"id": "demo_ch01", "has_book": False}
    assert req(server, "GET", "/api/domains/nope/status")[0] == 404
    status, _, body = req(server, "GET", "/api/pdf?domain=demo_ch01&target=budget&level=core&language=en&model=sonnet")
    assert status == 404 and "Generate it first" in json.loads(body)["detail"]


@pytest.mark.skipif(os.environ.get("BOOKGRAPH_WEB_TESTS") != "1" or not shutil.which("npm"),
                    reason="set BOOKGRAPH_WEB_TESTS=1 (needs npm and Playwright's chromium)")
def test_generate_and_export_pdf_from_the_real_app(library, tmp_path):
    """The built web app against `serve`, in Chromium: Generate from the content panel, then Export PDF."""
    from bookgraph.build import build_site, node_modules, _npm
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text().replace("models: []", "models: [claude_cli:sonnet]"))
    book = load_book(y)
    site = build_site(book, tmp_path / "site", stage=tmp_path / "stage", log=lambda m: None)
    holder, ready = {}, threading.Event()
    threading.Thread(target=serve, args=(book, site), daemon=True,
                     kwargs={"port": 0, "log_dir": tmp_path / "log", "llm": Scripted(),
                             "ready": lambda s: (holder.update(s=s), ready.set())}).start()
    assert ready.wait(10)
    script = node_modules(_npm(), log=lambda m: None).parent / "serve_smoke.mjs"
    shutil.copyfile(Path(__file__).parent / "web" / "serve_smoke.mjs", script)
    out = tmp_path / "out.json"
    try:
        done = subprocess.run(["node", str(script), f"http://127.0.0.1:{holder['s'].server_port}/demo/", "demo_ch01", "budget", str(out)],
                              capture_output=True, text=True, timeout=300)
    finally:
        holder["s"].shutdown()
    assert done.returncode == 0, done.stderr[-2000:]
    r = json.loads(out.read_text())
    assert r["errors"] == [] and "✓ Done" in r["log"] and "budget: generated -> concept_budget.html" in r["log"]
    assert any(c["url"].endswith("/core.en/sonnet/html/concept_budget.html") and "Body of Budget" in c["text"] for c in r["content"])
    assert r["pdfUrl"].endswith("/output/core.en/sonnet/pdf/concept_budget.pdf") and r["pdfType"] == "application/pdf"
    assert r["pdfMagic"] == "%PDF-"
