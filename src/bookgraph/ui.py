"""`bookgraph ui`: the pipeline as a Streamlit app, for one book at a time. Run it with `bgr ui` (it starts Streamlit on this file).

Pipeline   every step of every chapter with its state, and the buttons that run the ready ones
Review     one step: what it made (sections, graph, pages), its log and manifest, and Approve / Reject / Retry
Preview    the built site, served locally and embedded, once 5-build is OK (before you approve it)
Library    the catalog

The same manifests and review gates as the command line (bookgraph.pipeline): a step started here shows in `bgr status`, and the
other way round. Model steps are billed; "Run all" approves each step that ends OK, so use the Review tab to look in between."""
from __future__ import annotations

import os
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
import yaml

from bookgraph import pipeline as pl
from bookgraph import uisupport as ui
from bookgraph.book import load_book
from bookgraph.catalog import read_catalog
from bookgraph.exceptions import BookError
from bookgraph.paths import default_library

st.set_page_config(page_title="book-graph", page_icon="📚", layout="wide")

ICON = {"OK": "🟢", "NOT_OK": "🔴", "RUNNING": "🟡", "-": "⚪"}


def _state(m: dict | None) -> str:
    return (m or {}).get("status", "-")


def _review_label(m: dict | None) -> str:
    return ((m or {}).get("review") or {}).get("status", "") if m and m.get("status") != "RUNNING" else ""


