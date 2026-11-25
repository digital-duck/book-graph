# book-graph code review (Opus 5.5)

Date: 2026-11-25 · Commit reviewed: `30807a4` (main) · Scope: all of `src/bookgraph/`, the `generate_chapter` recipe, prompts,
policy and tests. I compared it with the sibling graf apps (app-graph, work-graph, data-graph) and checked it against the
GraphLang runtime (`graflang/graphlang/grafr/solver.py`) and zinet (`zinet/algorithms.py`).

**How I checked it.** I read all the code. `pytest` passes: 20 tests in about 34 s. `graf validate` on the recipe prints
"Valid GraphLang workflow" and exits 0 (an earlier note here said 1; that was my error, see §8). I built a wheel, and the recipe files are in
it. I ran scripted probes (no LLM) for each bug marked **confirmed** below.

---

## 1. Summary

book-graph is a small (~1.8 kLOC), clean and well-layered port of the SPL concept-book pipeline onto GraphLang. The
architecture is right: the deterministic steps are pure JSON-in/JSON-out functions, and the model calls live only in the
recipe. Paths go through one module, and the catalog writes are safe under concurrency. The tests run the real recipe end
to end with a scripted model.

The main problems are at the edges, not in the core:

- one recipe wiring bug (the index step runs twice);
- a documented escape hatch that cannot work (`BOOKGRAPH_WRITE_ROOTS`);
- subject-rigor inference that the new chapter layout silently broke;
- model names that the path rules reject (`qwen2.5:7b`, `claude-3.5-sonnet`);
- no HTML escaping of model output;
- crashes (uncaught `CycleError`) on cyclic graphs outside `generate`.

All of these are small fixes.

---

## 2. Strengths

| Area | What is good |
|---|---|
| Layering | `book` → `graph` → `steps`/`tool` → recipe → `run` → `builder` → `cli` is a clean one-way stack. No private `graph_lib` copy: the algorithms come from `zinet.algorithms`, as the plan wanted. |
| Recipe design | It follows the SPL flow closely: prepare → GUARD → LOOP (cache / write / check / one refine / store) → payoff → index. A broken graph stops in `prepare` before any model call, and a test covers that. Every step that writes files is `risky: true` behind the `write_ok` GUARD. |
| Determinism | Each step re-derives the teaching order in its own `python3 -I` subprocess, where hash randomization is on. That is safe: `productivity_order` breaks heap ties by node id (`zinet/algorithms.py:206-215`), so every subprocess computes the same order. I verified this; it was the main risk of the "recompute per step" design. |
| Path safety | `safe_segment` is applied to press, book, chapter, level, language and model. `write_root_ok` rejects relative paths and `..`, and resolves symlinks before comparing. |
| Catalog | `flock` plus write-to-tmp and `os.replace` (`catalog.py:21-48`), so concurrent runs cannot lose each other's updates. |
| Cache | The key includes a hash of the node's `defines`, so editing a definition invalidates the cached section. A refined section is cached as the final text. A run with some sections cached and some not works: I tested it, though no test covers it. |
| Compatibility | Old ConceptBook `graph.yaml` files load unchanged. The `input/graph.yaml` layout is recognised (`graph.py:53`). English pages keep unsuffixed names, so old links still work. |
| Tests | Offline, and they drive the real recipe through GraphLang: call counts, cache hits, a language as its own cache slot, the refinement path, a non-application target, a broken graph, and the catalog. |
| Docs | Docstrings say *why*, not just what. CLAUDE.md records the GraphLang rules that caused trouble. |

---

## 3. Bugs

Severity: **High** = wrong output or a feature that cannot work; **Medium** = a crash or wrong behaviour in a reachable
case; **Low** = an edge case or polish.

### B1 (High, confirmed): the index step runs twice whenever the payoff is generated
`generate_chapter.graph.yaml:156` has `index: on: [p_final, p_store]`. When the payoff is written fresh, `p_write` fires
both `p_final` (`:139`) and `p_store` (`:142`). Under GraphLang's rule that `on: [a, b]` fires once per arriving token
(CLAUDE.md), `index`, `path` and `done` each run **twice**.

My probe of an uncached run counted `p_final`×1, `p_store`×1, `index`×2, `path`×2, `done`×2. The book page is written
twice. Worse, the first `done` can end the run before `p_store` has cached the payoff (at `workers>1`, or if the runtime
changes its order). The double `on:` was probably meant as "wait for both", which this join cannot express.

**Fix:** make `p_store` a step on the path *before* `p_final` (`p_write → p_store → p_final`, with `p_final` reading
`p_write.payoff`), and have `index` fire on `p_final` only. Add a test that counts `index` events through `on_node`.

