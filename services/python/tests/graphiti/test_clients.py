from types import SimpleNamespace

import httpx
import pytest

from .test_traversal import records, request, Auth


@pytest.mark.asyncio
async def test_canonical_batch_checks_complete_identity_and_revocation():
    from knowledge_platform.graphiti.authorization import AuthorizationGuard
    from knowledge_platform.graphiti.clients import Clients
    from knowledge_platform.graphiti.schemas import GraphError
    auth = Auth()
    rows = records()
    async def call(method, path, target, token=None, body=None):
        return {'decisions':[{'page_id':r['graph'].page_id, 'revision_id':r['graph'].revision_id, 'space_id':'engineering', 'allowed':True} for r in rows]}
    c = Clients(None, auth)
    c.call = call
    guard = await AuthorizationGuard.begin(auth, 'token', ['engineering'])
    assert await c.pages('token', rows, request(), guard) == {'p0','p1'}
    async def malformed(*args, **kwargs):
        return {'decisions':[{'page_id':'different', 'revision_id':'revision-a','allowed':True}]}
    c.call = malformed
    with pytest.raises(GraphError):
        await c.pages('token', rows, request(), guard)


@pytest.mark.asyncio
async def test_machine_tokens_no_redirect_no_secret_error_or_bearer_persistence():
    from knowledge_platform.graphiti.clients import MachineTokens
    from knowledge_platform.graphiti.config import GraphitiSettings
    from knowledge_platform.graphiti.schemas import GraphError
    config = GraphitiSettings(graphiti_oidc_token_url='https://issuer/token',graphiti_oidc_client_id='graph',graphiti_oidc_client_secret='private-secret')
    async def handler(req):
        assert b'knowledge%3Aread' in req.content
        return httpx.Response(302,headers={'Location':'https://evil'},json={'access_token':'secret'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GraphError) as exc:
            await MachineTokens(config, client).token()
        assert 'secret' not in str(exc.value)