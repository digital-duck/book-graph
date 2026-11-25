# book-graph — Plan

Status: **proposal** (2026-11-24). Nothing built yet.

Goal: one new repo, `book-graph`, built on GraphLang (`graflang`), that turns a
`graph.yaml` for **any domain or book** into a published concept-book — without forking a
template repo per book.

---

## 1. Review: what exists today

| Repo | Role | Reality |
|---|---|---|
| `concept-book` | Multi-domain demo/sandbox (53 domains) | Frontend + FastAPI + SPL workflow, one repo holding many domains in `public/domains/`. Actively mutating ("refine" commits). |
| `concept-book-base` | Fork template | Same codebase as `concept-book`, empty `catalog.json`. Adds i18n `locales/`, `path_safety`, `catalog_lock`, model-aware paths. This is the copy-paste source of every `cb-*` repo. |
| `concept-book-press` (digi-duck-press) | Ingestion + publish pipeline | `ingest → extract (2-pass LLM) → validate → repo_init → sync → generate`, with SQLite progress tracking (`.pipeline/publish.db`). `repo_init` **forks `concept-book-base` into a new `cb-<book>` repo** — the scaling problem lives here. |
| `conceptbook-app` | Multi-tenant product | The already-working "one app, many domains" direction: 100 domains, publisher-namespaced `public/domains/{publisher}/{id}/`, auth/DB/personalization (DESIGN.md). Heavier than a book publisher needs. |

### Why the fork-per-book model doesn't scale

1. **Code drift.** Each `cb-*` is a snapshot of `concept-book-base` that then evolves
   locally. Already visible: `cb-chemistry` has diverged `api/services/{catalog_svc,executor,pdf_svc}.py`;
   `cb-linalg` / `cb-calculus` have a diverged `api/config.py`, `CLAUDE.md`, `.gitignore`, `package.json`.
   A fix to `executor.py` (e.g. path-safety, catalog locking) must be hand-ported to ≥12 repos.
2. **Content and engine are fused.** The graphs/HTML (data) live in the same repo as the Vite app
   and the FastAPI backend (code). A book is really `graph.yaml + settings + generated HTML`; the other ~95% of the repo is identical boilerplate.
3. **Four near-duplicate engines.** `graph_lib.py`, `tools.py`, `style_profiles.py`,
   `level_profiles.py` are copied into `concept-book`, `concept-book-base`, `cb-zinets`, and `conceptbook-app`;
   `graph_lib` also now exists as zinet's `zinet.algorithms` and app-graph's `graph_lib.py`.
4. **Generation is a 400-line SPL file + 40 KB `tools.py`** invoked by shelling out to `spl3 run`
   from the API (`executor.py`). SPL has no brace escape, no typed ports, no resumable checkpoint; the
   pipeline works around that with its own SQLite stage tracker.
5. **Publishing is manual glue.** `sync_from_press.py`, `init-app.sh`, `npm_deploy_all.sh`,
   `batch_gen_domains.py` — per-repo scripts, per-repo `.env`, per-repo GitHub Pages deploy.
6. **Config is code.** Book identity (title, ports, base path, branding, resource links) is spread across
   `vite.config.js`, `package.json`, `src/config.js`, `.env`, `CLAUDE.md`, and `cb-zinets/config.yaml`.

What is worth keeping: the graph-first idea and `graph.yaml` format; the
`public/domains/{id}/output/{level}.{lang}/{model}/html/` layout; the vis.js navigator; the
concept-book generation prompts (`write_section`, `refine_section`, `write_payoff`); press's
ingestion/extraction/validation; the on-demand-generation UX.

---

## 2. Proposal: `book-graph`

**Principle: a book is data, not a repo.** The engine, the web app and the recipes are
installed once. A book is a folder (or a small separate repo) containing `graph.yaml` +
`book.yaml`. Nothing is forked.

```
              ┌────────────── book-graph (one repo, one version) ─────────────┐
 graph.yaml ─►│ validate (zinet/appgraph) ─► recipes (graflang) ─► site builder │─► static site
 book.yaml  ─►│  extract  │  generate_book  │  translate │ publish             │   (GitHub Pages)
              └────────────────────────────────────────────────────────────────┘
```

