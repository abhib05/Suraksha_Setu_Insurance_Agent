"""Shared workflow plumbing: every step retries with backoff, and after 3 failures the case
gets a human task instead of failing; the step runs again once the task is resolved."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from suraksha.activities.case import CaseActivities

RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)
STEP_TIMEOUT = timedelta(seconds=60)


@dataclass
class FinalError:
    """A step failed in a way retrying cannot fix (e.g. the claim desk rejected the filing);
    the workflow decides what happens next instead of opening a step_failed task."""

    message: str


class StepRunner:
    case_id: str
    claim_id: str | None = None

    def __init__(self) -> None:
        self.resolved_tasks: set[str] = set()
        self.open_task: str | None = None

    @workflow.signal
    def resolve_task(self, task_id: str) -> None:
        self.resolved_tasks.add(task_id)

    async def step(self, fn: Any, *args: Any, final_error: str | None = None) -> Any:
        while True:
            try:
                return await workflow.execute_activity_method(
                    fn, args=list(args), start_to_close_timeout=STEP_TIMEOUT, retry_policy=RETRY
                )
            except ActivityError as err:
                if isinstance(err.cause, ApplicationError) and err.cause.type == final_error:
                    return FinalError(err.cause.message)
                name = getattr(fn, "__name__", str(fn))
                cause = err.cause.message if err.cause else str(err)
                await self.human_task(
                    "step_failed", f"{name} failed: {cause}", assignee="reviewer"
                )

    async def human_task(self, kind: str, detail: str, *, assignee: str, wait: bool = True) -> str:
        """Create a task and (by default) wait until someone resolves it. Family tasks pass
        wait=False because the data they ask for arrives as its own signal."""
        task_id = await workflow.execute_activity_method(
            CaseActivities.create_task,
            args=[self.case_id, self.claim_id, kind, detail, assignee],
            start_to_close_timeout=STEP_TIMEOUT, retry_policy=RetryPolicy(),
        )  # fmt: skip
        self.open_task = task_id
        if not wait:
            return task_id
        await workflow.wait_condition(lambda: task_id in self.resolved_tasks)
        self.open_task = None
        return task_id

    async def event(self, action: str, **detail: Any) -> None:
        await self.step(CaseActivities.record_event, self.case_id, action, detail)
