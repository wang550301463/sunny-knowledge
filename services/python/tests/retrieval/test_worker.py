from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from test_authorization import Auth
from test_projection import projection
from test_service import Clients


class Tokens:
    async def token(self):return "machine-token"


@pytest.mark.asyncio
async def test_worker_authorizes_before_each_embedding_and_does_not_borrow_user_token():
    from knowledge_platform.retrieval.config import RetrievalSettings
    from knowledge_platform.retrieval.worker import ProjectionWorker
    auth=Auth()
    clients=Clients(auth)
    worker=ProjectionWorker(RetrievalSettings(retrieval_embedding_configuration_id="model-v1",retrieval_embedding_dimensions=3),auth,None,None,clients,Tokens())
    fragments=await worker.build(projection(),[])
    assert len(fragments)==2
    assert clients.calls[:2] == ["authorize_pages","embed"]
    assert clients.calls[-1]=="authorize_pages"
    assert all(f.embedding==[1,0,0] for f in fragments)


@pytest.mark.asyncio
async def test_acl_reconciliation_reuses_vectors_without_new_provider_content():
    from knowledge_platform.retrieval.config import RetrievalSettings
    from knowledge_platform.retrieval.worker import ProjectionWorker
    from knowledge_platform.retrieval.projection import compile_fragments
    auth=Auth()
    clients=Clients(auth)
    existing=compile_fragments(projection(),configuration_id="model-v1",dimensions=3)
    for f in existing:f.embedding=[1,0,0]
    worker=ProjectionWorker(RetrievalSettings(retrieval_embedding_configuration_id="model-v1",retrieval_embedding_dimensions=3),auth,None,None,clients,Tokens())
    result=await worker.build(projection(),existing)
    assert all(f.embedding==[1,0,0] for f in result)
    assert not clients.calls


@pytest.mark.asyncio
async def test_worker_revocation_refuses_to_send_page_text_to_provider():
    from knowledge_platform.retrieval.config import RetrievalSettings
    from knowledge_platform.retrieval.worker import ProjectionWorker
    from knowledge_platform.retrieval.schemas import RetrievalError
    auth=Auth()
    clients=Clients(auth)
    async def denied(token,fragments,request):return set()
    clients.pages=denied
    worker=ProjectionWorker(RetrievalSettings(retrieval_embedding_configuration_id="model-v1",retrieval_embedding_dimensions=3),auth,None,None,clients,Tokens())
    with pytest.raises(RetrievalError) as error:
        await worker.build(projection(),[])
    assert error.value.code=="worker_authorization_required"
    assert "embed" not in clients.calls


@pytest.mark.asyncio
async def test_invalidated_page_is_projected_as_unsearchable_without_embedding():
    from knowledge_platform.retrieval.config import RetrievalSettings
    from knowledge_platform.retrieval.worker import ProjectionWorker
    auth=Auth()
    clients=Clients(auth)
    value=projection()
    value["content"]["state"]="stale"
    worker=ProjectionWorker(RetrievalSettings(retrieval_embedding_configuration_id="model-v1",retrieval_embedding_dimensions=3),auth,None,None,clients,Tokens())
    result=await worker.build(value,[])
    assert all(f.state=="stale" and f.embedding is None for f in result)
    assert not clients.calls