### 2.1 Inputs

- **`graph.yaml`** — unchanged ConceptBook/zinet format (`domain`, `primitives`, `concepts`,
  `applications`, `composed_of`, `tier`). Back-compat is a hard requirement: all existing
  `public/domains/*/input/graph.yaml` files must load as-is.
- **`book.yaml`** — new, replaces per-repo config sprawl:
  ```yaml
  book: linalg                 # slug
  title: Linear Algebra
  source: {kind: openstax|pdf|none, url: ..., license: ..., attribution: ...}
  domains:                     # 1..N graphs (e.g. one per chapter, or one monolithic)
    - {id: linalg_ch01, graph: chapters/ch01/graph.yaml, capstone: ...}
  levels: [core, college]
  languages: [en, zh]
  models: [claude_cli:claude-sonnet-5-5]
  site: {base: /cb-linalg/, brand: {logo: ..., colors: ...}, resources: [...]}
  publish: {target: gh-pages, repo: digital-duck/cb-linalg}
  ```
  Existing `cb-zinets/config.yaml` `resources:` block maps directly into `site.resources`.

### 2.2 Layout (the new repo)

```
book-graph/
  pyproject.toml                 # console script: `bookgraph` (alias `bg`)
  src/bookgraph/
    cli.py                       # click group
    book.py                      # BookSpec loader/validator for book.yaml
    run.py                       # thin wrapper over graphlang.recipes (same pattern as app-graph/run.py)
    graph/                       # graph load/validate — delegate to zinet; NO private copy of graph_lib
    build/                       # deterministic half: paths, cache, catalog (locked), html render, pdf
    recipes/
      extract_graph/             # chunks -> graph.yaml  (press's 2-pass extraction as a recipe)
      generate_concept/          # one node -> concept_*.html   (write_section + refine/verify loop)
      generate_book/             # whole graph in teaching order; payoff capstone; resumable
      translate_locale/          # UI/content locale pass
      publish/                   # sync + build + deploy (GUARDed, risky: true)
    levels.py  styles.py         # single copy of level/style profiles
    ingest/                      # openstax.py, pdf.py  (moved from press)
    server/                      # FastAPI (optional, local-only, 127.0.0.1)
  web/                           # ONE Vite app, parameterised by book.yaml at build time
  docs/ tests/
```

### 2.3 Why GraphLang here

- Recipe = `*.graph.yaml` with typed ports, replacing `build_concept_book.spl`.
  `generate_book` is a loop over `build_order()` with a per-concept cache check — the same shape as
  app-graph's `build_component`, and that port already proves the pattern works
  (`graflang ≥ 0.3`, `graphlang.recipes`, `claude_cli` default, `max_llm_calls` cap).
- **Resumability comes for free** (`checkpoint`/`resume`) — retires press's hand-rolled
  `.pipeline/publish.db` stage tracker and its "rate_limited → retry" logic.
- **Guarded writes**: every step that writes outside the book's output dir or pushes to GitHub is
  `risky: true` behind a Cedar GUARD (`write_root_ok`), as in app-graph. Publishing becomes an explicit
  policy-checked step instead of a shell script.
- **Record/replay** gives deterministic recipe tests with a scripted model — no LLM calls in CI, which today's
  `concept-book` (no test runner at all) lacks.
- Cap and log every run (`max_llm_calls`, `~/book-graph/logs`) — currently only a subprocess's stdout.

Dependencies, not copies: `graflang` (runtime), `zinet` (graph algorithms, vis), `dd-llm` (adapters),
optionally `app-graph` (reuse `validate`, `drift` for graph quality gates).

### 2.4 One web app, many books

Replace "fork the app" with "build the app against a book":

- `bookgraph build <book.yaml>` → `dist/` for that book: copies `web/` assets, injects
  `catalog.json`, `config.js`-equivalent (title, base path, brand, resources), and the book's `public/domains/`.
- Per-book repos (if wanted for GitHub Pages) contain **only** `book.yaml`, `graphs/`, `content/`
  and a 10-line workflow `uses: digital-duck/book-graph` — no app code to drift.
