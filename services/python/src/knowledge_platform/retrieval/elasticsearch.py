"""Elasticsearch Basic APIs only: BM25 and filtered dense-vector kNN, fused in Python."""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from .schemas import Fragment, RetrievalError, unavailable


def hard_filter(request, *, subjects, domains, configuration_id):
    instant = (request.as_of or datetime.now(UTC)).isoformat()
    filters = [
        {"terms":{"space_id":request.space_ids}},
        {"terms":{"acl_domain":domains}},
        {"term":{"acl_schema":1}},
        {"range":{"acl_clause_count":{"gte":1}}},
        {"term":{"state":"valid"}},
        {"term":{"embedding_configuration_id":configuration_id}},
        {"bool":{"must_not":{"nested":{"path":"acl_clauses","query":{"bool":{"must_not":{"terms":{"acl_clauses.subjects":subjects}}}}}}}},
        {"bool":{"should":[{"bool":{"must_not":{"exists":{"field":"valid_from"}}}},{"range":{"valid_from":{"lte":instant}}}],"minimum_should_match":1}},
        {"bool":{"should":[{"bool":{"must_not":{"exists":{"field":"valid_until"}}}},{"range":{"valid_until":{"gt":instant}}}],"minimum_should_match":1}},
    ]
    if not request.include_historical:
        filters.append({"term":{"is_current":True}})
    if request.known_at:
        filters.append({"range":{"known_at":{"lte":request.known_at.isoformat()}}})
    return filters


def search_bodies(request, *, subjects, domains, configuration_id, query_vector):
    filters = hard_filter(request,subjects=subjects,domains=domains,configuration_id=configuration_id)
    common = {"size":50,"track_total_hits":False,"_source":{"excludes":["embedding"]},"timeout":"1500ms"}
    bm25 = common | {"query":{"bool":{"must":{"multi_match":{"query":request.query,"fields":["title^2","text","entity_ids"],"type":"best_fields"}},"filter":filters}}}
    vector = common | {"knn":{"field":"embedding","query_vector":query_vector,"k":50,"num_candidates":100,"filter":{"bool":{"filter":filters}}}}
    return bm25, vector


def document(fragment):
    value = fragment.model_dump(mode="json")
    value["acl_schema"] = 1
    value["acl_clauses"] = [{"subjects":clause} for clause in value.pop("read_clauses")]
    value["acl_clause_count"] = len(value["acl_clauses"])
    return value


def decode(value):
    value = dict(value)
    if value.pop("acl_schema") != 1:
        raise ValueError
    clauses = value.pop("acl_clauses")
    if value.pop("acl_clause_count") != len(clauses):
        raise ValueError
    value["read_clauses"] = [v["subjects"] for v in clauses]
    return Fragment.model_validate(value)


def index_mapping(dimensions, configuration_id):
    keywords = ("id", "page_id", "revision_id", "space_id", "kind", "entity_ids", "acl_domain", "state", "embedding_configuration_id")
    properties = {key:{"type":"keyword"} for key in keywords}
    properties.update({key:{"type":"long"} for key in ("version", "acl_epoch", "acl_version", "acl_schema", "acl_clause_count", "embedding_dimensions")})
    properties.update({key:{"type":"date"} for key in ("valid_from", "valid_until", "known_at")})
    properties.update(title={"type":"text"}, text={"type":"text"},is_current={"type":"boolean"},evidence={"type":"object","enabled":False},acl_clauses={"type":"nested","properties":{"subjects":{"type":"keyword"}}},embedding={"type":"dense_vector","dims":dimensions,"index":True,"similarity":"cosine","index_options":{"type":"hnsw"}})
    return {"settings":{"number_of_shards":1,"number_of_replicas":0},"mappings":{"dynamic":"strict","_meta":{"schema":"knowledge-fragments-v2","embedding_configuration_id":configuration_id,"dimensions":dimensions},"properties":properties}}


