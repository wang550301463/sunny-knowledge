"""Worker protocol and actual Temporal/TCP behavior; no real model or account used."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from knowledge_platform.knowledge.lifecycle_config import KnowledgeSettings


def settings(**values):
    return KnowledgeSettings(
        knowledge_lifecycle_principal_id="life-worker",
        knowledge_lifecycle_oidc_token_url=values.pop("knowledge_lifecycle_oidc_token_url", "http://idp/token"),
        knowledge_lifecycle_oidc_client_id="lifecycle",
        knowledge_lifecycle_oidc_client_secret="PROTOCOL_ONLY_SECRET",
        knowledge_lifecycle_space_ids=["finance", "engineering"], **values,
    )


def body():
    return {"space_id": "finance", "scheduled_at": "2020-01-01T00:00:00+00:00",
            "cursor": None, "limit": 25, "idempotency_key": "batch"}


async def test_dispatcher_has_daily_stable_workflow_identity_and_no_credentials():
    from knowledge_platform.knowledge.lifecycle_worker import LifecycleDispatcher
    client = SimpleNamespace(start_workflow=AsyncMock())
    dispatcher = LifecycleDispatcher(client, settings())
    first = datetime(2020, 1, 1, 12, tzinfo=UTC)
    await dispatcher.dispatch(first)
    await dispatcher.dispatch(first + timedelta(hours=1))
    calls = client.start_workflow.call_args_list
    assert len(calls) == 4
    assert calls[0].kwargs["id"] == calls[2].kwargs["id"]
    assert calls[1].kwargs["id"] == calls[3].kwargs["id"]
    assert calls[0].kwargs["id"] != calls[1].kwargs["id"]
    assert calls[0].args[1]["scheduled_at"] == "2020-01-01T00:00:00+00:00"
    assert "PROTOCOL_ONLY_SECRET" not in str(calls)
    assert "access_token" not in str(calls)


async def test_client_gets_fresh_read_only_token_and_uses_workload_signed_http():
    from knowledge_platform.knowledge.lifecycle_worker import LifecycleClient
    calls = []
    async def provider(request):
        calls.append(request)
        if request.url.host == "idp":
            assert "scope=knowledge%3Aread" in request.content.decode()
            return httpx.Response(200, json={"access_token": "PRIVATE_TOKEN", "token_type": "Bearer"})
        assert request.url.path == "/internal/v1/lifecycle/reconcile"
        assert request.headers["X-Service-Token"] == "signed-knowledge"
        assert request.headers["Authorization"] == "Bearer PRIVATE_TOKEN"
        assert json.loads(request.content) == body()
        return httpx.Response(200, json={"items": [], "next_cursor": None})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        client = LifecycleClient(settings(), client=http, security=SimpleNamespace(issue=lambda target: "signed-" + target))
        assert await client.reconcile(body()) == {"items": [], "next_cursor": None}
        await client.reconcile(body())
    assert [r.url.host for r in calls] == ["idp", "knowledge", "idp", "knowledge"]


@pytest.mark.parametrize("failure", ["bad_json", "credential_error", "repeated_cursor"])
async def test_client_fails_closed_without_echoing_upstream_secrets(failure):
    from knowledge_platform.knowledge.lifecycle_worker import LifecycleClient
    async def provider(request):
        if request.url.host == "idp":
            if failure == "credential_error":
                return httpx.Response(401, text="PRIVATE_TOKEN PROTOCOL_ONLY_SECRET")
            return httpx.Response(200, json={"access_token": "PRIVATE_TOKEN", "token_type": "Bearer"})
        if failure == "bad_json":
            return httpx.Response(200, text="PRIVATE_TOKEN PROTOCOL_ONLY_SECRET")
        return httpx.Response(200, json={"items": [], "next_cursor": "same"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        client = LifecycleClient(settings(), client=http, security=SimpleNamespace(issue=lambda target: "signed"))
        with pytest.raises(Exception) as error:
            await client.reconcile({**body(), "cursor": "same"})
        assert "PRIVATE_TOKEN" not in str(error.value)
        assert "PROTOCOL_ONLY_SECRET" not in str(error.value)


async def test_cancel_during_actual_http_wait_closes_upstream_connection():
    from knowledge_platform.knowledge.lifecycle_worker import LifecycleClient
    started, disconnected = asyncio.Event(), asyncio.Event()
    async def respond(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            length = next(int(line.split(b":", 1)[1]) for line in header.split(b"\r\n")
                          if line.lower().startswith(b"content-length:"))
            await reader.readexactly(length)
            if header.startswith(b"POST /token "):
                payload = b'{"access_token":"TCP_ONLY_TOKEN","token_type":"Bearer"}'
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload)
                await writer.drain()
            else:
                started.set()
                assert await reader.read() == b""
                disconnected.set()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    address = "http://127.0.0.1:" + str(server.sockets[0].getsockname()[1])
    client = LifecycleClient(settings(knowledge_lifecycle_oidc_token_url=address + "/token", knowledge_url=address),
                             security=SimpleNamespace(issue=lambda target: "signed"))
    try:
        task = asyncio.create_task(client.reconcile(body()))
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(disconnected.wait(), 3)
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


async def test_real_temporal_workflow_paginates_with_deterministic_batch_keys():
    address = os.environ.get("INGEST_TEMPORAL_ADDRESS")
    if not address:
        pytest.skip("Real isolated Temporal service address required")
    from temporalio import activity
    from temporalio.client import Client
    from temporalio.worker import Worker
    from knowledge_platform.knowledge.lifecycle_workflow import LifecycleWorkflow
    calls = []
    @activity.defn(name="knowledge.lifecycle.reconcile")
    async def reconcile(request):
        calls.append(request)
        return {"next_cursor": "page:next" if request["cursor"] is None else None}
    client = await Client.connect(address)
    queue = "lifecycle-test-" + uuid4().hex
    async with Worker(client, task_queue=queue, workflows=[LifecycleWorkflow], activities=[reconcile]):
        result = await client.execute_workflow(LifecycleWorkflow.run,
            {"space_id": "finance", "scheduled_at": "2020-01-01T00:00:00+00:00", "policy_version": "freshness-v1"},
            id=queue, task_queue=queue, execution_timeout=timedelta(seconds=30))
    assert result == {"completed": True}
    assert len(calls) == 2
    assert calls[0]["idempotency_key"] != calls[1]["idempotency_key"]
    assert calls[1]["cursor"] == "page:next"

