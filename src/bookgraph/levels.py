"""Level -> style. `level` is the learner's progression; `style` is what the prompt actually changes with."""
from __future__ import annotations

LEVEL_TO_STYLE = {"intro": "feynman", "core": "core", "college": "college", "research": "research"}
# At research level, only these tags keep full proof-notation rigor; any other subject gets `research_applied`.
STEM_MATH_TAGS = {"math", "physics", "engineering"}


def resolve_style(level: str, tags: list[str] | tuple[str, ...] = ()) -> str:
    if level not in LEVEL_TO_STYLE:
        raise ValueError(f"unknown level {level!r}; one of {', '.join(LEVEL_TO_STYLE)}")
    style = LEVEL_TO_STYLE[level]
    if style == "research" and not (STEM_MATH_TAGS & set(tags)):
        return "research_applied"
    return style
