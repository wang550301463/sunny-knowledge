import json
import httpx
import pytest
from knowledge_platform.ingest.clients import MachineTokens, InternalClient
from knowledge_platform.ingest.config import IngestSettings
from knowledge_platform.ingest.schemas import IngestError, SourceCreate
from .test_http import security

async def test_machine_token_uses_client_credentials_fresh_each_call_without_redirects():
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.url=='http://keycloak/realms/knowledge/protocol/openid-connect/token'
        assert b'grant_type=client_credentials' in request.content
        assert b'client_secret=private-value' in request.content
        return httpx.Response(200,json={'access_token':'machine-token-'+str(len(calls)),'token_type':'Bearer'})
    settings=IngestSettings(ingest_oidc_token_url='http://keycloak/realms/knowledge/protocol/openid-connect/token',ingest_oidc_client_id='ingest-worker',ingest_oidc_client_secret='private-value')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        machine=MachineTokens(settings,client)
        assert await machine.token() != await machine.token()
    assert len(calls)==2

async def test_oidc_error_never_reflects_provider_response_or_secret():
    settings=IngestSettings(ingest_oidc_token_url='http://keycloak/token',ingest_oidc_client_id='worker',ingest_oidc_client_secret='private-value')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(401,text='private-value upstream-body'))) as client:
        with pytest.raises(IngestError) as exc: await MachineTokens(settings,client).token()
        assert 'private-value' not in str(exc.value)
        assert exc.value.code=='worker_authorization_required'

async def test_internal_calls_sign_target_workload_and_forward_only_supplied_bearer(security):
    def handler(request):
        assert security['ingest'].verify(request.headers['X-Service-Token'])=='ingest'
        assert request.headers['Authorization']=='Bearer machine-only'
        return httpx.Response(200,json={'ok':True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        internal=InternalClient(IngestSettings(ingest_url='http://ingest'),security['ingest'],client)
        await internal.request('GET','/path','machine-only',target='ingest')

@pytest.mark.parametrize('config',[{'path':123,'content':'x'},{'path':'x.md','content':None},{'url':[],'ref':'HEAD'},{'url':'https://host/repo','ref':[]}])
def test_invalid_config_types_are_controlled_validation_errors(config):
    with pytest.raises(ValueError):
        SourceCreate(name='x',space_id='s',kind='git' if 'url' in config else 'markdown',config=config)