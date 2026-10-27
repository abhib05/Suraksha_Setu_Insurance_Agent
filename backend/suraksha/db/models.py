"""Relational schema (R0 checklist M1). Personal identifiers live only in vault.secret;
every other table holds vault tokens. Enum-like columns are plain strings validated by the
contracts, so adding a value never needs a migration."""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from suraksha.db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class Created:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Case(Created, Base):
    __tablename__ = "case"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="open")
    channel: Mapped[str] = mapped_column(String(32), default="web_chat")
    workflow_id: Mapped[str | None] = mapped_column(String(80))


class Deceased(Base):
    __tablename__ = "deceased"
    case_id: Mapped[str] = mapped_column(ForeignKey("case.id"), primary_key=True)
    name_token: Mapped[str] = mapped_column(String(64))
    date_of_death: Mapped[date | None] = mapped_column(Date)
    place_of_death: Mapped[str | None] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(16), default="unknown")
    age_at_death: Mapped[int | None] = mapped_column(Integer)


class Claimant(Created, Base):
    __tablename__ = "claimant"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.id"), index=True)
    name_token: Mapped[str] = mapped_column(String(64))
    relationship: Mapped[str] = mapped_column(String(16))
    is_nominee: Mapped[bool | None] = mapped_column(Boolean)
    phone_token: Mapped[str | None] = mapped_column(String(64))
    bank_account_token: Mapped[str | None] = mapped_column(String(64))


class Asset(Created, Base):
    __tablename__ = "asset"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.id"), index=True)
    type: Mapped[str] = mapped_column(String(24))
    institution: Mapped[str | None] = mapped_column(String(120))
    identifier_token: Mapped[str | None] = mapped_column(String(64))
    card_variant: Mapped[str | None] = mapped_column(String(32))
    last_txn_date: Mapped[date | None] = mapped_column(Date)
    card_issue_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(64))


class Document(Created, Base):
    __tablename__ = "document"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    original_path: Mapped[str] = mapped_column(String(400))  # Fernet-encrypted at rest
    masked_path: Mapped[str | None] = mapped_column(String(400))  # the only copy agents see
    # masked | none_found | unsupported: agents may read only masked / none_found copies
    masking_status: Mapped[str] = mapped_column(String(16))
    aadhaar_masked: Mapped[int] = mapped_column(Integer, default=0)
    doc_type: Mapped[str | None] = mapped_column(String(32))
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # ExtractedDocument


class Entitlement(Created, Base):
    __tablename__ = "entitlement"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.id"), index=True)
    scheme_id: Mapped[str] = mapped_column(String(32))
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("asset.id"))
    strength: Mapped[str] = mapped_column(String(16))
    expected_payout_inr: Mapped[int | None] = mapped_column(Integer)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    rule_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # latest RuleResult


class Claim(Created, Base):
    __tablename__ = "claim"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.id"), index=True)
    entitlement_id: Mapped[str] = mapped_column(ForeignKey("entitlement.id"))
    scheme_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="cover_found")
    claim_number: Mapped[str | None] = mapped_column(String(64))
    workflow_id: Mapped[str | None] = mapped_column(String(80))


class Packet(Created, Base):
    __tablename__ = "packet"
    __table_args__ = (UniqueConstraint("claim_id", "version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claim.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)  # ClaimPacket
    verdict: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # VerifierVerdict


class Task(Created, Base):
    __tablename__ = "task"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.id"), index=True)
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("claim.id"))
    kind: Mapped[str] = mapped_column(String(32))  # missing_document, low_confidence, ...
    status: Mapped[str] = mapped_column(String(16), default="open")
    assignee: Mapped[str] = mapped_column(String(16), default="family")  # family | reviewer
    detail: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Consent(Base):
    __tablename__ = "consent"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    scope: Mapped[list[str]] = mapped_column(JSONB)
    given_by: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(16))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Approval(Base):
    __tablename__ = "approval"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("case.id"), index=True)
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("claim.id"))
    gate: Mapped[str] = mapped_column(String(32))
    approver: Mapped[str] = mapped_column(String(64))
    packet_version: Mapped[int | None] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Append-only and hash-chained. A database trigger rejects UPDATE, DELETE and TRUNCATE."""

    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    case_id: Mapped[str | None] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(64))  # agent:<name> | service:<name> | user:<id>
    action: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    rule_versions: Mapped[list[str] | None] = mapped_column(JSONB)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # never raw PII
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class VaultSecret(Base):
    __tablename__ = "secret"
    __table_args__ = {"schema": "vault"}
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))  # name | phone | account | card | aadhaar
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
