"""`bookgraph` (alias `bgr`): the developer and publisher command line."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from zinet.click_helpers import HELP_OPTION_NAMES, FullHelpGroup, version_option

from .book import load_book, scaffold_book
from .builder import generate_book
from .catalog import register_book, scan_library
from .exceptions import BookError
from .graph import load_graph
from .paths import default_library, safe_segment


def _library(ctx_value: str | None) -> Path:
    return Path(ctx_value).expanduser() if ctx_value else default_library()


def _is_segment(value: str) -> bool:
    try:
        safe_segment(value)
        return True
    except BookError:
        return False


def _find_book(target: str, library: Path) -> Path:
    """TARGET is a book.yaml, a book folder, or `press/book` (resolved under the library)."""
    p = Path(target).expanduser()
    explicit = p.is_absolute() or target.startswith((".", "~")) or p.is_file()      # `./acme/demo` and files mean the path
    if explicit and p.exists():
        return p
    press, _, book = target.partition("/")
    if "/" in target and not explicit and _is_segment(press) and _is_segment(book):
        in_library = library / press / book
        if in_library.exists() or not p.exists():                                  # the library wins over a folder of that name here
            return in_library
    if p.exists():
        return p
    raise BookError(f"no book at {target!r} (give a book.yaml, a folder, or press/book under {library})")


@click.group(cls=FullHelpGroup, context_settings={"help_option_names": HELP_OPTION_NAMES})
@version_option("book-graph", "bookgraph")
@click.option("--library", "-L", envvar="BOOKGRAPH_LIBRARY", default=None, help="the library root (default ~/book-graph/library)")
@click.pass_context
def main(ctx: click.Context, library: str | None) -> None:
    """Concept-books from graphs: press > book > chapter, each chapter a graph.yaml."""
    ctx.obj = _library(library)


def _guard(fn):
    """Turn a BookError into a one-line message and exit 1."""
    import functools

    @functools.wraps(fn)
    def inner(*a, **k):
        try:
            return fn(*a, **k)
        except BookError as e:
            raise click.ClickException(str(e))
    return inner


@main.command()
@click.argument("press")
@click.argument("book")
@click.option("--title", default="", help="the book's title (default: the slug, title-cased)")
@click.pass_obj
@_guard
def new(library: Path, press: str, book: str, title: str) -> None:
    """Scaffold <library>/PRESS/BOOK/ with a book.yaml and a one-chapter starter graph."""
    root = scaffold_book(library, press, book, title)
    click.echo(f"created {root}")


@main.command()
@click.argument("target")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.option("--strict", is_flag=True, help="fail on warnings too")
@click.pass_obj
@_guard
def validate(library: Path, target: str, fmt: str, strict: bool) -> None:
    """Check a graph.yaml, or every chapter graph of a book (book.yaml, folder or press/book): acyclic, declared, reducible to
    primitives, every node defined, no id declared twice, and each chapter's capstone a node of its graph. Warnings (a
    capstone that is not an application, tiers that do not rise along an edge) do not fail unless --strict. Exit 1 on a
    problem."""
    p = Path(target).expanduser()
    results: list[dict] = []
    if p.is_file() and p.name != "book.yaml":
        paths = [("", p, "")]
    else:
        book = load_book(_find_book(target, library))
        paths = [(c.id, book.graph_path(c), c.capstone) for c in book.chapters]
    for name, gp, capstone in paths:
        warnings: list[str] = []
        try:
            g = load_graph(gp)
            problems = g.validate()
            if not problems:
                more, warn = g.check_target(capstone)
                problems, warnings = problems + more, warn + g.warnings()
        except BookError as e:
            problems = [str(e)]
        ok = not problems and not (strict and warnings)
        results.append({"chapter": name or str(gp), "ok": ok, "problems": problems, "warnings": warnings})
    if fmt == "json":
        click.echo(json.dumps(results, indent=1))
    else:
        for r in results:
            click.echo(f"{'ok  ' if r['ok'] else 'FAIL'} {r['chapter']}" + ("" if not r["problems"] else ": " + "; ".join(r["problems"])))
            for w in r["warnings"]:
                click.echo(f"     warning: {w}")
    if not all(r["ok"] for r in results):
        sys.exit(1)


@main.command()
@click.argument("graph", type=click.Path(exists=True, dir_okay=False))
@click.option("--target", default="", help="the node to write toward (default: the last application)")
@click.option("--payoff-weight", type=float, default=1.5)
@_guard
def order(graph: str, target: str, payoff_weight: float) -> None:
    """Print the teaching order for a chapter graph: the order its sections are written in."""
    g = load_graph(graph)
    for n in g.plan(target or g.default_target(), payoff_weight):
        click.echo(n)


@main.command()
@click.argument("target")
@click.option("--chapter", "-c", "chapters", multiple=True, help="only this chapter (repeatable)")
@click.option("--level", "levels", multiple=True, help="only this level (repeatable)")
@click.option("--lang", "languages", multiple=True, help="only this language (repeatable)")
@click.option("--llm", "models", multiple=True, help="ADAPTER:MODEL (repeatable; default: the book's models)")
@click.option("--target-node", "node", default="", help="write toward this node instead of the chapter's capstone")
@click.option("--skip-cache", is_flag=True, help="regenerate every section (a resumed run keeps what it already regenerated)")
@click.option("--max-llm-calls", type=int, default=200, show_default=True,
              help="the cap for EACH chapter x level x language x model run, not for the whole book")
@click.option("--budget", type=int, default=None, help="the cap for the whole run, across resumes (with --resume: raises it)")
@click.option("--resume", "resume", default=None, metavar="RUN", help="continue a run (its id, or `last`) with its own options")
@click.pass_obj
@_guard
def generate(library: Path, target: str, chapters, levels, languages, models, node: str, skip_cache: bool, max_llm_calls: int,
             budget, resume) -> None:
    """Generate a book's pages (book.yaml, folder or press/book): every chapter x level x language x model, or the subset asked,
    as a resumable run: a run that stops (a failure, a budget, Ctrl-C) continues with --resume last."""
    book = load_book(_find_book(target, library))
    if node and not chapters and not resume:
        raise click.UsageError("--target-node names a node of one chapter: also give -c CHAPTER")
    if resume and (chapters or levels or languages or models or node or skip_cache):
        raise click.UsageError("--resume continues a run with its own selection and options: give none (only --budget, to raise its budget)")

    def line(v, c) -> None:
        tail = c.book if c.ok else f"FAILED: {c.error}"
        click.echo(f"{'ok  ' if c.ok else 'FAIL'} {v.key} ({c.llm_calls} model calls) {tail}")

    try:
        res = generate_book(book, library, chapters=list(chapters) or None, levels=list(levels) or None,
                            languages=list(languages) or None, models=list(models) or None, target=node, skip_cache=skip_cache,
                            max_llm_calls=max_llm_calls, budget=budget, resume=resume, on_variant=line)
    except KeyboardInterrupt:
        from .runs import load
        run = load(book, "last")
        click.echo(f"\ninterrupted: run {run.id} saved; continue with: bookgraph generate {target} --resume {run.id}", err=True)
        sys.exit(130)
    run = res.run
    counts = ", ".join(f"{n} {s}" for s, n in sorted(run.counts().items())) if run else ""
    click.echo(f"run {res.run_id}: {res.run_status} ({counts}; {run.spent if run else 0} model calls)"
               + (f": {res.run_reason}" if res.run_reason else ""))
    if res.run_status != "done":
        click.echo(f"continue with: bookgraph generate {target} --resume {res.run_id}", err=True)
    if res.catalog_error:
        click.echo(f"catalog not updated: {res.catalog_error}", err=True)
    if not res.ok:
        sys.exit(1)


@main.command()
@click.argument("target")
@click.pass_obj
@_guard
def runs(library: Path, target: str) -> None:
    """List a book's generate runs (newest last): status, variants done, model calls, and why an incomplete one stopped."""
    import time as _time
    from .runs import list_runs
    book = load_book(_find_book(target, library))
    for r in list_runs(book):
        c = r.counts()
        click.echo(f"{r.id}  {_time.strftime('%Y-%m-%d %H:%M', _time.localtime(r.started))}  {r.status:10} "
                   f"{c.get('done', 0)}/{len(r.variants)} done  {r.spent} calls" + (f"  {r.reason}" if r.reason else ""))


