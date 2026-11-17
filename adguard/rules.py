"""Deterministic pass: cheap, auditable, never hallucinates. Runs before any model call."""
import re

from .models import Finding, RuleId

BRANDS = r"(?:ozempic|wegovy|mounjaro|zepbound)"

# Presence checks: a match is a violation.
PATTERNS: dict[str, list[str]] = {
    "R1": [r"\bgeneric\b"],
    "R2": [
        # "not the same as Ozempic" is the disclaimer we want people to write, so negation doesn't count.
        rf"(?<!not\s)(?<!not\sthe\s)\b(?:same|identical|equivalent)\b[^.]{{0,40}}\b{BRANDS}\b",
        rf"\bgeneric\s+(?:version\s+of\s+)?{BRANDS}\b",
        rf"\bbasically\s+{BRANDS}\b",
        rf"\b{BRANDS}\s+alternative\b",
    ],
    "R3": [r"(?<!not )\bFDA[- ]approved\b", r"\bclinically\s+proven\b"],
    "R4": [r"\bDirectMeds\s+(?:semaglutide|tirzepatide)\b", r"\bour\s+(?:own\s+)?formula\b"],
    "R5": [
        r"\bguarantee[sd]?\b",
        r"\blose\s+\d+\s*(?:lbs?|pounds|kg)\b",
        r"\b\d+\s*%\s+(?:of\s+)?(?:your\s+)?body\s*weight\b",
        r"\beffortless(?:ly)?\b",
        r"\bmelts?\s+(?:away\s+)?fat\b",
    ],
    "R6": [r"\bDr\.\s+[A-Za-z]+",
           r"[\"“][^\"”]{5,}[\"”]\s*[-–—]\s*[A-Z]"],  # a quote attributed to someone
    "R8": [r"\b(?:no|zero)\s+side\s+effects\b", r"(?<!n't\s)(?<!not\s)\brisk[- ]free\b", r"\b(?:100%|totally|completely)\s+safe\b"],
}
COMPILED = {RuleId(rid): [re.compile(p, re.IGNORECASE) for p in ps] for rid, ps in PATTERNS.items()}

# R7 is an omission check: the ad must say all of these (rules.md: compounded, prescription required,
# and a licensed provider decides eligibility).
R7_REQUIRED = ("compounded", "prescription", "provider")


def check(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for rid, patterns in COMPILED.items():
        spans: set[tuple[int, int]] = set()
        for pat in patterns:
            for m in pat.finditer(text):
                if m.span() not in spans:
                    spans.add(m.span())
                    findings.append(Finding(rule_id=rid, source="rules", quote=m.group(0), span=m.span(),
                                            reason=f"matches pattern {pat.pattern!r}"))
    missing = [w for w in R7_REQUIRED if w not in text.lower()]
    if missing:
        findings.append(Finding(rule_id=RuleId("R7"), source="rules", reason=f"ad does not say: {', '.join(missing)}"))
    return findings
