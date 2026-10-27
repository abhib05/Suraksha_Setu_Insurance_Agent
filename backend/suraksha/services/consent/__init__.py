"""Consent service: record and withdraw consent; every data write on a case calls `require`."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from suraksha import audit
from suraksha.contracts import ConsentPurpose, ConsentRecord
from suraksha.db.base import new_id
from suraksha.db.models import Consent


class ConsentRequiredError(PermissionError):
    """Raised when a case has no active consent for the purpose."""


def record(session: Session, consent: ConsentRecord) -> str:
    row = Consent(
        id=new_id("cns"),
        case_id=consent.case_id,
        purpose=consent.purpose.value,
        scope=consent.scope,
        given_by=consent.given_by,
        method=consent.method.value,
        timestamp=consent.timestamp,
    )
    session.add(row)
    session.flush()
    audit.append(
        session,
        actor="service:consent",
        action="consent.given",
        case_id=consent.case_id,
        output=consent,
        detail={"consent_id": row.id, "purpose": row.purpose, "scope": row.scope},
    )
    return row.id


def withdraw(session: Session, case_id: str, purpose: ConsentPurpose | None = None) -> int:
    """Withdraw active consent (all purposes when `purpose` is None). Returns rows changed."""
    rows = _active(session, case_id, purpose)
    now = datetime.now(UTC)
    for row in rows:
        row.withdrawn_at = now
    session.flush()
    audit.append(
        session,
        actor="service:consent",
        action="consent.withdrawn",
        case_id=case_id,
        detail={"consent_ids": [r.id for r in rows], "purpose": purpose and purpose.value},
    )
    return len(rows)


def is_active(session: Session, case_id: str, purpose: ConsentPurpose) -> bool:
    return bool(_active(session, case_id, purpose))


def require(
    session: Session, case_id: str, purpose: ConsentPurpose = ConsentPurpose.PREPARE_CLAIMS
) -> None:
    if not is_active(session, case_id, purpose):
        raise ConsentRequiredError(f"case {case_id} has no active consent for {purpose.value}")


def _active(session: Session, case_id: str, purpose: ConsentPurpose | None) -> list[Consent]:
    q = select(Consent).where(Consent.case_id == case_id, Consent.withdrawn_at.is_(None))
    if purpose is not None:
        q = q.where(Consent.purpose == purpose.value)
    return list(session.scalars(q))
