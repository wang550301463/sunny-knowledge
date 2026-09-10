"""Graphiti 0.30.1 SDK writes; immutable physical projections and real adjacency reads."""
import asyncio
import json
import logging

from graphiti_core.driver.neo4j_driver import Neo4jDriver
from graphiti_core.edges import EntityEdge, EpisodicEdge
from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode

from .projection import digest
from .schemas import unavailable

# SDK diagnostics can contain raw Cypher parameters, source text and driver exception data.
# This service uses common bounded telemetry and its own safe error codes instead.
for _name in ['graphiti_core', 'neo4j', *list(logging.root.manager.loggerDict)]:
    if _name == 'graphiti_core' or _name.startswith('graphiti_core.') or _name == 'neo4j' or _name.startswith('neo4j.'):
        logging.getLogger(_name).setLevel(logging.CRITICAL)
        logging.getLogger(_name).disabled = True


class Neo4jGraph:
    def __init__(self, settings, *, driver=None):
        self.settings = settings
        self.driver = driver or Neo4jDriver(settings.graphiti_neo4j_uri, settings.graphiti_neo4j_user,
            settings.graphiti_neo4j_password, database=settings.graphiti_neo4j_database)

    def projection_id(self, revision_id, generation):
        return digest([self.settings.graphiti_namespace, revision_id, generation])

    async def initialize(self):
        async def ddl(statement):
            try:
                await self.driver.execute_query(statement)
            except Exception as exc:
                # Neo4j 5 refuses CREATE CONSTRAINT when an equivalent index
                # already exists (graphiti-core seeds some of them); that
                # satisfies the intent, so tolerate schema-already-there.
                if "AlreadyExists" not in type(exc).__name__ and "already exists" not in str(exc):
                    raise
        try:
            async with asyncio.timeout(60):
                init_task = getattr(self.driver, "_init_task", None)
                if init_task:
                    await init_task
                await ddl('CREATE INDEX knowledge_graph_entity_projection IF NOT EXISTS FOR (n:Entity) ON (n.knowledge_projection)')
                await ddl('CREATE INDEX knowledge_graph_entity_identity IF NOT EXISTS FOR (n:Entity) ON (n.knowledge_namespace, n.knowledge_entity_id)')
                await ddl('CREATE INDEX knowledge_graph_edge_projection IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON (r.knowledge_projection)')
                await ddl('CREATE CONSTRAINT knowledge_graph_entity_uuid IF NOT EXISTS FOR (n:Entity) REQUIRE n.uuid IS UNIQUE')
                await ddl('CREATE CONSTRAINT knowledge_graph_episode_uuid IF NOT EXISTS FOR (n:Episodic) REQUIRE n.uuid IS UNIQUE')
        except Exception as exc:
            logging.getLogger(__name__).exception("neo4j initialize failed")
            raise unavailable() from exc

    async def ready(self):
        await self._query('RETURN 1 AS ready')

    async def close(self):
        await self.driver.close()

    async def _query(self, query, **kwargs):
        try:
            async with asyncio.timeout(self.settings.graphiti_query_timeout_seconds):
                rows, _, _ = await self.driver.execute_query(query, routing_='r', **kwargs)
                return [dict(r) for r in rows]
        except Exception:
            raise unavailable() from None

    async def exists(self, projection_id, graph):
        """Check whether a projection episode already exists in Neo4j.

        Used by the projection worker to skip re-projecting unchanged
        revisions (catalog calls this as `await exists(projection_id, graph)`).
        """
        try:
            rows = await self._query(
                "MATCH (e:Episodic {uuid: $projection_id}) RETURN e.uuid LIMIT 1",
                projection_id=projection_id,
            )
            return bool(rows)
        except Exception:
            return False

    async def write(self, graph, generation):
        projection_id = self.projection_id(graph.revision_id, generation)
        node_ids = {n.id: digest([projection_id, 'node', n.id]) for n in graph.nodes}
        edge_ids = {e.id: digest([projection_id, 'edge', e.id]) for e in graph.edges}
        metadata = {'knowledge_namespace': self.settings.graphiti_namespace, 'knowledge_projection': projection_id,
            'knowledge_page_id': graph.page_id, 'knowledge_revision_id': graph.revision_id,
            'knowledge_acl_fingerprint': graph.policy_fingerprint, 'knowledge_state': graph.state,
            'knowledge_valid_from': graph.valid_from, 'knowledge_valid_until': graph.valid_until,
            'knowledge_known_at': graph.known_at}
        episode = EpisodicNode(uuid=projection_id, name=graph.revision_id, group_id=graph.group_id,
            source=EpisodeType.json, source_description='Immutable canonical Wiki claims and original evidence',
            content=json.dumps({'page_id': graph.page_id, 'revision_id': graph.revision_id, 'content_hash': graph.content_hash,
                'claims': [n.model_dump(mode='json') for n in graph.nodes], 'relations': [e.model_dump(mode='json') for e in graph.edges]}, ensure_ascii=False, separators=(',', ':')),
            valid_at=graph.valid_from or graph.known_at, created_at=graph.known_at, entity_edges=list(edge_ids.values()))
        try:
            async with self.driver.transaction() as tx:
                await self.driver.episode_node_ops.save(self.driver, episode, tx=tx)
                await tx.run('MATCH (n:Episodic {uuid:$id}) SET n += $metadata', id=projection_id, metadata=metadata)
                for node in graph.nodes:
                    entity = EntityNode(uuid=node_ids[node.id], name=node.name, group_id=graph.group_id,
                        labels=[] if node.type == 'unknown' else [node.type], summary=node.summary, created_at=graph.known_at,
                        attributes={**metadata, 'knowledge_entity_id': node.id, 'knowledge_fragments': node.fragment_ids,
                            'knowledge_payload': node.model_dump_json()})
                    await self.driver.entity_node_ops.save(self.driver, entity, tx=tx)
                    mention = EpisodicEdge(uuid=digest([projection_id, 'mention', node.id]), group_id=graph.group_id,
                        source_node_uuid=projection_id, target_node_uuid=node_ids[node.id], created_at=graph.known_at)
                    await self.driver.episodic_edge_ops.save(self.driver, mention, tx=tx)
                for edge in graph.edges:
                    entity_edge = EntityEdge(uuid=edge_ids[edge.id], group_id=graph.group_id, source_node_uuid=node_ids[edge.source], target_node_uuid=node_ids[edge.target],
                        name=edge.type, fact=edge.text, episodes=[projection_id], created_at=graph.known_at, valid_at=graph.valid_from, invalid_at=graph.valid_until,
                        attributes={**metadata, 'knowledge_edge_id': edge.id, 'knowledge_kind': edge.kind, 'knowledge_fragments': edge.fragment_ids,
                            'knowledge_payload': edge.model_dump_json()})
                    await self.driver.entity_edge_ops.save(self.driver, entity_edge, tx=tx)
        except Exception as error:
            import logging
            logging.getLogger(__name__).error('Neo4j write failed: %s: %s', type(error).__name__, str(error)[:300])
            raise unavailable() from None
        return projection_id

    async def seeds(self, projection_ids, fragment_ids, limit):
        rows = await self._query('''
            MATCH (n:Entity)
            WHERE n.knowledge_namespace = $namespace AND n.knowledge_projection IN $projections
              AND any(id IN n.knowledge_fragments WHERE id IN $fragments)
            RETURN n.knowledge_projection AS projection_id, n.knowledge_payload AS node
            ORDER BY n.knowledge_entity_id, n.knowledge_projection LIMIT $limit
        ''', namespace=self.settings.graphiti_namespace, projections=projection_ids, fragments=fragment_ids, limit=limit)
        return self._decode(rows, ('node',))

    async def adjacent(self, projection_ids, frontier, types, direction, limit):
        # User data are parameters. The only selectable predicate is this fixed enum mapping.
        predicate = {
            'outgoing': 's.knowledge_entity_id IN $frontier',
            'incoming': 't.knowledge_entity_id IN $frontier',
            'both': '(s.knowledge_entity_id IN $frontier OR t.knowledge_entity_id IN $frontier)',
        }.get(direction)
        if predicate is None:
            raise unavailable('invalid_graph_response', 'Graph direction is invalid')
        rows = await self._query('''
            MATCH (s:Entity)-[e:RELATES_TO]->(t:Entity)
            WHERE e.knowledge_namespace = $namespace AND e.knowledge_projection IN $projections
              AND s.knowledge_projection = e.knowledge_projection AND t.knowledge_projection = e.knowledge_projection
              AND e.name IN $types AND ''' + predicate + '''
            RETURN e.knowledge_projection AS projection_id, e.knowledge_payload AS edge,
                   s.knowledge_payload AS source, t.knowledge_payload AS target
            ORDER BY e.knowledge_edge_id, e.knowledge_projection LIMIT $limit
        ''', namespace=self.settings.graphiti_namespace, projections=projection_ids, frontier=frontier, types=types, limit=limit)
        return self._decode(rows, ('edge', 'source', 'target'))

    @staticmethod
    def _decode(rows, fields):
        try:
            for row in rows:
                for field in fields:
                    row[field] = json.loads(row[field])
            return rows
        except (TypeError, ValueError, KeyError):
            raise unavailable('invalid_graph_response', 'Graph projection payload is invalid') from None