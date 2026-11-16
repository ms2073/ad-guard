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
- On reject, `human_gate` asks for a reason and rule ID, then appends `{text, reason, rule_ids, ts}` to
  `feedback.jsonl`. Nothing reads that file automatically. Corrections become eval cases or rules only
  after human review, and the eval must pass before any change ships. No self-modifying prompts, no auto-retraining.

## Constraints
- Python 3.11+, `httpx`, `pydantic` (see requirements.txt; deps are already in `.venv`). No frameworks, no other SDKs.
  One file per stage under `adguard/`.
- Models via OpenGateway (`https://apis.opengateway.ai/v1`, Bearer `OPENGATEWAY_API_KEY`, loaded from `.env`):
  - generation: `POST /chat/completions` (OpenAI-compatible, `response_format` json_schema with `strict: true`),
    model from `ADGUARD_MODEL`, default `z-ai/glm-5.3-flash-ultrafast`.
  - decisions: `POST /decisions` with typed `noul` questions -> probability 0-1, no text generated,
    model from `ADGUARD_DECISION_MODEL`, default `sionic-ai/jev-omni`.
- Model output can come back wrapped in labels or notes. Check that writer/fixer output is plain ad copy in code;
  if it isn't, send it to the human with a note rather than retrying forever.
- If a router is added: it uses the decision model, never a generative LLM. Skip the LLM pass only when the rules
  found nothing and every risk probability is < 0.5; any failure means the full pass. It ships only if eval recall
  with the router matches recall without it.
- Prompts live in `adguard/prompts/*.md`, not inline strings, so evals can diff them.
- Don't add features I didn't ask for (no web UI, no DB, no async). Ask before adding a dependency.

## Evals
- `python -m evals.run` scores the reviewer on `evals/cases.jsonl`.
- Report per-rule precision/recall and overall violation recall.
- Score twice: rules + LLM together, and the LLM pass alone (it's the backstop for wording regex can't see).
- **Exit non-zero if combined recall < 0.95, any `must_catch` case is missed, or LLM-only recall < 0.85.**
  This is the regression gate.
- Never use an LLM judge for scoring. Labels are ground truth.

## Done means
Pipeline runs end to end on a one-line brief, evals pass, and a deliberately loosened reviewer prompt
makes the eval fail.
