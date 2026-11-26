# The library contract: book-graph is the back office, conceptbook-app reads it

book-graph makes the content; **conceptbook-app** shows it. Nothing is pushed to GitHub: the app pulls from the library
(`~/book-graph/library`, or `$BOOKGRAPH_LIBRARY`). So `6-publish` of the pipeline is a no-op, and the library's layout is the
interface between the two. This page says what a reader of the library may rely on. (`bgr publish` still exists: it builds and
pushes one book's static site when you ask for it directly, e.g. for a standalone `cb-*` site.)

## What a reader may rely on

```
<library>/catalog.json                                       every press, book and chapter (below)
<library>/<press>/<book>/book.yaml                           the book: title, levels, languages, models, tags, site, source
<library>/<press>/<book>/chapters/<ch>/graph.yaml            the chapter's concept graph (primitives, concepts, applications)
<library>/<press>/<book>/chapters/<ch>/concept_sources.yaml  where each concept comes from in the source text (path B only)
<library>/<press>/<book>/chapters/<ch>/output/graph.html     the chapter's navigator page
<library>/<press>/<book>/chapters/<ch>/output/<level>.<lang>/<model>/html/concept_<node>[_<lang>].html
<library>/<press>/<book>/chapters/<ch>/output/<level>.<lang>/<model>/html/book_<target>[_<lang>].html
<library>/<press>/<book>/pipeline/[<ch>/]<step>.out.yaml     what ran, its status and its review (below)
```

- `<level>` is `intro | core | college | research`; `<lang>` a language code (`en` pages carry no `_<lang>` suffix); `<model>` is the
  model's folder name (`adapter:model`, with every character outside `A-Za-z0-9_-` turned into `-`).
- English pages are `concept_<node>.html`; other languages add `_<lang>` before `.html`.
- Everything else under a book (`.bg_cache/`, `.bg_runs/`, `dist/`, `pipeline/logs/`) is book-graph's own: do not read it.

### catalog.json

```json
{"presses": [{"press": "openstax", "books": [{
  "book": "chemistry-2e", "title": "Chemistry 2e", "tags": ["chemistry"],
  "levels": ["core"], "languages": ["en"], "models": ["claude-sonnet-5-5"],
  "chapters": [{"id": "ch15", "title": "...", "capstone": "wastewater_phosphate_precipitation",
                "nodes": 23, "edges": 31, "primitives": 4, "concepts": 12, "applications": 7,
                "books": [{"target": "wastewater_phosphate_precipitation", "level": "college", "language": "en",
                           "model": "claude-sonnet-5-5", "file": "output/college.en/claude-sonnet-5-5/html/book_....html"}]}]}]}]}
```

`books` lists the generated book pages found on disk (its `file` is relative to the chapter folder); a chapter whose graph cannot
be loaded has an `error` instead of the counts. The catalog is rewritten atomically (under an `flock` on `catalog.json.lock`), so a
reader never sees a half-written file; if it is not valid JSON, treat the library as unavailable and run `bgr catalog`.

### What is ready to show: the pipeline manifests

`pipeline/<ch>/4-generate.out.yaml` says whether a chapter's pages were made and **reviewed**:

```yaml
status: OK                 # OK | NOT_OK | RUNNING
review: {status: approved, by: ..., at: ..., note: ...}      # pending | approved | rejected
```

Recommended rule for the app: show a chapter once its `4-generate` is `OK` and `approved`; a book's site once `5-build` is. Pages
of a chapter that is still `pending` or `rejected` exist on disk but have not passed a human.

### Attribution and licence

`book.yaml: source: {kind, url, license, attribution}` (and, for a chapter from a PDF, `chunks.yaml: source_attribution`) say
whose text a book comes from. For CC BY material such as OpenStax this must be shown: the app shows it on its **About** page.
The per-chapter source line is no longer drawn on the domain page.

## Rules for the two sides

- The app only **reads** the library. book-graph is the only writer (its CLI, `bgr ui`, or a recipe run).
- Never read a folder or file that is not listed above; new files may appear there at any time.
- A path or key listed above changes only with a note in `CHANGELOG.md` and a new line here.
- The contract is **unversioned for now**; if it must change in a way a reader cannot ignore, add `"contract": 2` to
  `catalog.json` and keep the old layout readable for one release.

## Open points (to decide with the app)

- Whether the app reads `catalog.json` only, or also scans folders: the catalog is faster, the folders are always current.
- Whether the app wants a flat export (`bgr export TARGET DOMAINS`, the old apps' `public/domains/` shape) as a transition.
- Which of `book.yaml`'s fields the app needs beyond those above (`site:`, `tags`, `source`).
