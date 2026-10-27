"""M2 done-when: case A runs end to end on stubs and pauses at the approval gate until a
signal arrives. Plus every branch of the claim state machine.

Uses Temporal's time-skipping test server (downloaded once by the SDK), no Docker needed.
"""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from temporalio.client import Client, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from suraksha.activities.case import CaseActivities
from suraksha.activities.store import MemoryStore
from suraksha.agents.stubs import Scenario, StubAgents
from suraksha.contracts import Approval, Decision, DocType, Gate, RuleStatus, SchemeId
from suraksha.workflows.case import CaseInput, CaseWorkflow
from suraksha.workflows.claim import ClaimWorkflow

QUEUE = "test"


@pytest.fixture(scope="module")
async def env():
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as e:
        yield e


class Harness:
    def __init__(self, env: WorkflowEnvironment, scenario: Scenario, **case_input):
        self.env, self.client = env, env.client
        self.store = MemoryStore()
        self.acts = CaseActivities(StubAgents(scenario), self.store)
        self.case_id = f"case_{uuid.uuid4().hex[:8]}"
        self.case_input = case_input

    async def __aenter__(self):
        self.worker = Worker(
            self.client, task_queue=QUEUE, workflows=[CaseWorkflow, ClaimWorkflow],
            activities=self.acts.all(), activity_executor=ThreadPoolExecutor(8),
        )  # fmt: skip
        self._run = asyncio.create_task(self.worker.run())
        self.case: WorkflowHandle = await self.client.start_workflow(
            CaseWorkflow.run, CaseInput(case_id=self.case_id, **self.case_input),
            id=f"case-{self.case_id}",
            task_queue=QUEUE,
        )  # fmt: skip
        return self

    async def __aexit__(self, *exc):
        await self.worker.shutdown()

    async def until(self, handle: WorkflowHandle, pred, timeout_s: float = 600):
        """Advance test time until the workflow's state() satisfies pred."""
        waited = 0.0
        while True:
            st = await handle.query("state")
            if pred(st):
                return st
            if waited > timeout_s:
                raise AssertionError(f"timed out; last state: {st}")
            await self.env.sleep(5)
            waited += 5

    def claim(self, claim_id: str) -> WorkflowHandle:
        return self.client.get_workflow_handle_for(ClaimWorkflow.run, f"claim-{claim_id}")

    def approval(self, claim_id: str, version: int, decision=Decision.APPROVE, comment=None):
        return Approval(case_id=self.case_id, claim_id=claim_id, gate=Gate.PACKET_APPROVAL,
                        approver="reviewer_1", packet_version=version, decision=decision,
                        comment=comment, decided_at=datetime.now(UTC))  # fmt: skip

    async def to_claims(self, choose=None) -> dict[str, str]:
        await self.case.signal(CaseWorkflow.consent_given)
        st = await self.until(self.case, lambda s: s["stage"] == "awaiting_choice")
        await self.case.signal(CaseWorkflow.choose_covers, choose or st["preselected"])
        st = await self.until(self.case, lambda s: s["stage"] == "claims_in_progress")
        return st["claims"]

    async def approve_and_pay(self, claim_id: str, version: int = 1, amount: int = 200_000):
        h = self.claim(claim_id)
        await h.signal(ClaimWorkflow.approve_packet, self.approval(claim_id, version))
        await self.until(h, lambda s: s["stage"] == "awaiting_payout_confirmation")
        await h.signal(ClaimWorkflow.confirm_payout, amount)


def stage(name):
    return lambda s: s["stage"] == name


async def test_case_a_end_to_end_pauses_at_approval_gate(env):
    async with Harness(env, Scenario()) as h:
        assert (await h.case.query("state"))["stage"] == "awaiting_consent"
        claims = await h.to_claims()
        assert len(claims) == 3

        for claim_id in claims.values():
            await h.until(h.claim(claim_id), stage("awaiting_approval"))
        # A long wait changes nothing: the gate holds until a human signals.
        await env.sleep(3 * 24 * 3600)
        for claim_id in claims.values():
            st = await h.claim(claim_id).query("state")
            assert st["stage"] == "awaiting_approval" and st["verdict"]["passed"]
            assert h.store.claims[claim_id]["status"] == "awaiting_approval"

        for claim_id in claims.values():
            await h.approve_and_pay(claim_id)
        outcome = await h.case.result()
        assert sorted(c.scheme_id for c in outcome.claims) == ["pmjjby", "pmsby", "rupay"]
        assert {c.status for c in outcome.claims} == {"closed_paid"}
        assert all(c.claim_number.startswith("MOCK-") for c in outcome.claims)

        actions = h.store.actions(h.case_id)
        for expected in ["interview.completed", "discovery.completed", "family.briefed",
                         "packet.verified", "claim.submitted"]:  # fmt: skip
            assert expected in actions


