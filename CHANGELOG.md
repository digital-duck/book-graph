# Changelog

## 0.1.0 (unreleased)
- Package `bookgraph`, CLI `bookgraph` / `bgr`: `new`, `validate`, `order`, `generate`, `catalog`, `list`.
- press > book > chapter model (`book.yaml`); ConceptBook `graph.yaml` unchanged.
- `generate_chapter` GraphLang recipe replacing `build_concept_book.spl`; section cache; per-library `catalog.json`.

### Fixes after the Opus 5.5 review (docs/DEV/review-by-opus-5-5.md)
- The index step ran twice when a payoff was generated (B1); `BOOKGRAPH_WRITE_ROOTS` never reached the policy (B2, now the `write_roots` input).
- Rigor is inferred from the book (slug, graph `domain:`), not the chapter folder (B3). Model names with `.`, `/` or `:` work; the cache key carries the full `adapter:model` (B4).
- Cycles are `BookError`s (B5); `$\alpha$` labels survive (B6); model output is HTML-escaped (B7); duplicate ids and string deps are rejected (B8).
- `--target-node` needs `-c` (B9); one bad chapter no longer aborts the book or skips the catalog (B10).
- The primitive-budget check is whole-word, per page language, ignores the node's own prerequisites and names the offenders (B11); the cache key includes a salt over prompts, style guide and budget (B12).
- The primitive check skips all of a node's ancestors (taught earlier); an unclosed `<figure>` no longer swallows the section; local paths like `books.v2/demo` work again.
- `book.yaml: legacy_cache:` reuses an SPL app's spl3 cache (read-only, `bookgraph.legacy`), so migrated books do not pay again for existing sections.
- Pages keep the `spl-credit` footer class (conceptbook-app strips it before reading text); `graflang` pinned to `>=0.3.0,<0.4`.
- `bgr migrate REPO` imports a `cb-*` repo (chapters keep their old ids as `global_id`; pages, navigators and book-level files are kept); `bgr export TARGET DOMAINS` writes a book into an app's `public/domains/` with the apps' flat `catalog.json`, its counts and page lists recomputed from disk.
- A damaged `catalog.json` is refused, not overwritten; unique temp files in the cache; one-pass page templating; `press/book` beats a same-named local folder (B13).
