"""`bookgraph` (alias `bgr`): the developer and publisher command line."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from . import __version__
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


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="bookgraph")
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
@click.pass_obj
@_guard
def validate(library: Path, target: str, fmt: str) -> None:
    """Check a graph.yaml, or every chapter graph of a book (book.yaml, folder or press/book). Exit 1 on any problem."""
    p = Path(target).expanduser()
    results: list[dict] = []
    if p.is_file() and p.name != "book.yaml":
        paths = [("", p)]
    else:
        book = load_book(_find_book(target, library))
        paths = [(c.id, book.graph_path(c)) for c in book.chapters]
    for name, gp in paths:
        try:
            problems = load_graph(gp).validate()
        except BookError as e:
            problems = [str(e)]
        results.append({"chapter": name or str(gp), "ok": not problems, "problems": problems})
    if fmt == "json":
        click.echo(json.dumps(results, indent=1))
    else:
        for r in results:
            click.echo(f"{'ok  ' if r['ok'] else 'FAIL'} {r['chapter']}" + ("" if r["ok"] else ": " + "; ".join(r["problems"])))
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
@click.option("--skip-cache", is_flag=True, help="regenerate every section")
@click.option("--max-llm-calls", type=int, default=200, show_default=True,
              help="the cap for EACH chapter x level x language x model run, not for the whole book")
@click.pass_obj
@_guard
def generate(library: Path, target: str, chapters, levels, languages, models, node: str, skip_cache: bool, max_llm_calls: int) -> None:
    """Generate a book's pages (book.yaml, folder or press/book): every chapter x level x language x model, or the subset asked."""
    book = load_book(_find_book(target, library))
    if node and not chapters:
        raise click.UsageError("--target-node names a node of one chapter: also give -c CHAPTER")
    res = generate_book(book, library, chapters=list(chapters) or None, levels=list(levels) or None, languages=list(languages) or None,
                        models=list(models) or None, target=node, skip_cache=skip_cache, max_llm_calls=max_llm_calls)
    for c in res.chapters:
        tail = c.book if c.ok else f"FAILED: {c.error}"
        click.echo(f"{'ok  ' if c.ok else 'FAIL'} {c.chapter} {c.level}.{c.language} {c.model} ({c.llm_calls} model calls) {tail}")
    if res.catalog_error:
        click.echo(f"catalog not updated: {res.catalog_error}", err=True)
    if not res.ok:
        sys.exit(1)


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
