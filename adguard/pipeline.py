"""brief -> writer (3 variants) -> reviewer -> [fixer -> reviewer, max 2] -> human gate

  python -m adguard.pipeline "Spring promo for compounded tirzepatide drops, first month $199"
  python -m adguard.pipeline --ad "It's basically Zepbound, just cheaper." [--ad ...]   review existing copy

There is no auto-publish path. Every ad, clean or not, goes to a person.
"""
import sys
from typing import Callable

from . import fixer, human_gate, llm, reviewer, writer
from .models import Escalation, Review

MAX_FIX_LOOPS = 2


def review_text(text: str, review_fn: Callable[[str], Review] = reviewer.review,
                fix_fn: Callable[[Review], object] = fixer.fix) -> list[Review]:
    """Review, then fix and re-review while blocked, at most MAX_FIX_LOOPS times. Returns the history.
    A model failure stops the loop and keeps the last version; the person sees why."""
    history = [review_fn(text)]
    for loop in range(1, MAX_FIX_LOOPS + 1):
        last = history[-1]
        if not last.blocked or last.escalations:
            break
        print(f"  blocked on {sorted(map(str, last.rule_ids))}, fixing (loop {loop}/{MAX_FIX_LOOPS})")
        try:
            fixed = fix_fn(last)
        except llm.ModelFailure as e:
            kind = "bad_shape" if e.kind == "invalid_output" else e.kind
            last.escalations.append(Escalation(stage="fixer", kind=kind, detail=e.detail))
            break
        history.append(review_fn(fixed.text))
    return history


def run(brief: str = "", ads: list[str] = (), ask=input) -> list[dict]:
    texts = list(ads)
    if brief:
        try:
            texts += writer.write(brief)
        except llm.ModelFailure as e:
            print(f"Writer failed ({e}); nothing to review. No ad was approved.")
        else:
            print(f"Writer produced 3 variants for: {brief}")
    records = []
    for i, text in enumerate(texts, 1):
        print(f"\n--- ad {i}/{len(texts)} ---")
        history = review_text(text)
        if len(history) > 1:
            print(f"  original: {history[0].text}")
        approved, reason = human_gate.gate(history[-1], ask=ask)
        records.append({"history": history, "approved": approved, "reason": reason})
    print(f"\n{sum(r['approved'] for r in records)} of {len(records)} approved.   LLM usage: {llm.usage}")
    return records


if __name__ == "__main__":
    args, ads = sys.argv[1:], []
    while "--ad" in args:
        i = args.index("--ad")
        if i + 1 >= len(args):
            sys.exit(__doc__)
        ads.append(args[i + 1])
        del args[i:i + 2]
    if not (args or ads):
        sys.exit(__doc__)
    run(" ".join(args), ads)
