from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from .test_projection import structured


class Auth:
    epoch = 9
    subjects = ['group:engineering', 'user:alice']
    fail = False

    async def resolve(self, token):
        if self.fail:
            from fastapi import HTTPException
            raise HTTPException(503, 'unavailable')
        return SimpleNamespace(id='alice', subjects=self.subjects, auth_epoch=self.epoch)

    async def require(self, *args):
        return SimpleNamespace(auth_epoch=self.epoch)


class Policies:
    allowed_ids = None

    async def allowed(self, guard, records):
        return [r['policy_fingerprint'] for r in records if self.allowed_ids is None or r['projection_id'] in self.allowed_ids]


class Catalog:
    def __init__(self, records):
        self.records = records

    async def scope(self, spaces, limit):
        return self.records


class Clients:
    permitted = None
    revoked_after = None
    calls = 0

    async def pages(self, token, records, request, guard, **kwargs):
        self.calls += 1
        if self.revoked_after and self.calls >= self.revoked_after:
            return set()
        return {r['projection_id'] for r in records if self.permitted is None or r['projection_id'] in self.permitted}

    async def evidence(self, token, refs, guard):
        return {__import__('knowledge_platform.graphiti.projection', fromlist=['digest']).digest(r) for r in refs}


class Backend:
    def __init__(self, records):
        self.records = records
        self.queries = []

    async def seeds(self, projection_ids, fragment_ids, limit):
        self.queries.append(('seeds', projection_ids))
        return [{'projection_id': r['projection_id'], 'node': n.model_dump(mode='json')} for r in self.records if r['projection_id'] in projection_ids for n in r['graph'].nodes if set(n.fragment_ids) & set(fragment_ids)][:limit]

    async def adjacent(self, projection_ids, frontier, types, direction, limit):
        self.queries.append(('adjacent', projection_ids))
        found = []
        for r in self.records:
            if r['projection_id'] not in projection_ids:
                continue
            nodes = {n.id: n for n in r['graph'].nodes}
            for e in r['graph'].edges:
                if e.type in types and ((direction in ['outgoing', 'both'] and e.source in frontier) or (direction in ['incoming', 'both'] and e.target in frontier)):
                    found.append({'projection_id': r['projection_id'], 'edge': e.model_dump(mode='json'), 'source': nodes[e.source].model_dump(mode='json'), 'target': nodes[e.target].model_dump(mode='json')})
        return found[:limit]


def records():
    from knowledge_platform.graphiti.projection import compile_graph
    a = structured()
    b = structured()
    b.update(page_id='page:db', revision_id='revision-b', current_revision='revision-b')
    b['policies'][0]['resource_id'] = b['page_id']
    b['content']['claims'][0].update(id='go:pq', entity={'id': 'go:pq', 'name': 'Postgres', 'type': 'Module'})
    b['content']['claims'][1]['relation'] = {'source_id': 'go:pq', 'target_id': 'service:disk', 'type': 'uses'}
    return [dict(projection_id=f'p{i}', graph=compile_graph(v), policies=v['policies'], policy_fingerprint=compile_graph(v).policy_fingerprint) for i, v in enumerate([a, b])]


def runtime(rows=None):
    from knowledge_platform.graphiti.service import GraphService
    rows = rows or records()
    auth, clients, policy, backend = Auth(), Clients(), Policies(), Backend(rows)
    service = GraphService(SimpleNamespace(graphiti_max_policy_records=10000), auth, Catalog(rows), backend, clients, policy=policy)
    return service, auth, clients, policy, backend


def request(hops=2, **kw):
    from knowledge_platform.graphiti.schemas import TraverseRequest
    from knowledge_platform.graphiti.projection import digest
    return TraverseRequest(space_ids=['engineering'], seed_fragment_ids=[digest(['page:pay', 'revision-a', 'claim', 'git:pay:module', 0])],
        relation_types=['depends_on', 'uses'], hops=hops, **kw)


@pytest.mark.asyncio
async def test_real_bfs_contract_two_hops_and_direction_no_synthetic_relation():
    service, *_ = runtime()
    result = await service.traverse('bearer', request())
    assert {e.type for e in result.edges} == {'depends_on', 'uses'}
    assert any(p.node_ids == ['git:pay:module', 'go:pq', 'service:disk'] for p in result.paths)
    assert len((await service.traverse('bearer', request(hops=1))).edges) == 1
    assert not (await service.traverse('bearer', request(direction='incoming'))).edges


@pytest.mark.asyncio
async def test_acl_partition_blocked_before_adjacent_query_and_late_revoke_prunes_all():
    service, auth, clients, policy, backend = runtime()
    policy.allowed_ids = {'p0'}
    result = await service.traverse('bearer', request())
    assert len(result.edges) == 1
    assert all('p1' not in ids for _, ids in backend.queries)
    clients.calls = 0
    clients.revoked_after = 2
    result = await service.traverse('bearer', request())
    assert not result.nodes and not result.edges and not result.paths


@pytest.mark.asyncio
async def test_hard_budgets_do_not_emit_dangling_edges():
    service, *_ = runtime()
    result = await service.traverse('bearer', request(max_nodes=2, max_edges=1))
    assert len(result.nodes) <= 2 and len(result.edges) == 1
    assert all({e.source, e.target} <= {n.id for n in result.nodes} for e in result.edges)
    assert result.degraded


@pytest.mark.asyncio
async def test_time_and_current_filters_precede_database_query():
    rows = records()
    rows[1]['graph'].valid_from = datetime(2027, 1, 1, tzinfo=UTC)
    service, *_, backend = runtime(rows)
    result = await service.traverse('bearer', request(as_of=datetime(2026, 1, 2, tzinfo=UTC)))
    assert len(result.edges) == 1
    assert all('p1' not in ids for _, ids in backend.queries)


@pytest.mark.asyncio
async def test_auth_outage_fails_closed_graph_outage_degrades_explicitly():
    from fastapi import HTTPException
    from knowledge_platform.graphiti.schemas import unavailable
    service, auth, *_, backend = runtime()
    auth.fail = True
    with pytest.raises(HTTPException):
        await service.traverse('bearer', request())
    auth.fail = False
    async def fail(*args):
        raise unavailable()
    backend.adjacent = fail
    result = await service.traverse('bearer', request())
    assert result.degraded == ['graph_unavailable']
    assert not result.edges