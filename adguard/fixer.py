"""Fixer: rewrites only the flagged claims. Output is shape-checked in code; one retry, then ModelFailure."""
from . import llm, shape
from .models import Fix, Review

PROMPT = llm.PROMPTS / "fixer.md"


def fix(review: Review) -> Fix:
    problems = "\n".join(f"- {f.rule_id}: " + (f"\"{f.quote}\" " if f.quote else "") + f"({f.reason})"
                         for f in review.findings)
    return llm.call(llm.load_prompt(PROMPT), f"<ad>\n{review.text}\n</ad>\n\nViolations:\n{problems}", Fix,
                    check=lambda f: shape.problem(f.text, original=review.text))
