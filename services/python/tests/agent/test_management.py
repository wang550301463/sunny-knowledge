import json

import httpx

from .test_boundaries import model_response
from .test_runtime import setup_run


async def test_model_receives_actual_scope_and_only_currently_permitted_tools(api):
    _,_,result,_=await setup_run(api,config={'space_ids':['engineering','restricted'],'model_configuration_id':'model1','tools':['search','get','feedback','propose_revision']})
    api.state.scopes=['knowledge:read']
    async def model(request,body):
        text=json.dumps(body['messages'])
        assert 'engineering' in text
        assert 'restricted' not in text
        assert {t['function']['name'] for t in body['tools']}=={'search','get'}
        return model_response(body,answer={'facts':[],'inferences':[],'gaps':['No evidence requested.']})
    api.state.chat_handler=model
    await api.app.state.worker.run_once()
    value=(await api.client.get('/api/v1/runs/'+result.json()['id'])).json()
    assert value['status']=='completed'


async def test_editors_can_choose_safe_chat_model_options_without_provider_credentials(api):
    async def upstream(request,body):
        if request.url.path=='/internal/v1/models':
            assert request.url.params['capability']=='chat'
            return httpx.Response(200,json={'items':[{'id':'qwen','name':'Qwen','configuration_id':'model1','capability':'chat','state':'active','provider_model':'qwen-plus','base_url':'http://PRIVATE-INTERNAL','has_credential':True,'test_state':'passed','capabilities':{'tools':True}}],'next_cursor':None})
    api.state.handler=upstream
    response=await api.client.get('/api/v1/agents/models')
    assert response.status_code==200,response.text
    assert response.json()['items'][0]['configuration_id']=='model1'
    assert 'PRIVATE-INTERNAL' not in response.text and 'has_credential' not in response.text


async def test_grant_administrator_can_inspect_a_private_configuration_before_publishing(api):
    a,_,_,_=await setup_run(api)
    api.state.principal='review-admin'
    api.state.permissions=['platform_admin']
    response=await api.client.get('/api/v1/agents/'+a['id'])
    assert response.status_code==200,response.text
    assert response.json()['configuration_id']==a['configuration_id']
    audit=await api.client.get('/api/v1/agents/'+a['id']+'/audit')
    assert audit.status_code==200
    assert audit.json()['items'][0]['action']=='created'
    api.state.denied.add(('read','engineering',None))
    assert (await api.client.get('/api/v1/agents/'+a['id'])).status_code==403