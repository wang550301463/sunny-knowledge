from datetime import UTC, datetime

import httpx
import pytest

from test_projection import projection


def test_both_recall_paths_use_same_permission_and_temporal_prefilter():
    from knowledge_platform.retrieval.elasticsearch import search_bodies
    from knowledge_platform.retrieval.schemas import SearchRequest
    request = SearchRequest(query="Pay calls",space_ids=["engineering"],as_of="2026-01-05T00:00:00Z")
    bm25, vector = search_bodies(request,subjects=["user:alice","group:engineering"],domains=["authorized-domain"],configuration_id="model-v1",query_vector=[1,0,0])
    hard = bm25["query"]["bool"]["filter"]
    assert vector["knn"]["filter"] == {"bool":{"filter":hard}}
    assert bm25["size"] == vector["size"] == vector["knn"]["k"] == 50
    assert "post_filter" not in bm25 and "post_filter" not in vector
    assert {"terms":{"acl_domain":["authorized-domain"]}} in hard
    assert {"term":{"state":"valid"}} in hard
    assert {"term":{"is_current":True}} in hard
    clause = next(f for f in hard if "bool" in f and "must_not" in f["bool"])
    assert clause["bool"]["must_not"]["nested"]["query"]["bool"]["must_not"]["terms"] == {"acl_clauses.subjects":["user:alice","group:engineering"]}
    assert "2026-01-05" in str(hard)
    assert "query_string" not in str(bm25)


@pytest.mark.asyncio
async def test_external_version_conflict_is_not_false_success_and_errors_do_not_echo_payloads():
    from knowledge_platform.retrieval.elasticsearch import ElasticIndex
    from knowledge_platform.retrieval.projection import compile_fragments
    from knowledge_platform.retrieval.schemas import RetrievalError
    from knowledge_platform.retrieval.config import RetrievalSettings
    requests=[]
    def handle(request):
        requests.append(request)
        return httpx.Response(200,json={"errors":True,"items":[{"index":{"status":409,"error":{"reason":"SECRET"}}}]})
    client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    index=ElasticIndex(RetrievalSettings(),client)
    fragment=compile_fragments(projection(),configuration_id="model-v1",dimensions=3)[0]
    fragment.embedding=[1,0,0]
    with pytest.raises(RetrievalError) as error:
        await index.put([fragment],generation=9)
    assert error.value.code == "projection_superseded"
    assert "SECRET" not in str(error.value)
    assert b'"version_type":"external_gte"' in requests[0].content
    assert b'"version":9' in requests[0].content
    await client.aclose()


@pytest.mark.asyncio
async def test_partial_es_search_or_malformed_hit_fails_closed():
    from knowledge_platform.retrieval.elasticsearch import ElasticIndex
    from knowledge_platform.retrieval.config import RetrievalSettings
    from knowledge_platform.retrieval.schemas import RetrievalError
    for payload in ({"timed_out":True,"hits":{"hits":[]}}, {"timed_out":False,"_shards":{"failed":1},"hits":{"hits":[]}}, {"timed_out":False,"_shards":{"failed":0},"hits":{"hits":[{"_id":"leak","_source":{"text":"SECRET"}}]}}):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=payload))) as client:
            with pytest.raises(RetrievalError) as error:
                await ElasticIndex(RetrievalSettings(),client).search({"size":50})
            assert "SECRET" not in str(error.value)