"""Timeline regression through signed canonical HTTP and real PostgreSQL revisions."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from knowledge.test_http import client_for
from knowledge.test_postgres import content, publish, source, store  # noqa: F401

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.knowledge.schemas import ProposalCreate
from knowledge_platform.retrieval.app import create_app
from knowledge_platform.retrieval.clients import Clients
from knowledge_platform.retrieval.config import RetrievalSettings
from knowledge_platform.retrieval.service import RetrievalService

from .test_app import Security


class CanonicalHTTPAuthorizer(HTTPAuthorizer):
    """IAM decisions are controlled; canonical requests use the real signed HTTP boundary."""

    def __init__(self, client, headers, iam):
        self.settings = RetrievalSettings(knowledge_url="http://knowledge")
        self.client = client
        self._owned_client = False
        self.security = SimpleNamespace(
            issue=lambda target: headers(caller="retrieval", actor=None)["X-Service-Token"]
        )
        self.iam = iam

    async def resolve(self, token):
        return await self.iam.resolve(token)

    async def require(self, token, action, space_id, resource_id=None):
        return await self.iam.require(token, action, space_id, resource_id)


async def two_revisions(store):
    first, _ = await publish(store)
    _, private = await source(store, resource="source:private", revision="b" * 40)
    knowledge_service, _, _ = store
    async with knowledge_service() as knowledge:
        proposal = await knowledge.propose(
            "alice",
            "page:payments",
            ProposalCreate(
                base_revision=first["id"],
                content=content(private, title="Private new conclusion"),
                reason="Reviewed new private source",
            ),
        )
        second = await knowledge.approve("reviewer", proposal["id"], "Reviewed")
    return first, second


@asynccontextmanager
async def timeline_client(store):
    _, iam, _ = store
    async with client_for(store) as (canonical, headers):
        authorizer = CanonicalHTTPAuthorizer(canonical, headers, iam)
        runtime = RetrievalService(
            authorizer.settings,
            authorizer,
            None,
            None,
            Clients(authorizer.settings, authorizer),
        )
        app = create_app(settings=authorizer.settings, runtime=runtime, security=Security())
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://retrieval"
            ) as client,
        ):
            yield client, canonical, headers


@pytest.mark.asyncio
async def test_timeline_retains_readable_pg_revision_when_current_source_is_private(store):
    first, second = await two_revisions(store)
    _, iam, _ = store
    iam.denied.add(("bob", "read", "source:private"))
    iam.epoch += 1
    async with timeline_client(store) as (retrieval, canonical, headers):
        current = await canonical.get("/api/v1/pages/page:payments", headers=headers(actor="bob"))
        assert current.status_code == 403
        history = await canonical.get(
            "/api/v1/pages/page:payments/revisions", headers=headers(actor="bob")
        )
        assert history.status_code == 200
        assert [revision["id"] for revision in history.json()["items"]] == [first["id"]]
        response = await retrieval.post(
            "/api/v1/timeline",
            headers={"X-Service-Token": "gateway", "Authorization": "Bearer bob"},
            json={"space_ids": ["finance"], "page_ids": ["page:payments"]},
        )
        assert response.status_code == 200, response.text
        assert [item["revision_id"] for item in response.json()["items"]] == [first["id"]]
        assert not response.json()["truncated"]
        assert second["id"] not in response.text
        assert "source:private" not in response.text
        assert "Private new conclusion" not in response.text


@pytest.mark.asyncio
async def test_timeline_out_of_scope_pg_history_cannot_consume_limit_or_set_truncated(store):
    await two_revisions(store)
    async with timeline_client(store) as (retrieval, _canonical, _headers):
        response = await retrieval.post(
            "/api/v1/timeline",
            headers={"X-Service-Token": "gateway", "Authorization": "Bearer bob"},
            json={"space_ids": ["engineering"], "page_ids": ["page:payments"], "limit": 1},
        )
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert not response.json()["truncated"]
        assert "finance" not in response.text


@pytest.mark.asyncio
async def test_timeline_canonical_auth_outage_aborts_readable_pg_history(store):
    await two_revisions(store)
    _, iam, _ = store
    async with timeline_client(store) as (retrieval, _canonical, _headers):
        async def unavailable(*args, **kwargs):
            raise HTTPException(503, "Canonical authorization unavailable")

        iam.authorize = unavailable
        response = await retrieval.post(
            "/api/v1/timeline",
            headers={"X-Service-Token": "gateway", "Authorization": "Bearer bob"},
            json={"space_ids": ["finance"], "page_ids": ["page:payments"]},
        )
        assert response.status_code == 503
        assert "items" not in response.json()
        assert "Private new conclusion" not in response.text