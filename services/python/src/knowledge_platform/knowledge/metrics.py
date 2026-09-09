"""Canonical, committed projection backlog; never inspect event payloads."""

from sqlalchemy import and_, func, select
from sqlalchemy.orm import aliased

from knowledge_platform.common.domain_metrics import QueueSnapshot

from .models import Delivery, Outbox

OUTBOX_QUEUES = ("outbox_retrieval", "outbox_graphiti")


async def outbox_snapshot(database):
    retrieval, graphiti = aliased(Delivery), aliased(Delivery)
    # One statement gives both consumers one PostgreSQL snapshot. Include active
    # leases, retries and missing delivery rows; only a committed ACK drains work.
    statement = select(
        func.count(Outbox.id).filter(retrieval.acked_at.is_(None)),
        func.min(Outbox.created_at).filter(retrieval.acked_at.is_(None)),
        func.count(Outbox.id).filter(graphiti.acked_at.is_(None)),
        func.min(Outbox.created_at).filter(graphiti.acked_at.is_(None)),
    ).select_from(Outbox)
    for delivery, consumer in ((retrieval, "retrieval"), (graphiti, "graphiti")):
        statement = statement.outerjoin(
            delivery, and_(delivery.event_id == Outbox.id, delivery.consumer == consumer)
        )
    async with database.session() as session:
        retrieval_count, retrieval_oldest, graphiti_count, graphiti_oldest = (
            await session.execute(statement)
        ).one()
    return {
        "outbox_retrieval": QueueSnapshot(retrieval_count, retrieval_oldest),
        "outbox_graphiti": QueueSnapshot(graphiti_count, graphiti_oldest),
    }
