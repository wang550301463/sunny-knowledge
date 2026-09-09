"""Exercise real HTTPAuthorizer parsing/errors with PG-backed knowledge HTTP routes."""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.common.config import Settings
from knowledge_platform.common.security import ServiceSecurity
from knowledge_platform.knowledge.schemas import PageCreate, ProposalCreate

from .test_http import client_for
from .test_postgres import content, source

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def real_http_authorizer(tmp_path, blocked_resource, block_on, upstream_status):
    keys = {name: Ed25519PrivateKey.generate() for name in ('knowledge', 'auth', 'iam')}
    public = {name: key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode() for name, key in keys.items()}
    private = {name: key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode() for name, key in keys.items()}
    key_file = tmp_path / 'knowledge.pem'
    key_file.write_text(private['knowledge'])
    key_file.chmod(0o600)
    registry_file = tmp_path / 'public.json'
    registry_file.write_text(json.dumps(public))
    security = ServiceSecurity('auth', private['auth'], public)
    calls = []
    blocked_calls = 0

    async def auth_http(request):
        nonlocal blocked_calls
        assert security.verify(request.headers['x-service-token']) == 'knowledge'
        assert request.headers['authorization'] == 'Bearer alice'
        if request.url.path == '/internal/v1/resolve':
            return httpx.Response(200, json={'principal': {'id': 'alice', 'subjects': ['user:alice'], 'permissions': [], 'auth_epoch': 1}})
        assert request.url.path == '/internal/v1/authorize'
        body = json.loads(request.content)
        resource_id = body.get('resource_id')
        calls.append(resource_id)
        if resource_id == blocked_resource:
            blocked_calls += 1
            if blocked_calls >= block_on:
                if upstream_status == 200:
                    return httpx.Response(200, json={'allowed': False, 'auth_epoch': 1, 'acl_version': 1, 'acl_domain': 'same-epoch'})
                return httpx.Response(upstream_status, json={'error': {'code': 'account_disabled', 'message': 'Principal disabled'}})
        return httpx.Response(200, json={'allowed': True, 'auth_epoch': 1, 'acl_version': 1, 'acl_domain': 'same-epoch'})

    settings = Settings(service_name='knowledge', service_private_key_file=str(key_file), service_public_keys_file=str(registry_file), auth_url='http://auth')
    async with httpx.AsyncClient(transport=httpx.MockTransport(auth_http)) as http_client:
        yield HTTPAuthorizer(settings, client=http_client), calls


async def route_with_authorized_content_before_rejection(store, route):
    service, _, _ = store
    source_request, snapshot = await source(store, content='// PRIVATE_ALPHA\nfunc Pay() {}\n', resource='source:alpha')
    async with service() as knowledge:
        created = await knowledge.create_page('alice', PageCreate(id='page:a', space_id='finance', content=content(snapshot, 'PRIVATE_ALPHA')))
        first = await knowledge.approve('reviewer', created['proposal']['id'], 'Verified')

    if route == 'source_revision':
        async with service() as knowledge:
            await knowledge.register_snapshot('alice', 'ingest', source_request.model_copy(update={'path': 'z.go', 'resource_id': 'source:beta'}))
        return ('GET', f"/internal/v1/sources/{snapshot['source_id']}/revisions/{snapshot['source_revision']}", None, 'source:beta', 1)

    if route == 'history':
        async with service() as knowledge:
            proposal = await knowledge.propose('alice', 'page:a', ProposalCreate(base_revision=first['id'], content=content(snapshot, 'PRIVATE_ALPHA newest'), reason='Update'))
            await knowledge.approve('reviewer', proposal['id'], 'Verified')
        return ('GET', '/api/v1/pages/page:a/revisions', None, 'source:alpha', 2)

    if route == 'reviews':
        async with service() as knowledge:
            for index in range(2):
                await knowledge.propose('alice', 'page:a', ProposalCreate(base_revision=first['id'], content=content(snapshot, f'PRIVATE_ALPHA {index}'), reason='Pending'))
        return ('GET', '/api/v1/reviews', None, 'source:alpha', 3)

    _, beta = await source(store, revision='beta-revision', resource='source:beta')
    if route == 'evidence':
        refs = [content(item).claims[0].evidence[0].model_dump(mode='json') for item in (snapshot, beta)]
        return ('POST', '/internal/v1/evidence/authorize', {'evidence': refs}, 'source:beta', 1)

    async with service() as knowledge:
        created = await knowledge.create_page('alice', PageCreate(id='page:b', space_id='finance', content=content(beta)))
        second = await knowledge.approve('reviewer', created['proposal']['id'], 'Verified')
    if route == 'page_batch':
        body = {'pages': [{'page_id': 'page:a', 'revision_id': first['id']}, {'page_id': 'page:b', 'revision_id': second['id']}]}
        return ('POST', '/internal/v1/pages/authorize', body, 'page:b', 1)
    return ('GET', '/api/v1/pages', None, 'page:b', 1)


@pytest.mark.parametrize('upstream_status', [401, 403])
@pytest.mark.parametrize('route', ['evidence', 'page_batch', 'pages', 'history', 'reviews', 'source_revision'])
async def test_upstream_account_rejection_aborts_entire_collection(store, tmp_path, route, upstream_status):
    method, path, body, blocked_resource, block_on = await route_with_authorized_content_before_rejection(store, route)
    async with real_http_authorizer(tmp_path, blocked_resource, block_on, upstream_status) as (authorizer, calls):
        async with client_for(store, authorizer=authorizer) as (client, headers):
            response = await client.request(method, path, json=body, headers=headers())
    assert 'source:alpha' in calls  # An authorized private item was assembled before upstream denial.
    assert response.status_code == upstream_status
    assert set(response.json()) == {'error'}
    assert 'PRIVATE_ALPHA' not in response.text


async def test_only_same_epoch_resource_denial_can_filter_an_evidence_item(store, tmp_path):
    method, path, body, blocked_resource, block_on = await route_with_authorized_content_before_rejection(store, 'evidence')
    async with real_http_authorizer(tmp_path, blocked_resource, block_on, 200) as (authorizer, _):
        async with client_for(store, authorizer=authorizer) as (client, headers):
            response = await client.request(method, path, json=body, headers=headers())
    assert response.status_code == 200
    decisions = response.json()['decisions']
    assert decisions[0]['allowed'] and 'PRIVATE_ALPHA' in decisions[0]['excerpt']
    assert decisions[1]['allowed'] is False and 'excerpt' not in decisions[1]