from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from suraksha.contracts import (
    Approval,
    CaseFacts,
    ClaimPacket,
    Deceased,
    Decision,
    EventType,
    FilledForm,
    Finding,
    FormField,
    Gate,
    RuleResult,
    RuleStatus,
    Severity,
    SourceRef,
    VerifierVerdict,
)

NOW = datetime(2026, 10, 28, tzinfo=UTC)


def test_case_facts_rejects_raw_names_and_extra_fields():
    with pytest.raises(ValidationError):
        Deceased(name_token="Ramesh Kumar")  # a real name, not a vault token
    with pytest.raises(ValidationError):
        CaseFacts(case_id="c1", deceased=Deceased(name_token="tok_abc123"), aadhaar="1234")

    facts = CaseFacts(
        case_id="c1",
        deceased=Deceased(
            name_token="tok_abc123", date_of_death=date(2026, 9, 14), event_type="accident"
        ),
    )
    assert facts.deceased.event_type is EventType.ACCIDENT


def test_rule_result_three_valued_invariants():
    RuleResult(
        entitlement_id="e1",
        status=RuleStatus.UNKNOWN,
        rule_ids=["RUPAY-R1"],
        rule_version="2025-26.1",
        missing_facts=["card_last_txn_date"],
        explanation_en="We need the date the card was last used.",
    )
    with pytest.raises(ValidationError, match="missing_facts"):
        RuleResult(
            entitlement_id="e1",
            status=RuleStatus.UNKNOWN,
            rule_ids=["RUPAY-R1"],
            rule_version="2025-26.1",
            explanation_en="?",
        )
    with pytest.raises(ValidationError, match="rule id"):
        RuleResult(
            entitlement_id="e1",
            status=RuleStatus.ELIGIBLE,
            rule_ids=[],
            rule_version="2025-26.1",
            explanation_en="ok",
        )


def test_packet_reports_untraced_fields_and_idempotency_key():
    packet = ClaimPacket(
        claim_id="cl1",
        scheme_id="pmjjby",
        version=2,
        cover_letter="To the Branch Manager",
        forms=[
            FilledForm(
                template_id="pmjjby-claim",
                fields=[
                    FormField(
                        name="date_of_death",
                        value="2026-09-14",
                        source_ref=SourceRef(kind="document_field", ref="d1#date_of_death"),
                    ),
                    FormField(name="nominee_name", value="tok_n1", source_ref=None),
                ],
            )
        ],
    )
    assert packet.untraced_fields() == ["pmjjby-claim.nominee_name"]
    assert packet.idempotency_key == "cl1:v2"


def test_verdict_with_blocker_cannot_pass():
    with pytest.raises(ValidationError):
        VerifierVerdict(
            subject_id="cl1:v1",
            passed=True,
            findings=[Finding(severity=Severity.BLOCKER, target="x", reason="y")],
        )


def test_approval_rules():
    with pytest.raises(ValidationError, match="comment"):
        Approval(
            case_id="c1",
            claim_id="cl1",
            gate=Gate.PACKET_APPROVAL,
            approver="r1",
            packet_version=1,
            decision=Decision.RETURN,
            decided_at=NOW,
        )
    with pytest.raises(ValidationError, match="packet version"):
        Approval(
            case_id="c1",
            claim_id="cl1",
            gate=Gate.PACKET_APPROVAL,
            approver="r1",
            decision=Decision.APPROVE,
            decided_at=NOW,
        )


def test_every_contract_exports_a_json_schema():
    # The Meta API's response_format needs one per agent output.
    import suraksha.contracts as c
    from suraksha.contracts.common import Contract

    for name in c.__all__:
        obj = getattr(c, name)
        if isinstance(obj, type) and issubclass(obj, Contract):
            assert obj.model_json_schema()["type"] == "object"
