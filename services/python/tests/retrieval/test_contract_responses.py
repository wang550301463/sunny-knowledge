"""Actual retrieval assembly traverses the new response serialization boundary unchanged."""

import httpx
import pytest

from knowledge_platform.retrieval.app import create_app
from knowledge_platform.retrieval.schemas import SearchRequest, TraverseRequest

from .test_app import Security
from .test_service import setup
from .test_timeline_postgres import timeline_client, two_revisions


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["search", "traverse"])
async def test_native_assembly_keeps_fixed_evidence_and_excludes_projection_private_fields(operation):
    service, _, _, index = setup()
    body = (
        SearchRequest(query="Pay", space_ids=["engineering"], as_of="2026-09-09T00:00:00+00:00")
        if operation == "search"
        else TraverseRequest(seed_fragment_ids=[index.fragments[0].id], space_ids=["engineering"], as_of="2026-09-09T00:00:00+00:00")
    )
    expected = await getattr(service, operation)("delegated", body)
    app = create_app(settings=service.settings, runtime=service, security=Security())
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://retrieval") as client:
        response = await client.post("/api/v1/" + operation, headers={"X-Service-Token": "gateway", "Authorization": "Bearer delegated"}, json=body.model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json() == expected
    assert expected["items"] and expected["evidence"]
    for item in response.json()["items"]:
        assert not {"embedding", "read_clauses", "policy_fingerprint", "acl_domain", "acl_epoch", "acl_version", "embedding_configuration_id", "embedding_dimensions"} & item.keys()
    assert expected["as_of"] == "2026-09-09T00:00:00+00:00"
    assert all(value["evidence"]["revision_id"] != expected["items"][0]["revision_id"] for value in expected["evidence"])


@pytest.mark.asyncio
async def test_pg_timeline_still_omits_revoked_revision_and_retains_native_time_format(store):
    first, second = await two_revisions(store)
    _, iam, _ = store
    iam.denied.add(("bob", "read", "source:private"))
    iam.epoch += 1
    async with timeline_client(store) as (retrieval, _, _):
        response = await retrieval.post("/api/v1/timeline", headers={"X-Service-Token": "gateway", "Authorization": "Bearer bob"}, json={"space_ids": ["finance"], "page_ids": ["page:payments"]})
    assert response.status_code == 200
    items = response.json()["items"]
    assert [r["revision_id"] for r in items] == [first["id"]]
    assert second["id"] not in str(response.json())
    assert items[0]["known_at"] == first["created_at"]
    assert response.json()["deployment_state"] == "unknown_without_deployment_evidence"