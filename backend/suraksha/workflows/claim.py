"""claim_workflow: one per cover, the state machine from SRS 3.2.

rules -> (unknown: collect facts | not eligible: close) -> documents -> build + verify ->
human approval (returned: rebuild) -> submitted -> under review -> paid -> payout confirmed
"""

from datetime import timedelta

from pydantic import BaseModel
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from suraksha.activities.case import CaseActivities
    from suraksha.contracts import (
        Action,
        Approval,
        CaseFacts,
        ClaimPacket,
        Decision,
        Entitlement,
        ExtractedDocument,
        RuleResult,
        RuleStatus,
        Severity,
        VerifierVerdict,
    )
    from suraksha.services.submission.portal import REJECTED
    from suraksha.workflows.base import FinalError, StepRunner


class ClaimInput(BaseModel):
    case_id: str
    claim_id: str
    entitlement: Entitlement
    rule_result: RuleResult
    case_facts: CaseFacts
    documents: list[ExtractedDocument]
    known_facts: dict[str, str] = {}
    status_poll_seconds: int = 30


class ClaimState(BaseModel):
    claim_id: str
    scheme_id: str
    stage: str
    packet_version: int = 0
    packet: ClaimPacket | None = None
    verdict: VerifierVerdict | None = None
    rule_result: RuleResult | None = None
    claim_number: str | None = None
    paid_amount_inr: int | None = None
    open_task: str | None = None
    explanation: str | None = None
    summary: str | None = None  # coordinator's note for the reviewer


class ClaimOutcome(BaseModel):
    claim_id: str
    scheme_id: str
    status: str  # closed_paid | closed_short_paid | closed_not_eligible
    claim_number: str | None = None
    paid_amount_inr: int | None = None


