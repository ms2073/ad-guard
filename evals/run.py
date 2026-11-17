"""Scores the reviewer against labelled cases. Labels are ground truth; no LLM judge.

python -m evals.run                          tune + held-out sets, rules+LLM and LLM-only
python -m evals.run --repeat 3               run everything 3 times; gate on the worst run, list flaky cases
python -m evals.run --rules-only             no API calls; scores the deterministic pass alone
python -m evals.run --reviewer-prompt PATH   score a different reviewer prompt (e.g. the loosened fixture)

Two sets:
  cases.jsonl    tune set. Prompts and rules may be changed to fix misses here.
  holdout.jsonl  held-out set. Never tune against it. If a held-out case fails, move it into cases.jsonl,
                 fix it there, and write a fresh held-out case to replace it.

Gate, per set, on every run (exit 1 if any fails):
  - combined violation recall >= 0.95
  - every must_catch case is blocked
  - LLM-only violation recall >= 0.85 (the LLM is the backstop for wording regex can't see)
  - compliant ads blocked <= 10% (a reviewer that blocks everything must not pass)
"""
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from adguard import llm, reviewer
from adguard.models import EvalCase, Review, RuleId

ROOT = Path(__file__).parent
MIN_RECALL, MIN_LLM_RECALL, MAX_FALSE_BLOCK = 0.95, 0.85, 0.10
WORKERS = 4  # more than this has caused refusals (rate limits)


def load(name: str) -> list[EvalCase]:
    lines = [l for l in (ROOT / name).read_text().splitlines() if l.strip()]
    return [EvalCase.model_validate(json.loads(l)) for l in lines]


def load_sets() -> dict[str, list[EvalCase]]:
    sets = {"tune": load("cases.jsonl"), "holdout": load("holdout.jsonl")}
    ids = [c.id for cases in sets.values() for c in cases]
    if dups := sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"duplicate case IDs across eval sets: {dups}")
    return sets


def wilson_low(k: int, n: int, z: float = 1.96) -> float:
    """Lower end of the 95% interval for k/n. With n this small, 1.000 recall doesn't mean 1.000."""
    if n == 0:
        return 1.0
    p = k / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def score(results: list[tuple[EvalCase, Review]]) -> dict:
    tp = {r: 0 for r in RuleId}; fp = dict(tp); fn = dict(tp)
    for case, rev in results:
        want, got = set(case.violations), rev.rule_ids
        for r in RuleId:
            tp[r] += r in want and r in got
            fp[r] += r not in want and r in got
            fn[r] += r in want and r not in got
    k, n = sum(tp.values()), sum(tp.values()) + sum(fn.values())
    return {
        "tp": tp, "fp": fp, "fn": fn, "k": k, "n": n, "recall": k / n if n else 1.0,
        "missed_must": [c.id for c, rev in results if c.must_catch and not rev.blocked],
        "false_blocks": [c.id for c, rev in results if not c.violations and rev.blocked],
        "n_compliant": sum(1 for c, _ in results if not c.violations),
        "wrong": [(c.id, sorted(map(str, c.violations)), sorted(map(str, rev.rule_ids)),
                   "; ".join(f"{e.kind}: {e.detail}" for e in rev.escalations)[:160])
                  for c, rev in results if set(c.violations) != rev.rule_ids or rev.escalations],
    }


def report(name: str, s: dict, detail: bool = True) -> None:
    fb_rate = len(s["false_blocks"]) / s["n_compliant"] if s["n_compliant"] else 0.0
    print(f"\n== {name} ==")
    if detail:
        print(f"{'rule':<5}{'prec':>7}{'recall':>8}   tp fp fn")
        for r in RuleId:
            tp, fp, fn = s["tp"][r], s["fp"][r], s["fn"][r]
            p = tp / (tp + fp) if tp + fp else 1.0
            rc = tp / (tp + fn) if tp + fn else 1.0
            print(f"{r:<5}{p:>7.2f}{rc:>8.2f}   {tp:>2} {fp:>2} {fn:>2}")
    print(f"violation recall: {s['recall']:.3f} ({s['k']}/{s['n']}, 95% floor {wilson_low(s['k'], s['n']):.2f})   "
          f"must_catch missed: {s['missed_must'] or 'none'}   "
          f"compliant blocked: {len(s['false_blocks'])}/{s['n_compliant']} ({fb_rate:.0%}) {s['false_blocks'] or ''}")
    for cid, want, got, esc in s["wrong"]:
        print(f"  {cid}: expected {want} got {got}" + (f"  ESCALATED: {esc}" if esc else ""))


