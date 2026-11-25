"""A chapter's graph.yaml: load, validate, teaching order, and the per-node text the prompts need.

The graph format is the ConceptBook one (also zinet's v1.0 flat form): `primitives`, `concepts` (`composed_of`) and
`applications` (`needs`), each node with `defines`, optional `labels: {lang: text}`, `diagram: {type, hint}`, `tier`.
The algorithms are zinet's (`zinet.algorithms`); this module only maps the YAML onto the DiGraph they take.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx
import yaml
from zinet import algorithms as za
from zinet.dag import CycleError

from .exceptions import BookError

SECTIONS = ("primitives", "concepts", "applications")
_EDGE_KEY = {"primitives": None, "concepts": "composed_of", "applications": "needs"}
_KIND = {"primitives": "primitive", "concepts": "concept", "applications": "application"}

LANGUAGE_NAMES = {
    "en": "English", "zh": "Chinese (中文)", "fr": "French (Français)", "es": "Spanish (Español)", "de": "German (Deutsch)",
    "ja": "Japanese (日本語)", "ko": "Korean (한국어)", "pt": "Portuguese (Português)", "ru": "Russian (Русский)",
    "it": "Italian (Italiano)", "ar": "Arabic (العربية)", "hi": "Hindi (हिन्दी)",
}


def language_name(code: str) -> str:
    """An explicit language name: weaker and local models follow it more reliably than a bare ISO code."""
    return LANGUAGE_NAMES.get((code or "").strip().lower(), code)


def title_label(node_id: str) -> str:
    return node_id.replace("_", " ").title()


@dataclass
class ChapterGraph:
    path: Path
    data: dict[str, Any]
    G: nx.DiGraph

    @property
    def chapter_id(self) -> str:
        """The chapter's folder name (`chapters/<id>/graph.yaml`), else the file's own `domain:` value."""
        p = self.path
        if p.parent.name == "input" and p.stem == "graph":
            return p.parent.parent.name
        if p.stem == "graph" and p.parent.parent.name == "chapters":
            return p.parent.name
        return str(self.data.get("domain") or p.stem)

    def node(self, node_id: str) -> dict[str, Any]:
        for section in SECTIONS:
            n = (self.data.get(section) or {}).get(node_id)
            if n is not None:
                return n or {}
        return {}

    def kind(self, node_id: str) -> str:
        return self.G.nodes[node_id].get("kind", "")

    def primitives(self) -> list[str]:
        return list((self.data.get("primitives") or {}).keys())

    def label(self, node_id: str, lang: str = "en") -> str:
        labels = self.node(node_id).get("labels") or {}
        return labels.get(lang) or labels.get("en") or self.node(node_id).get("label") or title_label(node_id)

    def has_label(self, node_id: str, lang: str) -> bool:
        return bool((self.node(node_id).get("labels") or {}).get(lang))

    def context(self, node_id: str) -> str:
        """The node's own `defines` (the "surrounding context" of its section), with its diagram hint appended."""
        n = self.node(node_id)
        if not n:
            return node_id
        text = n.get("defines") or node_id
        d = n.get("diagram")
        if d:
            text += f" [Diagram: {d.get('type', 'auto')} — {d.get('hint', '')}]"
        return text

    def default_target(self) -> str:
        """The last application in teaching order, else the last node."""
        order = za.productivity_order(self.G, 1.0, weighted_applications=True)
        apps = [n for n in order if self.kind(n) == "application"]
        return (apps or order)[-1]

    def plan(self, target: str, payoff_weight: float = 1.5) -> list[str]:
        """The teaching order of everything `target` needs, `target` last. Primitives come first, as in the ConceptBook apps."""
        if target not in self.G or "kind" not in self.G.nodes[target]:
            raise BookError(f"{self.path}: target {target!r} is not a node of this graph")
        needed = nx.ancestors(self.G, target) | {target}
        return za.productivity_order(za.restrict(self.G, needed), payoff_weight, weighted_applications=True,
                                     seed_by_reach=True, include_primitives=True)

    def applications_of(self, target: str) -> list[str]:
        return za.applications_of(self.G, target)

    def prerequisites(self, node_id: str) -> list[str]:
        return sorted(self.G.predecessors(node_id))

    def new_primitives(self, text: str) -> int:
        """How many primitive names a section mentions (the primitive-budget check)."""
        low = text.lower()
        return sum(1 for p in self.primitives() if p.replace("_", " ") in low or p in low)

    def validate(self) -> list[str]:
        """Hard problems, as messages; empty when the graph can be generated from."""
        problems: list[str] = []
        if not za.is_acyclic(self.G):
            try:
                za.ensure_acyclic(self.G)
            except CycleError as e:
                problems.append(f"cycle among: {e}")
        undeclared = sorted(n for n in self.G if "kind" not in self.G.nodes[n])
        if undeclared:
            problems.append(f"named as a prerequisite but never declared: {', '.join(undeclared)}")
        elif not za.reducible(self.G, self.primitives(), rule="declared"):
            problems.append("some concepts do not reduce to primitives")
        for section in SECTIONS:
            for nid, attrs in (self.data.get(section) or {}).items():
                if not (attrs or {}).get("defines"):
                    problems.append(f"{section[:-1]} {nid!r} has no 'defines' text")
        return problems


def load_graph(path: str | Path) -> ChapterGraph:
    p = Path(path).expanduser()
    if not p.is_file():
        raise BookError(f"no graph at {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise BookError(f"{p}: not valid YAML: {e}") from e
    if not isinstance(data, dict) or not any(k in data for k in SECTIONS):
        raise BookError(f"{p}: not a ConceptBook graph (needs primitives:, concepts: or applications:)")
    G = nx.DiGraph()
    for section in SECTIONS:
        for nid, attrs in (data.get(section) or {}).items():
            attrs = attrs or {}
            edge_key = _EDGE_KEY[section]
            deps = list(attrs.get(edge_key) or []) if edge_key else []
            G.add_node(nid, kind=_KIND[section], composed_of=deps)
            for d in deps:
                G.add_edge(d, nid)
    return ChapterGraph(path=p, data=data, G=G)


def section_params(language: str, style: str, model: str, context: str) -> str:
    """The section cache's key parameters. All four matter: the language, the style (level), the model, and a hash of the
    node's `defines` so editing a definition (or re-declaring the node elsewhere with another one) invalidates the entry."""
    return json.dumps({"language": language, "style": style, "model": model,
                       "ctx": hashlib.sha256((context or "").encode("utf-8")).hexdigest()[:16]}, sort_keys=True, ensure_ascii=False)


def retitle_heading(section: str, node_id: str, label: str, has_label: bool) -> str:
    """Make a section's leading `## heading` match the node's label in the page language.

    When the graph names the node in that language the heading is replaced whatever the model wrote; otherwise only a bare
    node-id heading is (a model's own translated heading on a non-English page stays)."""
    if has_label:
        return re.sub(r"\A\s*##[ \t]+[^\n]*", f"## {label}", section, count=1)
    return re.sub(r"^##\s+" + re.escape(node_id) + r"[ \t]*$", f"## {label}", section, count=1, flags=re.MULTILINE)
