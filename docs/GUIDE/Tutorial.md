# Tutorial: a concept book from a graph

A hands-on walk through book-graph, about 30 minutes. You will write a small concept graph for one chapter of a physics
book, generate its pages, look at them in the web app, then run the same book through the asynchronous pipeline, with a
review at every step, up to a published site. Two shorter parts follow: a chapter extracted from a PDF textbook, and an
existing `cb-*` concept-book repo brought into the library.

Install first with [Setup.md](Setup.md). The output below was produced by running the commands in order. Paths are shown
as `/tmp/bg-tutorial`, and run ids and timestamps will differ on your machine. The generation steps use GraphLang's
offline `mock` model, so the tutorial costs nothing. With a real model (`claude_cli:...`) the pages contain real prose,
and the number of model calls is the same.

Work in a scratch library, so your real one (`~/book-graph/library`) is not touched:

```bash
mkdir -p /tmp/bg-tutorial && cd /tmp/bg-tutorial
export BOOKGRAPH_LIBRARY=/tmp/bg-tutorial/library
```

## 1. A press, a book, a chapter

book-graph organizes content as **press > book > chapter**. A book is data in a library, not a repository: a
`book.yaml`, and a folder per chapter.

```bash
$ bgr new acme physics --title "Everyday Physics"
created /tmp/bg-tutorial/library/acme/physics
```

```yaml
# library/acme/physics/book.yaml
press: acme
book: physics
title: Everyday Physics
levels: [core]
languages: [en]
models: []                 # e.g. [claude_cli:claude-sonnet-5-5]
tags: []                   # math / physics / engineering select the rigorous research style
chapters:
  - {id: ch01, title: Chapter 1, graph: chapters/ch01/graph.yaml}
```

## 2. Write the chapter's concept graph (path A)

A chapter is a **concept graph**, made of three kinds of node:

- **primitives**: what the chapter takes as given;
- **concepts**: built from other nodes, listed under `composed_of`;
- **applications**: what the concepts are for, listed under `needs`.

Every node says what it `defines`. Replace the starter graph with this one:

```yaml
# library/acme/physics/chapters/ch01/graph.yaml
domain: physics_ch01
primitives:
  distance:
    label: Distance
    defines: How far something travels, measured in metres.
  time:
    label: Time
    defines: How long something takes, measured in seconds.
concepts:
  speed:
    label: Speed
    composed_of: [distance, time]
    defines: Distance travelled per unit of time.
  velocity:
    label: Velocity
    composed_of: [speed]
    defines: Speed together with the direction of motion.
  acceleration:
    label: Acceleration
    composed_of: [velocity, time]
    defines: How fast velocity changes per unit of time.
applications:
  braking_distance:
    label: Braking Distance
    needs: [acceleration, speed]
    defines: How far a car travels between the brakes going on and the car stopping.
```

This is **path A**: the graph is authored by an expert, a teacher, or any other means. Path B extracts it from a textbook
(part 7).

Check it, and see the order the sections will be written in:

```bash
$ bgr validate acme/physics
ok   ch01
$ bgr order library/acme/physics/chapters/ch01/graph.yaml
distance
time
speed
velocity
acceleration
braking_distance
```

`validate` checks several things:

- the graph has no cycle;
- every prerequisite is declared;
- every concept reduces to primitives;
- every node has a `defines`;
- no id is declared twice;
- the chapter's capstone is a node of the graph.

It also warns about a capstone that is not an application, and about tiers that do not rise along an edge. The **teaching
order** puts every node after its prerequisites, and early the nodes that unlock the most others.

Point the chapter at its capstone, the node its book is written toward, and choose the model. Edit book.yaml:

```yaml
models: [mock]            # for the tutorial; a real book uses e.g. [claude_cli:claude-sonnet-5-5]
chapters:
  - {id: ch01, title: Chapter 1, graph: chapters/ch01/graph.yaml, capstone: braking_distance}
```

## 3. Generate the pages

```bash
$ bgr generate acme/physics
ok   ch01 core.en mock (7 model calls) /tmp/bg-tutorial/library/acme/physics/chapters/ch01/output/core.en/mock/html/book_braking_distance.html
run 20261125-172547-cd8d: done (1 done; 7 model calls)
```

One section per node, in teaching order (6 calls), and a **payoff** for the capstone (1 call). For each section, the
recipe `generate_chapter`:

1. writes it;
2. checks it introduces no more than two new primitives, refining it once if it does;
3. caches it;
4. writes `concept_<node>.html`.

The chapter's book page, `book_braking_distance.html`, lists the sections and ends with the payoff. The pages are under
`output/<level>.<language>/<model>/html/`.

Run it again: everything comes from the cache.

```bash
$ bgr generate acme/physics
ok   ch01 core.en mock (0 model calls) .../book_braking_distance.html
run 20261125-172552-6d76: done (1 done; 0 model calls)
$ bgr runs acme/physics
20261125-172547-cd8d  2026-11-25 17:25  done       1/1 done  7 calls
20261125-172552-6d76  2026-11-25 17:25  done       1/1 done  0 calls
```

