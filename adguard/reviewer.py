"""Reviewer: deterministic pass first, then the LLM claim pass. Either can block; neither can clear the other.

The two passes are independent (the LLM never sees the rules hits), so the LLM pass can be scored on its own.
"""
from pathlib import Path
from typing import Callable

from . import llm, rules
from .models import Escalation, Finding, LLMReview, Review, RuleId

PROMPT = llm.PROMPTS / "reviewer.md"
LLMFn = Callable[[str], LLMReview]


def _usable(r: LLMReview) -> str | None:
    """Every ad makes at least one claim; an empty review means the model didn't do the job."""
    return "no claims extracted" if not r.claims else None


def llm_pass(prompt: Path = PROMPT) -> LLMFn:
    system = llm.load_prompt(prompt)
    return lambda text: llm.call(system, f"<ad>\n{text}\n</ad>", LLMReview, check=_usable)


def locate(text: str, quote: str) -> tuple[int, int] | None:
    i = text.find(quote)
    if i < 0:
        i = text.lower().find(quote.lower())
    return (i, i + len(quote)) if i >= 0 and quote else None


def review(text: str, use_rules: bool = True, use_llm: bool = True, llm_fn: LLMFn | None = None) -> Review:
    rev = Review(text=text)
    if use_rules:
        rev.passes.append("rules")
        rev.findings += rules.check(text)
    if use_llm:
        rev.passes.append("llm")
        try:
            out = (llm_fn or llm_pass())(text)
        except llm.ModelFailure as e:
            rev.escalations.append(Escalation(stage="reviewer", kind=e.kind, detail=e.detail))
        else:
            rev.claims = out.claims
            for c in out.claims:
                # A quote that isn't in the ad keeps its finding (span=None): code never drops an LLM finding.
                span = locate(text, c.quote)
                rev.findings += [Finding(rule_id=r, source="llm", reason=c.reason, quote=c.quote, span=span)
                                 for r in c.rule_ids]
            if missing := [k.replace("_", " ") for k, said in out.disclosures.model_dump().items() if not said]:
                rev.findings.append(Finding(rule_id=RuleId("R7"), source="llm",
                                            reason=f"ad does not say: {', '.join(missing)}"))
    rev.findings = _dedupe(rev.findings)
    return rev


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Keep every flagged phrase; drop exact repeats (same rule, same quote). Rules hits come first and win."""
    seen, out = set(), []
    for f in sorted(findings, key=lambda f: f.source != "rules"):
        key = (f.rule_id, (f.quote or "").strip().lower())
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
