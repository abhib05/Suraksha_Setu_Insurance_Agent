"""case_workflow: one per death.

consent -> interview -> documents -> discovery -> eligibility -> (review if unsure) ->
briefing -> family chooses covers -> one child claim_workflow per cover -> wait for all
"""

import asyncio

from pydantic import BaseModel
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from suraksha.activities.case import CaseActivities
    from suraksha.contracts import Entitlement, RuleResult, RuleStatus, Severity, Strength
    from suraksha.workflows.base import StepRunner
    from suraksha.workflows.claim import ClaimInput, ClaimOutcome, ClaimWorkflow


class CaseInput(BaseModel):
    case_id: str
    status_poll_seconds: int = 30
    # True when the family is interviewed in chat: wait for them to press "finish"
    wait_for_intake: bool = False


class CaseState(BaseModel):
    case_id: str
    stage: str
    entitlements: list[Entitlement] = []
    rule_results: list[RuleResult] = []
    needs_review: list[str] = []  # entitlement ids waiting for the eligibility gate
    briefing: str | None = None
    preselected: list[str] = []  # eligible covers, ticked by default for the family
    claims: dict[str, str] = {}  # entitlement id -> claim id
    open_task: str | None = None


class CaseOutcome(BaseModel):
    case_id: str
    claims: list[ClaimOutcome]


@workflow.defn
class CaseWorkflow(StepRunner):
    def __init__(self) -> None:
        super().__init__()
        self.st: CaseState | None = None
        self.consented = False
        self.intake_done = False
        self.reviewed: list[str] | None = None
        self.chosen: list[str] | None = None

    @workflow.signal
    def consent_given(self) -> None:
        self.consented = True

    @workflow.signal
    def intake_completed(self) -> None:
        self.intake_done = True

    @workflow.signal
    def eligibility_reviewed(self, approved_entitlement_ids: list[str]) -> None:
        self.reviewed = approved_entitlement_ids

    @workflow.signal
    def choose_covers(self, entitlement_ids: list[str]) -> None:
        self.chosen = entitlement_ids

    @workflow.query
    def state(self) -> CaseState:
        assert self.st is not None
        return self.st.model_copy(update={"open_task": self.open_task})

    async def _stage(self, stage: str) -> None:
        self.st.stage = stage
        await self.event("case.stage", stage=stage)

    @workflow.run
    async def run(self, inp: CaseInput) -> CaseOutcome:
        self.case_id = inp.case_id
        self.st = CaseState(case_id=inp.case_id, stage="awaiting_consent")
        await workflow.wait_condition(lambda: self.consented)

        await self._stage("interview")
        if inp.wait_for_intake:
            await workflow.wait_condition(lambda: self.intake_done)
        facts = await self.step(CaseActivities.interview, self.case_id)
        await self._stage("documents")
        docs = await self.step(CaseActivities.extract_documents, self.case_id)

        await self._stage("discovery")
        ents: list[Entitlement] = await self.step(CaseActivities.discover, facts, docs)
        results = [await self.step(CaseActivities.check_eligibility, e, facts, docs, {})
                   for e in ents]  # fmt: skip
        verdict = await self.step(CaseActivities.verify_entitlements, self.case_id, ents, results)
        self.st.entitlements, self.st.rule_results = ents, results

        # Eligibility gate only for unsure results: weak evidence or a verifier concern.
        flagged = {f.target.removeprefix("entitlement:") for f in verdict.findings
                   if f.severity is not Severity.INFO}  # fmt: skip
        self.st.needs_review = [
            e.entitlement_id
            for e in ents
            if e.strength is Strength.POSSIBLE or e.entitlement_id in flagged
        ]
        if self.st.needs_review:
            await self._stage("awaiting_eligibility_review")
            await workflow.wait_condition(lambda: self.reviewed is not None)
            rejected = set(self.st.needs_review) - set(self.reviewed)
            keep = [i for i, e in enumerate(ents) if e.entitlement_id not in rejected]
            ents, results = [ents[i] for i in keep], [results[i] for i in keep]
            self.st.entitlements, self.st.rule_results = ents, results

        await self._stage("briefing")
        self.st.briefing = await self.step(CaseActivities.brief_family, self.case_id, ents,
                                           results)  # fmt: skip
        pursuable = {r.entitlement_id for r in results if r.status is not RuleStatus.NOT_ELIGIBLE}
        self.st.preselected = [r.entitlement_id for r in results
                               if r.status is RuleStatus.ELIGIBLE]  # fmt: skip
        await self._stage("awaiting_choice")
        await workflow.wait_condition(lambda: self.chosen is not None)

        by_id = {r.entitlement_id: r for r in results}
        handles = []
        for ent in ents:
            if ent.entitlement_id not in self.chosen or ent.entitlement_id not in pursuable:
                continue
            claim_id = f"clm_{str(workflow.uuid4()).replace('-', '')[:16]}"
            self.st.claims[ent.entitlement_id] = claim_id
            handles.append(await workflow.start_child_workflow(
                ClaimWorkflow.run,
                ClaimInput(case_id=self.case_id, claim_id=claim_id, entitlement=ent,
                           rule_result=by_id[ent.entitlement_id], case_facts=facts,
                           documents=docs,
                           status_poll_seconds=inp.status_poll_seconds),
                id=f"claim-{claim_id}",
            ))  # fmt: skip
        await self._stage("claims_in_progress")  # only once every claim workflow exists
        outcomes = list(await asyncio.gather(*handles))
        await self._stage("closed")
        return CaseOutcome(case_id=self.case_id, claims=outcomes)
