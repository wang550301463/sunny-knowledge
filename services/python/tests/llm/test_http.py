import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.asyncio


def model_body(capability='embedding', **kwargs):
    return dict(name='My model', provider='cohere' if capability == 'rerank' else 'openai', provider_model='provider-version-one', base_url='https://provider.test/v1', capability=capability, dimensions=2 if capability == 'embedding' else None, credential='SECRET-provider-key', **kwargs)


async def create(api, capability='embedding', **kwargs):
    response = await api.client.post('/api/v1/models', json=model_body(capability, **kwargs), headers=api.headers())
    assert response.status_code == 201, response.text
    return response.json()


async def test_management_live_admin_and_scope_fail_closed(api):
    for path in ['/api/v1/models', '/api/v1/models/' + str(uuid4())]:
        assert (await api.client.get(path, headers=api.headers(actor=False))).status_code == 401
        api.state.permissions = []
        assert (await api.client.get(path, headers=api.headers())).status_code == 403
        api.state.permissions = ['platform_admin']
        api.state.scopes = []
        assert (await api.client.get(path, headers=api.headers())).status_code == 403
        api.state.scopes = ['knowledge:read']
        api.state.auth_status = 503
        assert (await api.client.get(path, headers=api.headers())).status_code == 503
        api.state.auth_status, api.state.scopes = 200, ['knowledge:read', 'knowledge:write']
    api.state.scopes = ['knowledge:read']
    assert (await api.client.post('/api/v1/models', json=model_body(), headers=api.headers())).status_code == 403
    api.state.auth_body = {'principal': {'id': 'alice', 'subjects': [], 'permissions': ['platform_admin'], 'auth_epoch': 1}, 'scopes': 'knowledge:read'}
    assert (await api.client.get('/api/v1/models', headers=api.headers())).status_code == 503
    assert (await api.client.get('/api/v1/models', headers={'X-Service-Token': 'fake'})).status_code == 401


async def test_ciphertext_secret_preserved_rotation_and_immutable_cas(api):
    from knowledge_platform.llm.models import Configuration, Audit
    from knowledge_platform.llm.schemas import ModelConfig
    model = await create(api)
    assert 'SECRET-provider-key' not in json.dumps(model)
    assert model['has_credential'] is True
    assert model['test_state'] == 'untested'
    cid = model['configuration_id']
    async with api.store.session() as session:
        before = await session.get(Configuration, cid)
        assert before.credential_ciphertext.startswith('v1:')
        assert 'SECRET' not in before.credential_ciphertext
    config = ModelConfig.model_validate({k: v for k, v in model_body().items() if k != 'credential'}).model_dump()
    config['provider_model'] = 'provider-version-two'
    body = {'base_configuration_id': cid, 'config': config}
    results = await asyncio.gather(*[api.client.put('/api/v1/models/' + model['id'], json=body, headers=api.headers()) for _ in range(2)])
    assert sorted(r.status_code for r in results) == [200, 409]
    updated = next(r.json() for r in results if r.status_code == 200)
    assert updated['configuration_id'] != cid and updated['version'] == 2
    for configuration, expected in [(cid, 'provider-version-one'), (updated['configuration_id'], 'provider-version-two')]:
        response = await api.client.post('/internal/v1/embeddings', headers=api.headers('retrieval', False), json={'configuration_id': configuration, 'input': ['private prompt']})
        assert response.status_code == 200, response.text
        assert json.loads(api.state.calls[-1].content)['model'] == expected
        assert api.state.calls[-1].headers['authorization'] == 'Bearer SECRET-provider-key'
    body['base_configuration_id'] = updated['configuration_id']
    body['credential'] = 'rotated-key'
    rotated = await api.client.put('/api/v1/models/' + model['id'], headers=api.headers(), json=body)
    assert rotated.status_code == 200
    response = await api.client.post('/internal/v1/embeddings', headers=api.headers('retrieval', False), json={'configuration_id': rotated.json()['configuration_id'], 'input': ['q']})
    assert response.status_code == 200
    assert api.state.calls[-1].headers['authorization'] == 'Bearer rotated-key'
    async with api.store.session() as session:
        audits = list((await session.scalars(select(Audit))).all())
        assert len(audits) == 3
        assert 'rotated-key' not in json.dumps([a.details for a in audits])
        with pytest.raises(DBAPIError):
            await session.execute(text('UPDATE llm_configurations SET version=99 WHERE id=:id'), {'id': cid})
        await session.rollback()


