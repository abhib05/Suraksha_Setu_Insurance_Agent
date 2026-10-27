"""Workflow endpoints (M2): start a case workflow, read its state, and pass human decisions
in as signals. Every human decision is also written to the approval table and audit log."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from temporalio.client import Client, WorkflowHandle
from temporalio.service import RPCError

from suraksha import audit
from suraksha.api.cases import DB, _case
from suraksha.contracts import Approval, Decision, Gate
from suraksha.db import models as m
from suraksha.db.base import new_id
from suraksha.services import consent
from suraksha.workflows.case import CaseInput, CaseWorkflow
from suraksha.workflows.claim import ClaimWorkflow

router = APIRouter(tags=["workflows"])
_client: Client | None = None


async def temporal() -> Client:
    global _client
    if _client is None:
        from suraksha.workflows.worker import connect

        try:
            _client = await connect()
        except RuntimeError as e:
            raise HTTPException(503, f"Temporal unreachable: {e}") from e
    return _client


def case_wf_id(case_id: str) -> str:
    return f"case-{case_id}"


def claim_wf_id(claim_id: str) -> str:
    return f"claim-{claim_id}"


async def _query(handle: WorkflowHandle) -> dict[str, Any]:
    try:
        return await handle.query("state")
    except RPCError as e:
        raise HTTPException(404, f"workflow not found: {e.message}") from e


async def signal_consent_if_running(case_id: str) -> None:
    """Called after consent is recorded; a case without a workflow yet is fine."""
    try:
        client = await temporal()
        await client.get_workflow_handle(case_wf_id(case_id)).signal(CaseWorkflow.consent_given)
    except (HTTPException, RPCError):
        pass


# --- case ---------------------------------------------------------------------------


class StartIn(BaseModel):
    status_poll_seconds: int = Field(default=30, ge=1)


@router.post("/cases/{case_id}/workflow", status_code=201)
async def start_case_workflow(case_id: str, session: DB, body: StartIn | None = None) -> dict:
    case = _case(session, case_id)
    client = await temporal()
    from suraksha.config import get_settings

    handle = await client.start_workflow(
        CaseWorkflow.run,
        CaseInput(case_id=case_id, status_poll_seconds=(body or StartIn()).status_poll_seconds),
        id=case_wf_id(case_id),
        task_queue=get_settings().temporal_task_queue,
    )
    case.workflow_id = handle.id
    if consent.is_active(session, case_id, consent.ConsentPurpose.PREPARE_CLAIMS):
        await handle.signal(CaseWorkflow.consent_given)
    audit.append(session, actor="user:family", action="workflow.started", case_id=case_id,
                 detail={"workflow_id": handle.id})  # fmt: skip
    return {"workflow_id": handle.id}


@router.get("/cases/{case_id}/workflow")
async def case_state(case_id: str) -> dict:
    return await _query((await temporal()).get_workflow_handle(case_wf_id(case_id)))


class ChooseIn(BaseModel):
    entitlement_ids: list[str]


@router.post("/cases/{case_id}/workflow/choose-covers")
async def choose_covers(case_id: str, body: ChooseIn, session: DB) -> dict:
    _case(session, case_id)
    await (
        (await temporal())
        .get_workflow_handle(case_wf_id(case_id))
        .signal(CaseWorkflow.choose_covers, body.entitlement_ids)
    )
    audit.append(session, actor="user:family", action="covers.chosen", case_id=case_id,
                 detail={"entitlement_ids": body.entitlement_ids})  # fmt: skip
    return {"ok": True}


class EligibilityReviewIn(BaseModel):
    approver: str
    approved_entitlement_ids: list[str]
    comment: str | None = None


@router.post("/cases/{case_id}/workflow/eligibility-review")
async def eligibility_review(case_id: str, body: EligibilityReviewIn, session: DB) -> dict:
    _case(session, case_id)
    _record_approval(session, Approval(
        case_id=case_id, gate=Gate.ELIGIBILITY_REVIEW, approver=body.approver,
        decision=Decision.APPROVE, comment=body.comment, decided_at=datetime.now(UTC),
    ), detail={"approved": body.approved_entitlement_ids})  # fmt: skip
    await (
        (await temporal())
        .get_workflow_handle(case_wf_id(case_id))
        .signal(CaseWorkflow.eligibility_reviewed, body.approved_entitlement_ids)
    )
    return {"ok": True}


# --- claims -------------------------------------------------------------------------


def _claim(session, claim_id: str) -> m.Claim:
    claim = session.get(m.Claim, claim_id)
    if claim is None:
        raise HTTPException(404, "claim not found")
    return claim


@router.get("/claims/{claim_id}")
async def claim_state(claim_id: str) -> dict:
    return await _query((await temporal()).get_workflow_handle(claim_wf_id(claim_id)))


class DecisionIn(BaseModel):
    approver: str
    packet_version: int = Field(ge=1)
    comment: str | None = None


def _record_approval(session, approval: Approval, detail: dict | None = None) -> None:
    session.add(m.Approval(
        id=new_id("apr"), case_id=approval.case_id, claim_id=approval.claim_id,
        gate=approval.gate.value, approver=approval.approver,
        packet_version=approval.packet_version, decision=approval.decision.value,
        comment=approval.comment, decided_at=approval.decided_at,
    ))  # fmt: skip
    session.flush()
    audit.append(
        session,
        actor=f"user:{approval.approver}",
        action=f"gate.{approval.gate.value}",
        case_id=approval.case_id,
        output=approval,
        detail={
            "claim_id": approval.claim_id,
            "decision": approval.decision.value,
            "packet_version": approval.packet_version,
            **(detail or {}),
        },
    )


async def _packet_decision(claim_id: str, body: DecisionIn, session, decision: Decision) -> dict:
    claim = _claim(session, claim_id)
    try:
        approval = Approval(
            case_id=claim.case_id, claim_id=claim_id, gate=Gate.PACKET_APPROVAL,
            approver=body.approver, packet_version=body.packet_version, decision=decision,
            comment=body.comment, decided_at=datetime.now(UTC),
        )  # fmt: skip
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    state = await _query((await temporal()).get_workflow_handle(claim_wf_id(claim_id)))
    if state["stage"] != "awaiting_approval" or state["packet_version"] != body.packet_version:
        raise HTTPException(409, f"claim is {state['stage']} at v{state['packet_version']}")
    _record_approval(session, approval)
    signal = (
        ClaimWorkflow.approve_packet
        if decision is Decision.APPROVE
        else ClaimWorkflow.return_packet
    )  # noqa: E501
    await (await temporal()).get_workflow_handle(claim_wf_id(claim_id)).signal(signal, approval)
    return {"ok": True}


@router.post("/claims/{claim_id}/approve")
async def approve_packet(claim_id: str, body: DecisionIn, session: DB) -> dict:
    return await _packet_decision(claim_id, body, session, Decision.APPROVE)


@router.post("/claims/{claim_id}/return")
async def return_packet(claim_id: str, body: DecisionIn, session: DB) -> dict:
    return await _packet_decision(claim_id, body, session, Decision.RETURN)


class PayoutIn(BaseModel):
    approver: str
    amount_inr: int = Field(ge=0)


@router.post("/claims/{claim_id}/confirm-payout")
async def confirm_payout(claim_id: str, body: PayoutIn, session: DB) -> dict:
    claim = _claim(session, claim_id)
    _record_approval(session, Approval(
        case_id=claim.case_id, claim_id=claim_id, gate=Gate.PAYOUT_CONFIRMATION,
        approver=body.approver, decision=Decision.APPROVE, decided_at=datetime.now(UTC),
    ), detail={"amount_inr": body.amount_inr})  # fmt: skip
    await (
        (await temporal())
        .get_workflow_handle(claim_wf_id(claim_id))
        .signal(ClaimWorkflow.confirm_payout, body.amount_inr)
    )
    return {"ok": True}


class FactsIn(BaseModel):
    facts: dict[str, str]


@router.post("/claims/{claim_id}/facts")
async def provide_facts(claim_id: str, body: FactsIn, session: DB) -> dict:
    claim = _claim(session, claim_id)
    consent.require(session, claim.case_id)
    audit.append(
        session,
        actor="user:family",
        action="facts.provided",
        case_id=claim.case_id,
        input=body.facts,
        detail={"claim_id": claim_id, "keys": sorted(body.facts)},
    )
    await (
        (await temporal())
        .get_workflow_handle(claim_wf_id(claim_id))
        .signal(ClaimWorkflow.facts_provided, body.facts)
    )
    return {"ok": True}


@router.post("/claims/{claim_id}/document-uploaded")
async def document_uploaded(claim_id: str, session: DB) -> dict:
    _claim(session, claim_id)
    await (
        (await temporal())
        .get_workflow_handle(claim_wf_id(claim_id))
        .signal(ClaimWorkflow.document_uploaded)
    )
    return {"ok": True}


# --- tasks --------------------------------------------------------------------------


class TaskOut(BaseModel):
    id: str
    case_id: str
    claim_id: str | None
    kind: str
    status: str
    assignee: str
    detail: str
    created_at: datetime


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(session: DB, status: str = "open", assignee: str | None = None) -> list[TaskOut]:
    q = select(m.Task).where(m.Task.status == status).order_by(m.Task.created_at)
    if assignee:
        q = q.where(m.Task.assignee == assignee)
    return [TaskOut.model_validate(t, from_attributes=True) for t in session.scalars(q)]


class ResolveIn(BaseModel):
    resolved_by: str
    note: str | None = None


@router.post("/tasks/{task_id}/resolve")
async def resolve_task(task_id: str, body: ResolveIn, session: DB) -> dict:
    task = session.get(m.Task, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    task.status = "done"
    audit.append(
        session,
        actor=f"user:{body.resolved_by}",
        action="task.resolved",
        case_id=task.case_id,
        detail={"task_id": task_id, "note": body.note},
    )
    wf = claim_wf_id(task.claim_id) if task.claim_id else case_wf_id(task.case_id)
    await (await temporal()).get_workflow_handle(wf).signal("resolve_task", task_id)
    return {"ok": True}