def check(set_name: str, full: dict, llm_only: dict | None) -> list[str]:
    """Pure gate logic, so it can be unit-tested without API calls."""
    f = []
    if full["recall"] < MIN_RECALL:
        f.append(f"{set_name}: combined recall {full['recall']:.3f} < {MIN_RECALL}")
    if full["missed_must"]:
        f.append(f"{set_name}: must_catch missed {full['missed_must']}")
    if full["n_compliant"] and len(full["false_blocks"]) / full["n_compliant"] > MAX_FALSE_BLOCK:
        f.append(f"{set_name}: compliant blocked {len(full['false_blocks'])}/{full['n_compliant']} > {MAX_FALSE_BLOCK:.0%}")
    if llm_only is not None and llm_only["recall"] < MIN_LLM_RECALL:
        f.append(f"{set_name}: LLM-only recall {llm_only['recall']:.3f} < {MIN_LLM_RECALL}")
    return f


def run_all(cases: list[EvalCase], **kw) -> list[tuple[EvalCase, Review]]:
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(zip(cases, pool.map(lambda c: reviewer.review(c.text, **kw), cases)))


def flaky(runs: list[list[tuple[EvalCase, Review]]]) -> list[str]:
    """Cases whose result changed between runs: the model is unsure about them."""
    out = []
    for i, (case, _) in enumerate(runs[0]):
        seen = {tuple(sorted(map(str, r[i][1].rule_ids))) for r in runs}
        if len(seen) > 1:
            out.append(f"{case.id} {[list(s) for s in seen]}")
    return out


def parse(argv: list[str]) -> tuple[int, bool, Path]:
    """Strict: an unknown or malformed flag (e.g. '--repeats 3') must not silently run a different eval."""
    repeat, rules_only, prompt, args = 1, False, reviewer.PROMPT, list(argv)
    while args:
        a = args.pop(0)
        if a == "--rules-only":
            rules_only = True
        elif a == "--repeat" and args and args[0].isdigit() and int(args[0]) >= 1:
            repeat = int(args.pop(0))
        elif a == "--reviewer-prompt" and args and Path(args[0]).is_file():
            prompt = Path(args.pop(0))
        else:
            print(f"bad argument {a!r}\n\n{__doc__}", file=sys.stderr)
            raise SystemExit(2)  # 2 = usage error, distinct from 1 = gate failure
    return repeat, rules_only, prompt


def main(argv: list[str]) -> int:
    repeat, rules_only, prompt = parse(argv)
    llm_fn = None if rules_only else reviewer.llm_pass(prompt)
    sets = load_sets()

    failures, history = [], {name: {"full": [], "llm": []} for name in sets}
    for i in range(repeat):
        for name, cases in sets.items():
            full_res = run_all(cases, use_llm=not rules_only, llm_fn=llm_fn)
            history[name]["full"].append(full_res)
            full, llm_only = score(full_res), None
            if not rules_only:
                llm_res = run_all(cases, use_rules=False, llm_fn=llm_fn)
                history[name]["llm"].append(llm_res)
                llm_only = score(llm_res)
            if i == 0:
                report(f"{name}, " + ("rules only" if rules_only else "rules + LLM"), full)
                if llm_only:
                    report(f"{name}, LLM only", llm_only)
            elif not rules_only:
                print(f"\n-- run {i + 1}/{repeat}, {name}: combined {full['recall']:.3f}, LLM-only "
                      f"{llm_only['recall']:.3f}, must_catch missed {full['missed_must'] or 'none'}, "
                      f"compliant blocked {full['false_blocks'] or 'none'}")
            if not rules_only:
                failures += [f"run {i + 1}: {x}" for x in check(name, full, llm_only)]
    if rules_only:
        # Rules alone are expected to miss wording regex can't see (that's the LLM's job), so no gate here.
        print("\nGATE: not applied (--rules-only scores the deterministic pass alone; run without it to gate)")
        return 0
    if repeat > 1:
        for name in sets:
            for mode in ("full", "llm"):
                print(f"flaky in {name} ({mode}): {flaky(history[name][mode]) or 'none'}")
    print(f"\nmodel: {llm.MODEL}   prompt: {prompt.relative_to(ROOT.parent) if prompt.is_relative_to(ROOT.parent) else prompt}"
          f"   usage: {llm.usage}")
    worst = f" (worst of {repeat} runs)" if repeat > 1 else ""
    print(f"\nGATE{worst}: " + ("FAIL - " + "; ".join(dict.fromkeys(failures)) if failures else "PASS"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
