"""FastAPI application for the sole canonical publication service."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.common.config import Settings
from knowledge_platform.common.db import create_database
from knowledge_platform.common.security import ServiceSecurity, bearer_token, service_identity

from .models import initialize
from .schemas import (
    AckRequest, DeterministicPublish, EvidenceAuthorizeRequest, KnowledgeError,
    LeaseRequest, PageAuthorizeRequest, PageCreate, ProposalCreate, ReviewAction,
    RollbackRequest, SourceSnapshotCreate, ValidityUpdate,
)
from .service import KnowledgeService


async def service(request: Request):
    async with request.app.state.database.session() as session:
        async with session.begin():
            yield KnowledgeService(session, request.app.state.authorizer)


# Function scope commits (or rolls back) before the HTTP response is sent.
Knowledge = Annotated[KnowledgeService, Depends(service, scope='function')]
Token = Annotated[str, Depends(bearer_token)]


def caller(request: Request) -> str:
    return request.state.caller


Caller = Annotated[str, Depends(caller)]


def error_response(status: int, code: str, message: str, headers=None):
    return JSONResponse(status_code=status, content={'error': {'code': code, 'message': message}}, headers=headers)


def create_app(*, database=None, authorizer=None, security=None, settings=None, initialize_schema=True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings or Settings.from_env('knowledge')
        app.state.database = database or create_database(config.database_url)
        app.state.authorizer = authorizer or HTTPAuthorizer(config)
        app.state.service_security = security or ServiceSecurity.from_settings(config)
        if initialize_schema:
            await initialize(app.state.database.engine)
        try:
            yield
        finally:
            if authorizer is None:
                await app.state.authorizer.close()
            if database is None:
                await app.state.database.close()

    app = FastAPI(title='Canonical Knowledge', version='1.0.0', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def workload_boundary(request: Request, call_next):
        if request.url.path not in {'/healthz', '/readyz'}:
            try:
                request.state.caller = service_identity(request)
            except HTTPException as error:
                return error_response(error.status_code, 'invalid_service_identity', str(error.detail), error.headers)
        return await call_next(request)

    @app.exception_handler(KnowledgeError)
    async def knowledge_error(request: Request, error: KnowledgeError):
        return error_response(error.status, error.code, error.message)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        code = {401: 'unauthenticated', 403: 'forbidden', 404: 'not_found', 503: 'dependency_unavailable'}.get(error.status_code, 'request_failed')
        return error_response(error.status_code, code, str(error.detail), error.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return error_response(422, 'invalid_request', 'Request does not match the API contract')

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, error: IntegrityError):
        return error_response(409, 'write_conflict', 'Canonical record conflicts with an existing record')

    @app.exception_handler(DBAPIError)
    async def database_error(request: Request, error: DBAPIError):
        return error_response(503, 'database_unavailable', 'Canonical database operation failed')

    @app.get('/healthz')
    async def health():
        return {'status': 'ok', 'service': 'knowledge'}

    @app.get('/readyz')
    async def readiness(request: Request):
        async with request.app.state.database.session() as session:
            await session.execute(text('SELECT 1'))
        return {'status': 'ready'}

    @app.get('/api/v1/pages')
    async def pages(knowledge: Knowledge, token: Token, space_id: str | None = None,
                    cursor: str | None = None, limit: int = Query(50, ge=1, le=100)):
        return await knowledge.list_pages(token, space_id, cursor, limit)

    @app.post('/api/v1/pages', status_code=201)
    async def create_page(body: PageCreate, knowledge: Knowledge, token: Token):
        return await knowledge.create_page(token, body)

    @app.get('/api/v1/pages/{page_id}')
    async def page(page_id: str, knowledge: Knowledge, token: Token):
        return await knowledge.get_page(token, page_id)

    @app.post('/api/v1/pages/{page_id}/proposals', status_code=201)
    async def propose(page_id: str, body: ProposalCreate, knowledge: Knowledge, token: Token):
        return await knowledge.propose(token, page_id, body)

    @app.get('/api/v1/pages/{page_id}/revisions')
    async def revisions(page_id: str, knowledge: Knowledge, token: Token,
                        cursor: int | None = Query(None, ge=1), limit: int = Query(50, ge=1, le=100)):
        return await knowledge.list_revisions(token, page_id, cursor, limit)

    @app.get('/api/v1/pages/{page_id}/revisions/{revision_id}')
    async def revision(page_id: str, revision_id: str, knowledge: Knowledge, token: Token):
        return await knowledge.get_revision(token, page_id, revision_id)

    @app.get('/api/v1/reviews')
    async def reviews(knowledge: Knowledge, token: Token,
                      status: Literal['pending', 'approved', 'rejected'] = 'pending',
                      space_id: str | None = None, cursor: str | None = None,
                      limit: int = Query(50, ge=1, le=100)):
        return await knowledge.list_reviews(token, status, space_id, cursor, limit)

    @app.post('/api/v1/reviews/{proposal_id}/approve')
    async def approve(proposal_id: str, body: ReviewAction, knowledge: Knowledge, token: Token):
        return await knowledge.approve(token, proposal_id, body.reason)

    @app.post('/api/v1/reviews/{proposal_id}/reject')
    async def reject(proposal_id: str, body: ReviewAction, knowledge: Knowledge, token: Token):
        return await knowledge.reject(token, proposal_id, body.reason)

    @app.post('/api/v1/pages/{page_id}/rollback')
    async def rollback(page_id: str, body: RollbackRequest, knowledge: Knowledge, token: Token):
        return await knowledge.rollback(token, page_id, body)

    @app.get('/api/v1/source-snapshots/{snapshot_id}')
    async def snapshot(snapshot_id: str, knowledge: Knowledge, token: Token):
        return await knowledge.get_snapshot(token, snapshot_id)

    @app.post('/internal/v1/sources/snapshots', status_code=201)
    async def register_snapshot(body: SourceSnapshotCreate, knowledge: Knowledge, token: Token, caller: Caller):
        return await knowledge.register_snapshot(token, caller, body)

    @app.get('/internal/v1/sources/{source_id}/revisions/{source_revision}')
    async def source_revision(source_id: str, source_revision: str, knowledge: Knowledge,
                              token: Token, path: str | None = None):
        return await knowledge.source_revision(token, source_id, source_revision, path)

    @app.post('/internal/v1/pages/publish')
    async def publish(body: DeterministicPublish, knowledge: Knowledge, token: Token, caller: Caller):
        return await knowledge.deterministic_publish(token, caller, body)

    @app.post('/internal/v1/pages/{page_id}/validity')
    async def validity(page_id: str, body: ValidityUpdate, knowledge: Knowledge, token: Token, caller: Caller):
        return await knowledge.update_validity(token, caller, page_id, body)

    @app.post('/internal/v1/outbox/lease')
    async def lease(body: LeaseRequest, knowledge: Knowledge, caller: Caller):
        return await knowledge.lease_outbox(caller, body)

    @app.post('/internal/v1/outbox/{event_id}/ack')
    async def ack(event_id: str, body: AckRequest, knowledge: Knowledge, caller: Caller):
        return await knowledge.ack_outbox(caller, event_id, body.lease_token)

    @app.get('/internal/v1/projections/pages/{page_id}/revisions/{revision_id}')
    async def projection(page_id: str, revision_id: str, knowledge: Knowledge, caller: Caller):
        return await knowledge.projection(caller, page_id, revision_id)

    @app.post('/internal/v1/evidence/authorize')
    async def authorize_evidence(body: EvidenceAuthorizeRequest, knowledge: Knowledge, token: Token):
        return await knowledge.authorize_evidence(token, body.evidence)

    @app.post('/internal/v1/pages/authorize')
    async def authorize_pages(body: PageAuthorizeRequest, knowledge: Knowledge, token: Token):
        return await knowledge.authorize_pages(token, body.pages, body.include_historical, body.as_of)

    return app


app = create_app()