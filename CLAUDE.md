# CLAUDE.md

Guidance for Claude Code in this repo. book-graph follows app-graph / work-graph / data-graph: a Python package over GraphLang
recipes, `click` CLI. The plan is `~/projects/digital-duck/concept-book/docs/DEV/readme-book-graph-plan.md` (the [WEN] answers there
are decisions: leave conceptbook-app and cb-zinets alone, one repo, SPL dropped, press > book > chapter instead of "domain").

## Commands

```bash
pip install -e ".[dev]"      # editable: the recipe steps run as `python3 -I -m bookgraph.tool`, which ignores PYTHONPATH, so the package must be installed
pytest                        # offline: a scripted model, no LLM calls (~3 min: each recipe step is a subprocess)
BOOKGRAPH_WEB_TESTS=1 pytest tests/test_build.py tests/test_serve.py   # opt-in: real builds driven in Chromium (Generate, PDF)
graf validate src/bookgraph/recipes/generate_chapter/generate_chapter.graph.yaml
bgr --help
```

## Architecture (`src/bookgraph/`)

- **`book.py`** `book.yaml` -> `Book`/`Chapter`; `scaffold_book`. **`paths.py`** the library tree, in one place; every path segment goes through `safe_segment`.
- **`graph.py`** graph.yaml -> `ChapterGraph` (networkx + `zinet.algorithms`): `validate`, `plan` (teaching order; matches the old apps' order exactly), per-node label/context. No private `graph_lib` copy; do not add one.
- **`steps.py` + `tool.py`** the recipe's deterministic steps (JSON in, JSON out, no model). **`pages.py` + `render.py`** HTML (the old apps' converter and templates, kept so pages look alike). **`cache.py`**, **`catalog.py`** (flock + atomic write), **`styles.py`** (verbatim from concept-book-base), **`levels.py`**.
- **`run.py`** thin layer over `graphlang.recipes` (log dir `~/book-graph/logs`, default model `claude_cli`). **`builder.py`** `generate_chapter`/`generate_book` (a resumable run). **`extract.py`** + **`recipes/extract_graph/`** `bgr extract`: concept-book-press's two passes (prompts verbatim, subject a parameter), then deterministic normalize (dep-less concept -> primitive, primitive with deps -> concept, unknown/self deps dropped, pass-1 labels kept, tiers from the edges) + validate, one repair call, `graph.draft.yaml` when still invalid. Parse YAML as-is first and repair only on failure: the line-wise repair breaks valid wrapped YAML. **`registry.py`** cross-chapter reuse: a node an earlier chapter (book.yaml order) declares gets concept-book-press's recap note in its context (prompt + library cache key; the legacy lookup keeps the plain context); `recap_earlier_chapters: false` turns it off. **`runs.py`** the run ledger, `<book>/.bg_runs/<id>.json`: `--resume` skips done variants, and the section cache resumes inside one (GraphLang checkpoints are not used: a LOOP is one checkpoint step). **`legacy.py`** read-only lookup in an SPL app's spl3 cache. **`navigator.py`** the chapter navigator page (verbatim port). **`serve.py`** `bgr serve`: the built app plus the old FastAPI's `/api/*` contract (generate as SSE, pdf, settings, domains) on the stdlib HTTP server, `domains/` served live from the library; 127.0.0.1 only, Host and same-origin checks, keys in memory only. **`publish.py`** + **`recipes/publish/`** `bgr publish`: a fresh one-commit force-push of the built site to `publish:`'s branch, behind `policies/publish.cedar` (site built, remote reachable, not the default/main/master branch, base path matches where it is served); `check`/`deploy` get `HOME`/`SSH_AUTH_SOCK` through `env_passthrough`. **`build.py`** + **`web/`** `bgr build`: the one web app (concept-book-base's frontend at `4c88c04`, three extension points in `src/config.js`/`vite.config.js`) staged with the book's export and `site:` strings, then `vite build`; node_modules installed once per package-lock under `~/book-graph/web/`. **`export.py`** `export_book` (a library book -> an app's `public/domains/` + its flat `catalog.json`, under the apps' `catalog.json.lock`) and `migrate_repo` (a `cb-*` repo -> the library; old ids kept as `global_id`). **`cli.py`** `bookgraph`/`bgr`.
- **`recipes/generate_chapter/`** the GraphLang recipe (port of `build_concept_book.spl`) with `prompts/` (the SPL prompts, only the parameters renamed to `{{in_x}}`), `policies/generate.cedar`, `graflang.errors.yaml`.

## Rules that bit (GraphLang)

A `$loop.X` must be read under the same name `X` (so the loop input is `style_guide`, and the prompt's input is `style_guide`/`language_label`);
a SOLVE subprocess runs with only `PATH`/`PYTHONUTF8`, so pass absolute paths;
a `risky: true` step is reachable only through a GUARD's `allow`; a step cannot read the caller's environment, so anything it needs (`BOOKGRAPH_WRITE_ROOTS`) is passed in as a recipe input by `builder.py`; `on: [a, b]` joins fire once per token, so a step that must run once is fed by ONE upstream step (the payoff is cached *before* `p_final`). Every step that writes files is `risky: true` behind the `write_ok` policy; keep it so.

## Known gaps vs. the SPL workflow

- The SPL's `verify_section` is not ported because it never checked anything: `graph_lib.verify_content` only imports sympy (its claim check is a TODO) and always passed, and the exact verifiers in `graph_lib.py` (right triangle, momentum, ...) were never called. Only the primitive-budget check runs. A real check would be new behaviour (and new refinement calls), not a port.
- The section cache has its own key scheme (language, style, full `adapter:model`, a hash of label+defines, and a salt over the prompt files, style guide and budget). An SPL app's spl3 cache is read, never written, through `legacy.py` when `book.yaml` sets `legacy_cache:`; a section hit is copied into `.bg_cache`, and a payoff hit is read again on each run.
- Model output is untrusted: `render.py` escapes all text, mermaid and display math, and passes only one exact `<figure>` shape. Keep it so when touching the converter.
- The navigator (`output/graph.html`) is `navigator.py`: concept-book-base's `concept_graph.py` page, ported **verbatim** (byte-identical; the app's GraphViewer.js scripts it through `frame.contentWindow`, so do not restyle it or swap in zinet's cytoscape page). It is rewritten when the graph's hash differs from `<chapter>/.navigator`; a migrated chapter keeps its own page until its graph changes.
- `web/` is the canonical frontend: change it there, keep its defaults equal to concept-book-base's, and keep per-book values in `book.yaml: site:` (never in `web/`). `BOOKGRAPH_WEB_TESTS=1 pytest -k browser` builds a site and drives it in Chromium (`tests/web/smoke.mjs`).
- `publish` tests push only to local bare repos under an isolated `HOME`; never point a test at a real remote.
- `serve` must stay on 127.0.0.1 with its Host and same-origin checks: its GET endpoints spend LLM calls, and it holds API keys (in memory only, never echoed). Keep the `/api/*` shapes: `web/` calls them unchanged.
- A failed model attempt counts as a model call (GraphLang's meter), so budgets count it. The recipe's model calls retry transient errors (`phases.exec.retry`, 15 s apart); tests patch `graphlang.grafr.lifecycle.time.sleep`.
- Not built yet: ingestion (OpenStax HTML / PDF -> chunks.yaml, from concept-book-press).
