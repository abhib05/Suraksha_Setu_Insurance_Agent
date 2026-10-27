"""M5 done-when: case A yields 3 packets that pass the verifier, and seeded errors (wrong
date, missing attachment) make it fail. Plus the model's limited role in each agent."""

import json
from collections import deque
from pathlib import Path

import pytest

from suraksha.agents.coordinator.agent import Summary, next_action
from suraksha.agents.discovery.agent import LineNote, discover
from suraksha.agents.filing.agent import AccidentText, build_packet
from suraksha.agents.filing.render import render_packet
from suraksha.agents.verifier.agent import ModelFindings, verify_packet
from suraksha.contracts import (
    Action,
    CaseFacts,
    DocType,
    ExtractedDocument,
    Finding,
    RuleStatus,
    SchemeId,
    Severity,
    SourceRef,
    Strength,
)
from suraksha.services import rules
from suraksha.services.registry import required_documents
from suraksha.services.rules.facts import rule_facts
from suraksha.services.rules.matchers import match_entitlements
from suraksha.tools.llm import LLMResult

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases"


def load(case: str):
    d = CASES / case
    facts = CaseFacts.model_validate_json((d / "facts.json").read_text())
    docs = [ExtractedDocument.model_validate(x)
            for x in json.loads((d / "documents.json").read_text())]  # fmt: skip
    rf = rule_facts(facts, docs)
    ents = {e.scheme_id: e for e in match_entitlements(facts, docs)}
    results = {s: rules.check_entitlement(e, rf) for s, e in ents.items()}
    return facts, docs, ents, results


def packet_for(case: str, scheme: SchemeId, **kw):
    facts, docs, ents, results = load(case)
    p = build_packet(f"clm_{scheme.value}", ents[scheme], facts, docs, 1, use_model=False, **kw)
    return p, ents[scheme], facts, docs, results[scheme]


def verify(p, ent, facts, docs, result, **kw):
    return verify_packet(p, ent, facts, docs, result, use_model=kw.pop("use_model", False), **kw)


class Fake:
    def __init__(self, *outs):
        self.outs = deque(outs)
        self.calls = []

    def __call__(self, agent, messages, schema, **_):
        self.calls.append((agent, messages))
        out = self.outs.popleft()
        if isinstance(out, Exception):
            raise out
        return out, LLMResult(agent=agent, model="m", text="")


# --- done-when -----------------------------------------------------------------------


@pytest.mark.parametrize("scheme", list(SchemeId))
def test_case_a_packets_pass_and_are_fully_traced(scheme):
    p, ent, facts, docs, result = packet_for("case_a", scheme)
    v = verify(p, ent, facts, docs, result)
    assert v.passed, v.findings
    assert v.findings == []
    assert p.untraced_fields() == []
    assert all(f.value for form in p.forms for f in form.fields), "every field filled"
    attached = {d.doc_type for d in docs if d.doc_id in {a.doc_id for a in p.attachments}}
    assert set(required_documents(scheme)) <= attached
    assert [a.order for a in p.attachments] == list(range(1, len(p.attachments) + 1))


def test_seeded_wrong_date_fails():
    p, ent, facts, docs, result = packet_for("case_a", SchemeId.PMJJBY)
    form = p.forms[0]
    bad = [f.model_copy(update={"value": "2026-09-13"}) if f.name == "date_of_death" else f
           for f in form.fields]  # fmt: skip
    p = p.model_copy(update={"forms": [form.model_copy(update={"fields": bad})]})
    v = verify(p, ent, facts, docs, result)
    assert not v.passed
    reasons = " ".join(f.reason for f in v.findings)
    assert "differs from" in reasons and "Date of death does not match" in reasons


def test_seeded_missing_attachment_fails():
    p, ent, facts, docs, result = packet_for("case_a", SchemeId.PMSBY)
    p = p.model_copy(update={"attachments": [a for a in p.attachments
                                             if "FIR" not in a.label]})  # fmt: skip
    v = verify(p, ent, facts, docs, result)
    assert not v.passed
    assert any("Required document missing: fir" in f.reason for f in v.findings)


def test_other_blockers():
    p, ent, facts, docs, result = packet_for("case_a", SchemeId.RUPAY)
    form = p.forms[0]

    def with_field(name, **upd):
        fields = [f.model_copy(update=upd) if f.name == name else f for f in form.fields]
        return p.model_copy(update={"forms": [form.model_copy(update={"fields": fields}),
                                              *p.forms[1:]]})  # fmt: skip

    cases = {
        "untraced": with_field("place_of_death", source_ref=None),
        "wrong amount": with_field("claim_amount_inr", value="500000"),
        "phantom doc": with_field("fir_number", source_ref=SourceRef(kind="document_field",
                                                                     ref="nope#fir_number")),
    }  # fmt: skip
    for label, bad in cases.items():
        assert not verify(bad, ent, facts, docs, result).passed, label
    not_eligible = result.model_copy(update={"status": RuleStatus.NOT_ELIGIBLE})
    assert not verify(p, ent, facts, docs, not_eligible).passed


