from __future__ import annotations

import re
from pathlib import Path

import pytest

from bookgraph.book import scaffold_book

GRAPH = """\
domain: demo_ch01
primitives:
  idea:
    defines: A starting notion.
  quantity:
    defines: Something that can be counted.
concepts:
  counting:
    composed_of: [idea, quantity]
    defines: Assigning numbers to a collection.
    labels: {zh: 计数}
  addition:
    composed_of: [counting]
    defines: Combining two counts.
applications:
  budget:
    needs: [addition]
    defines: Tracking money in and out.
"""


class Scripted:
    """Answers each prompt by its first line; keeps every prompt it saw."""
    name = "scripted"

    def __init__(self, over_budget: bool = False):
        self.prompts: list[str] = []
        self.over_budget = over_budget

    def generate(self, prompt, model=None, **opts):
        self.prompts.append(prompt)
        if m := re.search(r"Begin the section with a level-2 Markdown heading: ## (.+)", prompt):
            extra = " idea quantity counting" if self.over_budget else ""
            return f"## {m.group(1)}\n\nBody of {m.group(1)}. $x^2$.{extra}"
        if "Revise the section below" in prompt:
            return prompt.split("SECTION:\n", 1)[1].split("\n\nFEEDBACK:", 1)[0].replace("idea quantity counting", "")
        if "capstone of a concept-book" in prompt:
            return "## Payoff\n\nIt all comes together."
        raise AssertionError(f"unexpected prompt: {prompt[:200]}")


@pytest.fixture
def library(tmp_path: Path) -> Path:
    lib = tmp_path / "library"
    root = scaffold_book(lib, "acme", "demo", "Demo Book")
    (root / "chapters" / "ch01" / "graph.yaml").write_text(GRAPH, encoding="utf-8")
    return lib
