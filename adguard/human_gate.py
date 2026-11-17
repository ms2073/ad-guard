"""Nothing ships without a person. Approvals go to approved.jsonl; rejections go to feedback.jsonl.
Nothing reads feedback.jsonl automatically: a correction becomes an eval case or rule only after review."""
import json
import time
from pathlib import Path
from typing import Callable

from .models import Review, RuleId

ROOT = Path(__file__).parent.parent
APPROVED = ROOT / "approved.jsonl"
FEEDBACK = ROOT / "feedback.jsonl"
KNOWN = {r.value for r in RuleId}


def _append(path: Path, row: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def show(review: Review) -> None:
    print("\n" + "=" * 70 + f"\n{review.text}\n")
    for f in review.findings:
        print(f"  BLOCK {f.rule_id} [{f.source}] " + (f"\"{f.quote}\" - " if f.quote else "") + f.reason)
    for e in review.escalations:
        print(f"  NEEDS A PERSON: {e.stage} {e.kind}: {e.detail}")
    if not review.blocked:
        print("  Reviewer: no violations found.")


def gate(review: Review, ask: Callable[[str], str] = input) -> tuple[bool, str]:
    """Returns (approved, reason). The only way anything reaches approved.jsonl."""
    show(review)
    q = "Reviewer BLOCKED this. Approve anyway? [y/N] " if review.blocked else "Approve? [y/N] "
    if ask(q).strip().lower() == "y":
        _append(APPROVED, {"text": review.text, "reviewer_blocked": review.blocked, "ts": time.time()})
        return True, ""
    reason = ask("Why rejected? ").strip()
    ids = [r.strip().upper() for r in ask("Rule IDs (comma-separated, blank if none): ").split(",") if r.strip()]
    if unknown := [r for r in ids if r not in KNOWN]:
        print(f"  ignoring unknown rule IDs {unknown}")
    _append(FEEDBACK, {"text": review.text, "reason": reason, "rule_ids": [r for r in ids if r in KNOWN],
                       "ts": time.time()})
    return False, reason
