"""`bookgraph publish`: deploy a built site to a git branch (GitHub Pages: `gh-pages`), as the apps' `npm run deploy`
(`gh-pages -d dist --no-history --dotfiles`) did, but as a policy-checked GraphLang recipe (recipes/publish/).

book.yaml:

    publish:
      repo: digital-duck/cb-calculus          # GitHub owner/name -> git@github.com:<owner>/<name>.git
      # remote: <any git URL, or a path to a local repository>   (instead of repo)
      branch: gh-pages                        # default
      cname: books.example.org                # optional: a custom domain (CNAME file; the site is then served at /)

The recipe's `check` step gathers facts and the GUARD (policies/publish.cedar) permits the push only when all hold:

    site_ok     the folder is a built site: index.html and domains/catalog.json
    remote_ok   the remote is a GitHub URL or a local repository, and answers `git ls-remote`
    branch_ok   the branch is not the remote's default branch, nor main/master (a push replaces the branch's history)
    base_ok     the site was built for where it will be served: /<repo name>/ on GitHub project pages, / for
                <owner>.github.io or a custom domain

`deploy` (risky: true) commits the site as the single commit of a fresh history and force-pushes it to the branch, so
the branch holds exactly the site, as `--no-history` did. Both steps see HOME and SSH_AUTH_SOCK (env_passthrough), so git
uses the caller's SSH keys or agent, and nothing else of the caller's environment.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .book import Book
from .exceptions import BookError

_GITHUB = re.compile(r"^(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
PROTECTED = ("main", "master")


def remote_of(book: Book) -> str:
    pub = book.publish or {}
    if pub.get("remote"):
        r = str(pub["remote"])
        if not re.match(r"^[\w+.-]+://|^[\w.-]+@", r):                   # a local path, relative to the book
            r = str((book.root / r).expanduser().resolve())
        return r
    if pub.get("repo"):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", str(pub["repo"])):
            raise BookError(f"publish.repo {pub['repo']!r} must be OWNER/NAME")
        return f"git@github.com:{pub['repo']}.git"
    raise BookError("book.yaml has no publish.repo (or publish.remote): nowhere to publish")


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    if not env.get("SSH_AUTH_SOCK"):
        env.pop("SSH_AUTH_SOCK", None)                                  # passed through empty when the caller had none
    env.setdefault("GIT_TERMINAL_PROMPT", "0")                          # never wait for a password
    return env


def _git(*args: str, cwd: str | Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=_git_env())


def expected_base(remote: str, cname: str) -> str | None:
    """Where the site will be served, or None when that is not known (a non-GitHub remote)."""
    if cname:
        return "/"
    m = _GITHUB.match(remote)
    if not m:
        return None
    owner, name = m.groups()
    return "/" if name.lower() == f"{owner.lower()}.github.io" else f"/{name}/"


def built_base(site: Path) -> str:
    """The base path a vite build was made for, read from its index.html's asset URLs ("" when it cannot be told)."""
    try:
        page = (site / "index.html").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'(?:src|href)="(/[^"]*?)assets/', page)
    return m.group(1) if m else ""


def check(i: dict[str, Any]) -> dict[str, Any]:
    """The facts the publish policy decides on, and a plain-language reason for each that fails."""
    site, remote, branch = Path(i["site_dir"]), str(i["remote"]), str(i.get("branch") or "gh-pages")
    cname, base = str(i.get("cname") or ""), str(i.get("base") or "")
    problems = []
    site_ok = site.is_absolute() and (site / "index.html").is_file() and (site / "domains" / "catalog.json").is_file()
    if not site_ok:
        problems.append(f"{site} is not a built site (index.html and domains/catalog.json); run `bookgraph build`")
    local = Path(remote).is_absolute()
    remote_ok = bool(_GITHUB.match(remote)) or (local and Path(remote).is_dir())
    default = ""
    if not remote_ok:
        problems.append(f"remote {remote!r} is neither a GitHub repository URL nor a local repository")
    else:
        r = _git("ls-remote", "--symref", remote, "HEAD", timeout=60)
        if r.returncode != 0:
            remote_ok = False
            problems.append(f"cannot reach {remote}: {(r.stderr or r.stdout).strip()[-300:]}")
        elif m := re.search(r"^ref: refs/heads/(\S+)\s+HEAD", r.stdout, re.M):
            default = m.group(1)
    branch_ok = bool(re.fullmatch(r"[\w./-]+", branch)) and branch not in PROTECTED and branch != default
    if not branch_ok:
        problems.append(f"branch {branch!r} is the remote's default or a protected branch ({', '.join(PROTECTED)}); a publish"
                        f" replaces the branch's whole history")
    base = built_base(site) or base                                     # what the folder was really built for
    want = None if local else expected_base(remote, cname)
    base_ok = want is None or base == want
    if not base_ok:
        problems.append(f"the site was built for {base!r} but will be served at {want!r}: set site.base: {want} and rebuild")
    files = sum(1 for p in site.rglob("*") if p.is_file()) if site_ok else 0
    return {"site_ok": site_ok, "remote_ok": remote_ok, "branch_ok": branch_ok, "base_ok": base_ok, "default_branch": default,
            "files": files, "problems": "; ".join(problems) or "none"}


