"""generate_chapter() / generate_book(): inputs, call cap and a result the CLI prints, over the generate_chapter recipe."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .book import Book, Chapter, model_dir_name
from .catalog import register_book
from .exceptions import BookError
from .graph import load_graph
from .levels import resolve_style
from .paths import variant_dir
from .run import RecipeError, run_recipe
from .steps import write_roots_from_env


@dataclass
class ChapterResult:
    chapter: str
    level: str
    language: str
    model: str
    ok: bool
    book: str = ""
    manifest: str = ""
    llm_calls: int = 0
    log_path: str = ""
    error: str = ""


@dataclass
class BookResult:
    chapters: list[ChapterResult] = field(default_factory=list)
    catalog_error: str = ""

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.chapters) and not self.catalog_error


def generate_chapter(book: Book, chapter: Chapter, *, level: str, language: str, model: str = "", target: str = "",
                     skip_cache: bool = False, primitive_budget: int = 2, llm: Any = None, max_llm_calls: Optional[int] = 200,
                     max_steps: int = 5000, log_dir: Optional[Path] = None, checkpoint: Optional[Path] = None, resume: Optional[Path] = None,
                     on_node: Optional[Callable[[dict], None]] = None) -> ChapterResult:
    """Generate one chapter at one level, language and model. `model` is an `ADAPTER[:MODEL]` string (or empty with `llm` an object)."""
    folder = model_dir_name(model) if isinstance(model, str) else ""
    res = ChapterResult(chapter.id, level, language, folder, ok=False)
    try:
        graph = book.graph_path(chapter)
        load_graph(graph)                                # a missing or unreadable graph is this chapter's failure, not the book's
        root = book.chapter_root(chapter).resolve()
        out = variant_dir(root, level, language, folder).resolve()
        style = resolve_style(level, book.tags)
    except (BookError, ValueError) as e:
        res.error = str(e)
        return res
    inputs = {"chapter_graph": str(graph), "target": target or chapter.capstone, "style": style,
              "level": level, "language": language, "model": model if isinstance(model, str) else "", "chapter_title": chapter.title,
              "output_dir": str(out), "cache_dir": str(root / ".bg_cache"), "skip_cache": skip_cache,
              "primitive_budget": primitive_budget, "subject": f"{book.book} {' '.join(book.tags)}".strip(),
              "write_roots": write_roots_from_env(), "legacy_cache": book.legacy_cache}
    kw: dict[str, Any] = {}
    if log_dir is not None:
        kw["log_dir"] = log_dir
    try:
        run = run_recipe("generate_chapter", inputs, llm=llm if llm is not None else (model or "claude_cli"), max_llm_calls=max_llm_calls,
                         max_steps=max_steps, checkpoint=checkpoint, resume=resume, on_node=on_node, **kw)
    except RecipeError as e:
        res.error = str(e)
        return res
    res.llm_calls, res.log_path = run.llm_calls, str(run.log_path or "")
    if run.exit:
        res.error = f"exit {run.exit}: {run.outputs.get('book', '')}"
        return res
    res.ok, res.book, res.manifest = True, run.outputs.get("book", ""), run.outputs.get("manifest", "")
    return res


def generate_book(book: Book, library: Path, *, chapters: Optional[list[str]] = None, levels: Optional[list[str]] = None,
                  languages: Optional[list[str]] = None, models: Optional[list[str]] = None, llm: Any = None, **kw: Any) -> BookResult:
    """Every chapter x level x language x model the book lists (or the subset asked for); the catalog is refreshed at the end."""
    todo = [book.chapter(c) for c in chapters] if chapters else book.chapters
    out = BookResult()
    for model in (models if models is not None else book.models) or [""]:
        if not model and llm is None:
            raise BookError("no model: list one under 'models:' in book.yaml or pass --llm ADAPTER:MODEL")
    try:
        for ch in todo:
            for level in levels or book.levels:
                for lang in languages or book.languages:
                    for model in (models if models is not None else book.models) or [""]:
                        out.chapters.append(generate_chapter(book, ch, level=level, language=lang, model=model, llm=llm, **kw))
    finally:                                              # what was generated is listed even if a later variant raised
        try:
            register_book(library, book)
        except BookError as e:
            out.catalog_error = str(e)
    return out
