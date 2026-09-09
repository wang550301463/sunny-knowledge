"""Real PostgreSQL and signed HTTP lifecycle authorization/idempotency boundaries."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from .test_http import client_for
from .test_input_lineage import derived, private_wiki
from .test_postgres import content, publish

pytestmark = pytest.mark.asyncio
PAGE = "/api/v1/pages/page:payments"


async def table_count(store, table):
    async with store[2]() as session:
        return await session.scalar(text("SELECT count(*) FROM " + table))


async def test_metrics_read_does_not_record_access_or_change_canonical(store):
    revision, _ = await publish(store)
    async with client_for(store) as (client, headers):
        for _ in range(2):
            response = await client.get(PAGE + "/lifecycle", headers=headers())
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["revision_id"] == revision["id"]
            assert result["support"]["registered_source_count"] == 1
            assert result["access"]["mine_visits_30d"] == 0
            assert result["access"]["mine_last_accessed_at"] is None
        assert await table_count(store, "knowledge_access_events") == 0
        assert await table_count(store, "knowledge_revisions") == 1


async def test_personal_access_is_idempotent_and_separate_from_support_and_read_scope(store):
    revision, _ = await publish(store)
    store[1].denied.add(("alice", "write", "page:payments"))
    body = {"revision_id": revision["id"], "idempotency_key": "open-1"}
    async with client_for(store) as (client, headers):
        before = (await client.get(PAGE + "/lifecycle", headers=headers())).json()
        first = await client.post(PAGE + "/access-events", headers=headers(), json=body)
        assert first.status_code == 201, first.text
        repeated = await client.post(PAGE + "/access-events", headers=headers(), json=body)
        assert repeated.json() == first.json()
        after = (await client.get(PAGE + "/lifecycle", headers=headers())).json()
        other = (await client.get(PAGE + "/lifecycle", headers=headers(actor="bob"))).json()
        assert after["access"]["mine_visits_7d"] == 1
        assert after["access"]["mine_visits_30d"] == 1
        assert other["access"]["mine_visits_30d"] == 0
        assert before["support"] == after["support"]
        assert before["freshness"]["observed_at"] == after["freshness"]["observed_at"]
        assert await table_count(store, "knowledge_access_events") == 1


async def test_access_key_cannot_be_reused_for_another_revision(store):
    revision, snapshot = await publish(store)
    second = await derived(store, "page:second", [], body=content(snapshot))
    async with client_for(store) as (client, headers):
        body = {"revision_id": revision["id"], "idempotency_key": "same"}
        assert (await client.post(PAGE + "/access-events", headers=headers(), json=body)).status_code == 201
        response = await client.post(
            "/api/v1/pages/page:second/access-events", headers=headers(),
            json={**body, "revision_id": second["id"]},
        )
        assert response.status_code == 409
        assert await table_count(store, "knowledge_access_events") == 1


@pytest.mark.parametrize("caller", ["agent", "ingest", "retrieval", "graphiti", "mcp", "knowledge"])
async def test_only_web_gateway_can_submit_personal_events(store, caller):
    revision, _ = await publish(store)
    async with client_for(store) as (client, headers):
        response = await client.post(PAGE + "/access-events", headers=headers(caller),
                                    json={"revision_id": revision["id"], "idempotency_key": "x"})
        assert response.status_code == 403
        assert await table_count(store, "knowledge_access_events") == 0


@pytest.mark.parametrize("identity", ["machine", "channel"])
async def test_service_account_or_channel_delegation_cannot_record_via_gateway(store, identity):
    revision, _ = await publish(store)
    auth = store[1]

    async def resolved(token):
        return SimpleNamespace(id="alice", auth_epoch=auth.epoch, permissions=[],
                               subjects=[("service:" if identity == "machine" else "user:") + "alice"],
                               channel_context={} if identity == "channel" else None)
    auth.resolve = resolved
    async with client_for(store) as (client, headers):
        response = await client.post(PAGE + "/access-events", headers=headers(),
                                    json={"revision_id": revision["id"], "idempotency_key": "x"})
        assert response.status_code == 403
        assert await table_count(store, "knowledge_access_events") == 0


async def test_access_does_not_accept_forged_actor_or_client_timestamp(store):
    revision, _ = await publish(store)
    async with client_for(store) as (client, headers):
        response = await client.post(PAGE + "/access-events", headers=headers(), json={
            "revision_id": revision["id"], "idempotency_key": "x", "actor_id": "bob",
            "created_at": "2020-01-01T00:00:00Z",
        })
        assert response.status_code == 422
        assert await table_count(store, "knowledge_access_events") == 0


async def test_old_acl_domain_events_never_enter_new_domain_counts(store):
    revision, _ = await publish(store)
    async with client_for(store) as (client, headers):
        body = {"revision_id": revision["id"], "idempotency_key": "old-domain"}
        assert (await client.post(PAGE + "/access-events", headers=headers(), json=body)).status_code == 201
        store[1].epoch += 1
        store[1].policies["source:payments"] = {
            "space_id": "finance", "resource_id": "source:payments",
            "space_read_subjects": ["user:alice"], "resource_read_subjects": ["user:alice"],
            "acl_version": 2, "auth_epoch": store[1].epoch,
        }
        value = (await client.get(PAGE + "/lifecycle", headers=headers())).json()
        assert value["access"]["mine_visits_30d"] == 0
        repeated = await client.post(PAGE + "/access-events", headers=headers(), json=body)
        assert repeated.status_code == 201
        assert await table_count(store, "knowledge_access_events") == 1


@pytest.mark.parametrize("deny_input", [True, False])
async def test_all_input_wiki_and_source_acl_rechecked_before_metrics_access_or_replay(store, deny_input):
    _, snapshot = await publish(store)
    private = await private_wiki(store, source_snapshot=snapshot)
    revision = await derived(store, "page:derived", [private], body=content(snapshot))
    path = "/api/v1/pages/page:derived"
    async with client_for(store) as (client, headers):
        body = {"revision_id": revision["id"], "idempotency_key": "view"}
        assert (await client.post(path + "/access-events", headers=headers(), json=body)).status_code == 201
        store[1].denied.add(("alice", "read", private["page_id"] if deny_input else "source:payments"))
        store[1].epoch += 1
        for method, tail, kwargs in [("GET", "/lifecycle", {}), ("POST", "/access-events", {"json": body})]:
            response = await client.request(method, path + tail, headers=headers(), **kwargs)
            assert response.status_code == 403
            assert "mine_visits" not in response.text
        assert await table_count(store, "knowledge_access_events") == 1


async def test_access_rows_are_append_only(store):
    revision, _ = await publish(store)
    async with client_for(store) as (client, headers):
        response = await client.post(PAGE + "/access-events", headers=headers(),
                                    json={"revision_id": revision["id"], "idempotency_key": "x"})
        assert response.status_code == 201
    for operation in ["UPDATE knowledge_access_events SET actor_id='bob'", "DELETE FROM knowledge_access_events"]:
        with pytest.raises(Exception, match="immutable"):
            async with store[2]() as session, session.begin():
                await session.execute(text(operation))


async def worker_identity(store, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_LIFECYCLE_PRINCIPAL_ID", "life-worker")
    auth = store[1]
    original = auth.resolve
    async def resolved(token):
        if token in {"life-worker", "other-worker"}:
            return SimpleNamespace(id=token, subjects=["service:" + token], permissions=[], auth_epoch=auth.epoch)
        return await original(token)
    auth.resolve = resolved


async def test_daily_materialization_is_idempotent_and_monotonic(store, monkeypatch):
    revision, _ = await publish(store)
    await worker_identity(store, monkeypatch)
    import knowledge_platform.knowledge.lifecycle_service as lifecycle
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(lifecycle, "now", lambda: today + timedelta(days=4))
    path = "/internal/v1/lifecycle/reconcile"
    async with client_for(store) as (client, headers):
        body = {"space_id": "finance", "scheduled_at": (today + timedelta(days=2)).isoformat(), "idempotency_key": "day-2"}
        first = await client.post(path, headers=headers("knowledge", "life-worker"), json=body)
        assert first.status_code == 200, first.text
        repeated = await client.post(path, headers=headers("knowledge", "life-worker"), json=body)
        assert repeated.json() == first.json()
        assert first.json()["items"][0]["revision_id"] == revision["id"]
        older = await client.post(path, headers=headers("knowledge", "life-worker"), json={**body,
            "scheduled_at": (today + timedelta(days=1)).isoformat(), "idempotency_key": "day-1"})
        assert older.status_code == 200
        assert await table_count(store, "knowledge_lifecycle_snapshots") == 2
        async with store[2]() as session:
            head = (await session.execute(text("SELECT snapshot_id FROM knowledge_lifecycle_heads"))).scalar_one()
        assert head == first.json()["items"][0]["snapshot_id"]
        store[1].denied.add(("life-worker", "read", "source:payments"))
        store[1].epoch += 1
        denied = await client.post(path, headers=headers("knowledge", "life-worker"), json=body)
        assert denied.status_code == 403
        assert "snapshot_id" not in denied.text


@pytest.mark.parametrize(("caller", "actor"), [("gateway", "life-worker"), ("ingest", "life-worker"), ("knowledge", "alice"), ("knowledge", "other-worker")])
async def test_reconcile_is_pinned_to_exact_worker_and_service_identity(store, monkeypatch, caller, actor):
    await publish(store)
    await worker_identity(store, monkeypatch)
    async with client_for(store) as (client, headers):
        response = await client.post("/internal/v1/lifecycle/reconcile", headers=headers(caller, actor), json={
            "space_id": "finance", "scheduled_at": "2020-01-01T00:00:00Z", "idempotency_key": "x"})
        assert response.status_code == 403
        assert await table_count(store, "knowledge_lifecycle_snapshots") == 0

