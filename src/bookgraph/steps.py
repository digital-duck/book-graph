"""The deterministic steps of the generate_chapter recipe: plain functions, JSON in and JSON out, no model call.

The recipe runs each as `python3 -I -m bookgraph.tool STEP` (stdin: a JSON object whose keys are the recipe port names
without `in_`; stdout: one JSON value). A graph that cannot be generated from stops in `prepare`, before any model call.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from .cache import cache_get, cache_put
from .legacy import legacy_get, legacy_params
from .book import model_dir_name
from .exceptions import BookError
from .graph import ChapterGraph, language_name, load_graph, section_params
from .pages import write_book_index, write_concept_page
from .styles import infer_subject_rigor, style_instruction

_PROMPTS = Path(__file__).parent / "recipes" / "generate_chapter" / "prompts"


def write_roots_from_env() -> str:
    """BOOKGRAPH_WRITE_ROOTS as one string, for the recipe's `write_roots` input. Read by the caller (builder), never by a step:
    a recipe step runs with only PATH and PYTHONUTF8, so it cannot see the caller's environment."""
    return os.environ.get("BOOKGRAPH_WRITE_ROOTS", "")


def write_root_ok(path: str, extra_roots: str = "") -> bool:
    """May files be written here: absolute, and under the home folder, the temporary folder or one of `extra_roots`
    (`os.pathsep`-separated)."""
    p = Path(path)
    if not p.is_absolute() or ".." in p.parts:
        return False
    roots = [Path.home(), Path(tempfile.gettempdir())] + [Path(r) for r in extra_roots.split(os.pathsep) if r]
    return any(p.resolve().is_relative_to(r.resolve()) for r in roots)


def cache_salt(style_guide: str, primitive_budget: int) -> str:
    """A hash of what shapes a section besides the node: the three prompt files, the resolved style guide and the primitive
    budget. In every cache key, so editing a prompt or a style profile does not keep serving old sections."""
    h = hashlib.sha256()
    for name in ("write_section", "refine_section", "write_payoff"):
        h.update((_PROMPTS / f"{name}.md").read_bytes())
    h.update(style_guide.encode("utf-8"))
    h.update(str(primitive_budget).encode())
    return h.hexdigest()[:16]


def _plan(i: dict[str, Any]) -> tuple[ChapterGraph, list[str], str]:
    g = load_graph(i["chapter_graph"])
    target = i.get("target") or g.default_target()
    return g, g.plan(target, float(i.get("payoff_weight", 1.5))), target


def _params(i: dict[str, Any], g: ChapterGraph, context: str) -> str:
    return section_params(i["language"], i["style"], i.get("model", ""), context, i.get("key_salt", ""))


def prepare(i: dict[str, Any]) -> dict[str, Any]:
    g = load_graph(i["chapter_graph"])
    problems = g.validate()
    if problems:
        raise BookError(f"{g.path}: " + "; ".join(problems))
    g, order, target = _plan(i)
    # the subject (book slug and graph `domain:`) decides how much formalism the style guide asks for; a chapter folder
    # name such as `ch01` says nothing about it
    rigor = infer_subject_rigor(" ".join(x for x in (i.get("subject", ""), str(g.data.get("domain") or "")) if x) or g.chapter_id)
    guide = style_instruction(i["style"], rigor)
    roots = i.get("write_roots", "")
    return {"n": len(order), "target": target, "order": order, "target_kind": g.kind(target), "style_guide": guide,
            "key_salt": cache_salt(guide, int(i.get("primitive_budget", 2))), "language_label": language_name(i["language"]),
            "write_ok": write_root_ok(i["output_dir"], roots) and write_root_ok(i["cache_dir"], roots)}


def _legacy(i: dict[str, Any], key: str, context: str) -> str | None:
    """The SPL apps' cached text for `key` (bookgraph.legacy), when a legacy cache is given and the cache is not skipped."""
    if i.get("skip_cache") or not i.get("legacy_cache"):
        return None
    return legacy_get(i["legacy_cache"], key, legacy_params(i["language"], i["style"], model_dir_name(i.get("model", "")), context))


