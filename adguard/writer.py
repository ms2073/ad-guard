"""Writer: one-line brief -> 3 ad variants. Output is shape-checked in code; one retry, then ModelFailure."""
from . import llm, shape
from .models import Variants

PROMPT = llm.PROMPTS / "writer.md"


def _usable(v: Variants) -> str | None:
    if len(v.variants) != 3:
        return f"expected 3 variants, got {len(v.variants)}"
    return next((f"variant {i + 1}: {p}" for i, t in enumerate(v.variants) if (p := shape.problem(t))), None)


def write(brief: str) -> list[str]:
    out = llm.call(llm.load_prompt(PROMPT), f"Brief: {brief}", Variants, check=_usable)
    return [v.strip() for v in out.variants]
