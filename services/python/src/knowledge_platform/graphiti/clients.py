"""Workload HTTP contracts; graph never borrows another service's database or model key."""

from urllib.parse import quote

import httpx

from .projection import digest
from .schemas import unavailable


def safe_json(response):
    try:
        value = response.json()
        if not isinstance(value, dict):
            raise TypeError
        return value
    except (TypeError, ValueError):
        raise unavailable(
            "invalid_dependency_response", "Internal graph dependency response invalid"
        ) from None


class Clients:
    def __init__(self, settings, authorizer):
        self.settings, self.auth = settings, authorizer

    async def call(self, method, path, target, token=None, body=None):
        return safe_json(await self.auth.request(method, path, target, token, body))

    async def projection(self, page_id, revision_id):
        return await self.call(
            "GET",
            f"/internal/v1/projections/pages/{quote(page_id, safe='')}/revisions/{quote(revision_id, safe='')}",
            "knowledge",
        )

    async def pages(self, token, records, request, guard, *, inspection=False):
        keys = sorted({(r["graph"].page_id, r["graph"].revision_id) for r in records})
        permitted = set()
        for start in range(0, len(keys), 100):
            batch = keys[start : start + 100]
            data = await self.call(
                "POST",
                "/internal/v1/pages/authorize",
                "knowledge",
                token,
                {
                    "pages": [{"page_id": p, "revision_id": r} for p, r in batch],
                    "include_historical": request.include_historical,
                    "as_of": request.as_of.isoformat() if request.as_of else None,
                },
            )
            try:
                decisions = data["decisions"]
                if len(decisions) != len(batch) or {
                    (d["page_id"], d["revision_id"]) for d in decisions
                } != set(batch):
                    raise ValueError
                for decision in decisions:
                    if type(decision["allowed"]) is not bool:
                        raise ValueError
                    if (
                        decision.get("authorized" if inspection else "allowed") is True
                        and decision["space_id"] in request.space_ids
                    ):
                        permitted.add((decision["page_id"], decision["revision_id"]))
            except (KeyError, TypeError, ValueError):
                raise unavailable(
                    "invalid_authorization_response",
                    "Canonical graph authorization response invalid",
                ) from None
            await guard.finish()
        return {
            r["projection_id"]
            for r in records
            if (r["graph"].page_id, r["graph"].revision_id) in permitted
        }

    async def evidence(self, token, refs, guard):
        permitted = set()
        for start in range(0, len(refs), 100):
            batch = refs[start : start + 100]
            data = await self.call(
                "POST", "/internal/v1/evidence/authorize", "knowledge", token, {"evidence": batch}
            )
            try:
                decisions = data["decisions"]
                if len(decisions) != len(batch):
                    raise ValueError
                for original, decision in zip(batch, decisions, strict=True):
                    if decision["evidence"] != original or type(decision["allowed"]) is not bool:
                        raise ValueError
                    if decision["allowed"]:
                        permitted.add(digest(original))
            except (KeyError, TypeError, ValueError):
                raise unavailable(
                    "invalid_authorization_response", "Canonical graph evidence response invalid"
                ) from None
            await guard.finish()
        return permitted


class MachineTokens:
    def __init__(self, settings, client=None):
        self.settings, self.owned = settings, client is None
        self.client = client or httpx.AsyncClient(
            timeout=20, follow_redirects=False, trust_env=False
        )

    async def close(self):
        if self.owned:
            await self.client.aclose()

    async def token(self):
        s = self.settings
        if not all(
            (s.graphiti_oidc_token_url, s.graphiti_oidc_client_id, s.graphiti_oidc_client_secret)
        ):
            raise unavailable(
                "worker_authorization_required", "Configure and grant the graph service account"
            )
        try:
            response = await self.client.post(
                s.graphiti_oidc_token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": s.graphiti_oidc_client_id,
                    "client_secret": s.graphiti_oidc_client_secret,
                    "scope": "knowledge:read",
                },
                follow_redirects=False,
            )
            if response.status_code != 200:
                raise unavailable(
                    "worker_authorization_required", "Graph service account authentication failed"
                )
            value = response.json()
            token = value["access_token"]
            if not isinstance(token, str) or not token or value["token_type"].lower() != "bearer":
                raise ValueError
            return token
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise unavailable(
                "worker_authorization_required", "Graph service account identity unavailable"
            ) from None
"""Bound request bytes even when a caller omits Content-Length or sends chunks."""

from fastapi.responses import JSONResponse


class BodyLimit:
    def __init__(self, app, maximum=262144):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        parts, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            data = message.get("body", b"")
            size += len(data)
            if size > self.maximum:
                await JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "request_too_large",
                            "message": "Graph request exceeds the byte limit",
                        }
                    },
                )(scope, receive, send)
                return
            parts.append(data)
            if not message.get("more_body", False):
                break
        body = b"".join(parts)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
