"""Real owned PostgreSQL + signed routes compared to native service return values."""

from copy import deepcopy
from datetime import UTC, datetime
from functools import wraps

import pytest
from fastapi.encoders import jsonable_encoder

from knowledge_platform.knowledge.service import KnowledgeService

from .test_http import client_for
from .test_lifecycle_postgres import worker_identity
from .test_postgres import content, publish


@pytest.fixture
def returned(monkeypatch):
    captured = {}
    for name in (
        "projection", "projection_revisions", "lease_outbox", "ack_outbox",
        "authorize_pages", "authorize_evidence", "lifecycle_metrics", "record_access",
        "reconcile_lifecycle", "source_revision",
    ):
        original = getattr(KnowledgeService, name)

        def wrapped(name, original):
            @wraps(original)
            async def call(*args, **kwargs):
                value = await original(*args, **kwargs)
                captured[name] = jsonable_encoder(deepcopy(value))
                return value
            return call

        monkeypatch.setattr(KnowledgeService, name, wrapped(name, original))
    return captured


async def test_projection_outbox_and_original_list_preserve_all_native_fields(store, returned):
    revision, snapshot = await publish(store)
    async with client_for(store) as (client, headers):
        h = headers("retrieval", None)
        for path, method in [
            ("/internal/v1/projections/revisions", "projection_revisions"),
            (f"/internal/v1/projections/pages/page:payments/revisions/{revision['id']}", "projection"),
        ]:
            response = await client.get(path, headers=h)
            assert response.status_code == 200, response.text
            assert response.json() == returned[method]
        response = await client.post("/internal/v1/outbox/lease", headers=h, json={"consumer": "retrieval"})
        assert response.status_code == 200, response.text
        assert response.json() == returned["lease_outbox"]
        event = response.json()["items"][0]
        assert event["payload"]["revision_id"] == revision["id"]
        for _ in range(2):
            ack = await client.post(f"/internal/v1/outbox/{event['id']}/ack", headers=h, json={"lease_token": event["lease_token"]})
            assert ack.status_code == 200, ack.text
            assert ack.json() == returned["ack_outbox"] == {"acked": True}
        response = await client.get(f"/internal/v1/sources/{snapshot['source_id']}/revisions/{snapshot['source_revision']}", headers=headers("ingest"))
        assert response.status_code == 200, response.text
        assert response.json() == returned["source_revision"]
        assert "object_key" not in response.json()["items"][0]


async def test_authorization_visible_ineligible_and_denied_serializers_are_distinct(store, returned):
    revision, snapshot = await publish(store)
    ref = content(snapshot).claims[0].evidence[0].model_dump(mode="json")
    pair = {"page_id": revision["page_id"], "revision_id": revision["id"]}
    async with client_for(store) as (client, headers):
        h = headers("agent")
        for denied in (False, True):
            if denied:
                store[1].denied.add(("alice", "read", "source:payments"))
                store[1].epoch += 1
            for path, body, method in [
                ("/internal/v1/pages/authorize", {"pages": [pair]}, "authorize_pages"),
                ("/internal/v1/evidence/authorize", {"evidence": [ref]}, "authorize_evidence"),
            ]:
                response = await client.post(path, headers=h, json=body)
                assert response.status_code == 200, response.text
                assert response.json() == returned[method]
                decision = response.json()["decisions"][0]
                assert decision["allowed"] is not denied
                if denied:
                    assert set(decision) == ({"page_id", "revision_id", "allowed"} if "pages" in body else {"evidence", "allowed"})
        store[1].denied.clear()
        # An old revision remains readable with authorized=true, while ordinary
        # current-state retrieval excludes it. This is not a denied-read shape.
        from knowledge_platform.knowledge.schemas import ProposalCreate
        async with store[0]() as service:
            proposal = await service.propose("alice", pair["page_id"], ProposalCreate(base_revision=revision["id"], content=content(snapshot, title="Next")))
            await service.approve("reviewer", proposal["id"], "reviewed")
        response = await client.post("/internal/v1/pages/authorize", headers=h, json={"pages": [pair]})
        assert response.status_code == 200, response.text
        assert response.json() == returned["authorize_pages"]
        decision = response.json()["decisions"][0]
        assert decision["authorized"] is True and decision["allowed"] is False
        assert decision["space_id"] == "finance" and decision["version"] == 1


async def test_lifecycle_access_and_daily_worker_receipts_preserve_native_views(store, returned, monkeypatch):
    revision, _ = await publish(store)
    await worker_identity(store, monkeypatch)
    async with client_for(store) as (client, headers):
        path = "/api/v1/pages/page:payments"
        response = await client.get(path + "/lifecycle", headers=headers())
        assert response.status_code == 200, response.text
        assert response.json() == returned["lifecycle_metrics"]
        body = {"revision_id": revision["id"], "idempotency_key": "visible-page"}
        for _ in range(2):
            response = await client.post(path + "/access-events", headers=headers(), json=body)
            assert response.status_code == 201, response.text
            assert response.json() == returned["record_access"]
        response = await client.post("/internal/v1/lifecycle/reconcile", headers=headers("knowledge", "life-worker"), json={"space_id": "finance", "scheduled_at": datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(), "idempotency_key": "daily"})
        assert response.status_code == 200, response.text
        assert response.json() == returned["reconcile_lifecycle"]