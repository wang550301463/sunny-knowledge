"""Channel message/read-only-result broker protocol fixtures with real owned PostgreSQL."""
import json
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from knowledge_platform.agent.models import Run, Session, now

from .test_runtime import setup_run


@pytest.fixture
async def channel(api):
    agent, _, web_run, _ = await setup_run(api)
    await api.client.post("/api/v1/runs/" + web_run.json()["id"] + "/cancel", json={})
    response = await api.client.post("/api/v1/agents/" + agent["id"] + "/publish", json={"base_configuration_id": agent["configuration_id"]})
    assert response.status_code == 200
    fixture = SimpleNamespace(tokens={}, revoked=set(), exchanges=[], runs={}, owner="alice")
    original = api.app.state.service.auth.client

    def mint(**changes):
        context = {
            "context_id": "context1", "channel_id": "bot1", "conversation_key": "conversation1",
            "agent_id": agent["id"], "agent_configuration_id": agent["configuration_id"],
            "space_ids": ["engineering"], "chat_type": "group", "audience_id": "audience1", "group_key": "group1", "message_id": "message1",
            **changes,
        }
        token = ("skr_" if context.get("read_run_id") else "skc_") + str(len(fixture.tokens) + 1)
        fixture.tokens[token] = {"context": context, "owner": fixture.owner, "expires_at": int((now() + timedelta(seconds=170)).timestamp())}
        return token

    async def transport(request):
        body = json.loads(request.content) if request.content else {}
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if request.url.host == "auth":
            if request.url.path.endswith("/channel-run-token"):
                fixture.exchanges.append(deepcopy(body))
                if token != api.token or body.get("run_id") not in fixture.runs:
                    return httpx.Response(403)
                context = fixture.runs[body["run_id"]]
                delegated = mint(**context, read_run_id=body["run_id"])
                return httpx.Response(200, json={"access_token": delegated, "token_type": "Bearer", "expires_in": 120, "scope": "knowledge:read"})
            if token.startswith(("skc_", "skr_")):
                value = fixture.tokens.get(token)
                if value is None or token in fixture.revoked or value["expires_at"] <= now().timestamp():
                    return httpx.Response(403)
                if request.url.path.endswith("/resolve"):
                    return httpx.Response(200, json={"principal": {"id": value["owner"], "subjects": ["user:" + value["owner"]], "permissions": [], "auth_epoch": api.state.epoch, "channel_context": value["context"]}, "scopes": ["knowledge:read"], "delegated": True, "expires_at": value["expires_at"]})
                allowed = body.get("space_id") in value["context"]["space_ids"] and body.get("action") == "read" and (body.get("action"), body.get("space_id"), body.get("resource_id")) not in api.state.denied
                return httpx.Response(200, json={"allowed": allowed, "auth_epoch": api.state.epoch})
        return await api.state.handle(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        api.app.state.service.auth.client = client
        api.app.state.service.clients.client = client
        fixture.mint, fixture.agent, fixture.web_run = mint, agent, web_run.json()
        fixture.token = mint()
        fixture.headers = lambda token=None, caller="channel": {**api.headers(caller), "Authorization": "Bearer " + (token or fixture.token)}
        try:
            yield fixture
        finally:
            api.app.state.service.auth.client = original
            api.app.state.service.clients.client = original


async def create_channel_run(api, channel, question="What calls Ledger?", token=None):
    response = await api.client.post("/internal/v1/channel/runs", headers=channel.headers(token), json={"question": question})
    assert response.status_code == 201, response.text
    context = channel.tokens[token or channel.token]["context"]
    channel.runs[response.json()["id"]] = deepcopy(context)
    return response.json()


async def test_channel_runs_use_server_identity_published_config_and_durable_isolated_session(api, channel):
    run = await create_channel_run(api, channel)
    assert run["agent_id"] == channel.agent["id"]
    assert run["configuration_id"] == channel.agent["configuration_id"]
    assert run["actual_scope"] == ["engineering"]
    assert run["session_id"] != channel.web_run["session_id"]
    assert run["entrypoint"] == "channel"
    repeated = await create_channel_run(api, channel)
    assert repeated["id"] == run["id"]
    sessions = (await api.client.get("/api/v1/sessions")).json()["items"]
    assert run["session_id"] not in {row["id"] for row in sessions}
    async with api.database.session() as session:
        row = await session.get(Run, run["id"])
        assert row.encrypted_token and channel.token not in row.encrypted_token
        assert row.deadline.timestamp() <= channel.tokens[channel.token]["expires_at"]
        assert await session.scalar(select(func.count()).select_from(Session)) == 2
    await api.app.state.worker.run_once()
    complete = await api.client.get("/internal/v1/channel/runs/" + run["id"], headers=channel.headers())
    assert complete.status_code == 200 and complete.json()["answer_complete"]
    assert complete.json()["answer"]["facts"][0]["text"] == "Pay calls Ledger"
    assert all(t["function"]["name"] not in {"feedback", "propose_revision"} for body in api.state.chat_calls for t in body["tools"])


@pytest.mark.parametrize("forgery", [{"principal": "admin"}, {"space_ids": ["private"]}, {"session_id": "web"}, {"agent_id": "other"}, {"configuration_id": "draft"}, {"idempotency_key": "supplied"}])
async def test_channel_create_rejects_every_caller_supplied_identity_or_scope_field(api, channel, forgery):
    response = await api.client.post("/internal/v1/channel/runs", headers=channel.headers(), json={"question": "Q", **forgery})
    assert response.status_code == 422


async def test_channel_routes_reject_web_mcp_and_normal_bearer_and_public_routes_reject_delegation(api, channel):
    for caller in ["gateway", "mcp"]:
        response = await api.client.post("/internal/v1/channel/runs", headers=channel.headers(caller=caller), json={"question": "Q"})
        assert response.status_code == 403
    assert (await api.client.post("/internal/v1/channel/runs", headers=api.headers("channel"), json={"question": "Q"})).status_code == 403
    for caller in ["gateway", "mcp"]:
        assert (await api.client.get("/api/v1/runs/" + channel.web_run["id"], headers=channel.headers(caller=caller))).status_code == 403