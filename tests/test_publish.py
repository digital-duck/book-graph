"""`bookgraph publish`: the publish recipe against local bare repositories (nothing leaves the machine)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from bookgraph.book import load_book
from bookgraph.cli import main
from bookgraph.exceptions import BookError
from bookgraph.publish import expected_base, publish_site, remote_of


def git(*args, cwd=None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HOME with a git identity, so the steps' git (HOME is passed through) never reads the real one."""
    h = tmp_path / "home"
    h.mkdir()
    (h / ".gitconfig").write_text("[user]\n  name = Test Publisher\n  email = test@example.invalid\n[init]\n  defaultBranch = main\n")
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def remote(tmp_path, home) -> Path:
    """A bare repository whose default branch is main, with one commit on it."""
    bare = tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(bare))
    src = tmp_path / "src"
    git("init", "-q", "-b", "main", str(src))
    (src / "README.md").write_text("source\n")
    git("add", "-A", cwd=src)
    git("commit", "-q", "-m", "source", cwd=src)
    git("push", "-q", str(bare), "main", cwd=src)
    return bare


def site(tmp_path: Path) -> Path:
    s = tmp_path / "dist"
    (s / "domains").mkdir(parents=True, exist_ok=True)
    (s / "index.html").write_text("<html>site</html>")
    (s / ".nojekyll").write_text("")
    (s / "domains" / "catalog.json").write_text("[]")
    return s


def book_to(library: Path, publish: str):
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text() + publish)
    return load_book(y)


def test_publish_replaces_the_branch_with_one_commit(library, tmp_path, remote):
    book = book_to(library, f"publish: {{remote: {remote}, cname: books.example.org}}\n")
    s = site(tmp_path)
    dry = publish_site(book, s, dry_run=True, log_dir=tmp_path / "log")
    assert dry["summary"].startswith("dry run: would push 3 file(s)") and dry["commit"] == ""
    assert "gh-pages" not in git("ls-remote", "--heads", str(remote))
    first = publish_site(book, s, log_dir=tmp_path / "log")
    assert first["commit"] and git("rev-parse", "gh-pages", cwd=remote) == first["commit"]
    files = set(git("ls-tree", "-r", "--name-only", "gh-pages", cwd=remote).splitlines())
    assert files == {"index.html", ".nojekyll", "domains/catalog.json", "CNAME"}
    (s / "index.html").write_text("<html>site v2</html>")
    second = publish_site(book, s, log_dir=tmp_path / "log")
    assert second["commit"] != first["commit"] and git("rev-list", "--count", "gh-pages", cwd=remote) == "1"   # no history
    assert git("rev-parse", "main", cwd=remote) != second["commit"]                                         # main untouched


@pytest.mark.parametrize("branch", ["main", "master"])
def test_policy_refuses_the_default_or_a_protected_branch(library, tmp_path, remote, branch):
    book = book_to(library, f"publish: {{remote: {remote}, branch: {branch}}}\n")
    with pytest.raises(BookError, match="refused by policies/publish.cedar: .*protected branch"):
        publish_site(book, site(tmp_path), log_dir=tmp_path / "log")
    assert git("rev-list", "--count", "main", cwd=remote) == "1"


def test_policy_refuses_an_unbuilt_site_and_an_unknown_remote(library, tmp_path, remote):
    book = book_to(library, f"publish: {{remote: {remote}}}\n")
    with pytest.raises(BookError, match="not a built site"):
        publish_site(book, tmp_path / "nothing", log_dir=tmp_path / "log")
    book = book_to(library, "")
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text().replace(f"remote: {remote}", f"remote: {tmp_path / 'missing.git'}"))
    with pytest.raises(BookError, match="neither a GitHub repository URL nor a local repository"):
        publish_site(load_book(y), site(tmp_path), log_dir=tmp_path / "log")


def test_targets_and_base_paths(library):
    assert remote_of(book_to(library, "publish: {repo: digital-duck/cb-calculus}\n")) == "git@github.com:digital-duck/cb-calculus.git"
    assert expected_base("git@github.com:digital-duck/cb-calculus.git", "") == "/cb-calculus/"
    assert expected_base("https://github.com/digital-duck/digital-duck.github.io", "") == "/"
    assert expected_base("git@github.com:o/r.git", "books.example.org") == "/" and expected_base("/srv/x.git", "") is None
    y = library / "acme/demo/book.yaml"
    y.write_text(y.read_text().replace("publish: {repo: digital-duck/cb-calculus}\n", "publish: {repo: not a repo}\n"))
    with pytest.raises(BookError, match="OWNER/NAME"):
        remote_of(load_book(y))
    y.write_text(y.read_text().replace("publish: {repo: not a repo}\n", ""))
    with pytest.raises(BookError, match="nowhere to publish"):
        remote_of(load_book(y))


def test_base_mismatch_is_refused_for_github_pages(tmp_path, home, monkeypatch):
    from bookgraph import publish as pub
    monkeypatch.setattr(pub, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 0, "ref: refs/heads/main\tHEAD\n", ""))
    facts = pub.check({"site_dir": str(site(tmp_path)), "remote": "git@github.com:digital-duck/cb-demo.git", "base": "/demo/"})
    assert facts["remote_ok"] and facts["branch_ok"] and not facts["base_ok"] and "site.base: /cb-demo/" in facts["problems"]
    facts = pub.check({"site_dir": str(site(tmp_path)), "remote": "git@github.com:digital-duck/cb-demo.git", "base": "/cb-demo/"})
    assert facts["base_ok"] and facts["problems"] == "none"
    (tmp_path / "dist" / "index.html").write_text('<script type="module" src="/demo/assets/index-x.js"></script>')
    facts = pub.check({"site_dir": str(tmp_path / "dist"), "remote": "git@github.com:digital-duck/cb-demo.git", "base": "/cb-demo/"})
    assert not facts["base_ok"] and "built for '/demo/'" in facts["problems"]       # the built page wins over book.yaml


def test_cli_asks_before_replacing_the_site(library, tmp_path, remote):
    book_to(library, f"publish: {{remote: {remote}}}\n")
    s = site(tmp_path)
    run = lambda *a, **k: CliRunner().invoke(main, ["-L", str(library), "publish", "acme/demo", "--no-build", "-o", str(s), *a], **k)
    r = run(input="n\n")
    assert r.exit_code == 1 and "Replace" in r.output and "gh-pages" not in git("ls-remote", "--heads", str(remote))
    assert "dry run" in run("--dry-run").output
    r = run("--yes", "-m", "Publish v1")
    assert r.exit_code == 0 and "pushed 3 file(s)" in r.output
    assert git("log", "-1", "--format=%s", "gh-pages", cwd=remote) == "Publish v1"