@main.command()
@click.argument("target", required=False)
@click.pass_obj
@_guard
def catalog(library: Path, target: str | None) -> None:
    """Refresh <library>/catalog.json: one book (book.yaml, folder or press/book), or every book in the library."""
    cat = register_book(library, load_book(_find_book(target, library))) if target else scan_library(library)
    books = sum(len(p["books"]) for p in cat["presses"])
    click.echo(f"{library / 'catalog.json'}: {len(cat['presses'])} press(es), {books} book(s)")


@main.command()
@click.argument("target")
@click.argument("domains", type=click.Path(file_okay=False))
@click.pass_obj
@_guard
def export(library: Path, target: str, domains: str) -> None:
    """Write a book (book.yaml, folder or press/book) into DOMAINS, a web app's public/domains: each chapter's graph, pages and
    navigator under its global id, and its entry in DOMAINS/catalog.json (other entries are kept)."""
    from .export import export_book
    entries = export_book(load_book(_find_book(target, library)), Path(domains).expanduser())
    for e in entries:
        click.echo(f"{e['id']}: {len(e['generated_concepts'])} concept page(s), {len(e['books'])} book page(s)"
                   f"{'' if e['has_navigator'] else ', no navigator'}")
    click.echo(f"{Path(domains) / 'catalog.json'}: {len(entries)} chapter(s) written")


