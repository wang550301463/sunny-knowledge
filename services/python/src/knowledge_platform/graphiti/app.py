"""Graphiti service HTTP boundary: authorized adjacency traversal over the
projected graph. Reconstructed 2026-09-09 — traverse is the primary route;
projection status routes restore incrementally."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.common.db import create_database
from knowledge_platform.common.observability import install_telemetry
from knowledge_platform.common.security import bearer_token

from .backend import Neo4jGraph
from .clients import Clients
from .config import GraphitiSettings
from .schemas import GraphError, TraverseRequest
from .service import GraphService
from .store import Catalog


def error(status, code, message, headers=None):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}}, headers=headers)


def create_app(config: GraphitiSettings | None = None):
    config = config or GraphitiSettings.from_env("graphiti")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = config
        app.state.database = create_database(config.database_url)
        authorizer = HTTPAuthorizer(config)
        app.state.authorizer = authorizer
        app.state.clients = Clients(config, authorizer)
        app.state.catalog = Catalog(app.state.database)
        app.state.backend = Neo4jGraph(config)
        await app.state.backend.initialize()
        app.state.service = GraphService(config, authorizer, app.state.catalog, app.state.backend, app.state.clients)
        try:
            yield
        finally:
            await app.state.backend.close()
            await app.state.database.dispose()

    app = FastAPI(title="graphiti", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(GraphError)
    async def graph_error(_: Request, exc: GraphError):
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return error(exc.status_code, "graph_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError):
        return error(422, "invalid_request", "Request validation failed")

    @app.get("/healthz")
    @app.get("/readyz")
    async def health():
        return {"status": "ok", "service": "graphiti"}

    @app.post("/internal/v1/traverse")
    async def traverse(body: TraverseRequest, request: Request):
        token = bearer_token(request)
        return await request.app.state.service.traverse(token, body)

    install_telemetry(app, config)
    return app


app = create_app()
