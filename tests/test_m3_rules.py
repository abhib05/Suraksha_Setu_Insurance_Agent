"""M3 done-when: rules alone give the right answers for cases A, B and C, and every rule
branch (pass, fail, each missing-fact path) is covered."""

import json
from datetime import date
from pathlib import Path

import pytest

from suraksha.contracts import (
    AssetType,
    CaseFacts,
    DocType,
    ExtractedDocument,
    RuleStatus,
    SchemeId,
    Strength,
)
from suraksha.services import rules
from suraksha.services.registry import expected_payout, required_documents
from suraksha.services.rules import evaluate_rule, explain_rule, load_rules
from suraksha.services.rules.facts import rule_facts
from suraksha.services.rules.logic import UNKNOWN, cover_end, evaluate
from suraksha.services.rules.matchers import match_entitlements, premium_debits, transactions

CASES = Path(__file__).resolve().parents[1] / "evals" / "cases"


def load_case(name: str):
    d = CASES / name
    facts = CaseFacts.model_validate_json((d / "facts.json").read_text())
    docs = [ExtractedDocument.model_validate(x)
            for x in json.loads((d / "documents.json").read_text())]  # fmt: skip
    return facts, docs, json.loads((d / "expected.json").read_text())


# --- the three evaluation cases -----------------------------------------------------


@pytest.mark.parametrize("case", ["case_a", "case_b", "case_c"])
def test_eval_case_rules_alone(case):
    facts, docs, expected = load_case(case)
    ents = {e.scheme_id.value: e for e in match_entitlements(facts, docs)}
    assert {s: e.strength.value for s, e in ents.items()} == expected["covers"]
    assert {s: e.expected_payout_inr for s, e in ents.items()} == expected["payout_inr"]
    rf = rule_facts(facts, docs)
    for scheme, exp in expected["rules"].items():
        r = rules.check_entitlement(ents[scheme], rf)
        assert r.status.value == exp["status"], (scheme, r)
        assert r.rule_ids == exp["rule_ids"], (scheme, r)
        assert r.missing_facts == exp.get("missing_facts", [])
        assert r.explanation_en  # every answer is explained in words
    for scheme, missing in expected["missing_documents"].items():
        held = {d.doc_type for d in docs}
        got = [t.value for t in required_documents(SchemeId(scheme)) if t not in held]
        assert got == missing


def test_unknown_resolves_when_family_answers():
    facts, docs, _ = load_case("case_b")
    rupay = next(e for e in match_entitlements(facts, docs) if e.scheme_id is SchemeId.RUPAY)
    assert rules.check_entitlement(rupay, rule_facts(facts, docs)).status is RuleStatus.UNKNOWN
    answered = rule_facts(facts, docs, {"card_last_txn_date": "2026-07-20"})
    assert rules.check_entitlement(rupay, answered).status is RuleStatus.ELIGIBLE
    stale = rule_facts(facts, docs, {"card_last_txn_date": "2026-03-01"})
    assert rules.check_entitlement(rupay, stale).status is RuleStatus.NOT_ELIGIBLE


# --- every rule branch ------------------------------------------------------------

BASE = {
    "event_type": "accident", "date_of_death": "2026-09-14", "age_at_death": 41,
    "pmjjby_premium_date": "2026-05-28", "pmjjby_premium_inr": 436,
    "pmjjby_enrolment_date": "2024-05-28",
    "pmsby_premium_date": "2026-05-25", "pmsby_premium_inr": 20,
    "pmsby_enrolment_date": "2024-05-25",
    "card_last_txn_date": "2026-09-10", "card_issue_date": "2019-03-02",
}  # fmt: skip