@main.command()
@click.argument("target")
@click.option("--chapter", "-c", "chapters", multiple=True, help="only this chapter (repeatable)")
@click.option("--force", is_flag=True, help="rewrite every navigator, also ones that are up to date (e.g. migrated ones)")
@click.pass_obj
@_guard
def navigator(library: Path, target: str, chapters, force: bool) -> None:
    """Write each chapter's navigator, output/graph.html (the web apps' interactive concept graph), where it is missing or
    older than the chapter's graph.yaml; `export` does the same."""
    from .export import ensure_navigators
    book = load_book(_find_book(target, library))
    todo = [book.chapter(c) for c in chapters] if chapters else book.chapters
    written = ensure_navigators(book, todo, force=force)
    for p in written:
        click.echo(f"wrote {p}")
    click.echo(f"{len(written)} of {len(todo)} navigator(s) written")


@main.command()
@click.argument("target")
@click.option("--out", "-o", default="", help="the site folder (default: <book>/dist)")
@click.option("--stage", default="", help="where the app is assembled (default: ~/book-graph/build/<press>/<book>)")
@click.pass_obj
@_guard
def build(library: Path, target: str, out: str, stage: str) -> None:
    """Build a book (book.yaml, folder or press/book) as a static site: the one web app with the book's chapters, pages,
    navigators and catalog, and its `site:` title, base path and strings. Needs Node.js (npm)."""
    from .build import build_site, site_base
    book = load_book(_find_book(target, library))
    dest = build_site(book, out or book.root / "dist", stage=stage or None, log=lambda m: click.echo(m, err=True))
    click.echo(f"built {dest} (served at {site_base(book)}); preview: npx vite preview --outDir {dest} --base {site_base(book)}")


