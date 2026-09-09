"""Public model administration and workload-isolated inference."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.common.db import create_database
from knowledge_platform.common.secrets import SecretBox
from knowledge_platform.common.security import ServiceSecurity, bearer_token, service_identity

from .adapters import AdapterRegistry
from .models import Audit, Invocation, UsageOutcome, initialize
from .runtime import InferenceService
from .schemas import (Capability, ChatRequest, EmbeddingRequest, LLMError, LLMSettings,
                      ModelCreate, ModelState, ModelUpdate, RerankRequest, TestRequest)
from .service import ModelStore

ALLOWLIST = {'chat': {'agent', 'graphiti'}, 'embedding': {'retrieval', 'ingest', 'graphiti'}, 'rerank': {'retrieval'}}


def error_response(status, code, message, headers=None):
    return JSONResponse(status_code=status, content={'error': {'code': code, 'message': message}}, headers=headers)


class ResolvedPrincipal(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore')
    id: str = Field(min_length=1, max_length=512)
    subjects: list[str]
    permissions: list[str]
    auth_epoch: int = Field(ge=0)


class ResolvedIdentity(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore')
    principal: ResolvedPrincipal
    scopes: list[str]


async def administrator(request: Request):
    token = bearer_token(request)
    response = await request.app.state.authorizer.request('POST', '/internal/v1/resolve', 'auth', token, {})
    try:
        resolved = ResolvedIdentity.model_validate(response.json())
    except (ValueError, ValidationError):
        raise HTTPException(503, 'Invalid authorization response') from None
    required = 'knowledge:read' if request.method == 'GET' else 'knowledge:write'
    if 'platform_admin' not in resolved.principal.permissions or required not in resolved.scopes:
        raise HTTPException(403, 'Model administration permission and scope required')
    return resolved.principal.id


Admin = Annotated[str, Depends(administrator)]


class BodyLimit:
    def __init__(self, app, maximum):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > self.maximum:
                return await error_response(413, 'request_too_large', 'Request body exceeds limit')(scope, receive, send)
            chunks.append(chunk)
            if not message.get('more_body', False):
                break
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
            return await receive()
        await self.app(scope, bounded_receive, send)


def create_app(*, database=None, authorizer=None, security=None, settings=None,
               provider_client=None, initialize_schema=True):
    config = settings or LLMSettings.from_env('llm')

    @asynccontextmanager
    async def lifespan(app):
        app.state.database = database or create_database(config.database_url)
        # Suppress bound SQL parameter rendering even when exceptions reach server logging.
        app.state.database.engine.hide_parameters = True
        app.state.authorizer = authorizer or HTTPAuthorizer(config)
        app.state.service_security = security or ServiceSecurity.from_settings(config)
        app.state.provider_client = provider_client or httpx.AsyncClient(timeout=60, follow_redirects=False, trust_env=False,
                                                                        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20))
        secrets = SecretBox(config.credential_encryption_key.get_secret_value())
        registry = AdapterRegistry(app.state.provider_client)
        app.state.models = ModelStore(app.state.database, secrets, registry, config)
        app.state.inference = InferenceService(app.state.database, app.state.models, registry)
        if initialize_schema:
            await initialize(app.state.database.engine)
        try:
            yield
        finally:
            if provider_client is None:
                await app.state.provider_client.aclose()
            if authorizer is None:
                await app.state.authorizer.close()
            if database is None:
                await app.state.database.close()

    app = FastAPI(title='Model Service', version='1.0.0', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit, maximum=config.llm_max_request_bytes)

    @app.middleware('http')
    async def workload_boundary(request, call_next):
        request.state.request_id = str(uuid4())
        if request.url.path not in {'/healthz', '/readyz'}:
            try:
                request.state.caller = service_identity(request)
                path = request.url.path
                if path.startswith('/api/v1/models'):
                    allowed = {'gateway'}
                elif path == '/internal/v1/chat':
                    allowed = ALLOWLIST['chat']
                elif path == '/internal/v1/embeddings':
                    allowed = ALLOWLIST['embedding']
                elif path == '/internal/v1/rerank':
                    allowed = ALLOWLIST['rerank']
                elif path == '/internal/v1/models':
                    allowed = set.union(*ALLOWLIST.values())
                else:
                    allowed = set()
                if request.state.caller not in allowed:
                    raise HTTPException(403, 'Service operation not permitted')
            except HTTPException as error:
                return error_response(error.status_code, 'invalid_service_identity', str(error.detail),
                                      {'X-Request-ID': request.state.request_id})
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        return response

    @app.exception_handler(LLMError)
    async def llm_error(request, error):
        return error_response(error.status, error.code, error.message)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return error_response(error.status_code, {401: 'unauthenticated', 403: 'forbidden', 503: 'dependency_unavailable'}.get(error.status_code, 'request_failed'), str(error.detail), error.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return error_response(422, 'invalid_request', 'Request does not match the API contract')

    @app.exception_handler(DBAPIError)
    async def database_error(request, error):
        return error_response(503, 'database_unavailable', 'Model database operation failed')

    @app.get('/healthz')
    async def health():
        return {'status': 'ok', 'service': 'llm'}

    @app.get('/readyz')
    async def ready(request: Request):
        async with request.app.state.database.session() as session:
            await session.execute(text('SELECT 1'))
        return {'status': 'ready'}

    @app.post('/api/v1/models', status_code=201)
    async def create_model(body: ModelCreate, request: Request, actor: Admin):
        return await request.app.state.models.create(actor, body)

    @app.get('/api/v1/models')
    async def list_models(request: Request, actor: Admin, capability: Capability | None = None,
                          cursor: UUID | None = None, limit: int = Query(50, ge=1, le=100)):
        return await request.app.state.models.list(capability=capability, cursor=str(cursor) if cursor else None, limit=limit)

    @app.get('/api/v1/models/{model_id}')
    async def get_model(model_id: UUID, request: Request, actor: Admin):
        return await request.app.state.models.get(str(model_id))

    @app.put('/api/v1/models/{model_id}')
    async def update_model(model_id: UUID, body: ModelUpdate, request: Request, actor: Admin):
        return await request.app.state.models.update(actor, str(model_id), body.base_configuration_id, config=body.config, credential=body.credential)

    @app.patch('/api/v1/models/{model_id}/state')
    async def model_state(model_id: UUID, body: ModelState, request: Request, actor: Admin):
        return await request.app.state.models.update(actor, str(model_id), body.base_configuration_id, state=body.state)

    @app.delete('/api/v1/models/{model_id}')
    async def retire_model(model_id: UUID, base_configuration_id: UUID, request: Request, actor: Admin):
        return await request.app.state.models.update(actor, str(model_id), base_configuration_id, state='retired')

    @app.get('/api/v1/models/{model_id}/versions')
    async def versions(model_id: UUID, request: Request, actor: Admin,
                       cursor: int | None = Query(None, ge=1), limit: int = Query(50, ge=1, le=100)):
        return await request.app.state.models.versions(str(model_id), cursor, limit)

    @app.get('/api/v1/models/{model_id}/audit')
    async def audit(model_id: UUID, request: Request, actor: Admin, limit: int = Query(50, ge=1, le=100)):
        await request.app.state.models.get(str(model_id))
        async with request.app.state.database.session() as session:
            rows = (await session.scalars(select(Audit).where(Audit.model_id == str(model_id)).order_by(Audit.created_at.desc()).limit(limit))).all()
            return {'items': [{'id': r.id, 'configuration_id': r.configuration_id, 'action': r.action, 'actor_id': r.actor_id, 'details': r.details, 'created_at': r.created_at.isoformat()} for r in rows]}

    @app.get('/api/v1/models/{model_id}/usage')
    async def model_usage(model_id: UUID, request: Request, actor: Admin, limit: int = Query(50, ge=1, le=100)):
        from .models import Configuration
        await request.app.state.models.get(str(model_id))
        async with request.app.state.database.session() as session:
            rows = (await session.execute(select(Invocation, UsageOutcome).join(Configuration, Invocation.configuration_id == Configuration.id).outerjoin(UsageOutcome, Invocation.id == UsageOutcome.invocation_id).where(Configuration.model_id == str(model_id)).order_by(Invocation.started_at.desc()).limit(limit))).all()
            return {'items': [{'invocation_id': i.id, 'configuration_id': i.configuration_id, 'caller': i.caller, 'capability': i.capability, 'started_at': i.started_at.isoformat(), 'outcome': o.outcome if o else 'unknown', 'duration_ms': o.duration_ms if o else None, 'request_id': o.provider_request_id if o else None, 'usage': o.usage if o else {}} for i, o in rows]}

    @app.post('/api/v1/models/{model_id}/test')
    async def test_model(model_id: UUID, body: TestRequest, request: Request, actor: Admin):
        models, inference = request.app.state.models, request.app.state.inference
        model = await models.get(str(model_id))
        if model['configuration_id'] != str(body.configuration_id):
            raise LLMError(409, 'configuration_conflict', 'Test must target current configuration')
        capability, cid = model['capability'], body.configuration_id
        result = {'test_state': 'passed', 'capabilities': {capability: False}, 'dimensions': None, 'error_code': None}
        try:
            if capability == 'embedding':
                probe = await inference.infer('llm:test', capability, EmbeddingRequest(configuration_id=cid, input=['capability probe']))
                result['dimensions'] = probe['dimensions']
            elif capability == 'rerank':
                await inference.infer('llm:test', capability, RerankRequest(configuration_id=cid, query='probe', documents=['probe'], top_n=1))
            else:
                probe = ChatRequest(configuration_id=cid, messages=[{'role': 'user', 'content': 'Reply with OK.'}], max_output_tokens=min(64, model['max_output_tokens']))
                await inference.infer('llm:test', capability, probe)
                if body.test_tools:
                    probe = ChatRequest(configuration_id=cid, messages=[{'role': 'user', 'content': 'Call the probe function with value OK.'}], tools=[{'type': 'function', 'function': {'name': 'probe', 'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}}}], max_output_tokens=min(128, model['max_output_tokens']))
                    output = await inference.infer('llm:test', capability, probe)
                    result['capabilities']['tools'] = bool(output['tool_calls']) and all(c['function']['name'] == 'probe' for c in output['tool_calls'])
                if body.test_stream:
                    probe = ChatRequest(configuration_id=cid, messages=[{'role': 'user', 'content': 'Reply with OK.'}], stream=True, max_output_tokens=min(64, model['max_output_tokens']))
                    events = [event async for event in inference.stream('llm:test', probe)]
                    result['capabilities']['stream'] = bool(events and events[-1]['type'] == 'completed')
            result['capabilities'][capability] = True
        except LLMError as error:
            result['test_state'], result['error_code'] = 'failed', error.code
        return await models.record_test(actor, str(model_id), body, result)

    @app.get('/internal/v1/models')
    async def discover(request: Request, capability: Capability,
                       cursor: UUID | None = None, limit: int = Query(50, ge=1, le=100)):
        if request.state.caller not in ALLOWLIST[capability]:
            raise HTTPException(403, 'Service capability not permitted')
        return await request.app.state.models.list(capability=capability, active=True, cursor=str(cursor) if cursor else None, limit=limit)

    @app.post('/internal/v1/embeddings')
    async def embeddings(body: EmbeddingRequest, request: Request):
        return await request.app.state.inference.infer(request.state.caller, 'embedding', body)

    @app.post('/internal/v1/rerank')
    async def rerank(body: RerankRequest, request: Request):
        return await request.app.state.inference.infer(request.state.caller, 'rerank', body)

    @app.post('/internal/v1/chat')
    async def chat(body: ChatRequest, request: Request):
        if not body.stream:
            return await request.app.state.inference.infer(request.state.caller, 'chat', body)
        # Fail config validation before accepting SSE, then the runtime checks again after queueing.
        await request.app.state.models.snapshot(body.configuration_id, 'chat')
        async def events():
            iterator = request.app.state.inference.stream(request.state.caller, body)
            try:
                async for event in iterator:
                    yield 'event: ' + event['type'] + '\ndata: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
            except LLMError as error:
                yield 'event: error\ndata: ' + json.dumps({'type': 'error', 'error': {'code': error.code, 'message': error.message}}) + '\n\n'
            except DBAPIError:
                yield 'event: error\ndata: ' + json.dumps({'type': 'error', 'error': {'code': 'database_unavailable', 'message': 'Model database operation failed'}}) + '\n\n'
            finally:
                await iterator.aclose()
        return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    return app


app = create_app()