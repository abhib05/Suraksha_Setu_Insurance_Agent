"""Temporal worker: `python -m suraksha.workflows.worker`. Runs both workflows and all
activities, with stub agents until M7 swaps in the real ones."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from suraksha.activities.case import CaseActivities
from suraksha.activities.store import DbStore
from suraksha.agents.stubs import StubAgents
from suraksha.config import get_settings
from suraksha.workflows.case import CaseWorkflow
from suraksha.workflows.claim import ClaimWorkflow


async def connect() -> Client:
    s = get_settings()
    return await Client.connect(
        s.temporal_address, namespace=s.temporal_namespace, data_converter=pydantic_data_converter
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await connect()
    activities = CaseActivities(StubAgents(), DbStore())
    worker = Worker(
        client,
        task_queue=get_settings().temporal_task_queue,
        workflows=[CaseWorkflow, ClaimWorkflow],
        activities=activities.all(),
        activity_executor=ThreadPoolExecutor(16),
    )
    logging.info("worker listening on %s", get_settings().temporal_task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
