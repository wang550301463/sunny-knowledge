from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from test_authorization import Auth
from test_projection import projection


class Catalog:
    async def policies(self,spaces,limit):
        return [projection()]


class Index:
    def __init__(self,fragments):
        self.fragments=fragments
        self.searches=[]
    async def search(self,body):
        self.searches.append(body)
        return self.fragments
    async def by_ids(self,ids,filters):
        return [f for f in self.fragments if f.id in ids]


class Clients:
    def __init__(self,auth):
        self.auth=auth
        self.denied=set()
        self.calls=[]
        self.revoke_on_rerank=False
        self.graph_error=None
    async def pages(self,token,fragments,request):
        self.calls.append("authorize_pages")
        return {f.id for f in fragments if f.id not in self.denied}
    async def evidence(self,token,refs):
        self.calls.append("authorize_evidence")
        return [{"evidence":ref.model_dump(mode="json"),"allowed":True,"space_id":"engineering","excerpt":"exact source\r\nsecond","sha256":"d"*64} for ref in refs]
    async def embeddings(self,token,configuration_id,texts,dimensions):
        self.calls.append("embed")
        return [[1,0,0] for _ in texts]
    async def rerank(self,token,configuration_id,query,fragments):
        self.calls.append(("rerank",[f.id for f in fragments]))
        if self.revoke_on_rerank:
            self.denied.update(f.id for f in fragments)
            self.auth.epoch+=1
        return [(index,1/(index+1)) for index in range(len(fragments))]
    async def traverse(self,token,request,ids):
        if self.graph_error:
            raise self.graph_error
        from knowledge_platform.retrieval.schemas import GraphResult
        return GraphResult()


def setup():
    from knowledge_platform.retrieval.config import RetrievalSettings
    from knowledge_platform.retrieval.projection import compile_fragments
    from knowledge_platform.retrieval.service import RetrievalService
    auth=Auth()
    clients=Clients(auth)
    fragments=compile_fragments(projection(),configuration_id="embed-v1",dimensions=3)
    index=Index(fragments)
    settings=RetrievalSettings(retrieval_embedding_configuration_id="embed-v1",retrieval_embedding_dimensions=3,retrieval_rerank_configuration_id="rerank-v1")
    return RetrievalService(settings,auth,Catalog(),index,clients),auth,clients,index


@pytest.mark.asyncio
async def test_search_removes_revoked_candidates_before_rerank_and_returns_exact_evidence():
    from knowledge_platform.retrieval.schemas import SearchRequest
    service,auth,clients,index=setup()
    clients.denied.add(index.fragments[0].id)
    result=await service.search("token",SearchRequest(query="Pay",space_ids=["engineering"]))
    assert len(result["items"]) == 1
    assert result["items"][0]["kind"] == "fact"
    assert result["evidence"][0]["excerpt"] == "exact source\r\nsecond"
    rerank=next(c for c in clients.calls if isinstance(c,tuple))
    assert rerank[1] == [index.fragments[1].id]
    assert clients.calls.count("authorize_pages") >= 3
    assert "policy_fingerprint" in str(index.searches[0])
    assert "text" in result["items"][0]
    assert "embedding" not in result["items"][0]
    assert "read_clauses" not in result["items"][0]


@pytest.mark.asyncio
async def test_revocation_during_rerank_fails_entire_response():
    from knowledge_platform.retrieval.schemas import SearchRequest,RetrievalError
    service,auth,clients,index=setup()
    clients.revoke_on_rerank=True
    with pytest.raises(RetrievalError) as error:
        await service.search("token",SearchRequest(query="Pay",space_ids=["engineering"]))
    assert error.value.code == "authorization_changed"


@pytest.mark.asyncio
async def test_graph_unavailable_degrades_but_graph_authentication_failure_aborts():
    from knowledge_platform.retrieval.schemas import SearchRequest,Relations,unavailable
    service,auth,clients,index=setup()
    request=SearchRequest(query="Who depends on Pay?",space_ids=["engineering"],relation=Relations())
    clients.graph_error=unavailable("graph_unavailable","SECRET endpoint")
    result=await service.search("token",request)
    assert result["degraded"] == ["graph_unavailable"]
    assert "SECRET" not in str(result)
    clients.graph_error=HTTPException(403,"denied")
    with pytest.raises(HTTPException) as error:
        await service.search("token",request)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_model_configuration_never_synthesizes_a_fallback():
    from knowledge_platform.retrieval.schemas import SearchRequest,RetrievalError
    service,auth,clients,index=setup()
    service.settings.retrieval_rerank_configuration_id=""
    with pytest.raises(RetrievalError) as error:
        await service.search("token",SearchRequest(query="Pay",space_ids=["engineering"]))
    assert error.value.code == "model_not_configured"
    assert not index.searches
    assert not clients.calls


@pytest.mark.asyncio
async def test_embedding_override_cannot_mix_incompatible_index_model():
    from knowledge_platform.retrieval.schemas import SearchRequest,RetrievalError
    service,auth,clients,index=setup()
    with pytest.raises(RetrievalError) as error:
        await service.search("token",SearchRequest(query="Pay",space_ids=["engineering"],embedding_configuration_id="other-model"))
    assert error.value.status == 409
    assert not clients.calls