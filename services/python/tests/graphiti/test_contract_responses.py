"""Graph native result serialization retains defaults and exposes only navigable proof IDs."""

import httpx
import pytest

from knowledge_platform.graphiti.app import create_app
from knowledge_platform.graphiti.config import GraphitiSettings
from knowledge_platform.graphiti.schemas import GraphResult, TraverseRequest

from .test_app import Security
from .test_traversal import runtime


@pytest.mark.asyncio
async def test_native_graph_paths_and_degraded_default_result_are_unchanged_by_dto():
    service, *_ = runtime()
    body = TraverseRequest(space_ids=["engineering"], seed_fragment_ids=["seed"], as_of="2026-09-09T00:00:00+00:00")
    raw = await service.traverse("delegated", body)
    app = create_app(settings=GraphitiSettings(), runtime=service, security=Security())
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://graphiti") as client:
        response = await client.post("/api/v1/graph/traverse", headers={"X-Service-Token": "gateway", "Authorization": "Bearer delegated"}, json=body.model_dump(mode="json"))
        assert response.status_code == 200
        assert response.json() == raw.model_dump(mode="json")
        async def unavailable(*args):
            return GraphResult(degraded=["graph_unavailable"])
        service.traverse = unavailable
        degraded = await client.post("/api/v1/graph/traverse", headers={"X-Service-Token": "gateway", "Authorization": "Bearer delegated"}, json=body.model_dump(mode="json"))
        assert degraded.json() == {"nodes": [], "edges": [], "paths": [], "degraded": ["graph_unavailable"]}