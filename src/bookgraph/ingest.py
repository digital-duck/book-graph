"""`bookgraph ingest`: a chapter of a PDF textbook -> its chunks.yaml (one chunk per section), the input of `extract`.

concept-book-press's PDF ingester (pipeline/ingest/pdf.py), verbatim but for its progress output, which goes through
`_log` (silent unless the CLI sets it): chapters and sections from the PDF's bookmarks (level 1 and 2), text per section
with page numbers and running headers removed, and a source attribution. OpenStax books' PDFs work as they did in press's
publish pipeline. press's OpenStax-HTML ingester (it fetched openstax.org pages) is not ported: its PDFs are the same books.

Needs PyMuPDF (`pip install "book-graph[ingest]"`), imported only here. PyMuPDF is AGPL-3.0; book-graph is Apache-2.0,
so it is an optional extra, as in press, not a dependency.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf
import yaml


def _log(*args, **kwargs) -> None:
    """Progress lines (press printed them); the CLI points this at click.echo."""


@dataclass
class Chunk:
    section_id: str
    section_title: str
    text: str
    page_start: int = 0
    page_end: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v}


@dataclass
class ChapterContent:
    source_pdf: str
    chapter_number: int
    chapter_title: str
    chunks: list[Chunk]
    source_attribution: str = ""

    def to_dict(self) -> dict:
        return {
            "source_pdf": self.source_pdf,
            "source_attribution": self.source_attribution,
            "chapter_number": self.chapter_number,
            "chapter_title": self.chapter_title,
            "chunks": [c.to_dict() for c in self.chunks],
        }

    def save_yaml(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False,
                      allow_unicode=True, width=120)
        _log(f"  Saved {len(self.chunks)} chunks to {path}")


def _extract_pages_text(doc: pymupdf.Document, page_start: int, page_end: int) -> str:
    """Extract and join text from a range of pages (0-indexed, exclusive end)."""
    texts = []
    for i in range(page_start, min(page_end, len(doc))):
        page = doc[i]
        texts.append(page.get_text())
    return "\n".join(texts)


def _clean_text(text: str) -> str:
    """Clean extracted PDF text: fix line breaks, remove page headers/footers."""
    # Remove common header/footer patterns (page numbers, chapter titles repeated)
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip isolated page numbers
        if re.match(r'^\d{1,4}$', stripped):
            continue
        # Skip very short lines that look like headers (e.g. "Chapter 1")
        if re.match(r'^Chapter \d+$', stripped):
            continue
        # Skip "Access for free" footers
        if 'Access for free' in stripped:
            continue
        cleaned.append(line)
    text = "\n".join(cleaned)
    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _build_section_id(chapter: int, section_title: str) -> str:
    """Build a section_id from chapter number and title."""
    m = re.match(r'^(\d+\.\d+)\s+', section_title)
    if m:
        return f"ch{chapter:02d}_s{m.group(1).replace('.', '_')}"
    slug = re.sub(r'[^a-z0-9]+', '_', section_title.lower()).strip('_')[:40]
    return f"ch{chapter:02d}_{slug}"


def get_toc(pdf_path: str | Path) -> list[tuple[int, str, int]]:
    """Return the PDF's table of contents as [(level, title, page), ...]."""
    doc = pymupdf.open(str(pdf_path))
    toc = doc.get_toc()
    doc.close()
    return toc


# Front/back-matter titles excluded from positional chapter numbering when a
# PDF's level-1 bookmarks have no "Chapter N" prefix to key off of (case-
# insensitive, matched as a whole title). Covers common textbook conventions,
# not just Beezer's FCLA -- keeps the positional fallback usable for other
# non-OpenStax-style PDFs too.
_NON_CHAPTER_L1_TITLES = {
    'preface', 'acknowledgements', 'acknowledgments', 'foreword',
    'introduction', 'preliminaries', 'appendix', 'appendices',
    'reference', 'references', 'index', 'glossary', 'bibliography',
    'colophon', 'about the author', 'dedication',
}


def _number_l1_entries(entries: list[tuple[str, int]]) -> list[int]:
    """Resolve a chapter number for each level-1 (title, page) entry, in order.

    Prefers an explicit "Chapter N" prefix (OpenStax convention) wherever any
    entry uses it. Otherwise falls back to 1-based position among entries
    whose title isn't recognized front/back matter (e.g. Beezer's FCLA, whose
    chapter titles are plain -- "Vectors", "Matrices" -- with no "Chapter N"
    prefix, but whose front matter -- "Preface", "Acknowledgements" -- must
    not shift the numbering) -- so a well-bookmarked PDF without OpenStax-
    style numbering still ingests with the same chapter numbers a human
    reader of that book would use. Entries this can't confidently number
    (an unmatched non-numbered entry in a mixed TOC) resolve to -1 (skip).
    """
    uses_chapter_prefix = any(re.match(r'Chapter\s+(\d+)', title) for title, _ in entries)
    numbers = []
    position = 0
    for title, _page in entries:
        m = re.match(r'Chapter\s+(\d+)', title)
        if m:
            numbers.append(int(m.group(1)))
        elif uses_chapter_prefix:
            # Mixed TOC (e.g. an Appendix after numbered chapters) -- skip,
            # matching prior OpenStax-only behavior.
            numbers.append(-1)
        elif title.strip().lower() in _NON_CHAPTER_L1_TITLES:
            numbers.append(-1)
        else:
            position += 1
            numbers.append(position)
    return numbers


