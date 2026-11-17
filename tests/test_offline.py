"""Offline tests: no API calls. The model is replaced with fakes, so wiring and safety properties are checked for free.
Only the tune set is used here; the held-out set is scored by evals.run, never asserted on in tests."""
import json

import pytest
from pydantic import ValidationError

from adguard import llm, reviewer, rules
from adguard.models import EvalCase, LLMClaim, LLMDisclosures, LLMReview, RuleId
from evals import run as evals

TUNE = evals.load("cases.jsonl")
CLEAN = "Talk to a licensed provider about compounded semaglutide. Prescription required."


ALL_SAID = LLMDisclosures(compounded=True, prescription_required=True, provider_decides=True)


def fake(*claims: LLMClaim, disclosures: LLMDisclosures = ALL_SAID):
    return lambda text: LLMReview(claims=list(claims) or [LLMClaim(quote=text[:20], rule_ids=[], reason="fine")],
                                  disclosures=disclosures)


# --- rules pass ---

def test_rule_ids_come_from_rules_md():
    assert [r.value for r in RuleId] == [f"R{i}" for i in range(1, 9)]


def test_rules_never_block_a_compliant_tune_case():
    for c in TUNE:
        if not c.violations:
            assert rules.check(c.text) == [], c.id


def test_rules_only_flag_labelled_rules():
    for c in TUNE:
        got = {f.rule_id for f in rules.check(c.text)}
        assert got <= set(c.violations), (c.id, got)


def test_r7_needs_compounded_prescription_and_provider():
    assert {f.rule_id for f in rules.check("Compounded semaglutide, prescription required.")} == {"R7"}
    assert rules.check(CLEAN) == []


def test_fda_disclaimer_is_not_an_approval_claim():
    assert not rules.check("Compounded drugs are not FDA-approved. Prescription required; a provider decides.")


def test_negated_risk_free_is_a_disclosure_not_a_claim():
    tail = " Compounded semaglutide, prescription required; a licensed provider decides."
    assert not rules.check("It isn't risk-free: nausea is common." + tail)
    assert not rules.check("It is not risk-free." + tail)
    assert {f.rule_id for f in rules.check("Totally risk-free." + tail)} == {"R8"}


def test_rules_findings_carry_their_span():
    text = "Generic Ozempic. Compounded, prescription required, provider decides."
    f = next(f for f in rules.check(text) if f.rule_id == "R1")
    assert text[f.span[0]:f.span[1]] == f.quote == "Generic"


# --- reviewer merge semantics ---

def test_llm_cannot_clear_a_rules_hit():
    r = reviewer.review("Generic Ozempic. Compounded, prescription required, provider decides.", llm_fn=fake())
    assert r.blocked and "R1" in r.rule_ids


def test_llm_can_add_findings():
    text = "Mounjaro results, lower price. Compounded tirzepatide, prescription required, provider decides."
    r = reviewer.review(text, llm_fn=fake(LLMClaim(quote="Mounjaro results", rule_ids=[RuleId("R2")], reason="implied")))
    assert r.rule_ids == {"R2"} and r.findings[0].source == "llm" and r.findings[0].span == (0, 16)


def test_quote_not_in_ad_still_blocks():
    r = reviewer.review(CLEAN, llm_fn=fake(LLMClaim(quote="made up phrase", rule_ids=[RuleId("R5")], reason="x")))
    assert r.blocked and r.findings[0].span is None


def test_llm_missing_disclosure_blocks_as_r7():
    r = reviewer.review(CLEAN, llm_fn=fake(disclosures=ALL_SAID.model_copy(update={"provider_decides": False})))
    assert r.rule_ids == {"R7"} and r.findings[0].quote is None and "provider decides" in r.findings[0].reason


def test_compliant_claims_dont_block_but_are_kept():
    r = reviewer.review(CLEAN, llm_fn=fake())
    assert not r.blocked and len(r.claims) == 1 and r.passes == ["rules", "llm"]


def test_every_flagged_phrase_is_kept():
    text = "Guaranteed results. Lose 20 lbs. Compounded, prescription required, provider decides."
    assert len([f for f in reviewer.review(text, use_llm=False).findings if f.rule_id == "R5"]) == 2


def test_model_failure_blocks():
    def fail(t):
        raise llm.ModelFailure("refused", "test")
    r = reviewer.review(CLEAN, llm_fn=fail)
    assert r.blocked and r.escalations[0].kind == "refused" and not r.findings


def test_blocked_is_serialised():
    assert json.loads(reviewer.review(CLEAN, use_llm=False).model_dump_json())["blocked"] is False


# --- llm client, fake transport ---

