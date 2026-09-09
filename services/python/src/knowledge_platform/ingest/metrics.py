"""Aggregate only ingest-owned committed task state; no source/task labels."""
from sqlalchemy import func, select

from knowledge_platform.common.domain_metrics import QueueSnapshot
from .models import Task

TASK_QUEUES=('tasks_queued','tasks_running','tasks_failed','tasks_review_needed')


async def task_snapshot(database):
    async with database.session() as session:
        rows=(await session.execute(select(Task.status,func.count(),func.min(Task.created_at)).where(Task.status.in_(['queued','running','failed','review_needed'])).group_by(Task.status))).all()
    values={queue:QueueSnapshot(0,None) for queue in TASK_QUEUES}
    for status,count,oldest in rows:
        values['tasks_'+status]=QueueSnapshot(count,oldest)
    return values