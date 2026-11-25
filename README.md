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
| `bgr validate TARGET` | a graph.yaml, or every chapter of a book; exit 1 on any problem |
| `bgr order GRAPH [--target NODE]` | the order sections are written in |
| `bgr generate TARGET [-c CHAPTER] [--level L] [--lang X] [--llm A:M] [--skip-cache] [--max-llm-calls N]` | run the `generate_chapter` recipe for every chapter x level x language x model |
| `bgr catalog [TARGET]` | refresh `catalog.json` for one book or the whole library |
| `bgr list` | presses, books, chapters |
| `bgr migrate REPO [--press P] [--book SLUG] [--adapter A]` | import a `cb-*` concept-book repo: chapters, pages, navigators, and its spl3 cache as `legacy_cache:` |
| `bgr navigator TARGET [-c CHAPTER] [--force]` | write each chapter's navigator (`output/graph.html`) where it is missing or its graph changed |
| `bgr build TARGET [--out DIR]` | the book as a static site: the one web app with the book's content and `site:` strings (needs npm) |
| `bgr publish TARGET [--dry-run] [--yes] [--no-build]` | build, then push the site to `publish:` (GitHub Pages `gh-pages`) as one fresh commit, behind a policy check |
| `bgr export TARGET DOMAINS` | write a book into a web app's `public/domains/` (graphs, pages, navigators, flat `catalog.json`) |

TARGET is a `book.yaml`, a book folder, or `press/book` under the library.

## Status

Packaging, validation, generation, the catalog, `migrate` and `export` are done and tested with a scripted model (no LLM
calls in tests). `migrate` then `export` reproduces each `cb-*` repo's `public/domains/` file for file. Not yet:
ingestion/extraction from PDFs (concept-book-press) and `serve` (the local API behind Generate). See `docs/` and `concept-book/docs/DEV/readme-book-graph-plan.md`.
