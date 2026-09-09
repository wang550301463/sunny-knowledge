"""Retrieval HTTP boundary: no user-supplied principal or unauthenticated knowledge reads."""

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.common.db import create_database
from knowledge_platform.common.observability import install_telemetry
from knowledge_platform.common.security import ServiceSecurity, bearer_token, service_identity

from .clients import Clients
from .config import RetrievalSettings
from .elasticsearch import ElasticIndex
from .models import initialize
from .schemas import RetrievalError, SearchRequest, TimelineRequest, TraverseRequest
from .service import RetrievalService
from .store import Catalog

Token = Annotated[str, Depends(bearer_token)]


def error(status, code, message, headers=None):
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}, headers=headers
    )


def create_app(
    *, settings=None, database=None, authorizer=None, security=None, index=None, runtime=None
):
    config = settings or RetrievalSettings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.service_security = security or ServiceSecurity.from_settings(config)
        if runtime:
            app.state.runtime = runtime
            yield
            return
        app.state.database = database or create_database(config.database_url)
        app.state.authorizer = authorizer or HTTPAuthorizer(config)
        app.state.index = index or ElasticIndex(config)
        await initialize(app.state.database.engine)
        app.state.runtime = RetrievalService(
            config,
            app.state.authorizer,
            Catalog(app.state.database),
            app.state.index,
            Clients(config, app.state.authorizer),
        )
        try:
            yield
        finally:
            if index is None:
                await app.state.index.close()
            if authorizer is None:
                await app.state.authorizer.close()
            if database is None:
                await app.state.database.close()

    app = FastAPI(
        title="Knowledge Retrieval",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.url.path not in {"/healthz", "/readyz"}:
            try:
                caller = service_identity(request)
                allowed = (
                    {"gateway", "agent", "mcp", "graphiti"}
                    if request.url.path.startswith("/api/v1/")
                    else {"agent", "mcp", "graphiti"}
                    if request.url.path.startswith("/internal/v1/")
                    else set()
                )
                if caller not in allowed:
                    raise HTTPException(403, "Service operation not permitted")
            except HTTPException as exc:
                return error(
                    exc.status_code, "invalid_service_identity", str(exc.detail), exc.headers
                )
        return await call_next(request)

    @app.exception_handler(RetrievalError)
    async def retrieval_error(request, exc):
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = {
            401: "unauthenticated",
            403: "forbidden",
            404: "not_found",
            503: "dependency_unavailable",
        }.get(exc.status_code, "request_failed")
        return error(
            exc.status_code,
            code,
            "Authentication, authorization, or dependency request failed",
            exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error(422, "invalid_request", "Request does not match the retrieval API contract")

    @app.exception_handler(SQLAlchemyError)
    async def db_error(request, exc):
        return error(503, "database_unavailable", "Retrieval projection catalog unavailable")

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "service": "retrieval"}

    @app.get("/readyz")
    async def ready(request: Request):
        async with request.app.state.database.session() as session:
            await session.execute(text("SELECT 1"))
        await request.app.state.index.initialize(
            config.retrieval_embedding_dimensions, config.retrieval_embedding_configuration_id
        )
        return {"status": "ready"}

    @app.post("/api/v1/search")
    @app.post("/internal/v1/search")
    async def search(body: SearchRequest, request: Request, token: Token):
        return await request.app.state.runtime.search(token, body)

    @app.post("/api/v1/traverse")
    @app.post("/internal/v1/traverse")
    async def traverse(body: TraverseRequest, request: Request, token: Token):
        return await request.app.state.runtime.traverse(token, body)

    @app.post("/api/v1/timeline")
    @app.post("/internal/v1/timeline")
    async def timeline(body: TimelineRequest, request: Request, token: Token):
        return await request.app.state.runtime.timeline(token, body)

    install_telemetry(app, config)
    return app


app = create_app()
from pydantic import Field, field_validator

from knowledge_platform.common.config import Settings


class RetrievalSettings(Settings):
    service_name: str = "retrieval"
    retrieval_es_url: str = "http://elasticsearch:9200"
    retrieval_es_index: str = "knowledge-fragments-v2"
    retrieval_es_api_key: str = Field(default="", repr=False)
    retrieval_embedding_configuration_id: str = ""
    retrieval_embedding_dimensions: int = Field(default=0, ge=0, le=4096)
    retrieval_rerank_configuration_id: str = ""
    retrieval_oidc_token_url: str = ""
    retrieval_oidc_client_id: str = ""
    retrieval_oidc_client_secret: str = Field(default="", repr=False)
    retrieval_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    retrieval_reconcile_seconds: float = Field(default=60.0, ge=1, le=3600)
    retrieval_worker_timeout_seconds: float = Field(default=240.0, ge=10, le=270)
    retrieval_max_policy_domains: int = Field(default=10000, ge=1, le=50000)
    retrieval_policy_cache_seconds: float = Field(default=5, ge=0, le=30)
    retrieval_graph_timeout_seconds: float = Field(default=5, ge=0.1, le=30)

    @field_validator("retrieval_es_index")
    @classmethod
    def safe_index(cls, value):
        if (
            not value
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in value)
            or value[0] in "-_"
        ):
            raise ValueError("Invalid Elasticsearch index name")
        return value
