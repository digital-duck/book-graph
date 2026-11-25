# Setup

How to install book-graph (the command is `bookgraph`, alias `bgr`) and get a working LLM backend. For a guided walk-through
see [Tutorial.md](Tutorial.md); for everything else, [User-Guide.md](User-Guide.md).

## 1. Prerequisites

| Need | Version | Check | For |
|---|---|---|---|
| conda (Anaconda or Miniconda) | any | `conda --version` | the `graf` environment |
| git | any | `git --version` | the code, and `publish` |
| Python | 3.11 or 3.12 (comes with the env) | `python --version` | everything |
| Node.js with npm | 18 or newer | `node --version` | `build`, `serve`, `publish`, Export PDF |
| Claude Code CLI, logged in | any recent | `claude --version` | the default LLM backend, `claude_cli` |

Graph validation, teaching order, migration and export need neither Node nor an LLM. Generation and extraction need an LLM
(section 5). The web app needs Node: book-graph builds it with Vite.

## 2. Get the code

book-graph is one of the GraphLang apps (app-graph, work-graph, data-graph, book-graph). It runs on three shared packages
that live in their own repositories. Clone them side by side:

```bash
mkdir -p ~/projects/digital-duck && cd ~/projects/digital-duck
git clone git@github.com:digital-duck/zinet.git        # graph algorithms (teaching order, reducibility)
git clone git@github.com:digital-duck/dd-llm.git       # LLM adapters
git clone git@github.com:digital-duck/graflang.git     # the GraphLang runtime (package `graphlang`)
git clone git@github.com:digital-duck/book-graph.git
```

## 3. Create the environment and install

Use the shared `graf` environment, and give it Python and pip explicitly:

```bash
conda create -n graf python=3.11 pip -y
conda activate graf
which python                     # must be .../envs/graf/bin/python
```

> `conda create -n graf` without `python=3.11 pip` makes an environment with no Python, and your shell then falls
> through to the base environment's pip.

Install the shared packages first, then book-graph, all as editable installs (`git pull` then updates them):

```bash
cd ~/projects/digital-duck
python -m pip install -e zinet -e dd-llm -e graflang
python -m pip install -e "book-graph[dev]"
```

The install must be a real one, not a `PYTHONPATH`. The recipes' deterministic steps run as `python3 -I -m bookgraph.tool`,
which ignores `PYTHONPATH`.

Optional, for `bgr ingest` (reading chapters from PDF textbooks):

```bash
python -m pip install -e "book-graph[ingest]"     # PyMuPDF
```

> PyMuPDF is licensed AGPL-3.0, and book-graph is Apache-2.0. That is why it is an optional extra, imported only by
> `bgr ingest`. Skip it if you only author graphs yourself (the Tutorial's path A).

## 4. Verify

```bash
bgr --version                     # bookgraph, version 0.1.0
bgr --help                        # the commands
graf validate ~/projects/digital-duck/book-graph/src/bookgraph/recipes/generate_chapter/generate_chapter.graph.yaml
cd ~/projects/digital-duck/book-graph && pytest -q
```

`pytest` runs offline: a scripted model, no LLM calls, about 3 minutes, because every recipe step is a subprocess. Two
tests build the web app and drive it in a browser. They are opt-in:

```bash
BOOKGRAPH_WEB_TESTS=1 pytest tests/test_build.py tests/test_serve.py
```

## 5. The LLM backend

A model is named `ADAPTER:MODEL`, in `book.yaml` (`models: [claude_cli:claude-sonnet-5-5]`) or with `--llm`. The adapters
are dd-llm's.

| Adapter | Needs | Example |
|---|---|---|
| `claude_cli` (the default) | the Claude Code CLI, logged in; no API key | `claude_cli:claude-sonnet-5-5` |
| `anthropic` | `ANTHROPIC_API_KEY` | `anthropic:claude-sonnet-5-5` |
| `openai` | `OPENAI_API_KEY` | `openai:gpt-5` |
| `gemini` | `GEMINI_API_KEY` | `gemini:gemini-2.5-pro` |
| `openrouter` | `OPENROUTER_API_KEY` | `openrouter:anthropic/claude-sonnet-5-5` |
| `ollama` | a local Ollama server | `ollama:qwen2.5:7b` |
| `mock` | nothing (GraphLang's offline stand-in; pages contain placeholder text) | `mock` |

Try the backend once, on the starter book, before a real book. `mock` costs nothing; a real model makes about 4 calls on
the starter graph:

```bash
bgr new acme demo
bgr generate acme/demo --llm mock
bgr generate acme/demo --llm claude_cli:claude-sonnet-5-5
```

A model's pages go into a folder named after the model part (`claude-sonnet-5-5`, `qwen2-5-7b`). The cache key keeps the
whole `ADAPTER:MODEL`, so two adapters serving the same model name do not share cached sections.

## 6. The web app's dependencies

The first `bgr build` (or `serve`, or `publish`) installs the web app's npm packages once, into
`~/book-graph/web/<hash of package-lock.json>/`. Every book then shares them, so a build takes a few seconds. Export PDF
(in `bgr serve`) and the browser tests also need Playwright's Chromium:

```bash
cd ~/book-graph/web/*/ && npx playwright install chromium
```

## 7. Where things are written

| Path | What | Change with |
|---|---|---|
| `~/book-graph/library/` | the library: `<press>/<book>/book.yaml`, chapters, pages, `catalog.json` | `--library/-L`, or `BOOKGRAPH_LIBRARY` |
| `<book>/chapters/<chapter>/` | `graph.yaml`, `chunks.yaml`, `output/` (pages, navigator), `.bg_cache/` (sections) | |
| `<book>/.bg_runs/` | the generate runs' ledgers (`bgr runs`, `--resume`) | |
| `<book>/pipeline/` | the pipeline's step manifests and logs | |
| `<book>/dist/` | the built site | `--out` |
| `~/book-graph/logs/` | recipe run logs | |
| `~/book-graph/web/`, `~/book-graph/build/` | the web app's npm packages, and build stages | `--stage` |

Generated files may only be written under your home folder or the temporary folder; a policy in the recipes enforces it.
To use a library elsewhere (a data disk, say), list its root in `BOOKGRAPH_WRITE_ROOTS` (`os.pathsep`-separated).

## 8. Publishing to GitHub Pages (optional)

`bgr publish` pushes a built site to a branch, `gh-pages` by default, over git. With `publish: {repo: OWNER/NAME}` the
remote is `git@github.com:OWNER/NAME.git`, so set up SSH access to GitHub first (`ssh -T git@github.com`). The push uses
your SSH keys or agent; nothing else of your environment is passed to it. Try `bgr publish BOOK --dry-run` first.

## Troubleshooting

| Symptom | Cause, and fix |
|---|---|
| `ModuleNotFoundError: bookgraph` inside a recipe step | book-graph is not installed in this env (a `PYTHONPATH` does not reach `python3 -I`): `python -m pip install -e book-graph` |
| `refused by policies/generate.cedar` | the library is outside your home and `/tmp`: add its root to `BOOKGRAPH_WRITE_ROOTS` |
| `npm is not on PATH` | install Node.js 18+ (`node --version`), then retry `bgr build` |
| `bookgraph ingest needs PyMuPDF` | `python -m pip install -e "book-graph[ingest]"` |
| Export PDF fails in `bgr serve` | install Chromium for Playwright (section 6) |
| `no model: list one under 'models:'` | add `models: [claude_cli:...]` to book.yaml, or pass `--llm` |
| `claude` not found / not logged in | install Claude Code and run `claude` once to log in, or use another adapter |
