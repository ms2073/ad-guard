@AGENTS.md

## Claude Code only
- After you edit anything in adguard/prompts/ or rules.md, a hook runs the eval automatically. If it fails,
  fix the cause; never edit labels to make it pass.
- Don't open `evals/holdout.jsonl` to fix a miss. A failing held-out case gets moved to cases.jsonl first
  (see Evals in AGENTS.md); only then work on it.
- A label may change only to make the gate stricter, with the reason in the commit message.
- New rules go through the /add-rule skill.
- Before saying a change is done, run `evals.run --repeat 3` once, on its own, and show the GATE line.