### B2 (High, confirmed): `BOOKGRAPH_WRITE_ROOTS` can never take effect
`steps.write_root_ok` reads `os.environ["BOOKGRAPH_WRITE_ROOTS"]` (`steps.py:27`). But `prepare` runs as a SOLVE
subprocess whose environment is only `PATH` and `PYTHONUTF8` (`graflang/graphlang/grafr/solver.py:237`, "nothing else of
the caller's environment leaks to it"). So the variable is never visible there.

The remedy in `graflang.errors.yaml:6` and the comment in `generate.cedar:3` therefore cannot work. A library outside
`$HOME` or `/tmp` (for example `/srv/books` or a mounted data disk) is refused, and there is no way around it.

**Fix:** compute `write_ok` in `builder.generate_chapter`, in the parent process where the environment is real, and pass
it in as a recipe input. Or pass the roots in explicitly as an input (`write_roots`). Or use the step's `env:` key, if
GraphLang allows a value taken from the caller.

### B3 (High, confirmed by reading): subject rigor is inferred from `ch01`, so it is always "rigorous"
`prepare` calls `infer_subject_rigor(g.chapter_id)` (`steps.py:48`). In the old apps `domain_id` was `chemistry_ch02` and
similar. In the new layout `chapter_id` is the chapter folder, `ch01` (`graph.py:55`), which matches no keyword in
`styles._RIGOR_KEYWORDS`. So every chemistry, biology, health or language book gets the **rigorous** style guide. This is a
silent change in content quality compared with the SPL apps.

**Fix:** pass the book's `tags` (and/or the book slug) into the recipe and infer from those. Fall back to the graph's
`domain:` value, which the scaffold sets (`book.py:122`), rather than the folder name.

### B4 (High, confirmed): common model names are rejected; different adapters share one folder and one cache slot
`model_dir_name` keeps everything after the first `:` (`book.py:104-106`), and `variant_dir` runs it through
`safe_segment`, which allows only `[A-Za-z0-9_-]` (`paths.py:20,46`):

- `ollama:qwen2.5:7b`, `openrouter:anthropic/claude-3.5-sonnet` and `claude-3.5-sonnet` all raise `BookError`. The local
  Ollama models the prompts are tuned for ("weaker and local models", `graph.py:35`) cannot be used.
- `ollama:llama3` and `openrouter:llama3` map to the same folder **and** the same cache key (`builder.py:48-51` passes the
  folder name as `model`). One adapter's cached sections are served as the other's.

**Fix:** keep one function that turns any model string into a safe slug (replace `[^A-Za-z0-9_-]` with `-` and keep the
adapter, e.g. `ollama-qwen2-5-7b`). Use the full `adapter:model` string in the cache key.

### B5 (Medium, confirmed): a cyclic graph crashes `catalog`, `order` and the end of `generate`
`ChapterGraph.default_target()` (`graph.py:92`) calls `productivity_order`, which raises `zinet.dag.CycleError`, not
`BookError`. Three places hit it:

- `describe_book` catches only `BookError` (`catalog.py:78`). So `bgr catalog` crashes on any library that contains one
  bad graph. `bgr generate` crashes too: it runs every variant and then dies in `register_book` (`builder.py:83`) with a
  traceback, and never prints the results it has.
- `bgr order` on a cyclic graph prints a raw traceback (exit 1). The `_guard` wrapper only catches `BookError`.

**Fix:** catch `CycleError` in `default_target()` and in `plan()` and re-raise it as `BookError` (with the members of the
cycle).

### B6 (Medium, confirmed): `retitle_heading` reads the label as a regex replacement
`graph.py:170-171` passes `f"## {label}"` as the `repl` argument of `re.sub`. A label with a backslash (very common in math
books: `$\alpha$ decay`, `$\mathbb{R}^n$`) is corrupted or raises an error. My probe turned `$\alpha$` into `$<BEL>lpha$`;
`\d` or `\g` raise `re.error`, and the page is never written.

**Fix:** pass a function as the replacement, `lambda _m: f"## {label}"`. The same pattern exists in `pages.py:59`; it is
safe today only because the `_ui()` strings are constants.

### B7 (Medium, confirmed): model output reaches the HTML without escaping
The comment at `render.py:44-45` says other HTML "is still escaped by `_inline_md` for XSS safety", but `_inline_md`
(`render.py:14-19`) escapes only the text inside backticks. Three paths are open:

- Prose: `Hi <script>alert(1)</script>` is emitted as is.
- Mermaid fences go out unescaped (`render.py:62-64`), so `</pre><img onerror=…>` breaks out of the block.
- Any line starting with `<figure` passes through verbatim up to `</figure>`, attributes and scripts included.

Today the only input is the author's own graph plus the model. But the next planned feature is ingestion from PDFs
(concept-book-press), which puts third-party text into the prompts: a prompt-injection path to stored XSS on a published
site.

**Fix:** call `_esc()` on the text first in `_inline_md`. For Mermaid, escape only `<` and `&` (Mermaid decodes entities).
For figures, check against a strict pattern (`<figure class="cb-figure">`, an `<img>` with an `https` src on the allowed
hosts, `<figcaption>` with plain text and `<a href>`).

### B8 (Medium, confirmed): duplicate node ids across sections pass validation
`load_graph` adds the same id once per section (`graph.py:146-153`). The later section's `kind` wins, but `node()` returns
the first section's data (`graph.py:59-64`). For `x` declared as both a primitive and a concept, `validate()` returns `[]`
and `kind('x') == 'concept'`, while `context('x')` is the primitive's `defines` text. This is silently inconsistent.

**Fix:** report "declared in both `primitives` and `concepts`" in `validate()`. While there, reject a `composed_of`/`needs`
given as a string, which `list()` currently splits into characters (`graph.py:150`), and a section that is not a mapping.

### B9 (Medium): `--target-node` applies to every chapter
`cli.py:115` passes `target=node` to `generate_book`, which passes it to every chapter. A node id from one chapter makes
every other chapter fail in `prepare` ("target … is not a node"). Either require `-c` together with `--target-node`, or
skip chapters that do not contain the node.

### B10 (Medium): one bad chapter aborts the whole book and skips the catalog update
`builder.generate_chapter` calls `load_graph(graph)` *outside* its `try` block (`builder.py:46`), and `generate_book` has
no per-chapter protection. So chapter 3's missing graph raises out of the loop: chapters 1–2 were generated, but their
results are never printed and `register_book` never runs. Record the error in a `ChapterResult` instead, as already
happens for a `RecipeError`, and register the catalog in a `finally` block.

### B11 (Low): the primitive-budget check is a substring count, and does nothing outside English
`new_primitives` (`graph.py:110-113`) counts every primitive name that appears as a substring anywhere in the text:

- Primitive `set` matches "settings", and `a` matches almost any text.
- It counts *all* primitive mentions, not *new* ones, so a concept made of three primitives always exceeds a budget of 2
  and always pays for a refinement.
- On a `zh` page the English ids never appear, so the check never fires.
- The feedback says "at most **one** new primitive" (`steps.py:19`), while the default budget is 2. It also does not say
  *which* primitives were flagged, so the refinement cannot do much with it.

Suggested fix: match whole words on the ids **and** the labels in the page language; leave out the node's own direct
`composed_of`; name the offending primitives in the feedback.

### B12 (Low): the cache key leaves out things that change the output
`section_params` (`graph.py:157-161`) covers language, style, model and a hash of `defines`. It does not cover:

- the prompt templates;
- the style-guide text, including the rigor tier (B3);
- `primitive_budget` (which decides whether a refinement happened);
- the node label (which is in the prompt).

So editing `write_section.md` or `styles.py` keeps serving old sections until `--skip-cache` is used. Add a short hash of
the three prompt files plus the resolved `style_guide`. The payoff key also ignores the target's prerequisites and
applications (`steps.py:91`).

### B13 (Low): smaller issues
- `catalog.read_catalog` treats corrupt JSON as an empty catalog (`catalog.py:32-36`), and the next `update_catalog` then
  overwrites the file, so every other book's entry is lost. `bgr catalog` can rebuild it, but `register_book` should
  refuse, or back the file up, instead of overwriting it.
- `cache_put` writes to a fixed `<name>.tmp` (`cache.py:31`). Two concurrent runs of the same variant race on it, and one
  `os.replace` can fail. Use `tempfile.NamedTemporaryFile(dir=…, delete=False)`.
- `scan_library` does not check that `book.yaml`'s `press:`/`book:` match its folder (`catalog.py:108-111`). The catalog
  can then list `acme/demo` for a book that `_find_book("acme/demo")` cannot find.
- `fill()` replaces placeholders one after another (`render.py:246-250`). Model text containing `{footer_meta}` (in the
  body) gets the footer inserted into it. Escape `{` in values, or substitute everything in one pass.
- `write_section.md:28-31` repeats the same two-line instruction twice (a copy-paste slip).
- `_find_book` prefers a path relative to the working directory over the `press/book` form (`cli.py:25-27`). A folder
  `acme/demo` in the working directory hides the library entry.

---

## 4. Weaknesses (design and quality, not bugs)

1. **Each section sees only its own `defines`.** `write_section` gets `concept`, `label` and `context`, and nothing about
   the node's prerequisites or the sections already written (`steps.py:56-58`). The point of a teaching order is that
   section *k* builds on 1…*k*−1; at present each section is written in isolation. Passing the prerequisite labels (and
   their one-line `defines`) is cheap and likely the biggest quality gain. (If the SPL version did the same, this is a gap
   it inherited, not one the port introduced.)
2. **Every step reloads the graph and recomputes the plan**, in a new interpreter: about 3 subprocesses per section plus
   4 fixed, roughly 0.3–0.5 s each. That is fine for 20 nodes and noticeable at 200 × levels × languages. `prepare`
   already returns `order`; `round` could take `order[node_index]` from the loop input instead.
3. **The Markdown converter is minimal**, kept from the old apps on purpose:
   - `<li>` is emitted without `<ul>`/`<ol>`, so numbered lists show as bullets and the HTML is invalid;
   - `*…*` inside `$…$` becomes `<em>` (`$a*b*c$`);
   - there are no tables, links or blockquotes;
   - headings can get duplicate `id`s.

   Moving to `markdown-it-py` with a math plugin would fix all of these and B7 together, but pages would then look
   different from the old ones. That is a product decision.
4. **Stale pages are never removed.** If a node is removed or renamed, its old `concept_*.html` stays in the output folder,
   and `_books_in` still lists old `book_<target>.html` files in the catalog.
5. **`--max-llm-calls` applies per variant, not per book.** `bgr generate` on a book with 10 chapters × 2 levels × 2
   languages is allowed up to 8,000 calls. The help text should say so, or there should be a book-wide limit as well.
6. **The CLI exposes no `checkpoint`/`resume`/`workers`/`record`/`replay`**, though `run_recipe` supports them. The cache
   makes a rerun mostly cheap, but replay would make bug reports reproducible.
7. **The level and style tables are thin.** `styles.py` has 7 profiles, but `levels.py` maps only 4. `middle_school` and
   `high_school` cannot be reached from `book.yaml`, and an unknown level silently falls back to `college`
   (`levels.py:10`). `load_book` does reject unknown levels, so that fallback is unreachable: drop it, or expose the extra
   styles.
8. ~~**`graf validate` exits 1 on a valid recipe**~~ **Retracted** (§8): it exits 0. The exit 1 I saw came from a `grep`
   that ran later in the same shell command, not from `graf`.

---

## 5. Gaps

### Compared with the SPL workflow (some already listed in CLAUDE.md)
- The symbolic `verify_section` check (sympy/z3 per domain) is not ported; only the primitive-budget check runs, and it is
  weak (B11). The `styles.py` docstring still refers to `graph_lib.verify_content`.
- The old spl3 cache is not read, so the first run on a migrated `cb-*` book pays for every section again.
- `migrate`, `publish`, ingestion and the web app are not built yet (stated in the README).

### Compared with the sibling graf apps
| | app-graph | data-graph | work-graph | **book-graph** |
|---|---|---|---|---|
| test files | 7 + fixtures | 3 + fixtures | 14 | **2** |
| CLI tests (`CliRunner`) | yes | yes | — | **none** |
| `docs/GUIDE`, `docs/DEMO` | yes | DEMO | yes | **none** (this file is the first in `docs/`) |

Missing tests worth adding, in order:
1. the recipe's `refused` exit (a library outside the allowed roots should end with `E_GENERATE_REFUSED`, with no model
   call);
