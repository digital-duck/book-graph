You are an expert author writing a concept-book section.

{{in_style_guide}}

Write a self-contained section for the concept: {{in_concept}}
Surrounding context: {{in_context}}

Begin the section with a level-2 Markdown heading: ## {{in_label}}

Follow the structure and length specified in the STYLE GUIDE above. The
STRUCTURE labels there (e.g. "Picture this", "Worked example") describe what
each part should contain — they are English labels for your reference only,
not text to copy verbatim. Write every word of the output, including any
structural headers, entirely in {{in_language_label}}.
Use LaTeX for all mathematics and notation; mathematical notation and LaTeX
remain in their standard form regardless of language. Always wrap LaTeX in
its math delimiters — a single dollar sign on each side for inline math, a
double dollar sign on its own line before and after for a display equation
— never leave a bare LaTeX command with no surrounding delimiters: MathJax
only renders delimited math, so an undelimited LaTeX command (e.g. a bare
\underbrace with nothing wrapped around it) shows up as broken raw text
instead of a rendered equation.
Write an equation only when it states a real, established relationship of
the subject (a law, a definition, a standard formula, a measured quantity) —
never invent a formula, proportionality, or logical expression to dress up a
qualitative idea; state such ideas in words. Every equation must be complete,
valid LaTeX: balanced braces, and every fraction written as \frac{...}{...}.
Refer to concepts by their natural-language names in {{in_language_label}}; never write
identifiers containing underscores in the prose.
Refer to concepts by their natural-language names in {{in_language_label}}; never write
identifiers containing underscores in the prose.
If the context includes a [Diagram: type — hint], generate a diagram
immediately after the main explanation (before any closing summary). Choose
whichever option better serves the hint:

OPTION A — Mermaid diagram (preferred for flows, DAGs, sequences,
comparisons, or any diagram that can be expressed in text-based syntax):
Output a fenced mermaid block, immediately followed by a brief italic caption.
Use the stated diagram type as the Mermaid diagram kind (e.g. "graph TD",
"flowchart LR", "sequenceDiagram"). Keep it to ≤12 nodes. Write all node
labels in {{in_language_label}}. Use valid Mermaid syntax only; no self-loops.
```mermaid
<diagram type>
<diagram body>
```
*Caption in {{in_language_label}} describing what the diagram shows.*

OPTION B — Public-domain image reference (preferred for molecular structures,
anatomical figures, or any diagram requiring visual/spatial precision that
Mermaid cannot represent):
Output exactly this HTML block (no surrounding Markdown, no extra tags):
<figure class="cb-figure">
  <img src="IMAGE_URL" alt="ALT_TEXT" class="cb-figure-img">
  <figcaption>CAPTION_IN_{{in_language_label}}. Source: <a href="SOURCE_URL">TITLE</a>, LICENSE.</figcaption>
</figure>
Use only images with verifiable, permanent URLs from Wikimedia Commons
(upload.wikimedia.org/…), OpenStax (openstax.org/…), RCSB PDB (rcsb.org/…),
or NCBI (ncbi.nlm.nih.gov/…). If you cannot identify a specific, real URL
for a public-domain image that directly illustrates this concept, choose
Option A instead. Never fabricate or guess a URL.

If neither option produces a useful diagram for this concept, omit it entirely.
Output only the section text. No preamble, no metadata.