Every `generate` is a **run**, recorded in `<book>/.bg_runs/`. If a run stops part-way, `bgr generate acme/physics
--resume last` continues it. Causes include a failure, its `--budget`, or Ctrl-C (which prints the exact command). Only
what is not done runs, and the sections already written come from the cache. Add levels, languages or models to book.yaml
(`levels: [intro, core]`, `languages: [en, zh]`), and `generate` writes every chapter × level × language × model
combination.

## 4. Look at it

```bash
bgr serve acme/physics            # http://127.0.0.1:8200/physics/  (Ctrl-C to stop)
```

`serve` builds the web app against the book. The first time, it installs the app's npm packages, which takes about a
minute. It then serves the book on 127.0.0.1 with the local API behind the app's buttons.

- **The home page** lists the chapters.
- **A chapter page** shows its **navigator** (the interactive concept graph) and, when you click a node, its section.
- **Generate, Export PDF and Settings** work. Pages generated from the browser appear at once, because `serve` reads the
  library live.
- **API keys** typed into Settings stay in memory only.

`bgr build acme/physics` writes the same app as a static site to `<book>/dist/`.

## 5. The pipeline: a manifest per step, a review at every step

The same book can go through the **asynchronous pipeline**. Each step writes a manifest proving it completed (OK or
NOT_OK), and the next step starts only after a human has approved it:

```
path B (from a PDF):        1-ingest -> 2-extract -> 3-validate -> 4-generate ┐
path A (an authored graph):                          3-validate -> 4-generate ┤ every chapter
                                                                               └-> 5-build -> 6-publish
```

For the tutorial, publish to a local repository standing in for GitHub, and give the site a title. Add to book.yaml:

```bash
git init -q --bare -b main /tmp/bg-tutorial/site.git      # (it needs a first commit on main; any will do)
```

```yaml
publish: {remote: /tmp/bg-tutorial/site.git}
site: {base: /physics/, title: {en: Everyday Physics}, tagline: {en: Motion you can see}}
```

`bgr status` shows each chapter's steps and the book's, what is **READY**, and why the rest waits:

```bash
$ bgr status acme/physics
READY  ch01       3-validate  -                          not run yet
       ch01       4-generate  -                          3-validate not approved (or stale)
       book       5-build     -                          4-generate not approved (or stale) for ch01
       book       6-publish   -                          5-build not approved (or stale)
```

`bgr next` starts every ready step and exits. By default each step runs as a background process with its own log in
`<book>/pipeline/logs/`. `--foreground` runs them here instead, which is easier to follow in a tutorial:

```bash
$ bgr next acme/physics --foreground
ran 3-validate ch01
$ bgr status acme/physics
       ch01       3-validate  OK, review pending         awaiting review              6 nodes, valid
       ch01       4-generate  -                          3-validate not approved (or stale)
       ...
```

The step is OK, and it waits for you. Read its manifest, then approve it:

```bash
$ cat library/acme/physics/pipeline/ch01/3-validate.out.yaml
step: 3-validate
book: acme/physics
chapter: ch01
run: 20261125-172612-4cee
status: OK
started: 2026-11-25T17:26:12-0500
inputs:
  graph: d4274f831666db91
  capstone: braking_distance
finished: 2026-11-25T17:26:12-0500
summary: 6 nodes, valid
error: ''
details:
  problems: []
  warnings: []
  nodes: 6
review:
  status: pending
$ bgr approve acme/physics 3-validate -c ch01 -m "graph checked"
3-validate ch01: approved
```

Continue, step by step. At each one, look before approving. Generation's pages are in the chapter's `output/` (or use
`bgr serve`); after the build, preview the site:

```bash
$ bgr next acme/physics --foreground
ran 4-generate ch01
$ bgr approve acme/physics 4-generate -c ch01
4-generate ch01: approved
$ bgr next acme/physics --foreground
ran 5-build
$ bgr approve acme/physics 5-build -m "site previewed"
5-build: approved
$ bgr next acme/physics --foreground
ran 6-publish
$ bgr status acme/physics
       ch01       3-validate  OK, review approved        done                         6 nodes, valid
       ch01       4-generate  OK, review approved        done                         run 20261125-172613-05a0: done (1 done; 0 model calls)
       book       5-build     OK, review approved        done                         built /tmp/bg-tutorial/library/acme/physics/dist ...
       book       6-publish   OK, review pending         awaiting review              pushed 14 file(s) to /tmp/bg-tutorial/site.git gh-pages ...
```

`4-generate` made no model calls: the sections from part 3 were in the cache. The site is now the single commit on the
repository's `gh-pages` branch, which is what GitHub Pages serves.

**What makes it asynchronous, and resumable:**

