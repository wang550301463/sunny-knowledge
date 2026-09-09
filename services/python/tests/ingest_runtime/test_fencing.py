"""Durable ingest generation allocation and canonical HTTP fence dispatch."""

import json

import httpx
import pytest

from knowledge_platform.ingest.clients import InternalClient
from knowledge_platform.ingest.config import IngestSettings
from knowledge_platform.ingest.models import Source, Task
from knowledge_platform.ingest.schemas import IngestError, SourceUpdate
from knowledge_platform.ingest.service import IngestService

from .test_pipeline import KnowledgeProtocol, finish, setup_pipeline
from .test_postgres import Machine


class FencedKnowledge(KnowledgeProtocol):
    def __init__(self):
        super().__init__()
        self.generations = {}
        self.dispatches = []
        self.lose_advance = False

    async def advance_fence(self, token, source_id, space_id, resource_id, generation):
        assert token == "worker"
        previous = self.generations.get(source_id, 0)
        if generation < previous:
            raise IngestError(409, "source_generation_conflict", "Old generation")
        self.generations[source_id] = generation
        if self.lose_advance:
            self.lose_advance = False
            raise IngestError(503, "dependency_unavailable", "Committed advance reply lost")
        return dict(source_id=source_id, space_id=space_id, resource_id=resource_id, generation=generation)

    async def request(self, method, path, token, body=None, **kwargs):
        if method == "POST" and (path == "/api/v1/pages" or path.endswith(("/publish", "/proposals", "/validity", "/source-deletion"))):
            source_id, generation = kwargs["source_generation"]
            self.dispatches.append((path, source_id, generation))
            if self.generations.get(source_id) != generation:
                raise IngestError(409, "source_generation_conflict", "Late old write")
        return await super().request(method, path, token, body, **kwargs)


async def test_advance_committed_then_local_rollback_consumes_generation_and_recovers(store):
    source, first, pipeline, _knowledge = await setup_pipeline(store)
    canonical = FencedKnowledge()
    pipeline.internal = canonical
    await finish(pipeline, first["id"])
    db, auth, box = store
    first_generation = canonical.generations[source["id"]]
    canonical.lose_advance = True
    update = SourceUpdate(base_version=1, name="v2", config={"url": "https://host/repo", "ref": "HEAD"})
    with pytest.raises(IngestError) as exc:
        async with db.session() as session, session.begin():
            await IngestService(session, auth, Machine(), canonical, box).update("alice", source["id"], update)
    assert exc.value.status == 503
    orphan_generation = canonical.generations[source["id"]]
    assert orphan_generation > first_generation
    async with db.session() as session:
        row = await session.get(Source, source["id"])
        assert row.version == 1
        assert row.generation == first_generation
    async with db.session() as session, session.begin():
        changed = await IngestService(session, auth, Machine(), canonical, box).update("alice", source["id"], update)
        assert changed["version"] == 2
        row = await session.get(Source, source["id"])
        assert row.generation > orphan_generation


async def test_stale_generation_never_becomes_refreshed_automatic_review(store):
    source, task, pipeline, _knowledge = await setup_pipeline(store)
    canonical = FencedKnowledge()
    pipeline.internal = canonical
    await pipeline.advance(task["id"])
    canonical.generations[source["id"]] += 1
    await finish(pipeline, task["id"])
    db, _auth, _box = store
    async with db.session() as session:
        row = await session.get(Task, task["id"])
        assert row.status == "superseded"
        assert row.error_code == "source_generation_conflict"
    assert canonical.dispatches == []


async def test_internal_fence_headers_and_exact_fence_errors_survive_http_boundary(security):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/advance"):
            return httpx.Response(200, json=json.loads(request.content))
        assert request.headers["X-Ingest-Source-Id"] == "source-1"
        assert request.headers["X-Ingest-Generation"] == "12"
        return httpx.Response(409, json={"error": {"code": "source_generation_conflict"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        internal = InternalClient(IngestSettings(), security["ingest"], client)
        await internal.advance_fence("worker", "source-1", "space", "source:source-1", 12)
        with pytest.raises(IngestError) as exc:
            await internal.request("POST", "/internal/v1/pages/publish", "worker", {}, source_generation=("source-1", 12))
        assert exc.value.code == "source_generation_conflict"
    assert "X-Ingest-Generation" not in requests[0].headers
