import asyncio
import json

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test-client", "version": "1"}}}


async def initialize(client, token="alice"):
    result = await client.post("/mcp", json=INIT, headers={"Authorization": "Bearer "+token, "Accept": "application/json, text/event-stream"})
    assert result.status_code == 200, result.text
    sid = result.headers["mcp-session-id"]
    headers = {"Authorization": "Bearer "+token, "Accept": "application/json, text/event-stream", "MCP-Session-Id": sid, "MCP-Protocol-Version": "2025-11-25"}
    result = await client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
    assert result.status_code == 202
    return headers


def rpc_body(response):
    if "text/event-stream" in response.headers.get("content-type", ""):
        return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith("data: ") and line[6:].startswith("{")))
    return response.json()


async def test_unauthenticated_discovery_and_scope(runtime):
    client, domains, _, resource = runtime
    response = await client.post("/mcp", json=INIT)
    assert response.status_code == 401
    assert f'resource_metadata="{resource.removesuffix("/mcp")}/.well-known/oauth-protected-resource/mcp"' in response.headers["www-authenticate"]
    assert 'scope="knowledge:read"' in response.headers["www-authenticate"]
    metadata = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert metadata["resource"] == resource
    assert metadata["authorization_servers"] == ["http://localhost:18180/idp/realms/knowledge"]
    assert not domains.calls


@pytest.mark.parametrize("claims", [{"audiences": ["knowledge-api"]}, {"audiences": ["http://other/mcp", "knowledge-api"]}, {"issuer": "http://other"}, {"client_id": "knowledge-web"}, {"expires_at": 1}])
async def test_resource_audience_issuer_client_and_expiry_are_mandatory(runtime, claims):
    client, domains, _, _ = runtime
    domains.modify_identity = claims
    response = await client.post("/mcp", json=INIT, headers={"Authorization": "Bearer alice"})
    assert response.status_code == 401


async def test_real_official_client_negotiates_and_calls_six_strict_tools(runtime):
    _, domains, keys, resource = runtime
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer alice", "X-Service-Token": keys["gateway"].issue("mcp")}) as http:
        async with streamable_http_client(resource, http_client=http) as streams:
            async with ClientSession(streams.read, streams.write) as session:
                negotiated = await session.initialize()
                assert negotiated.protocol_version == "2025-11-25"
                tools = (await session.list_tools()).tools
                assert {t.name for t in tools} == {"search", "get", "traverse", "timeline", "ask", "feedback"}
                assert next(t for t in tools if t.name == "search").input_schema["additionalProperties"] is False
                found = await session.call_tool("search", {"query": "Payment", "space_ids": ["space"]})
                assert not found.is_error
                assert found.structured_content["gaps"] == ["no_authorized_evidence"]
                assert ("retrieval", "POST", "/internal/v1/search", "alice") in [c[:4] for c in domains.calls]
                bad = await session.call_tool("search", {"query": "Payment", "space_ids": ["space"], "principal": "admin"})
                assert bad.is_error


async def test_legacy_session_is_principal_bound_and_live_revocation_is_not_replayed(runtime):
    client, domains, _, _ = runtime
    headers = await initialize(client)
    request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    response = await client.post("/mcp", json=request, headers={**headers, "Authorization": "Bearer bob"})
    assert response.status_code == 404
    response = await client.post("/mcp", json=request, headers={**headers, "Authorization": "Bearer alice-refresh"})
    assert response.status_code == 200
    domains.denied.add("alice")
    response = await client.post("/mcp", json=request, headers=headers)
    assert response.status_code == 403
    assert "search" not in response.text


async def test_feedback_challenges_only_independent_scope_before_write(runtime):
    client, domains, _, _ = runtime
    headers = await initialize(client)
    request = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "feedback", "arguments": {"run_id": "run", "rating": "helpful", "idempotency_key": "f-1"}}}
    response = await client.post("/mcp", json=request, headers=headers)
    assert response.status_code == 403
    assert 'error="insufficient_scope"' in response.headers["www-authenticate"]
    assert "knowledge:feedback" in response.headers["www-authenticate"]
    assert not any(c[0] == "agent" for c in domains.calls)
    response = await client.post("/mcp", json=request, headers={**headers, "Authorization": "Bearer alice-feedback"})
    assert response.status_code == 200
    assert rpc_body(response)["result"]["structuredContent"]["id"] == "feedback"


async def test_authorization_failure_or_epoch_change_discards_domain_payload(runtime):
    client, domains, _, _ = runtime
    headers = await initialize(client)
    domains.payload["gaps"] = ["private answer"]
    domains.after_search = lambda: setattr(domains, "epoch", 2)
    response = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "search", "arguments": {"query": "Payment", "space_ids": ["space"]}}})
    assert "private answer" not in response.text
    assert rpc_body(response)["result"]["isError"] is True
    domains.unavailable = True
    failed = await client.post("/mcp", json=INIT, headers={"Authorization": "Bearer alice"})
    assert failed.status_code == 503
    assert "never expose" not in failed.text


async def test_origin_body_version_headers_and_no_cached_sse_replay(runtime):
    client, _, _, _ = runtime
    headers = await initialize(client)
    request = {"jsonrpc": "2.0", "id": 5, "method": "tools/list"}
    assert (await client.post("/mcp", headers={**headers, "Origin": "https://evil.example"}, json=request)).status_code == 403
    assert (await client.post("/mcp", headers={**headers, "MCP-Protocol-Version": "1900-01-01"}, json=request)).status_code >= 400
    assert (await client.post("/mcp", headers=headers, content=b"x" * 270000)).status_code == 413
    assert (await client.get("/mcp", headers={**headers, "Last-Event-ID": "another-user-event"})).status_code == 400
    assert (await client.post("/mcp", headers={**headers, "Accept": "text/html"}, json=request)).status_code == 406


async def test_cancelled_notification_stops_inflight_read(runtime):
    client, domains, _, _ = runtime
    headers = await initialize(client)
    domains.slow = True
    pending = asyncio.create_task(client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 91, "method": "tools/call", "params": {"name": "search", "arguments": {"query": "Payment", "space_ids": ["space"]}}}))
    await asyncio.wait_for(domains.waiting.wait(), 3)
    response = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 91}})
    assert response.status_code == 202
    await asyncio.wait_for(domains.cancelled.wait(), 3)
    pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)
