from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from .test_authorization import Auth
from .test_projection import structured


class Tokens:
    async def token(self):
        return 'separate-graph-machine'


class Clients:
    denied = False
    calls = 0
    async def projection(self, page, revision):
        return structured()
    async def pages(self, token, records, request, guard, *, inspection=False):
        assert token == 'separate-graph-machine' and inspection is True
        self.calls += 1
        return set() if self.denied else {r['projection_id'] for r in records}


class Backend:
    writes = 0
    async def write(self, graph, generation):
        self.writes += 1
        return 'committed-projection'
    async def exists(self, projection_id, graph):
        return False


class Catalog:
    commits = 0
    async def project(self, graph, write, **kwargs):
        await write(graph, 1)
        self.commits += 1
        return 'projected'


def worker():
    from knowledge_platform.graphiti.config import GraphitiSettings
    from knowledge_platform.graphiti.worker import ProjectionWorker
    auth, catalog, backend, clients = Auth(), Catalog(), Backend(), Clients()
    return ProjectionWorker(GraphitiSettings(), auth, catalog, backend, clients, Tokens())


@pytest.mark.asyncio
async def test_worker_real_machine_authorization_before_and_after_graph_commit():
    w=worker()
    assert await w.project('page:pay','revision-a') == 'projected'
    assert w.clients.calls == 2 and w.backend.writes == 1 and w.catalog.commits == 1


@pytest.mark.asyncio
async def test_machine_revocation_before_write_refuses_and_late_revocation_leaves_orphan_unreachable():
    from knowledge_platform.graphiti.schemas import GraphError
    w=worker()
    w.clients.denied=True
    with pytest.raises(GraphError):
        await w.project('page:pay','revision-a')
    assert w.backend.writes == 0 and w.catalog.commits == 0
    w=worker()
    async def late(graph,generation):
        w.clients.denied=True
        return 'orphan'
    w.backend.write=late
    with pytest.raises(GraphError):
        await w.project('page:pay','revision-a')
    assert w.catalog.commits == 0


@pytest.mark.asyncio
async def test_only_own_consumer_leased_and_ack_after_committed_catalog():
    w=worker()
    calls=[]
    async def call(method,path,target,token=None,body=None):
        calls.append((path, body))
        if path.endswith('/lease'):
            assert body == {'consumer':'graphiti','limit':1,'lease_seconds':300}
            return {'items':[{'id':'event','page_id':'page:pay','revision_id':'revision-a','lease_token':'fenced-lease'}]}
        assert w.catalog.commits == 1
        assert body == {'lease_token':'fenced-lease'}
        return {}
    w.clients.call=call
    assert await w.once()
    assert len(calls)==2


@pytest.mark.asyncio
async def test_rebuild_checkpoint_advances_only_after_whole_batch_and_resume_cursor():
    w=worker()
    checkpoint=[]
    @asynccontextmanager
    async def lock(key):
        yield
    async def state(key):
        return {'cursor':'old-cursor','processed':100}
    async def save(*args):
        checkpoint.append(args)
    w.catalog.rebuild_lock=lock
    w.catalog.rebuild_state=state
    w.catalog.rebuild_checkpoint=save
    async def call(method,path,target,**kwargs):
        assert path == '/internal/v1/projections/revisions?limit=100&cursor=old-cursor'
        return {'items':[{'page_id':'page:pay','revision_id':'revision-a','version':1}], 'next_cursor':None}
    w.clients.call=call
    await w.rebuild()
    assert checkpoint[0][1:] == (None,1,True)