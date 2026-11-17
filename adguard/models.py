"""Typed handoffs between pipeline stages. Stages pass these, never free text."""
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

RULES_MD = Path(__file__).parent.parent / "rules.md"


def _rule_ids() -> dict[str, str]:
    """Rule IDs come from the rules.md table, so labels and patterns can't name a rule that doesn't exist."""
    ids = re.findall(r"^\|\s*(R\d+)\s*\|", RULES_MD.read_text(), re.M)
    if not ids:
        raise RuntimeError(f"no rule IDs found in {RULES_MD}")
    return {r: r for r in ids}


RuleId = StrEnum("RuleId", _rule_ids())
Source = Literal["rules", "llm"]


# --- What the LLM reviewer returns (sent as a strict json_schema) ---

class LLMClaim(BaseModel):
    quote: str = Field(description="The claim, copied exactly from the ad")
    rule_ids: list[RuleId] = Field(description="Rule IDs this claim breaks; empty if it breaks none")
    reason: str = Field(description="One sentence: why it breaks those rules, or why it is fine")


class LLMDisclosures(BaseModel):
    """R7 as a checklist the model fills in; code decides R7 from it. A free-form omissions list let the
    model report "R7: none missing" as an omission (tune c22)."""
    compounded: bool = Field(description="The ad says the product is compounded")
    prescription_required: bool = Field(description="The ad says a prescription is required")
    provider_decides: bool = Field(description="The ad says a licensed provider is involved in deciding")


class LLMReview(BaseModel):
    claims: list[LLMClaim] = Field(description="Every claim in the ad, in order, each checked against the rules")
    disclosures: LLMDisclosures


# --- What the reviewer produces; every later stage consumes this ---

class Finding(BaseModel):
    rule_id: RuleId
    source: Source
    reason: str
    quote: str | None = None              # None for omissions
    span: tuple[int, int] | None = None   # located by code; None if the quote isn't in the ad


class Escalation(BaseModel):
    """A model failure. Always blocks; a person decides."""
    stage: Literal["reviewer", "writer", "fixer"]
    kind: Literal["refused", "api_error", "invalid_output", "bad_shape"]
    detail: str


class Review(BaseModel):
    text: str
    findings: list[Finding] = []
    escalations: list[Escalation] = []
    claims: list[LLMClaim] = []           # the full claim map, compliant claims included
    passes: list[Source] = []             # which passes ran

    @computed_field
    @property
    def blocked(self) -> bool:
        return bool(self.findings or self.escalations)

    @property
    def rule_ids(self) -> set[RuleId]:
        return {f.rule_id for f in self.findings}


# --- Eval cases ---

class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    violations: list[RuleId]
    must_catch: bool = False
    note: str | None = None
