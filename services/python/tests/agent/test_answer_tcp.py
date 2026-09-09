"""Real TCP provider + PostgreSQL + real Agent SSE; protocol simulation, not model-quality acceptance."""
import asyncio
import json
import socket
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from sqlalchemy import select, update

from knowledge_platform.agent.models import Event, Run, now

from .model_protocol import is_final, model_response
from .test_final_streaming import line
from .test_runtime import setup_run
from .test_transport import RoutingTransport


def frame(kind, value):
    return ("event: " + kind + "\ndata: " + json.dumps({"type": kind, **value}, ensure_ascii=False) + "\n\n").encode()


@asynccontextmanager
async def final_provider(api, *, tail="valid"):
    state = SimpleNamespace(
        partial_sent=asyncio.Event(), allow_first=asyncio.Event(), first_sent=asyncio.Event(),
        release=asyncio.Event(), closed=asyncio.Event(), ended=False, requests=[], final_calls=0,
    )
    handlers = []

    async def handle(reader, writer):
        handlers.append(asyncio.current_task())
        final = False
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            headers = {line.split(":", 1)[0].lower(): line.split(":", 1)[1].strip() for line in header.decode().split("\r\n")[1:] if ":" in line}
            assert "authorization" not in headers and "x-service-token" in headers
            if header.startswith(b"GET "):
                content = json.dumps({"configuration_id": "model1", "capability": "chat", "state": "active", "max_output_tokens": 8192, "max_input_chars": 131072}).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: " + str(len(content)).encode() + b"\r\n\r\n" + content)
                await writer.drain()
                return
            body = json.loads(await reader.readexactly(int(headers["content-length"])))
            state.requests.append(body)
            final = is_final(body)
            if not final:
                assert body["tools"]
                request = httpx.Request("POST", "http://llm/internal/v1/chat", json=body, headers=headers)
                response = await api.state.handle(request)
                content = await response.aread()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\nContent-Length: " + str(len(content)).encode() + b"\r\n\r\n" + content)
                await writer.drain()
                return
            state.final_calls += 1
            assert body["tools"] == []
            tools = [json.loads(m["content"]) for m in body["messages"] if m["role"] == "tool"]
            cid = tools[0]["citations"][0]["id"] if tools else None
            first = line("fact", "已验证：支付调用账本🧭", [cid]) if cid else line("gap", "没有检索证据🧭")
            second = line("inference", "可能影响账本", [cid]) if cid else line("gap", "分析未完成")
            suffix = {
                "valid": second + '{"done":true}\n',
                "invalid": line("fact", "FORGED_PRIVATE", ["invented"]),
                "truncated": '{"kind":"fact","text":"UNFINISHED_SECRET',
                "missing_done": second,
                "mismatch": second + '{"done":true}\n',
                "tool_delta": "",
                "length": second + '{"done":true}\n',
            }[tail]
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")

            async def send(payload, fragmented=False):
                pieces = [payload[i:i+1] for i in range(len(payload))] if fragmented else [payload]
                for piece in pieces:
                    writer.write(format(len(piece), "x").encode() + b"\r\n" + piece + b"\r\n")
                await writer.drain()

            # Split both JSON content across events and multibyte UTF-8 across HTTP chunks.
            await send(frame("content_delta", {"delta": first[:-1]}), fragmented=True)
            state.partial_sent.set()
            await state.allow_first.wait()
            await send(frame("content_delta", {"delta": "\n"}), fragmented=True)
            state.first_sent.set()
            disconnect = asyncio.create_task(reader.read())
            release = asyncio.create_task(state.release.wait())
            try:
                done, _ = await asyncio.wait([disconnect, release], return_when=asyncio.FIRST_COMPLETED)
                if disconnect in done:
                    return
            finally:
                disconnect.cancel()
                release.cancel()
                await asyncio.gather(disconnect, release, return_exceptions=True)
            if tail == "tool_delta":
                await send(frame("tool_call_delta", {"delta": {"name": "search"}}))
            else:
                await send(frame("content_delta", {"delta": suffix}), fragmented=True)
            await send(frame("completed", {
                "configuration_id": "model1", "invocation_id": "actual-final",
                "content": first + suffix + ("MISMATCH" if tail == "mismatch" else ""),
                "tool_calls": [], "usage": {"total_tokens": 7},
                "finish_reason": "length" if tail == "length" else "stop",
            }))
            writer.write(b"0\r\n\r\n")
            await writer.drain()
            state.ended = True
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if final:
                state.closed.set()
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    old_url = api.settings.llm_url
    api.settings.llm_url = "http://127.0.0.1:" + str(server.sockets[0].getsockname()[1])
    old = api.app.state.service.clients.client
    async with httpx.AsyncClient(transport=RoutingTransport(api.state.handle), trust_env=False) as http:
        api.app.state.service.clients.client = http
        try:
            yield state
        finally:
            api.app.state.service.clients.client = old
            api.settings.llm_url = old_url
            server.close()
            await server.wait_closed()
            for task in handlers:
                task.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)


@asynccontextmanager
async def tcp_api(api):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    server = uvicorn.Server(uvicorn.Config(api.app, lifespan="off", access_log=False, log_config=None, log_level="critical", timeout_graceful_shutdown=1))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(3):
            while not server.started:
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", headers=api.headers(), trust_env=False) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 4)
        sock.close()


