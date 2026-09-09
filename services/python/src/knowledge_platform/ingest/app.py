"""Authenticated source and durable ingest task API."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.common.db import create_database
from knowledge_platform.common.observability import install_telemetry
from knowledge_platform.common.security import ServiceSecurity, bearer_token, service_identity
from knowledge_platform.common.secrets import SecretBox
from .clients import InternalClient, MachineTokens
from .config import IngestSettings
from .auto_sync import auto_sync_loop, auto_sync_source
from .connectors import GitConnector, OSSConnector, SnapshotLimits, UploadConnector
from .models import initialize
from .schemas import IngestError, SourceCreate, SourceUpdate, VersionRequest
from .service import IngestService
from .storage import S3Store


def error_response(status, code, message, headers=None):
    return JSONResponse(status_code=status, content={'error': {'code': code, 'message': message}}, headers=headers)


def new_service(request, session):
    state = request.app.state
    return IngestService(session, state.authorizer, state.machine, state.internal, state.secret_box)


async def service(request: Request):
    async with request.app.state.database.session() as session, session.begin():
        yield new_service(request, session)


Service = Annotated[IngestService, Depends(service, scope='function')]
Token = Annotated[str, Depends(bearer_token)]


def create_app(*, database=None, authorizer=None, security=None, machine=None, internal=None,
               secret_box=None, storage=None, settings=None, initialize_schema=True):
    config = settings or IngestSettings.from_env('ingest')

    @asynccontextmanager
    async def lifespan(app):
        state = app.state
        state.database = database or create_database(config.database_url)
        state.service_security = security or ServiceSecurity.from_settings(config)
        state.authorizer = authorizer or HTTPAuthorizer(config)
        state.machine = machine or MachineTokens(config)
        state.internal = internal or InternalClient(config, state.service_security)
        state.secret_box = secret_box or SecretBox(config.ingest_encryption_key)
        state.storage = storage or S3Store.from_settings(config)
        state.preview_slots = asyncio.Semaphore(config.ingest_preview_concurrency)
        state.limits = SnapshotLimits(max_bytes=config.ingest_max_snapshot_bytes,
            max_files=config.ingest_max_snapshot_files, max_git_disk_bytes=config.ingest_max_git_disk_bytes,
            timeout_seconds=config.ingest_git_timeout_seconds)
        if initialize_schema: await initialize(state.database.engine)

        def auto_sync_service(session):
            return IngestService(session, state.authorizer, state.machine, state.internal, state.secret_box)
        state.auto_sync_service = auto_sync_service
        state.auto_sync_task = None
        if config.ingest_auto_sync_enabled:
            state.auto_sync_task = asyncio.create_task(auto_sync_loop(state, config.ingest_auto_sync_tick_seconds))
        try: yield
        finally:
            if state.auto_sync_task is not None:
                state.auto_sync_task.cancel()
            for value, supplied in ((state.authorizer, authorizer), (state.machine, machine),
                                     (state.internal, internal), (state.database, database)):
                if supplied is None: await value.close()

    app = FastAPI(title='Source ingestion', version='1.0.0', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def boundary(request, call_next):
        if request.url.path not in {'/healthz', '/readyz'}:
            try:
                if service_identity(request) != 'gateway':
                    raise HTTPException(403, 'Service operation not permitted')
                bearer_token(request)
                if request.method in {'POST', 'PATCH', 'DELETE'}:
                    # A UTF-8 body may be JSON-unicode-escaped; the parser additionally enforces
                    # the exact decoded 4 MB file budget. Never buffer an unbounded request.
                    chunks, size = [], 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > 24_128_000:
                            return error_response(413, 'source_limit_exceeded', 'Request exceeds byte budget')
                        chunks.append(chunk)
                    request._body = b''.join(chunks)
            except HTTPException as error:
                return error_response(error.status_code, 'unauthenticated' if error.status_code == 401 else 'forbidden', str(error.detail))
        return await call_next(request)

    @app.exception_handler(IngestError)
    async def ingest_error(request, error):
        return error_response(error.status, error.code, error.message)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return error_response(error.status_code, {401: 'unauthenticated', 403: 'forbidden', 404: 'not_found'}.get(error.status_code, 'dependency_unavailable'), str(error.detail), error.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return error_response(422, 'invalid_request', 'Request does not match the API contract')

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, error):
        return error_response(409, 'write_conflict', 'Ingest record conflicts with an existing record')

    @app.exception_handler(DBAPIError)
    async def database_error(request, error):
        return error_response(503, 'database_unavailable', 'Ingest database operation failed')

    @app.get('/healthz')
    async def health(): return {'status': 'ok', 'service': 'ingest'}

    @app.get('/readyz')
    async def ready(request: Request):
        async with request.app.state.database.session() as session: await session.execute(text('SELECT 1'))
        return {'status': 'ready'}

    @app.post('/api/v1/sources', status_code=201)
    async def create(body: SourceCreate, service: Service, token: Token):
        return await service.create(token, body)

    @app.get('/api/v1/sources')
    async def sources(service: Service, token: Token, space_id: str | None = None,
                      cursor: str | None = None, limit: int = Query(50, ge=1, le=100)):
        return await service.list_sources(token, space_id, cursor, limit)

    @app.get('/api/v1/sources/{source_id}')
    async def get(source_id: str, service: Service, token: Token):
        return await service.get(token, source_id)

    @app.patch('/api/v1/sources/{source_id}')
    async def update(source_id: str, body: SourceUpdate, service: Service, token: Token):
        return await service.update(token, source_id, body)

    @app.post('/api/v1/sources/{source_id}/preview')
    async def preview(source_id: str, body: VersionRequest, request: Request, token: Token):
        # Network capture does not hold a source row lock. A second transaction reauthorizes
        # and CAS-pins the captured snapshot, rejecting a concurrent source version update.
        async with request.app.state.preview_slots:
            async with request.app.state.database.session() as session, session.begin():
                data = await new_service(request, session).preview_input(token, source_id, body.base_version)
            if 'preview' in data: return data['preview']
            connector = ({'git': lambda: GitConnector(request.app.state.limits),
                          'oss': lambda: OSSConnector(request.app.state.limits)}.get(data['kind'],
                         lambda: UploadConnector(data['kind'], request.app.state.limits)))()
            snapshot = await asyncio.to_thread(connector.capture, data['config'], data['credential'], body.base_version)
            key, manifest = await asyncio.to_thread(request.app.state.storage.put_snapshot, source_id, body.base_version, snapshot)
            async with request.app.state.database.session() as session, session.begin():
                return await new_service(request, session).save_preview(token, source_id, body.base_version, key, manifest)

    @app.post('/api/v1/sources/{source_id}/sync', status_code=202)
    async def sync(source_id: str, body: VersionRequest, service: Service, token: Token):
        return await service.sync(token, source_id, body.base_version)

    @app.post('/api/v1/sources/{source_id}/auto-sync')
    async def trigger_auto_sync(source_id: str, request: Request, token: Token):
        # Authorize like any write, then run the automatic capture path once.
        async with request.app.state.database.session() as session, session.begin():
            await new_service(request, session).authorize(
                token, await new_service(request, session).source(source_id), write=True)
        return await auto_sync_source(request.app.state, source_id)

    @app.delete('/api/v1/sources/{source_id}', status_code=202)
    async def delete(source_id: str, body: VersionRequest, service: Service, token: Token):
        return await service.delete(token, source_id, body.base_version)

    @app.get('/api/v1/tasks')
    async def tasks(service: Service, token: Token, space_id: str | None = None,
                    source_id: str | None = None, cursor: str | None = None,
                    limit: int = Query(50, ge=1, le=100)):
        return await service.list_tasks(token, space_id, source_id, cursor, limit)

    @app.get('/api/v1/tasks/{task_id}')
    async def task(task_id: str, service: Service, token: Token):
        return await service.get_task(token, task_id)

    install_telemetry(app, config)
    return app


app = create_app()