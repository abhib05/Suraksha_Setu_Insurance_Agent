"""Verifier verdicts, coordinator decisions and human approvals."""

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from suraksha.contracts.common import Contract


class Severity(StrEnum):
    BLOCKER = "blocker"  # packet cannot go out
    WARNING = "warning"  # reviewer must look
    INFO = "info"


class Finding(Contract):
    severity: Severity
    target: str  # what it is about, e.g. "pmjjby-form.date_of_death" or "entitlement:e1"
    reason: str


class VerifierVerdict(Contract):
    """Produced by the read-only verifier agent."""

    subject_id: str  # packet claim_id:vN, entitlement id or rule result id
    passed: bool
    findings: list[Finding] = []

    @model_validator(mode="after")
    def _blockers_fail(self) -> "VerifierVerdict":
        if self.passed and any(f.severity is Severity.BLOCKER for f in self.findings):
            raise ValueError("a verdict with a blocker finding cannot pass")
        return self


class Action(StrEnum):
    """The only moves the coordinator may pick; the workflow rejects anything else."""

    ASK_QUESTION = "ask_question"
    REQUEST_DOCUMENT = "request_document"
    RUN_DISCOVERY = "run_discovery"
    CHECK_ELIGIBILITY = "check_eligibility"
    BRIEF_FAMILY = "brief_family"
    BUILD_PACKET = "build_packet"
    REQUEST_APPROVAL = "request_approval"
    SUBMIT = "submit"
    CHECK_STATUS = "check_status"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    CLOSE = "close"


class NextAction(Contract):
    """Produced by the case coordinator."""

    action: Action
    reason: str
    summary: str  # one paragraph for the reviewer


class Gate(StrEnum):
    CONSENT = "consent"
    ELIGIBILITY_REVIEW = "eligibility_review"
    PACKET_APPROVAL = "packet_approval"
    ESCALATION = "escalation"
    CLOSE_REJECTED = "close_rejected"
    PAYOUT_CONFIRMATION = "payout_confirmation"


class Decision(StrEnum):
    APPROVE = "approve"
    RETURN = "return"


class Approval(Contract):
    """Recorded by the review console when a human passes a gate."""

    case_id: str
    claim_id: str | None = None
    gate: Gate
    approver: str  # reviewer user id
    packet_version: int | None = Field(default=None, ge=1)
    decision: Decision
    comment: str | None = None
    decided_at: datetime

    @model_validator(mode="after")
    def _return_needs_comment(self) -> "Approval":
        if self.decision is Decision.RETURN and not (self.comment or "").strip():
            raise ValueError("returning a packet needs a comment saying what to fix")
        if self.gate is Gate.PACKET_APPROVAL and self.packet_version is None:
            raise ValueError("packet approval must name the packet version")
        return self
