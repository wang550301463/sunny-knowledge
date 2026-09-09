"""Real loopback TCP/WebSocket behavior, independent of the channel worker."""

import asyncio
import json
import socket

import httpx
import pytest
import pytest_asyncio
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from server import Limits, create_app


@pytest_asyncio.fixture
async def running():
    servers = []

    async def start(limits=None):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(limits or Limits()),
                log_config=None,
                log_level="critical",
                access_log=False,
                ws="websockets-sansio",
                ws_max_size=131072,
                lifespan="on",
            )
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        servers.append((server, task))
        for _ in range(100):
            if server.started:
                return f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}/"
            await asyncio.sleep(0.01)
        raise AssertionError("fixture server did not start")

    yield start
    for server, task in servers:
        server.should_exit = True
        await asyncio.wait_for(task, 3)


async def subscribe(ws, bot="fixture-bot", secret="wecom-protocol-only"):
    await ws.send(json.dumps({"cmd": "aibot_subscribe", "headers": {"req_id": "subscribe"}, "body": {"bot_id": bot, "secret": secret}}))
    return json.loads(await asyncio.wait_for(ws.recv(), 1))


def message(request="request", text="研发问题", message_id="message"):
    return {"message_id": message_id, "request_id": request, "user_id": "wecom-user", "chat_type": "group", "chat_id": "group", "text": text}


async def response(ws, content, finish=False, request="request", stream="stream"):
    await ws.send(json.dumps({"cmd": "aibot_respond_msg", "headers": {"req_id": request}, "body": {"msgtype": "stream", "stream": {"id": stream, "content": content, "finish": finish}}}))


@pytest.mark.asyncio
async def test_subscribe_ping_native_callback_and_cumulative_utf8_replies(running):
    base, url = await running()
    async with httpx.AsyncClient(base_url=base) as client, connect(url) as ws:
        assert (await subscribe(ws))["errcode"] == 0
        await ws.send(json.dumps({"cmd": "ping", "headers": {"req_id": "heartbeat"}}))
        assert json.loads(await ws.recv())["headers"]["req_id"] == "heartbeat"
        injected = await client.post("/fixtures/bots/fixture-bot/messages", json=message())
        assert injected.status_code == 202
        callback = json.loads(await ws.recv())
        assert callback == {"cmd": "aibot_msg_callback", "headers": {"req_id": "request"}, "body": {"msgid": "message", "aibotid": "fixture-bot", "chattype": "group", "chatid": "group", "from": {"userid": "wecom-user"}, "msgtype": "text", "text": {"content": "研发问题"}}}
        for text, finished in [("研发", False), ("研发答案🙂", True)]:
            await response(ws, text, finished)
            assert json.loads(await ws.recv())["errcode"] == 0
        state = (await client.get("/fixtures/bots/fixture-bot")).json()
        assert state["connected"] and state["connection_count"] == 1
        assert state["injected_message_count"] == 1
        assert [(f["sequence"], f["finished"], f["content"]) for f in state["replies"]] == [(1, False, "研发"), (2, True, "研发答案🙂")]
        assert all(f["request_id"] == "request" and not f["ack_dropped"] for f in state["replies"])
        assert "secret" not in json.dumps(state)


@pytest.mark.asyncio
async def test_only_dedicated_fixture_credentials_are_accepted_without_echo(running):
    base, url = await running()
    async with httpx.AsyncClient(base_url=base) as client:
        for bot, secret in [("real-bot", "wecom-protocol-only"), ("fixture-bot", "USER-PRIVATE-SECRET")]:
            async with connect(url) as ws:
                ack = await subscribe(ws, bot, secret)
                assert ack["errcode"] != 0
                assert secret not in json.dumps(ack)
                with pytest.raises(ConnectionClosed):
                    await ws.recv()
        state = (await client.get("/fixtures/bots/fixture-bot")).json()
        assert state["connection_count"] == 0 and not state["connected"]
        denied = await client.get("/fixtures/bots/real-bot")
        assert denied.status_code == 400
        assert "USER-PRIVATE-SECRET" not in denied.text


