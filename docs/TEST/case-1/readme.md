# Test case 1: path B on one small chapter of OpenStax *Chemistry 2e*

| Item | Description |
|---|---|
| Goal | Run the pipeline's **path B** (a chapter extracted from a PDF) on a real textbook, one step and one review at a time |
| Book | `openstax/chemistry-2e` in the default library, `~/book-graph/library/openstax/chemistry-2e/` |
| Chapter | `ch15`, *Chapter 15 Equilibria of Other Reaction Classes*: the smallest chapter of the book (5 sections, 67,071 characters) |
| Source | `~/projects/digital-duck/concept-book-press/input/chemistry-2e.pdf` (OpenStax, CC BY 4.0, 207.7 MB) |
| Model | `claude_cli:claude-sonnet-5-5` (book.yaml `models:`); **billed from step 2 on** |
| Status | **Steps 1 to 5 done and approved; 6-publish not run on purpose.** 8 model calls in all. See section 5. |

Why this chapter: every chapter of the two OpenStax PDFs was measured with book-graph's ingester. The smallest are chemistry-2e
ch15 (67,071 characters), ch13 (68,518) and ch16 (70,590). Pass 1 of extraction sends the whole chapter to the model, so the smallest
chapter is the cheapest real test.

## 1. What was run (2026-11-25)

### 1.1 The book

```bash
bgr new openstax chemistry-2e --title "Chemistry 2e"
rm -r ~/book-graph/library/openstax/chemistry-2e/chapters/ch01      # the starter chapter, not wanted here
```

`~/book-graph/library/openstax/chemistry-2e/book.yaml` was then written as follows. The chapter's `source:` is what puts it on path B.

```yaml
press: openstax
book: chemistry-2e
title: Chemistry 2e
levels: [core]
languages: [en]
models: [claude_cli:claude-sonnet-5-5]
tags: [chemistry]
source: {kind: openstax, url: "https://openstax.org/details/books/chemistry-2e", license: CC BY 4.0}
site: {base: /cb-chemistry-2e/, title: {en: Chemistry 2e}}
chapters:
  - id: ch15
    title: Equilibria of Other Reaction Classes
    graph: chapters/ch15/graph.yaml
    source: {pdf: /home/papagame/projects/digital-duck/concept-book-press/input/chemistry-2e.pdf, chapter: 15}
```

### 1.2 Before any step

```
$ bgr validate openstax/chemistry-2e
FAIL ch15: no graph at .../openstax/chemistry-2e/chapters/ch15/graph.yaml      # expected: on path B the graph comes from 2-extract

$ bgr status openstax/chemistry-2e
READY  ch15       1-ingest    -                          not run yet
       ch15       2-extract   -                          1-ingest not approved (or stale)
       ch15       3-validate  -                          2-extract not approved (or stale)
       ch15       4-generate  -                          3-validate not approved (or stale)
       book       5-build     -                          4-generate not approved (or stale) for ch15
       book       6-publish   -                          5-build not approved (or stale)
```

### 1.3 Step 1, 1-ingest (in the background, about 2 s)

```
$ bgr next openstax/chemistry-2e
started 1-ingest ch15 (log: .../openstax/chemistry-2e/pipeline/logs/ch15.1-ingest.log)
```

The manifest, `pipeline/ch15/1-ingest.out.yaml`:

```yaml
step: 1-ingest
book: openstax/chemistry-2e
chapter: ch15
run: 20261125-180007-2c46
status: OK
inputs:
  pdf: 217794376:1788638372803790559        # the PDF by size and modification time
  chapter: '15'
summary: 5 section(s), 67071 characters from chemistry-2e.pdf chapter 15 (Chapter 15 Equilibria of Other Reaction Classes)
details:
  chunks: .../openstax/chemistry-2e/chapters/ch15/chunks.yaml
  chapter_title: Chapter 15 Equilibria of Other Reaction Classes
review:
  status: pending
```

The output, `chapters/ch15/chunks.yaml`:

| Section | Pages | Characters | Share |
|---|---|---:|---:|
| 15.1 Precipitation and Dissolution | 747–760 | 23,733 | 35% |
| 15.2 Lewis Acids and Bases | 761–764 | 6,023 | 9% |
| 15.3 Coupled Equilibria | 765–769 | 9,317 | 14% |
| Summary (key terms, key equations) | 770 | 3,733 | 6% |
| Exercises | 771–780 | 24,265 | 36% |

Attribution: *"Concept graph extracted from: Chemistry 2E. Original textbook by OpenStax, licensed under CC BY 4.0. …"* (correct for this book).

## 2. Review findings on step 1

