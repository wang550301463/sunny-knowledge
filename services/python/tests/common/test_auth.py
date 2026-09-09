from pathlib import Path
import json

from fastapi import HTTPException
import httpx
import pytest

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.common.config import Settings
from knowledge_platform.common.security import ServiceSecurity
from test_security import pair


@pytest.fixture
def settings(tmp_path):
    private, public = pair()
    (tmp_path/'key.pem').write_text(private)
    (tmp_path/'keys.json').write_text(json.dumps({'knowledge':public,'auth':pair()[1],'iam':pair()[1]}))
    return Settings(service_name='knowledge',service_private_key_file=str(tmp_path/'key.pem'),service_public_keys_file=str(tmp_path/'keys.json'))


async def test_resolves_identity_from_live_auth_with_targeted_workload_token(settings):
    def handle(request):
        assert request.url == 'http://auth:8080/internal/v1/resolve'
        assert request.headers['authorization'] == 'Bearer end-user-token'
        security = ServiceSecurity.from_settings(settings)
        security.name = 'auth'
        assert security.verify(request.headers['x-service-token']) == 'knowledge'
        return httpx.Response(200,json={'principal':{'id':'alice','subjects':['user:alice'],'permissions':[],'auth_epoch':42},'scopes':['knowledge:read']})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        auth = HTTPAuthorizer(settings,client)
        assert (await auth.resolve('end-user-token')).auth_epoch == 42


async def test_deny_is_not_overridden_by_successful_http_status(settings):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,json={'allowed':False,'auth_epoch':4,'acl_version':2,'acl_domain':'domain'}))) as client:
        auth=HTTPAuthorizer(settings,client)
        with pytest.raises(HTTPException) as error:
            await auth.require('token','read','space','private')
        assert error.value.status_code == 403


@pytest.mark.parametrize('mode',['timeout','broken','unauthorized'])
async def test_authorization_failure_fails_closed_without_reflecting_upstream_body(settings,mode):
    def handle(request):
        if mode=='timeout': raise httpx.ReadTimeout('secret backend host',request=request)
        if mode=='broken': return httpx.Response(200,text='backend-private-body')
        return httpx.Response(401,text='backend-private-body')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        auth=HTTPAuthorizer(settings,client)
        with pytest.raises(HTTPException) as error:
            await auth.require('token','read','space','resource')
        assert error.value.status_code in {401,503}
        assert 'private' not in str(error.value.detail)


async def test_service_http_client_never_follows_untrusted_redirects(settings):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(302,headers={'location':'https://outside.example'}))) as client:
        auth=HTTPAuthorizer(settings,client)
        with pytest.raises(HTTPException): await auth.resolve('token')