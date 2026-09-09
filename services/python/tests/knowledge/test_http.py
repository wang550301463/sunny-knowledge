import hashlib
from contextlib import asynccontextmanager

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from fastapi import HTTPException

from .test_postgres import content, publish, source, store  # noqa: F401

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def client_for(store):
    from knowledge_platform.common.db import Database
    from knowledge_platform.common.security import ServiceSecurity
    from knowledge_platform.knowledge.app import create_app
    names = ('knowledge', 'gateway', 'ingest', 'retrieval', 'graphiti', 'agent')
    keys = {name: Ed25519PrivateKey.generate() for name in names}
    public = {name: key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode() for name, key in keys.items()}
    security = {name: ServiceSecurity(name, key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode(), public) for name, key in keys.items()}
    _, auth, sessions = store
    app = create_app(database=Database(sessions.kw['bind']), authorizer=auth, security=security['knowledge'], initialize_schema=False)
    def headers(caller='gateway', actor='alice'):
        result = {'X-Service-Token': security[caller].issue('knowledge')}
        if actor:
            result['Authorization'] = 'Bearer ' + actor
        return result
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://knowledge') as client:
            yield client, headers


async def test_all_public_routes_require_workload_and_delegated_identity(store):
    async with client_for(store) as (client, headers):
        for path in ('/api/v1/pages', '/api/v1/reviews', '/api/v1/pages/missing', '/api/v1/pages/missing/revisions'):
            response = await client.get(path, headers={'Authorization': 'Bearer alice', 'X-Service-Token': 'forged'})
            assert response.status_code == 401
            assert 'error' in response.json()
            response = await client.get(path, headers=headers(actor=None))
            assert response.status_code == 401
        assert (await client.get('/healthz')).status_code == 200


async def test_public_page_create_ignores_spoofed_principal_and_creates_review(store):
    _, snapshot = await source(store)
    async with client_for(store) as (client, headers):
        h = headers()
        h['X-Principal-Id'] = 'reviewer'
        response = await client.post('/api/v1/pages', headers=h, json={'id': 'page:http', 'space_id': 'finance', 'content': content(snapshot).model_dump(mode='json')})
        assert response.status_code == 201, response.text
        result = response.json()
        assert result['page']['current_revision'] is None
        assert result['proposal']['proposed_by'] == 'alice'
        response = await client.post(f"/api/v1/reviews/{result['proposal']['id']}/approve", headers=headers(actor='reviewer'), json={'reason': 'Verified'})
        assert response.status_code == 200
        assert response.json()['number'] == 1
        page = await client.get('/api/v1/pages/page:http', headers=headers())
        assert page.json()['revision']['id'] == response.json()['id']


async def test_projection_workload_capability_cannot_be_used_by_agent_or_gateway(store):
    revision, _ = await publish(store)
    path = f"/internal/v1/projections/pages/page:payments/revisions/{revision['id']}"
    async with client_for(store) as (client, headers):
        for caller in ('agent', 'gateway'):
            response = await client.get(path, headers=headers(caller=caller))
            assert response.status_code == 403
        response = await client.get(path, headers=headers(caller='retrieval', actor=None))
        assert response.status_code == 200
        assert response.json()['content']['markdown']


async def test_internal_publish_rejects_gateway_even_with_reviewer_bearer(store):
    _, snapshot = await source(store)
    async with client_for(store) as (client, headers):
        response = await client.post('/internal/v1/pages/publish', headers=headers(actor='reviewer'), json={'page_id': 'page:auto', 'space_id': 'finance', 'base_revision': None, 'content': content(snapshot).model_dump(mode='json'), 'proof': {'compiler_version': 'v1', 'schema_version': 'v2', 'snapshot_ids': [snapshot['id']], 'idempotency_key': 'http:auto'}})
        assert response.status_code == 403


async def test_auth_unavailable_fails_closed_for_batch_and_directory(store):
    revision, _ = await publish(store)
    _, auth, _ = store
    async def unavailable(*args, **kwargs):
        raise HTTPException(503, 'Required internal service unavailable')
    auth.require = unavailable
    async with client_for(store) as (client, headers):
        response = await client.get('/api/v1/pages', headers=headers())
        assert response.status_code == 503
        assert 'Pay is defined' not in response.text
        response = await client.post('/internal/v1/pages/authorize', headers=headers(caller='retrieval'), json={'pages': [{'page_id': 'page:payments', 'revision_id': revision['id']}]})
        assert response.status_code == 503


async def test_source_registration_requires_ingest_and_read_reauthorizes_source(store):
    data = dict(source_id='git:http', source_revision='r1', resource_id='source:http', space_id='finance', path='main.go', kind='code', text='package main', sha256=hashlib.sha256(b'package main').hexdigest(), object_key='raw/http/r1/main.go')
    async with client_for(store) as (client, headers):
        assert (await client.post('/internal/v1/sources/snapshots', headers=headers(), json=data)).status_code == 403
        response = await client.post('/internal/v1/sources/snapshots', headers=headers('ingest'), json=data)
        assert response.status_code == 201
        snapshot = response.json()
        store[1].denied.add(('alice', 'read', 'source:http'))
        response = await client.get('/api/v1/source-snapshots/' + snapshot['id'], headers=headers())
        assert response.status_code == 403