async def test_not_eligible_is_explained_and_never_filed(env):
    sc = Scenario(rule_status={SchemeId.PMSBY: RuleStatus.NOT_ELIGIBLE})
    async with Harness(env, sc) as h:
        await h.case.signal(CaseWorkflow.consent_given)
        st = await h.until(h.case, stage("awaiting_choice"))
        pmsby = next(e["entitlement_id"] for e in st["entitlements"] if e["scheme_id"] == "pmsby")
        assert pmsby not in st["preselected"]
        assert "lapsed" in st["briefing"]
        # Even if the family ticks it, no claim is opened for a not-eligible cover.
        await h.case.signal(
            CaseWorkflow.choose_covers, [e["entitlement_id"] for e in st["entitlements"]]
        )
        st = await h.until(h.case, stage("claims_in_progress"))
        assert pmsby not in st["claims"] and len(st["claims"]) == 2


async def test_unknown_rule_waits_for_missing_facts(env):
    sc = Scenario(rule_status={SchemeId.RUPAY: RuleStatus.UNKNOWN},
                  missing_facts={SchemeId.RUPAY: ["card_last_txn_date"]})  # fmt: skip
    async with Harness(env, sc) as h:
        await h.case.signal(CaseWorkflow.consent_given)
        st = await h.until(h.case, stage("awaiting_choice"))
        rupay = next(e["entitlement_id"] for e in st["entitlements"] if e["scheme_id"] == "rupay")
        await h.case.signal(CaseWorkflow.choose_covers, [rupay])
        st = await h.until(h.case, stage("claims_in_progress"))
        claim = h.claim(st["claims"][rupay])
        st = await h.until(claim, lambda s: s["stage"] == "collecting_facts" and s["open_task"])
        assert st["open_task"] and h.store.tasks[st["open_task"]]["assignee"] == "family"
        await claim.signal(ClaimWorkflow.facts_provided, {"card_last_txn_date": "2026-09-10"})
        st = await h.until(claim, stage("awaiting_approval"))
        assert st["rule_result"]["status"] == "eligible"


async def test_missing_document_task_then_upload(env):
    sc = Scenario(missing_docs={SchemeId.PMJJBY: [DocType.DEATH_CERTIFICATE]})
    async with Harness(env, sc) as h:
        await h.case.signal(CaseWorkflow.consent_given)
        st = await h.until(h.case, stage("awaiting_choice"))
        pmjjby = next(e["entitlement_id"] for e in st["entitlements"]
                      if e["scheme_id"] == "pmjjby")  # fmt: skip
        await h.case.signal(CaseWorkflow.choose_covers, [pmjjby])
        st = await h.until(h.case, stage("claims_in_progress"))
        claim = h.claim(st["claims"][pmjjby])
        st = await h.until(claim, lambda s: s["stage"] == "collecting_documents" and s["open_task"])
        assert "death_certificate" in h.store.tasks[st["open_task"]]["detail"]
        await claim.signal(ClaimWorkflow.document_uploaded)
        await h.until(claim, stage("awaiting_approval"))


async def test_returned_packet_is_rebuilt_and_stale_approval_ignored(env):
    async with Harness(env, Scenario()) as h:
        claims = await h.to_claims()
        cid = next(iter(claims.values()))
        claim = h.claim(cid)
        await h.until(claim, stage("awaiting_approval"))
        await claim.signal(
            ClaimWorkflow.return_packet,
            h.approval(cid, 1, Decision.RETURN, "Nominee name misspelt"),
        )
        st = await h.until(claim, lambda s: s["stage"] == "awaiting_approval"
                           and s["packet_version"] == 2)  # fmt: skip
        assert "Nominee name misspelt" in st["packet"]["cover_letter"]
        # An approval for the old version must not release the new one.
        await claim.signal(ClaimWorkflow.approve_packet, h.approval(cid, 1))
        await env.sleep(60)
        assert (await claim.query("state"))["stage"] == "awaiting_approval"
        await h.approve_and_pay(cid, version=2)
        assert (await claim.result()).status == "closed_paid"


