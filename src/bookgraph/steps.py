"""The deterministic steps of the generate_chapter recipe: plain functions, JSON in and JSON out, no model call.

The recipe runs each as `python3 -I -m bookgraph.tool STEP` (stdin: a JSON object whose keys are the recipe port names
without `in_`; stdout: one JSON value). A graph that cannot be generated from stops in `prepare`, before any model call.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from .cache import cache_get, cache_put
from .exceptions import BookError
from .graph import ChapterGraph, language_name, load_graph, section_params
from .pages import write_book_index, write_concept_page
from .styles import infer_subject_rigor, style_instruction

_PRIMITIVE_FEEDBACK = "introduce at most one new primitive per section"


def write_root_ok(path: str) -> bool:
    """May files be written here: absolute, and under the home folder, the temporary folder or BOOKGRAPH_WRITE_ROOTS."""
    p = Path(path)
    if not p.is_absolute() or ".." in p.parts:
        return False
    roots = [Path.home(), Path(tempfile.gettempdir())] + [Path(r) for r in os.environ.get("BOOKGRAPH_WRITE_ROOTS", "").split(os.pathsep) if r]
    return any(p.resolve().is_relative_to(r.resolve()) for r in roots)


def _plan(i: dict[str, Any]) -> tuple[ChapterGraph, list[str], str]:
    g = load_graph(i["chapter_graph"])
    target = i.get("target") or g.default_target()
    return g, g.plan(target, float(i.get("payoff_weight", 1.5))), target


def _params(i: dict[str, Any], g: ChapterGraph, context: str) -> str:
    return section_params(i["language"], i["style"], i.get("model", ""), context)


def prepare(i: dict[str, Any]) -> dict[str, Any]:
    g = load_graph(i["chapter_graph"])
    problems = g.validate()
    if problems:
        raise BookError(f"{g.path}: " + "; ".join(problems))
    g, order, target = _plan(i)
    return {"n": len(order), "target": target, "order": order, "target_kind": g.kind(target),
            "style_guide": style_instruction(i["style"], infer_subject_rigor(g.chapter_id)),
            "language_label": language_name(i["language"]), "write_ok": write_root_ok(i["output_dir"]) and write_root_ok(i["cache_dir"])}


def round_(i: dict[str, Any]) -> dict[str, Any]:
    """Section `node_index`: which node, its label and context, and the cached section when there is one."""
    g, order, _ = _plan(i)
    node = order[int(i["node_index"])]
    context = g.context(node)
    hit = None if i.get("skip_cache") else cache_get(i["cache_dir"], node, _params(i, g, context))
    return {"node": node, "label": g.label(node, i["language"]), "context": context, "hit": hit is not None, "cached": hit or ""}


def check(i: dict[str, Any]) -> dict[str, Any]:
    """The primitive-budget check on a written section: feedback for one refinement when it names too many primitives."""
    g = load_graph(i["chapter_graph"])
    over = g.new_primitives(i["section"]) > int(i.get("primitive_budget", 2))
    return {"ok": not over, "feedback": _PRIMITIVE_FEEDBACK if over else ""}


def store(i: dict[str, Any]) -> dict[str, Any]:
    """Cache the section (unless it came from the cache) and write its concept page."""
    g, order, target = _plan(i)
    rnd = i["round"]
    node, section = rnd["node"], i["section"]
    if not rnd["hit"]:
        cache_put(i["cache_dir"], node, _params(i, g, rnd["context"]), section)
    page = write_concept_page(g, node, section, i["output_dir"], i["language"], chapter_title=i.get("chapter_title", ""),
                              level=i.get("level", ""), model=i.get("model", ""))
    line = f"{node}: {'cache' if rnd['hit'] else 'generated'} -> {page.name}"
    return {"manifest": (i.get("manifest") or "") + line + "\n"}


def payoff_round(i: dict[str, Any]) -> dict[str, Any]:
    """The capstone's payoff inputs. Only an application target has one; others skip it with no model call."""
    g, order, target = _plan(i)
    needed = g.kind(target) == "application"
    out: dict[str, Any] = {"needed": needed, "hit": False, "cached": "", "target_label": "", "target_context": "", "prereqs": "", "apps": ""}
    if not needed:
        return out
    lang = i["language"]
    ctx = g.context(target)
    key = target + "__payoff"
    hit = None if i.get("skip_cache") else cache_get(i["cache_dir"], key, _params(i, g, ctx))
    out.update(target_label=g.label(target, lang), target_context=ctx,
               prereqs=", ".join(g.label(p, lang) for p in g.prerequisites(target)) or "none",
               apps=", ".join(g.label(a, lang) for a in g.applications_of(target)) or "none",
               hit=hit is not None, cached=hit or "")
    return out


def payoff_store(i: dict[str, Any]) -> dict[str, Any]:
    g, _, target = _plan(i)
    rnd = i["round"]
    if not rnd["hit"]:
        cache_put(i["cache_dir"], target + "__payoff", _params(i, g, rnd["target_context"]), i["payoff"])
    return {"payoff": i["payoff"]}


def finish(i: dict[str, Any]) -> dict[str, Any]:
    """Write the book index (TOC + payoff) and return its path."""
    g, order, target = _plan(i)
    page = write_book_index(g, order, target, i.get("payoff") or "", i["output_dir"], i["language"],
                            chapter_title=i.get("chapter_title", ""), level=i.get("level", ""), model=i.get("model", ""))
    return {"book": str(page)}


STEPS = {"prepare": prepare, "round": round_, "check": check, "store": store, "payoff_round": payoff_round,
         "payoff_store": payoff_store, "finish": finish}