class ElasticIndex:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=20,follow_redirects=False)
        self.owned = client is None
        self.base = settings.retrieval_es_url.rstrip("/")
        self.index = settings.retrieval_es_index

    async def close(self):
        if self.owned:
            await self.client.aclose()

    async def request(self, method, path, **kwargs):
        headers = kwargs.pop("headers",{})
        if self.settings.retrieval_es_api_key:
            headers["Authorization"] = "ApiKey " + self.settings.retrieval_es_api_key
        try:
            response = await self.client.request(method,self.base+path,headers=headers,follow_redirects=False,**kwargs)
            if not 200 <= response.status_code < 300:
                raise unavailable("elasticsearch_unavailable", "Elasticsearch operation failed")
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise unavailable("elasticsearch_unavailable", "Elasticsearch response unavailable") from None

    async def initialize(self, dimensions, configuration_id):
        if not dimensions or not configuration_id:
            raise unavailable("model_not_configured", "Configure the retrieval embedding model and dimensions")
        try:
            response = await self.client.get(self.base+"/"+self.index+"/_mapping", headers={"Authorization":"ApiKey "+self.settings.retrieval_es_api_key} if self.settings.retrieval_es_api_key else {})
            if response.status_code == 404:
                try:
                    await self.request("PUT","/"+self.index,json=index_mapping(dimensions,configuration_id))
                except RetrievalError:
                    # Another replica may create the same index; its immutable model/schema still must match.
                    pass
                response = await self.client.get(self.base+"/"+self.index+"/_mapping",headers={"Authorization":"ApiKey "+self.settings.retrieval_es_api_key} if self.settings.retrieval_es_api_key else {})
            if response.status_code != 200:
                raise ValueError
            mapping=response.json()[self.index]["mappings"]
            if mapping.get("_meta") != {"schema":"knowledge-fragments-v2","embedding_configuration_id":configuration_id,"dimensions":dimensions} or mapping["properties"]["embedding"]["dims"] != dimensions:
                raise unavailable("index_configuration_conflict", "Embedding configuration changes require a separate rebuilt index")
        except (httpx.HTTPError,KeyError,TypeError,ValueError):
            raise unavailable("elasticsearch_unavailable", "Elasticsearch index metadata unavailable") from None

    async def put(self, fragments, *, generation):
        for offset in range(0,len(fragments),100):
            batch=fragments[offset:offset+100]
            lines=[]
            for fragment in batch:
                if fragment.embedding is None or len(fragment.embedding) != fragment.embedding_dimensions or any(not math.isfinite(v) for v in fragment.embedding) or not any(fragment.embedding):
                    raise unavailable("invalid_embedding", "Projection embedding is invalid")
                lines.append(json.dumps({"index":{"_index":self.index,"_id":fragment.id,"version":generation,"version_type":"external_gte"}},separators=(",",":")))
                lines.append(json.dumps(document(fragment),separators=(",",":"),ensure_ascii=False))
            data=await self.request("POST","/_bulk?refresh=wait_for",content="\n".join(lines)+"\n",headers={"Content-Type":"application/x-ndjson"})
            try:
                statuses=[item["index"]["status"] for item in data["items"]]
                if any(status == 409 for status in statuses):
                    raise unavailable("projection_superseded", "A newer projection has fenced this write")
                if len(statuses) != len(batch) or any(status not in (200,201) for status in statuses):
                    raise ValueError
            except (KeyError,TypeError,ValueError):
                raise unavailable("elasticsearch_unavailable", "Elasticsearch bulk projection failed") from None

    async def search(self, body):
        result=await self.request("POST","/"+self.index+"/_search?allow_partial_search_results=false",json=body)
        try:
            if result["timed_out"] or result["_shards"]["failed"] != 0:
                raise ValueError
            fragments=[]
            for hit in result["hits"]["hits"]:
                fragment=decode(hit["_source"])
                if hit["_id"] != fragment.id:
                    raise ValueError
                fragments.append(fragment)
            if len(fragments) > body.get("size",50):
                raise ValueError
            return fragments
        except (KeyError,TypeError,ValueError,ValidationError):
            raise unavailable("invalid_search_response", "Elasticsearch returned an incomplete search result") from None

    async def by_ids(self, ids, filters):
        if not ids:
            return []
        if len(ids) > 1000:
            raise unavailable("graph_budget_exceeded", "Graph evidence exceeds the retrieval budget")
        return await self.search({"size":len(ids),"track_total_hits":False,"_source":{"excludes":["embedding"]},"query":{"bool":{"filter":filters+[{"ids":{"values":ids}}]}}})