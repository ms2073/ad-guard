# Ad-Guard

A guarded multi-agent pipeline for telehealth ad copy (compounded GLP-1 programs).
Agents draft ads; a reviewer checks every claim against `rules.md`; a fixer repairs flagged claims;
a human approves. Nothing ships without a human decision.

## Why this exists
The FDA has sent three waves of warning letters to telehealth companies over compounded GLP-1 marketing
(Sep 2025, Mar 2026, Jun 2026). Ad platforms review health ads claim by claim. A creative pipeline that
generates ads without a compliance gate turns ad spend into regulatory risk.

## Architecture (keep it this small)
brief -> writer (3 variants) -> reviewer -> [fixer -> reviewer, max 2 loops] -> human_gate -> approved.jsonl

- Stages pass **typed JSON** (pydantic models), never free text.
- Reviewer = **deterministic rule pass first** (regex/keyword: cheap, auditable, never hallucinates),
  then an **LLM claim pass**: extract each claim, classify against rules, cite rule IDs.
- A violation from either pass blocks. The LLM may add findings; it cannot clear a deterministic hit.
- `human_gate` is a CLI prompt. No auto-publish path exists, not even behind a flag.

## Constraints
- Python 3.11+, `anthropic` SDK, `pydantic`. No frameworks. One file per stage under `adguard/`.
- Model IDs come from env (`ADGUARD_MODEL`), default `claude-sonnet-5-5`.
- Prompts live in `adguard/prompts/*.md`, not inline strings, so evals can diff them.
- Don't add features I didn't ask for (no web UI, no DB, no async). Ask before adding a dependency.

## Evals
- `python -m evals.run` scores the reviewer on `evals/cases.jsonl`.
- Report per-rule precision/recall and overall violation recall.
- **Exit non-zero if violation recall < 0.95 or any `must_catch` case is missed.** This is the regression gate.
- Never use an LLM judge for scoring. Labels are ground truth.

## Done means
Pipeline runs end to end on a one-line brief, evals pass, and a deliberately loosened reviewer prompt
makes the eval fail.