def deploy(i: dict[str, Any]) -> dict[str, Any]:
    """Commit the site as one fresh commit and force-push it to the branch (or, with dry_run, say what would be pushed)."""
    site, remote, branch = Path(i["site_dir"]), str(i["remote"]), str(i.get("branch") or "gh-pages")
    cname, message = str(i.get("cname") or ""), str(i.get("message") or "Publish with book-graph")
    files = sum(1 for p in site.rglob("*") if p.is_file())
    if i.get("dry_run"):
        return {"pushed": False, "commit": "", "summary": f"dry run: would push {files} file(s) from {site} to {remote} {branch}"}
    with tempfile.TemporaryDirectory(prefix="bookgraph-publish-") as tmp:
        work = Path(tmp) / "site"
        shutil.copytree(site, work)                                     # dotfiles included (.nojekyll), as --dotfiles did
        if cname:
            (work / "CNAME").write_text(cname + "\n", encoding="utf-8")
        for args in (("init", "-q", "-b", branch), ("add", "-A"), ("commit", "-q", "-m", message)):
            r = _git(*args, cwd=work)
            if r.returncode != 0:
                raise BookError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[-500:]}")
        sha = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
        r = _git("push", "--force", "--quiet", remote, f"HEAD:refs/heads/{branch}", cwd=work, timeout=600)
        if r.returncode != 0:
            raise BookError(f"git push to {remote} {branch} failed: {(r.stderr or r.stdout).strip()[-500:]}")
    return {"pushed": True, "commit": sha, "summary": f"pushed {files} file(s) to {remote} {branch} as {sha[:12]}"}


STEPS = {"publish_check": check, "publish_deploy": deploy}


@contextmanager
def _passthrough_env() -> Iterator[None]:
    """env_passthrough fails on an unset name: give SSH_AUTH_SOCK an empty value for the run when the caller has none."""
    added = "SSH_AUTH_SOCK" not in os.environ
    if added:
        os.environ["SSH_AUTH_SOCK"] = ""
    try:
        yield
    finally:
        if added:
            os.environ.pop("SSH_AUTH_SOCK", None)


def publish_site(book: Book, site_dir: str | Path, *, dry_run: bool = False, message: str = "",
                 log_dir: Path | None = None) -> dict[str, str]:
    """Run the publish recipe for a built site. Returns {summary, commit}; raises BookError when refused or failed."""
    from .build import site_base
    from .run import RecipeError, run_recipe
    pub = book.publish or {}
    inputs = {"site_dir": str(Path(site_dir).expanduser().resolve()), "remote": remote_of(book),
              "branch": str(pub.get("branch") or "gh-pages"), "cname": str(pub.get("cname") or ""), "base": site_base(book),
              "message": message or f"Publish {book.press}/{book.book} with book-graph", "dry_run": dry_run}
    kw: dict[str, Any] = {} if log_dir is None else {"log_dir": log_dir}
    with _passthrough_env():
        try:
            run = run_recipe("publish", inputs, max_llm_calls=0, **kw)
        except RecipeError as e:
            raise BookError(f"publish failed: {e}") from e
    if run.exit:
        raise BookError(str(run.outputs.get("summary") or run.exit))
    return {"summary": str(run.outputs.get("summary", "")), "commit": str(run.outputs.get("commit", ""))}