- **Readiness.** `bgr next` starts a step only when the step before it is OK, **approved and current**, and its own
  manifest is missing, its inputs changed, or it was rejected or marked for retry. Run `bgr next` by hand, from cron, or
  as `bgr next acme/physics --watch 60`. Steps never start one another.
- **Inputs are fingerprinted.** Edit `graph.yaml` now, and `3-validate` becomes ready again, while everything after it
  waits until it is redone and approved: an approval counts only while its inputs are unchanged.
- **Failures.** A NOT_OK step is not rerun on its own. Fix its input, or `bgr retry acme/physics 4-generate -c ch01`.
  A generation that stopped resumes its run, so the finished sections are not paid for again.
- **Rejection.** `bgr reject acme/physics 4-generate -c ch01 -m "too terse"` runs the step again, and the note is kept
  in the manifest.
- **Skipping review.** To let a step through without one, list it in book.yaml: `pipeline: {auto_approve: [3-validate]}`.

## 6. Publishing to GitHub Pages

For a real site, replace the local remote with the GitHub repository and set the base path GitHub serves it at:

```yaml
publish: {repo: your-org/cb-physics}         # git@github.com:your-org/cb-physics.git, branch gh-pages
site: {base: /cb-physics/, title: {en: Everyday Physics}}
```

`bgr publish acme/physics --dry-run` goes through every check and pushes nothing. A policy, `policies/publish.cedar`,
refuses the push when:

- the site is not built;
- the remote cannot be reached;
- the branch is the remote's default (or `main`/`master`): a publish replaces the branch's whole history;
- the site was built for another base path than the one it will be served at.

`bgr publish` asks before replacing the live site. In the pipeline, approving `5-build` is that confirmation.

## 7. Path B: a chapter from a PDF textbook

With the `[ingest]` extra (Setup, section 3), a chapter can start from a textbook PDF that has bookmarks; OpenStax books
do. For example, OpenStax's *Chemistry 2e*, in a book of its own:

```bash
$ bgr new acme chem --title "Chemistry 2e"
$ bgr ingest acme/chem --pdf chemistry-2e.pdf --list
  1  p.23    Chapter 1 Essential Ideas
  2  p.73    Chapter 2 Atoms, Molecules, and Ions
  3  p.129   Chapter 3 Composition of Substances and Solutions
  ...
$ bgr ingest acme/chem --pdf chemistry-2e.pdf --chapter 3
wrote .../acme/chem/chapters/ch03/chunks.yaml: 6 section(s), 80938 characters
added chapter ch03 (Chapter 3 Composition of Substances and Solutions) to book.yaml; next: bookgraph extract acme/chem -c ch03
```

`bgr extract acme/chem -c ch03 --llm claude_cli:claude-sonnet-5-5` then turns the chunks into `graph.yaml`. It takes
two model passes: the concepts taught, then their prerequisites. The result is then cleaned up deterministically:

- a concept with no prerequisite becomes a primitive;
- dependencies on unknown ids are dropped;
- the labels from the first pass are kept;
- tiers are computed from the edges.

The graph is validated. If it fails, one more model call repairs it; a graph still invalid is kept as
`graph.draft.yaml`. Expect about 3 model calls per chapter. The first pass sends the whole chapter, here about 81,000
characters. Review the graph before generating from it: this is the cheapest place to fix a concept map.

In the pipeline, the chapter declares its source instead, and `1-ingest` and `2-extract` run as steps, each reviewed:

```yaml
chapters:
  - {id: ch03, title: Composition of Substances, graph: chapters/ch03/graph.yaml,
     source: {pdf: chemistry-2e.pdf, chapter: 3}}
```

## 8. Bring in an existing concept-book repo

The `cb-*` repositories (cb-calculus, cb-meta-health, …) each fork the whole web app for one book. `migrate` reads one
into the library, and `export` writes a book back out as an app's `public/domains/`:

```bash
bgr -L ~/book-graph/library migrate ~/projects/digital-duck/cb-meta-health
bgr -L ~/book-graph/library export digital-duck/meta-health /tmp/domains
```

The repo is only read. `migrate` copies the following:

- each chapter keeps its old id (`meta_health_ch01`) as its global id, so URLs do not change;
- the generated pages, the navigators and the catalog fields come along;
- `legacy_cache:` points at the repo's SPL cache, so sections generated there are reused. A chapter of cb-meta-health
  regenerates with 0 model calls.

Exported, every one of the 13 cb repos reproduces its `public/domains/` file for file.

## 9. What you have seen, and where next

- A book is `book.yaml` plus a graph per chapter. Pages, navigators, site and catalog are all generated from them.
- Generation is a GraphLang recipe: cached, policy-checked, resumable.
- The pipeline is a set of manifests that you review, step by step, and that `bgr next` advances.

The [User-Guide.md](User-Guide.md) has the details: the `book.yaml` and `graph.yaml` reference, levels and styles, the
caches, every command, the pipeline rules, the web app, and the safety rules of `serve` and `publish`.