"""Graphiti HTTP boundary: no user-supplied principal or unauthenticated knowledge reads."""

import logging
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

from .backend import Neo4jGraph
from .clients import Clients
from .config import GraphitiSettings
from .http import BodyLimit
from .models import initialize
from .schemas import GraphError, TraverseRequest
from .service import GraphService
from .store import Catalog

Token = Annotated[str, Depends(bearer_token)]


def error(status, code, message, headers=None):
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}, headers=headers
    )


def create_app(
    *, settings=None, database=None, authorizer=None, security=None, backend=None, runtime=None
):
    config = settings or GraphitiSettings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.service_security = security or ServiceSecurity.from_settings(config)
        if runtime:
            app.state.runtime = runtime
            yield
            return
        app.state.database = database or create_database(config.database_url)
        app.state.authorizer = authorizer or HTTPAuthorizer(config)
        app.state.index = backend or Neo4jGraph(config)
        await initialize(app.state.database.engine)
        try:
            await app.state.index.initialize()
        except GraphError:
            logging.getLogger(__name__).warning(
                "Graph projection is unavailable; traversal will report degradation"
            )
        app.state.runtime = GraphService(
            config,
            app.state.authorizer,
            Catalog(app.state.database),
            app.state.index,
            Clients(config, app.state.authorizer),
        )
        try:
            yield
        finally:
            if backend is None:
                await app.state.index.close()
            if authorizer is None:
                await app.state.authorizer.close()
            if database is None:
                await app.state.database.close()

    app = FastAPI(
        title="Knowledge Graphiti",
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
                    {"gateway", "agent", "mcp", "retrieval"}
                    if request.url.path.startswith("/api/v1/")
                    else {"agent", "mcp", "retrieval"}
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

    @app.exception_handler(GraphError)
    async def graphiti_error(request, exc):
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
        return error(422, "invalid_request", "Request does not match the graphiti API contract")

    @app.exception_handler(SQLAlchemyError)
    async def db_error(request, exc):
        return error(503, "database_unavailable", "Graphiti projection catalog unavailable")

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "service": "graphiti"}

    @app.get("/readyz")
    async def ready(request: Request):
        async with request.app.state.database.session() as session:
            await session.execute(text("SELECT 1"))
        await request.app.state.index.ready()
        return {"status": "ready"}

    @app.post("/api/v1/graph/traverse")
    @app.post("/internal/v1/traverse")
    async def traverse(body: TraverseRequest, request: Request, token: Token):
        return await request.app.state.runtime.traverse(token, body)

    app.add_middleware(BodyLimit)
    install_telemetry(app, config)
    return app


app = create_app()
"""Owned durable outbox consumer and explicit canonical-to-Graphiti rebuild."""

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.common.db import create_database

from .authorization import AuthorizationGuard
from .backend import Neo4jGraph
from .clients import Clients, MachineTokens
from .config import GraphitiSettings
from .models import initialize
from .projection import compile_graph, digest
from .schemas import GraphError, TraverseRequest, unavailable
from .store import Catalog

logger = logging.getLogger(__name__)