@main.command()
@click.argument("target")
@click.option("--dry-run", is_flag=True, help="build and check everything, push nothing")
@click.option("--yes", "-y", is_flag=True, help="do not ask before replacing the live site")
@click.option("--no-build", is_flag=True, help="publish the site already built in --out instead of building it first")
@click.option("--out", "-o", default="", help="the site folder (default: <book>/dist)")
@click.option("--message", "-m", default="", help="the commit message")
@click.pass_obj
@_guard
def publish(library: Path, target: str, dry_run: bool, yes: bool, no_build: bool, out: str, message: str) -> None:
    """Build a book's site and push it to book.yaml's `publish:` target (GitHub Pages: the gh-pages branch), as one fresh
    commit that replaces the branch. A policy (recipes/publish/policies/publish.cedar) refuses the push unless the site is
    built, the remote is reachable, the branch is not the remote's default and the base path matches where it is served."""
    from .build import build_site, site_base
    from .publish import publish_site, remote_of
    book = load_book(_find_book(target, library))
    remote, branch = remote_of(book), str(book.publish.get("branch") or "gh-pages")
    site = Path(out).expanduser() if out else book.root / "dist"
    if not no_build:
        build_site(book, site, log=lambda m: click.echo(m, err=True))
    if not dry_run and not yes:
        click.confirm(f"Replace {remote} {branch} with {site} (served at {site_base(book)})?", abort=True)
    res = publish_site(book, site, dry_run=dry_run, message=message)
    click.echo(res["summary"])


@main.command()
@click.argument("target")
@click.option("--port", "-p", type=int, default=8200, show_default=True, help="on 127.0.0.1 only")
@click.option("--no-build", is_flag=True, help="serve the app already built in --out instead of building it first")
@click.option("--out", "-o", default="", help="the built app (default: <book>/dist)")
@click.pass_obj
@_guard
def serve(library: Path, target: str, port: int, no_build: bool, out: str) -> None:
    """Serve a book's site on 127.0.0.1 with the local API behind Generate, Export PDF and Settings. Content is served live
    from the library, so generated pages appear at once. Settings (API keys included) live in memory only."""
    from .build import build_site, site_base
    from .serve import HOST, serve as run
    book = load_book(_find_book(target, library))
    site = Path(out).expanduser() if out else book.root / "dist"
    if not no_build:
        build_site(book, site, log=lambda m: click.echo(m, err=True))
    click.echo(f"serving {book.press}/{book.book} at http://{HOST}:{port}{site_base(book)}  (Ctrl-C to stop)")
    try:
        run(book, site, port=port)
    except KeyboardInterrupt:
        pass


@main.command()
@click.argument("target")
@click.option("--pdf", "pdf", required=True, type=click.Path(exists=True, dir_okay=False), help="the textbook PDF (with bookmarks)")
@click.option("--chapter", "number", type=int, default=None, help="the chapter number, as `--list` shows it")
@click.option("--id", "cid", default="", help="the chapter id in book.yaml (default: chNN)")
@click.option("--list", "list_only", is_flag=True, help="list the PDF's chapters and stop")
@click.option("--attribution", default=None, help="the source attribution (default: OpenStax, CC BY 4.0, as in press)")
@click.option("--force", is_flag=True, help="replace an existing chunks.yaml")
@click.pass_obj
@_guard
def ingest(library: Path, target: str, pdf: str, number, cid: str, list_only: bool, attribution, force: bool) -> None:
    """Read one chapter of a PDF textbook into the book: <chapter>/chunks.yaml, one chunk per section (concept-book-press's
    PDF ingester), and the chapter in book.yaml. Then `bookgraph extract`. Needs PyMuPDF: pip install "book-graph[ingest]"."""
    try:
        from . import ingest as ing
    except ImportError as e:
        raise click.ClickException(f"bookgraph ingest needs PyMuPDF ({e}): pip install \"book-graph[ingest]\"")
    if list_only:
        for num, title, page in ing.list_chapters(pdf):
            click.echo(f"{num:3d}  p.{page:<5d} {title}")
        return
    if number is None:
        raise click.UsageError("give --chapter N (see --list)")
    book = load_book(_find_book(target, library))
    cid = safe_segment(cid or f"ch{number:02d}", "chapter")
    dest = book.root / "chapters" / cid / "chunks.yaml"
    if dest.exists() and not force:
        raise click.ClickException(f"{dest} already exists: pass --force to read the chapter again")
    ing._log = lambda *a, **k: click.echo(" ".join(str(x) for x in a), err=True)
    try:
        content = ing.ingest_chapter(pdf, number, attribution=attribution)
    except ValueError as e:
        raise click.ClickException(str(e))
    content.save_yaml(dest)
    click.echo(f"wrote {dest}: {len(content.chunks)} section(s), {sum(len(c.text) for c in content.chunks)} characters")
    if any(c.id == cid for c in book.chapters):
        click.echo(f"chapter {cid} is already in book.yaml; next: bookgraph extract {target} -c {cid}")
        return
    from .book import add_chapter
    if add_chapter(book.root / "book.yaml", cid, content.chapter_title):
        click.echo(f"added chapter {cid} ({content.chapter_title}) to book.yaml; next: bookgraph extract {target} -c {cid}")
    else:
        click.echo(f"add this chapter to book.yaml's chapters:, then run bookgraph extract {target} -c {cid}\n"
                   f"  - {{id: {cid}, title: {json.dumps(content.chapter_title, ensure_ascii=False)}, graph: chapters/{cid}/graph.yaml}}")