from types import SimpleNamespace

import pytest

from .test_projection import projection


class Auth:
    def __init__(self):
        self.epoch = 9
        self.subjects = ["user:alice", "group:engineering"]
        self.current = projection()["policies"]
        self.calls = 0

    async def resolve(self, token):
        return SimpleNamespace(id="alice", subjects=self.subjects, auth_epoch=self.epoch)

    async def require(self, token, action, space_id, resource_id=None):
        return SimpleNamespace(allowed=True, auth_epoch=self.epoch)

    async def request(self, method, path, target, token=None, json=None):
        self.calls += 1
        assert path == "/internal/v1/policies/batch"
        result = {
            "auth_epoch": self.epoch,
            "items": [
                {
                    "space_id": r["space_id"],
                    "resource_id": r["resource_id"],
                    "found": True,
                    "policy": next(p for p in self.current if p["resource_id"] == r["resource_id"]),
                }
                for r in json["resources"]
            ],
        }
        return SimpleNamespace(json=lambda: result)


@pytest.mark.asyncio
async def test_sibling_same_domain_cannot_allow_tightened_resource_old_fingerprint():
    from knowledge_platform.retrieval.authorization import AuthorizationGuard, PolicyCache
    from knowledge_platform.retrieval.projection import policy_fingerprint

    first = projection()
    second = projection()
    second["policies"][0] = dict(second["policies"][0], resource_id="page:other")
    auth = Auth()
    auth.current = [dict(p) for p in first["policies"]] + [dict(second["policies"][0])]
    auth.current[0].update(resource_read_subjects=["user:bob"], acl_version=10)
    guard = await AuthorizationGuard.begin(auth, "token", ["engineering"])
    allowed = await PolicyCache().allowed(guard, [first, second])
    assert policy_fingerprint(first["policies"]) not in allowed
    assert policy_fingerprint(second["policies"]) in allowed


@pytest.mark.asyncio
async def test_epoch_change_invalidates_cached_policy_and_prevents_output():
    from knowledge_platform.retrieval.authorization import AuthorizationGuard, PolicyCache
    from knowledge_platform.retrieval.schemas import RetrievalError

    auth = Auth()
    cache = PolicyCache()
    guard = await AuthorizationGuard.begin(auth, "token", ["engineering"])
    assert await cache.allowed(guard, [projection()])
    assert await cache.allowed(guard, [projection()])
    assert auth.calls == 1
    auth.epoch += 1
    with pytest.raises(RetrievalError, match="Authorization changed"):
        await guard.finish()
    auth.current = [
        dict(p, auth_epoch=auth.epoch, resource_read_subjects=["user:bob"]) for p in auth.current
    ]
    guard = await AuthorizationGuard.begin(auth, "token", ["engineering"])
    assert await cache.allowed(guard, [projection()]) == []
    assert auth.calls == 2


@pytest.mark.asyncio
async def test_malformed_or_mixed_epoch_policy_response_fails_closed():
    from knowledge_platform.retrieval.authorization import AuthorizationGuard, PolicyCache
    from knowledge_platform.retrieval.schemas import RetrievalError

    auth = Auth()
    auth.current[0]["auth_epoch"] = 8
    guard = await AuthorizationGuard.begin(auth, "token", ["engineering"])
    with pytest.raises(RetrievalError):
        await PolicyCache().allowed(guard, [projection()])
