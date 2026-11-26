"""`bookgraph ingest`: a chapter of a PDF textbook -> its chunks.yaml (one chunk per section), the input of `extract`.

The reading itself lives in dd-extract (`dd_extract.chapters`: chapters and sections from the PDF's bookmarks, page numbers and running
headers removed), where any project can use it; it began as concept-book-press's PDF ingester. What stays here is book-graph's own default:
the source attribution, which assumes an OpenStax book unless `--attribution` says otherwise.

Needs `pip install "book-graph[ingest]"` (dd-extract with its PyMuPDF extra; PyMuPDF is AGPL-3.0 and book-graph is Apache-2.0, so it
stays an optional extra), imported only here.
"""
from __future__ import annotations

from pathlib import Path

from dd_extract.chapters import ChapterContent, Chunk, list_chapters  # noqa: F401  (re-exported)
from dd_extract.chapters import extract_chapter


def _log(*args, **kwargs) -> None:
    """Progress lines; the CLI points this at click.echo."""


def _openstax_attribution(pdf_path: Path) -> str:
    source_name = pdf_path.stem.replace("-", " ").replace("_", " ").title()
    return (
        f"Concept graph extracted from: {source_name}. "
        f"Original textbook by OpenStax, licensed under CC BY 4.0. "
        f"This concept graph is a companion learning aid, not a reproduction. "
        f"Download the full textbook free at openstax.org."
    )


def ingest_chapter(pdf_path: str | Path, chapter: int, attribution: str | None = None) -> ChapterContent:
    """The chapter's sections as chunks. Without `attribution` the text assumes an OpenStax source (CC BY 4.0); pass one for any other book."""
    pdf_path = Path(pdf_path)
    return extract_chapter(pdf_path, chapter, attribution=attribution if attribution is not None else _openstax_attribution(pdf_path),
                           log=lambda *a, **k: _log(*a, **k))
