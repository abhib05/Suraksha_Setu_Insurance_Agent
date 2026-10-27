"""The agents the worker runs for real cases: interview and documents come from what the
family gave us (chat intake + stored extractions); discovery and eligibility are the M3
services; filing, verifier and coordinator are the M5 agents with the model on; submission
goes to the partner portal through the M6 adapter."""

from functools import cached_property

from sqlalchemy import select

from suraksha.agents.stubs import StubAgents
from suraksha.config import get_settings
from suraksha.contracts import (
    CaseFacts,
    ClaimPacket,
    ExtractedDocument,
    SchemeId,
    SubmissionReceipt,
)
from suraksha.db import models as m
from suraksha.db.base import session_scope
from suraksha.services import intake as intake_svc
from suraksha.services import vault
from suraksha.services.submission.portal import PortalClient


class PortalSubmission:
    """Files packets with the partner portal (names revealed from the vault at that
    boundary, audited) and polls it for status."""

    strict_reveal = True  # a name token missing from the vault stops the filing

    @cached_property
    def portal(self) -> PortalClient:
        return PortalClient(get_settings().portal_base_url)

    def submit(self, packet: ClaimPacket) -> SubmissionReceipt:
        with session_scope() as s:
            case_id = s.get(m.Claim, packet.claim_id).case_id

            def reveal(token: str) -> str:
                try:
                    return vault.reveal(s, token, actor="service:submission",
                                        purpose="submission", case_id=case_id)  # fmt: skip
                except vault.VaultError:
                    if self.strict_reveal:
                        raise
                    return token

            return self.portal.submit(packet, reveal)

    def claim_status(self, claim_number: str, scheme: SchemeId) -> tuple[str, int | None]:
        return self.portal.status(claim_number)


class LiveAgents(PortalSubmission, StubAgents):
    use_model = True

    def extract_documents(self, case_id: str) -> list[ExtractedDocument]:
        with session_scope() as s:
            q = select(m.Document).where(m.Document.case_id == case_id,
                                         m.Document.extraction.is_not(None))  # fmt: skip
            return [ExtractedDocument.model_validate(d.extraction) for d in s.scalars(q)]

    def interview(self, case_id: str) -> CaseFacts:
        with session_scope() as s:
            draft = dict(intake_svc.get_intake(s, case_id).draft or {})
        facts = intake_svc.to_case_facts(case_id, draft)
        return intake_svc.enrich(facts, self.extract_documents(case_id))


class DemoAgents(PortalSubmission, StubAgents):
    """AGENTS=demo: case fixtures and no model, but real filing with the mock portal."""

    strict_reveal = False  # fixture name tokens are not in the vault
