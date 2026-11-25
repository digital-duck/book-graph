# book-graph

Concept-books from graphs, over [GraphLang](https://github.com/digital-duck/graflang). A chapter is a `graph.yaml`
(primitives, concepts, applications); `bookgraph` writes one concept-book section per node in teaching order, plus a
payoff and an index page. SPL is gone: generation is a GraphLang recipe.

**press > book > chapter.** A *book* belongs to a *press*, and has *chapters*, each with its own graph. (The earlier
concept-book apps called a chapter a "domain" and forked a repo per book; here a book is data in a shared library.)

```
~/book-graph/library/                      # or $BOOKGRAPH_LIBRARY, or --library
  catalog.json                             # every press, book and chapter
  <press>/<book>/book.yaml
  <press>/<book>/chapters/<chapter>/graph.yaml
  <press>/<book>/chapters/<chapter>/output/<level>.<lang>/<model>/html/{concept,book}_<node>[_<lang>].html
  <press>/<book>/chapters/<chapter>/.bg_cache/        # section cache: a hit costs no model call
```

## Quick start

```bash
pip install -e ".[dev]"                    # installs `bookgraph` (alias `bgr`)

bgr new acme demo                          # scaffold a book with a one-chapter starter graph
bgr validate acme/demo                     # acyclic, reducible to primitives, every node has `defines`
bgr order ~/book-graph/library/acme/demo/chapters/ch01/graph.yaml     # the teaching order
bgr generate acme/demo --llm claude_cli:claude-sonnet-5-5 --level core --lang en
bgr list
```

Existing ConceptBook graphs load unchanged (`bgr validate path/to/calculus_ch01/input/graph.yaml`).

## book.yaml

```yaml
press: acme
book: demo
title: Demo Book
levels: [core]                  # intro | core | college | research
languages: [en]
models: [claude_cli:claude-sonnet-5-5]
tags: [math]                    # math/physics/engineering keep proof rigor at research level
site: {base: /cb-demo/, title: Demo Book}         # optional: `bgr build`
publish: {repo: digital-duck/cb-demo}             # optional: `bgr publish` (branch gh-pages)
chapters:
  - {id: ch01, title: Chapter 1, graph: chapters/ch01/graph.yaml, capstone: budget}
```

## Commands

| Command | Purpose |
|---|---|
| `bgr new PRESS BOOK` | scaffold a book |
| `bgr validate TARGET [--strict]` | a graph.yaml, or every chapter of a book (incl. each capstone is a node); warnings for a non-application capstone and tiers that do not rise; exit 1 on a problem (or a warning, with --strict) |
| `bgr order GRAPH [--target NODE]` | the order sections are written in |
| `bgr generate TARGET [-c CHAPTER] [--level L] [--lang X] [--llm A:M] [--skip-cache] [--max-llm-calls N] [--budget N]` | run the `generate_chapter` recipe for every chapter x level x language x model, as a resumable run |
| `bgr generate TARGET --resume last [--budget N]` | continue a run that stopped (a failure, its budget, Ctrl-C): only what is not done runs, and finished sections come from the cache |
| `bgr runs TARGET` | a book's runs: status, variants done, model calls, why one stopped |
| `bgr catalog [TARGET]` | refresh `catalog.json` for one book or the whole library |
| `bgr list` | presses, books, chapters |
| `bgr ingest TARGET --pdf FILE (--chapter N \| --list)` | one chapter of a PDF textbook -> `<chapter>/chunks.yaml`, and the chapter in book.yaml (needs `pip install "book-graph[ingest]"`) |
| `bgr extract TARGET -c CHAPTER [--chunks FILE] [--llm A:M] [--force]` | a chapter's chunks.yaml -> its graph.yaml: two model passes, normalized, validated, repaired once |
| `bgr next TARGET [--watch N] [--parallel N] [--foreground] [--dry-run]` | the asynchronous pipeline: start every step that is ready (see below) |
| `bgr status TARGET` | each chapter's steps and the book's, with what is ready and what waits for review |
| `bgr approve/reject TARGET STEP [-c CHAPTER\|all] [-m NOTE]`, `bgr retry ...` | review a step's last run: approve lets the next step start; reject runs it again; retry reruns a NOT_OK one |
| `bgr migrate REPO [--press P] [--book SLUG] [--adapter A]` | import a `cb-*` concept-book repo: chapters, pages, navigators, and its spl3 cache as `legacy_cache:` |
| `bgr navigator TARGET [-c CHAPTER] [--force]` | write each chapter's navigator (`output/graph.html`) where it is missing or its graph changed |
| `bgr build TARGET [--out DIR]` | the book as a static site: the one web app with the book's content and `site:` strings (needs npm) |
| `bgr serve TARGET [--port 8200]` | the site on 127.0.0.1 with the local API: Generate, Export PDF and Settings work; pages appear live |
| `bgr publish TARGET [--dry-run] [--yes] [--no-build]` | build, then push the site to `publish:` (GitHub Pages `gh-pages`) as one fresh commit, behind a policy check |
| `bgr export TARGET DOMAINS` | write a book into a web app's `public/domains/` (graphs, pages, navigators, flat `catalog.json`) |

TARGET is a `book.yaml`, a book folder, or `press/book` under the library.

## The pipeline

```
path B (from a PDF):        1-ingest -> 2-extract -> 3-validate -> 4-generate ┐
path A (an authored graph):                          3-validate -> 4-generate ┤ every chapter
                                                                               └-> 5-build -> 6-publish
```

A chapter with `source: {pdf: book.pdf, chapter: 3}` in book.yaml starts at 1-ingest; any other starts at 3-validate from
its graph.yaml. Every step writes `<book>/pipeline/[<chapter>/]<step>.out.yaml`: OK or NOT_OK, the fingerprints of its
inputs, and a review. `bgr next` starts, in the background, every step whose predecessor is OK, approved and current, and
whose own manifest is missing, stale, rejected or marked for retry; run it by hand, from cron, or with `--watch`. Every
step waits for `bgr approve` (`pipeline: {auto_approve: [3-validate]}` in book.yaml lets a step through); a NOT_OK step
runs again once its input is fixed or `bgr retry` marks it.

## Status

Packaging, validation, generation, the catalog, `migrate` and `export` are done and tested with a scripted model (no LLM
calls in tests). `migrate` then `export` reproduces each `cb-*` repo's `public/domains/` file for file. From a PDF:
`bgr ingest BOOK --pdf FILE --chapter N`, then `bgr extract BOOK -c chNN`, `bgr generate`, `bgr build`, `bgr publish`. Documentation: [Setup](docs/GUIDE/Setup.md), [Tutorial](docs/GUIDE/Tutorial.md), [User Guide](docs/GUIDE/User-Guide.md); the demo deck, [docs/DEMO/book-graph-v1.demo.html](docs/DEMO/book-graph-v1.demo.html) (open it in a browser; arrow keys, T outline, N notes, PDF); the plan and the review in `docs/DEV/`.
