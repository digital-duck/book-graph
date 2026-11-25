# CLAUDE.md

Guidance for Claude Code in this repo. book-graph follows app-graph / work-graph / data-graph: a Python package over GraphLang
recipes, `click` CLI. The plan is `~/projects/digital-duck/concept-book/docs/DEV/readme-book-graph-plan.md` (the [WEN] answers there
are decisions: leave conceptbook-app and cb-zinets alone, one repo, SPL dropped, press > book > chapter instead of "domain").

## Commands

```bash
pip install -e ".[dev]"      # editable: the recipe steps run as `python3 -I -m bookgraph.tool`, which ignores PYTHONPATH, so the package must be installed
pytest                        # offline: a scripted model, no LLM calls (~1 min: each recipe step is a subprocess)
graf validate src/bookgraph/recipes/generate_chapter/generate_chapter.graph.yaml
bgr --help
```

## Architecture (`src/bookgraph/`)

- **`book.py`** `book.yaml` -> `Book`/`Chapter`; `scaffold_book`. **`paths.py`** the library tree, in one place; every path segment goes through `safe_segment`.
- **`graph.py`** graph.yaml -> `ChapterGraph` (networkx + `zinet.algorithms`): `validate`, `plan` (teaching order; matches the old apps' order exactly), per-node label/context. No private `graph_lib` copy; do not add one.
- **`steps.py` + `tool.py`** the recipe's deterministic steps (JSON in, JSON out, no model). **`pages.py` + `render.py`** HTML (the old apps' converter and templates, kept so pages look alike). **`cache.py`**, **`catalog.py`** (flock + atomic write), **`styles.py`** (verbatim from concept-book-base), **`levels.py`**.
- **`run.py`** thin layer over `graphlang.recipes` (log dir `~/book-graph/logs`, default model `claude_cli`). **`builder.py`** `generate_chapter`/`generate_book`. **`cli.py`** `bookgraph`/`bgr`.
- **`recipes/generate_chapter/`** the GraphLang recipe (port of `build_concept_book.spl`) with `prompts/` (the SPL prompts, only the parameters renamed to `{{in_x}}`), `policies/generate.cedar`, `graflang.errors.yaml`.

## Rules that bit (GraphLang)

A `$loop.X` must be read under the same name `X` (so the loop input is `style_guide`, and the prompt's input is `style_guide`/`language_label`);
a SOLVE subprocess runs with only `PATH`/`PYTHONUTF8`, so pass absolute paths; `on: [a, b]` fires once per arriving token (exclusive branches only);
a `risky: true` step is reachable only through a GUARD's `allow`. Every step that writes files is `risky: true` behind the `write_ok` policy; keep it so.

## Known gaps vs. the SPL workflow

- The symbolic `verify_section` (sympy/z3 per domain) is not ported; only the primitive-budget check runs.
- The section cache has its own key scheme; the old spl3 cache is not read.
- Not built yet: ingestion/extraction (from concept-book-press), the web app, `publish`, `migrate`.
