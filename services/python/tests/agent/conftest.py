import base64
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest_asyncio.fixture
async def database():
    from knowledge_platform.agent.models import initialize
    from knowledge_platform.common.db import Database
    private = Path(__file__).resolve().parents[4] / 'knowledge-docker/.local/test-env.json'
    url = os.environ.get('TEST_AGENT_DATABASE_URL')
    if not url and private.exists():
        url = json.loads(private.read_text())['databases']['agent']
    if not url:
        pytest.skip('Real isolated Agent PostgreSQL database required')
    url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
    schema = 'test_agent_' + uuid4().hex
    admin = create_async_engine(url, hide_parameters=True)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_async_engine(url, connect_args={'options': f'-csearch_path={schema}'}, hide_parameters=True)
    try:
        await initialize(engine)
        yield Database(engine)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await admin.dispose()


@pytest_asyncio.fixture
async def api(database, tmp_path):
    from knowledge_platform.agent.app import create_app
    from knowledge_platform.agent.config import AgentSettings
    from knowledge_platform.common.auth import HTTPAuthorizer
    from knowledge_platform.common.security import ServiceSecurity
    keys = {name: Ed25519PrivateKey.generate() for name in ['agent', 'auth', 'llm', 'knowledge', 'retrieval', 'gateway', 'mcp', 'channel', 'ingest']}
    public = {name: key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode() for name, key in keys.items()}
    security = {name: ServiceSecurity(name, key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode(), public) for name, key in keys.items()}
    (tmp_path / 'public.json').write_text(json.dumps(public))
    (tmp_path / 'agent.pem').write_text(security['agent'].private_key)
    settings = AgentSettings(service_private_key_file=str(tmp_path / 'agent.pem'), service_public_keys_file=str(tmp_path / 'public.json'), agent_encryption_key=base64.b64encode(os.urandom(32)).decode(), agent_poll_seconds=0.02, agent_event_poll_seconds=0.02, agent_lease_seconds=3, agent_heartbeat_seconds=0.2)
    state = SimpleNamespace(principal='alice', epoch=1, permissions=[], scopes=['knowledge:read', 'knowledge:write', 'knowledge:feedback'], denied=set(), source_allowed=True, page_allowed=True, calls=[], chat_calls=[], handler=None, chat_handler=None, proposal_calls=[], reply=None, auth_status=200, model_active=True)
    reference = {'resource_id':'source:repo', 'revision_id':'snapshot1', 'source_id':'git:repo', 'source_revision':'commit1', 'path':'main.go', 'start_line':1, 'end_line':1, 'kind':'code', 'valid_from':None, 'valid_until':None}
    page = {'id':'page:pay', 'space_id':'engineering', 'current_revision':'revision1', 'revision_number':1, 'revision':{'id':'revision1', 'page_id':'page:pay', 'number':1, 'content':{'title':'Payment', 'markdown':'Pay calls Ledger', 'entity_type':'Module', 'claims':[], 'evidence':[reference], 'state':'valid', 'valid_from':None, 'valid_until':None}, 'access_dependencies':['snapshot1']}}

    async def handle(request):
        name = request.url.host
        assert security[name].verify(request.headers['x-service-token']) == 'agent'
        body = json.loads(request.content) if request.content else {}
        state.calls.append((name, request.method, request.url.path, body))
        if name == 'auth':
            if state.auth_status != 200:
                return httpx.Response(state.auth_status, json={'secret':'must-not-leak'})
            if request.url.path.endswith('/resolve'):
                return httpx.Response(200, json={'principal':{'id':state.principal, 'subjects':['user:'+state.principal], 'permissions':state.permissions, 'auth_epoch':state.epoch}, 'scopes':state.scopes})
            allowed = (body.get('action'), body.get('space_id'), body.get('resource_id')) not in state.denied and (body.get('action'), body.get('space_id'), None) not in state.denied
            return httpx.Response(200, json={'allowed':allowed, 'auth_epoch':state.epoch})
        if state.handler:
            result = await state.handler(request, body)
            if result is not None:
                return result
        if name == 'llm':
            assert 'authorization' not in request.headers
            if request.method == 'GET':
                return httpx.Response(200 if state.model_active else 409, json={'configuration_id':'model1', 'capability':'chat', 'state':'active', 'max_output_tokens':8192, 'max_input_chars':131072, 'max_response_bytes':1000000, 'timeout_seconds':180})
            state.chat_calls.append(body)
            if state.chat_handler:
                return await state.chat_handler(request, body)
            if not any(m['role'] == 'tool' for m in body['messages']):
                value = {'content':None, 'tool_calls':[{'id':'call1', 'type':'function', 'function':{'name':'search', 'arguments':json.dumps({'query':'payment', 'space_ids':['engineering']})}}]}
            else:
                result = json.loads(next(m['content'] for m in body['messages'] if m['role'] == 'tool'))
                cid = result['citations'][0]['id']
                value = {'content':json.dumps({'facts':[{'text':'Pay calls Ledger', 'citation_ids':[cid]}], 'inferences':[], 'gaps':[]}), 'tool_calls':[]}
            value.update({'configuration_id':'model1', 'invocation_id':'invocation'+str(len(state.chat_calls)), 'usage':{'total_tokens':9}, 'finish_reason':'tool_calls' if value['tool_calls'] else 'stop'})
            if body['stream']:
                return httpx.Response(200, headers={'content-type':'text/event-stream'}, text='event: completed\ndata: '+json.dumps({'type':'completed', **value})+'\n\n')
            return httpx.Response(200, json=value)
        assert request.headers.get('authorization', '').startswith('Bearer ')
        if name == 'retrieval':
            return httpx.Response(200, json={'items':[{'id':'fragment1', 'page_id':'page:pay', 'revision_id':'revision1', 'space_id':'engineering', 'title':'Payment', 'text':'Pay calls Ledger', 'kind':'fact', 'evidence':[reference], 'citation_ids':[], 'primary':True, 'version':1}], 'citations':[], 'degraded':[], 'graph':{'nodes':[], 'edges':[], 'paths':[]}})
        if name == 'knowledge':
            if request.url.path.endswith('/pages/authorize'):
                return httpx.Response(200, json={'decisions':[{**p, 'allowed':state.page_allowed, 'authorized':state.page_allowed, 'space_id':'engineering', 'state':'valid', 'is_current':True} for p in body['pages']]})
            if request.url.path.endswith('/evidence/authorize'):
                return httpx.Response(200, json={'decisions':[{'evidence':e, 'allowed':state.source_allowed, 'space_id':'engineering', 'excerpt':'Pay calls Ledger', 'sha256':'a'*64} for e in body['evidence']]})
            if request.method == 'GET':
                return httpx.Response(200, json=page if '/revisions/' not in request.url.path else page['revision'])
            if request.url.path.endswith('/proposals'):
                state.proposal_calls.append(body)
                return httpx.Response(201, json={'id':'proposal1', 'status':'pending', 'page_id':'page:pay', 'base_revision':body['base_revision']})
        return httpx.Response(500)

    token = jwt.encode({'sub':'alice', 'exp':int(time.time())+3600}, 'test-signing-key-not-a-real-key-32', algorithm='HS256')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
        auth = HTTPAuthorizer(settings, upstream)
        app = create_app(settings=settings, database=database, authorizer=auth, security=security['agent'])
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://agent') as client:
                def headers(caller='gateway'):
                    return {'X-Service-Token':security[caller].issue('agent'), 'Authorization':'Bearer '+token}
                client.headers.update(headers())
                yield SimpleNamespace(client=client, app=app, state=state, database=database, settings=settings, headers=headers, token=token, reference=reference)