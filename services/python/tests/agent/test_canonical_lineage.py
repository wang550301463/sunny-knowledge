"""Actual Agent and canonical HTTP applications backed by separate owned PostgreSQL DBs.

Only IAM and model responses are protocol fixtures. Canonical writes, reviews, publication,
input closure, and all page/evidence reads execute their real service implementations.
"""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from knowledge_platform.common.db import Database
from knowledge_platform.knowledge.app import create_app
from knowledge_platform.knowledge.models import initialize

from .test_boundaries import call, model_response
from .test_runtime import setup_run


class PolicyFixture:
    def __init__(self, token):
        self.tokens = {token: "alice", "reviewer": "reviewer", "bob": "bob"}
        self.denied = set()
        self.epoch = 1

    async def resolve(self, token):
        actor = self.tokens[token]
        return SimpleNamespace(id=actor, subjects=["user:" + actor], permissions=[], auth_epoch=self.epoch)

    async def authorize(self, token, action, space_id, resource_id=None):
        return SimpleNamespace(allowed=(self.tokens[token], action, resource_id) not in self.denied, auth_epoch=self.epoch, acl_domain="test", acl_version=self.epoch)

    async def policy(self, space_id, resource_id):
        return {"space_id": space_id, "resource_id": resource_id, "space_read_subjects": ["user:alice", "user:bob", "user:reviewer"], "resource_read_subjects": ["user:alice", "user:reviewer"] if resource_id == "page:private" else None, "acl_version": self.epoch, "auth_epoch": self.epoch}

    async def request(self, method, path, target, token=None, json=None):
        return SimpleNamespace(status_code=201, raise_for_status=lambda: None)


class ServiceTransport(httpx.AsyncBaseTransport):
    def __init__(self, canonical, handler):
        self.canonical = httpx.ASGITransport(app=canonical)
        self.fixture = httpx.MockTransport(handler)

    async def handle_async_request(self, request):
        if request.url.host == "knowledge":
            return await self.canonical.handle_async_request(request)
        return await self.fixture.handle_async_request(request)

    async def aclose(self):
        await self.canonical.aclose()
        await self.fixture.aclose()


@pytest_asyncio.fixture
async def canonical(api):
    private = Path(__file__).resolve().parents[4] / "knowledge-docker/.local/test-env.json"
    url = os.environ.get("TEST_DATABASE_URL")
    if not url and private.exists():
        url = json.loads(private.read_text())["databases"]["knowledge"]
    if not url:
        pytest.skip("Separate canonical PostgreSQL database required")
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    schema = "test_agent_canonical_" + uuid4().hex
    admin = create_async_engine(url, hide_parameters=True)
    async with admin.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_async_engine(url, connect_args={"options": f"-csearch_path={schema}"}, hide_parameters=True)
    await initialize(engine)
    policies = PolicyFixture(api.token)
    app = create_app(database=Database(engine), authorizer=policies, security=api.security["knowledge"], initialize_schema=False)

    def headers(actor=None, caller="gateway"):
        return {"Authorization": "Bearer " + (actor or api.token), "X-Service-Token": api.security[caller].issue("knowledge")}

    old = api.app.state.service.clients.client
    try:
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=ServiceTransport(app, api.state.handle), base_url="http://knowledge") as upstream:
            api.app.state.service.clients.client = upstream
            yield SimpleNamespace(client=upstream, headers=headers, policy=policies)
    finally:
        api.app.state.service.clients.client = old
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await admin.dispose()


