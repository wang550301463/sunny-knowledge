"""Authorized hybrid retrieval, evidence assembly, and bounded graph cooperation."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from urllib.parse import quote

from .authorization import AuthorizationGuard, PolicyCache
from .elasticsearch import hard_filter, search_bodies
from .projection import digest, policy_fingerprint
from .ranking import fuse
from .schemas import GraphResult, RetrievalError, unavailable


class RetrievalService:
    def __init__(self,settings,authorizer,catalog,index,clients,policy_cache=None):
        self.settings,self.auth,self.catalog,self.index,self.clients=settings,authorizer,catalog,index,clients
        self.policy_cache=policy_cache or PolicyCache(settings.retrieval_policy_cache_seconds)

    def models(self,request):
        embedding=request.embedding_configuration_id or self.settings.retrieval_embedding_configuration_id
        rerank=request.rerank_configuration_id or self.settings.retrieval_rerank_configuration_id
        if not embedding or not rerank or not self.settings.retrieval_embedding_dimensions:
            raise unavailable("model_not_configured", "Configure retrieval embedding and rerank models")
        if embedding != self.settings.retrieval_embedding_configuration_id:
            raise RetrievalError(409,"index_configuration_conflict","The requested embedding configuration has no compatible index")
        return embedding,rerank

    async def scope(self,token,request):
        guard=await AuthorizationGuard.begin(self.auth,token,request.space_ids)
        records=await self.catalog.policies(request.space_ids,self.settings.retrieval_max_policy_domains)
        fingerprints=await self.policy_cache.allowed(guard,records)
        domains=sorted({r["acl_domain"] for r in records if policy_fingerprint(r["policies"]) in set(fingerprints)})
        return guard,domains,fingerprints

    async def live(self,guard,fragments,request):
        allowed=await self.clients.pages(guard.token,fragments,request)
        await guard.finish()
        return [f for f in fragments if f.id in allowed]

    async def graph(self,token,request,ids):
        try:
            return await self.clients.traverse(token,request,ids),[]
        except RetrievalError as error:
            if error.status == 503:
                return GraphResult(),["graph_unavailable"]
            raise

    async def search(self,token,request):
        embedding,rerank=self.models(request)
        request=request.model_copy(update={"as_of":request.as_of or datetime.now(UTC)})
        guard,domains,fingerprints=await self.scope(token,request)
        vector=(await self.clients.embeddings(token,embedding,[request.query],self.settings.retrieval_embedding_dimensions))[0]
        await guard.finish()
        bm25body,vectorbody=search_bodies(request,subjects=guard.principal.subjects,domains=domains,fingerprints=fingerprints,configuration_id=embedding,query_vector=vector)
        keyword,semantic=await asyncio.gather(self.index.search(bm25body),self.index.search(vectorbody))
        candidates={f.id:f for f in keyword+semantic}
        live=await self.live(guard,list(candidates.values()),request)
        permitted={f.id for f in live}
        ranks={"bm25":[f.id for f in keyword if f.id in permitted],"vector":[f.id for f in semantic if f.id in permitted]}
        seeds=fuse(ranks,limit=30)
        graph=GraphResult()
        degraded=[]
        if request.relation and seeds:
            graph,degraded=await self.graph(token,request,[f.id for f in seeds])
            graph_ids=list(dict.fromkeys([fid for n in graph.nodes for fid in n.fragment_ids]+[fid for e in graph.edges for fid in e.fragment_ids]+[fid for p in graph.paths for fid in p.fragment_ids]))
            if len(graph_ids)>1000:
                graph=GraphResult()
                degraded=["graph_budget_exceeded"]
            else:
                filters=hard_filter(request,subjects=guard.principal.subjects,domains=domains,fingerprints=fingerprints,configuration_id=embedding)
                additional=await self.index.by_ids(graph_ids,filters)
                additional=await self.live(guard,additional,request)
                candidates.update({f.id:f for f in additional})
                visible={f.id for f in additional}
                graph=self.prune_graph(graph,visible)
                ranks["graph"]=[fid for fid in graph_ids if fid in visible]
        fused=fuse(ranks,limit=60)
        ranked=[candidates[f.id] for f in fused]
        ranked=await self.live(guard,ranked,request)
        if ranked:
            reranked=await self.clients.rerank(token,rerank,request.query,ranked)
        else:
            reranked=[]
        # The model may have run for seconds; every candidate is rechecked before selecting output.
        ranked_live={f.id for f in await self.live(guard,ranked,request)}
        main=[ranked[index] for index,_ in reranked if ranked[index].id in ranked_live][:request.limit]
        scores={ranked[index].id:score for index,score in reranked}
        main_ids={f.id for f in main}
        relevant_paths=[p for p in graph.paths if main_ids.intersection(p.fragment_ids)]
        required={fid for p in relevant_paths for fid in p.fragment_ids}
        supplements=[candidates[fid] for fid in sorted(required-main_ids) if fid in candidates and fid in ranked_live]
        # Relationship evidence must stay in the already-authorized/reranked budget.
        selected=main+supplements
        graph=self.prune_graph(graph,{f.id for f in selected})
        return await self.assemble(guard,request,selected,main_ids,graph,degraded,scores,{r.id:r.score for r in fused})

    @staticmethod
    def prune_graph(graph,visible):
        nodes=[n for n in graph.nodes if set(n.fragment_ids)<=visible]
        node_ids={n.id for n in nodes}
        edges=[e for e in graph.edges if e.source in node_ids and e.target in node_ids and set(e.fragment_ids)<=visible]
        edge_ids={e.id for e in edges}
        paths=[p for p in graph.paths if set(p.fragment_ids)<=visible and set(p.node_ids)<=node_ids and set(p.edge_ids)<=edge_ids]
        return GraphResult(nodes=nodes,edges=edges,paths=paths,degraded=[])

    async def assemble(self,guard,request,selected,main_ids,graph,degraded,scores=None,rrf=None):
        selected=await self.live(guard,selected,request)
        refs={ref.model_dump_json():ref for f in selected for ref in f.evidence}
        decisions=await self.clients.evidence(guard.token,list(refs.values()))
        supported={digest(d["evidence"]):d for d in decisions if d["allowed"]}
        selected=[f for f in selected if all(digest(ref.model_dump(mode="json")) in supported for ref in f.evidence)]
        selected=await self.live(guard,selected,request)
        ids={f.id for f in selected}
        graph=self.prune_graph(graph,ids)
        used={digest(ref.model_dump(mode="json")) for f in selected for ref in f.evidence}
        items=[]
        for fragment in selected:
            value=fragment.model_dump(mode="json",exclude={"embedding","read_clauses","policy_fingerprint","acl_domain","acl_epoch","acl_version","embedding_configuration_id","embedding_dimensions"})
            value.update(primary=fragment.id in main_ids,rrf_score=(rrf or {}).get(fragment.id),rerank_score=(scores or {}).get(fragment.id),citation_ids=[digest(ref.model_dump(mode="json")) for ref in fragment.evidence],url="/knowledge/pages/"+quote(fragment.page_id,safe="")+"?revision="+quote(fragment.revision_id,safe=""))
            items.append(value)
        evidence=[{"id":key,**{k:v for k,v in supported[key].items() if k!="allowed"}} for key in sorted(used)]
        await guard.finish()
        gaps=[]
        if not items:
            gaps.append("no_authorized_evidence")
        if any(not f.evidence for f in selected):
            gaps.append("original_evidence_missing")
        if request.include_historical:
            gaps.append("history_contains_projected_revisions_only")
        return {"items":items,"evidence":evidence,"graph":graph.model_dump(mode="json"),"degraded":degraded,"gaps":gaps,"auth_epoch":guard.principal.auth_epoch,"as_of":request.as_of.isoformat() if request.as_of else None,"known_at":request.known_at.isoformat() if request.known_at else None,"time_basis":"source_revision_and_knowledge_time","deployment_state":"unknown_without_deployment_evidence"}

    async def traverse(self,token,request):
        configuration=self.settings.retrieval_embedding_configuration_id
        if not configuration:
            raise unavailable("model_not_configured", "Configure the retrieval embedding index")
        request=request.model_copy(update={"as_of":request.as_of or datetime.now(UTC)})
        guard,domains,fingerprints=await self.scope(token,request)
        filters=hard_filter(request,subjects=guard.principal.subjects,domains=domains,fingerprints=fingerprints,configuration_id=configuration)
        seeds=await self.live(guard,await self.index.by_ids(request.seed_fragment_ids,filters),request)
        graph,degraded=await self.graph(token,request,[f.id for f in seeds]) if seeds else (GraphResult(),[])
        ids=list(dict.fromkeys([f.id for f in seeds]+[fid for n in graph.nodes for fid in n.fragment_ids]+[fid for e in graph.edges for fid in e.fragment_ids]+[fid for p in graph.paths for fid in p.fragment_ids]))
        if len(ids)>1000:
            raise unavailable("graph_budget_exceeded","Graph evidence exceeds the retrieval budget")
        fragments=await self.live(guard,await self.index.by_ids(ids,filters),request)
        # Traversal returns relationship evidence without sending content to a language model.
        return await self.assemble(guard,request,fragments,{f.id for f in seeds},graph,degraded)