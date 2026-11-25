"""Running a shipped recipe: book-graph's defaults over GraphLang's `graphlang.recipes`.

    from bookgraph.run import run_recipe
    result = run_recipe("generate_chapter", {"chapter_graph": ..., ...}, llm="claude_cli:claude-sonnet-5-5", max_llm_calls=60)
    result.outputs, result.exit, result.llm_calls, result.log_path

Recipes are found by name under `bookgraph/recipes/<name>/<name>.graph.yaml`, the model defaults to `claude_cli`, and run logs
go to ~/book-graph/logs. Everything else (the call meter, `python3` as this interpreter, checkpoints, exits) is GraphLang's,
shared with app-graph and work-graph.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from graphlang.recipes import Recipe, RecipeError, RecipeRun

__all__ = ["RECIPES", "DEFAULT_LLM", "LOG_DIR", "Recipe", "RecipeError", "RecipeRun", "recipe_path", "run_recipe"]

RECIPES = Path(__file__).parent / "recipes"
DEFAULT_LLM = "claude_cli"
LOG_DIR = Path.home() / "book-graph" / "logs"


def recipe_path(name: str | Path) -> Path:
    """A shipped recipe by name (recipes/<name>/<name>.graph.yaml), or a path to any recipe file."""
    p = Path(name)
    if p.suffix == ".yaml":
        return p
    return RECIPES / str(name) / f"{name}.graph.yaml"


def run_recipe(name: str | Path, inputs: dict[str, Any], *, llm: str | Any = DEFAULT_LLM, max_llm_calls: Optional[int] = None,
               max_steps: int = 5000, workers: int = 1, wall_time_s: Optional[float] = None, checkpoint: Optional[Path] = None,
               resume: Optional[Path] = None, record: Optional[Path] = None, replay: Optional[Path] = None,
               log_dir: Optional[Path] = LOG_DIR, log_level: str = "info", on_node: Optional[Callable[[dict], None]] = None,
               llm_timeout: Optional[int] = None, max_tokens: Optional[int] = None) -> RecipeRun:
    """Run a recipe to its end and return its outputs. Raises RecipeError when it stops early (a model-call or step limit,
    a node that failed, a policy that refuses the graph); a run that ends at an error exit returns with `.exit` set."""
    return Recipe(recipe_path(name)).run(
        inputs, llm=llm, max_llm_calls=max_llm_calls, max_steps=max_steps, workers=workers, wall_time_s=wall_time_s,
        checkpoint=checkpoint, resume=resume, record=record, replay=replay, log_dir=log_dir, log_level=log_level, on_node=on_node,
        llm_options={"timeout": int(llm_timeout)} if llm_timeout else None, max_tokens=max_tokens)