@workflow.defn
class ClaimWorkflow(StepRunner):
    def __init__(self) -> None:
        super().__init__()
        self.st: ClaimState | None = None
        self.facts: dict[str, str] = {}
        self.decision: Approval | None = None
        self.uploads = 0
        self.payout_received: int | None = None

    # human gates and family inputs
    @workflow.signal
    def approve_packet(self, approval: Approval) -> None:
        self._decide(approval, Decision.APPROVE)

    @workflow.signal
    def return_packet(self, approval: Approval) -> None:
        self._decide(approval, Decision.RETURN)

    def _decide(self, approval: Approval, expected: Decision) -> None:
        # Ignore decisions on a stale packet version or with the wrong verb.
        if (
            self.st
            and approval.decision is expected
            and (approval.packet_version == self.st.packet_version)
        ):
            self.decision = approval

    @workflow.signal
    def confirm_payout(self, amount_inr: int) -> None:
        self.payout_received = amount_inr

    @workflow.signal
    def facts_provided(self, facts: dict[str, str]) -> None:
        self.facts.update(facts)

    @workflow.signal
    def document_uploaded(self) -> None:
        self.uploads += 1

    @workflow.query
    def state(self) -> ClaimState:
        assert self.st is not None
        return self.st.model_copy(update={"open_task": self.open_task})

    async def _stage(self, stage: str) -> None:
        self.st.stage = stage
        await self.step(CaseActivities.set_claim_status, self.case_id, self.claim_id, stage,
                        self.st.claim_number)  # fmt: skip

    @workflow.run
    async def run(self, inp: ClaimInput) -> ClaimOutcome:
        self.case_id, self.claim_id = inp.case_id, inp.claim_id
        ent = inp.entitlement
        self.facts = dict(inp.known_facts)
        self.st = ClaimState(
            claim_id=inp.claim_id,
            scheme_id=ent.scheme_id.value,
            stage="cover_found",
            rule_result=inp.rule_result,
        )
        await self.step(CaseActivities.open_claim, self.case_id, self.claim_id, ent)

        # 1. Rules: collect missing facts until the result is known.
        result = inp.rule_result
        while result.status is RuleStatus.UNKNOWN:
            await self._stage("collecting_facts")
            asked = len(self.facts)
            await self.human_task(
                "missing_facts",
                "Please tell us: " + ", ".join(result.missing_facts),
                assignee="family",
                wait=False,
            )
            await workflow.wait_condition(lambda asked=asked: len(self.facts) > asked)
            self.open_task = None
            result = await self.step(CaseActivities.check_eligibility, ent, inp.case_facts,
                                     inp.documents, self.facts)  # fmt: skip
            self.st.rule_result = result
        if result.status is RuleStatus.NOT_ELIGIBLE:
            self.st.explanation = result.explanation_en
            await self._stage("closed_not_eligible")
            return self._outcome("closed_not_eligible")

        # 2. Documents: one task per round until nothing required is missing.
        while missing := await self.step(CaseActivities.missing_documents, self.claim_id,
                                         ent.scheme_id, inp.documents):  # fmt: skip
            await self._stage("collecting_documents")
            seen = self.uploads
            await self.human_task(
                "missing_document",
                "Please upload: " + ", ".join(d.value for d in missing),
                assignee="family",
                wait=False,
            )
            await workflow.wait_condition(lambda seen=seen: self.uploads > seen)
            self.open_task = None
            await self.step(CaseActivities.note_document_uploaded, self.claim_id)

        # 3. Build, verify, and wait for a human to approve this exact version.
        comment: str | None = None
        while True:
            self.st.packet_version += 1
            await self._stage("building_packet")
            packet = await self.step(CaseActivities.build_packet, self.claim_id, ent,
                                     inp.case_facts, inp.documents, self.st.packet_version,
                                     comment)  # fmt: skip
            verdict = await self.step(CaseActivities.verify_packet, self.case_id, packet, ent,
                                      inp.case_facts, inp.documents, result)  # fmt: skip
            nxt = await self.step(CaseActivities.coordinate, self.case_id, packet, verdict, result)
            self.st.packet, self.st.verdict, self.st.summary = packet, verdict, nxt.summary
            if nxt.action is not Action.REQUEST_APPROVAL:
                reasons = "; ".join(f.reason for f in verdict.findings
                                    if f.severity is Severity.BLOCKER)  # fmt: skip
                await self.human_task(
                    "verifier_failed", f"v{packet.version}: {reasons}", assignee="reviewer"
                )
                comment = f"fix verifier findings: {reasons}"
                continue
            self.decision = None
            await self._stage("awaiting_approval")
            await workflow.wait_condition(lambda: self.decision is not None)
            if self.decision.decision is not Decision.APPROVE:
                comment = self.decision.comment
                continue

            # 4. Submit (idempotent per claim + version). A rejection by the claim desk is
            # final for this version: a reviewer looks at it, then the packet is rebuilt.
            receipt = await self.step(CaseActivities.submit_packet, self.case_id, packet,
                                      final_error=REJECTED)  # fmt: skip
            if not isinstance(receipt, FinalError):
                break
            await self.human_task("portal_rejected", f"v{packet.version}: {receipt.message}",
                                  assignee="reviewer")  # fmt: skip
            comment = f"claim desk rejected v{packet.version}: {receipt.message}"

        # 5. Follow up until paid.
        self.st.claim_number = receipt.claim_number
        await self._stage("submitted")
        while True:
            portal = await self.step(CaseActivities.check_claim_status, receipt.claim_number,
                                     ent.scheme_id)  # fmt: skip
            if portal.status == "paid":
                self.st.paid_amount_inr = portal.paid_amount_inr
                break
            if portal.status != self.st.stage:
                await self._stage(portal.status)
            await workflow.sleep(timedelta(seconds=inp.status_poll_seconds))

        # 6. A human confirms the money reached the nominee; a shortfall opens an appeal.
        await self._stage("awaiting_payout_confirmation")
        await workflow.wait_condition(lambda: self.payout_received is not None)
        expected = ent.expected_payout_inr or 0
        if self.payout_received < expected:
            await self._stage("appeal_open")
            await self.human_task(
                "appeal",
                f"Paid Rs {self.payout_received:,} of Rs {expected:,}; start appeal.",
                assignee="reviewer",
            )
            await self._stage("closed_short_paid")
            return self._outcome("closed_short_paid")
        await self._stage("closed_paid")
        return self._outcome("closed_paid")

    def _outcome(self, status: str) -> ClaimOutcome:
        return ClaimOutcome(claim_id=self.claim_id, scheme_id=self.st.scheme_id, status=status,
                            claim_number=self.st.claim_number,
                            paid_amount_inr=self.payout_received)  # fmt: skip
