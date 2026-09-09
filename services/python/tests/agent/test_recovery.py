import asyncio
from datetime import timedelta
from copy import deepcopy

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

from knowledge_platform.agent.models import Configuration, Event, Run, now
from knowledge_platform.agent.worker import Worker

from .test_boundaries import call, model_response
from .test_runtime import setup_run


async def test_restart_after_validated_tool_intent_resumes_without_duplicate_model_decision(api):
    _,_,result,_=await setup_run(api)
    store=api.app.state.service.store
    original=store.save
    interrupted=False
    async def save(run,**kwargs):
        nonlocal interrupted
        await original(run,**kwargs)
        if not interrupted and kwargs.get('event_data',{}).get('status')=='tools_requested':
            interrupted=True
            raise asyncio.CancelledError
    store.save=save
    with pytest.raises(asyncio.CancelledError):
        await api.app.state.worker.run_once()
    store.save=original
    async with api.database.session() as session,session.begin():
        row=await session.get(Run,result.json()['id'])
        assert row.checkpoint['phase']=='tools'
        assert row.encrypted_token
        await session.execute(update(Run).where(Run.id==row.id).values(lease_until=now()-timedelta(seconds=1)))
    restarted=Worker(api.app.state.service,store)
    await restarted.run_once()
    value=(await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
    assert value['status']=='completed' and value['rounds']==2 and value['tool_calls']==1
    assert len(api.state.chat_calls)==2
    async with api.database.session() as session:
        events=list(await session.scalars(select(Event).where(Event.run_id==value['id']).order_by(Event.seq)))
        assert [e.seq for e in events]==list(range(1,len(events)+1))


async def test_concurrent_claims_never_execute_the_same_run_twice(api):
    _,_,result,_=await setup_run(api)
    claims=await asyncio.gather(*[api.app.state.service.store.claim() for _ in range(5)])
    assert len([claim for claim in claims if claim])==1
    owner=next(claim for claim in claims if claim)
    await api.app.state.worker.process(owner)
    assert (await api.client.get('/api/v1/runs/'+result.json()['id'])).json()['status']=='completed'


async def test_wrong_encryption_key_fails_closed_without_using_machine_identity(api):
    from knowledge_platform.common.secrets import SecretBox
    import base64
    import os
    _,_,result,_=await setup_run(api)
    api.app.state.service.box=SecretBox(base64.b64encode(os.urandom(32)).decode())
    await api.app.state.worker.run_once()
    async with api.database.session() as session:
        row=await session.get(Run,result.json()['id'])
        assert row.status=='failed' and row.encrypted_token is None
    assert not api.state.chat_calls


async def test_configurations_and_event_history_are_database_immutable(api):
    a,_,result,_=await setup_run(api)
    async with api.database.session() as session:
        with pytest.raises(DBAPIError),session.begin():
            await session.execute(update(Configuration).where(Configuration.id==a['configuration_id']).values(name='overwritten'))
    async with api.database.session() as session:
        with pytest.raises(DBAPIError),session.begin():
            await session.execute(update(Event).where(Event.run_id==result.json()['id']).values(data={'fake':'event'}))


async def test_at_most_two_read_tools_are_in_flight(api):
    _,_,result,_=await setup_run(api,config={'space_ids':['engineering'],'model_configuration_id':'model1','budget':{'model_rounds':1}})
    active,maximum=0,0
    async def model(request,body):
        return model_response(body,calls=[call('search'+str(i),'search',{'query':str(i),'space_ids':['engineering']}) for i in range(5)])
    async def upstream(request,body):
        nonlocal active,maximum
        if request.url.host=='retrieval':
            active+=1
            maximum=max(maximum,active)
            await asyncio.sleep(.03)
            active-=1
            return httpx.Response(200,json={'items':[],'degraded':[]})
    api.state.chat_handler,api.state.handler=model,upstream
    await api.app.state.worker.run_once()
    value=(await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
    assert maximum==2 and active==0
    assert value['tool_calls']==5 and value['status']=='partial'


async def test_source_text_cannot_expand_the_allowed_tool_set(api):
    _,_,result,_=await setup_run(api)
    async def model(request,body):
        # Even if a provider follows malicious source instructions, execution revalidates names.
        return model_response(body,calls=[call('malicious','execute_shell',{'command':'exfiltrate'})])
    api.state.chat_handler=model
    await api.app.state.worker.run_once()
    value=(await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
    assert value['status']=='failed' and value['error_code']=='invalid_tool_call'
    assert not [c for c in api.state.calls if c[0] in {'knowledge','retrieval'}]