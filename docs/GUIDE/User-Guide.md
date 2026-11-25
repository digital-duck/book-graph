# User Guide

book-graph turns concept graphs into published concept books. A chapter is a graph of what it teaches; book-graph writes
one section per node in teaching order, a payoff for the chapter's capstone, an interactive navigator, and a web site,
and publishes it. It runs on GraphLang recipes, like the other GraphLang apps (app-graph, work-graph, data-graph). Install
with [Setup.md](Setup.md); learn it with [Tutorial.md](Tutorial.md).

- [Core concepts](#core-concepts)
- [book.yaml](#bookyaml)
- [graph.yaml](#graphyaml)
- [Generation](#generation)
- [The pipeline](#the-pipeline)
- [The web app: build, serve, publish](#the-web-app-build-serve-publish)
- [From existing content: ingest, extract, migrate, export](#from-existing-content-ingest-extract-migrate-export)
- [Command reference](#command-reference)
- [Files, environment, safety](#files-environment-safety)
- [Limitations](#limitations)

## Core concepts

| Term | Meaning |
|---|---|
| **library** | A folder holding every press and book, plus `catalog.json` (default `~/book-graph/library`). |
| **press > book > chapter** | A press publishes books, and a book has chapters, each with its own concept graph. A book is data in the library, not a forked repository. |
| **concept graph** | A chapter's `graph.yaml`: **primitives** (taken as given), **concepts** (built from other nodes) and **applications** (what they are for). Edges point from a prerequisite to what needs it. |
| **teaching order** | The order sections are written in: every node after its prerequisites, and early the nodes that unlock the most others (zinet's productivity order). |
| **capstone** | The node a chapter's book is written toward, normally an application, which gets a **payoff** section. Sections are written for the capstone and everything it needs. |
| **variant** | One chapter at one **level**, one **language** and one **model**, with pages in `output/<level>.<language>/<model>/html/`. |
| **section cache** | Every section written is cached, keyed by what reaches the prompt. A cache hit costs no model call. |
| **recipe** | A GraphLang workflow (`*.graph.yaml`): model calls, deterministic steps, and policy GUARDs. book-graph ships `generate_chapter`, `extract_graph` and `publish`. |
| **pipeline** | Asynchronous steps (`1-ingest` … `6-publish`). Each writes a manifest, is reviewed by a person, and is started by `bgr next`. |

Commands take a **TARGET**: a `book.yaml`, a book folder, or `press/book` under the library. `--library/-L` (or
`BOOKGRAPH_LIBRARY`) chooses the library.

## book.yaml

```yaml
press: digital-duck                    # letters, digits, _ and -
book: linalg
title: Linear Algebra
levels: [core, college]                # intro | core | college | research
languages: [en, zh]                    # en pages are unsuffixed; others get _<lang>
models: [claude_cli:claude-sonnet-5-5] # ADAPTER:MODEL; the page folder is the model part, path-safe
tags: [math]                           # math / physics / engineering keep proof rigor at the research level
source: {kind: openstax, url: ..., license: ..., attribution: ...}         # optional, recorded in the catalog
recap_earlier_chapters: true           # default: a concept an earlier chapter introduced is recapped, not re-derived
legacy_cache: ../cb-linalg/spl/.spl    # optional: an SPL app's cache, read (never written) so its sections are reused
pipeline: {auto_approve: []}           # optional: pipeline steps that need no human review
site:                                  # optional, for build / serve / publish
  base: /cb-linalg/                    # where the site is served (GitHub Pages: /<repo>/)
  title: {en: Linear Algebra, zh: 线性代数}
  tagline: {en: From vectors to the spectral theorem}
  description: A concept book of linear algebra.
  logo: images/logo.svg                # a file inside the book folder, shown instead of the title
  tags: {math: {en: math, zh: 数学}}
  models: [{value: sonnet, label: sonnet (Claude)}]   # default: the book's models
publish: {repo: digital-duck/cb-linalg, branch: gh-pages, cname: ""}   # optional, for publish
chapters:
  - id: ch01                           # letters, digits, _ and -
    title: Systems of equations
    graph: chapters/ch01/graph.yaml    # default chapters/<id>/graph.yaml
    capstone: gaussian_elimination     # default: the last application in teaching order
    global_id: linalg_ch01             # optional; default <book>_<id>: the id in URLs and catalog.json
    description: ...                   # optional
    source: {pdf: linalg.pdf, chapter: 1, attribution: ...}   # optional: pipeline path B (relative to the book folder)
    catalog: {i18n: {zh: {name: 第一章}}}                       # optional: extra catalog.json fields, exported as they are
```

`bgr new PRESS BOOK` writes a starter book.yaml with one chapter.

## graph.yaml

The ConceptBook format; every existing graph loads unchanged.

```yaml
domain: linalg_ch01
primitives:
  vector:
    label: Vector                       # optional; default: the id, title-cased
    labels: {zh: 向量}                   # optional, per language: headings and the navigator
    defines: An ordered list of numbers.    # required: the node's meaning, sent to the model
    tier: 0                             # optional
concepts:
  linear_combination:
    composed_of: [vector]
    defines: A sum of scaled vectors.
    diagram: {type: flowchart LR, hint: vectors scaled then added}   # optional: asks for a diagram
applications:
  gaussian_elimination:
    needs: [linear_combination]
    defines: Solving a linear system by row operations.
```

`bgr validate` reports **problems**, which fail it:

- a cycle;
- a prerequisite that is never declared;
- a concept that does not reduce to primitives;
- a node without `defines`;
- an id declared in two sections;
- a section or dependency list of the wrong type;
- a chapter capstone that is not a node of its graph.

It reports **warnings**, which fail only with `--strict`:

- a capstone that is not an application (its book gets no payoff);
- a tier that does not rise along an edge.

## Generation

`bgr generate TARGET` runs the `generate_chapter` recipe for every chapter × level × language × model of the book, or
the subset given with `-c`, `--level`, `--lang` and `--llm`.

**Per chapter, the recipe does this:**

1. **prepare:** loads and validates the graph (a broken one stops here, before any model call), plans the teaching order
   toward the capstone, and picks the style guide. The style comes from the level (`intro` → feynman, `core`, `college`,
   `research`); the subject rigor comes from the book's slug, tags and graph domain.
2. **A policy GUARD** checks where the run may write (below).
3. **A loop over the sections.** For each one: the cache, or the model. Then a **primitive-budget check**: a section may
   name at most two primitives the reader has not met yet, and gets **one refinement** if it names more. Then it is
   cached, and `concept_<node>.html` is written.
4. **The payoff,** for an application capstone: cached like a section.
5. **The book page,** `book_<capstone>.html`: a table of contents plus the payoff.

**The section cache** is keyed by everything that reaches the prompt: the language, the style, the full `ADAPTER:MODEL`,
the node's label and definition, the earlier chapter's recap note if any, and a hash of the three prompt files, the style
guide and the primitive budget. Editing a definition or a prompt makes the affected sections misses; `--skip-cache`
ignores the cache.

**The legacy cache.** With `legacy_cache:`, a miss is looked up read-only in an SPL app's spl3 store, using spl3's own
key, and a hit is copied into the library's cache. That way a migrated book does not pay again for content it already
has.

**Cross-chapter recaps.** A node that an earlier chapter (in book.yaml order) also declares gets concept-book-press's note
in its context: "Already introduced in Chapter N as: … give a brief recap rather than a full re-derivation". The section
then recaps. Turn it off with `recap_earlier_chapters: false`.

**Runs are resumable.** Every `generate` is a run with a ledger in `<book>/.bg_runs/`, and `bgr runs TARGET` lists a
book's runs.
- **How a run stops.** Each variant is capped by `--max-llm-calls` (default 200), and `--budget N` caps the whole run.
  A run also stops after two failed variants in a row, which usually means the model is down or rate-limited. Ctrl-C
  saves the ledger.
- **Resuming.** `bgr generate TARGET --resume last` (or a run id) runs only what is not done, and finished sections come
  from the cache. `--budget` on a resume raises a spent budget. A resumed `--skip-cache` run reuses what it regenerated
  itself.
- **Retries.** The recipe retries transient model failures twice, 15 s apart. A failed attempt counts as a model call.

## The pipeline

```
path B (from a PDF):        1-ingest -> 2-extract -> 3-validate -> 4-generate ┐
path A (an authored graph):                          3-validate -> 4-generate ┤ every chapter
                                                                               └-> 5-build -> 6-publish
```

A chapter with `source: {pdf, chapter}` is on path B. Any other chapter is on path A, starting at `3-validate` from its
graph.yaml.

| Step | Does | Output |
|---|---|---|
| `1-ingest` | the chapter's sections from the PDF | `chunks.yaml` |
| `2-extract` | the concept graph from the chunks (on a first run, an existing graph.yaml is kept for review instead) | `graph.yaml` |
| `3-validate` | `bgr validate`'s checks | problems, warnings |
| `4-generate` | every level × language × model of the chapter, as one run | pages, run id |
| `5-build` | the static site | `<book>/dist/` |
| `6-publish` | the push to `publish:` (its review confirms the replacement of the live site) | the commit |

**Manifests.** Every step writes `<book>/pipeline/<chapter>/<step>.out.yaml`, or `<book>/pipeline/<step>.out.yaml` for
5 and 6:

```yaml
step: 3-validate
chapter: ch01
run: 20261125-172612-4cee
status: OK                      # OK | NOT_OK | RUNNING (with its pid)
started: ...; finished: ...
inputs: {graph: d4274f831666db91, capstone: braking_distance}   # fingerprints of what it read
summary: 6 nodes, valid
error: ''
details: {problems: [], warnings: [], nodes: 6}
review: {status: approved, by: <reviewer>, at: ..., note: graph checked}
```

**`bgr next TARGET`** starts every step that is ready, then exits:

- **When a step is ready:** the step before it is OK, **approved and current** (its inputs unchanged since), and its own
  manifest is missing, its inputs changed, or it was rejected or marked for retry.
- **How steps run:** each in a detached background process, logging to `<book>/pipeline/logs/`, at most `--parallel`
  (default 2) at once.
- **Options:** `--watch SECONDS` keeps dispatching, `--foreground` runs in-process, and `--dry-run` only lists.
- **No step starts another.** There is deliberately no `run` command: `bgr next`, by hand or from cron, advances the
  pipeline as far as the reviews allow.

**Review.** Every step waits for a person unless book.yaml's `pipeline.auto_approve` lists it.

| Command | Effect |
|---|---|
| `bgr status TARGET` | every step: its state, whether it is READY, and why the rest waits |
| `bgr approve TARGET STEP [-c CH\|all] [-m NOTE] [--by NAME]` | approve an OK run; the next step may start |
| `bgr reject TARGET STEP [-c CH\|all] -m NOTE` | the step runs again (2-extract extracts again, 4-generate regenerates with skip-cache); the note is kept |
| `bgr retry TARGET STEP [-c CH\|all]` | run a NOT_OK step again as it is, e.g. after a rate limit |

**Resuming from a failure.** A NOT_OK step is not rerun on its own. Fix its input (the fingerprint then changes), or
retry it. A failed `4-generate` resumes its run ledger.

**Staleness.** Changing a step's input (editing `graph.yaml` after it was validated, say) makes the step ready again, and
blocks everything after it until it is redone and approved.

The direct commands (`bgr extract`, `bgr generate`, …) work without the pipeline and write no manifests. The pipeline sees
their effects through the input fingerprints.

## The web app: build, serve, publish

There is one web app for every book: concept-book-base's frontend, in `bookgraph/web/`. Per-book values come from
book.yaml's `site:` and are never written into `web/`.

- **`bgr build TARGET [--out DIR]`** stages the app, exports the book into its `public/domains/`, writes `config.js`,
  `ui.yaml` and `index.html` from `site:`, and runs Vite with `site.base` (default `/<book>/`). The output is
  `<book>/dist/`.
- **`bgr serve TARGET [--port 8200]`** serves the built app together with the local API behind Generate, Export PDF and
  Settings, on **127.0.0.1 only**. Content is read live from the library, so a page generated from the browser appears at
  once. It keeps the old FastAPI backend's API, and is protected as follows:
  - the `Host` header must be this server;
  - `/api` requests must be same-origin, so another website open in your browser cannot start a paid generation;
  - API keys typed into Settings stay in memory, are never sent back, and never reach a recipe step.
- **`bgr publish TARGET [--dry-run] [--yes] [--no-build]`** builds, then runs the `publish` recipe, which pushes the site
  as one fresh commit to `publish:`'s branch (`gh-pages`), as `gh-pages --no-history --dotfiles` did. Its policy,
  `policies/publish.cedar`, refuses the push unless:
  - the folder is a built site;
  - the remote is reachable;
  - the branch is neither the remote's default nor `main`/`master`;
  - the base path the site was built for (read from the built page) matches where it will be served.

  The command asks before replacing the live site unless `--yes`. Git uses your SSH keys or agent (`HOME` and
  `SSH_AUTH_SOCK` only).
- **`bgr navigator TARGET [-c CH] [--force]`** writes each chapter's navigator, `output/graph.html`. It is
  concept-book-base's page, byte for byte. It is written when missing or when the graph changed, and `--force` upgrades
  migrated ones.

## From existing content: ingest, extract, migrate, export

- **`bgr ingest TARGET --pdf FILE --chapter N [--id ID]`** reads one chapter of a bookmarked PDF into
  `<chapter>/chunks.yaml`, one chunk per section, and adds the chapter to book.yaml. `--list` shows the chapters. It is
  concept-book-press's ingester and needs the `[ingest]` extra (PyMuPDF, AGPL-3.0).
- **`bgr extract TARGET -c CHAPTER [--chunks FILE] [--llm A:M] [--force]`** turns `chunks.yaml` into `graph.yaml` with
  concept-book-press's two passes (the concepts, then their prerequisites). Then:
  - a deterministic cleanup: a concept with no prerequisite becomes a primitive, a primitive with prerequisites a
    concept; unknown and self dependencies are dropped; the first pass's labels are kept; tiers are computed;
  - validation, with one repair call if it fails;
  - a graph still invalid is kept as `graph.draft.yaml`.

  It costs about 3 model calls per chapter, and an existing graph.yaml is never replaced without `--force`.
- **`bgr migrate REPO [--press P] [--book SLUG] [--adapter A]`** imports a `cb-*` concept-book repo, which it only reads:
  chapters (old ids kept as global ids), graphs, pages, navigators, catalog fields and site strings. `legacy_cache:` is
  set to the repo's SPL cache and `publish.repo` to its GitHub origin. Check the adapter in the models it writes
  (`claude_cli:<folder>`).
- **`bgr export TARGET DOMAINS`** writes a book into an app's `public/domains/` and merges its chapters into the app's
  flat `catalog.json`, under the app's own lock. The page lists and counts are recomputed from disk, and other entries are
  kept. A migrated cb repo exports back file for file.

## Command reference

| Command | Purpose |
|---|---|
| `bgr new PRESS BOOK [--title T]` | scaffold a book with a one-chapter starter graph |
| `bgr validate TARGET [--strict] [--format json]` | check a graph.yaml, or every chapter of a book |
| `bgr order GRAPH [--target NODE]` | print the teaching order |
| `bgr generate TARGET [-c] [--level] [--lang] [--llm] [--target-node NODE -c CH] [--skip-cache] [--max-llm-calls N] [--budget N]` | generate pages, as a resumable run |
| `bgr generate TARGET --resume last\|RUN [--budget N]` | continue a run |
| `bgr runs TARGET` | list a book's runs |
| `bgr ingest TARGET --pdf FILE (--chapter N \| --list)` | a PDF chapter to chunks.yaml |
| `bgr extract TARGET -c CHAPTER` | chunks.yaml to graph.yaml |
| `bgr navigator TARGET [--force]` | the chapters' navigators |
| `bgr build TARGET [-o DIR]` | the static site |
| `bgr serve TARGET [-p PORT]` | the site plus the local API, on 127.0.0.1 |
| `bgr publish TARGET [--dry-run] [-y]` | build and push to `publish:` |
| `bgr next TARGET [--watch N] [--parallel N] [--foreground] [--dry-run]` | dispatch ready pipeline steps |
| `bgr status TARGET` | the pipeline's state |
| `bgr approve / reject / retry TARGET STEP [-c CH\|all]` | review a step |
| `bgr migrate REPO` | import a cb-* repo |
| `bgr export TARGET DOMAINS` | write a book into an app's public/domains |
| `bgr catalog [TARGET]`, `bgr list` | refresh and list `<library>/catalog.json` |

Every command takes `-L/--library`. `bgr COMMAND --help` lists every option.

## Files, environment, safety

| Path | What |
|---|---|
| `<library>/catalog.json` | every press, book and chapter (written under a lock, atomically) |
| `<book>/chapters/<ch>/graph.yaml`, `chunks.yaml`, `concept_sources.yaml` | a chapter's inputs |
| `<book>/chapters/<ch>/output/<level>.<lang>/<model>/html/` | its pages; `output/graph.html` is the navigator; `pdf/` holds exported PDFs |
| `<book>/chapters/<ch>/.bg_cache/` | the section cache |
| `<book>/.bg_runs/<run>.json` | generate run ledgers |
| `<book>/pipeline/` | step manifests and their logs |
| `<book>/dist/` | the built site |
| `~/book-graph/logs/`, `~/book-graph/web/`, `~/book-graph/build/` | run logs, the web app's npm packages, build stages |

| Variable | Effect |
|---|---|
| `BOOKGRAPH_LIBRARY` | the library (as `--library`) |
| `BOOKGRAPH_WRITE_ROOTS` | more folders that recipes may write under, besides your home and the temporary folder |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | keys for those adapters (or typed into `serve`'s Settings, in memory) |
| `BOOKGRAPH_WEB_TESTS=1` | runs the browser tests |

**Safety rules:**

- **Writes are policy-checked.** Every recipe step that writes files is `risky: true`, behind a Cedar GUARD: writes go
  under your home, the temporary folder or `BOOKGRAPH_WRITE_ROOTS`.
- **Recipe steps are isolated.** They run with only `PATH` and `PYTHONUTF8`; `publish` adds `HOME` and `SSH_AUTH_SOCK`.
- **Every path segment is checked.** Press, book, chapter, level, language and model are each checked to be a plain name.
- **Model output is untrusted.** Pages escape it; only one exact `<figure>` shape from public-domain hosts passes
  through.
- **Nothing overwrites by surprise.** `migrate` and `export` never overwrite a damaged catalog, and `extract` and
  `ingest` never replace existing work without `--force`.

## Limitations

- The SPL apps' `verify_section` is not ported: it never checked anything, since its claim check was a TODO. Only the
  primitive-budget check runs.
- A rejection's note is recorded in the manifest, but not yet passed to the model.
- `extract` has been tested with scripted models and on concept-book-press's 190 real extracted graphs, but not yet run
  against a live model in this repository. Try one chapter before a whole book.
- concept-book-press's OpenStax-HTML ingester is not ported; the books' PDFs work.
- Navigators for chapters migrated from older cb repos have no per-language labels until `bgr navigator --force`
  rewrites them.
- `serve` uses the built bundle, so editing `web/` needs a restart, and Vite's dev server cannot talk to it yet.
