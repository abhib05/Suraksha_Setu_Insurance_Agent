"""Case, consent, document and audit endpoints (M1). Every data write needs active consent."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from suraksha import audit
from suraksha.contracts import ConsentMethod, ConsentPurpose, ConsentRecord
from suraksha.db.base import new_id, session_scope
from suraksha.db.models import Case
from suraksha.services import consent, docstore, vault

router = APIRouter(prefix="/cases", tags=["cases"])


def db() -> Iterator[Session]:
    with session_scope() as session:
        yield session


DB = Annotated[Session, Depends(db)]


def _case(session: Session, case_id: str) -> Case:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    return case


class CaseIn(BaseModel):
    channel: str = "web_chat"


class CaseOut(BaseModel):
    case_id: str
    status: str


@router.post("", response_model=CaseOut, status_code=201)
def open_case(body: CaseIn, session: DB) -> CaseOut:
    case = Case(id=new_id("case"), channel=body.channel)
    session.add(case)
    session.flush()
    audit.append(
        session,
        actor="user:family",
        action="case.opened",
        case_id=case.id,
        detail={"channel": body.channel},
    )
    return CaseOut(case_id=case.id, status=case.status)


class ConsentIn(BaseModel):
    given_by_name: str  # tokenised into the vault immediately; never stored in plain text
    purpose: ConsentPurpose = ConsentPurpose.PREPARE_CLAIMS
    scope: list[str] = ["identity", "bank", "documents"]
    method: ConsentMethod = ConsentMethod.CHAT


class ConsentOut(BaseModel):
    consent_id: str
    given_by: str


@router.post("/{case_id}/consent", response_model=ConsentOut, status_code=201)
def give_consent(case_id: str, body: ConsentIn, session: DB) -> ConsentOut:
    _case(session, case_id)
    token = vault.tokenize(session, vault.SecretKind.NAME, body.given_by_name)
    record = ConsentRecord(
        case_id=case_id,
        purpose=body.purpose,
        scope=body.scope,
        given_by=token,
        method=body.method,
        timestamp=datetime.now(UTC),
    )
    return ConsentOut(consent_id=consent.record(session, record), given_by=token)


@router.post("/{case_id}/consent/withdraw")
def withdraw_consent(case_id: str, session: DB) -> dict[str, int]:
    _case(session, case_id)
    return {"withdrawn": consent.withdraw(session, case_id)}


class DocumentOut(BaseModel):
    doc_id: str
    sha256: str
    masking_status: str
    aadhaar_masked: int


@router.post("/{case_id}/documents", response_model=DocumentOut, status_code=201)
async def upload_document(case_id: str, file: UploadFile, session: DB) -> DocumentOut:
    _case(session, case_id)
    data = await file.read()
    try:
        doc = docstore.save(
            session,
            case_id,
            file.filename or "upload",
            data,
            file.content_type or "application/octet-stream",
        )
    except consent.ConsentRequiredError as e:
        raise HTTPException(403, str(e)) from e
    except docstore.DocumentRejectedError as e:
        raise HTTPException(422, str(e)) from e
    return DocumentOut(
        doc_id=doc.id,
        sha256=doc.sha256,
        masking_status=doc.masking_status,
        aadhaar_masked=doc.aadhaar_masked,
    )


class AuditEventOut(BaseModel):
    id: int
    ts: datetime
    actor: str
    action: str
    model: str | None
    prompt_version: str | None
    rule_versions: list[str] | None
    input_hash: str | None
    output_hash: str | None
    detail: dict[str, Any]
    hash: str


@router.get("/{case_id}/audit", response_model=list[AuditEventOut])
def case_audit(case_id: str, session: DB) -> list[AuditEventOut]:
    _case(session, case_id)
    return [
        AuditEventOut.model_validate(e, from_attributes=True)
        for e in audit.events_for_case(session, case_id)
    ]


audit_router = APIRouter(prefix="/audit", tags=["audit"])


class ChainOut(BaseModel):
    ok: bool
    events: int
    first_bad_id: int | None


@audit_router.get("/verify", response_model=ChainOut)
def verify_audit_chain(session: DB) -> ChainOut:
    c = audit.verify_chain(session)
    return ChainOut(ok=c.ok, events=c.events, first_bad_id=c.first_bad_id)
