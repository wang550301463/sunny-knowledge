"""PostgreSQL transaction fencing of timed-out, still-running ingest requests."""

import asyncio

import pytest
from sqlalchemy import func, select

from .test_http import client_for
from .test_postgres import content, source

pytestmark = pytest.mark.asyncio


def fence_body(generation=1, source_id="git:payments", resource_id="source:payments"):
    return {
        "source_id": source_id,
        "space_id": "finance",
        "resource_id": resource_id,
        "generation": generation,
    }


def fence_headers(headers, generation=1, source_id="git:payments", caller="ingest"):
    return {
        **headers(caller),
        "X-Ingest-Source-Id": source_id,
        "X-Ingest-Generation": str(generation),
    }


def page_body(snapshot, page_id="page:fenced"):
    return {
        "id": page_id,
        "space_id": "finance",
        "content": content(snapshot).model_dump(mode="json"),
        "idempotency_key": page_id,
    }


async def counts(store):
    from knowledge_platform.knowledge.models import Audit, Operation, Outbox, Page, Proposal, Revision

    async with store[2]() as session:
        return tuple(
            [await session.scalar(select(func.count()).select_from(model))
             for model in (Page, Proposal, Revision, Operation, Audit, Outbox)]
        )


async def test_advance_is_monotonic_identity_bound_and_live_authorized(store):
    async with client_for(store) as (client, headers):
        path = "/internal/v1/source-fences/advance"
        assert (await client.post(path, headers=headers(), json=fence_body())).status_code == 403
        assert (await client.post(path, headers=headers("ingest", None), json=fence_body())).status_code == 401
        first = await client.post(path, headers=headers("ingest"), json=fence_body())
        assert first.status_code == 200, first.text
        assert first.json()["generation"] == 1
        initial = await counts(store)
        retry = await client.post(path, headers=headers("ingest"), json=fence_body())
        assert retry.status_code == 200
        assert await counts(store) == initial
        advance = await client.post(path, headers=headers("ingest"), json=fence_body(2))
        assert advance.status_code == 200
        old = await client.post(path, headers=headers("ingest"), json=fence_body())
        assert old.status_code == 409
        assert old.json()["error"]["code"] == "source_generation_conflict"
        wrong = await client.post(path, headers=headers("ingest"), json=fence_body(3, resource_id="source:other"))
        assert wrong.status_code == 409
        store[1].denied.add(("alice", "write", "source:payments"))
        assert (await client.post(path, headers=headers("ingest"), json=fence_body(3))).status_code == 403


async def test_content_missing_forged_mismatched_or_old_fence_has_no_canonical_residuals(store):
    _, snapshot = await source(store)
    async with client_for(store) as (client, headers):
        initial = await counts(store)
        missing = await client.post("/api/v1/pages", headers=headers("ingest"), json=page_body(snapshot))
        assert missing.status_code == 422
        forged = await client.post("/api/v1/pages", headers=fence_headers(headers, caller="gateway"), json=page_body(snapshot))
        assert forged.status_code == 403
        unknown = await client.post("/api/v1/pages", headers=fence_headers(headers), json=page_body(snapshot))
        assert unknown.status_code == 409
        assert await counts(store) == initial
        assert (await client.post("/internal/v1/source-fences/advance", headers=headers("ingest"), json=fence_body(2))).status_code == 200
        initial = await counts(store)
        late = await client.post("/api/v1/pages", headers=fence_headers(headers), json=page_body(snapshot))
        assert late.status_code == 409
        assert late.json()["error"]["code"] == "source_generation_conflict"
        mismatch = await client.post("/api/v1/pages", headers=fence_headers(headers, 2), json={**page_body(snapshot), "space_id": "other"})
        assert mismatch.status_code == 409
        assert await counts(store) == initial
        valid = await client.post("/api/v1/pages", headers=fence_headers(headers, 2), json=page_body(snapshot))
        assert valid.status_code == 201, valid.text
        assert (await client.post("/internal/v1/source-fences/advance", headers=headers("ingest"), json=fence_body(3))).status_code == 200
        initial = await counts(store)
        cached_old = await client.post("/api/v1/pages", headers=fence_headers(headers, 2), json=page_body(snapshot))
        assert cached_old.status_code == 409
        assert await counts(store) == initial


async def test_advance_waits_for_old_write_commit_and_fences_late_old_requests(store):
    from knowledge_platform.knowledge.schemas import PageCreate, SourceFenceAdvance, SourceGeneration

    _, snapshot = await source(store)
    async with store[0]() as knowledge:
        await knowledge.advance_source_fence("alice", "ingest", SourceFenceAdvance(**fence_body()))
    held, release = asyncio.Event(), asyncio.Event()

    async def old_write():
        async with store[0]() as knowledge:
            await knowledge.guard_source_write("alice", "ingest", SourceGeneration(source_id="git:payments", generation=1))
            await knowledge.create_page("alice", PageCreate(**page_body(snapshot)))
            held.set()
            await release.wait()

    async def advance():
        async with store[0]() as knowledge:
            return await knowledge.advance_source_fence("alice", "ingest", SourceFenceAdvance(**fence_body(2)))

    old = asyncio.create_task(old_write())
    await asyncio.wait_for(held.wait(), 2)
    newer = asyncio.create_task(advance())
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(newer), 0.1)
        # Another source's lock remains independent while this source is held.
        async with store[0]() as knowledge:
            await asyncio.wait_for(knowledge.advance_source_fence("alice", "ingest", SourceFenceAdvance(**fence_body(1, "git:other", "source:other"))), 1)
    finally:
        release.set()
        await asyncio.wait_for(old, 2)
        await asyncio.wait_for(newer, 2)
    async with client_for(store) as (client, headers):
        # Successful advancement means committed old output can be reconciled with GET.
        existing = await client.get("/api/v1/pages/page:fenced", headers=headers("ingest"))
        assert existing.status_code == 200
        initial = await counts(store)
        late = await client.post("/api/v1/pages", headers=fence_headers(headers), json=page_body(snapshot, "page:late"))
        assert late.status_code == 409
        assert (await client.get("/api/v1/pages/page:late", headers=headers("ingest"))).status_code == 404
        assert await counts(store) == initial