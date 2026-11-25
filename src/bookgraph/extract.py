"""`bookgraph extract`: a chapter's chunks.yaml (bookgraph ingest) -> its graph.yaml, as the extract_graph recipe.

The two model passes and their prompts are concept-book-press's (pipeline/extract/), the subject no longer fixed to
"a physics textbook". What this module adds are the recipe's deterministic steps:

    extract_prepare   format the chunks as press did; may it write the graph (write_ok)
    extract_concepts  pass 1's answer -> concepts (fences stripped, press's YAML repair), or why it cannot be read
    extract_check     pass 2's (or the repair's) answer -> a normalized graph and its problems:
                        a concept with no prerequisite is a primitive (press documents this as a manual fix), a primitive
                        with prerequisites a concept; dependencies on unknown ids or on the node itself are dropped;
                        pass 1's labels are kept (press's graphs had none); tiers are computed from the edges
                        (primitives 0, a node 1 + its highest prerequisite); then book-graph's own validation
    extract_write     (risky) graph.yaml, in press's readable YAML style, and concept_sources.yaml (concept -> sections)
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import networkx as nx
import yaml

from .exceptions import BookError

SECTIONS = ("primitives", "concepts", "applications")
_DEPS = {"primitives": None, "concepts": "composed_of", "applications": "needs"}

# ── concept-book-press pipeline/extract/ (graph_extractor.py, prompts.py), verbatim but for two fixes in _repair_yaml ──────────────────────────────────

_FREE_TEXT_RE = re.compile(
    r'^(\s+(?:defines|label|chapter_title|section_title):\s+)(.+)$',
    flags=re.MULTILINE,
)


def _strip_yaml_fences(text: str) -> str:
    """Strip markdown code fences from LLM output if present."""
    lines = text.strip().split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _repair_yaml(text: str) -> str:
    """Quote free-text values that would otherwise break YAML parsing (a bare colon, a partly quoted value)."""
    def _quote(m: re.Match) -> str:
        key_part, value = m.group(1), m.group(2)
        stripped = value.rstrip()
        if stripped[:1] in ("[", "{", "|", ">"):
            return key_part + value
        if stripped[:1] in ("'", '"') and not (len(stripped) > 1 and stripped.endswith(stripped[0])):
            return key_part + value          # a quoted value that goes on to the next line: not broken (second fix)
        try:
            # A scalar only: `Something measured: a length` parses as a mapping on its own, so press's check (any parse)
            # let exactly the colon case through, and the document then failed. (The one change to press's code.)
            valid = isinstance(yaml.safe_load(stripped), (str, int, float, bool, type(None)))
        except yaml.YAMLError:
            valid = False
        if not valid:
            value = '"' + stripped.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return key_part + value

    return _FREE_TEXT_RE.sub(_quote, text)


def format_chunks_for_prompt(chunks_data: dict) -> str:
    """Format chunk data into a text block for the LLM prompt."""
    parts = []
    for i, chunk in enumerate(chunks_data.get("chunks", []), 1):
        section_id = chunk.get("section_id", f"section_{i}")
        parts.append(f"## Section {i} [id={section_id}]: {chunk.get('section_title', 'Untitled')}")
        parts.append(chunk.get("text", ""))
        if chunk.get("math_expressions"):
            parts.append("Math: " + "; ".join(chunk["math_expressions"]))
        if chunk.get("key_terms"):
            parts.append("Key terms: " + ", ".join(chunk["key_terms"]))
        parts.append("")
    if chunks_data.get("key_terms"):
        parts.append("## Chapter Key Terms")
        parts.append(", ".join(chunks_data["key_terms"]))
    return "\n".join(parts)


def _is_prose(value: str) -> bool:
    return "\n" in value or (" " in value and len(value) > 40)


def _represent_str(dumper: "_ReadableDumper", data: str):
    if _is_prose(data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


class _ReadableDumper(yaml.SafeDumper):
    """SafeDumper with block-literal style for prose (defines), so the graph is easy to edit by hand."""


_ReadableDumper.add_representer(str, _represent_str)


def dump_graph(graph: dict) -> str:
    return yaml.dump(graph, Dumper=_ReadableDumper, default_flow_style=False, sort_keys=False, allow_unicode=True, width=100)

# ── the recipe's steps ─────────────────────────────────────────────────────────────────────────────────────────────


def _read_yaml(text: str) -> tuple[Any, str]:
    """Parse as it is, and repair only if that fails: the repair works line by line, so applied to valid YAML (a quoted
    value PyYAML wrapped onto two lines) it can break it, as press's did for 31 of its 190 extracted graphs."""
    text = _strip_yaml_fences(text or "")
    try:
        return yaml.safe_load(text), ""
    except yaml.YAMLError:
        pass
    try:
        return yaml.safe_load(_repair_yaml(text)), ""
    except yaml.YAMLError as e:
        return None, f"not valid YAML: {str(e).splitlines()[0]}"