@main.command()
@click.argument("target")
@click.option("--chapter", "-c", "chapter", required=True, help="the chapter (book.yaml id)")
@click.option("--chunks", default="", help="a chunks.yaml (default: <chapter>/chunks.yaml, written by `bookgraph ingest`)")
@click.option("--llm", "model", default="", help="ADAPTER:MODEL (default: the book's first model)")
@click.option("--force", is_flag=True, help="replace an existing graph.yaml")
@click.option("--max-llm-calls", type=int, default=10, show_default=True)
@click.pass_obj
@_guard
def extract(library: Path, target: str, chapter: str, chunks: str, model: str, force: bool, max_llm_calls: int) -> None:
    """Extract a chapter's concept graph from its chunks (concept-book-press's two passes): normalized, validated, repaired
    once if needed, written as the chapter's graph.yaml (and concept_sources.yaml)."""
    from .builder import extract_chapter
    book = load_book(_find_book(target, library))
    res = extract_chapter(book, book.chapter(chapter), chunks=Path(chunks) if chunks else None, model=model, force=force,
                          max_llm_calls=max_llm_calls)
    if not res.ok:
        raise click.ClickException(res.error + (f" ({res.llm_calls} model calls)" if res.llm_calls else ""))
    click.echo(f"{res.summary} ({res.llm_calls} model calls) -> {res.graph}")


@main.command()
@click.argument("repo", type=click.Path(exists=True, file_okay=False))
@click.option("--press", default="digital-duck", show_default=True)
@click.option("--book", "slug", default="", help="the book slug (default: the repo name without `cb-`)")
@click.option("--title", default="", help="the book title (default: the slug, title-cased)")
@click.option("--adapter", default="claude_cli", show_default=True, help="the LLM adapter the repo's model folders were made with")
@click.pass_obj
@_guard
def migrate(library: Path, repo: str, press: str, slug: str, title: str, adapter: str) -> None:
    """Import a concept-book app repo (cb-calculus, ...) into the library: book.yaml, chapter graphs, generated pages and
    navigators, and its spl3 cache as `legacy_cache:`. The repo is only read."""
    from .export import migrate_repo
    b = migrate_repo(repo, library, press=press, book=slug, title=title, adapter=adapter)
    click.echo(f"created {b.root / 'book.yaml'}: {len(b.chapters)} chapter(s), levels {', '.join(b.levels)}, "
               f"languages {', '.join(b.languages)}, models {', '.join(b.models) or '-'}"
               f"{', legacy cache ' + b.legacy_cache if b.legacy_cache else ''}")


def _step_target(book, step: str, chapter: str):
    from .pipeline import BOOK_STEPS, CHAPTER_STEPS, STEPS
    if step not in STEPS:
        raise click.UsageError(f"STEP is one of {', '.join(STEPS)}")
    if step in BOOK_STEPS:
        if chapter:
            raise click.UsageError(f"{step} is a book step: give no -c")
        return [None]
    if not chapter:
        raise click.UsageError(f"{step} is a chapter step: give -c CHAPTER (or -c all)")
    return list(book.chapters) if chapter == "all" else [book.chapter(chapter)]


