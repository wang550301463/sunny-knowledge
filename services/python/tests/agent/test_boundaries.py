import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from knowledge_platform.agent.models import Event, Run, now
from knowledge_platform.agent.schemas import AgentError
from knowledge_platform.agent.streaming import event_stream

from .test_runtime import setup_run


def model_response(body, *, calls=None, answer=None, content=None):
    value = {'type':'completed','configuration_id':body['configuration_id'],'invocation_id':'test-invocation','content':content or (json.dumps(answer) if answer else None),'tool_calls':calls or [],'finish_reason':'tool_calls' if calls else 'stop','usage':{'total_tokens':3}}
    return httpx.Response(200,headers={'content-type':'text/event-stream'},text='event: completed\ndata: '+json.dumps(value)+'\n\n')


def call(cid,name,args):
    return {'id':cid,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}


async def test_event_replay_reauthorizes_between_frames_after_slow_reader_revocation(api):
    _,_,result,_ = await setup_run(api)
    await api.app.state.worker.run_once()
    stream = event_stream(api.app.state.service,api.token,result.json()['id'],0)
    first = await anext(stream)
    assert 'event: queued' in first
    api.state.source_allowed = False
    api.state.epoch += 1
    remaining = ''.join([frame async for frame in stream])
    assert 'Pay calls Ledger' not in remaining
    assert 'event: error' in remaining


async def test_parallel_read_failure_cancels_and_awaits_its_sibling(api):
    _,_,_,_ = await setup_run(api)
    started, closed = asyncio.Event(), asyncio.Event()
    async def model(request,body):
        return model_response(body,calls=[call('one','search',{'query':'fail','space_ids':['engineering']}),call('two','search',{'query':'wait','space_ids':['engineering']})])
    async def upstream(request,body):
        if request.url.host == 'retrieval':
            if body['query'] == 'fail':
                await started.wait()
                return httpx.Response(403)
            started.set()
            try:
                await asyncio.sleep(20)
            finally:
                closed.set()
    api.state.chat_handler,api.state.handler = model,upstream
    await asyncio.wait_for(api.app.state.worker.run_once(),5)
    assert closed.is_set(), 'Orphaned read HTTP task survives a failed run'


async def test_common_original_evidence_contract_is_reused():
    from knowledge_platform.agent.schemas import EvidenceRef
    from knowledge_platform.common.evidence import EvidenceRef as CanonicalEvidenceRef
    assert EvidenceRef is CanonicalEvidenceRef


async def test_invalid_model_citation_never_becomes_answer(api):
    _,_,result,_ = await setup_run(api)
    async def model(request,body):
        return model_response(body,answer={'facts':[{'text':'Made up','citation_ids':['invented']}],'inferences':[],'gaps':[]})
    api.state.chat_handler = model
    await api.app.state.worker.run_once()
    response = (await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
    assert response['status']=='failed' and response['answer'] is None
    assert response['error_code']=='unsupported_citation'


async def test_current_source_recheck_after_model_prevents_output_and_clears_delegation(api):
    _,_,result,_ = await setup_run(api)
    async def model(request,body):
        if not any(m['role']=='tool' for m in body['messages']):
            return model_response(body,calls=[call('one','search',{'query':'q','space_ids':['engineering']})])
        cid = json.loads(next(m['content'] for m in body['messages'] if m['role']=='tool'))['citations'][0]['id']
        api.state.source_allowed=False
        api.state.epoch+=1
        return model_response(body,answer={'facts':[{'text':'Private answer','citation_ids':[cid]}],'inferences':[],'gaps':[]})
    api.state.chat_handler=model
    await api.app.state.worker.run_once()
    response = await api.client.get('/api/v1/runs/'+result.json()['id'])
    assert response.json()['content_hidden'] is True
    assert 'Private answer' not in response.text
    async with api.database.session() as session:
        row = await session.get(Run,result.json()['id'])
        assert row.encrypted_token is None and row.answer is None
        events = list(await session.scalars(select(Event).where(Event.run_id==row.id)))
        assert 'Private answer' not in json.dumps([event.data for event in events])


async def test_expired_queued_run_erases_secret_without_calling_provider(api):
    _,_,result,_ = await setup_run(api)
    async with api.database.session() as session, session.begin():
        await session.execute(update(Run).where(Run.id==result.json()['id']).values(deadline=now()-timedelta(seconds=1)))
    assert await api.app.state.worker.run_once()
    async with api.database.session() as session:
        row = await session.get(Run,result.json()['id'])
        assert row.status=='failed' and row.encrypted_token is None
    assert not api.state.chat_calls


async def test_stale_lease_cannot_save_after_another_worker_claims(api):
    _,_,result,_ = await setup_run(api)
    store = api.app.state.service.store
    old = await store.claim()
    async with api.database.session() as session, session.begin():
        await session.execute(update(Run).where(Run.id==result.json()['id']).values(lease_until=now()-timedelta(seconds=1)))
    new = await store.claim()
    assert new['lease_token'] != old['lease_token']
    with pytest.raises(AgentError,match='lease'):
        await store.save(old,terminal='completed',answer={'facts':[],'inferences':[],'gaps':[]})
    await api.app.state.worker.process(new)
    assert (await api.client.get('/api/v1/runs/'+result.json()['id'])).json()['status']=='completed'


async def test_restart_during_unknown_model_call_is_explicit_partial_not_blind_repeat(api):
    _,_,result,_ = await setup_run(api)
    store = api.app.state.service.store
    old = await store.claim()
    old['checkpoint']={'phase':'model_inflight','rounds':1,'tool_calls':0}
    await store.save(old)
    async with api.database.session() as session, session.begin():
        await session.execute(update(Run).where(Run.id==result.json()['id']).values(lease_until=now()-timedelta(seconds=1)))
    await api.app.state.worker.run_once()
    response=(await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
    assert response['status']=='partial' and response['error_code']=='model_interrupted'
    assert not api.state.chat_calls


async def test_model_round_limit_returns_existing_evidence_without_unsupported_conclusions(api):
    _,_,result,_ = await setup_run(api,config={'space_ids':['engineering'],'model_configuration_id':'model1','budget':{'model_rounds':1}})
    await api.app.state.worker.run_once()
    response=(await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
    assert response['status']=='partial' and response['error_code']=='budget_reached'
    assert response['rounds']==1 and response['tool_calls']==1
    assert response['answer']['facts'][0]['text']=='Pay calls Ledger'


async def test_schema_unknown_fields_and_identity_forgery_are_rejected_without_input_echo(api):
    response=await api.client.post('/api/v1/sessions',json={'principal':'other-user-PRIVATE'})
    assert response.status_code==422 and 'other-user-PRIVATE' not in response.text
    response=await api.client.post('/internal/v1/sessions',json={},headers=api.headers('gateway'))
    assert response.status_code==403
    response=await api.client.post('/internal/v1/sessions',json={},headers=api.headers('mcp'))
    assert response.status_code==201


async def test_epoch_change_during_composite_read_fails_whole_request(api):
    _,_,result,_ = await setup_run(api)
    await api.app.state.worker.run_once()
    async def upstream(request,body):
        if request.url.path.endswith('/evidence/authorize'):
            api.state.epoch+=1
    api.state.handler=upstream
    response=await api.client.get('/api/v1/runs/'+result.json()['id'])
    assert response.status_code==503
    assert 'Pay calls Ledger' not in response.text