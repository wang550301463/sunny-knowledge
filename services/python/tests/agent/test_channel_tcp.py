"""Real TCP fragmented provider and channel SSE, with real owned PostgreSQL persistence."""

import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from knowledge_platform.agent.models import Event, Run, now
from knowledge_platform.agent.store import Store
from knowledge_platform.agent.worker import Worker

from .test_answer_tcp import final_provider, tcp_api
from .test_channel_runtime import channel as channel  # noqa: PLC0414 -- pytest fixture
from .test_channel_runtime import create_channel_run


async def channel_prefix(client, fixture, provider, run):
    path = "/internal/v1/channel/runs/" + run["id"]
    await asyncio.wait_for(provider.partial_sent.wait(), 5)
    initial = await client.get(path, headers=fixture.headers())
    assert initial.status_code == 200 and initial.json()["answer"] is None
    provider.allow_first.set()
    async with asyncio.timeout(5):
        while True:
            current = await client.get(path, headers=fixture.headers())
            assert current.status_code == 200
            if current.json().get("answer"):
                return current.json()
            await asyncio.sleep(0.01)


async def test_channel_tcp_first_block_read_and_sse_disconnect_resume_before_provider_finishes(api, channel):
    async with final_provider(api, fallback=channel.transport) as provider, tcp_api(api) as client:
        run = await create_channel_run(api, channel)
        task = asyncio.create_task(api.app.state.worker.run_once())
        try:
            first = await channel_prefix(client, channel, provider, run)
            assert first["answer"]["facts"][0]["text"] == "已验证：支付调用账本🧭"
            assert not first["answer_complete"] and not provider.ended
            path = "/internal/v1/channel/runs/" + run["id"]
            async with client.stream("GET", path + "/events", headers=channel.headers()) as response:
                assert response.status_code == 200
                async with asyncio.timeout(5):
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            event = json.loads(line[6:])
                            if event["type"] == "answer_block":
                                assert event["data"]["text"] == first["answer"]["facts"][0]["text"]
                                cursor = event["seq"]
                                break
            assert not provider.ended and not task.done()
            # A browser result reader uses a separately scoped exact-run broker while generating.
            web = await client.get("/api/v1/runs/" + run["id"])
            assert web.json()["answer"] == first["answer"] and channel.exchanges
            provider.release.set()
            await asyncio.wait_for(task, 5)
            later = channel.mint(context_id="new-message", message_id="new-message")
            replay = await client.get(path + "/events", headers={**channel.headers(later), "Last-Event-ID": str(cursor)})
            events = [json.loads(line[6:]) for line in replay.text.splitlines() if line.startswith("data: ")]
            assert all(event["seq"] > cursor for event in events)
            assert [event["data"]["index"] for event in events if event["type"] == "answer_block"] == [1]
            assert events[-1]["data"]["run"]["answer_complete"]
            assert events[-1]["data"]["run"]["entrypoint"] == "channel"
            assert provider.final_calls == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("action", ["stop", "clear", "restart", "audience", "binding", "expired_message"])
async def test_channel_tcp_prefix_control_revocation_restart_and_expiry(api, channel, action):
    async with final_provider(api, fallback=channel.transport) as provider, tcp_api(api) as client:
        run = await create_channel_run(api, channel)
        task = asyncio.create_task(api.app.state.worker.run_once())
        try:
            first = await channel_prefix(client, channel, provider, run)
            if action in {"stop", "clear"}:
                control = channel.mint(context_id="control", message_id="control")
                response = await client.post("/internal/v1/channel/conversation/" + ("cancel" if action == "stop" else "clear"), headers=channel.headers(control), json={})
                assert response.status_code == 200
            elif action == "restart":
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                async with api.database.session() as session, session.begin():
                    await session.execute(update(Run).where(Run.id == run["id"]).values(lease_until=now() - timedelta(seconds=1)))
                recovered = Worker(api.app.state.service, Store(api.database, api.settings))
                assert await recovered.run_once()
            else:
                if action == "audience":
                    channel.audience_denied = True
                elif action == "binding":
                    channel.revoked.add(channel.token)
                    # Historical broker no longer finds a current binding/context for this run.
                    channel.runs.clear()
                else:
                    channel.tokens[channel.token]["expires_at"] = int((now() - timedelta(seconds=1)).timestamp())
                provider.release.set()
            if action != "restart":
                await asyncio.wait_for(task, 5)
            await asyncio.wait_for(provider.closed.wait(), 3)
            assert provider.final_calls == 1
            value = await client.get("/api/v1/runs/" + run["id"])
            if action == "audience":
                assert value.json()["content_hidden"] and "已验证" not in value.text
            elif action in {"clear", "binding"}:
                assert value.status_code in {403, 404} and "已验证" not in value.text
            else:
                assert value.status_code == 200, value.text
                assert value.json()["answer"] == first["answer"] and not value.json()["answer_complete"]
                assert value.json()["status"] == ("cancelled" if action == "stop" else "partial")
            if action in {"audience", "clear", "binding"}:
                replay = await client.get("/api/v1/runs/" + run["id"] + "/events")
                assert replay.status_code in {403, 404} and "已验证" not in replay.text
            async with api.database.session() as session:
                row = await session.get(Run, run["id"])
                assert row.encrypted_token is None
                events = list(await session.scalars(select(Event).where(Event.run_id == run["id"], Event.type == "answer_block")))
                assert len(events) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_channel_worker_does_not_replace_original_message_context_with_later_token(api, channel):
    run = await create_channel_run(api, channel)
    later = channel.mint(context_id="another-message", message_id="another-message")
    async with api.database.session() as session, session.begin():
        row = await session.get(Run, run["id"])
        row.encrypted_token = api.app.state.service.box.encrypt(later, "agent-run:" + run["id"])
    await api.app.state.worker.run_once()
    assert not api.state.chat_calls
    async with api.database.session() as session:
        row = await session.get(Run, run["id"])
        assert row.status == "failed" and row.encrypted_token is None