def _table(rows: list[dict]) -> None:
    """A Markdown table. (st.dataframe needs pyarrow, which breaks on a NumPy it was not built for; this needs nothing.)"""
    if not rows:
        st.caption("(none)")
        return
    cell = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    head = list(rows[0])
    st.markdown("\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                          + ["| " + " | ".join(cell(r.get(h, "")) for h in head) + " |" for r in rows]))


def _flash() -> None:
    msg = st.session_state.pop("flash", None)
    if msg:
        (st.error if msg[0] == "error" else st.success)(msg[1])


def _do(fn, *args, ok: str = "", **kw):
    """Run a pipeline action; show its BookError instead of a traceback; rerun to redraw."""
    try:
        res = fn(*args, **kw)
        st.session_state["flash"] = ("ok", ok or "done")
        return res
    except BookError as e:
        st.session_state["flash"] = ("error", str(e))
    finally:
        st.rerun()


# ── sidebar: the library and the book ──────────────────────────────────────────────────────────────────────────────

st.sidebar.title("📚 book-graph")
lib = Path(st.sidebar.text_input("Library", os.environ.get("BOOKGRAPH_LIBRARY") or str(default_library()))).expanduser()
books = ui.list_books(lib)
if not books:
    st.warning(f"No book in {lib}: create one with `bgr new PRESS BOOK`.")
    st.stop()
target = st.sidebar.selectbox("Book", books)
auto = st.sidebar.checkbox("Refresh while a step is running", value=True)
if st.sidebar.button("Reload"):
    st.rerun()

root = lib / target
book = load_book(root)
rows = pl.status_rows(book)
running = any(_state(r["manifest"]) == "RUNNING" and r["why"] == "running" for r in rows)
st.title(f"{book.title}")
st.caption(f"{target} · levels {', '.join(book.levels)} · languages {', '.join(book.languages)} · models {', '.join(book.models) or '-'}")
_flash()

tab_pipe, tab_review, tab_preview, tab_lib = st.tabs(["Pipeline", "Review", "Preview", "Library"])

# ── Pipeline ───────────────────────────────────────────────────────────────────────────────────────────────────────

with tab_pipe:
    table = []
    for r in rows:
        t, m = r["task"], r["manifest"]
        table.append({"chapter": t.chapter.id if t.chapter else "book", "step": t.step,
                      "state": f"{ICON.get(_state(m), '⚪')} {_state(m)}", "review": _review_label(m),
                      "next": "▶ ready" if r["ready"] else r["why"], "summary": ((m or {}).get("summary") or (m or {}).get("error") or "")[:110]})
    _table(table)

    c1, c2, c3 = st.columns(3)
    ready = pl.ready_tasks(book)
    if c1.button(f"Start the {len(ready)} ready step(s) in the background", disabled=not ready,
                 help="what `bgr next` does; each waits for your review afterwards"):
        _do(pl.dispatch, book, lib, ok=f"started {len(ready)} step(s)")
    until = c2.selectbox("Run all, up to", pl.STEPS, index=len(pl.STEPS) - 1)
    if c3.button("Run all now (approves each OK step)", type="primary",
                 help="what `bgr pipeline` does: synchronous, billed for model steps, nobody reviews in between"):
        with st.spinner("Running the pipeline: this page waits until it stops…"):
            try:
                res = pl.run_pipeline(root, until=until)
                st.session_state["flash"] = ("ok" if res["ok"] else "error", f"{len(res['ran'])} step(s) run; {res['stopped']}")
            except BookError as e:
                st.session_state["flash"] = ("error", str(e))
        st.rerun()
    if running and auto:
        time.sleep(3)
        st.rerun()

# ── Review ─────────────────────────────────────────────────────────────────────────────────────────────────────────

with tab_review:
    names = [r["task"].name for r in rows]
    pick = st.selectbox("Step", names, index=next((i for i, r in enumerate(rows) if _review_label(r["manifest"]) == "pending"), 0))
    r = rows[names.index(pick)]
    t, m, ch = r["task"], r["manifest"], r["task"].chapter
    st.subheader(f"{ICON.get(_state(m), '⚪')} {t.name}")
    if m is None:
        st.info(f"Not run yet. {r['why']}.")
    else:
        st.write(m.get("summary") or "")
        if m.get("error"):
            st.error(m["error"])
        rv = m.get("review") or {}
        st.caption(f"run {m.get('run')} · {m.get('started', '')} → {m.get('finished', '')} · review: {rv.get('status', '-')}"
                   + (f" by {rv.get('by')} {rv.get('note', '')}" if rv.get("by") else ""))

        if t.step == "1-ingest" and ch:
            _table(ui.chunk_rows(book, ch))
        elif t.step in ("2-extract", "3-validate") and ch and book.graph_path(ch).exists():
            if t.step == "3-validate":
                d = m.get("details") or {}
                for p in d.get("problems") or []:
                    st.error(p)
                for w in d.get("warnings") or []:
                    st.warning(w)
            _table(ui.graph_rows(book, ch))
            st.caption(f"Edit {book.graph_path(ch)} by hand if needed: the next run sees the change.")
        elif t.step == "4-generate" and ch:
            pages = ui.page_files(book, ch)
            if pages:
                shown = st.selectbox("Page", pages, format_func=lambda p: f"{p.parent.parent.parent.name}/{p.parent.parent.name} · {p.name}")
                components.html(shown.read_text(encoding="utf-8", errors="replace"), height=640, scrolling=True)
            else:
                st.info("No page yet.")
        elif t.step == "5-build":
            st.info("Open the **Preview** tab to look at the built site before you approve it.")
        elif t.step == "6-publish":
            st.info("A no-op: conceptbook-app reads the library (docs/GUIDE/Library-Contract.md).")

    b1, b2, b3, b4 = st.columns(4)
    status = _state(m)
    if b1.button("Run this step", disabled=not r["ready"], help=f"{r['why']}"):
        _do(pl.spawn, book, lib, t, ok=f"started {t.name}")
    if b2.button("Approve", type="primary", disabled=not (m and status == "OK" and _review_label(m) != "approved")):
        _do(pl.review, book, t.step, ch, "approve", by=os.environ.get("USER", "ui"), ok=f"approved {t.name}")
    note = st.text_input("Note (for Reject)", key=f"note-{t.name}")
    if b3.button("Reject (run again)", disabled=not (m and status == "OK")):
        _do(pl.review, book, t.step, ch, "reject", by=os.environ.get("USER", "ui"), note=note, ok=f"rejected {t.name}")
    if b4.button("Retry", disabled=status != "NOT_OK", help="run a NOT_OK step again as it is"):
        _do(pl.review, book, t.step, ch, "retry", ok=f"{t.name} marked for retry")

    with st.expander("Log"):
        st.code(ui.step_log(book, t.step, ch) or "(no log)", language="text")
    if m:
        with st.expander("Manifest"):
            st.code(yaml.safe_dump(m, sort_keys=False, allow_unicode=True), language="yaml")

# ── Preview ────────────────────────────────────────────────────────────────────────────────────────────────────────

with tab_preview:
    site = ui.built_site(book)
    if site is None:
        st.info("Nothing to preview yet: 5-build has not finished OK.")
    else:
        from bookgraph.build import site_base
        pv = st.session_state.get("preview")
        if pv and not (ui.alive(pv["pid"]) and pv.get("target") == target):
            ui.stop_preview(pv["pid"])
            pv = st.session_state.pop("preview", None) and None
        p1, p2 = st.columns(2)
        if pv is None:
            if p1.button("Start preview", type="primary"):
                try:
                    pv = ui.start_preview(lib, target)
                    st.session_state["preview"] = {**pv, "target": target}
                    st.rerun()
                except RuntimeError as e:
                    st.error(str(e))
        else:
            url = f"{pv['root']}{site_base(book)}"
            p1.markdown(f"Serving at [{url}]({url}) — Generate in the page makes billed model calls.")
            if p2.button("Stop preview"):
                ui.stop_preview(pv["pid"])
                st.session_state.pop("preview", None)
                st.rerun()
            components.iframe(url, height=760, scrolling=True)

# ── Library ────────────────────────────────────────────────────────────────────────────────────────────────────────

with tab_lib:
    try:
        cat = read_catalog(lib)
        _table([{"press": p["press"], "book": b["book"], "title": b.get("title", ""), "chapters": len(b.get("chapters", []))}
                      for p in cat.get("presses", []) for b in p.get("books", [])])
    except BookError as e:
        st.error(str(e))
    st.caption("conceptbook-app reads this catalog and the folders under it: docs/GUIDE/Library-Contract.md")
