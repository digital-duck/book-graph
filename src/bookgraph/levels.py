"""Level -> style. `level` is the learner's progression; `style` is what the prompt actually changes with."""
from __future__ import annotations

LEVEL_TO_STYLE = {"intro": "feynman", "core": "core", "college": "college", "research": "research"}
# At research level, only these tags keep full proof-notation rigor; any other subject gets `research_applied`.
STEM_MATH_TAGS = {"math", "physics", "engineering"}


def resolve_style(level: str, tags: list[str] | tuple[str, ...] = ()) -> str:
    style = LEVEL_TO_STYLE.get(level, "college")
    if style == "research" and not (STEM_MATH_TAGS & set(tags)):
        return "research_applied"
    return style
