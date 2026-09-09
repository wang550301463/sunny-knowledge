from types import SimpleNamespace

import httpx
import pytest


class Auth:
    def __init__(self,result):self.result=result
    async def request(self,*args,**kwargs):return SimpleNamespace(json=lambda:self.result)


@pytest.mark.asyncio
@pytest.mark.parametrize("vectors",[[[True,0,0]],[[float('nan'),0,0]],[[0,0,0]],[[1,0]],[[1,0,0],[1,0,0]]])
async def test_embedding_protocol_rejects_nonfinite_wrong_dimensions_count_or_zero(vectors):
    from knowledge_platform.retrieval.clients import Clients
    from knowledge_platform.retrieval.config import RetrievalSettings
    from knowledge_platform.retrieval.schemas import RetrievalError
    clients=Clients(RetrievalSettings(),Auth({"configuration_id":"model-v1","dimensions":3,"embeddings":vectors}))
    with pytest.raises(RetrievalError):
        await clients.embeddings("token","model-v1",["private"],3)


@pytest.mark.asyncio
async def test_graph_rejects_nodes_beyond_advertised_hop_budget():
    from knowledge_platform.retrieval.clients import Clients
    from knowledge_platform.retrieval.config import RetrievalSettings
    from knowledge_platform.retrieval.schemas import SearchRequest,Relations,RetrievalError
    result={"nodes":[{"id":"a","fragment_ids":["seed"]},{"id":"b","fragment_ids":["middle"]},{"id":"c","fragment_ids":["too-far"]}],"edges":[{"id":"ab","source":"a","target":"b","type":"uses","kind":"fact","fragment_ids":["middle"]},{"id":"bc","source":"b","target":"c","type":"uses","kind":"fact","fragment_ids":["too-far"]}],"paths":[],"degraded":[]}
    clients=Clients(RetrievalSettings(),Auth(result))
    with pytest.raises(RetrievalError):
        await clients.traverse("token",SearchRequest(query="Pay",space_ids=["engineering"],relation=Relations(hops=1)),["seed"])


@pytest.mark.asyncio
async def test_machine_identity_rejects_redirect_and_never_echoes_response():
    from knowledge_platform.retrieval.clients import MachineTokens
    from knowledge_platform.retrieval.config import RetrievalSettings
    from knowledge_platform.retrieval.schemas import RetrievalError
    requests=[]
    def respond(request):
        requests.append(request)
        return httpx.Response(302,headers={"Location":"https://untrusted.test"},text="SECRET")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        tokens=MachineTokens(RetrievalSettings(retrieval_oidc_token_url="https://identity.test/token",retrieval_oidc_client_id="retrieval-worker",retrieval_oidc_client_secret="private"),http)
        with pytest.raises(RetrievalError) as error:await tokens.token()
        assert "SECRET" not in str(error.value)
        assert len(requests)==1