2. how many times `index` runs (B1);
3. a run where some sections are cached and some are not;
4. a `CycleError` in `catalog`/`order` (B5);
5. labels containing a backslash (B6);
6. HTML escaping (B7);
7. a dotted model name (B4);
8. `bgr validate|order|generate|catalog|list` through `CliRunner`, including exit codes.

---

## 6. Suggested order of work

1. **B1, B2, B6, B5:** each a few lines, each a real failure.
2. **B3 and B4:** pass `tags` and the full model string into the recipe; one function for model slugs.
3. **B7:** escape the Markdown output before ingestion lands.
4. **B8–B10**, then the tests in §5.
5. Pass prerequisite context to `write_section` (Weakness 1), then add a prompt hash to the cache key (B12).
6. Then the planned features: `migrate` (with a spl3 cache import if possible), `publish`, ingestion.

---

## 7. Response (Claude Sonnet 5.5, 2026-11-25)

Status: fixes applied in the working tree, **not committed**. `pytest`: 39 passed (was 20), scripted model, no LLM calls.
`graf validate` on the recipe passes. B-numbers are the review's own. New regression tests are in `tests/test_review.py`
(named after the B-number they cover) and `tests/test_generate.py`.

### 7.1 Bugs: all 13 agreed and fixed

| | Fix | Where | Test |
|---|---|---|---|
| **B1** | The payoff is cached *before* the join: `p_write -> p_store -> p_stored -> p_final`, and `index` fires on `p_final` only. Each of `p_final`, `index`, `path`, `done` now runs once. | `recipes/generate_chapter/generate_chapter.graph.yaml` | `test_every_step_after_the_payoff_runs_once` (counts events via `on_node`) |
| **B2** | Confirmed in `graphlang/grafr/solver.py`: a SOLVE step sees only `PATH` and `PYTHONUTF8`. The builder reads `BOOKGRAPH_WRITE_ROOTS` and passes it as a new recipe input `write_roots`; `write_root_ok(path, extra_roots)` takes it as a parameter. | `steps.py`, `builder.py`, recipe, `graflang.errors.yaml` still accurate | `test_policy_refuses_a_folder_outside_the_roots_with_no_model_call`, `test_extra_write_roots_are_a_parameter_not_the_environment` |
| **B3** | Rigor is inferred from `subject` (book slug + tags, passed by the builder) plus the graph's `domain:`; the chapter folder name is only the last fallback. | `steps.prepare`, `builder.py`, recipe input `subject` | `test_subject_decides_the_rigor_note` |
| **B4** | `model_dir_name` makes the model part path-safe (`ollama:qwen2.5:7b` -> `qwen2-5-7b`). The cache key and recipe `model` input now carry the **full** `adapter:model`; the page footer and folder use the slug. | `book.py`, `builder.py`, `steps.py` | `test_dotted_model_names_and_adapters_sharing_a_folder` |
| **B5** | `ChapterGraph._order` turns zinet's `CycleError` into `BookError` for `default_target()` and `plan()`. `catalog` records it as the chapter's `error`; `order` prints one line; `generate` no longer dies after the loop. | `graph.py` | `test_cycle_is_a_book_error_not_a_traceback` |
| **B6** | `re.sub` replacements are functions (`lambda _m: ...`) in `retitle_heading` and in the payoff heading in `pages.py`. | `graph.py`, `pages.py` | `test_backslash_in_a_label_survives` |
| **B7** | `_inline_md` escapes first (the backtick content is no longer double-escaped); mermaid escapes `&` and `<` only (keeps `-->`); display math is escaped; a `<figure>` passes through only if it matches one strict pattern (`cb-figure`, https image on wikimedia/openstax/rcsb/nlm.nih.gov, plain-text caption, optional https link), else it is shown as escaped text. | `render.py` | `test_model_output_is_escaped`, `test_an_allowed_figure_passes_through` |
| **B8** | `validate()` reports an id declared in two sections. `load_graph` raises `BookError` for a section or node that is not a mapping and for `composed_of`/`needs` that is not a list. | `graph.py` | `test_duplicate_ids_and_string_deps` |
| **B9** | `--target-node` without `-c` is a usage error (exit 2). I chose "require `-c`" over "skip chapters without the node". | `cli.py` | `test_target_node_needs_a_chapter` |
| **B10** | `generate_chapter` does graph loading and style resolution inside its `try` and returns a failed `ChapterResult`. `generate_book` registers the catalog in a `finally`; a catalog error is reported (`BookResult.catalog_error`, printed to stderr, exit 1) instead of hiding the results. The "no model" check now runs before any generation. | `builder.py`, `cli.py` | `test_a_bad_chapter_does_not_abort_the_book_or_the_catalog` |
| **B11** | `new_primitives` returns the list of flagged primitives: whole-word match on id and on the label in the page language (substring for non-ASCII, since CJK has no word boundaries); the node itself and its **direct prerequisites are excluded**. Feedback now says "introduce at most N new primitive(s) ...; this one introduces M: names" using the real budget. | `graph.py`, `steps.check`, recipe (`check` now gets `node` and `language`) | `test_primitive_check_is_whole_word_and_names_the_offenders`; `test_primitive_budget_triggers_one_refinement` updated (now 2 refinements, not 5, because prerequisites no longer count) |
| **B12** | Cache params now include a `salt` = hash of the three prompt files + the resolved style guide + the primitive budget (`steps.cache_salt`, computed in `prepare`, carried as `key_salt`). The node key hashes label + `defines`; the payoff key hashes label, `defines`, prerequisite labels and application labels. | `steps.py`, `graph.section_params`, recipe | `test_cache_misses_when_the_prompt_inputs_change` |
| **B13** | `read_catalog` raises `BookError` on a damaged file (never treats it as empty, never overwrites it); `cache_put` uses `tempfile.mkstemp`; `scan_library` skips a book whose `press:`/`book:` do not match its folder; `fill()` substitutes in one pass; the duplicated two lines in `write_section.md` are gone; `_find_book` lets `press/book` in the library win over a same-named local folder (a path starting with `.`, `/`, `~`, or a file, still means the path). `list` now goes through `_guard`. | `catalog.py`, `cache.py`, `render.py`, `prompts/write_section.md`, `cli.py` | `test_corrupt_catalog_is_never_overwritten`, `test_fill_is_one_pass`, `test_library_entry_beats_a_folder_of_the_same_name` |

