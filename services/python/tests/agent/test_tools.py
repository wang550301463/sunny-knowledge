import json

import httpx

from .test_boundaries import call, model_response
from .test_runtime import setup_run


async def test_formal_change_is_only_an_idempotent_review_proposal_with_observed_originals(api):
    _,_,result,_ = await setup_run(api,config={'space_ids':['engineering'],'mode':'maintenance','model_configuration_id':'model1'})
    async def model(request,body):
        tool_messages = [m for m in body['messages'] if m['role']=='tool']
        if not tool_messages:
            return model_response(body,calls=[call('search','search',{'query':'q','space_ids':['engineering']})])
        evidence = json.loads(tool_messages[0]['content'])['citations'][0]['id']
        if len(tool_messages)==1:
            return model_response(body,calls=[call('proposal','propose_revision',{'space_id':'engineering','page_id':'page:pay','base_revision':'revision1','title':'Payment','markdown':'Proposed new conclusion','reason':'Original source conflicts with narrative','citation_ids':[evidence]})])
        proposal = json.loads(tool_messages[-1]['content'])
        assert proposal['status']=='pending' and proposal['published'] is False
        return model_response(body,answer={'facts':[{'text':'Pay calls Ledger','citation_ids':[evidence]}],'inferences':[],'gaps':['Proposal requires human review.']})
    api.state.chat_handler=model
    await api.app.state.worker.run_once()
    value=(await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
    assert value['status']=='completed',value
    assert len(api.state.proposal_calls)==1
    request=api.state.proposal_calls[0]
    assert request['kind']=='formal_change' and request['base_revision']=='revision1'
    assert request['content']['evidence']==[api.reference]
    assert request['idempotency_key'].startswith('agent-')
    assert not [c for c in api.state.calls if c[1]=='POST' and any(x in c[2] for x in ('/publish','/approve','/validity'))]


async def test_proposal_cannot_introduce_unseen_citation_or_scope(api):
    _,_,result,_=await setup_run(api,config={'space_ids':['engineering'],'mode':'maintenance','model_configuration_id':'model1'})
    async def model(request,body):
        return model_response(body,calls=[call('bad','propose_revision',{'space_id':'engineering','page_id':'page:pay','base_revision':'revision1','title':'Payment','markdown':'Unsupported','reason':'Source says so','citation_ids':['invented']})])
    api.state.chat_handler=model
    await api.app.state.worker.run_once()
    value=(await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
    assert value['status']=='failed'
    assert not api.state.proposal_calls


async def test_explicit_historical_get_can_inspect_stale_version_without_claiming_it_current(api):
    _,_,result,_=await setup_run(api)
    async def upstream(request,body):
        if request.url.path.endswith('/pages/authorize'):
            return httpx.Response(200,json={'decisions':[{**p,'space_id':'engineering','allowed':False,'authorized':True,'state':'stale','is_current':False} for p in body['pages']]})
        if request.url.path.endswith('/revisions/old-revision'):
            return httpx.Response(200,json={'id':'old-revision','page_id':'page:pay','number':1,'content':{'title':'Payment','markdown':'Historic function','entity_type':'Module','claims':[],'evidence':[api.reference],'state':'stale'}})
    async def model(request,body):
        if not any(m['role']=='tool' for m in body['messages']):
            return model_response(body,calls=[call('history','get',{'space_id':'engineering','page_id':'page:pay','revision_id':'old-revision'})])
        data=json.loads(next(m['content'] for m in body['messages'] if m['role']=='tool'))
        assert data['items'][0]['historical'] is True
        assert data['items'][0]['state']=='stale'
        return model_response(body,answer={'facts':[],'inferences':[],'gaps':['This revision is stale; no current or production conclusion is established.']})
    api.state.handler,api.state.chat_handler=upstream,model
    await api.app.state.worker.run_once()
    value=(await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
    assert value['status']=='completed',value


async def test_all_context_dependencies_are_retained_even_uncited_source(api):
    _,_,result,_=await setup_run(api)
    async def model(request,body):
        if not any(m['role']=='tool' for m in body['messages']):
            return model_response(body,calls=[call('search','search',{'query':'q','space_ids':['engineering']})])
        return model_response(body,answer={'facts':[],'inferences':[],'gaps':['No supported conclusion after consulting a source.']})
    api.state.chat_handler=model
    await api.app.state.worker.run_once()
    api.state.source_allowed=False
    api.state.epoch+=1
    response=await api.client.get('/api/v1/runs/'+result.json()['id'])
    assert response.json()['content_hidden'] is True
    assert 'consulting a source' not in response.text


async def test_prior_revoked_answer_does_not_enter_subsequent_run_model_context(api):
    _,_,result,body=await setup_run(api)
    await api.app.state.worker.run_once()
    api.state.source_allowed=False
    api.state.epoch+=1
    second=await api.client.post('/api/v1/runs',json={**body,'question':'A new question','idempotency_key':'second'})
    assert second.status_code==201
    async def model(request,payload):
        assert 'Pay calls Ledger' not in json.dumps(payload['messages'])
        assert 'What calls Ledger' not in json.dumps(payload['messages'])
        return model_response(payload,answer={'facts':[],'inferences':[],'gaps':['No authorized evidence.']})
    api.state.chat_handler=model
    await api.app.state.worker.run_once()
    assert (await api.client.get('/api/v1/runs/'+second.json()['id'])).json()['status']=='completed'


async def test_private_agent_is_invisible_to_other_editor_until_published(api):
    a,_,_,_=await setup_run(api)
    api.state.principal='bob'
    assert (await api.client.get('/api/v1/agents/'+a['id'])).status_code==404
    assert (await api.client.get('/api/v1/agents')).json()['items']==[]


async def test_summary_is_versioned_idempotent_and_not_independent_evidence(api):
    _,s,_,_=await setup_run(api)
    await api.app.state.worker.run_once()
    first=await api.client.post('/api/v1/sessions/'+s['id']+'/summaries',json={})
    again=await api.client.post('/api/v1/sessions/'+s['id']+'/summaries',json={})
    assert first.json()['id']==again.json()['id']
    assert first.json()['version']==1 and first.json()['independent_evidence_count']==0
    assert 'confidence' not in first.text