"""Repair Markdown pipe tables that reach a generated page as flattened text.

`render._md_to_html` has no table support, so a model's `| A | B |` / `|---|---|` / `| 1 | 2 |` lines are joined into one
paragraph: `<p>| A | B | |---|---| | 1 | 2 |</p>`.  `repair_tables` turns those into a real `<table>` (any number of columns; `:---:`
alignment kept), and is applied to the output of `_md_to_html`, so every new page is repaired as it is written.

`concept-book/scripts/repair-html-tables.py` is a thin CLI over `repair_with_report`, for repairing pages already written; the logic
lives only here.  A table whose rows do not match the header's column count is left unchanged, never guessed at.
"""
from __future__ import annotations

import re

# A separator run: |---|---| / | :---: | ---: | …  (one or more dashed cells)
SEP_RUN = re.compile(r"\|(?:\s*:?-{3,}:?\s*\|)+")
SEP_CELL = re.compile(r"^\s*(:?)-{3,}(:?)\s*$")
P_BLOCK = re.compile(r"<p>(.*?)</p>", re.S)
PROTECTED = re.compile(r"<(pre|code)\b.*?</\1>", re.S | re.I)

TABLE_STYLE = "border-collapse: collapse; border: 1px solid #333;"
CELL_STYLE = "border: 1px solid #333; padding: 6px 12px;"


def _align(spec: str) -> str | None:
    m = SEP_CELL.match(spec)
    if not m:
        return None
    left, right = m.group(1), m.group(2)
    if left and right:
        return "center"
    if right:
        return "right"
    if left:
        return "left"
    return None


def _build_table(header: list[str], aligns: list[str | None], rows: list[list[str]], styled: bool) -> str:
    ts = f' style="{TABLE_STYLE}"' if styled else ""

    def cell(tag: str, text: str, al: str | None) -> str:
        st = CELL_STYLE + (f" text-align: {al};" if al else "") if styled else (f"text-align: {al};" if al else "")
        attr = f' style="{st}"' if st else ""
        return f"      <{tag}{attr}>{text.strip()}</{tag}>"

    out = [f"<table{ts}>", "  <thead>", "    <tr>"]
    out += [cell("th", h, a) for h, a in zip(header, aligns)]
    out += ["    </tr>", "  </thead>", "  <tbody>"]
    for r in rows:
        out.append("    <tr>")
        out += [cell("td", c, a) for c, a in zip(r, aligns)]
        out.append("    </tr>")
    out += ["  </tbody>", "</table>"]
    return "\n".join(out)


def _split_cells(row_text: str) -> list[str]:
    """'| a | b |' -> ['a', 'b'] (outer pipes optional)."""
    t = row_text.strip()
    if t.startswith("|"):
        t = t[1:]
    if t.endswith("|"):
        t = t[:-1]
    return [c.strip() for c in t.split("|")]


def _flat_table(inner: str, styled: bool) -> tuple[str, str] | None:
    """Convert a one-paragraph flattened table. Returns (html, note) or None."""
    m = SEP_RUN.search(inner)
    if not m:
        return None
    sep_cells = [c for c in m.group(0).split("|")[1:-1]]
    ncols = len(sep_cells)
    aligns = [_align(c) for c in sep_cells]

    # Header: the ncols+1 pipes immediately before the separator run.
    before = inner[: m.start()].rstrip()
    if not before.endswith("|"):
        return None
    idx = len(before)
    for _ in range(ncols + 1):
        idx = before.rfind("|", 0, idx)
        if idx == -1:
            return None
    prefix, header_text = before[:idx].strip(), before[idx:]
    header = _split_cells(header_text)
    if len(header) != ncols:
        return None

    # Body: after the separator, ncols cells per row; rows are joined by '| |'.
    tokens = inner[m.end():].split("|")
    # tokens[0] is whitespace before the first row's opening pipe
    # (the separator's closing pipe was consumed by SEP_RUN, so the row's own
    # opening pipe is tokens[0]/tokens[1] boundary).
    toks = tokens[1:] if tokens and tokens[0].strip() == "" else None
    if toks is None:
        return None
    rows: list[list[str]] = []
    suffix = ""
    i = 0
    while i < len(toks):
        remaining = toks[i:]
        if len(remaining) < ncols + 1:
            suffix = "|".join(remaining).strip()
            break
        cells = [c.strip() for c in remaining[:ncols]]
        boundary = remaining[ncols]
        rows.append(cells)
        i += ncols + 1
        if boundary.strip() != "":  # text after the last row's closing pipe
            suffix = boundary.strip()
            break
    if not rows or any(len(r) != ncols for r in rows):
        return None
    if suffix and "|" in suffix:
        return None

    parts = []
    if prefix:
        parts.append(f"<p>{prefix}</p>")
    parts.append(_build_table(header, aligns, rows, styled))
    if suffix:
        parts.append(f"<p>{suffix}</p>")
    return "\n".join(parts), f"{ncols} cols x {len(rows)} rows"


def _row_paragraph(line: str) -> str | None:
    m = re.fullmatch(r"\s*<p>\s*(\|.*\|)\s*</p>\s*", line)
    return m.group(1) if m else None


def _fix_row_paragraphs(text: str, styled: bool, report: list[str]) -> str:
    """Form 2: consecutive one-row-per-<p> lines."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = i
        block = []
        while j < len(lines) and _row_paragraph(lines[j]) is not None:
            block.append(_row_paragraph(lines[j]))
            j += 1
        if len(block) >= 2 and SEP_RUN.fullmatch(block[1].strip()):
            header = _split_cells(block[0])
            sep = [c for c in block[1].strip().split("|")[1:-1]]
            rows = [_split_cells(b) for b in block[2:]]
            if len(sep) == len(header) and rows and all(len(r) == len(header) for r in rows):
                out.append(_build_table(header, [_align(c) for c in sep], rows, styled))
                report.append(f"row-per-<p> table ({len(header)} cols x {len(rows)} rows)")
                i = j
                continue
            report.append("SKIPPED row-per-<p> table: ragged rows")
        if block:
            out.extend(lines[i:j])
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def repair_with_report(text: str, styled: bool = True) -> tuple[str, list[str]]:
    report: list[str] = []

    # Hide <pre>/<code> so their content is never touched.
    stash: list[str] = []

    def hide(m: re.Match) -> str:
        stash.append(m.group(0))
        return f"\x00{len(stash) - 1}\x00"

    work = PROTECTED.sub(hide, text)

    def fix_p(m: re.Match) -> str:
        inner = m.group(1)
        if not SEP_RUN.search(inner):
            return m.group(0)
        res = _flat_table(inner, styled)
        if res is None:
            # leave one-row-per-<p> separator paragraphs for the second pass
            if not _row_paragraph(m.group(0)):
                report.append("SKIPPED malformed table (left unchanged): " + inner[:60].replace("\n", " "))
            return m.group(0)
        html, note = res
        report.append(f"flattened table ({note})")
        return html

    work = P_BLOCK.sub(fix_p, work)
    work = _fix_row_paragraphs(work, styled, report)
    work = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], work)
    return work, report




def repair_tables(html: str, styled: bool = True) -> str:
    """`html` with every flattened pipe table replaced by a `<table>`; unchanged when it has none."""
    if "---" not in html or "|" not in html:
        return html
    return repair_with_report(html, styled)[0]
