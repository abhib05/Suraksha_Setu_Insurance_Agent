"""Append-only, hash-chained audit log.

Each event stores the hash of the one before it, so any edit to history breaks the chain.
There is no update or delete path here, and a database trigger (migration 0001) rejects
UPDATE, DELETE and TRUNCATE on audit_event even from raw SQL.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from suraksha.db.models import AuditEvent

GENESIS = "0" * 64
_LOCK_KEY = 0x5A_0A_D1_7  # advisory lock that serialises chain appends


def content_hash(value: Any) -> str:
    """Stable SHA-256 of a contract, dict, str or bytes; used for input/output hashes."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, bytes):
        blob = value
    elif isinstance(value, str):
        blob = value.encode()
    else:
        blob = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def _event_hash(prev_hash: str, fields: dict[str, Any]) -> str:
    body = json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def _hashed_fields(e: AuditEvent) -> dict[str, Any]:
    return {
        "ts": e.ts.astimezone(UTC).isoformat(),
        "case_id": e.case_id,
        "actor": e.actor,
        "action": e.action,
        "model": e.model,
        "prompt_version": e.prompt_version,
        "rule_versions": e.rule_versions,
        "input_hash": e.input_hash,
        "output_hash": e.output_hash,
        "detail": e.detail,
    }


def append(
    session: Session,
    *,
    actor: str,
    action: str,
    case_id: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    rule_versions: list[str] | None = None,
    input: Any = None,
    output: Any = None,
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    """Record one step. Pass `input`/`output` to store their hashes, never their content."""
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    prev = session.scalar(select(AuditEvent.hash).order_by(AuditEvent.id.desc()).limit(1))
    event = AuditEvent(
        ts=datetime.now(UTC),
        case_id=case_id,
        actor=actor,
        action=action,
        model=model,
        prompt_version=prompt_version,
        rule_versions=rule_versions,
        input_hash=content_hash(input) if input is not None else None,
        output_hash=content_hash(output) if output is not None else None,
        detail=detail or {},
        prev_hash=prev or GENESIS,
    )
    event.hash = _event_hash(event.prev_hash, _hashed_fields(event))
    session.add(event)
    session.flush()
    return event


@dataclass
class ChainCheck:
    ok: bool
    events: int
    first_bad_id: int | None = None


def verify_chain(session: Session) -> ChainCheck:
    """Recompute every hash in order; the first mismatch is where history was altered."""
    prev = GENESIS
    count = 0
    # populate_existing: check what is in the database, not objects cached in this session
    q = select(AuditEvent).order_by(AuditEvent.id).execution_options(populate_existing=True)
    for e in session.scalars(q):
        count += 1
        if e.prev_hash != prev or e.hash != _event_hash(prev, _hashed_fields(e)):
            return ChainCheck(False, count, e.id)
        prev = e.hash
    return ChainCheck(True, count)


def events_for_case(session: Session, case_id: str) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.id)
        )
    )