def test_case_b_packets_block_on_missing_certificate_and_unknown_rupay():
    p, ent, facts, docs, result = packet_for("case_b", SchemeId.RUPAY)
    v = verify(p, ent, facts, docs, result)
    reasons = " ".join(f.reason for f in v.findings)
    assert not v.passed
    assert "death_certificate" in reasons and "Eligibility is unknown" in reasons
    assert any(c.item.startswith("Missing: Death certificate") for c in p.checklist)


# --- the model's limited role ----------------------------------------------------------


def test_model_findings_are_capped_at_warnings():
    p, ent, facts, docs, result = packet_for("case_a", SchemeId.PMJJBY)
    fake = Fake(ModelFindings(findings=[Finding(severity=Severity.BLOCKER, target="packet",
                                                reason="Looks odd")]))  # fmt: skip
    v = verify(p, ent, facts, docs, result, use_model=True, complete_json=fake)
    assert v.passed
    assert [(f.severity, f.reason) for f in v.findings] == [(Severity.WARNING, "[model] Looks odd")]
    assert fake.calls[0][0] == "verifier"


def test_model_unavailable_is_visible_not_silent():
    p, ent, facts, docs, result = packet_for("case_a", SchemeId.PMJJBY)
    v = verify(p, ent, facts, docs, result, use_model=True, complete_json=Fake(RuntimeError()))
    assert v.passed and "AI review did not run" in v.findings[0].reason


def test_filing_model_only_rewords_the_accident_text():
    facts, docs, ents, results = load("case_a")
    fake = Fake(AccidentText(description_en="A truck hit the two-wheeler.", translated=True))
    p = build_packet("clm_r", ents[SchemeId.RUPAY], facts, docs, 1, complete_json=fake)
    descr = [f for form in p.forms for f in form.fields if f.name == "accident_description"]
    assert {f.value for f in descr} == {"A truck hit the two-wheeler."}
    assert all(f.source_ref.ref.endswith("#cause") for f in descr)  # still cites the FIR
    assert len(fake.calls) == 1  # same FIR text translated once for both forms
    v = verify(p, ents[SchemeId.RUPAY], facts, docs, results[SchemeId.RUPAY])
    assert v.passed  # rewording is allowed for this field only


def test_coordinator_picks_action_in_code_and_screens_summary():
    p, ent, facts, docs, result = packet_for("case_a", SchemeId.PMSBY)
    ok = verify(p, ent, facts, docs, result)
    na = next_action(p, ok, result, complete_json=Fake(Summary(summary="PMSBY looks fine.")))
    assert na.action is Action.REQUEST_APPROVAL and na.summary == "PMSBY looks fine."
    na = next_action(p, ok, result, complete_json=Fake(Summary(summary="It will be paid soon.")))
    assert "will be paid" not in na.summary and "PMSBY-R1" in na.summary  # plain fallback
    bad = ok.model_copy(update={"passed": False, "findings": [
        Finding(severity=Severity.BLOCKER, target="x", reason="broken")]})  # fmt: skip
    na = next_action(p, bad, result, use_model=False)
    assert na.action is Action.ESCALATE_TO_HUMAN and "broken" in na.summary


def test_discovery_notes_only_for_weak_premium_evidence():
    facts, docs, _, _ = load("case_a")
    pb = docs[0]
    weak = pb.model_copy(update={"fields": [
        f.model_copy(update={"value": f.value.replace("PMJJBY PREM RENEWAL", "LIC PREMIUM")})
        if f.name == "txn" else f for f in pb.fields]})  # fmt: skip
    fake = Fake(LineNote(likely="likely", reason="Rs 436 in late May matches PMJJBY renewal."))
    ents, notes = discover(facts, [weak, *docs[1:]], complete_json=fake)
    strengths = {e.scheme_id: e.strength for e in ents}
    assert strengths[SchemeId.PMJJBY] is Strength.POSSIBLE  # the note never upgrades it
    assert list(notes.values()) == ["likely: Rs 436 in late May matches PMJJBY renewal."]
    assert len(fake.calls) == 1


def test_packet_html_shows_sources_findings_and_revealed_names():
    p, ent, facts, docs, result = packet_for("case_a", SchemeId.RUPAY)
    v = verify(p, ent, facts, docs, result).model_copy(update={"findings": [
        Finding(severity=Severity.WARNING, target="rupay-claim-form.place_of_death",
                reason="Check the district.")]})  # fmt: skip
    html = render_packet(p, v, result, "Summary text.", reveal=lambda t: f"NAME<{t[-6:]}>")
    assert (
        "Card-issuing bank&#39;s declaration" in html or "Card-issuing bank's declaration" in html
    )
    assert "document field · case_a-d2#date_of_death" in html
    assert "Check the district." in html and "Summary text." in html
    assert "NAME&lt;" in html  # revealed through the callback, and HTML-escaped
    assert "RUPAY-R1, RUPAY-R2" in html


def test_doc_types_in_forms_have_labels():
    from suraksha.agents.filing.forms import ATTACHMENT_LABELS

    for scheme in SchemeId:
        for t in required_documents(scheme):
            assert t in ATTACHMENT_LABELS, t
    assert DocType.ID_PROOF in ATTACHMENT_LABELS
