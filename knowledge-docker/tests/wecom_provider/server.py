"""Bounded, opt-in WeCom protocol fixture. Never connect a real bot to this service."""

import asyncio
import hashlib
import ipaddress
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")
MAX_FRAME_BYTES = 131072
MAX_TEXT_BYTES = 32768
MAX_RESPONSE_BYTES = 20480
FIXTURE_SECRET = "wecom-protocol-only"


class CapacityError(Exception):
    pass


@dataclass(frozen=True)
class Limits:
    max_bots: int = 32
    max_connections: int = 64
    max_requests: int = 256
    max_replies: int = 256
    max_reply_bytes: int = 8 * 1024 * 1024
    max_injections: int = 10000

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            raise ValueError("Positive fixture limits required")


def key(value):
    return isinstance(value, str) and KEY.fullmatch(value) is not None


def bot_key(value):
    return key(value) and value.startswith("fixture-") and len(value) > 8


def strict_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("Duplicate JSON field")
        out[key] = value
    return out


def invalid_constant(_):
    raise ValueError("Invalid JSON constant")


def decode(raw):
    if len(raw) > MAX_FRAME_BYTES:
        raise ValueError("Fixture frame too large")
    result = json.loads(raw, object_pairs_hook=strict_object, parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError("Object required")
    return result


async def request_body(request):
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > MAX_FRAME_BYTES:
            raise ValueError("Fixture control body too large")
    return decode(raw)


def frame(value):
    if set(value) - {"cmd", "headers", "body"} or not isinstance(value.get("cmd"), str):
        raise ValueError("Invalid fixture command")
    headers = value.get("headers")
    if not isinstance(headers, dict) or set(headers) != {"req_id"} or not key(headers["req_id"]):
        raise ValueError("Invalid fixture request ID")
    return value["cmd"], headers["req_id"], value.get("body")


def ack(request_id, code=0):
    return {"headers": {"req_id": request_id}, "errcode": code, "errmsg": "ok" if not code else "fixture_subscription_denied"}


class Session:
    def __init__(self, ws):
        self.ws = ws
        self.serial = asyncio.Lock()
        self.closed = False
        self.bot_id = None

    async def send(self, payload):
        async with self.serial:
            if self.closed:
                raise ConnectionError("Fixture connection closed")
            await asyncio.wait_for(self.ws.send_json(payload), 2)

    async def close(self, code=1000):
        async with self.serial:
            if not self.closed:
                self.closed = True
                try:
                    await asyncio.wait_for(self.ws.close(code=code), 2)
                except (RuntimeError, ConnectionError, WebSocketDisconnect, TimeoutError):
                    pass


@dataclass
class Bot:
    session: Session | None = None
    connection_count: int = 0
    injected_message_count: int = 0
    injection_attempts: int = 0
    requests: dict = field(default_factory=dict)
    replies: list = field(default_factory=list)
    drop_next_response_ack: bool = False
    dropped_response_ack_count: int = 0

    def snapshot(self, bot_id):
        return {
            "bot_id": bot_id,
            "connected": self.session is not None and not self.session.closed,
            "connection_count": self.connection_count,
            "injected_message_count": self.injected_message_count,
            "replies": list(self.replies),
            "drop_next_response_ack": self.drop_next_response_ack,
            "dropped_response_ack_count": self.dropped_response_ack_count,
        }


class Registry:
    def __init__(self, limits):
        self.limits = limits
        self.lock = asyncio.Lock()
        self.bots = {}
        self.sessions = set()
        self.reply_bytes = 0

    async def admit(self, session):
        async with self.lock:
            if len(self.sessions) >= self.limits.max_connections:
                raise CapacityError
            self.sessions.add(session)

    async def bind(self, session, bot_id):
        async with self.lock:
            if bot_id not in self.bots and len(self.bots) >= self.limits.max_bots:
                raise CapacityError
            state = self.bots.setdefault(bot_id, Bot())
            if state.connection_count >= self.limits.max_injections:
                raise CapacityError
            old = state.session
            state.session = session
            state.connection_count += 1
            session.bot_id = bot_id
            return old

    async def release(self, session):
        async with self.lock:
            self.sessions.discard(session)
            state = self.bots.get(session.bot_id)
            if state and state.session is session:
                state.session = None

    async def record(self, session, request_id, body):
        if not isinstance(body, dict) or set(body) != {"msgtype", "stream"} or body["msgtype"] != "stream":
            raise ValueError("Stream body required")
        stream = body["stream"]
        if not isinstance(stream, dict) or set(stream) != {"id", "content", "finish"}:
            raise ValueError("Stream fields required")
        if not key(stream["id"]) or not isinstance(stream["content"], str) or type(stream["finish"]) is not bool:
            raise ValueError("Invalid stream fields")
        size = len(stream["content"].encode("utf-8"))
        if size > MAX_RESPONSE_BYTES:
            raise ValueError("Oversized stream content")
        async with self.lock:
            state = self.bots[session.bot_id]
            if state.session is not session or session.closed or request_id not in state.requests:
                raise ValueError("Unknown or stale fixture reply")
            if len(state.replies) >= self.limits.max_replies or self.reply_bytes + size > self.limits.max_reply_bytes:
                raise CapacityError
            dropped = state.drop_next_response_ack
            state.drop_next_response_ack = False
            state.dropped_response_ack_count += int(dropped)
            request = state.requests[request_id]
            request["reply_count"] += 1
            state.replies.append({"request_id": request_id, "sequence": len(state.replies) + 1, "request_sequence": request["reply_count"], "stream_id": stream["id"], "finished": stream["finish"], "content": stream["content"], "ack_dropped": dropped})
            self.reply_bytes += size
            return dropped


def create_app(limits=None):
    registry = Registry(limits or Limits())

    @asynccontextmanager
    async def lifespan(_):
        yield
        async with registry.lock:
            sessions = list(registry.sessions)
        await asyncio.gather(*(session.close(1001) for session in sessions))

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def no_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    async def health():
        return {"status": "protocol_fixture", "real_wecom": False}

    @app.get("/fixtures/bots/{bot_id}")
    async def inspect_bot(bot_id: str):
        if not bot_key(bot_id):
            return JSONResponse({"error": "fixture_bot_required"}, status_code=400)
        async with registry.lock:
            return registry.bots.get(bot_id, Bot()).snapshot(bot_id)

    @app.post("/fixtures/bots/{bot_id}/{operation}")
    async def control(bot_id: str, operation: str, request: Request):
        if not bot_key(bot_id):
            return JSONResponse({"error": "fixture_bot_required"}, status_code=400)
        if operation not in {"messages", "disconnect", "ack-policy"}:
            return JSONResponse({"error": "unknown_fixture_operation"}, status_code=404)
        try:
            body = await request_body(request)
            if operation == "messages":
                if set(body) != {"message_id", "request_id", "user_id", "chat_type", "chat_id", "text"}:
                    raise ValueError
                if not all(key(body[name]) for name in ("message_id", "request_id", "user_id")):
                    raise ValueError
                if body["chat_type"] not in {"single", "group"}:
                    raise ValueError
                if body["chat_type"] == "group" and not key(body["chat_id"]):
                    raise ValueError
                if body["chat_type"] == "single" and body["chat_id"] != "":
                    raise ValueError
                if not isinstance(body["text"], str) or not body["text"].strip() or len(body["text"].encode("utf-8")) > MAX_TEXT_BYTES:
                    raise ValueError
            elif operation == "ack-policy":
                if set(body) != {"drop_next_response_ack"} or type(body["drop_next_response_ack"]) is not bool:
                    raise ValueError
            elif body:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            return JSONResponse({"error": "invalid_fixture_control"}, status_code=400)
        async with registry.lock:
            state = registry.bots.get(bot_id)
            if state is None:
                return JSONResponse({"error": "fixture_bot_not_connected"}, status_code=409)
            session = state.session
            if operation == "ack-policy":
                state.drop_next_response_ack = body["drop_next_response_ack"]
                return {"status": "configured", "drop_next_response_ack": state.drop_next_response_ack}
            if operation == "messages":
                if session is None or session.closed:
                    return JSONResponse({"error": "fixture_bot_not_connected"}, status_code=409)
                fingerprint = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                previous = state.requests.get(body["request_id"])
                if previous and previous["fingerprint"] != fingerprint:
                    return JSONResponse({"error": "fixture_request_conflict"}, status_code=409)
                if (previous is None and len(state.requests) >= registry.limits.max_requests) or state.injection_attempts >= registry.limits.max_injections:
                    return JSONResponse({"error": "fixture_capacity_reached"}, status_code=429)
                state.requests.setdefault(body["request_id"], {"fingerprint": fingerprint, "reply_count": 0})
                state.injection_attempts += 1
                sequence = state.injection_attempts
        if operation == "disconnect":
            if session:
                await session.close(1012)
            return {"status": "disconnected"}
        callback = {"cmd": "aibot_msg_callback", "headers": {"req_id": body["request_id"]}, "body": {"msgid": body["message_id"], "aibotid": bot_id, "chattype": body["chat_type"], "chatid": body["chat_id"], "from": {"userid": body["user_id"]}, "msgtype": "text", "text": {"content": body["text"]}}}
        try:
            await session.send(callback)
        except (ConnectionError, RuntimeError, WebSocketDisconnect, TimeoutError):
            await session.close(1011)
            return JSONResponse({"error": "fixture_delivery_unknown"}, status_code=503)
        async with registry.lock:
            state.injected_message_count += 1
        return JSONResponse({"status": "injected", "sequence": sequence}, status_code=202)

    @app.websocket("/")
    async def socket_endpoint(ws: WebSocket):
        # The worker must share the fixture namespace and explicitly use loopback.
        if ws.client is None or not ipaddress.ip_address(ws.client.host).is_loopback:
            await ws.close(code=1008)
            return
        session = Session(ws)
        try:
            await registry.admit(session)
            await ws.accept()
            incoming = await asyncio.wait_for(ws.receive(), 3)
            if incoming["type"] != "websocket.receive" or incoming.get("text") is None:
                raise ValueError
            command, request_id, body = frame(decode(incoming["text"].encode("utf-8")))
            if command != "aibot_subscribe" or not isinstance(body, dict) or set(body) != {"bot_id", "secret"} or not bot_key(body["bot_id"]) or body["secret"] != FIXTURE_SECRET:
                await session.send(ack(request_id, 40014))
                raise ValueError
            try:
                old = await registry.bind(session, body["bot_id"])
            except CapacityError:
                await session.send(ack(request_id, 45009))
                raise
            if old is not None and old is not session and not old.closed:
                try:
                    await old.send({"cmd": "aibot_event_callback", "headers": {"req_id": "fixture-displacement"}, "body": {"event": {"eventtype": "disconnected_event"}}})
                except (ConnectionError, RuntimeError, WebSocketDisconnect, TimeoutError):
                    pass
                await old.close()
            await session.send(ack(request_id))
            while True:
                incoming = await asyncio.wait_for(ws.receive(), 90)
                if incoming["type"] == "websocket.disconnect":
                    break
                if incoming.get("text") is None:
                    raise ValueError
                command, request_id, body = frame(decode(incoming["text"].encode("utf-8")))
                if command == "ping" and body in (None, {}):
                    await session.send(ack(request_id))
                elif command == "aibot_respond_msg":
                    if not await registry.record(session, request_id, body):
                        await session.send(ack(request_id))
                else:
                    raise ValueError
        except CapacityError:
            await session.close(1013)
        except (ValueError, TypeError, KeyError, ConnectionError, WebSocketDisconnect, RuntimeError, TimeoutError):
            await session.close(1008)
        finally:
            await registry.release(session)
            await session.close()

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8765, access_log=False, log_config=None, log_level="critical", proxy_headers=False, ws="websockets-sansio", ws_max_size=MAX_FRAME_BYTES, ws_max_queue=16)