- Frontend extension points stay as in `concept-book-base` (`config.js`, `router.js`, paths in `lib/paths.js`),
  but the one canonical copy lives in `web/`. Domain-specific pages (cb-zinets' Compare, Resources,
  idiom/character views) become **plugins** loaded from `book.yaml: site.plugins`.
- Publisher namespace (`{publisher}/{id}`) adopted from `conceptbook-app` so a single deployment can
  host many books later without a path migration.

### 2.5 CLI

| Command | Purpose |
|---|---|
| `bookgraph new <slug>` | scaffold `book.yaml` + `graphs/` (no fork, no `npm install` per book) |
| `bookgraph ingest <pdf\|openstax-slug> [--chapters]` | → `chunks.yaml` |
| `bookgraph extract <chunks> -o graph.yaml` | recipe `extract_graph` |
| `bookgraph validate <book.yaml\|graph.yaml>` | zinet/appgraph checks: acyclic, reducible, connected, tiers |
| `bookgraph generate <book.yaml> [--domain] [--target] [--level] [--lang] [--model]` | recipe `generate_book`/`generate_concept`, resumable |
| `bookgraph build <book.yaml>` | static site into `dist/` |
| `bookgraph serve <book.yaml>` | dev web + local API (on-demand Generate button) |
| `bookgraph publish <book.yaml>` | build + deploy, GUARDed |
| `bookgraph run <book.yaml>` | the full `ingest→extract→validate→generate→build→publish` chain (replaces `cb_pipeline publish run`) |
| `bookgraph migrate <cb-repo>` | import an existing `cb-*` repo (see §4) |

---

## 3. Mapping the four repos

| Today | Becomes |
|---|---|
| `concept-book-base` `src/`, `api/`, `scripts/`, `locales/` | `book-graph/web/` + `src/bookgraph/server/` + `build/` (one copy) |
| `concept-book-base` `spl/build_concept_book.spl`, `tools.py`, `style_profiles.py`, `level_*` | `recipes/generate_*` + `levels.py`/`styles.py` + `build/` |
| `concept-book-base` `scripts/concept_graph.py` (60 KB) | `zinet.vis` / `graph/` (drop the fork) |
| `concept-book-press` `pipeline/{ingest,extract,validate}` | `ingest/`, `recipes/extract_graph`, validate via zinet |
| `concept-book-press` `pipeline/publish/*` (SQLite stages, repo_init fork) | `bookgraph run` + GraphLang checkpoints; `repo_init` no longer forks |
| `conceptbook-app` auth/DB/personalization | **not** in v1; remains its own product, consuming `book-graph` as the content engine and `book.yaml` as the catalog entry (publisher namespace already compatible) |
| `concept-book` (multi-domain demo) | a `book.yaml` with many `domains:`; stays as the showcase, built by `bookgraph` |
| `cb-*` repos (12) | `book.yaml` + graphs + generated content; built with `bookgraph build` |
| `cb-zinets` | a book with `site.plugins: [zinets]` — keeps its sqlite/pinyin tooling as a plugin, not a fork |

---

## 4. Migration (incremental, no big bang)

**Phase 0 — Decide scope (this doc).** Confirm: new repo name `book-graph`; package `bookgraph`;
depends on graflang/zinet; conceptbook-app stays separate.

**Phase 1 — Skeleton + book.yaml.** Repo, `pyproject`, `BookSpec`, `validate`, `build` that
reproduces **one** existing book (`cb-calculus`, simplest) byte-for-byte-ish from `book.yaml` + its existing
`public/domains/`. Success: `bookgraph build` output renders identically to today's `dist/`.
*Gate:* no generation yet — pure packaging.

**Phase 2 — Single web app.** Consolidate `concept-book-base/src` (the superset: i18n, model-aware
paths, path_safety) into `web/`. Diff `cb-chemistry`/`cb-linalg` customizations; fold wanted ones into
core or into plugins; discard the rest. Add Vitest/Playwright smoke test.

