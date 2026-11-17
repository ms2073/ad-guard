# Ad-Guard

A guarded multi-agent pipeline for telehealth ads (compounded GLP-1 programs), text and image.
Agents draft ads; a reviewer checks every claim against `rules.md`; a fixer repairs flagged claims;
a person approves. Nothing ships without a human decision.

## Why this exists
The FDA has sent three waves of warning letters to telehealth companies over compounded GLP-1 marketing
(Sep 2025, Mar 2026, Jun 2026). Ad platforms review health ads claim by claim. A creative pipeline that
generates ads without a compliance gate turns ad spend into regulatory risk.

## Scope of this build
Build the text pipeline and its evals. The image path, review board and router already exist in
`../ad-guard-rehearsal` (built earlier, same design); don't build them here unless I ask.

## Architecture (keep it this small)
text:  brief -> writer (3 variants) -> reviewer -> [fixer -> reviewer, max 2 loops] -> human_gate
image: image -> image_review (GLM vision: on-image text + plain description of visuals) -> reviewer -> human_gate
both:  -> approved.jsonl / feedback.jsonl, and board.html (static review board for the run)

- Stages pass **typed JSON** (pydantic models), never free text.
- Reviewer = **deterministic rule pass first** (regex/keyword: cheap, auditable, never hallucinates),
  then an **LLM claim pass**: extract each claim, classify against rules, cite rule IDs.
- A violation from either pass blocks. The LLM may add findings; it cannot clear a deterministic hit.
- Any model failure (refusal, API error, invalid output twice) blocks the ad and sends it to a person.
- Images: the vision model only describes; it never judges. Rules run on the transcribed words; the LLM
  reviewer sees words plus the visual description. Image ads are not auto-fixed; a person decides.
- `human_gate` is a CLI prompt. No auto-publish path exists, not even behind a flag.
- On reject, `human_gate` asks for a reason and rule ID, then appends `{text, reason, rule_ids, ts}` to
  `feedback.jsonl`. Nothing reads that file automatically. Corrections become eval cases or rules only
  after human review, and the eval must pass before any change ships. No self-modifying prompts, no auto-retraining.
- `board.html` is one static file per run: flagged phrases marked inline with their rule, fix history,
  router probabilities, flagged images outlined. No server, no framework, no web fonts.

Run it:
- `python -m adguard.pipeline "Spring promo for compounded tirzepatide drops, first month $199"`
- `python -m adguard.pipeline --ad "existing copy" --image path.png` (review existing creative)

## Constraints
- Python 3.11+, `httpx`, `pydantic`. See requirements.txt; deps are in `.venv`.
  No frameworks, no other SDKs. One file per stage under `adguard/`.
- Models via OpenGateway (`https://apis.opengateway.ai/v1`, Bearer `OPENGATEWAY_API_KEY`, loaded from `.env`):
  - generation and vision: `POST /chat/completions` (OpenAI-compatible, `response_format` json_schema with
    `strict: true`), model from `ADGUARD_MODEL`, default `z-ai/glm-5.3-flash-ultrafast`.
  - decisions: `POST /decisions` with typed `noul` questions -> probability 0-1, no text generated,
    model from `ADGUARD_DECISION_MODEL`, default `sionic-ai/jev-omni`.
- Model output can come back wrapped in labels or notes. Check in code that writer/fixer output is plain ad copy
  and that the image reader returned usable text; if not, send it to the human with a note rather than retrying forever.
- Router (decision model, opt-in with `--router`): skip the LLM pass only when the rules found nothing and every
  risk probability is < 0.5; any failure means the full pass. It ships only if recall with the router matches
  recall without it **on the held-out set**. Status: **does not ship.** It matched on the tune set (5/20 fast-pathed,
  all compliant) but fast-pathed 3 held-out violations (h08, h10, h25). Not used for images either: it scored an
  obvious before/after image at 0.45.
- Prompts live in `adguard/prompts/*.md`, not inline strings, so evals can diff them.
- Don't add features I didn't ask for (no web UI beyond the static board, no DB, no async). Ask before adding a dependency.

## Evals
Labels are ground truth. Never use an LLM judge for scoring.

Sets:
- `evals/cases.jsonl` (tune set): prompts and rules may be changed to fix misses here.
- `evals/holdout.jsonl` (held-out set): **never tune against it.** If a held-out case fails, move it to
  `cases.jsonl` with the next free ID, fix it there, and write a fresh held-out case to replace it.
  Commit new labels before running anything against them.

Commands:
- `python -m evals.run`: both text sets, scored twice: rules + LLM, and the LLM pass alone.
- `python -m evals.run --repeat 3`: gate on the worst of 3 runs and list flaky cases. Do this before calling a change done.
- `python -m evals.run --rules-only` (free). `--router` and `--images` exist only in the rehearsal repo.
- Never run two evals at the same time: concurrent runs caused refusals (likely rate limits) and a false gate failure.

**Gate, per set, every run (exit non-zero if any fails):**
- combined violation recall >= 0.95
- every `must_catch` case blocked
- LLM-only violation recall >= 0.85 (the LLM is the backstop for wording regex can't see)
- compliant ads blocked <= 10% (a reviewer that blocks everything must not pass)

Reports print the 95% lower bound next to recall: with ~15-25 violations per set, 1.000 means "at least ~0.8".

Rehearsal results (worst of 3): tune 25/25, held-out 14/14, images 8/8, no compliant ad blocked.
Rules alone: 92% on tune, 21% on held-out. The regex is precise but doesn't generalise; that's the LLM's job.

## Done means
Pipeline runs end to end on a one-line brief, `evals.run --repeat 3` passes, and a deliberately loosened
reviewer prompt makes the eval fail.
