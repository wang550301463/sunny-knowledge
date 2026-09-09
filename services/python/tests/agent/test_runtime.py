import asyncio
import json

import pytest
from sqlalchemy import select


async def setup_run(api, *, config=None, spaces=None, key='request1'):
    c = api.client
    a = await c.post('/api/v1/agents', json={'name':'R&D', 'owner_space_id':'engineering', 'config':config or {'space_ids':['engineering'], 'model_configuration_id':'model1'}})
    assert a.status_code == 201, a.text
    s = await c.post('/api/v1/sessions', json={})
    assert s.status_code == 201, s.text
    body = {'agent_id':a.json()['id'], 'session_id':s.json()['id'], 'question':'What calls Ledger?', 'space_ids':spaces or ['engineering'], 'idempotency_key':key}
    run = await c.post('/api/v1/runs', json=body)
    return a.json(), s.json(), run, body


async def test_real_pg_run_executes_tools_pins_config_erases_token_and_replays_events(api):
    from knowledge_platform.agent.models import Run
    a, s, result, body = await setup_run(api)
    assert result.status_code == 201, result.text
    run_id = result.json()['id']
    async with api.database.session() as session:
        run = await session.get(Run, run_id)
        assert run.encrypted_token and api.token not in run.encrypted_token
        assert run.configuration_id == a['configuration_id']
    assert await api.app.state.worker.run_once()
    answer = await api.client.get('/api/v1/runs/'+run_id)
    assert answer.status_code == 200, answer.text
    value = answer.json()
    assert value['status'] == 'completed'
    assert value['answer']['facts'][0]['text'] == 'Pay calls Ledger'
    assert len(value['citations']) == 1
    assert value['usage']['total_tokens'] == 18
    assert len(api.state.chat_calls) == 2
    assert all('hidden' not in json.dumps(m) for m in api.state.chat_calls)
    async with api.database.session() as session:
        run = await session.get(Run, run_id)
        assert run.encrypted_token is None
    replay = await api.client.get('/api/v1/runs/'+run_id+'/events')
    assert replay.status_code == 200
    events = [json.loads(x[6:]) for x in replay.text.splitlines() if x.startswith('data: ')]
    assert [e['seq'] for e in events] == list(range(1, len(events)+1))
    assert events[-1]['type'] == 'completed'
    cursor = events[-2]['seq']
    tail = await api.client.get('/api/v1/runs/'+run_id+'/events', headers={'Last-Event-ID':str(cursor)})
    assert len([x for x in tail.text.splitlines() if x.startswith('id: ')]) == 1
    assert (await api.client.post('/api/v1/runs', json=body)).json()['id'] == run_id
    assert (await api.client.post('/api/v1/runs', json={**body, 'question':'changed'})).status_code == 409


async def test_revocation_hides_whole_history_export_summary_and_prevents_model_reuse(api):
    _, s, result, _ = await setup_run(api)
    await api.app.state.worker.run_once()
    run_id = result.json()['id']
    summary = await api.client.post('/api/v1/sessions/'+s['id']+'/summaries', json={})
    assert summary.status_code == 201, summary.text
    api.state.source_allowed = False
    api.state.epoch += 1
    for path in ['/api/v1/runs/'+run_id, '/api/v1/sessions/'+s['id']+'/history', '/api/v1/sessions/'+s['id']+'/summaries', '/api/v1/runs/'+run_id+'/export', '/api/v1/runs/'+run_id+'/events']:
        response = await api.client.get(path)
        assert 'Pay calls Ledger' not in response.text
        assert 'What calls Ledger' not in response.text
        assert 'snapshot1' not in response.text
    api.state.auth_status = 503
    assert (await api.client.get('/api/v1/runs/'+run_id)).status_code == 503


async def test_admin_without_content_read_cannot_create_or_run(api):
    api.state.permissions = ['platform_admin']
    api.state.denied.add(('read', 'engineering', None))
    response = await api.client.post('/api/v1/agents', json={'name':'x', 'owner_space_id':'engineering', 'config':{'space_ids':['engineering']}})
    assert response.status_code == 403


async def test_scope_intersection_and_disabled_tools_are_enforced_after_model_output(api):
    _, _, result, _ = await setup_run(api, config={'space_ids':['engineering','restricted'], 'tool_space_ids':{'search':['restricted']}, 'model_configuration_id':'model1'})
    assert result.status_code == 201
    await api.app.state.worker.run_once()
    response = (await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
    assert response['status'] == 'failed'
    assert not [x for x in api.state.calls if x[0] == 'retrieval']


async def test_no_implicit_model_and_shared_publish_requires_grant(api):
    a, _, result, _ = await setup_run(api, config={'space_ids':['engineering']})
    assert result.status_code == 409
    api.state.denied.add(('grant','engineering',None))
    assert (await api.client.post('/api/v1/agents/'+a['id']+'/publish', json={'base_configuration_id':a['configuration_id']})).status_code == 403


async def test_configuration_cas_published_snapshot_and_run_freeze(api):
    a, _, result, _ = await setup_run(api)
    update = {'name':'Updated', 'base_configuration_id':a['configuration_id'], 'config':{'space_ids':['engineering'], 'model_configuration_id':'model1', 'prompt':'NEW PROMPT'}}
    results = await asyncio.gather(*[api.client.put('/api/v1/agents/'+a['id'], json=update) for _ in range(2)])
    assert sorted(r.status_code for r in results) == [200, 409]
    await api.app.state.worker.run_once()
    assert all('NEW PROMPT' not in json.dumps(c) for c in api.state.chat_calls)
    current = next(r.json() for r in results if r.status_code == 200)
    published = await api.client.post('/api/v1/agents/'+a['id']+'/publish', json={'base_configuration_id':current['configuration_id']})
    assert published.status_code == 200, published.text
    api.state.principal = 'bob'
    assert (await api.client.get('/api/v1/agents/'+a['id'])).status_code == 200
    assert (await api.client.get('/api/v1/runs/'+result.json()['id'])).status_code == 404


async def test_workload_boundary_scope_feedback_and_cancel_before_claim(api):
    _, _, result, _ = await setup_run(api)
    run_id = result.json()['id']
    for caller in ['channel','ingest']:
        assert (await api.client.get('/api/v1/runs/'+run_id, headers=api.headers(caller))).status_code == 403
    assert (await api.client.get('/api/v1/runs/'+run_id, headers=api.headers('mcp'))).status_code == 200
    assert (await api.client.post('/api/v1/runs/'+run_id+'/cancel', json={})).status_code == 200
    assert not await api.app.state.worker.run_once()
    api.state.scopes.remove('knowledge:feedback')
    assert (await api.client.post('/api/v1/feedback', json={'run_id':run_id, 'rating':'incorrect', 'idempotency_key':'fb1'})).status_code == 403
    api.state.scopes.append('knowledge:feedback')
    assert (await api.client.post('/api/v1/feedback', json={'run_id':run_id, 'rating':'incorrect', 'idempotency_key':'fb1'})).status_code == 201
    retried = await api.client.post('/api/v1/runs/'+run_id+'/retry', json={'idempotency_key':'retry1'})
    assert retried.status_code == 201 and retried.json()['id'] != run_id