**Phase 3 — Generation as recipes.** Port `build_concept_book.spl` →
`recipes/generate_concept` + `generate_book`. Keep prompts verbatim (write_section / refine / payoff);
verify with `record`/`replay` against existing generated HTML for a few nodes. Port the cache keys
(language, style/level, model, defines-hash) — they encode hard-won bug fixes (see the comments in
the `.spl`); don't re-derive them.

**Phase 4 — Ingestion/extraction.** Move `pipeline/{ingest,extract}`; extraction becomes a
recipe with validate+repair loop (borrowing app-graph's `validate_repair` part). Keep the
"cross-chapter concept registry" and the `composed_of: []` → primitive fix as deterministic steps.

**Phase 5 — Publish + migrate.** `bookgraph publish`; `bookgraph migrate cb-<x>` (reads
`catalog.json`, `public/domains/*`, `.env`, `vite.config.js` base → emits `book.yaml`). Migrate all
`cb-*` one at a time; each old repo gets a README pointer and is then frozen. Retire
`concept-book-base`.

**Phase 6 — Optional.** Multi-book portal; hook `conceptbook-app` to `book-graph` content;
`drift` checks between re-extracted graphs.

Each phase ships independently; the existing repos keep working until Phase 5.

---

## 5. Risks & open questions

| Risk / question | Mitigation / need decision |
|---|---|
| Ported recipes change prompt behaviour → regenerated content differs | Keep prompts verbatim; replay-test; never regenerate existing content during migration (cache keys must match) |
| `conceptbook-app` already solves multi-domain; is `book-graph` duplicating it? | Position `book-graph` as the **content/build engine + static publisher**; `conceptbook-app` = hosted multi-tenant product that consumes it. Confirm that split. |
| Static GitHub Pages per book vs one portal | `book.yaml` supports both; build per book first (matches today's URLs `digital-duck.github.io/cb-*`) |
| `cb-zinets` has bespoke sqlite/pinyin pages | Plugin mechanism in `web/` + `build/` (Phase 2); if too heavy, leave `cb-zinets` out of scope (you already excluded it) |
| graflang is "early prototype, private" | App-graph already depends on it at `≥0.3`; pin the version; deterministic steps live in plain Python so a runtime swap is possible |
| Where does generated content live (git bloat: HTML per level×lang×model) | Content repo or branch per book, or `gh-pages` only; decide before Phase 5 |
| Backend holds user LLM keys | Preserve `127.0.0.1` bind, `path_safety`, in-memory settings — move as-is, do not relax |
| Naming overlap: `app-graph`, `work-graph`, `book-graph`, `data-graph` | Consistent family; each is a GraphLang app. Fine, but document the family in graflang README |

### Decisions needed from you

1. Is `conceptbook-app` the long-term portal (so `book-graph` stays headless + static), or should `book-graph` subsume it?

[WEN] leave conceptbook-app alone

2. One repo containing engine + web, or split `bookgraph` (Python) and `bookgraph-web`? (Recommendation: one repo for now.)

[WEN] one repo, created at ~/projects/digital-duck/book-graph locally, 
https://github.com/digital-duck/book-graph remotely


3. Content location: per-book content repos vs. gh-pages-only vs. a shared `books/` monorepo?

[WEN] will be similar to  conceptbook-app where many concept-books are already aggregated 

4. Should `book.yaml` allow many graphs per book (chapters as domains, as press does today), or force one graph per book with chapters as sub-graphs (cf. `suggest-graph-clusters`)? (Recommendation: many, matching existing `linalg_ch01..08`.)

[WEN] domain is the wrong term for "chapter", if possible, let us adopt traditional terms such as publisher press > book > chapter

5. OK to drop SPL entirely in this repo (recipes only)?

[WEN] drop SPL

6. what to do with cb-zinets

[WEN] leave it alone

---

## 6. Success criteria

- Creating a new book = `bookgraph new` + `bookgraph run` — **no fork, no per-repo scripts**.
- A bug fix in the engine reaches every book by upgrading one package.
- Every existing `cb-*` book rebuilds from `book.yaml` with unchanged URLs and unchanged cached content.
- Recipe tests run offline with a scripted model.
- `concept-book-base` and `concept-book-press/pipeline/publish` are retired.