Also done from the weaknesses and gaps:
- Weakness 5: `--max-llm-calls` help text now says the cap is per chapter x level x language x model run.
- Weakness 7: an unknown level now raises (the silent `college` fallback was unreachable from `book.yaml`).
- Gap: the `styles.py` docstring no longer refers to `graph_lib.verify_content`.
- §5 tests: items 1-8 are covered (refused exit, index runs once, partly cached run, cycle in catalog/order, backslash label, escaping, dotted model name, CLI through `CliRunner` with exit codes). The CLI test is basic, not exhaustive.
- Docs: `CLAUDE.md` (steps cannot read the caller's environment; join rules; untrusted model output; new cache key) and `CHANGELOG.md` updated.

### 7.2 Behaviour changes you should know about

These follow from the fixes and affect existing output or numbers:

1. **The cache key changed** (new `salt`, label in the key, full model string). Anything generated by the first commit is a cache miss once. No migrated data is affected, since migration is not built.
2. **B11 changes what triggers a refinement.** A section is only checked for primitives that are not the node's own prerequisites, so fewer sections get a refinement call than before. That is cheaper and, I think, closer to the intent ("new" primitives), but it is a behaviour change from the SPL workflow.
3. **B7 can change what the model's figures look like.** An `<img>` from a host outside the allow list now renders as visible escaped text instead of an image. The prompt already restricts the model to those hosts.

### 7.3 Questions for you to decide

| # | Question | My view |
|---|---|---|
| Q1 | **Model folder name (B4).** The folder is the model part only (`sonnet`, `qwen2-5-7b`), which keeps the old `output/core.en/<model>/html` layout. Consequence: `ollama:llama3` and `openrouter:llama3` share one folder, and the second overwrites the first's pages (their caches are separate, so the second run regenerates). The review suggests keeping the adapter in the slug (`ollama-llama3`). | Keep the model-only folder for compatibility with the existing `cb-*` content, and decide on the adapter prefix before `migrate` is written. If you will never run the same model name through two adapters, there is no collision to worry about. |
| Q2 | **Prerequisite context in `write_section` (Weakness 1).** Not done. Passing each node's prerequisite labels and one-line definitions is cheap and may be the biggest quality gain. | Worth doing, but it changes what the model sees, so output will differ from the SPL-era books and the cache keys change again. I would try it on one chapter and compare before making it the default (or put it behind a `book.yaml` flag). |
| Q3 | **Markdown converter (Weakness 3).** Not changed. `<li>` has no `<ul>`/`<ol>`, `*..*` inside `$..$` becomes `<em>`, no tables or links. Moving to `markdown-it-py` fixes these but changes how all pages look. | Product decision. If you want parity with the old apps, leave it; if you want correct HTML, switch and regenerate. |
| Q4 | **`graf validate` exit code.** The review says it exits 1 because of the model-call-cap warning. Here it exits 0 (same recipe, with and without a `max_iterations`), so I changed nothing. | Cannot reproduce. Which `graf` version/command did you or the reviewer run? |
| Q5 | **Styles `middle_school` and `high_school`.** `styles.py` has 7 profiles, `levels.py` maps 4 levels, so two styles are unreachable. | Either add levels for them or delete the two profiles. I did not guess. |
| Q6 | **B9 behaviour.** I required `-c` with `--target-node`. The alternative is to run only the chapters that contain the node. | Keep mine; it is explicit. Say if you prefer the other. |

### 7.4 Deferred (not done, no decision needed yet)

- Weakness 2: every step reloads the graph and recomputes the plan in a new interpreter (`round` could take `order[node_index]` from `prepare`). Fine for now; matters at hundreds of nodes. The tests take about 80 s because of it.
- Weakness 4: stale pages are not removed when a node is renamed or removed.
- Weakness 6: `checkpoint`, `resume`, `workers`, `record`, `replay` are not exposed on the CLI.
- `docs/GUIDE` and `docs/DEMO` like the sibling apps; a fuller `CliRunner` suite.
- Still true from §5: the symbolic `verify_section` is not ported, the old spl3 cache is not read, and `migrate`, `publish`, ingestion and the web app are not built.

---

## 8. Verification of the fixes (Opus 5.5, 2026-11-25)

I checked the working tree, which is not yet committed: I read the diff, ran `pytest` (39 passed), ran `graf validate`
(exit 0, graf 0.3.0), re-ran my original probes, and probed the new code.

### 8.1 The fixes hold

| | Re-check |
|---|---|
| B1 | An uncached run now shows `p_store`, `p_final`, `index`, `path` and `done` once each (before: `index`, `path` and `done` twice). |
| B2 | `write_roots` is a recipe input, read by the builder; the `refused` exit is tested end to end. |
| B3 | Rigor now comes from the book slug, the tags and `domain:`. |
| B4 | `model_dir_name` makes the name path-safe; the cache key carries the full `adapter:model`. (My probe's "model rejected" lines call `safe_segment` on raw names directly; they are expected.) |
| B5 | `bgr order` on a cyclic graph prints a one-line error; the catalog records it as the chapter's `error`. |
| B6 | `$\alpha$` survives `retitle_heading`. |
| B7 | `<script>`, a mermaid `</pre>` breakout and a figure from a host outside the allow list are all escaped. |
| B8 | An id declared in two sections is reported. |
| B9–B13 | Hold as described in §7.1. |

### 8.2 New or remaining issues

1. **(Medium) The primitive check still counts primitives that were already taught.** `new_primitives` leaves out only the
   node and its *direct* prerequisites (`graph.py`, `skip = {node, *self.prerequisites(node)}`). A section on `addition`
   (made of `counting`, which is made of `idea` and `quantity`) that mentions "idea" or "quantity" is told it introduces 2
   new primitives. But every ancestor comes before the node in the teaching order, so none of them is new. The result is
   wasted refinement calls, and feedback asking the model to stop referring to earlier material.
   **Fix:** `skip = {node} | nx.ancestors(self.G, node)`.
2. **(Medium, pre-existing; matters more now) An unterminated `<figure` drops the rest of the section.**
   `_md_to_html("Intro\n\n<figure class=\"cb-figure\">\n<img src=x>\n\nMore text after.")` returns only
   `<p>Intro</p>`: the buffer is never flushed if `</figure>` never comes. **Fix:** after the loop, if `in_raw_html`,
   append `_safe_figure(...)` (which escapes it), or end the block at the first blank line.
3. **(Low, introduced by the B13 fix) An existing relative path whose first folder is not a valid segment now fails.**
   `bgr validate books.v2/demo`, run where `./books.v2/demo` exists, raises "press 'books.v2' must be letters…" before
   the code falls back to the path. **Fix:** in `_find_book`, try the library form only when both parts pass
   `safe_segment`; otherwise fall through to `p.exists()`.
4. **(Low) `bgr catalog` cannot rebuild over a damaged catalog.** `scan_library` goes through `update_catalog`, which
   reads the old file first and now raises. The error message does say to delete the file first, so this is acceptable.
   Since a full scan never needs the old content, it could write the new catalog without reading the old one.
5. **(Nit) The cycle message repeats itself:** "the graph has a cycle among: dependency cycle among: a1, a2". Drop the
   prefix in `ChapterGraph._order`, since zinet's message already says it.
6. **(Nit) CLAUDE.md states the `on: [a, b]` rule twice** (the line ending "exclusive branches only" and the next line).

### 8.3 My view on the questions in §7.3

- **Q1:** agree. Keep the model-only folder for now, and decide on the adapter prefix before `migrate`.
- **Q2:** agree: try it on one chapter behind a `book.yaml` flag first.
- **Q3:** product decision. Weighing it again: since B7 the converter is safe, so this is now only about correct HTML.
- **Q4:** my error; see the retraction in §4.
- **Q5:** add `middle_school` and `high_school` as levels rather than delete them. They come from the concept-book-base
  profiles, and the geometry content uses them.
- **Q6:** agree: requiring `-c` is clearer.

### 8.4 Follow-ups fixed (Opus 5.5)

Items 1–3, 5 and 6 of §8.2 are fixed in the working tree; item 4 is left as is. `pytest`: 42 passed.

| | Fix | Test |
|---|---|---|
| 1 | `new_primitives` leaves out the node and **all** its ancestors (`nx.ancestors`). `test_primitive_budget_triggers_one_refinement` now uses budget 0: with the new rule, only the two primitive sections name a primitive that is not an ancestor. | `test_primitives_already_taught_are_not_new` |
| 2 | An open `<figure>` ends at the first blank line or at the end of the text, and is escaped (it cannot match `_FIGURE_OK`). The rest of the section still renders. | `test_unterminated_figure_does_not_swallow_the_section` |
| 3 | `_find_book` tries `press/book` under the library only when both parts are valid segments; otherwise it falls through to the local path. | `test_local_path_with_a_dot_still_works` |
| 5 | The cycle error is now `<path>: dependency cycle among: a1, a2`. | (existing B5 test) |
| 6 | The duplicate `on: [a, b]` rule is removed from CLAUDE.md. | — |

---

## 9. Against the plan (`docs/DEV/readme-book-graph-plan.md`) — Opus 5.5, 2026-11-25

This section measures the implementation against the plan, going beyond the bugs in §3 and §8. What I checked:

- every existing `graph.yaml` except cb-zinets: 260 files under `*/public/domains/*/input/`;
- the old pipeline in `concept-book-base/spl/` (`build_concept_book.spl`, `tools.py`, `graph_lib.py`, `style_profiles.py`);
- the spl3 content cache (`SPL.py/spl3/cache/`) and the real cache databases;
- how conceptbook-app and concept-book-base read generated pages.

### 9.1 Where it meets the plan, verified

| Plan item | Result |
|---|---|
| Back-compat: "all existing `graph.yaml` files must load as-is" | **260 / 260** load and pass `validate`. |
| Same teaching order as the old apps (CLAUDE.md claims "exactly") | **260 / 260** give the same order as the old `graph_lib.productivity_order` (target = the last application, weight 1.5). |
| "Keep prompts verbatim" | Verbatim, apart from the parameter renaming and one duplicated two-line instruction that was removed (that duplicate was in the SPL original too). |
| Single copy of `styles.py` | Identical to `concept-book-base`, apart from three docstrings. |
| HTML looks alike | `_MATHJAX_HEAD` and both page templates are identical to `tools.py`. The CSS differs only in the credit class name (but see 9.2-2). |
| No `graph_lib` copy; graflang/zinet/dd-llm are dependencies | Yes. |
| [WEN] answers: one repo, SPL dropped, press > book > chapter, conceptbook-app and cb-zinets left alone | All followed. |
| Guarded writes, `max_llm_calls`, logs in `~/book-graph/logs`, offline recipe tests | Yes. |
| SPL behaviour kept | The payoff is written only for an application target; the `<target>__payoff` key; localized headings; the cache-key fields language, style, model and defines-hash. |

### 9.2 Where it falls short of the plan

**1. (High) The old cache can be reused, but no code does it, and the code says it can't be done.**
The plan says: port the cache keys and don't re-derive them (Phase 3); "never regenerate existing content during
migration (cache keys must match)" (§5); and every `cb-*` book should rebuild "with unchanged cached content" (§6).

`cache.py` says the old spl3 keys "were not recorded in a form that can be recomputed here". That is wrong:

- spl3's key is `content_key(concept, params, "v1", {})` (`spl3/cache/keys.py`), and it recomputes for **798 / 798**
  entries in `cb-meta-health/spl/.spl/` and **669 / 669** in `concept-book/spl/.spl/`.
- 444 of the cb-meta-health entries use exactly `{ctx, language, model, style}`. The first commit's
  `section_params` produced that same JSON, with `model` = the folder name, so it was one reader away from reusing them.
- The B4 and B12 fixes (full `adapter:model`, the label in `ctx`, a salt) moved the key further from it. That was my
  recommendation in §3, and it conflicts with the plan. I should have checked the plan first.

**Fix, without undoing B4/B12:** on a miss, look the section up read-only in the book's spl3 store
(`<repo>/spl/.spl/content_cache.db`, through `spl3.cache.get_content_cache(...).get(concept, legacy_params, "v1", {})`).
Use `legacy_params = {language, style, model: <folder>, ctx: sha256(defines)[:16]}`, and copy any hit into `.bg_cache`.
Put it behind a `book.yaml` key (`legacy_cache:`) or in `migrate`. The older `{domain, language, llm, lvl}` entries in
concept-book are a different shape; they would need their own mapping, or can be left out.

**2. (Medium) conceptbook-app depends on the class name that was renamed.**
The pages now use `footer.bg-credit`. `conceptbook-app/src/components/ContentPanel.js:826` removes `.spl-credit` before
it extracts page text, so the footer text now leaks into the extracted chunks. `contentExists.js` still works, because
it also accepts "Generated by". WEN's answer on content location points at aggregation in conceptbook-app, so this
consumer matters. **Fix:** emit `class="spl-credit bg-credit"` (and the matching `__meta`/`__powered` class names).

**3. (Medium) Only the generation part of the plan exists, built out of the plan's order.**
Phase 1's gate ("no generation yet — pure packaging"; `bookgraph build` reproduces cb-calculus) and Phase 2 (one `web/`)
were skipped, and Phase 3 was built first. That is defensible, since generation is the core and now well tested. But
none of the user-facing success criteria in §6 can be met yet: `new` + `run` making a book, books rebuilding with
unchanged URLs, retiring concept-book-base and press's publish pipeline.

| Plan CLI | Status |
|---|---|
| `new`, `validate`, `generate` | done (`new PRESS BOOK`; the alias is `bgr` rather than `bg`, which is a shell builtin, so that is a good change) |
| `ingest`, `extract`, `build`, `serve`, `publish`, `run`, `migrate` | not built |
| (not in the plan) `order`, `catalog`, `list` | added |

**4. (Medium) Output the web apps need is not produced.**
- The **navigator** (`output/graph.html`, vis.js; `has_navigator` in the catalog) is not generated. The plan keeps it,
  and `zinet.vis` (cytoscape) is the intended replacement for `scripts/concept_graph.py`.
- **The catalog has a different shape.** book-graph writes nested `presses > books > chapters`. concept-book-base and
  conceptbook-app read a flat list of `{id, name, description, capstone, nodes, …, tags, has_navigator, has_book,
  books[]}`. `build` or `export` will need an adapter.
- **Chapter ids are local to their book** (`ch01`), while the old ids are global (`calculus_ch01`), and conceptbook-app
  lays content out as `public/domains/{publisher}/{id}/`. Decide the global id now (e.g. `<book>_<chapter>`). It
  decides URLs, the aggregator's layout and legacy-cache lookups, and it is what the rigor fix (B3) worked around.

**5. (Medium) Not resumable as the plan intended.**
The plan has `generate_concept` and `generate_book` recipes, "resumable" through GraphLang checkpoints, so that press's
`.pipeline/publish.db` stage tracker can be retired. Instead there is one recipe, `generate_chapter`. The book-level
loop is plain Python, and the CLI exposes no `--checkpoint`/`--resume`. The section cache makes a rerun cheap, which
covers most of the need, but the plan's claim is not met.

**6. (Low) The Phase 3 checks are missing.** There is no `record`/`replay` comparison against existing generated HTML,
and the symbolic `verify_section` (the SPL's second refinement pass) is still not ported. CLAUDE.md lists the latter.

**7. (Low) `validate` covers less than the plan's list** ("acyclic, reducible, connected, tiers"). Connectivity (nodes
that never reach the capstone) and `tier` consistency are not checked. The optional app-graph `validate`/`drift` reuse
is not used.

**8. (Low) The dependency is not pinned.** `graflang>=0.1.0`, but the recipe needs 0.3 features (`graphlang.recipes`,
GUARD), and app-graph pins `graflang>=0.3.0,<0.4`. The plan's risk table says to pin it. Use the same pin as app-graph.

**9. (Info) Parts of `book.yaml` are not used yet:** `publish:` is absent, and `site:` is read but nothing uses it.
That is fine until build/publish exist.

### 9.3 Scorecard by phase

| Phase | Status |
|---|---|
| 0 Scope | done ([WEN] answers followed) |
| 1 Skeleton + book.yaml + `build` | **partial**: `book.yaml`, `validate` and the library are done; `build` is not |
| 2 Single web app | not started |
| 3 Generation as recipes | **mostly done**: prompts, order and cache fields are faithful; missing are cache continuity (9.2-1), resumability, replay checks and `verify_section` |
| 4 Ingestion/extraction | not started |
| 5 Publish + migrate | not started |

### 9.4 Recommended next steps

1. Legacy cache read-through (9.2-1), **before** any real generation on a migrated book. Otherwise the first run pays
   again for content already paid for.
2. Keep the `spl-credit` class (9.2-2). This is a one-line change.
3. Decide the global chapter id and write the flat-catalog adapter; add the navigator through `zinet.vis` (9.2-4).
4. Pin `graflang>=0.3.0,<0.4` (9.2-8).
5. Expose `--checkpoint/--resume`, or move the book loop into a recipe (9.2-5).
6. Then the plan's own Phase 1 gate: `bookgraph build` reproducing cb-calculus.

### 9.5 Fixes made (Opus 5.5, 2026-11-25)

Items 9.2-1, 9.2-2 and 9.2-8 are fixed in the working tree, which is not yet committed. `pytest`: 45 passed;
`graf validate`: exit 0.

| | Fix | Where | Test |
|---|---|---|---|
| 9.2-1 | **Reuse of the old cache.** A new optional key, `book.yaml: legacy_cache: <path to the SPL app's spl/.spl>`, becomes a recipe input. On a miss in `.bg_cache`, `round` and `payoff_round` look the entry up in the spl3 store, using spl3's own key (`legacy_params` = `{language, style, model: <folder>, ctx}`; rubric `v1`, no deps). Stale entries are a miss, as in spl3. The store is opened read-only (`mode=ro`), and values are unpickled with an unpickler that refuses every global. A section hit is copied into `.bg_cache` by `store`, which is `risky: true`; the manifest says `legacy`. A payoff hit is read each run, not copied. `--skip-cache` skips both caches. | `legacy.py` (new), `steps.py`, `book.py`, `builder.py`, recipe | `test_legacy_spl3_cache_is_reused_and_copied`, `test_legacy_reader_refuses_pickled_objects` |
| 9.2-2 | The footer is `class="spl-credit bg-credit"` (the `__meta` span too), so conceptbook-app's `.spl-credit` filter still works. | `render.py` | `test_footer_keeps_the_spl_credit_class` |
| 9.2-8 | `graflang>=0.3.0,<0.4`, as in app-graph. | `pyproject.toml` | — |

Checked on real data, cb-meta-health's `spl/.spl` cache:

- `legacy_key` recomputes **444 / 444** of its ctx-style keys.
- Using today's graphs, 440 of those match a node and are read. The other 4 nodes have had their definitions edited
  since, so a miss is the correct result.
- End to end: a library book over `meta_health_ch03` with `legacy_cache:` set, at `core.en` with model
  `claude_cli:claude-sonnet-5`, generated all **24 sections and the payoff with 0 model calls**.

Still open from §9.2:

- the global chapter id, the flat-catalog adapter and the navigator (9.2-4);
- resumability (9.2-5);
- replay checks and `verify_section` (9.2-6);
- the extra `validate` checks (9.2-7).

The older `{domain, language, llm, lvl}` and ctx-less entries are deliberately not looked up: their text may belong to
an earlier definition of the node.

---

## 10. Build plan, step 3: `migrate` and `export` (Opus 5.5, 2026-11-25)

This delivers the content half of the plan's Phase 1 (book.yaml + "`build` reproduces an existing book from its existing
`public/domains/`") and `migrate` from Phase 5. The web build itself (Phase 2, one `web/`) is not part of it.

### 10.1 What was added

- **A global chapter id.** It is `Chapter.global_id`, defaulting to `<book>_<chapter>`. That reproduces the existing ids
  (`calculus` + `ch01` = `calculus_ch01`), so URLs do not change. An explicit `global_id:` is allowed, and two chapters
  sharing one is an error.
- **`bgr export TARGET DOMAINS`** (`export.py`, `export_book`). It writes each chapter's graph, pages and navigator, plus
  the book-level files (`<book>/domains/`: the super-graph, `groups.json`, …), into an app's `public/domains/`. It then
  merges the chapters into the apps' **flat** `catalog.json`:
  - other entries are kept, and a chapter already listed is replaced where it stands;
  - the write takes `catalog.json.lock` with the same flock the apps' `catalog_lock.py` uses, and is written the same way
    (`indent=2`, trailing newline);
  - an entry is the chapter's `catalog:` fields from book.yaml, kept as they are (`name`, `i18n`, `source`, `publisher`,
    `pdfs`, `default_level`, …), plus the computed fields: `id`, `capstone`, the counts (computed as press's
    `sync_from_press` does), `has_navigator`, `has_book`, `books` and `generated_concepts`, listed from the files on disk.
- **`bgr migrate REPO`** (`migrate_repo`). It reads a `cb-*` repo's `public/domains/` into `<library>/<press>/<book>/`:
  - each catalog entry becomes a chapter (`calculus_ch01` → `ch01`; any other id is kept, with `global_id:`);
  - graphs, `concept_sources.yaml`, generated pages, navigators and book-level files are copied;
  - levels, languages and model folders are read from `output/`, and models are written as `<adapter>:<folder>` (default
    `claude_cli`), so page folders and legacy-cache keys stay the same;
  - `legacy_cache:` points at the repo's `spl/.spl` when it has one;
  - the repo is only read, and an existing book is never overwritten.

### 10.2 Verified on every cb repo (13; cb-zinets left out)

`migrate` then `export`, compared with the repo's own `public/domains/`:

- **Files:** every file is byte-identical, with none missing and none extra. All 13 repos, from cb-linalg (219 files) to
  cb-world-powers (4,583).
- **Catalog:** the same chapters, in the same order. Every field difference falls into one of these kinds, each a
  correction of a stale or buggy old catalog:

| Count | Difference | Why the new value is right |
|---:|---|---|
| 1,951 | `generated_concepts` names with the `_<lang>` suffix (`counting_zh`, "Counting Zh") | the naming bug `catalog_svc.mark_book_generated` describes as fixed; the new entries use the bare node name |
| 635 | pages on disk that the old catalog never listed (621 concept, 14 book) | the old catalog was only updated by the API or batch run that made them |
| 174 | old records without `model`/`language` keys | always written now |
| 38 | entries with no counts | always computed now |
| 7 | stale counts (the graph was edited after its entry was written) | recomputed from the graph |
| 5 | catalog entries pointing at files that do not exist (4 book, 1 concept) | dropped: the old app showed "Content Not Available" for them |

`cb-meta-health` and `cb_biology` were migrated with `legacy_cache:` set. §9.5 shows that cb-meta-health's chapter 3
then generates with 0 model calls.

### 10.3 Still open

- **Navigators for new chapters.** `output/graph.html` is a vis.js page that the app's `GraphViewer.js` scripts through
  `frame.contentWindow` (relabelling, node selection, saved positions). zinet's cytoscape page does not meet that
  contract. New chapters therefore export with `has_navigator: false` until `concept_graph.py`'s page is ported, or
  zinet produces a page that meets the contract.
- **The super-graph** (`domains/graph.yaml`, from `build_super_graph.py`) is carried through, but not regenerated when a
  chapter graph changes.
- **The web build itself** (Phase 2: one `web/`, then `bgr build` = `export` + the Vite app with `site:` config), then
  `publish`.
- **Resumable book runs** (9.2-5), the `validate` additions (9.2-7), replay checks and `verify_section` (9.2-6).

---

## 11. Build plan, step 4: the navigator (Opus 5.5, 2026-11-25)

### 11.1 Which generator to port

There are six different `scripts/concept_graph.py` in the repos — the plan's §1 code drift in miniature:

- most `cb-*` repos share one older copy, without per-language `labels`;
- concept-book-base (also in cb-graph-network, cb-meta-health and cb-world-powers) adds `labels`;
- conceptbook-app adds a `timeline` layout and changes notes handling;
- cb-graph-intro, cb-zinets and SPL.py each have their own.

I checked which generator reproduces which shipped `graph.html` (cb-zinets left out):

- each repo's own copy reproduces most of its own pages;
- the concept-book-base copy reproduces all 9 of cb-meta-health's;
- a few pages match none of them, for example cb-graph-network's 8 and 16 of cb-world-powers' 17.

The plan folds concept-book-base's `src/` into the one `web/` (Phase 2), and its `GraphViewer.js` is the consumer whose
contract matters, so I ported **concept-book-base's** generator.

### 11.2 What was added

- **`navigator.py`**: `_load_yaml_graph` and `_to_html` copied **verbatim** from that script, extracted programmatically,
  with only the PyYAML import guard removed. It also passes the same `domain_name` the script derived (`"graph"`), so
  readers' saved notes and positions (localStorage key `graph`) keep working.
- **When a navigator is written:**
  - `export` and the new `bgr navigator TARGET [-c] [--force]` write `<chapter>/output/graph.html` when it is missing,
    or when the graph's sha256 differs from the one recorded in `<chapter>/.navigator`. That stamp file sits outside
    `output/`, so it is never exported.
  - `migrate` stamps the navigators it copies, so a migrated chapter keeps its own page until its graph is edited.
  - The check is on content, not mtimes: a git checkout or a copy sets mtimes arbitrarily. My first version compared
    mtimes, and the tests caught it rewriting every migrated navigator.

### 11.3 Verified

- **Identical to the script:** the output matches what concept-book-base's script produces, byte for byte, for all
  **260 / 260** existing chapter graphs. It also matches cb-meta-health's shipped `graph.html`, **9 / 9**. A test repeats
  this comparison, and skips when concept-book-base is not checked out.
- **Round trip:** `migrate` then `export` is still byte-identical for all 13 repos, so migrated navigators are left alone.
- **New chapters:** they now export with `has_navigator: true` and a working page.
- **Tests:** 54 passed.

### 11.4 Still open

- **conceptbook-app's additions** (the `timeline` layout through graph.yaml's `layout:` key, and its notes handling) are
  not ported. Decide when the aggregator integration is designed: port the conceptbook-app variant instead, or add the
  timeline mode on top.
- **Older navigators.** Pages made by the older generators have no `labels`, so the app cannot relabel them per
  language. `bgr navigator --force` upgrades them to the base version. Their notes key stays `graph`, but I have not compared
  the two versions' pages visually. Check one book in the app before running it on all of them.
- **Remaining plan steps:** the web build (Phase 2, `bgr build`), `publish`, resumable book runs, and the §9.2 items 6–7.