def prepare(i: dict[str, Any]) -> dict[str, Any]:
    from .steps import write_root_ok
    try:
        chunks = yaml.safe_load(Path(i["chunks"]).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise BookError(f"cannot read chunks {i['chunks']}: {e}") from e
    if not isinstance(chunks, dict) or not chunks.get("chunks"):
        raise BookError(f"{i['chunks']}: no chunks (expected bookgraph ingest's chunks.yaml)")
    text = format_chunks_for_prompt(chunks)
    return {"chunks_text": text, "sections": len(chunks["chunks"]), "chars": len(text),
            "write_ok": write_root_ok(i["out_dir"], i.get("write_roots", ""))}


def concepts(i: dict[str, Any]) -> dict[str, Any]:
    """Pass 1's answer -> {ok, concepts_yaml (for pass 2), labels, error}."""
    data, err = _read_yaml(i["answer"])
    items = (data or {}).get("concepts") if isinstance(data, dict) else None
    if err or not isinstance(items, list) or not items:
        return {"ok": False, "concepts_yaml": "", "labels": {}, "error": err or "no `concepts:` list in the answer"}
    good = [c for c in items if isinstance(c, dict) and c.get("id")]
    labels = {str(c["id"]): str(c.get("label") or "") for c in good if c.get("label")}
    sections = {str(c["id"]): [str(s) for s in c.get("section_ids") or []] for c in good}
    return {"ok": True, "concepts_yaml": yaml.dump({"concepts": good}, default_flow_style=False, allow_unicode=True),
            "labels": labels, "sections": sections, "count": len(good), "error": ""}


def normalize(graph: dict[str, Any], labels: dict[str, str], domain: str) -> tuple[dict[str, Any], list[str]]:
    """The deterministic fixes; returns (graph, notes)."""
    notes: list[str] = []
    nodes: dict[str, tuple[str, dict[str, Any]]] = {}
    for section in SECTIONS:
        sec = graph.get(section) or {}
        if not isinstance(sec, dict):
            notes.append(f"'{section}' was not a mapping: dropped")
            continue
        for nid, attrs in sec.items():
            nid = str(nid)
            if nid in nodes:
                notes.append(f"{nid!r} declared twice: kept the {nodes[nid][0][:-1]}")
                continue
            nodes[nid] = (section, dict(attrs or {}) if isinstance(attrs, dict) else {"defines": str(attrs or "")})
    known = set(nodes)
    for nid, (section, attrs) in list(nodes.items()):
        a, b = attrs.pop("composed_of", None), attrs.pop("needs", None)
        raw = a or b or []
        deps = [str(d) for d in (raw if isinstance(raw, list) else [raw])]
        unknown = [d for d in deps if d not in known]
        if unknown:
            notes.append(f"{nid}: dropped unknown prerequisite(s) {', '.join(unknown)}")
        if nid in deps:
            notes.append(f"{nid}: dropped itself as a prerequisite")
        deps = list(dict.fromkeys(d for d in deps if d in known and d != nid))
        if section == "concepts" and not deps:
            section = "primitives"
            notes.append(f"{nid}: a concept with no prerequisite, moved to primitives")
        elif section == "primitives" and deps:
            section = "concepts"
            notes.append(f"{nid}: a primitive with prerequisites, moved to concepts")
        if _DEPS[section]:
            attrs[_DEPS[section]] = deps
        if not attrs.get("label") and labels.get(nid):
            attrs["label"] = labels[nid]
        nodes[nid] = (section, attrs)
    G = nx.DiGraph()
    G.add_nodes_from(nodes)
    for nid, (section, attrs) in nodes.items():
        for d in attrs.get(_DEPS[section] or "", []) if _DEPS[section] else []:
            G.add_edge(d, nid)
    if nx.is_directed_acyclic_graph(G):
        tier: dict[str, int] = {}
        for n in nx.topological_sort(G):
            preds = list(G.predecessors(n))
            tier[n] = 1 + max(tier[p] for p in preds) if preds else 0
        changed = sum(1 for n, (_, a) in nodes.items() if a.get("tier") != tier[n])
        for n, (_, a) in nodes.items():
            a["tier"] = tier[n]
        if changed:
            notes.append(f"tiers computed from the edges ({changed} changed)")
    out: dict[str, Any] = {"domain": graph.get("domain") or domain}
    for section in SECTIONS:
        out[section] = {}
    for nid, (section, attrs) in nodes.items():
        order = ["label", "defines", _DEPS[section], "tier"]
        out[section][nid] = {k: attrs[k] for k in order if k and k in attrs} | {k: v for k, v in attrs.items() if k not in order}
    return out, notes


def check(i: dict[str, Any]) -> dict[str, Any]:
    """Pass 2's (or the repair's) answer -> {ok, graph_yaml, problems, notes}."""
    from .graph import load_graph
    data, err = _read_yaml(i["answer"])
    if err or not isinstance(data, dict) or not any(k in data for k in SECTIONS):
        return {"ok": False, "graph_yaml": i.get("answer") or "", "problems": err or "no primitives/concepts/applications in the answer",
                "notes": ""}
    graph, notes = normalize(data, dict(i.get("labels") or {}), str(i.get("domain") or ""))
    text = dump_graph(graph)
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "graph.yaml"
        f.write_text(text, encoding="utf-8")
        try:
            problems = load_graph(f).validate()
        except BookError as e:
            problems = [str(e).split(": ", 1)[-1]]
    return {"ok": not problems, "graph_yaml": text, "problems": "\n".join(f"- {p}" for p in problems) or "none",
            "notes": "; ".join(notes) or "none", "nodes": sum(len(graph[s]) for s in SECTIONS)}


def write(i: dict[str, Any]) -> dict[str, Any]:
    """Write graph.yaml (or graph.draft.yaml when it is still invalid) and concept_sources.yaml into out_dir."""
    out = Path(i["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    checked = i["checked"]                                              # extract_check's result: graph_yaml, notes, nodes
    valid = bool(i.get("valid", checked.get("ok")))
    path = out / ("graph.yaml" if valid else "graph.draft.yaml")
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(checked["graph_yaml"], encoding="utf-8")
    tmp.replace(path)
    try:
        chunks = yaml.safe_load(Path(i["chunks"]).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        chunks = {}
    titles = {c.get("section_id", ""): c.get("section_title", "") for c in chunks.get("chunks", []) if c.get("section_id")}
    labels, sections = dict(i.get("labels") or {}), dict(i.get("sections") or {})
    sources = {cid: {"label": labels.get(cid, cid), "sections": [{"id": s, "title": titles.get(s, s)} for s in sids if s]}
               for cid, sids in sections.items()}
    if valid and sources:
        (out / "concept_sources.yaml").write_text(
            yaml.dump({"domain": i.get("domain", ""), "sources": sources}, default_flow_style=False, sort_keys=False,
                      allow_unicode=True, width=100), encoding="utf-8")
    return {"graph": str(path), "summary": f"{'wrote' if valid else 'still invalid, kept the draft as'} {path.name} "
                                           f"({checked.get('nodes', '?')} nodes; normalized: {checked.get('notes', 'none')})"
                                           + ("" if valid else f"; problems: {checked.get('problems', '')}")}


STEPS = {"extract_prepare": prepare, "extract_concepts": concepts, "extract_check": check, "extract_write": write}