@main.command(name="next")
@click.argument("target")
@click.option("--parallel", type=int, default=2, show_default=True, help="at most this many steps running at once")
@click.option("--foreground", is_flag=True, help="run the ready steps here, one after another, instead of in the background")
@click.option("--dry-run", is_flag=True, help="only say what would start")
@click.option("--watch", type=int, default=0, metavar="SECONDS", help="keep dispatching every SECONDS until stopped")
@click.pass_obj
@_guard
def next_(library: Path, target: str, parallel: int, foreground: bool, dry_run: bool, watch: int) -> None:
    """Start every pipeline step that is ready: the step before it is OK and approved, and its own manifest is missing,
    stale (its inputs changed), rejected or marked for retry. Each runs in the background and writes
    <book>/pipeline/[<chapter>/]<step>.out.yaml; see `bookgraph status`."""
    import time as _time
    from .pipeline import dispatch, log_path
    root = _find_book(target, library)
    while True:
        book = load_book(root)
        started = dispatch(book, library, parallel=parallel, foreground=foreground, dry_run=dry_run)
        for t in started:
            where = "" if foreground or dry_run else f" (log: {log_path(book, t.step, t.chapter)})"
            click.echo(f"{'would start' if dry_run else 'ran' if foreground else 'started'} {t.name}{where}")
        if not watch:
            if not started:
                click.echo("nothing ready: see `bookgraph status` (a step waits for the one before it to be OK and approved)")
            return
        _time.sleep(watch)


@main.command()
@click.argument("target")
@click.option("--auto-approve-on-success/--no-auto-approve", default=True, show_default=True,
              help="approve each step that ends OK, so the next one starts at once; --no-auto-approve stops at the first step that needs review")
@click.option("--until", "until", default=None, metavar="STEP", help="stop after this step (1-ingest .. 6-publish)")
@click.option("--chapter", "-c", "chapters", multiple=True, help="only these chapters' steps (repeatable)")
@click.option("--llm", "model", default="", help="ADAPTER:MODEL for the model steps (default: the book's first model)")
@click.pass_obj
@_guard
def pipeline(library: Path, target: str, auto_approve_on_success: bool, until: str | None, chapters: tuple, model: str) -> None:
    """Run a book's whole pipeline here, step after step, until nothing more is ready: the synchronous `bookgraph next`.

    Each step that ends OK is approved on the spot (--no-auto-approve to review each one yourself: it stops at the first).
    A step that fails stops what depends on it; fix it and run again, finished steps are not repeated. Model steps (2-extract,
    4-generate) are billed, and nobody reviews them in between: use --until 3-validate to look at the graph first."""
    from .pipeline import run_pipeline
    root = _find_book(target, library)
    res = run_pipeline(root, auto_approve=auto_approve_on_success, until=until, chapters=list(chapters) or None,
                       llm=model or None, log=click.echo)
    click.echo(f"{len(res['ran'])} step(s) run; {res['stopped']}")
    if not res["ok"]:
        sys.exit(1)


@main.command()
@click.argument("target")
@click.pass_obj
@_guard
def status(library: Path, target: str) -> None:
    """The pipeline of a book: each chapter's steps (1-ingest .. 4-generate), then 5-build and 6-publish, with what is ready
    and what waits for review."""
    from .pipeline import status_rows
    book = load_book(_find_book(target, library))
    for r in status_rows(book):
        t, m = r["task"], r["manifest"]
        if m:
            review = (m.get("review") or {}).get("status", "")
            state = m["status"] + (f", review {review}" if m["status"] != "RUNNING" and review else "")
            line = (m.get("summary") or m.get("error") or "")[:90]
        else:
            state, line = "-", ""
        flag = "READY  " if r["ready"] else "       "
        click.echo(f"{flag}{t.chapter.id if t.chapter else 'book':10} {t.step:11} {state:26} {r['why']:28} {line}")


