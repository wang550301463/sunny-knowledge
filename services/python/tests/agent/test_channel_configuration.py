from .test_runtime import setup_run


async def test_channel_can_only_inspect_current_published_config_with_real_bearer(api):
    a,_,_,_=await setup_run(api)
    path='/internal/v1/agents/'+a['id']+'/published'
    assert (await api.client.get(path,headers=api.headers('channel'))).status_code==404
    published=await api.client.post('/api/v1/agents/'+a['id']+'/publish',json={'base_configuration_id':a['configuration_id']})
    assert published.status_code==200
    update=await api.client.put('/api/v1/agents/'+a['id'],json={'base_configuration_id':a['configuration_id'],'name':'Draft','config':{'space_ids':['engineering'],'model_configuration_id':'model1','prompt':'UNPUBLISHED PRIVATE DRAFT'}})
    assert update.status_code==200
    response=await api.client.get(path,headers=api.headers('channel'))
    assert response.status_code==200,response.text
    assert response.json()['configuration_id']==a['configuration_id']
    assert response.json()['shared'] is True
    assert 'UNPUBLISHED PRIVATE DRAFT' not in response.text
    assert (await api.client.get(path,headers={**api.headers('channel'),'Authorization':''})).status_code==401
    assert (await api.client.get(path,headers=api.headers('mcp'))).status_code==403
    assert (await api.client.get('/api/v1/agents/'+a['id'],headers=api.headers('channel'))).status_code==403
    assert (await api.client.post('/internal/v1/sessions',json={},headers=api.headers('channel'))).status_code==403


async def test_channel_published_validation_does_not_grant_knowledge_scope(api):
    a,_,_,_=await setup_run(api,config={'space_ids':['engineering','restricted'],'model_configuration_id':'model1'})
    await api.client.post('/api/v1/agents/'+a['id']+'/publish',json={'base_configuration_id':a['configuration_id']})
    api.state.principal='channel-admin'
    api.state.permissions=['platform_admin']
    api.state.denied.add(('read','restricted',None))
    path='/internal/v1/agents/'+a['id']+'/published'
    response=await api.client.get(path,headers=api.headers('channel'))
    assert response.status_code==200
    assert response.json()['config']['space_ids']==['engineering']
    api.state.denied.add(('read','engineering',None))
    assert (await api.client.get(path,headers=api.headers('channel'))).status_code==403
    api.state.auth_status=503
    assert (await api.client.get(path,headers=api.headers('channel'))).status_code==503