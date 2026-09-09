"""Read aggregate reconciliation state from the caller's own catalog only."""
from datetime import UTC,datetime,timedelta
from sqlalchemy import func,select
from .domain_metrics import QueueSnapshot

PROJECTION_QUEUES=('reconcile_due','failed_revisions')


async def catalog_snapshot(database,revision_model,reconcile_seconds):
    cutoff=datetime.now(UTC)-timedelta(seconds=reconcile_seconds)
    due=revision_model.checked_at < cutoff
    failed=revision_model.failed_attempts > 0
    async with database.session() as session:
        count,oldest,failures=(await session.execute(select(func.count().filter(due),func.min(revision_model.checked_at).filter(due),func.count().filter(failed)).select_from(revision_model))).one()
    return {
        'reconcile_due':QueueSnapshot(count,oldest+timedelta(seconds=reconcile_seconds) if oldest is not None else None),
        # checked_at may be a future retry deadline, not the time the first failure occurred.
        'failed_revisions':QueueSnapshot(failures,None),
    }