def _review(library: Path, target: str, step: str, chapter: str, decision: str, note: str, by: str) -> None:
    from .pipeline import review
    book = load_book(_find_book(target, library))
    for ch in _step_target(book, step, chapter):
        m = review(book, step, ch, decision, by=by, note=note)
        click.echo(f"{step}{' ' + ch.id if ch else ''}: {'marked for retry' if decision == 'retry' else m['review']['status']}")


@main.command()
@click.argument("target")
@click.argument("step")
@click.option("--chapter", "-c", default="", help="the chapter, or `all` (chapter steps)")
@click.option("--note", "-m", default="")
@click.option("--by", default="", help="the reviewer (default: your user name)")
@click.pass_obj
@_guard
def approve(library: Path, target: str, step: str, chapter: str, note: str, by: str) -> None:
    """Approve a step's last run (it must be OK): the next step may then start."""
    _review(library, target, step, chapter, "approve", note, by)


@main.command()
@click.argument("target")
@click.argument("step")
@click.option("--chapter", "-c", default="", help="the chapter, or `all` (chapter steps)")
@click.option("--note", "-m", default="", help="why: kept in the manifest")
@click.option("--by", default="", help="the reviewer (default: your user name)")
@click.pass_obj
@_guard
def reject(library: Path, target: str, step: str, chapter: str, note: str, by: str) -> None:
    """Reject a step's last run: `bookgraph next` runs it again (2-extract extracts again, 4-generate regenerates)."""
    _review(library, target, step, chapter, "reject", note, by)


@main.command()
@click.argument("target")
@click.argument("step")
@click.option("--chapter", "-c", default="", help="the chapter, or `all` (chapter steps)")
@click.pass_obj
@_guard
def retry(library: Path, target: str, step: str, chapter: str) -> None:
    """Mark a NOT_OK step to run again as it is (e.g. after a rate limit); a changed input makes it ready by itself."""
    _review(library, target, step, chapter, "retry", "", "")


@main.command(name="pipeline-step", hidden=True)
@click.argument("book_dir")
@click.argument("step")
@click.option("--chapter", "-c", default="")
@click.pass_obj
@_guard
def pipeline_step(library: Path, book_dir: str, step: str, chapter: str) -> None:
    """(internal) Run one pipeline step and write its manifest: what `bookgraph next` starts in the background."""
    from .pipeline import run_step
    book = load_book(book_dir)
    m = run_step(book, step, book.chapter(chapter) if chapter else None)
    click.echo(f"{m['status']}: {m.get('summary', '')}")
    if m["status"] != "OK":
        sys.exit(1)


@main.command()
@click.option("--port", "-p", type=int, default=8501, show_default=True, help="the Streamlit port")
@click.argument("streamlit_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_obj
@_guard
def ui(library: Path, port: int, streamlit_args: tuple) -> None:
    """Open the pipeline as a web app (Streamlit): run each step, review what it made, approve or reject it, and preview the
    built site. The same manifests as `bookgraph status`/`approve`."""
    import importlib.util
    import os
    if importlib.util.find_spec("streamlit") is None:
        raise click.ClickException("Streamlit is not installed: pip install -e . (it is a dependency of book-graph)")
    from . import ui as ui_module
    env = dict(os.environ, BOOKGRAPH_LIBRARY=str(library))
    os.execvpe(sys.executable, [sys.executable, "-m", "streamlit", "run", ui_module.__file__, "--server.port", str(port),
                                "--server.address", "127.0.0.1", "--browser.gatherUsageStats", "false", *streamlit_args], env)


@main.command(name="list")
@click.pass_obj
@_guard
def list_(library: Path) -> None:
    """List the presses, books and chapters of the library's catalog."""
    from .catalog import read_catalog
    for press in read_catalog(library)["presses"]:
        click.echo(press["press"])
        for b in press["books"]:
            click.echo(f"  {b['book']}  {b['title']}  ({len(b['chapters'])} chapters)")


if __name__ == "__main__":
    main()
