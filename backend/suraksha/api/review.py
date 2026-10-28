"""Review console reads (M6, M7): every claim with where it stands, and one claim's packet
versions, decisions and open tasks. Decisions themselves go through the workflow endpoints
(`/claims/{id}/approve|return|confirm-payout`, `/tasks/{id}/resolve`)."""

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from suraksha.api.cases import DB
from suraksha.api.workflows import TaskOut, _claim
from suraksha.db import models as m
from suraksha.services.registry import load_scheme
from suraksha.services.rules import load_rules

router = APIRouter(prefix="/review", tags=["review"])

# Stages where the claim is waiting on a reviewer, shown first in the queue.
NEEDS_REVIEWER = ("awaiting_approval", "awaiting_payout_confirmation")


class VerdictCounts(BaseModel):
    passed: bool
    blockers: int
    warnings: int


class ClaimRow(BaseModel):
    claim_id: str
    case_id: str
    scheme_id: str
    status: str
    claim_number: str | None
    packet_version: int | None
    verdict: VerdictCounts | None
    open_tasks: int
    updated_at: datetime


class PacketVersion(BaseModel):
    version: int
    created_at: datetime
    verdict: VerdictCounts | None


class Decision(BaseModel):
    gate: str
    approver: str
    decision: str
    packet_version: int | None
    comment: str | None
    decided_at: datetime


class ClaimDetail(ClaimRow):
    expected_payout_inr: int | None
    versions: list[PacketVersion]
    decisions: list[Decision]
    tasks: list[TaskOut]


def _counts(verdict: dict | None) -> VerdictCounts | None:
    if not verdict:
        return None
    sev = [f["severity"] for f in verdict.get("findings", [])]
    return VerdictCounts(passed=verdict["passed"], blockers=sev.count("blocker"),
                         warnings=sev.count("warning"))  # fmt: skip


def _latest_packet(session, claim_id: str) -> m.Packet | None:
    q = select(m.Packet).where(m.Packet.claim_id == claim_id).order_by(m.Packet.version.desc())
    return session.scalars(q.limit(1)).first()


def _row(session, claim: m.Claim, open_tasks: int) -> dict:
    packet = _latest_packet(session, claim.id)
    return dict(
        claim_id=claim.id, case_id=claim.case_id, scheme_id=claim.scheme_id,
        status=claim.status, claim_number=claim.claim_number,
        packet_version=packet.version if packet else None,
        verdict=_counts(packet.verdict) if packet else None, open_tasks=open_tasks,
        updated_at=packet.created_at if packet else claim.created_at,
    )  # fmt: skip


@router.get("/claims", response_model=list[ClaimRow])
def list_claims(session: DB, case_id: str | None = None) -> list[ClaimRow]:
    q = select(m.Claim)
    if case_id:
        q = q.where(m.Claim.case_id == case_id)
    tasks = dict(session.execute(
        select(m.Task.claim_id, func.count()).where(m.Task.status == "open",
                                                    m.Task.claim_id.is_not(None))
        .group_by(m.Task.claim_id)
    ).all())  # fmt: skip
    rows = [ClaimRow(**_row(session, c, tasks.get(c.id, 0))) for c in session.scalars(q)]
    rows.sort(key=lambda r: r.updated_at, reverse=True)
    rows.sort(key=lambda r: r.status not in NEEDS_REVIEWER and r.open_tasks == 0)
    return rows


@router.get("/claims/{claim_id}", response_model=ClaimDetail)
def claim_detail(claim_id: str, session: DB) -> ClaimDetail:
    claim = _claim(session, claim_id)
    packets = session.scalars(
        select(m.Packet).where(m.Packet.claim_id == claim_id).order_by(m.Packet.version)
    ).all()
    decisions = session.scalars(
        select(m.Approval).where(m.Approval.claim_id == claim_id).order_by(m.Approval.decided_at)
    ).all()
    tasks = session.scalars(
        select(m.Task).where(m.Task.claim_id == claim_id).order_by(m.Task.created_at)
    ).all()
    ent = session.get(m.Entitlement, claim.entitlement_id)
    open_tasks = sum(t.status == "open" for t in tasks)
    return ClaimDetail(
        **_row(session, claim, open_tasks),
        expected_payout_inr=ent.expected_payout_inr if ent else None,
        versions=[PacketVersion(version=p.version, created_at=p.created_at,
                                verdict=_counts(p.verdict)) for p in packets],
        decisions=[Decision.model_validate(d, from_attributes=True) for d in decisions],
        tasks=[TaskOut.model_validate(t, from_attributes=True) for t in tasks],
    )  # fmt: skip


# --- rules (the text behind every cited rule id) -------------------------------------

rules_router = APIRouter(prefix="/rules", tags=["review"])


class RuleOut(BaseModel):
    id: str
    scheme: str
    title: str
    version: str
    effective_from: str
    effective_to: str | None
    requires: list[str]
    logic: Any
    explain_pass: str
    explain_fail: str
    source_id: str
    source_title: str | None
    source_url: str | None


@rules_router.get("", response_model=list[RuleOut])
def list_rules() -> list[RuleOut]:
    out = []
    for r in load_rules():
        source_id = re.match(r"[A-Z0-9-]+", r.source).group(0)  # "RUPAY-IDFC (...)"
        src = next((s for s in load_scheme(r.scheme).get("sources", [])
                    if s["id"] == source_id), {})  # fmt: skip
        out.append(RuleOut(
            id=r.id, scheme=r.scheme, title=r.title, version=r.version,
            effective_from=r.effective_from, effective_to=r.effective_to,
            requires=list(r.requires), logic=r.logic, explain_pass=r.explain_pass,
            explain_fail=r.explain_fail, source_id=r.source,
            source_title=src.get("title"), source_url=src.get("url"),
        ))  # fmt: skip
    return out
