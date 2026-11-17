# Ad-Guard

A compliance gate for AI-written telehealth ads (compounded GLP-1 programs). A writer agent drafts ads,
a reviewer checks every claim against `rules.md`, a fixer repairs flagged claims, and a person approves
or rejects each one. There is no auto-publish path.

The rules come from public themes in the FDA's 2025–2026 warning letters to telehealth companies:
calling a compounded drug "generic" or "the same as" Ozempic/Wegovy, implying FDA approval, branding the
drug as the company's own, outcome guarantees, endorsements, missing disclosures, minimising risk.
This is a demo rule set, not legal advice.

```
brief -> writer (3 variants) -> reviewer -> [fixer -> reviewer, max 2] -> human_gate
                                   |
                     rules pass (regex) + LLM claim pass
```

- **Reviewer:** the deterministic regex pass runs first. Then an LLM pass extracts each claim and cites
  rule IDs. A hit from either pass blocks. The LLM can add findings but can't clear a regex hit.
- **Model failures** (refusal, API error, invalid output twice) block the ad and send it to a person.
- **Writer and fixer output** is checked in code: no labels, markdown or notes. A failure keeps the last
  version and goes to the person with a note.
- **human_gate** is the only thing that writes `approved.jsonl`. Rejections go to `feedback.jsonl` with a
  reason and rule IDs. Nothing reads that file automatically; a correction becomes an eval case only
  after a person reviews it.

Models go through OpenGateway (`ADGUARD_MODEL`, default `z-ai/glm-5.3-flash-ultrafast`). Prompts live in
`adguard/prompts/`. The only dependencies are httpx and pydantic.

## Run

```
python -m venv .venv && .venv/bin/pip install -r requirements.txt
echo 'OPENGATEWAY_API_KEY=...' > .env

.venv/bin/python -m adguard.pipeline "Spring promo for compounded tirzepatide drops, first month \$199"
.venv/bin/python -m adguard.pipeline --ad "It's basically Zepbound, just cheaper."   # review existing copy
.venv/bin/python -m pytest -q                                                      # offline, no API calls
```

## Evals

Labels are ground truth; there is no LLM judge. Two sets:

- `evals/cases.jsonl`: the tune set. Prompts and rules may change to fix misses here.
- `evals/holdout.jsonl`: the held-out set. Never tuned against. A held-out miss moves to the tune set
  with the next ID and is replaced by a fresh case, committed before anything else changes.

```
.venv/bin/python -m evals.run                 # both sets, rules+LLM and LLM alone
.venv/bin/python -m evals.run --repeat 3      # gate on the worst of 3 runs, list flaky cases
.venv/bin/python -m evals.run --rules-only    # free; the regex pass alone
.venv/bin/python -m evals.run --reviewer-prompt evals/fixtures/reviewer_loose.md   # must FAIL
```

The gate runs per set on every run, and the command exits 1 if any check fails:

- combined violation recall ≥ 0.95
- every `must_catch` case blocked
- LLM-only recall ≥ 0.85
- compliant ads blocked ≤ 10%, so a reviewer that blocks everything can't pass

Reports print the 95% Wilson lower bound next to recall. With 15–32 violations per set, 1.000 means
"at least about 0.8–0.9".

In Claude Code, a PostToolUse hook (`.claude/hooks/eval-on-prompt-change.sh`) runs the eval whenever
`adguard/prompts/` or `rules.md` is edited. A failure is sent back to the agent.

## Results (18 Nov 2026)

| | tune (32 violations, 9 compliant) | held-out (15 violations, 9 compliant) |
|---|---|---|
| rules + LLM, worst of 3 | PASS: 1.000 (32/32), LLM alone 1.000, 0 compliant blocked | PASS: 1.000 (15/15), LLM alone 1.000, 0 compliant blocked |
| rules alone | 0.812 (26/32), 0 compliant blocked | 0.267 (4/15), 0 compliant blocked |
| loosened reviewer prompt | FAIL: 0.938, must-catch c26 and c27 missed | FAIL: 0.867, must-catch h09 and h25 missed |

The regex is precise but doesn't generalise to new wording; that's what the LLM pass is for. The
loosened prompt shows why the held-out set exists: it fails the gate even where the tune set barely moves.

## Not in this repo

Built earlier on the same design in a separate rehearsal repo: image ads (GLM vision transcribes and
describes, and the same reviewer judges), a static HTML review board, and a decision-model router. The
router matched recall on the tune set with 25% fewer LLM calls, but on the held-out set it skipped the
LLM for three real violations, so it is off.

The remaining flakiness is extra findings only, never a miss: the model sometimes adds R5 to a testimonial
(c10, h28) or an extra rule next to the right one (c01, h08, h26). Those still block, which is the safe side.
