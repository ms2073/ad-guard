"""Deterministic check that a model returned finished ad copy, not copy wrapped in labels or commentary.
A prompt instruction alone didn't stop this in rehearsal ('**Price angle** — "..." (~163 chars)')."""
import re

JUNK = re.compile(r"\*\*|^#|`|rewritten ad|changes made|^note\b|\(~?\d+\s*chars?\)|\bangle\b\s*[—:-]|"
                  r"^variant\s*\d|^[\"“].*[\"”]$", re.I | re.M)


def problem(text: str, original: str | None = None) -> str | None:
    """Why the text doesn't look like a finished ad, or None if it does."""
    t = text.strip()
    if len(t) < 40:
        return "too short to be an ad"
    if m := JUNK.search(t):
        return f"contains formatting or commentary: {m.group(0)!r}"
    if original and len(t) < 0.5 * len(original.strip()):
        return "fix dropped more than half of the original ad"
    return None
