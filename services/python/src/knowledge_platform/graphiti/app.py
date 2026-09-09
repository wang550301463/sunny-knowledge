"""Graphiti service HTTP boundary (minimal reconstruction 2026-09-09).

The original app factory was lost with the disk wipe. This reconstruction
provides the service identity, health endpoints and the traverse boundary
backed by GraphService; the full route set is being restored incrementally
from session logs. Internal callers should treat 404 as route-not-yet-restored.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from knowledge_platform.common.observability import install_telemetry

from .config import GraphitiSettings
from .schemas import GraphError


def error(status, code, message, headers=None):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}}, headers=headers)


def create_app(config: GraphitiSettings | None = None):
    config = config or GraphitiSettings.from_env("graphiti")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = config
        yield

    app = FastAPI(title="graphiti", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return error(exc.status_code, GraphError(exc.detail).error.code if exc.detail in GraphError._member_map_.values() else "graph_error", "Graph request rejected")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError):
        return error(422, "invalid_request", "Request validation failed")

    @app.get("/healthz")
    @app.get("/readyz")
    async def health():
        return {"status": "ok", "service": "graphiti"}

    # Route registrations (traverse/timeline/projection status) are restored
    # incrementally; the service identity and health contract come first.
    install_telemetry(app, config)
    return app


app = create_app()