@pytest.mark.asyncio
async def test_duplicate_callbacks_disconnect_and_reconnect_preserve_history(running):
    base, url = await running()
    async with httpx.AsyncClient(base_url=base) as client:
        async with connect(url) as ws:
            await subscribe(ws)
            for _ in range(2):
                assert (await client.post("/fixtures/bots/fixture-bot/messages", json=message())).status_code == 202
                assert json.loads(await ws.recv())["body"]["msgid"] == "message"
            await response(ws, "one answer", True)
            await ws.recv()
            assert (await client.post("/fixtures/bots/fixture-bot/disconnect", json={})).status_code == 200
            with pytest.raises(ConnectionClosed):
                await ws.recv()
        async with connect(url) as ws:
            await subscribe(ws)
            state = (await client.get("/fixtures/bots/fixture-bot")).json()
            assert state["connection_count"] == 2 and state["connected"]
            assert state["injected_message_count"] == 2 and len(state["replies"]) == 1
            conflict = message(text="different content with same request ID")
            assert (await client.post("/fixtures/bots/fixture-bot/messages", json=conflict)).status_code == 409


@pytest.mark.asyncio
async def test_dropped_response_ack_is_consumed_once_and_frame_is_still_observable(running):
    base, url = await running()
    async with httpx.AsyncClient(base_url=base) as client, connect(url) as ws:
        await subscribe(ws)
        await client.post("/fixtures/bots/fixture-bot/messages", json=message())
        await ws.recv()
        assert (await client.post("/fixtures/bots/fixture-bot/ack-policy", json={"drop_next_response_ack": True})).status_code == 200
        await ws.send(json.dumps({"cmd": "ping", "headers": {"req_id": "ping"}}))
        assert json.loads(await ws.recv())["errcode"] == 0
        await response(ws, "outcome unknown", True)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.recv(), 0.05)
        state = (await client.get("/fixtures/bots/fixture-bot")).json()
        assert len(state["replies"]) == 1 and state["replies"][0]["ack_dropped"]
        assert not state["drop_next_response_ack"] and state["dropped_response_ack_count"] == 1
        # A broken client's resend is recorded, never hidden by fixture dedup.
        await response(ws, "outcome unknown", True)
        assert json.loads(await ws.recv())["errcode"] == 0
        state = (await client.get("/fixtures/bots/fixture-bot")).json()
        assert len(state["replies"]) == 2 and not state["replies"][1]["ack_dropped"]


@pytest.mark.asyncio
async def test_capacity_and_invalid_frames_fail_explicitly_without_erasing_history(running):
    base, url = await running(Limits(max_bots=1, max_requests=1, max_replies=1, max_reply_bytes=20))
    async with httpx.AsyncClient(base_url=base) as client, connect(url) as ws:
        await subscribe(ws)
        async with connect(url) as other:
            assert (await subscribe(other, "fixture-other"))["errcode"] != 0
        await client.post("/fixtures/bots/fixture-bot/messages", json=message())
        await ws.recv()
        assert (await client.post("/fixtures/bots/fixture-bot/messages", json=message("second"))).status_code == 429
        await response(ws, "kept", True)
        await ws.recv()
        await response(ws, "overflow", True)
        with pytest.raises(ConnectionClosed):
            await ws.recv()
        state = (await client.get("/fixtures/bots/fixture-bot")).json()
        assert [v["content"] for v in state["replies"]] == ["kept"]
        assert not state["connected"]
        assert (await client.post("/fixtures/bots/fixture-bot/messages", json=message())).status_code == 409


@pytest.mark.asyncio
async def test_control_validation_and_concurrent_socket_writes(running):
    base, url = await running()
    async with httpx.AsyncClient(base_url=base) as client, connect(url) as ws:
        await subscribe(ws)
        for body in [{**message(), "secret": "DO-NOT-ECHO"}, {**message(), "chat_type": "unknown"}, {**message(), "text": "界" * 11000}, {**message(), "user_id": "../../escape"}]:
            response = await client.post("/fixtures/bots/fixture-bot/messages", json=body)
            assert response.status_code == 400 and "DO-NOT-ECHO" not in response.text
        values = await asyncio.gather(*(client.post("/fixtures/bots/fixture-bot/messages", json=message(f"request-{i}", message_id=f"message-{i}")) for i in range(10)))
        assert all(v.status_code == 202 for v in values)
        received = [json.loads(await ws.recv()) for _ in range(10)]
        assert {v["headers"]["req_id"] for v in received} == {f"request-{i}" for i in range(10)}
        await ws.send('{"cmd":"ping","headers":{"req_id":"a","req_id":"b"}}')
        with pytest.raises(ConnectionClosed):
            await ws.recv()