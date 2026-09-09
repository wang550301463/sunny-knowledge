"""Executable Temporal worker plus durable PostgreSQL task dispatcher."""
import asyncio
import logging

from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker
from sqlalchemy import select

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.common.db import create_database
from knowledge_platform.common.secrets import SecretBox
from .clients import InternalClient, MachineTokens
from .config import IngestSettings
from .models import initialize, Task, now
from .pipeline import Pipeline, TERMINAL
from .storage import S3Store
from .workflow import IngestWorkflow, TemporalDispatcher


class Activities:
    def __init__(self, pipeline): self.pipeline = pipeline

    @activity.defn(name='ingest.advance')
    async def advance(self, task_id: str) -> bool:
        try:
            return await self.pipeline.advance(task_id)
        except Exception:
            # No source content, SQL parameters, upstream exception text, or tokens in history.
            raise ApplicationError('Ingest dependency operation failed', type='IngestDependencyFailure') from None

    @activity.defn(name='ingest.fail')
    async def fail(self, task_id: str):
        try:
            async with self.pipeline.db.session() as session, session.begin():
                task = await session.get(Task, task_id, with_for_update=True)
                if task and task.status not in TERMINAL:
                    task.status, task.stage, task.error_code, task.updated_at = 'failed', 'failed', 'retry_exhausted', now()
        except Exception:
            raise ApplicationError('Ingest task checkpoint unavailable', type='IngestDependencyFailure') from None


async def main():
    config = IngestSettings.from_env('ingest')
    db = create_database(config.database_url)
    auth, machine, internal = HTTPAuthorizer(config), MachineTokens(config), InternalClient(config)
    await initialize(db.engine)
    pipeline = Pipeline(db, auth, machine, internal, SecretBox(config.ingest_encryption_key), S3Store.from_settings(config))
    client = await Client.connect(config.ingest_temporal_address, namespace=config.ingest_temporal_namespace)
    activities = Activities(pipeline)
    dispatcher = TemporalDispatcher(db, client, config.ingest_temporal_task_queue)
    worker = Worker(client, task_queue=config.ingest_temporal_task_queue,
                    workflows=[IngestWorkflow], activities=[activities.advance, activities.fail],
                    max_concurrent_activities=4, max_concurrent_workflow_tasks=8)
    try:
        async with worker:
            while True:
                try: await dispatcher.dispatch()
                except Exception: logging.getLogger(__name__).warning('Ingest task dispatch unavailable')
                await asyncio.sleep(2)
    finally:
        await auth.close()
        await machine.close()
        await internal.close()
        await db.close()


if __name__ == '__main__': asyncio.run(main())