---
name: add-rule
description: Add a new compliance rule from an FDA letter, platform policy change, or reviewed human correction. Updates rules.md, adds labelled eval cases, and runs the eval gate.
disable-model-invocation: true
---
1. Ask me for the source (FDA letter URL, policy change, or a line from feedback.jsonl) if I didn't give one.
2. Write the rule as one row in `rules.md`: next free ID, one-sentence rule, one example violation. Presence or omission check, say which.
3. Add at least 3 cases to `evals/cases.jsonl` (the tune set, never holdout.jsonl): one clear violation (`must_catch: true`), one edge case, one compliant ad that looks close but is fine. Show them to me before saving.
4. If the rule can be caught by a pattern, add it to the deterministic pass. Otherwise it's LLM-pass only; say so.
5. Commit the new cases first, then run `python3 -m evals.run --repeat 3`. If any existing case regresses, stop and show me. Don't edit labels to make it pass.
6. Summarize: rule added, cases added, eval result.
