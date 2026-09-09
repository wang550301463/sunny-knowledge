import os
from uuid import uuid4

import pytest
import pytest_asyncio

from .test_projection import structured


@pytest_asyncio.fixture
async def graph_backend():
    from knowledge_platform.graphiti.backend import Neo4jGraph
    from knowledge_platform.graphiti.config import GraphitiSettings
    uri = os.getenv('TEST_GRAPHITI_NEO4J_URI')
    if not uri:
        pytest.skip('Real Neo4j not configured')
    settings = GraphitiSettings(graphiti_neo4j_uri=uri, graphiti_neo4j_password=os.environ['TEST_GRAPHITI_NEO4J_PASSWORD'], graphiti_namespace='test-' + uuid4().hex)
    backend = Neo4jGraph(settings)
    await backend.initialize()
    try:
        yield backend
    finally:
        async with backend.driver.transaction() as tx:
            await tx.run('MATCH (n) WHERE n.knowledge_namespace = $namespace DETACH DELETE n', namespace=settings.graphiti_namespace)
        await backend.close()


@pytest.mark.asyncio
async def test_real_sdk_episode_entity_edge_and_adjacency_without_model(graph_backend):
    from knowledge_platform.graphiti.projection import compile_graph
    graph = compile_graph(structured())
    pid = graph_backend.projection_id(graph.revision_id, 1)
    await graph_backend.write(graph, 1)
    seed = next(n for n in graph.nodes if n.id == 'git:pay:module')
    rows = await graph_backend.seeds([pid], seed.fragment_ids, 100)
    assert any(r['node']['id'] == seed.id for r in rows)
    outgoing = await graph_backend.adjacent([pid], [seed.id], ['depends_on'], 'outgoing', 20)
    assert len(outgoing) == 1
    assert outgoing[0]['edge']['target'] == 'go:pq'
    assert not await graph_backend.adjacent([pid], [seed.id], ['depends_on'], 'incoming', 20)
    assert not await graph_backend.adjacent([pid], [seed.id], ['uses'], 'both', 20)
    from graphiti_core.nodes import EpisodicNode
    episode = await EpisodicNode.get_by_uuid(graph_backend.driver, pid)
    assert episode.group_id == graph.group_id
    assert episode.entity_edges
    assert graph.revision_id in episode.content


@pytest.mark.asyncio
async def test_old_late_generation_cannot_poison_selected_new_graph(graph_backend):
    from knowledge_platform.graphiti.projection import compile_graph
    old = compile_graph(structured())
    value = structured()
    value['content']['claims'][1]['relation']['target_id'] = 'new:target'
    newer = compile_graph(value)
    await graph_backend.write(newer, 2)
    await graph_backend.write(old, 1)  # Simulate older transaction committing after timeout.
    pid = graph_backend.projection_id(newer.revision_id, 2)
    rows = await graph_backend.adjacent([pid], ['git:pay:module'], ['depends_on'], 'outgoing', 20)
    assert {r['edge']['target'] for r in rows} == {'new:target'}
    assert not await graph_backend.adjacent(['uncommitted-projection'], ['git:pay:module'], ['depends_on'], 'outgoing', 20)


@pytest.mark.asyncio
async def test_sdk_projection_transaction_rolls_back_whole_episode_on_failure(graph_backend, monkeypatch):
    from knowledge_platform.graphiti.projection import compile_graph
    from knowledge_platform.graphiti.schemas import GraphError
    graph = compile_graph(structured())
    async def broken(*args, **kwargs):
        raise RuntimeError('unsafe secret provider text')
    monkeypatch.setattr(graph_backend.driver.entity_edge_ops, 'save', broken)
    with pytest.raises(GraphError) as exc:
        await graph_backend.write(graph, 1)
    assert 'secret' not in str(exc.value)
    assert not await graph_backend.seeds([graph_backend.projection_id(graph.revision_id, 1)], graph.nodes[0].fragment_ids, 100)