def reply(content: str | None, finish: str = "stop") -> dict:
    return {"choices": [{"finish_reason": finish, "message": {"content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def test_invalid_output_twice_is_a_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "_post", lambda p, b: calls.append(1) or reply("not json"))
    with pytest.raises(llm.ModelFailure) as e:
        llm.call("sys", "user", LLMReview)
    assert e.value.kind == "invalid_output" and len(calls) == 2


def test_invalid_then_valid_recovers(monkeypatch):
    answers = iter([reply("{}"), reply('{"claims": [{"quote": "a", "rule_ids": ["R1"], "reason": "b"}], "disclosures": {"compounded": true, "prescription_required": true, "provider_decides": true}}')])
    monkeypatch.setattr(llm, "_post", lambda p, b: next(answers))
    assert llm.call("sys", "user", LLMReview).claims[0].rule_ids == ["R1"]


def test_unknown_rule_id_from_model_is_invalid(monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda p, b: reply('{"claims": [{"quote": "a", "rule_ids": ["R99"], "reason": "b"}], "disclosures": {"compounded": true, "prescription_required": true, "provider_decides": true}}'))
    with pytest.raises(llm.ModelFailure):
        llm.call("sys", "user", LLMReview)


def test_empty_review_is_not_a_pass(monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda p, b: reply('{"claims": [], "disclosures": {"compounded": true, "prescription_required": true, "provider_decides": true}}'))
    r = reviewer.review(CLEAN, llm_fn=lambda t: llm.call("sys", t, LLMReview, check=reviewer._usable))
    assert r.blocked and r.escalations[0].kind == "invalid_output"


def test_content_filter_is_refused(monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda p, b: reply(None, finish="content_filter"))
    with pytest.raises(llm.ModelFailure) as e:
        llm.call("sys", "user", LLMReview)
    assert e.value.kind == "refused"


def test_strict_schema_is_strict_and_inlined():
    s = llm._strict(LLMReview.model_json_schema())
    assert "$defs" not in json.dumps(s) and s["additionalProperties"] is False
    claim = s["properties"]["claims"]["items"]
    assert claim["required"] == ["quote", "rule_ids", "reason"]
    assert s["properties"]["disclosures"]["additionalProperties"] is False
    assert claim["properties"]["rule_ids"]["items"]["enum"] == [f"R{i}" for i in range(1, 9)]


# --- eval harness ---

def test_eval_case_rejects_unknown_rule_and_extra_fields():
    with pytest.raises(ValidationError):
        EvalCase(id="x", text="t", violations=["R99"])
    with pytest.raises(ValidationError):
        EvalCase.model_validate({"id": "x", "text": "t", "violations": [], "mustcatch": True})


def test_case_ids_unique_across_sets():
    evals.load_sets()  # raises on duplicates


def full_score(**over) -> dict:
    return {"recall": 1.0, "missed_must": [], "false_blocks": [], "n_compliant": 10, **over}


def test_gate_passes_clean_scores():
    assert evals.check("tune", full_score(), {"recall": 0.9}) == []


@pytest.mark.parametrize("full,llm_only,expect", [
    (full_score(recall=0.94), None, "combined recall"),
    (full_score(missed_must=["c01"]), None, "must_catch"),
    (full_score(false_blocks=["a", "b"]), None, "compliant blocked"),
    (full_score(), {"recall": 0.84}, "LLM-only recall"),
])
def test_gate_fails(full, llm_only, expect):
    assert expect in " ".join(evals.check("tune", full, llm_only))


# --- evals.run main(): exit codes, with a fake model (no API) ---

def fake_model(flag_labelled: bool):
    """flag_labelled=True: an oracle that returns each case's labels. False: a reviewer that flags nothing."""
    labels = {c.text: c.violations for cases in evals.load_sets().values() for c in cases}

    def model(text):
        rids = [r for r in labels[text] if r != "R7"] if flag_labelled else []
        r7 = flag_labelled and "R7" in labels[text]
        return LLMReview(claims=[LLMClaim(quote=text[:20], rule_ids=rids, reason="x")],
                         disclosures=ALL_SAID.model_copy(update={"provider_decides": not r7}))
    return lambda prompt: model


def test_main_exits_1_when_the_gate_fails(monkeypatch, capsys):
    monkeypatch.setattr(reviewer, "llm_pass", fake_model(flag_labelled=False))
    assert evals.main([]) == 1 and "GATE: FAIL" in capsys.readouterr().out


def test_main_exits_0_when_the_gate_passes(monkeypatch, capsys):
    monkeypatch.setattr(reviewer, "llm_pass", fake_model(flag_labelled=True))
    assert evals.main(["--repeat", "2"]) == 0 and "GATE (worst of 2 runs): PASS" in capsys.readouterr().out


# --- writer / fixer shape checks, fix loop, human gate (fakes only) ---

from adguard import fixer, human_gate, pipeline, shape, writer
from adguard.models import Fix, Variants

GOOD = "Spring pricing: compounded tirzepatide drops from $199 for the first month. Prescription required; a licensed provider decides."


def test_shape_rejects_wrapped_or_truncated_copy():
    assert shape.problem(GOOD) is None
    assert shape.problem('**Price angle** — "' + GOOD + '" (~163 chars)')
    assert shape.problem("Rewritten ad:\n\n" + GOOD + "\n\nChanges made:\n- added R7")
    assert shape.problem("Variant 1: " + GOOD)
    assert shape.problem(GOOD[:30] + " compounded.", original=GOOD * 2)


def test_every_eval_case_passes_the_shape_check():
    # real ad copy (compliant or not) must never be mistaken for commentary
    for c in TUNE:
        assert shape.problem(c.text) is None, c.id


def test_writer_needs_three_clean_variants():
    assert writer._usable(Variants(variants=[GOOD] * 3)) is None
    assert writer._usable(Variants(variants=[GOOD] * 2))
    assert writer._usable(Variants(variants=[GOOD, GOOD, "**Care angle** — " + GOOD]))


def test_writer_bad_shape_twice_is_a_model_failure(monkeypatch):
    bad = json.dumps({"variants": ["**Price angle** — " + GOOD] * 3})
    monkeypatch.setattr(llm, "_post", lambda path, body: {"choices": [{"message": {"content": bad}}]})
    with pytest.raises(llm.ModelFailure):
        writer.write("brief")


def _blocked(text):
    return reviewer.review(text, llm_fn=fake(LLMClaim(quote=text[:10], rule_ids=["R2"], reason="x")))


def test_fix_loop_is_capped():
    calls = []
    history = pipeline.review_text(GOOD, review_fn=_blocked,
                                   fix_fn=lambda r: calls.append(1) or Fix(text=GOOD, changes=[]))
    assert len(calls) == pipeline.MAX_FIX_LOOPS and len(history) == pipeline.MAX_FIX_LOOPS + 1
    assert history[-1].blocked


def test_fixer_failure_keeps_last_version_and_escalates():
    def bad_fix(r):
        raise llm.ModelFailure("invalid_output", "fixer output rejected: too short to be an ad")
    history = pipeline.review_text(GOOD, review_fn=_blocked, fix_fn=bad_fix)
    assert len(history) == 1 and history[0].text == GOOD
    assert history[0].escalations[0].stage == "fixer" and history[0].blocked


def test_clean_ad_is_not_sent_to_the_fixer():
    history = pipeline.review_text(GOOD, review_fn=lambda t: reviewer.review(t, llm_fn=fake()),
                                   fix_fn=lambda r: pytest.fail("fixer called on a clean ad"))
    assert len(history) == 1 and not history[0].blocked


def test_human_gate_logs_rejection_and_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(human_gate, "FEEDBACK", tmp_path / "feedback.jsonl")
    monkeypatch.setattr(human_gate, "APPROVED", tmp_path / "approved.jsonl")
    r = _blocked(GOOD)
    answers = iter(["n", "implies it's Mounjaro", "r2, R99"])
    assert human_gate.gate(r, ask=lambda _: next(answers)) == (False, "implies it's Mounjaro")
    row = json.loads((tmp_path / "feedback.jsonl").read_text())
    assert row["rule_ids"] == ["R2"] and row["text"] == GOOD and not (tmp_path / "approved.jsonl").exists()
    assert human_gate.gate(r, ask=lambda _: "y") == (True, "")
    assert json.loads((tmp_path / "approved.jsonl").read_text())["reviewer_blocked"] is True


def test_pipeline_approves_nothing_without_a_yes(tmp_path, monkeypatch):
    monkeypatch.setattr(human_gate, "FEEDBACK", tmp_path / "feedback.jsonl")
    monkeypatch.setattr(human_gate, "APPROVED", tmp_path / "approved.jsonl")
    monkeypatch.setattr(writer, "write", lambda brief: [GOOD] * 3)
    monkeypatch.setattr(pipeline, "review_text", lambda t: [reviewer.review(t, llm_fn=fake())])
    records = pipeline.run("brief", ask=lambda _: "")  # person just presses Enter
    assert len(records) == 3 and not any(r["approved"] for r in records)
    assert not (tmp_path / "approved.jsonl").exists()


def test_no_auto_publish_path_exists():
    import inspect
    # only human_gate writes approvals; the pipeline has no other route to approved.jsonl
    assert "APPROVED" not in inspect.getsource(pipeline) and "approved" not in inspect.getsource(writer)