These come from concept-book-press's ingester, which book-graph ports verbatim; they are not new bugs.

1. **Exercises are included: 24,265 characters, 36% of the chapter.** press skips sections named "Problems & Exercises" (the physics
   book's name), but *Chemistry 2e* calls them "Exercises", so they come through. They are practice problems whose equations are
   missing (see 3), so they add cost to pass 1 and little meaning. Skipping them would bring pass 1 from about 67k to about 43k
   characters.
2. **Sections leak into each other at page boundaries.** A section is cut by pages, but a section can start mid-page. So 15.2 begins
   with the end of 15.1's worked example: *"of CdS is 1.0 10–28. Solution This calculation can be performed using the ICE approach…"*.
3. **Math and equations are lost by the PDF's text layer.** "1.0 × 10⁻²⁸" comes out as "1.0 10–28". Displayed equations are missing
   (*"solve the simplified equation for x:"* is followed by nothing), and the exercises' chemical equations are empty
   (*"(a) (b) (c) (d) (e)"*). The concepts survive; the formulas do not. A fix would need a math-aware PDF reader, so there is none here.

A smaller one: the attribution names the book "Chemistry 2E", title-cased from the file name, rather than *Chemistry 2e*.

## 3. What you do next

Run everything from any folder; the library is the default one. Check where you are at any time with:

```bash
bgr status openstax/chemistry-2e
```

### Step 1: review 1-ingest (now)

Read the chunks, and decide.

```bash
less ~/book-graph/library/openstax/chemistry-2e/chapters/ch15/chunks.yaml
cat  ~/book-graph/library/openstax/chemistry-2e/pipeline/ch15/1-ingest.out.yaml
```

Check:

- the 5 sections and their page ranges are as in the table above;
- 15.1 starts with the chapter introduction, which is expected, since it comes before the first section;
- findings 1 to 3 of section 2, and whether you accept them for this test.

Then choose one path.

- **Path (a), accept as it is.** Extraction runs on all 67k characters, exercises included:

  ```bash
  bgr approve openstax/chemistry-2e 1-ingest -c ch15 -m "accepted with exercises; see case-1 findings"
  ```

- **Path (b), fix the ingester first.** Ask for findings 1 and 2 to be fixed: skip "Exercises", and cut sections at their headings.
  Changed chunks change the fingerprint, so the next `bgr next` reruns 1-ingest by itself. Review it again, then approve as in
  (a).

### Step 2: 2-extract (billed: about 3 model calls)

```bash
bgr next openstax/chemistry-2e --dry-run      # must say: would start 2-extract ch15
bgr next openstax/chemistry-2e                # starts it in the background
bgr status openstax/chemistry-2e              # until 2-extract is no longer RUNNING
tail -f ~/book-graph/library/openstax/chemistry-2e/pipeline/logs/ch15.2-extract.log
```

- **What to expect:** pass 1 (the concepts, with the whole chapter as input), pass 2 (the prerequisites), and one repair call only if
  validation fails. About 3 calls.
- **The outputs:** `chapters/ch15/graph.yaml` and `chapters/ch15/concept_sources.yaml`. If the graph is still invalid after the repair,
  you get `graph.draft.yaml` and a NOT_OK manifest instead.
- **Review:**

  ```bash
  cat ~/book-graph/library/openstax/chemistry-2e/pipeline/ch15/2-extract.out.yaml        # summary: nodes, what was normalized, calls
  less ~/book-graph/library/openstax/chemistry-2e/chapters/ch15/graph.yaml
  bgr order ~/book-graph/library/openstax/chemistry-2e/chapters/ch15/graph.yaml          # the teaching order it implies
  ```

  Check:
  - the primitives are things the chapter takes as given (equilibrium constant, solubility, …);
  - the concepts match 15.1 to 15.3;
  - the applications are real uses;
  - every `defines` is faithful to the text, and the labels are readable.

  **Edit `graph.yaml` by hand if needed:** an edit is a new input, so 3-validate will see it.
- **Then:** `bgr approve openstax/chemistry-2e 2-extract -c ch15 -m "..."`. Or, to draw a new sample:
  `bgr reject openstax/chemistry-2e 2-extract -c ch15 -m "why"`, then `bgr next`.

### Step 3: 3-validate (no model)

```bash
bgr next openstax/chemistry-2e --foreground
bgr status openstax/chemistry-2e
```

- **OK:** read the warnings in the manifest (a capstone that is not an application, tiers that do not rise along an edge), then
  `bgr approve openstax/chemistry-2e 3-validate -c ch15`.
- **NOT_OK:** the manifest lists the problems. Fix `graph.yaml`; the next `bgr next` reruns it by itself.
- **Before approving, set the chapter's capstone** in book.yaml (`capstone: <an application id>`); otherwise the last application in
  teaching order is used. This changes the input, so 3-validate reruns: review it again.

### Step 4: 4-generate (billed: about one call per section, plus the payoff)

`bgr order` from step 2 shows how many sections will be written: one call each, a second for any section that fails the primitive
check, and one for the payoff.

```bash
bgr next openstax/chemistry-2e
bgr status openstax/chemistry-2e
bgr serve openstax/chemistry-2e         # http://127.0.0.1:8200/cb-chemistry-2e/ : read the pages and the navigator
```

- **If it stops** (a rate limit, a failure), its manifest is NOT_OK. `bgr retry openstax/chemistry-2e 4-generate -c ch15`, then
  `bgr next`, resumes the run: finished sections come from the cache.
- **Review:** read a few sections and the payoff in the browser, then `bgr approve openstax/chemistry-2e 4-generate -c ch15`.

### Steps 5 and 6: build and publish (optional for this test)

```bash
bgr next openstax/chemistry-2e --foreground                   # 5-build -> <book>/dist

bgr serve openstax/chemistry-2e --no-build                    # preview the built site; Ctrl-C to stop

bgr approve openstax/chemistry-2e 5-build -m "previewed"      # its approval is publish's confirmation

```

**Preview before approving 5-build.** `bgr serve` runs in the foreground on `127.0.0.1:8200` (`-p` changes the port); open
<http://127.0.0.1:8200/cb-chemistry-2e/> (the path is `site.base` in book.yaml).

- `--no-build` serves the `dist/` that 5-build just made. Without it, `serve` rebuilds `dist/` first.
- Check: the home page lists the book and chapter; the one generated page, `book_wastewater_phosphate_precipitation`, opens and
  reads well; the navigator links work.
- **Avoid the Generate button** on the other nodes: it makes billed model calls, which this test's call count does not expect.
  Settings (API keys included) stay in memory only.

**Do not run 6-publish in this test.** book.yaml has no `publish:` target, so 6-publish ends NOT_OK, saying there is nowhere to
publish. That is expected, and harmless.

## 4. Undo, or start over

The test only writes inside the book folder: the PDF and concept-book-press are only read.

```bash
rm    ~/book-graph/library/openstax/chemistry-2e/pipeline/ch15/2-extract.out.yaml   # forget one step (it becomes "not run yet")
rm -r ~/book-graph/library/openstax/chemistry-2e                                     # remove the whole test book
bgr catalog                                                                          # refresh ~/book-graph/library/catalog.json
```

To reproduce step 1 from scratch, repeat section 1.1, then `bgr next openstax/chemistry-2e`.

## 5. Results

| Step | Date | Status | Model calls | Review (approved / rejected, note) |
|---|---|---|---:|---|
| 1-ingest | 2026-11-25 | OK | 0 | approved 21:19, path (a): "accepted with exercises; see case-1 findings" |
| 2-extract | 2026-11-25 | OK (34 s) | 2 | approved 21:23, "extract step passed"; no repair call needed |
| 3-validate | 2026-11-25 | OK | 0 | approved 21:25; 23 nodes, no problems, no warnings; capstone not set |
| 4-generate | 2026-11-25 | OK (75 s) | 6 | approved 21:29; one page built (see notes) |
| 5-build | 2026-11-25 | OK | 0 | approved; site in `dist/` |
| 6-publish | - | not run | - | skipped on purpose: book.yaml has no `publish:` target |

Total: 8 model calls (`claude_cli:claude-sonnet-5-5`). The whole path B ran end to end, one step and one review at a time.

Notes on the graph and pages, and on anything that went wrong:

- Step 1 was accepted as it was, so findings 1 to 3 of section 2 (exercises included, sections leaking at page boundaries, math lost) were not fixed.
- Step 2 used 2 calls, not the readme's estimate of about 3, because validation passed first time. It made a graph of 23 nodes and normalized one thing: `solubility_equilibrium` was a concept with no prerequisite, so it was moved to primitives.
- Step 3 ran with no `capstone:` in book.yaml, so the last application in teaching order was used.
- Step 4 built a single page, `book_wastewater_phosphate_precipitation.html` (variant `college.en`), not the whole chapter: 1 variant done, 6 calls. The estimate in section 3 ("one call per section plus the payoff") assumes a whole-chapter run and overstates a single-target run.
- Step 5 built `dist/` with `index.html`, `assets/` and `domains/`.
- Nothing went wrong: no repair, no retry, no resume.
