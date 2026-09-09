import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest_asyncio.fixture
async def store():
    from knowledge_platform.common.db import Database
    from knowledge_platform.llm.models import initialize
    repository = Path(__file__).resolve().parents[4]
    private = repository / 'knowledge-docker/.local/test-env.json'
    url = os.environ.get('TEST_LLM_DATABASE_URL')
    if not url:
        if not private.exists():
            import pytest
            pytest.skip('Real PostgreSQL LLM DSN required')
        url = json.loads(private.read_text())['databases']['llm']
    url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
    schema = 'test_llm_' + uuid4().hex
    admin = create_async_engine(url, hide_parameters=True)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_async_engine(url, connect_args={'options': f'-csearch_path={schema}'}, hide_parameters=True)
    try:
        await initialize(engine)
        yield Database(engine)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await admin.dispose()


@pytest_asyncio.fixture
async def api(store, tmp_path):
    from knowledge_platform.common.auth import HTTPAuthorizer
    from knowledge_platform.common.security import ServiceSecurity
    from knowledge_platform.llm.app import create_app
    from knowledge_platform.llm.schemas import LLMSettings
    names = ['llm', 'auth', 'gateway', 'retrieval', 'ingest', 'graphiti', 'agent', 'mcp']
    keys = {name: Ed25519PrivateKey.generate() for name in names}
    public = {name: key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode() for name, key in keys.items()}
    security = {name: ServiceSecurity(name, key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode(), public) for name, key in keys.items()}
    (tmp_path / 'public.json').write_text(json.dumps(public))
    (tmp_path / 'private.pem').write_text(security['llm'].private_key)
    settings = LLMSettings(service_private_key_file=str(tmp_path / 'private.pem'), service_public_keys_file=str(tmp_path / 'public.json'), credential_encryption_key=base64.b64encode(os.urandom(32)).decode())
    state = SimpleNamespace(permissions=['platform_admin'], scopes=['knowledge:read', 'knowledge:write'], auth_status=200, calls=[], status=200, handler=None, auth_body=None)
    async def auth_handle(request):
        assert security['auth'].verify(request.headers['x-service-token']) == 'llm'
        assert request.headers['authorization'] == 'Bearer opaque-user-token'
        return httpx.Response(state.auth_status, json=state.auth_body or {'principal': {'id': 'alice', 'subjects': ['user:alice'], 'permissions': state.permissions, 'auth_epoch': 1}, 'scopes': state.scopes})
    async def provider_handle(request):
        state.calls.append(request)
        if state.handler:
            return await state.handler(request)
        if state.status != 200:
            return httpx.Response(state.status, text='SECRET-provider-key prompt source-private', headers={'retry-after': '0'})
        body = json.loads(request.content)
        if request.url.path.endswith('/embeddings'):
            return httpx.Response(200, json={'data': [{'index': i, 'embedding': [1.0, 2.0]} for i, _ in enumerate(body['input'])], 'usage': {'prompt_tokens': 2, 'total_tokens': 2}}, headers={'x-request-id': 'provider_req_1'})
        if request.url.path.endswith('/rerank'):
            return httpx.Response(200, json={'results': [{'index': i, 'relevance_score': .8} for i in range(body['top_n'])]})
        return httpx.Response(200, json={'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(auth_handle)) as auth_http, httpx.AsyncClient(transport=httpx.MockTransport(provider_handle)) as provider:
        authorizer = HTTPAuthorizer(settings, auth_http)
        app = create_app(database=store, authorizer=authorizer, security=security['llm'], settings=settings, provider_client=provider, initialize_schema=False)
        def headers(caller='gateway', actor=True):
            result = {'X-Service-Token': security[caller].issue('llm')}
            if actor:
                result['Authorization'] = 'Bearer opaque-user-token'
            return result
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://llm') as client:
            yield SimpleNamespace(client=client, headers=headers, state=state, app=app, store=store)