import asyncio
import hashlib
import json

import pytest
from sqlalchemy import text

from .test_postgres import content, publish, source


async def manifest(store, revision, paths):
    from knowledge_platform.knowledge.schemas import EvidenceRef, SourceSnapshotCreate

    raw = json.dumps({"type": "source_manifest", "source_id": "git:payments", "source_revision": revision, "paths": paths}, sort_keys=True)
    request = SourceSnapshotCreate(
        source_id="git:payments", source_revision=revision, space_id="finance",
        resource_id="source:payments", path=".__knowledge__/manifest.json", kind="source_manifest",
        text=raw, sha256=hashlib.sha256(raw.encode()).hexdigest(), object_key=f"manifests/{revision}.json",
    )
    service, _, _ = store
    async with service() as knowledge:
        snapshot = await knowledge.register_snapshot("alice", "ingest", request)
    return EvidenceRef(
        resource_id=request.resource_id, revision_id=snapshot["id"], source_id=request.source_id,
        source_revision=revision, path=request.path, kind="source_manifest", start_line=1, end_line=1,
    )


async def test_create_and_proposal_retries_are_durable_and_still_authorized(store):
    from knowledge_platform.knowledge.schemas import KnowledgeError, PageCreate, ProposalCreate

    _, snapshot = await source(store)
    service, auth, sessions = store
    request = PageCreate(space_id="finance", content=content(snapshot), idempotency_key="task:create")

    async def create():
        async with service() as knowledge:
            return await knowledge.create_page("alice", request)

    results = await asyncio.gather(create(), create())
    assert results[0]["page"]["id"] == results[1]["page"]["id"]
    assert results[0]["proposal"]["id"] == results[1]["proposal"]["id"]
    async with service() as knowledge:
        revision = await knowledge.approve("reviewer", results[0]["proposal"]["id"], "Review")
    update = ProposalCreate(base_revision=revision["id"], content=content(snapshot, "Updated"), reason="Update", idempotency_key="task:proposal")
    async with service() as knowledge:
        proposal = await knowledge.propose("alice", revision["page_id"], update)
    async with service() as knowledge:
        await knowledge.approve("reviewer", proposal["id"], "Review")
    async with service() as knowledge:
        assert (await knowledge.propose("alice", revision["page_id"], update))["id"] == proposal["id"]
    async with sessions() as session:
        assert (await session.execute(text("SELECT count(*) FROM knowledge_proposals"))).scalar_one() == 2
    changed = request.model_copy(update={"reason": "Different request"})
    with pytest.raises(KnowledgeError) as conflict:
        async with service() as knowledge:
            await knowledge.create_page("alice", changed)
    assert conflict.value.status == 409
    auth.denied.add(("alice", "read", "source:payments"))
    with pytest.raises(KnowledgeError) as denied:
        await create()
    assert denied.value.status == 403


async def test_manifest_proves_deletion_without_rewriting_text_and_retry_is_idempotent(store):
    from knowledge_platform.knowledge.schemas import KnowledgeError, SourceDeletion

    revision, _ = await publish(store)
    before = await manifest(store, "a" * 40, ["main.go"])
    after = await manifest(store, "b" * 40, [])
    request = SourceDeletion(base_revision=revision["id"], before_manifest=before, after_manifest=after, deleted_paths=["main.go"], reason="File removed at pinned commit", idempotency_key="task:delete")
    service, auth, sessions = store
    async with service() as knowledge:
        removed = await knowledge.source_deletion("alice", "ingest", revision["page_id"], request)
    assert removed["content"]["state"] == "stale"
    assert removed["content"]["claims"][0]["state"] == "stale"
    assert removed["content"]["markdown"] == revision["content"]["markdown"]
    async with service() as knowledge:
        repeated = await knowledge.source_deletion("alice", "ingest", revision["page_id"], request)
    assert repeated["id"] == removed["id"]
    async with sessions() as session:
        assert (await session.execute(text("SELECT count(*) FROM knowledge_outbox"))).scalar_one() == 2
    auth.denied.add(("alice", "read", "source:payments"))
    with pytest.raises(KnowledgeError):
        async with service() as knowledge:
            await knowledge.source_deletion("alice", "ingest", revision["page_id"], request)


@pytest.mark.parametrize("after_paths,old_revision,deleted", [(["main.go"], "a" * 40, ["main.go"]), ([], "c" * 40, ["main.go"]), ([], "a" * 40, ["unrelated.go"])])
async def test_unproven_or_obsolete_deletion_cannot_invalidate_a_page(store, after_paths, old_revision, deleted):
    from knowledge_platform.knowledge.schemas import KnowledgeError, SourceDeletion

    revision, _ = await publish(store)
    before = await manifest(store, old_revision, ["main.go"])
    after = await manifest(store, "b" * 40, after_paths)
    request = SourceDeletion(base_revision=revision["id"], before_manifest=before, after_manifest=after, deleted_paths=deleted, reason="Delete")
    service, _, _ = store
    with pytest.raises(KnowledgeError) as error:
        async with service() as knowledge:
            await knowledge.source_deletion("alice", "ingest", revision["page_id"], request)
    assert error.value.status == 422
    async with service() as knowledge:
        assert (await knowledge.get_page("alice", revision["page_id"]))["current_revision"] == revision["id"]
