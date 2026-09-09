from copy import deepcopy

import pytest

from .fixtures import projection


def structured():
    value = projection()
    claim = value['content']['claims'][0]
    claim.update(id='git:pay:module', entity_type='Module', entity={'id': 'git:pay:module', 'name': 'Payment', 'type': 'Module'})
    relation = deepcopy(claim)
    relation.update(id='claim:dependency', text='Payment depends on postgres.', entity=None,
                    relation={'source_id': claim['id'], 'target_id': 'go:pq', 'type': 'depends_on'})
    value['content']['claims'].append(relation)
    return value


def test_formal_edges_require_structured_canonical_claims_and_exact_fragment_ids():
    from knowledge_platform.graphiti.projection import compile_graph, digest
    value = structured()
    graph = compile_graph(value)
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert (edge.source, edge.target, edge.type, edge.kind) == ('git:pay:module', 'go:pq', 'depends_on', 'fact')
    assert edge.fragment_ids == [digest(['page:pay', 'revision-a', 'claim', 'claim:dependency', 0])]
    assert edge.evidence == value['content']['claims'][0]['evidence']
    assert next(n for n in graph.nodes if n.id == 'go:pq').placeholder
    assert 'graph_projection_pending' in graph.degraded
    assert not compile_graph(projection()).edges  # Natural language is never a formal edge.


def test_partition_is_full_acl_domain_but_fingerprint_is_resource_specific():
    from knowledge_platform.graphiti.projection import compile_graph
    a, b = structured(), structured()
    b['page_id'] = 'page:other'
    b['policies'][0]['resource_id'] = b['page_id']
    ga, gb = compile_graph(a), compile_graph(b)
    assert ga.group_id == gb.group_id
    assert ga.policy_fingerprint != gb.policy_fingerprint


@pytest.mark.parametrize('field', ['policies', 'read_clauses', 'acl_domain'])
def test_incomplete_policy_provenance_rejected(field):
    from knowledge_platform.graphiti.projection import compile_graph
    from knowledge_platform.graphiti.schemas import GraphError
    value = structured()
    value[field] = [] if field != 'acl_domain' else 'forged'
    with pytest.raises(GraphError):
        compile_graph(value)


def test_inference_and_time_never_become_formal_or_current_facts():
    from knowledge_platform.graphiti.projection import compile_graph
    value = structured()
    value['content']['claims'][1].update(kind='inference', valid_until='2026-02-01T00:00:00Z')
    graph = compile_graph(value)
    assert graph.edges[0].kind == 'inference'
    assert graph.valid_until.isoformat() == '2026-02-01T00:00:00+00:00'
    value['content']['claims'][0]['state'] = 'stale'
    assert compile_graph(value).state == 'stale'


def test_no_evidence_no_graph_fact_and_source_identity_cannot_be_forged():
    from knowledge_platform.graphiti.projection import compile_graph
    from knowledge_platform.graphiti.schemas import GraphError
    value = structured()
    value['content']['claims'][1]['evidence'] = []
    with pytest.raises(GraphError):
        compile_graph(value)
    value = structured()
    value['content']['claims'][1]['evidence'][0]['path'] = 'different.go'
    with pytest.raises(GraphError):
        compile_graph(value)