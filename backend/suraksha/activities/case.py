"""Temporal activities: each wraps one agent or service call, persists the result and writes
an audit event. Workflows never touch agents, the database or the network directly."""

from pydantic import BaseModel
from temporalio import activity

from suraksha.activities.store import Store
from suraksha.agents.stubs import StubAgents
from suraksha.contracts import (
    CaseFacts,
    ClaimPacket,
    DocType,
    Entitlement,
    ExtractedDocument,
    RuleResult,
    SchemeId,
    SubmissionReceipt,
    VerifierVerdict,
)


class PortalStatus(BaseModel):
    status: str
    paid_amount_inr: int | None = None


class CaseActivities:
    def __init__(self, agents: StubAgents, store: Store):
        self.agents = agents
        self.store = store

    def all(self) -> list:
        return [
            self.interview, self.extract_documents, self.discover, self.check_eligibility,
            self.verify_entitlements, self.brief_family, self.open_claim,
            self.missing_documents, self.note_document_uploaded, self.build_packet,
            self.verify_packet, self.submit_packet, self.check_claim_status,
            self.set_claim_status, self.create_task, self.record_event,
        ]  # fmt: skip

    @activity.defn
    def interview(self, case_id: str) -> CaseFacts:
        facts = self.agents.interview(case_id)
        self.store.save_facts(facts)
        self.store.record(case_id, "agent:conversation", "interview.completed", output=facts)
        return facts

    @activity.defn
    def extract_documents(self, case_id: str) -> list[ExtractedDocument]:
        docs = self.agents.extract_documents(case_id)
        self.store.record(case_id, "agent:document", "documents.extracted", output=docs,
                          detail={"doc_ids": [d.doc_id for d in docs]})  # fmt: skip
        return docs

    @activity.defn
    def discover(self, facts: CaseFacts, docs: list[ExtractedDocument]) -> list[Entitlement]:
        ents = self.agents.discover(facts, docs)
        self.store.save_entitlements(ents, [])
        self.store.record(facts.case_id, "agent:discovery", "discovery.completed", output=ents,
                          detail={"schemes": [e.scheme_id.value for e in ents]})  # fmt: skip
        return ents

    @activity.defn
    def check_eligibility(
        self, ent: Entitlement, facts: CaseFacts, docs: list[ExtractedDocument],
        known_facts: dict[str, str],
    ) -> RuleResult:  # fmt: skip
        result = self.agents.check_eligibility(ent, facts, docs, known_facts)
        self.store.save_entitlements([ent], [result])
        self.store.record(
            ent.case_id, "service:rules", "eligibility.checked", input=ent, output=result,
            rule_versions=[f"{r}@{result.rule_version}" for r in result.rule_ids],
            detail={"entitlement_id": ent.entitlement_id, "status": result.status.value,
                    "rule_ids": result.rule_ids, "missing_facts": result.missing_facts},
        )  # fmt: skip
        return result

    @activity.defn
    def verify_entitlements(
        self, ents: list[Entitlement], results: list[RuleResult]
    ) -> VerifierVerdict:
        verdict = self.agents.verify_entitlements(ents, results)
        self.store.record(
            ents[0].case_id,
            "agent:verifier",
            "discovery.verified",
            output=verdict,
            detail={"passed": verdict.passed, "findings": len(verdict.findings)},
        )
        return verdict

    @activity.defn
    def brief_family(self, case_id: str, ents: list[Entitlement], results: list[RuleResult]) -> str:
        text = self.agents.brief(ents, results)
        self.store.record(case_id, "agent:conversation", "family.briefed", output=text)
        return text

    @activity.defn
    def open_claim(self, case_id: str, claim_id: str, ent: Entitlement) -> None:
        self.store.open_claim(case_id, claim_id, ent)
        self.store.record(
            case_id,
            "service:workflow",
            "claim.opened",
            detail={"claim_id": claim_id, "scheme": ent.scheme_id.value},
        )

    @activity.defn
    def missing_documents(
        self, claim_id: str, scheme: SchemeId, docs: list[ExtractedDocument]
    ) -> list[DocType]:
        return self.agents.missing_documents(claim_id, scheme, docs)

    @activity.defn
    def note_document_uploaded(self, claim_id: str) -> None:
        self.agents.document_uploaded(claim_id)

    @activity.defn
    def build_packet(
        self, claim_id: str, ent: Entitlement, version: int, reviewer_comment: str | None
    ) -> ClaimPacket:
        packet = self.agents.build_packet(claim_id, ent, version, reviewer_comment)
        self.store.record(
            ent.case_id,
            "agent:filing",
            "packet.built",
            output=packet,
            detail={"claim_id": claim_id, "version": version},
        )
        return packet

    @activity.defn
    def verify_packet(self, case_id: str, packet: ClaimPacket) -> VerifierVerdict:
        verdict = self.agents.verify_packet(packet)
        self.store.save_packet(packet, verdict)
        self.store.record(case_id, "agent:verifier", "packet.verified", input=packet,
                          output=verdict, detail={"claim_id": packet.claim_id,
                          "version": packet.version, "passed": verdict.passed})  # fmt: skip
        return verdict

    @activity.defn
    def submit_packet(self, case_id: str, packet: ClaimPacket) -> SubmissionReceipt:
        receipt = self.agents.submit(packet)
        self.store.record(case_id, "service:submission", "claim.submitted", input=packet,
                          output=receipt, detail={"claim_id": packet.claim_id,
                          "claim_number": receipt.claim_number,
                          "idempotency_key": receipt.idempotency_key})  # fmt: skip
        return receipt

    @activity.defn
    def check_claim_status(self, claim_number: str, scheme: SchemeId) -> PortalStatus:
        status, paid = self.agents.claim_status(claim_number, scheme)
        return PortalStatus(status=status, paid_amount_inr=paid)

    @activity.defn
    def set_claim_status(
        self, case_id: str, claim_id: str, status: str, claim_number: str | None
    ) -> None:
        self.store.set_claim_status(claim_id, status, claim_number)
        self.store.record(
            case_id,
            "service:workflow",
            "claim.status",
            detail={"claim_id": claim_id, "status": status},
        )

    @activity.defn
    def create_task(
        self, case_id: str, claim_id: str | None, kind: str, detail: str, assignee: str
    ) -> str:
        return self.store.create_task(case_id, claim_id, kind, detail, assignee)

    @activity.defn
    def record_event(self, case_id: str, action: str, detail: dict) -> None:
        self.store.record(case_id, "service:workflow", action, detail=detail)
