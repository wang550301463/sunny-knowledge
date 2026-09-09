"""Graphiti 0.30.1 CRUD adaptation for deterministic graphs without model vectors.

The pinned SDK always calls Neo4j's vector-property procedures even for None,
which Neo4j rejects. Its official query builders expose has_aoss=True to omit
these procedure calls. Use only that path when the optional embedding is absent;
never fabricate a numeric vector. All node/edge models and query builders remain
Graphiti's, and tx is the same real SDK Neo4j transaction as the episode/mentions.
"""
from graphiti_core.driver.driver import GraphProvider
from graphiti_core.driver.neo4j.operations.entity_edge_ops import Neo4jEntityEdgeOperations
from graphiti_core.driver.neo4j.operations.entity_node_ops import Neo4jEntityNodeOperations
from graphiti_core.models.edges.edge_db_queries import get_entity_edge_save_query
from graphiti_core.models.nodes.node_db_queries import get_entity_node_save_query


class OptionalVectorNodes(Neo4jEntityNodeOperations):
    async def save(self, executor, node, tx=None):
        if node.name_embedding is not None:
            return await super().save(executor, node, tx=tx)
        data = {'uuid':node.uuid, 'name':node.name, 'name_embedding':None, 'group_id':node.group_id,
            'summary':node.summary, 'created_at':node.created_at, **(node.attributes or {})}
        query = get_entity_node_save_query(GraphProvider.NEO4J, ':'.join(sorted(set(node.labels + ['Entity']))), has_aoss=True)
        if tx is not None:
            await tx.run(query, entity_data=data)
        else:
            await executor.execute_query(query, entity_data=data)


class OptionalVectorEdges(Neo4jEntityEdgeOperations):
    async def save(self, executor, edge, tx=None):
        if edge.fact_embedding is not None:
            return await super().save(executor, edge, tx=tx)
        data = {'uuid':edge.uuid, 'source_uuid':edge.source_node_uuid, 'target_uuid':edge.target_node_uuid,
            'name':edge.name, 'fact':edge.fact, 'fact_embedding':None, 'group_id':edge.group_id,
            'episodes':edge.episodes, 'created_at':edge.created_at, 'expired_at':edge.expired_at,
            'valid_at':edge.valid_at, 'invalid_at':edge.invalid_at, **(edge.attributes or {})}
        query = get_entity_edge_save_query(GraphProvider.NEO4J, has_aoss=True)
        if tx is not None:
            await tx.run(query, edge_data=data)
        else:
            await executor.execute_query(query, edge_data=data)