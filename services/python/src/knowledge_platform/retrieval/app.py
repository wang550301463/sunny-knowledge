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
from knowledge_platform.common.security import ServiceSecurity,bearer_token,service_identity

from .clients import Clients
from .config import RetrievalSettings
from .elasticsearch import ElasticIndex
from .models import initialize
from .schemas import RetrievalError, SearchRequest, TimelineRequest, TraverseRequest
from .service import RetrievalService
from .store import Catalog

Token=Annotated[str,Depends(bearer_token)]


def error(status,code,message,headers=None):
    return JSONResponse(status_code=status,content={"error":{"code":code,"message":message}},headers=headers)


def create_app(*,settings=None,database=None,authorizer=None,security=None,index=None,runtime=None):
    config=settings or RetrievalSettings()
    @asynccontextmanager
    async def lifespan(app):
        app.state.service_security=security or ServiceSecurity.from_settings(config)
        if runtime:
            app.state.runtime=runtime
            yield
            return
        app.state.database=database or create_database(config.database_url)
        app.state.authorizer=authorizer or HTTPAuthorizer(config)
        app.state.index=index or ElasticIndex(config)
        await initialize(app.state.database.engine)
        app.state.runtime=RetrievalService(config,app.state.authorizer,Catalog(app.state.database),app.state.index,Clients(config,app.state.authorizer))
        try:
            yield
        finally:
            if index is None:await app.state.index.close()
            if authorizer is None:await app.state.authorizer.close()
            if database is None:await app.state.database.close()
    app=FastAPI(title="Knowledge Retrieval",version="1.0.0",lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

    @app.middleware("http")
    async def boundary(request,call_next):
        if request.url.path not in {"/healthz","/readyz"}:
            try:
                caller=service_identity(request)
                allowed={"gateway","agent","mcp","graphiti"} if request.url.path.startswith("/api/v1/") else {"agent","mcp","graphiti"} if request.url.path.startswith("/internal/v1/") else set()
                if caller not in allowed:
                    raise HTTPException(403,"Service operation not permitted")
            except HTTPException as exc:
                return error(exc.status_code,"invalid_service_identity",str(exc.detail),exc.headers)
        return await call_next(request)

    @app.exception_handler(RetrievalError)
    async def retrieval_error(request,exc):
        return error(exc.status,exc.code,exc.message)

    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        code={401:"unauthenticated",403:"forbidden",404:"not_found",503:"dependency_unavailable"}.get(exc.status_code,"request_failed")
        return error(exc.status_code,code,"Authentication, authorization, or dependency request failed",exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        return error(422,"invalid_request","Request does not match the retrieval API contract")

    @app.exception_handler(SQLAlchemyError)
    async def db_error(request,exc):
        return error(503,"database_unavailable","Retrieval projection catalog unavailable")

    @app.get("/healthz")
    async def health():return {"status":"ok","service":"retrieval"}

    @app.get("/readyz")
    async def ready(request:Request):
        async with request.app.state.database.session() as session:
            await session.execute(text("SELECT 1"))
        await request.app.state.index.initialize(config.retrieval_embedding_dimensions,config.retrieval_embedding_configuration_id)
        return {"status":"ready"}

    @app.post("/api/v1/search")
    @app.post("/internal/v1/search")
    async def search(body:SearchRequest,request:Request,token:Token):
        return await request.app.state.runtime.search(token,body)

    @app.post("/api/v1/traverse")
    @app.post("/internal/v1/traverse")
    async def traverse(body:TraverseRequest,request:Request,token:Token):
        return await request.app.state.runtime.traverse(token,body)

    @app.post("/api/v1/timeline")
    @app.post("/internal/v1/timeline")
    async def timeline(body:TimelineRequest,request:Request,token:Token):
        return await request.app.state.runtime.timeline(token,body)

    install_telemetry(app,config)
    return app


app=create_app()