BRANCHES = [
    # rule, overrides, expected status
    ("PMJJBY-R1", {}, "eligible"),
    ("PMJJBY-R1", {"pmjjby_premium_date": "2025-05-28"}, "not_eligible"),  # last year's cover
    (
        "PMJJBY-R1",
        {"pmjjby_premium_date": "2026-03-10", "pmjjby_premium_inr": 114},
        "not_eligible",
    ),  # pro-rata Mar debit only covers to 31 May 2026
    ("PMJJBY-R1", {"pmjjby_premium_date": "2026-09-20"}, "not_eligible"),  # paid after death
    ("PMJJBY-R2", {}, "eligible"),
    ("PMJJBY-R2", {"event_type": "illness", "pmjjby_enrolment_date": "2026-09-01"}, "not_eligible"),
    ("PMJJBY-R2", {"event_type": "illness", "pmjjby_enrolment_date": "2026-08-15"}, "eligible"),
    ("PMJJBY-R2", {"pmjjby_enrolment_date": "2026-09-10"}, "eligible"),  # accident in lien: ok
    ("PMJJBY-R3", {"age_at_death": 55}, "eligible"),
    ("PMJJBY-R3", {"age_at_death": 56}, "not_eligible"),
    ("PMSBY-R1", {}, "eligible"),
    ("PMSBY-R1", {"pmsby_premium_date": "2025-05-25"}, "not_eligible"),
    ("PMSBY-R2", {"event_type": "illness"}, "not_eligible"),
    ("PMSBY-R2", {"event_type": "suicide"}, "not_eligible"),
    ("PMSBY-R3", {"age_at_death": 70}, "eligible"),
    ("PMSBY-R3", {"age_at_death": 71}, "not_eligible"),
    ("PMSBY-R4", {"pmsby_enrolment_date": "2026-09-01"}, "not_eligible"),
    ("PMSBY-R4", {}, "eligible"),
    ("RUPAY-R1", {"event_type": "natural"}, "not_eligible"),
    ("RUPAY-R2", {"card_last_txn_date": "2026-06-16"}, "eligible"),  # day 90
    ("RUPAY-R2", {"card_last_txn_date": "2026-06-15"}, "not_eligible"),  # day 91
    ("RUPAY-R2", {"card_last_txn_date": "2026-09-14"}, "eligible"),  # accident day itself
    (
        "RUPAY-R2",
        {"card_last_txn_date": "2026-01-01", "card_issue_date": "2026-07-01"},
        "eligible",
    ),  # new card, no use needed
]


@pytest.mark.parametrize(("rule_id", "overrides", "status"), BRANCHES)
def test_rule_branch(rule_id, overrides, status):
    out = evaluate_rule(explain_rule(rule_id), {**BASE, **overrides})
    assert out.status.value == status


def test_every_rule_has_a_branch_test_and_cites_a_source():
    tested = {b[0] for b in BRANCHES}
    for r in load_rules():
        assert r.id in tested, f"{r.id} has no branch test"
        assert r.source and r.explain_pass and r.explain_fail


@pytest.mark.parametrize("rule", load_rules(), ids=lambda r: r.id)
def test_each_required_fact_missing_gives_unknown_or_a_decided_short_circuit(rule):
    """Drop each required fact in turn: the rule is unknown and names that fact, unless
    the other facts already decide it (or-short-circuit)."""
    for fact in rule.requires:
        facts = {k: v for k, v in BASE.items() if k != fact}
        out = evaluate_rule(rule, facts)
        if out.status is RuleStatus.UNKNOWN:
            assert fact in out.missing
        else:
            assert out.missing == []


def test_scheme_result_unknown_lists_every_missing_fact_with_a_question():
    facts = {k: v for k, v in BASE.items() if k not in ("age_at_death", "pmsby_enrolment_date")}
    r = rules.check(SchemeId.PMSBY, facts, "e1")
    assert r.status is RuleStatus.UNKNOWN
    assert r.missing_facts == ["age_at_death", "pmsby_enrolment_date"]
    assert "their age" in r.explanation_en and "first joined PMSBY" in r.explanation_en


def test_not_eligible_beats_unknown_and_cites_only_failed_rules():
    facts = {**BASE, "event_type": "illness"}
    del facts["age_at_death"]
    r = rules.check(SchemeId.PMSBY, facts, "e1")
    assert r.status is RuleStatus.NOT_ELIGIBLE and r.rule_ids == ["PMSBY-R2"]


def test_rules_are_chosen_by_date_of_death():
    old = {**BASE, "date_of_death": "2021-01-10"}
    with pytest.raises(LookupError):
        rules.check(SchemeId.PMSBY, old, "e1")  # no ruleset loaded for before June 2022


