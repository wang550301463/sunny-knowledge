"""Canonical queue truth across real publication, lease and ACK transactions."""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from prometheus_client import generate_latest
from sqlalchemy import delete, select, update

from knowledge_platform.common.domain_metrics import DomainMetrics, SnapshotSampler
from knowledge_platform.knowledge.models import Delivery, Outbox
from knowledge_platform.knowledge.schemas import LeaseRequest

from .test_postgres import content, draft, publish

pytestmark = pytest.mark.asyncio


def database(store):
    return SimpleNamespace(session=store[2])


async def test_canonical_backlog_follows_commit_and_ack_not_lease(store):
    from knowledge_platform.knowledge.metrics import outbox_snapshot

    created, _ = await draft(store)
    service, _, _ = store
    async with service() as knowledge:
        await knowledge.approve("reviewer", created["proposal"]["id"], "Reviewed")
        assert all(value.count == 0 for value in (await outbox_snapshot(database(store))).values())
    queues = await outbox_snapshot(database(store))
    assert {key: value.count for key, value in queues.items()} == {
        "outbox_retrieval": 1, "outbox_graphiti": 1,
    }
    async with service() as knowledge:
        event = (await knowledge.lease_outbox(
            "retrieval", LeaseRequest(consumer="retrieval", limit=1, lease_seconds=300)
        ))["items"][0]
    assert (await outbox_snapshot(database(store)))["outbox_retrieval"].count == 1
    with pytest.raises(RuntimeError):
        async with service() as knowledge:
            await knowledge.ack_outbox("retrieval", event["id"], event["lease_token"])
            raise RuntimeError("rollback")
    assert (await outbox_snapshot(database(store)))["outbox_retrieval"].count == 1
    async with service() as knowledge:
        await knowledge.ack_outbox("retrieval", event["id"], event["lease_token"])
        assert (await outbox_snapshot(database(store)))["outbox_retrieval"].count == 1
    queues = await outbox_snapshot(database(store))
    assert queues["outbox_retrieval"].count == 0 and queues["outbox_retrieval"].oldest is None
    assert queues["outbox_graphiti"].count == 1


async def test_backlog_counts_missing_deliveries_and_oldest_event_without_cross_consumer_join(store):
    from knowledge_platform.knowledge.metrics import outbox_snapshot
    from knowledge_platform.knowledge.schemas import ProposalCreate

    revision, snapshot = await publish(store)
    service, _, sessions = store
    async with service() as knowledge:
        proposal = await knowledge.propose("alice", "page:payments", ProposalCreate(
            base_revision=revision["id"], content=content(snapshot, "Next"), reason="Next"
        ))
        await knowledge.approve("reviewer", proposal["id"], "Reviewed")
    old = datetime.now(UTC) - timedelta(hours=2)
    async with sessions.begin() as session:
        events = (await session.scalars(select(Outbox).order_by(Outbox.version))).all()
        events[0].created_at = old
        await session.execute(delete(Delivery).where(
            Delivery.event_id == events[0].id, Delivery.consumer == "graphiti"
        ))
        await session.execute(update(Delivery).where(
            Delivery.event_id == events[0].id, Delivery.consumer == "retrieval"
        ).values(acked_at=datetime.now(UTC)))
        await session.execute(update(Delivery).where(
            Delivery.event_id == events[1].id, Delivery.consumer == "graphiti"
        ).values(attempts=9, lease_until=datetime.now(UTC) + timedelta(minutes=5)))
    queues = await outbox_snapshot(database(store))
    assert queues["outbox_retrieval"].count == 1
    assert queues["outbox_graphiti"].count == 2 and queues["outbox_graphiti"].oldest == old
    assert queues["outbox_retrieval"].oldest > old


async def test_sampler_marks_database_failure_unknown_without_exporting_content(store):
    from knowledge_platform.knowledge.metrics import OUTBOX_QUEUES, outbox_snapshot

    revision, _ = await publish(store)
    metrics = DomainMetrics("knowledge")
    sampler = SnapshotSampler(metrics, lambda: outbox_snapshot(database(store)), OUTBOX_QUEUES)
    assert await sampler.sample()
    async def failed():
        raise RuntimeError("private canonical database payload")
    sampler.load = failed
    assert not await sampler.sample()
    for queue in OUTBOX_QUEUES:
        labels = {"service": "knowledge", "queue": queue}
        assert metrics.registry.get_sample_value("knowledge_queue_known", labels) == 0
        assert math.isnan(metrics.registry.get_sample_value("knowledge_queue_size", labels))
    rendered = generate_latest(metrics.registry).decode()
    assert "private canonical" not in rendered and revision["id"] not in rendered


async def test_knowledge_lifespan_starts_and_joins_sampler(store, monkeypatch):
    import knowledge_platform.knowledge.app as module
    from .test_http import client_for

    started, stopped = asyncio.Event(), asyncio.Event()
    class Sampler:
        def __init__(self, metrics, load, queues):
            self.load = load
            assert set(queues) == {"outbox_retrieval", "outbox_graphiti"}
        async def run(self):
            try:
                assert all(item.count == 0 for item in (await self.load()).values())
                started.set()
                await asyncio.Future()
            finally:
                stopped.set()
    monkeypatch.setattr(module, "SnapshotSampler", Sampler, raising=False)
    async with client_for(store):
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not stopped.is_set()
    assert stopped.is_set()