def round_(i: dict[str, Any]) -> dict[str, Any]:
    """Section `node_index`: which node, its label and context, and the cached section when there is one: this library's
    cache first, then the SPL app's (`legacy_cache`), whose hit `store` copies into this library's cache."""
    g, order, _ = _plan(i)
    node = order[int(i["node_index"])]
    context, label = g.context(node), g.label(node, i["language"])
    key_ctx = f"{label}\n{context}"                     # both reach the prompt, so both are in the key
    hit = None if i.get("skip_cache") else cache_get(i["cache_dir"], node, _params(i, g, key_ctx))
    source = "cache" if hit is not None else ""
    if hit is None and (hit := _legacy(i, node, context)) is not None:
        source = "legacy"
    return {"node": node, "label": label, "context": context, "key_ctx": key_ctx, "hit": hit is not None, "cached": hit or "",
            "source": source or "generated"}


def check(i: dict[str, Any]) -> dict[str, Any]:
    """The primitive-budget check on a written section: feedback for one refinement when it introduces more primitives than the
    budget allows (see ChapterGraph.new_primitives), naming them."""
    g = load_graph(i["chapter_graph"])
    budget = int(i.get("primitive_budget", 2))
    found = g.new_primitives(i["section"], i.get("node", ""), i.get("language", "en"))
    if len(found) <= budget:
        return {"ok": True, "feedback": ""}
    names = ", ".join(g.label(p, i.get("language", "en")) for p in found)
    return {"ok": False, "feedback": f"introduce at most {budget} new primitive(s) per section; this one introduces {len(found)}: {names}"}


def store(i: dict[str, Any]) -> dict[str, Any]:
    """Cache the section (unless it came from the cache) and write its concept page."""
    g, order, target = _plan(i)
    rnd = i["round"]
    node, section = rnd["node"], i["section"]
    if not rnd["hit"] or rnd.get("source") == "legacy":         # a legacy hit is copied, so later runs need no legacy cache
        cache_put(i["cache_dir"], node, _params(i, g, rnd["key_ctx"]), section)
    page = write_concept_page(g, node, section, i["output_dir"], i["language"], chapter_title=i.get("chapter_title", ""),
                              level=i.get("level", ""), model=model_dir_name(i.get("model", "")))
    line = f"{node}: {rnd.get('source') or ('cache' if rnd['hit'] else 'generated')} -> {page.name}"
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
    prereqs = ", ".join(g.label(p, lang) for p in g.prerequisites(target)) or "none"
    apps = ", ".join(g.label(a, lang) for a in g.applications_of(target)) or "none"
    label = g.label(target, lang)
    key_ctx = "\n".join((label, ctx, prereqs, apps))     # the payoff prompt carries all four
    hit = None if i.get("skip_cache") else cache_get(i["cache_dir"], target + "__payoff", _params(i, g, key_ctx))
    if hit is None:                                      # the SPL app's payoff, keyed on the target's context alone (read each run)
        hit = _legacy(i, target + "__payoff", ctx)
    out.update(target_label=label, target_context=ctx, prereqs=prereqs, apps=apps, key_ctx=key_ctx,
               hit=hit is not None, cached=hit or "")
    return out


def payoff_store(i: dict[str, Any]) -> dict[str, Any]:
    g, _, target = _plan(i)
    rnd = i["round"]
    if not rnd["hit"]:
        cache_put(i["cache_dir"], target + "__payoff", _params(i, g, rnd["key_ctx"]), i["payoff"])
    return {"payoff": i["payoff"]}


def finish(i: dict[str, Any]) -> dict[str, Any]:
    """Write the book index (TOC + payoff) and return its path."""
    g, order, target = _plan(i)
    page = write_book_index(g, order, target, i.get("payoff") or "", i["output_dir"], i["language"],
                            chapter_title=i.get("chapter_title", ""), level=i.get("level", ""),
                            model=model_dir_name(i.get("model", "")))
    return {"book": str(page)}


STEPS = {"prepare": prepare, "round": round_, "check": check, "store": store, "payoff_round": payoff_round,
         "payoff_store": payoff_store, "finish": finish}
