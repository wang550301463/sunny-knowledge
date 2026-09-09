"""Temporal histories contain task IDs and booleans; all source data stays in PG/S3."""
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, WorkflowAlreadyStartedError

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import select, update
    from .models import Task


@workflow.defn
class IngestWorkflow:
    @workflow.run
    async def run(self, task_id: str):
        try:
            for _ in range(50):
                done = await workflow.execute_activity('ingest.advance', task_id,
                    start_to_close_timeout=timedelta(minutes=5),
                    schedule_to_close_timeout=timedelta(minutes=30),
                    retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2),
                                             maximum_interval=timedelta(seconds=60)))
                if done: return
            workflow.continue_as_new(task_id)
        except ActivityError:
            await workflow.execute_activity('ingest.fail', task_id,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2),
                                         maximum_interval=timedelta(seconds=60)))


class TemporalDispatcher:
    def __init__(self, database, client, task_queue):
        self.database, self.client, self.task_queue = database, client, task_queue

    async def start(self, task_id):
        try:
            await self.client.start_workflow(IngestWorkflow.run, task_id,
                id='ingest:' + task_id, task_queue=self.task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
        except WorkflowAlreadyStartedError:
            pass

    async def dispatch(self):
        async with self.database.session() as session:
            ids = (await session.scalars(select(Task.id).where(Task.status.in_(['queued', 'running']), Task.workflow_started.is_(False)).order_by(Task.created_at).limit(50))).all()
        for task_id in ids:
            await self.start(task_id)
            # Start → checkpoint crash is safe: deterministic Temporal ID rejects duplicate start.
            async with self.database.session() as session, session.begin():
                await session.execute(update(Task).where(Task.id == task_id).values(workflow_started=True))
        return len(ids)