async def prefix(api, provider, run_id):
    await asyncio.wait_for(provider.partial_sent.wait(), 5)
    pending = (await api.client.get("/api/v1/runs/" + run_id)).json()
    assert pending["status"] == "running" and pending["answer"] is None
    provider.allow_first.set()
    async with asyncio.timeout(5):
        while True:
            current = (await api.client.get("/api/v1/runs/" + run_id)).json()
            if current.get("answer"):
                return current
            await asyncio.sleep(0.01)


async def test_real_tcp_first_block_is_readable_and_sse_resumable_before_provider_finishes(api):
    async with final_provider(api) as provider, tcp_api(api) as client:
        _, _, result, _ = await setup_run(api)
        run_id = result.json()["id"]
        task = asyncio.create_task(api.app.state.worker.run_once())
        try:
            current = await prefix(api, provider, run_id)
            assert current["answer"]["facts"][0]["text"] == "已验证：支付调用账本🧭"
            assert current["answer_complete"] is False and not provider.ended and not task.done()
            async with client.stream("GET", "/api/v1/runs/" + run_id + "/events") as response:
                assert response.status_code == 200
                async with asyncio.timeout(5):
                    async for record in response.aiter_lines():
                        if record.startswith("data: "):
                            event = json.loads(record[6:])
                            if event["type"] == "answer_block":
                                assert event["data"] == {"index": 0, "kind": "fact", **current["answer"]["facts"][0]}
                                cursor = event["seq"]
                                break
            # A consumer disconnect leaves provider/worker alive. Durable GET has the prefix.
            assert not task.done() and not provider.ended
            live = (await client.get("/api/v1/runs/" + run_id)).json()
            assert live["answer"] == current["answer"]
            provider.release.set()
            await asyncio.wait_for(task, 5)
            final = (await client.get("/api/v1/runs/" + run_id)).json()
            assert final["status"] == "completed" and final["answer_complete"]
            assert final["rounds"] == 3 and provider.final_calls == 1
            replay = await client.get("/api/v1/runs/" + run_id + "/events", headers={"Last-Event-ID": str(cursor)})
            events = [json.loads(row[6:]) for row in replay.text.splitlines() if row.startswith("data: ")]
            assert all(e["seq"] > cursor for e in events)
            assert [e["data"]["index"] for e in events if e["type"] == "answer_block"] == [1]
            assert events[-1]["data"]["run"]["answer"] == final["answer"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("tail,code", [
    ("invalid", "unsupported_citation"), ("truncated", "incomplete_answer_stream"),
    ("missing_done", "incomplete_answer_stream"), ("mismatch", "invalid_final_model_stream"),
    ("tool_delta", "final_tools_forbidden"), ("length", "model_output_limit"),
])
async def test_real_tcp_bad_suffix_retains_only_verified_prefix_as_incomplete(api, tail, code):
    async with final_provider(api, tail=tail) as provider:
        _, _, result, _ = await setup_run(api)
        run_id = result.json()["id"]
        task = asyncio.create_task(api.app.state.worker.run_once())
        try:
            first = await prefix(api, provider, run_id)
            provider.release.set()
            await asyncio.wait_for(task, 5)
            value = (await api.client.get("/api/v1/runs/" + run_id)).json()
            assert value["status"] == "partial" and value["error_code"] == code
            assert value["answer_complete"] is False
            assert value["answer"]["facts"] == first["answer"]["facts"]
            assert "FORGED_PRIVATE" not in json.dumps(value) and "UNFINISHED_SECRET" not in json.dumps(value)
            async with api.database.session() as session:
                row = await session.get(Run, run_id)
                assert row.encrypted_token is None
                events = list(await session.scalars(select(Event).where(Event.run_id == run_id)))
                assert "FORGED_PRIVATE" not in json.dumps([e.data for e in events])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("action", ["cancel", "restart", "revoke", "deadline"])
async def test_real_tcp_prefix_cancel_restart_revocation_and_deadline(api, action):
    async with final_provider(api) as provider:
        _, _, result, _ = await setup_run(api, config={"space_ids": ["engineering"], "model_configuration_id": "model1", "budget": {"seconds": 4 if action == "deadline" else 180}})
        run_id = result.json()["id"]
        task = asyncio.create_task(api.app.state.worker.run_once())
        try:
            current = await prefix(api, provider, run_id)
            if action == "cancel":
                response = await api.client.post("/api/v1/runs/" + run_id + "/cancel", json={})
                assert response.json()["status"] == "cancelled"
            elif action == "restart":
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                async with api.database.session() as session, session.begin():
                    await session.execute(update(Run).where(Run.id == run_id).values(lease_until=now() - timedelta(seconds=1)))
                await api.app.state.worker.run_once()
            elif action == "revoke":
                api.state.source_allowed = False
                api.state.epoch += 1
                provider.release.set()
            await asyncio.wait_for(task, 7) if action != "restart" else asyncio.sleep(0)
            value = (await api.client.get("/api/v1/runs/" + run_id)).json()
            if action == "revoke":
                assert value["content_hidden"] and "已验证" not in json.dumps(value, ensure_ascii=False)
                replay = await api.client.get("/api/v1/runs/" + run_id + "/events")
                assert "已验证" not in replay.text and "snapshot1" not in replay.text
            else:
                assert value["answer"] == current["answer"] and not value["answer_complete"]
                assert value["status"] == ("cancelled" if action == "cancel" else "partial")
                assert value["error_code"] == {"cancel": "cancelled_by_user", "restart": "model_interrupted", "deadline": "run_deadline"}[action]
            await asyncio.wait_for(provider.closed.wait(), 3)
            assert provider.final_calls == 1
            async with api.database.session() as session:
                assert (await session.get(Run, run_id)).encrypted_token is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)