async def test_verifier_failure_creates_reviewer_task_then_rebuilds(env):
    async with Harness(env, Scenario(verifier_fails_versions={1})) as h:
        claims = await h.to_claims()
        cid = next(iter(claims.values()))
        claim = h.claim(cid)
        st = await h.until(claim, lambda s: s["open_task"] is not None)
        assert h.store.tasks[st["open_task"]]["kind"] == "verifier_failed"
        await claim.signal("resolve_task", st["open_task"])
        st = await h.until(claim, stage("awaiting_approval"))
        assert st["packet_version"] == 2 and st["verdict"]["passed"]


async def test_portal_rejection_goes_to_reviewer_then_rebuilds(env):
    async with Harness(env, Scenario(portal_rejects_versions={1})) as h:
        claims = await h.to_claims()
        cid = next(iter(claims.values()))
        claim = h.claim(cid)
        await h.until(claim, stage("awaiting_approval"))
        await claim.signal(ClaimWorkflow.approve_packet, h.approval(cid, 1))
        st = await h.until(claim, lambda s: s["open_task"] is not None)
        task = h.store.tasks[st["open_task"]]
        assert task["kind"] == "portal_rejected" and "stamp missing" in task["detail"]
        assert "claim.rejected" in h.store.actions(h.case_id)
        await claim.signal("resolve_task", st["open_task"])
        st = await h.until(claim, lambda s: s["stage"] == "awaiting_approval"
                           and s["packet_version"] == 2)  # fmt: skip
        assert "claim desk rejected v1" in st["packet"]["cover_letter"]
        await h.approve_and_pay(cid, version=2)
        assert (await claim.result()).status == "closed_paid"


async def test_flaky_step_retries_then_becomes_human_task(env):
    # interview fails 2x (retried away), discover fails 3x (exhausts retries -> task).
    async with Harness(env, Scenario(flaky={"interview": 2, "discover": 3})) as h:
        await h.case.signal(CaseWorkflow.consent_given)
        st = await h.until(h.case, lambda s: s["open_task"] is not None)
        task = h.store.tasks[st["open_task"]]
        assert task["kind"] == "step_failed" and "discover" in task["detail"]
        assert st["stage"] == "discovery"
        await h.case.signal("resolve_task", st["open_task"])
        await h.until(h.case, stage("awaiting_choice"))


async def test_weak_evidence_goes_through_eligibility_review(env):
    async with Harness(env, Scenario(weak_evidence={SchemeId.RUPAY})) as h:
        await h.case.signal(CaseWorkflow.consent_given)
        st = await h.until(h.case, lambda s: s["stage"] == "awaiting_eligibility_review"
                           and s["open_task"])  # fmt: skip
        assert len(st["needs_review"]) == 1
        task = h.store.tasks[st["open_task"]]
        assert task["kind"] == "eligibility_review" and "rupay (possible" in task["detail"]
        await h.case.signal(CaseWorkflow.eligibility_reviewed, [])  # reviewer rejects it
        st = await h.until(h.case, stage("awaiting_choice"))
        assert {e["scheme_id"] for e in st["entitlements"]} == {"pmjjby", "pmsby"}


async def test_short_payment_opens_appeal(env):
    async with Harness(env, Scenario()) as h:
        claims = await h.to_claims()
        cid = next(iter(claims.values()))
        await h.until(h.claim(cid), stage("awaiting_approval"))
        await h.approve_and_pay(cid, amount=100_000)
        st = await h.until(h.claim(cid), lambda s: s["open_task"] is not None)
        assert h.store.tasks[st["open_task"]]["kind"] == "appeal"
        await h.claim(cid).signal("resolve_task", st["open_task"])
        assert (await h.claim(cid).result()).status == "closed_short_paid"


async def test_client_connects_with_pydantic_converter(env):
    assert isinstance(env.client, Client)


async def test_chat_intake_waits_for_finish(env):
    async with Harness(env, Scenario(), wait_for_intake=True) as h:
        await h.case.signal(CaseWorkflow.consent_given)
        await h.until(h.case, stage("interview"))
        await env.sleep(3600)
        assert (await h.case.query("state"))["stage"] == "interview"
        assert "interview.completed" not in h.store.actions(h.case_id)
        await h.case.signal(CaseWorkflow.intake_completed)
        await h.until(h.case, stage("awaiting_choice"))


async def test_case_with_no_covers_closes_with_an_explanation(env):
    async with Harness(env, Scenario(fixture="case_none")) as h:
        await h.case.signal(CaseWorkflow.consent_given)
        st = await h.until(h.case, stage("awaiting_choice"))
        assert st["entitlements"] == [] and "could not find a cover" in st["briefing"]
        await h.case.signal(CaseWorkflow.choose_covers, [])
        assert (await h.case.result()).claims == []
