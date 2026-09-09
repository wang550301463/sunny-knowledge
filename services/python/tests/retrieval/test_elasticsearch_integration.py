"""Real Elasticsearch smoke. Explicit isolated index; never touches the application index."""
import os
from uuid import uuid4

import pytest
import pytest_asyncio

from .test_projection import projection


@pytest_asyncio.fixture
async def real_index():
    endpoint=os.environ.get("RETRIEVAL_TEST_ES_URL")
    if not endpoint:
        pytest.skip("RETRIEVAL_TEST_ES_URL required for real Elasticsearch")
    from knowledge_platform.retrieval.config import RetrievalSettings
    from knowledge_platform.retrieval.elasticsearch import ElasticIndex
    index=ElasticIndex(RetrievalSettings(retrieval_es_url=endpoint,retrieval_es_index="knowledge-retrieval-test-"+uuid4().hex,retrieval_es_api_key=os.environ.get("RETRIEVAL_TEST_ES_API_KEY","")))
    await index.initialize(3,"embedding-test-v1")
    try:
        yield index
    finally:
        await index.request("DELETE","/"+index.index)
        await index.close()


def value_for(name,resource_subjects=None):
    from knowledge_platform.retrieval.projection import clauses_for,digest
    from knowledge_platform.retrieval.schemas import Policy
    value=projection(page_id="page:"+name,revision_id="revision:"+name,current_revision="revision:"+name)
    value["policies"][0].update(resource_id="page:"+name,resource_read_subjects=resource_subjects)
    value["read_clauses"]=clauses_for([Policy.model_validate(p) for p in value["policies"]])
    value["acl_domain"]=digest({"space_id":value["space_id"],"read_clauses":value["read_clauses"]})
    return value


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_bm25_and_knn_conjunctive_acl_status_time_and_fingerprints(real_index):
    from knowledge_platform.retrieval.elasticsearch import search_bodies
    from knowledge_platform.retrieval.projection import compile_fragments,policy_fingerprint
    from knowledge_platform.retrieval.schemas import SearchRequest
    values=[value_for("allowed",["user:alice"]),value_for("tightened",["user:bob"]),value_for("inherited"),value_for("stale"),value_for("future"),value_for("expired")]
    values[3]["content"]["state"]="stale"
    values[4]["content"]["valid_from"]="2027-01-01T00:00:00Z"
    values[5]["content"]["valid_until"]="2025-01-01T00:00:00Z"
    documents=[]
    for value in values:
        fragments=compile_fragments(value,configuration_id="embedding-test-v1",dimensions=3)
        for fragment in fragments:fragment.embedding=[1,0,0]
        documents.extend(fragments)
    await real_index.put(documents,generation=1)
    request=SearchRequest(query="Payment",space_ids=["engineering"],as_of="2026-01-05T00:00:00Z")
    bodies=search_bodies(request,subjects=["user:alice","group:engineering"],domains=sorted({v["acl_domain"] for v in values}),fingerprints=[policy_fingerprint(v["policies"]) for v in values],configuration_id="embedding-test-v1",query_vector=[1,0,0])
    for body in bodies:
        result=await real_index.search(body)
        assert {f.page_id for f in result}=={"page:allowed","page:inherited"}
    # A space match is insufficient without the second, tightening clause.
    denied_bodies=search_bodies(request,subjects=["group:engineering"],domains=sorted({v["acl_domain"] for v in values}),fingerprints=[policy_fingerprint(v["policies"]) for v in values],configuration_id="embedding-test-v1",query_vector=[1,0,0])
    for body in denied_bodies:
        assert {f.page_id for f in await real_index.search(body)}=={"page:inherited"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_real_external_generation_rejects_late_write_and_model_mismatch(real_index):
    from knowledge_platform.retrieval.projection import compile_fragments
    from knowledge_platform.retrieval.schemas import RetrievalError
    fragments=compile_fragments(value_for("fenced"),configuration_id="embedding-test-v1",dimensions=3)
    for fragment in fragments:fragment.embedding=[1,0,0]
    await real_index.put(fragments,generation=20)
    with pytest.raises(RetrievalError) as error:
        await real_index.put(fragments,generation=10)
    assert error.value.code=="projection_superseded"
    with pytest.raises(RetrievalError) as error:
        await real_index.initialize(3,"other-model")
    assert error.value.code=="index_configuration_conflict"