# --- evaluator --------------------------------------------------------------------


def test_kleene_logic():
    t, f, u = {"==": [1, 1]}, {"==": [1, 2]}, {"var": "missing"}
    assert evaluate({"or": [t, u]}, {}).value is True
    assert evaluate({"and": [f, u]}, {}).value is False
    assert evaluate({"and": [t, u]}, {}).value is UNKNOWN
    assert evaluate({"!": [u]}, {}).missing == ["missing"]
    assert evaluate({"days_between": ["2026-01-01", "2026-03-01"]}, {}).value == 59


@pytest.mark.parametrize(("paid", "amount", "end"), [
    ("2026-05-28", 436, "2027-05-31"),  # May renewal
    ("2026-08-05", 436, "2027-05-31"),  # late enrolment in the year
    ("2026-03-10", 114, "2026-05-31"),  # last-quarter pro-rata
    ("2026-05-10", 114, "2026-05-31"),  # pro-rata in May is not a renewal
])  # fmt: skip
def test_cover_end(paid, amount, end):
    assert cover_end(paid, amount, 436) == end


# --- matchers and fact inference ---------------------------------------------------


def _passbook(*lines):
    return [ExtractedDocument(
        doc_id="d1", case_id="c", doc_type=DocType.PASSBOOK, masked_copy_ref="x", sha256="0" * 64,
        fields=[{"name": "txn", "value": v, "confidence": 0.9} for v in lines],
    )]  # fmt: skip


def test_premium_matcher_strength():
    txns = transactions(_passbook(
        "2026-05-28|PMJJBY PREM|436|DR",
        "2026-05-29|LIC PREMIUM|436|DR",  # right amount, generic insurance words
        "2026-05-30|UPI GROCERY|436|DR",  # right amount, nothing insurance-like
        "2026-05-25|PMSBY|20|CR",  # a credit is never a premium
    ))  # fmt: skip
    hits = premium_debits(txns, SchemeId.PMJJBY)
    assert [(h.txn.description, h.strength) for h in hits] == [
        ("PMJJBY PREM", Strength.STRONG),
        ("LIC PREMIUM", Strength.POSSIBLE),
    ]
    assert premium_debits(txns, SchemeId.PMSBY) == []


def test_unreadable_lines_are_skipped():
    assert transactions(_passbook("garbled", "2026-13-01|X|1|DR", "2026-05-01|A|1|DR")) != []
    assert len(transactions(_passbook("garbled", "2026-05-01|A|1|DR"))) == 1


def test_enrolment_date_only_when_evidence_settles_it():
    facts, _, _ = load_case("case_a")
    # One May renewal debit 10 days before death: renewal or first year? Unknown -> ask.
    f = facts.model_copy(update={"deceased": facts.deceased.model_copy(
        update={"date_of_death": date(2026, 6, 7)})})  # fmt: skip
    rf = rule_facts(f, _passbook("2026-05-28|PMJJBY|436|DR"))
    assert "pmjjby_enrolment_date" not in rf
    # A second, earlier year's debit proves it is a renewal.
    rf = rule_facts(f, _passbook("2025-05-28|PMJJBY|436|DR", "2026-05-28|PMJJBY|436|DR"))
    assert rf["pmjjby_enrolment_date"] == "2025-05-28"
    # An off-season debit is a fresh enrolment: lien starts on that day.
    rf = rule_facts(f, _passbook("2025-05-28|PMJJBY|436|DR", "2026-02-02|PMJJBY|228|DR"))
    assert rf["pmjjby_enrolment_date"] == "2026-02-02"


def test_rupay_payout_tier_and_one_card_only():
    assert expected_payout(SchemeId.RUPAY, "2018-08-28") == 100_000
    assert expected_payout(SchemeId.RUPAY, "2018-08-29") == 200_000
    facts, docs, _ = load_case("case_a")
    card = next(a for a in facts.assets if a.type is AssetType.DEBIT_CARD)
    two = facts.model_copy(
        update={"assets": [*facts.assets, card.model_copy(update={"asset_id": "x"})]}
    )
    assert sum(e.scheme_id is SchemeId.RUPAY for e in match_entitlements(two, docs)) == 1
