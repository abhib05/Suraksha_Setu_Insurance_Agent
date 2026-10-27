"""The agents the worker runs for real cases: interview and documents come from what the
family gave us (chat intake + stored extractions); discovery and eligibility are the M3
services; filing, verifier and submission stay stubs until M5-M6."""

from sqlalchemy import select

from suraksha.agents.stubs import StubAgents
from suraksha.contracts import CaseFacts, ExtractedDocument
from suraksha.db import models as m
from suraksha.db.base import session_scope
from suraksha.services import intake as intake_svc


class LiveAgents(StubAgents):
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
