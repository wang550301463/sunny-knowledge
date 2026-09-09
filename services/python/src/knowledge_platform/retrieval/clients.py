"""Versioned service clients; safe protocol errors never expose provider payloads."""
from __future__ import annotations

import asyncio
import math
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from .schemas import GraphResult, RetrievalError, unavailable


def safe_json(response):
    try:
        value=response.json()
        if not isinstance(value,dict):
            raise ValueError
        return value
    except ValueError:
        raise unavailable("invalid_dependency_response", "Internal service response is invalid") from None


class Clients:
    def __init__(self, settings, authorizer):
        self.settings,self.auth=settings,authorizer

    async def call(self,method,path,target,token=None,body=None):
        return safe_json(await self.auth.request(method,path,target,token,body))

    async def pages(self,token,fragments,request,*,inspection=False):
        keys=sorted({(f.page_id,f.revision_id) for f in fragments})
        permitted=set()
        for offset in range(0,len(keys),100):
            batch=keys[offset:offset+100]
            value=await self.call("POST","/internal/v1/pages/authorize","knowledge",token,{"pages":[{"page_id":p,"revision_id":r} for p,r in batch],"include_historical":request.include_historical,"as_of":request.as_of.isoformat() if request.as_of else None})
            try:
                decisions=value["decisions"]
                if len(decisions) != len(batch) or {(d["page_id"],d["revision_id"]) for d in decisions} != set(batch):
                    raise ValueError
                for decision in decisions:
                    field="authorized" if inspection else "allowed"
                    if decision.get(field) is True:
                        if decision["space_id"] not in request.space_ids:
                            continue
                        permitted.add((decision["page_id"],decision["revision_id"]))
                    elif decision.get("allowed") is not False:
                        raise ValueError
            except (KeyError,TypeError,ValueError):
                raise unavailable("invalid_authorization_response", "Canonical authorization response is invalid") from None
        return {f.id for f in fragments if (f.page_id,f.revision_id) in permitted}

    async def evidence(self,token,refs):
        values=[]
        for offset in range(0,len(refs),100):
            batch=refs[offset:offset+100]
            value=await self.call("POST","/internal/v1/evidence/authorize","knowledge",token,{"evidence":[r.model_dump(mode="json") for r in batch]})
            try:
                decisions=value["decisions"]
                if len(decisions) != len(batch):
                    raise ValueError
                for decision,ref in zip(decisions,batch,strict=True):
                    if decision["evidence"] != ref.model_dump(mode="json") or type(decision["allowed"]) is not bool:
                        raise ValueError
                    if decision["allowed"] and (not isinstance(decision["excerpt"],str) or not isinstance(decision["sha256"],str)):
                        raise ValueError
                values.extend(decisions)
            except (KeyError,TypeError,ValueError):
                raise unavailable("invalid_authorization_response", "Original evidence response is invalid") from None
        return values

    async def embeddings(self,token,configuration_id,texts,dimensions):
        # Batches cap total input at 64K chars under even conservative provider configurations.
        vectors=[]
        batch=[]
        total=0
        async def flush():
            if not batch:
                return
            value=await self.call("POST","/internal/v1/embeddings","llm",token,{"configuration_id":configuration_id,"input":batch})
            try:
                embeddings=value["embeddings"]
                if value["configuration_id"] != configuration_id or value["dimensions"] != dimensions or len(embeddings) != len(batch):
                    raise ValueError
                for vector in embeddings:
                    if not isinstance(vector,list) or len(vector) != dimensions or any(type(v) not in (float,int) or not math.isfinite(v) for v in vector) or not any(vector):
                        raise ValueError
                vectors.extend(embeddings)
            except (KeyError,TypeError,ValueError):
                raise unavailable("invalid_embedding", "Model embedding response is invalid") from None
        for item in texts:
            if batch and (len(batch) >= 16 or total+len(item)>64000):
                await flush()
                batch=[]
                total=0
            batch.append(item)
            total+=len(item)
        await flush()
        return vectors

    async def rerank(self,token,configuration_id,query,fragments):
        value=await self.call("POST","/internal/v1/rerank","llm",token,{"configuration_id":configuration_id,"query":query,"documents":[f.text for f in fragments],"top_n":len(fragments)})
        try:
            results=value["results"]
            if value["configuration_id"] != configuration_id or len(results) != len(fragments):
                raise ValueError
            if {r["index"] for r in results} != set(range(len(fragments))):
                raise ValueError
            if any(type(r["index"]) is not int or type(r["score"]) not in (int,float) or not math.isfinite(r["score"]) for r in results):
                raise ValueError
            return sorted([(r["index"],r["score"]) for r in results],key=lambda r:(-r[1],r[0]))
        except (KeyError,TypeError,ValueError):
            raise unavailable("invalid_rerank", "Model rerank response is invalid") from None

    async def traverse(self,token,request,ids):
        relation=request.relation
        body={"space_ids":request.space_ids,"seed_fragment_ids":ids,"relation_types":relation.types,"direction":relation.direction,"hops":relation.hops,"max_nodes":100,"max_edges":200,"as_of":request.as_of.isoformat() if request.as_of else None,"known_at":request.known_at.isoformat() if request.known_at else None,"include_historical":request.include_historical}
        try:
            async with asyncio.timeout(self.settings.retrieval_graph_timeout_seconds):
                value=await self.call("POST","/internal/v1/traverse","graphiti",token,body)
            graph=GraphResult.model_validate(value)
            nodes={n.id:n for n in graph.nodes}
            edges={e.id:e for e in graph.edges}
            if len(nodes)!=len(graph.nodes) or len(edges)!=len(graph.edges):
                raise ValueError
            for edge in graph.edges:
                if edge.source not in nodes or edge.target not in nodes or edge.type not in relation.types:
                    raise ValueError
            for path in graph.paths:
                if len(path.edge_ids)>relation.hops or len(path.node_ids)!=len(path.edge_ids)+1:
                    raise ValueError
                for i,edge_id in enumerate(path.edge_ids):
                    edge=edges[edge_id]
                    pair=(path.node_ids[i],path.node_ids[i+1])
                    permitted={(edge.source,edge.target)} if relation.direction=="outgoing" else {(edge.target,edge.source)} if relation.direction=="incoming" else {(edge.source,edge.target),(edge.target,edge.source)}
                    if pair not in permitted:
                        raise ValueError
            return graph
        except HTTPException as error:
            if error.status_code in {401,403}:
                raise
            raise unavailable("graph_unavailable", "Graph traversal is unavailable") from None
        except (TimeoutError,KeyError,TypeError,ValueError,ValidationError,RetrievalError):
            raise unavailable("graph_unavailable", "Graph traversal is unavailable") from None

    async def projection(self,page_id,revision_id):
        return await self.call("GET",f"/internal/v1/projections/pages/{quote(page_id,safe='')}/revisions/{quote(revision_id,safe='')}","knowledge")


class MachineTokens:
    def __init__(self,settings,client=None):
        self.settings=settings
        self.client=client or httpx.AsyncClient(timeout=20,follow_redirects=False)
        self.owned=client is None

    async def close(self):
        if self.owned:
            await self.client.aclose()

    async def token(self):
        s=self.settings
        if not all((s.retrieval_oidc_token_url,s.retrieval_oidc_client_id,s.retrieval_oidc_client_secret)):
            raise unavailable("worker_authorization_required", "Configure and grant the retrieval service account")
        try:
            response=await self.client.post(s.retrieval_oidc_token_url,data={"grant_type":"client_credentials","client_id":s.retrieval_oidc_client_id,"client_secret":s.retrieval_oidc_client_secret,"scope":"knowledge:read"},follow_redirects=False)
            if response.status_code != 200:
                raise unavailable("worker_authorization_required", "Retrieval service account authentication failed")
            value=response.json()
            token=value["access_token"]
            if not isinstance(token,str) or not token or value["token_type"].lower() != "bearer":
                raise ValueError
            return token
        except (httpx.HTTPError,KeyError,TypeError,ValueError):
            raise unavailable("worker_authorization_required", "Retrieval service account identity unavailable") from None