def list_chapters(pdf_path: str | Path) -> list[tuple[int, str, int]]:
    """List all chapters found in the PDF TOC."""
    toc = get_toc(pdf_path)
    l1 = [(title, page) for level, title, page in toc if level == 1]
    nums = _number_l1_entries(l1)
    return [(num, title, page) for (title, page), num in zip(l1, nums) if num > 0]


def ingest_chapter(
    pdf_path: str | Path,
    chapter: int,
    attribution: str | None = None,
) -> ChapterContent:
    """Extract all sections of a chapter from a PDF textbook."""
    pdf_path = Path(pdf_path)
    doc = pymupdf.open(str(pdf_path))
    toc = doc.get_toc()

    # Find chapter boundaries and sections from TOC
    chapter_title = ""
    chapter_start_page = -1
    chapter_end_page = len(doc)
    sections: list[tuple[str, int, int]] = []  # (title, start_page, end_page)

    # Collect all L1 entries (title, page) and resolve each to a chapter number
    # via the same convention-detecting helper list_chapters() uses, so
    # --chapter N means the same thing in both commands regardless of whether
    # the PDF uses OpenStax-style "Chapter N" titles or plain titles (Beezer).
    l1 = [(title, page) for level, title, page in toc if level == 1]
    l1_nums = _number_l1_entries(l1)

    for i, (title, page) in enumerate(l1):
        num = l1_nums[i]
        if num == chapter:
            chapter_title = title
            chapter_start_page = page - 1  # 0-indexed
        elif chapter_start_page >= 0:
            # First L1 entry after our chapter starts (numbered or not,
            # e.g. an Appendix) marks the end boundary.
            chapter_end_page = page - 1
            break

    if chapter_start_page < 0:
        doc.close()
        raise ValueError(f"Chapter {chapter} not found in PDF TOC")

    # Find L2 sections within the chapter
    in_chapter = False
    section_entries: list[tuple[str, int]] = []
    skip_sections = {'Chapter Outline', 'Glossary', 'Section Summary',
                     'Conceptual Questions', 'Problems & Exercises',
                     'Problems &amp; Exercises', 'Test Prep'}

    for level, title, page in toc:
        if level == 1 and title == chapter_title:
            in_chapter = True
            continue
        if level == 1 and in_chapter:
            break
        if level == 2 and in_chapter:
            if title in skip_sections:
                continue
            if title.startswith('Chapter Outline') or title.startswith('Introduction'):
                continue
            section_entries.append((title, page - 1))

    # Convert to (title, start_page, end_page) using next section as boundary
    for i, (title, start) in enumerate(section_entries):
        end = section_entries[i + 1][1] if i + 1 < len(section_entries) else chapter_end_page
        sections.append((title, start, end))

    _log(f"Ingesting {pdf_path.name} chapter {chapter}: {chapter_title}")
    _log(f"  Pages {chapter_start_page + 1}-{chapter_end_page}, {len(sections)} sections")

    chunks = []
    for title, start, end in sections:
        text = _extract_pages_text(doc, start, end)
        text = _clean_text(text)
        if not text.strip():
            continue
        chunk = Chunk(
            section_id=_build_section_id(chapter, title),
            section_title=title,
            text=text,
            page_start=start + 1,
            page_end=end,
        )
        chunks.append(chunk)
        _log(f"  {title}: pages {start + 1}-{end} ({len(text)} chars)")

    doc.close()

    if attribution is None:
        # Default assumes OpenStax provenance (this pipeline's original and
        # most common source); pass --attribution explicitly for any other
        # source PDF (e.g. Beezer's FCLA is GNU FDL, not CC BY 4.0 / OpenStax)
        # so the generated graph.yaml doesn't carry a false license claim.
        source_name = pdf_path.stem.replace('-', ' ').replace('_', ' ').title()
        attribution = (
            f"Concept graph extracted from: {source_name}. "
            f"Original textbook by OpenStax, licensed under CC BY 4.0. "
            f"This concept graph is a companion learning aid, not a reproduction. "
            f"Download the full textbook free at openstax.org."
        )

    return ChapterContent(
        source_pdf=pdf_path.name,
        chapter_number=chapter,
        chapter_title=chapter_title,
        chunks=chunks,
        source_attribution=attribution,
    )