class ProjectionWorker:
    def __init__(self, settings, authorizer, catalog, backend, clients, tokens):
        self.settings, self.auth, self.catalog, self.backend, self.clients, self.tokens = (
            settings,
            authorizer,
            catalog,
            backend,
            clients,
            tokens,
        )

    async def project(self, page_id, revision_id, event_id=None, *, force=False):
        async with asyncio.timeout(self.settings.graphiti_worker_timeout_seconds):
            value = await self.clients.projection(page_id, revision_id)
            if value.get("page_id") != page_id or value.get("revision_id") != revision_id:
                raise unavailable(
                    "invalid_projection", "Canonical projection identity is inconsistent"
                )
            graph = compile_graph(value)
            token = await self.tokens.token()
            guard = await AuthorizationGuard.begin(self.auth, token, [graph.space_id])
            guard.epoch(graph.acl_epoch)
            instant = datetime.now(UTC)
            if graph.valid_from is not None and instant < graph.valid_from:
                instant = graph.valid_from
            if graph.valid_until is not None and instant >= graph.valid_until:
                instant = graph.valid_until - timedelta(microseconds=1)
            request = TraverseRequest(
                space_ids=[graph.space_id],
                seed_fragment_ids=["projection"],
                include_historical=True,
                as_of=instant,
            )

            if await self.clients.pages(
                token,
                [{"projection_id": "candidate", "graph": graph}],
                request,
                guard,
                inspection=True,
            ) != {"candidate"}:
                raise unavailable(
                    "worker_authorization_required",
                    "Graph service account cannot read this revision",
                )
            await guard.finish()

            async def authorized_write(compiled, generation):
                records = [{"projection_id": "candidate", "graph": compiled}]
                if await self.clients.pages(token, records, request, guard, inspection=True) != {
                    "candidate"
                }:
                    raise unavailable(
                        "worker_authorization_required",
                        "Graph service account cannot read this revision",
                    )
                await guard.finish()
                projection_id = await self.backend.write(compiled, generation)
                if await self.clients.pages(token, records, request, guard, inspection=True) != {
                    "candidate"
                }:
                    raise unavailable(
                        "worker_authorization_required",
                        "Graph service account lost revision access",
                    )
                await guard.finish()
                return projection_id

            return await self.catalog.project(
                graph,
                authorized_write,
                event_id=event_id,
                exists=None if force else self.backend.exists,
            )

    async def once(self):
        leased = await self.clients.call(
            "POST",
            "/internal/v1/outbox/lease",
            "knowledge",
            body={"consumer": "graphiti", "limit": 1, "lease_seconds": 300},
        )
        try:
            items = leased["items"]
            if not isinstance(items, list) or len(items) > 1:
                raise ValueError
            for event in items:
                await self.project(event["page_id"], event["revision_id"], event["id"])
                await self.clients.call(
                    "POST",
                    f"/internal/v1/outbox/{quote(event['id'], safe='')}/ack",
                    "knowledge",
                    body={"lease_token": event["lease_token"]},
                )
        except (KeyError, TypeError, ValueError):
            raise unavailable(
                "invalid_outbox_response", "Knowledge outbox response is invalid"
            ) from None
        return bool(items)

    async def rebuild(self):
        key = digest([self.settings.graphiti_namespace, self.settings.graphiti_neo4j_database])
        async with self.catalog.rebuild_lock(key):
            state = await self.catalog.rebuild_state(key)
            cursor = state["cursor"]
            seen = set()
            while True:
                parameters = {"limit": 100}
                if cursor:
                    parameters["cursor"] = cursor
                page = await self.clients.call(
                    "GET",
                    "/internal/v1/projections/revisions?" + urlencode(parameters),
                    "knowledge",
                )
                try:
                    items = page["items"]
                    next_cursor = page.get("next_cursor")
                    if (
                        not isinstance(items, list)
                        or len(items) > 100
                        or (
                            next_cursor is not None
                            and (
                                not isinstance(next_cursor, str)
                                or not next_cursor
                                or len(next_cursor) > 4096
                            )
                        )
                    ):
                        raise ValueError
                    if len({(r["page_id"], r["revision_id"]) for r in items}) != len(items):
                        raise ValueError
                    for row in items:
                        if (
                            type(row["version"]) is not int
                            or row["version"] < 1
                            or any(
                                not isinstance(row[k], str) or not row[k]
                                for k in ("page_id", "revision_id")
                            )
                        ):
                            raise ValueError
                    if next_cursor and (not items or next_cursor == cursor or next_cursor in seen):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    raise unavailable(
                        "invalid_rebuild_response", "Canonical revision enumeration is invalid"
                    ) from None
                for row in items:
                    await self.project(row["page_id"], row["revision_id"], force=True)
                await self.catalog.rebuild_checkpoint(
                    key, next_cursor, len(items), next_cursor is None
                )
                if next_cursor is None:
                    return
                seen.add(next_cursor)
                cursor = next_cursor

    async def reconcile(self):
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.graphiti_reconcile_seconds)
        for row in await self.catalog.due(cutoff, limit=20):
            try:
                await self.project(row["page_id"], row["revision_id"])
            except (GraphError, HTTPException, SQLAlchemyError, TimeoutError) as error:
                code = error.code if isinstance(error, GraphError) else "dependency_unavailable"
                await self.catalog.defer(row["revision_id"], code)


async def run(*, rebuild=False):
    settings = GraphitiSettings()
    database = create_database(settings.database_url)
    auth = HTTPAuthorizer(settings)
    backend = Neo4jGraph(settings)
    tokens = MachineTokens(settings)
    await initialize(database.engine)
    worker = ProjectionWorker(
        settings, auth, Catalog(database), backend, Clients(settings, auth), tokens
    )
    try:
        if rebuild:
            await backend.initialize()
            await worker.rebuild()
            return
        initialized = False
        while True:
            try:
                if not initialized:
                    await backend.initialize()
                    initialized = True
                busy = await worker.once()
                await worker.reconcile()
                if not busy:
                    await asyncio.sleep(settings.graphiti_poll_seconds)
            except (GraphError, HTTPException, SQLAlchemyError, TimeoutError):
                logger.warning("Graph projection attempt failed; delivery remains retryable")
                await asyncio.sleep(settings.graphiti_poll_seconds)
    finally:
        await tokens.close()
        await backend.close()
        await auth.close()
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Authorized Graphiti projection consumer")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Resume or start canonical revision enumeration, then exit",
    )
    arguments = parser.parse_args()
    try:
        asyncio.run(run(rebuild=arguments.rebuild))
    except (GraphError, HTTPException, SQLAlchemyError, TimeoutError):
        logger.error("Graph worker failed; consult service readiness and private projection status")
        raise SystemExit(1) from None
