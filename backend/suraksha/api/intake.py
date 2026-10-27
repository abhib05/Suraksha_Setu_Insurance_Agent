"""Family-facing intake (M4): chat with the conversation agent, see extracted documents,
and finish the interview so the case workflow can continue."""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from openai import OpenAIError
from pydantic import BaseModel
from sqlalchemy import select

from suraksha import audit
from suraksha.agents.conversation.agent import take_turn
from suraksha.agents.screening.agent import screen_text
from suraksha.api.cases import DB, _case
from suraksha.contracts import ConsentPurpose
from suraksha.db import models as m
from suraksha.db.base import new_id
from suraksha.services import consent, docstore
from suraksha.services import intake as intake_svc
from suraksha.tools.llm import AgentOutputError, ReplayMissError, SpendLimitError

log = logging.getLogger("suraksha.intake")
router = APIRouter(tags=["intake"])

INJECTION_REPLY = (
    "I can only help with finding and claiming your family's insurance. "
    "Shall we continue with the next question?"
)
DISTRESS_REPLY = (
    "I'm so sorry you are going through this. You are not alone. A member of our team will "
    "call you. If you feel unsafe right now, please call Tele-MANAS on 14416 (free, 24x7)."
)
OFFLINE_REPLY = (
    "I'm having trouble responding right now. Your messages are saved; please try again in "
    "a few minutes, or a member of our team will contact you."
)
MODEL_ERRORS = (AgentOutputError, ReplayMissError, SpendLimitError, OpenAIError)


class ChatIn(BaseModel):
    message: str


class ChatOut(BaseModel):
    reply: str
    consent_active: bool
    intake_complete: bool
    known: dict[str, Any]
    flagged: str | None = None


def _task(session, case_id: str, kind: str, detail: str, assignee: str = "reviewer") -> str:
    t = m.Task(id=new_id("task"), case_id=case_id, kind=kind, detail=detail, assignee=assignee)
    session.add(t)
    session.flush()
    audit.append(session, actor="service:intake", action="task.created", case_id=case_id,
                 detail={"task_id": t.id, "kind": kind})  # fmt: skip
    return t.id


@router.post("/cases/{case_id}/chat", response_model=ChatOut)
def chat(case_id: str, body: ChatIn, session: DB) -> ChatOut:
    _case(session, case_id)
    text = body.message.strip()[:2000]
    if not text:
        raise HTTPException(422, "empty message")
    row = intake_svc.get_intake(session, case_id)
    given = consent.is_active(session, case_id, ConsentPurpose.PREPARE_CLAIMS)
    prior = intake_svc.history(session, case_id)
    intake_svc.add_message(session, case_id, "user", text)

    flagged = None
    try:
        screening = screen_text(text)
        if screening.distress:
            flagged, reply = "distress", DISTRESS_REPLY
            _task(
                session, case_id, "family_support", "Family member may be in distress; call back."
            )
        elif screening.injection:
            flagged, reply = "injection", INJECTION_REPLY
        else:
            turn, version = take_turn(prior, text, known=intake_svc.known_for_prompt(row.draft),
                                      consent_given=given)  # fmt: skip
            reply = turn.reply
            changed = intake_svc.merge(session, row, turn.updates)
            row.complete = row.complete or turn.intake_complete
            if turn.wants_human:
                _task(session, case_id, "family_support", "Family asked to talk to a person.")
            audit.append(
                session,
                actor="agent:conversation",
                action="chat.turn",
                case_id=case_id,
                model="muse-spark-1.3",
                prompt_version=version,
                input=text,
                output=turn,
                detail={"changed": changed, "intake_complete": row.complete},
            )
    except MODEL_ERRORS as e:
        log.warning("chat model call failed: %s", e)
        flagged, reply = "model_unavailable", OFFLINE_REPLY
    if flagged:
        audit.append(session, actor="service:screening", action=f"chat.{flagged}",
                     case_id=case_id, input=text)  # fmt: skip
    intake_svc.add_message(session, case_id, "assistant", reply)
    return ChatOut(
        reply=reply,
        consent_active=given,
        intake_complete=row.complete,
        known=intake_svc.known_for_prompt(row.draft),
        flagged=flagged,
    )


class ChatHistoryOut(BaseModel):
    messages: list[dict[str, str]]
    consent_active: bool
    intake_complete: bool
    finished: bool
    known: dict[str, Any]


@router.get("/cases/{case_id}/chat", response_model=ChatHistoryOut)
def chat_history(case_id: str, session: DB) -> ChatHistoryOut:
    _case(session, case_id)
    row = intake_svc.get_intake(session, case_id)
    return ChatHistoryOut(
        messages=intake_svc.history(session, case_id),
        consent_active=consent.is_active(session, case_id, ConsentPurpose.PREPARE_CLAIMS),
        intake_complete=row.complete, finished=row.finished,
        known=intake_svc.known_for_prompt(row.draft),
    )  # fmt: skip


@router.post("/cases/{case_id}/intake/finish")
async def finish_intake(case_id: str, session: DB) -> dict:
    _case(session, case_id)
    consent.require(session, case_id)
    row = intake_svc.get_intake(session, case_id)
    row.finished = True
    audit.append(
        session,
        actor="user:family",
        action="intake.finished",
        case_id=case_id,
        detail={"agent_marked_complete": row.complete},
    )
    session.commit()
    from suraksha.api.workflows import signal_intake_if_running

    await signal_intake_if_running(case_id)
    return {"ok": True}


class DocumentView(BaseModel):
    doc_id: str
    filename: str
    created_at: datetime
    masking_status: str
    aadhaar_masked: int
    doc_type: str | None
    extraction: dict[str, Any] | None


@router.get("/cases/{case_id}/documents", response_model=list[DocumentView])
def list_documents(case_id: str, session: DB) -> list[DocumentView]:
    _case(session, case_id)
    q = select(m.Document).where(m.Document.case_id == case_id).order_by(m.Document.created_at)
    return [DocumentView(doc_id=d.id, filename=d.filename, created_at=d.created_at,
                         masking_status=d.masking_status, aadhaar_masked=d.aadhaar_masked,
                         doc_type=d.doc_type, extraction=d.extraction)
            for d in session.scalars(q)]  # fmt: skip


@router.post("/cases/{case_id}/documents/extract-pending")
def extract_pending(case_id: str, session: DB) -> dict[str, int]:
    """Re-read documents uploaded while the model was unavailable."""
    from suraksha.api.cases import _extract

    _case(session, case_id)
    q = select(m.Document).where(
        m.Document.case_id == case_id,
        m.Document.extraction.is_(None),
        m.Document.masking_status.in_(("masked", "none_found")),
    )
    counts: dict[str, int] = {}
    for doc in session.scalars(q).all():
        status = _extract(session, doc)
        counts[status] = counts.get(status, 0) + 1
    return counts


@router.get("/cases/{case_id}/documents/{doc_id}/image")
def document_image(case_id: str, doc_id: str, session: DB) -> Response:
    """The masked working copy only; originals never leave the docstore over HTTP."""
    doc = session.get(m.Document, doc_id)
    if doc is None or doc.case_id != case_id:
        raise HTTPException(404, "document not found")
    try:
        return Response(docstore.working_copy(doc), media_type="image/png")
    except docstore.NotMaskedError as e:
        raise HTTPException(409, str(e)) from e
