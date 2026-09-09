"""HTTP API and durable SSE facade. Worker is a separate process using the same owned DB."""
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.common.db import create_database
from knowledge_platform.common.observability import install_telemetry
from knowledge_platform.common.security import ServiceSecurity, bearer_token, service_identity
from knowledge_platform.common.secrets import SecretBox

from .authorization import Guard
from .clients import Clients
from .config import AgentSettings
from .models import initialize
from .schemas import AgentCreate, AgentError, AgentUpdate, FeedbackCreate, PRESETS, Publish, Retry, RunCreate, SessionCreate, Strict, fail
from .service import AgentService
from .store import Store
from .streaming import EventResponse, event_stream
from .worker import Worker

Token = Annotated[str,Depends(bearer_token)]


def error(status,code,message,headers=None):
    return JSONResponse(status_code=status,content={'error':{'code':code,'message':message}},headers=headers)


def create_app(*,settings=None,database=None,authorizer=None,security=None):
    config = settings or AgentSettings()

    @asynccontextmanager
    async def lifespan(app):
        box = SecretBox(config.agent_encryption_key)
        db = database or create_database(config.database_url)
        await initialize(db.engine)
        owned_http = None
        if authorizer is None:
            owned_http = httpx.AsyncClient(timeout=config.request_timeout,follow_redirects=False,trust_env=False)
        auth = authorizer or HTTPAuthorizer(config,owned_http)
        clients = Clients(config,auth)
        service = AgentService(config,db,auth,clients,box)
        store = Store(db,config)
        service.store = store
        app.state.database, app.state.service = db,service
        app.state.worker = Worker(service,store)
        app.state.service_security = security or ServiceSecurity.from_settings(config)
        try:
            yield
        finally:
            if owned_http:
                await owned_http.aclose()
            if database is None:
                await db.close()

    app = FastAPI(title='Knowledge Agent',version='1.0.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

    @app.middleware('http')
    async def boundary(request,call_next):
        if request.url.path not in {'/healthz','/readyz'}:
            try:
                caller = service_identity(request)
                if caller not in {'gateway','mcp'}:
                    raise HTTPException(403,'Service operation not permitted')
                # Channel requires an explicitly verified binding/audience broker. No principal,
                # groups or scope assertion supplied in JSON can activate that future capability.
                if request.url.path.startswith('/internal/v1/') and caller != 'mcp':
                    raise HTTPException(403,'Internal operation not permitted')
            except HTTPException as exc:
                return error(exc.status_code,'invalid_service_identity','Service operation not permitted')
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(AgentError)
    async def agent_error(request,exc):
        return error(exc.status,exc.code,exc.message)

    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        return error(exc.status_code,'authorization_unavailable' if exc.status_code==503 else 'request_denied','Authentication, authorization or dependency request failed',exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        return error(422,'invalid_request','Request does not match the Agent API contract')

    @app.exception_handler(IntegrityError)
    async def integrity_error(request,exc):
        return error(409,'write_conflict','Agent record changed or an active session run already exists')

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request,exc):
        return error(503,'database_unavailable','Agent persistence unavailable')

    @app.get('/healthz')
    async def health():
        return {'status':'ok','service':'agent'}

    @app.get('/readyz')
    async def ready(request:Request):
        async with request.app.state.database.session() as session:
            await session.execute(text('SELECT 1'))
        return {'status':'ready'}

    @app.get('/api/v1/agents/presets')
    async def presets(request:Request,token:Token):
        guard = await Guard.begin(request.app.state.service.auth,token)
        await guard.finish()
        return {'items':[{'id':key,**value,'model_configuration_id':None} for key,value in PRESETS.items()]}

    @app.post('/api/v1/agents',status_code=201)
    async def create_agent(body:AgentCreate,request:Request,token:Token):
        return await request.app.state.service.create_agent(token,body)

    @app.get('/api/v1/agents')
    async def agents(request:Request,token:Token,cursor:str|None=None,limit:int=Query(50,ge=1,le=100)):
        return await request.app.state.service.list_agents(token,cursor,limit)

    @app.get('/api/v1/agents/{agent_id}')
    async def agent(agent_id:str,request:Request,token:Token,configuration_id:str|None=None):
        return await request.app.state.service.get_agent(token,agent_id,configuration_id)

    @app.put('/api/v1/agents/{agent_id}')
    async def update_agent(agent_id:str,body:AgentUpdate,request:Request,token:Token):
        return await request.app.state.service.update_agent(token,agent_id,body)

    @app.post('/api/v1/agents/{agent_id}/publish')
    async def publish(agent_id:str,body:Publish,request:Request,token:Token):
        return await request.app.state.service.publish(token,agent_id,body)

    @app.get('/api/v1/agents/{agent_id}/versions')
    async def versions(agent_id:str,request:Request,token:Token,cursor:int|None=Query(None,ge=1),limit:int=Query(50,ge=1,le=100)):
        return await request.app.state.service.versions(token,agent_id,cursor,limit)

    @app.post('/api/v1/sessions',status_code=201)
    @app.post('/internal/v1/sessions',status_code=201)
    async def session(body:SessionCreate,request:Request,token:Token):
        return await request.app.state.service.create_session(token,body)

    @app.get('/api/v1/sessions')
    async def sessions(request:Request,token:Token,cursor:str|None=None,limit:int=Query(50,ge=1,le=100)):
        return await request.app.state.service.list_sessions(token,cursor,limit)

    @app.delete('/api/v1/sessions/{session_id}')
    async def clear(session_id:str,request:Request,token:Token):
        return await request.app.state.service.clear_session(token,session_id)

    @app.get('/api/v1/sessions/{session_id}/history')
    async def history(session_id:str,request:Request,token:Token,cursor:str|None=None,limit:int=Query(50,ge=1,le=100)):
        return await request.app.state.service.history(token,session_id,cursor,limit)

    @app.post('/api/v1/sessions/{session_id}/summaries',status_code=201)
    async def summary_create(session_id:str,body:Strict,request:Request,token:Token):
        return await request.app.state.service.summaries(token,session_id,True)

    @app.get('/api/v1/sessions/{session_id}/summaries')
    async def summaries(session_id:str,request:Request,token:Token):
        return await request.app.state.service.summaries(token,session_id)

    @app.post('/api/v1/runs',status_code=201)
    @app.post('/internal/v1/runs',status_code=201)
    async def run(body:RunCreate,request:Request,token:Token):
        return await request.app.state.service.create_run(token,body)

    @app.get('/api/v1/runs/{run_id}')
    @app.get('/internal/v1/runs/{run_id}')
    async def run_get(run_id:str,request:Request,token:Token):
        return await request.app.state.service.get_run(token,run_id)

    @app.post('/api/v1/runs/{run_id}/cancel')
    @app.post('/internal/v1/runs/{run_id}/cancel')
    async def cancel(run_id:str,body:Strict,request:Request,token:Token):
        return await request.app.state.service.cancel(token,run_id)

    @app.post('/api/v1/runs/{run_id}/retry',status_code=201)
    async def retry(run_id:str,body:Retry,request:Request,token:Token):
        return await request.app.state.service.retry(token,run_id,body)

    @app.get('/api/v1/runs/{run_id}/events')
    @app.get('/internal/v1/runs/{run_id}/events')
    async def events(run_id:str,request:Request,token:Token,cursor:int=Query(0,ge=0)):
        last = request.headers.get('last-event-id')
        if last is not None:
            if not last.isascii() or not last.isdecimal() or len(last)>18:
                raise fail('invalid_event_cursor','Invalid event cursor',422)
            cursor = int(last)
        await request.app.state.service.event_batch(token,run_id,cursor)
        return EventResponse(event_stream(request.app.state.service,token,run_id,cursor),settings=config)

    @app.get('/api/v1/runs/{run_id}/export')
    async def export(run_id:str,request:Request,token:Token):
        return PlainTextResponse(await request.app.state.service.export(token,run_id),media_type='text/markdown',headers={'Content-Disposition':'attachment; filename="knowledge-answer.md"'})

    @app.post('/api/v1/feedback',status_code=201)
    @app.post('/internal/v1/feedback',status_code=201)
    async def feedback(body:FeedbackCreate,request:Request,token:Token):
        return await request.app.state.service.feedback(token,body)

    install_telemetry(app,config)
    return app


app = create_app()