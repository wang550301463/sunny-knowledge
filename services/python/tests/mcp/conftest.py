import asyncio
import json
import socket
import time
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from knowledge_platform.common.security import ServiceSecurity


@pytest.fixture
def keys():
    private, public = {}, {}
    for name in ("mcp", "gateway", "auth", "knowledge", "retrieval", "agent"):
        key = Ed25519PrivateKey.generate()
        private[name] = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        public[name] = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return {name: ServiceSecurity(name, private[name], public) for name in private}


class Domains:
    def __init__(self, keys, resource):
        self.keys, self.resource = keys, resource
        self.calls = []
        self.epoch = 1
        self.denied = set()
        self.unavailable = False
        self.modify_identity = {}
        self.payload = {"items": [], "evidence": [], "graph": {"nodes": [], "edges": [], "paths": [], "degraded": []}, "degraded": [], "gaps": ["no_authorized_evidence"], "auth_epoch": 1, "as_of": "2026-09-08T00:00:00Z", "known_at": None, "time_basis": "source_revision_and_knowledge_time", "deployment_state": "unknown_without_deployment_evidence"}
        self.after_search = None
        self.waiting, self.cancelled = asyncio.Event(), asyncio.Event()
        self.slow = False

    async def handle(self, request):
        target = request.url.host
        assert self.keys[target].verify(request.headers["x-service-token"]) == "mcp"
        token = request.headers["authorization"].removeprefix("Bearer ")
        body = json.loads(request.content) if request.content else None
        self.calls.append((target, request.method, request.url.path, token, body))
        if self.unavailable:
            return httpx.Response(503, json={"private": "never expose upstream body"})
        if target == "auth":
            if token in self.denied:
                return httpx.Response(403, json={"error": "disabled"})
            if token not in {"alice", "bob", "alice-feedback", "alice-refresh"}:
                return httpx.Response(401, json={"error": "invalid"})
            return httpx.Response(200, json={"principal": {"id": token.split('-')[0], "subjects": ["user:"+token.split('-')[0]], "permissions": [], "auth_epoch": self.epoch}, "scopes": ["knowledge:read"] + (["knowledge:feedback"] if token.endswith("feedback") else []), "audiences": [self.resource, "knowledge-api"], "issuer": "http://localhost:18180/idp/realms/knowledge", "client_id": "knowledge-mcp", "expires_at": int(time.time())+300, **self.modify_identity})
        if target == "retrieval":
            if self.slow:
                self.waiting.set()
                try:
                    await asyncio.sleep(60)
                finally:
                    self.cancelled.set()
            result = httpx.Response(200, json=self.payload)
            if self.after_search:
                self.after_search()
            return result
        if target == "knowledge":
            if request.url.path == "/internal/v1/evidence/authorize":
                return httpx.Response(200, json={"decisions": [{"evidence": {**r, "valid_from": None, "valid_until": None}, "allowed": True, "space_id": "space", "excerpt": "exact original", "sha256": "a"*64} for r in body["evidence"]]})
            return httpx.Response(200, json={"id": "page", "space_id": "space", "current_revision": None, "revision": None})
        if target == "agent":
            if request.url.path.endswith("/feedback"):
                return httpx.Response(201, json={"id": "feedback", "run_id": body["run_id"]})
            if request.url.path.endswith("/sessions"):
                return httpx.Response(201, json={"id": "session", "title": body["title"], "created_at": "2026-09-08T00:00:00Z"})
            return httpx.Response(200, json={"id": "run", "session_id": "session", "status": "completed", "event_seq": 3, "content_hidden": False, "answer": {"facts": [], "inferences": [], "gaps": ["No evidence"]}, "citations": []})
        raise AssertionError(target)


@asynccontextmanager
async def serve(app, sock):
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(.01)
        assert server.started
        yield
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 5)
        except TimeoutError:
            server.force_exit = True
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest_asyncio.fixture
async def runtime(keys):
    from knowledge_platform.mcp.app import create_app
    from knowledge_platform.mcp.config import MCPSettings

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    resource = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
    domains = Domains(keys, resource)
    settings = MCPSettings(mcp_resource_url=resource, mcp_issuer_url="http://localhost:18180/idp/realms/knowledge", mcp_allowed_client_ids=["knowledge-mcp"], request_timeout=3)
    async with httpx.AsyncClient(transport=httpx.MockTransport(domains.handle)) as upstream:
        app = create_app(settings=settings, security=keys["mcp"], client=upstream)
        async with serve(app, sock):
            async with httpx.AsyncClient(base_url=resource.removesuffix("/mcp"), timeout=5, headers={"X-Service-Token": keys["gateway"].issue("mcp")}) as client:
                yield client, domains, keys, resource