@pytest.mark.parametrize("private_has_public_source", [False, True])
async def test_agent_private_wiki_to_public_target_retains_acl_through_actual_review_and_publish(api, canonical, private_has_public_source):
    client, headers = canonical.client, canonical.headers
    source_text = "package public\nfunc Public() {}\n"
    source = await client.post("/internal/v1/sources/snapshots", headers=headers(caller="ingest"), json={"source_id": "git:public", "source_revision": "a" * 40, "resource_id": "source:public", "space_id": "engineering", "path": "main.go", "kind": "code", "text": source_text, "sha256": hashlib.sha256(source_text.encode()).hexdigest(), "object_key": "snapshots/public/main.go"})
    assert source.status_code == 201, source.text
    snapshot = source.json()
    evidence = {"resource_id": "source:public", "revision_id": snapshot["id"], "source_id": "git:public", "source_revision": "a" * 40, "path": "main.go", "start_line": 1, "end_line": 2, "kind": "code", "valid_from": None, "valid_until": None}
    revisions = {}
    for page_id, markdown, refs in [("page:public", "Public target", [evidence]), ("page:private", "PRIVATE_WIKI_CONCLUSION", [evidence] if private_has_public_source else [])]:
        created = await client.post("/api/v1/pages", headers=headers(), json={"id": page_id, "space_id": "engineering", "content": {"title": page_id, "markdown": markdown, "entity_type": "Module", "evidence": refs}})
        assert created.status_code == 201, created.text
        approved = await client.post("/api/v1/reviews/" + created.json()["proposal"]["id"] + "/approve", headers=headers("reviewer"), json={"reason": "Initial human review"})
        assert approved.status_code == 200, approved.text
        revisions[page_id] = approved.json()["id"]
    canonical.policy.denied.add(("bob", "read", "page:private"))
    _, _, result, _ = await setup_run(api, config={"space_ids": ["engineering"], "mode": "maintenance", "model_configuration_id": "model1"})
    submitted = {}

    async def model(request, body):
        messages = [m for m in body["messages"] if m["role"] == "tool"]
        if not messages:
            return model_response(body, calls=[call("private", "get", {"space_id": "engineering", "page_id": "page:private"})])
        if len(messages) == 1:
            assert "PRIVATE_WIKI_CONCLUSION" in messages[0]["content"]
            return model_response(body, calls=[call("public", "get", {"space_id": "engineering", "page_id": "page:public"})])
        if len(messages) == 2:
            citation = json.loads(messages[1]["content"])["citations"][0]["id"]
            return model_response(body, calls=[call("derive", "propose_revision", {"space_id": "engineering", "page_id": "page:public", "base_revision": revisions["page:public"], "title": "Derived", "markdown": "PRIVATE_WIKI_CONCLUSION", "reason": "Private knowledge combined with public evidence", "citation_ids": [citation]})])
        submitted.update(json.loads(messages[-1]["content"]))
        return model_response(body, answer={"facts": [], "inferences": [], "gaps": ["Human review required"]})

    api.state.chat_handler = model
    await api.app.state.worker.run_once()
    run = await api.client.get("/api/v1/runs/" + result.json()["id"])
    assert run.json()["status"] == "completed", run.text
    assert submitted["published"] is False
    review_path = "/api/v1/reviews/" + submitted["proposal_id"]
    review = await client.get(review_path, headers=headers())
    assert review.status_code == 200, review.text
    assert review.json()["input_revisions"] == [{"page_id": key, "revision_id": value} for key, value in sorted(revisions.items())]
    assert review.json()["content"]["evidence"] == [evidence]
    assert (await client.get("/api/v1/pages/page:public", headers=headers("bob"))).status_code == 200
    assert (await client.get(review_path, headers=headers("bob"))).status_code == 403
    assert (await client.post(review_path + "/approve", headers=headers("bob"), json={"reason": "Unauthorized review"})).status_code == 403
    approved = await client.post(review_path + "/approve", headers=headers("reviewer"), json={"reason": "Authorized review"})
    assert approved.status_code == 200, approved.text
    assert (await client.get("/api/v1/pages/page:public", headers=headers("bob"))).status_code == 403
    canonical.policy.denied.add(("alice", "read", "page:private"))
    canonical.policy.epoch += 1
    hidden = await api.client.get("/api/v1/runs/" + result.json()["id"])
    assert hidden.json()["content_hidden"] is True
    assert "Human review required" not in hidden.text