async def test_workload_allowlists_explicit_config_and_retirement(api):
    model = await create(api)
    body = {'configuration_id': model['configuration_id'], 'input': ['q']}
    for caller in ['gateway', 'agent', 'mcp']:
        response = await api.client.post('/internal/v1/embeddings', json=body, headers=api.headers(caller))
        assert response.status_code == 403
    for caller in ['retrieval', 'ingest', 'graphiti']:
        assert (await api.client.post('/internal/v1/embeddings', json=body, headers=api.headers(caller, False))).status_code == 200
    assert (await api.client.post('/internal/v1/embeddings', json={'input': ['q']}, headers=api.headers('retrieval', False))).status_code == 422
    assert (await api.client.get('/internal/v1/models?capability=chat', headers=api.headers('retrieval', False))).status_code == 403
    response = await api.client.patch('/api/v1/models/' + model['id'] + '/state', json={'base_configuration_id': model['configuration_id'], 'state': 'retired'}, headers=api.headers())
    assert response.status_code == 200
    assert (await api.client.post('/internal/v1/embeddings', json=body, headers=api.headers('retrieval', False))).status_code == 409
    discovery = await api.client.get('/internal/v1/models?capability=embedding', headers=api.headers('retrieval', False))
    assert discovery.json()['items'] == []
    history = await api.client.get('/api/v1/models/' + model['id'] + '/versions', headers=api.headers())
    assert len(history.json()['items']) == 2
    assert 'SECRET' not in history.text


async def test_test_endpoint_detects_capability_and_records_safe_usage(api):
    from knowledge_platform.llm.models import UsageOutcome, Invocation
    model = await create(api)
    path = '/api/v1/models/' + model['id'] + '/test'
    response = await api.client.post(path, headers=api.headers(), json={'configuration_id': model['configuration_id']})
    assert response.status_code == 200, response.text
    assert response.json()['test_state'] == 'passed'
    assert response.json()['capabilities']['embedding'] is True
    assert response.json()['dimensions'] == 2
    api.state.status = 401
    response = await api.client.post(path, headers=api.headers(), json={'configuration_id': model['configuration_id']})
    assert response.status_code == 200
    assert response.json()['test_state'] == 'failed'
    assert response.json()['error_code'] == 'provider_rejected'
    assert 'SECRET' not in response.text and 'source-private' not in response.text
    async with api.store.session() as session:
        calls = list((await session.scalars(select(Invocation))).all())
        outcomes = list((await session.scalars(select(UsageOutcome))).all())
        assert len(calls) == len(outcomes) == 2
        assert {o.outcome for o in outcomes} == {'succeeded', 'provider_rejected'}
        assert all(o.duration_ms >= 0 for o in outcomes)
        successful = next(o for o in outcomes if o.outcome == 'succeeded')
        assert successful.provider_request_id == 'provider_req_1'
        assert successful.usage == {'prompt_tokens': 2, 'total_tokens': 2}


async def test_validation_redacts_secrets_and_rejects_unsafe_urls(api):
    body = model_body()
    for url in ['https://user:SECRET-provider-key@example.com/v1', 'https://example.com?key=SECRET-provider-key', 'file:///etc/passwd', 'http://provider.test/v1']:
        body['base_url'] = url
        response = await api.client.post('/api/v1/models', json=body, headers=api.headers())
        assert response.status_code == 422
        assert 'SECRET' not in response.text
    body = model_body()
    body['credential'] = '\r\nSECRET-provider-key'
    response = await api.client.post('/api/v1/models', json=body, headers=api.headers())
    assert response.status_code == 422
    assert 'SECRET' not in response.text


async def test_deadline_and_cancellation_persist_outcome_and_release(api):
    from knowledge_platform.llm.models import UsageOutcome
    model = await create(api, timeout_seconds=.08, concurrency_per_replica=1, max_queue_per_replica=1)
    entered = asyncio.Event()
    async def hang(request):
        entered.set()
        await asyncio.Event().wait()
    api.state.handler = hang
    body = {'configuration_id': model['configuration_id'], 'input': ['secret-prompt']}
    response = await api.client.post('/internal/v1/embeddings', headers=api.headers('retrieval', False), json=body)
    assert response.status_code == 504, response.text
    api.state.handler = None
    assert (await api.client.post('/internal/v1/embeddings', headers=api.headers('retrieval', False), json=body)).status_code == 200
    async with api.store.session() as session:
        assert 'provider_timeout' in set((await session.scalars(select(UsageOutcome.outcome))).all())
    api.state.handler = hang
    entered.clear()
    task = asyncio.create_task(api.app.state.inference.infer('retrieval', 'embedding', body))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with api.store.session() as session:
        assert 'cancelled' in set((await session.scalars(select(UsageOutcome.outcome))).all())