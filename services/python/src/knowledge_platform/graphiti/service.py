"""Live authorization and bounded BFS over actual Neo4j relationship adjacency."""
from datetime import UTC, datetime

from pydantic import ValidationError

from .authorization import AuthorizationGuard, PolicyCache
from .projection import digest
from .schemas import GraphEdge, GraphError, GraphNode, GraphPath, GraphResult, unavailable


class GraphService:
    def __init__(self, settings, authorizer, catalog, backend, clients, *, policy=None):
        self.settings, self.auth, self.catalog, self.backend, self.clients = settings, authorizer, catalog, backend, clients
        self.policy = policy or PolicyCache(ttl=0)

    @staticmethod
    def temporal(graph, request):
        return (
            graph.state == 'valid'
            and (request.include_historical or graph.is_current)
            and (graph.valid_from is None or graph.valid_from <= request.as_of)
            and (graph.valid_until is None or request.as_of < graph.valid_until)
            and (request.known_at is None or graph.known_at <= request.known_at)
        )

    async def traverse(self, token, request):
        request = request.model_copy(update={'as_of': request.as_of or datetime.now(UTC)})
        guard = await AuthorizationGuard.begin(self.auth, token, request.space_ids)
        records = await self.catalog.scope(request.space_ids, self.settings.graphiti_max_policy_records)
        fingerprints = set(await self.policy.allowed(guard, records))
        records = [r for r in records if r['policy_fingerprint'] in fingerprints and self.temporal(r['graph'], request)]
        permitted = await self.clients.pages(token, records, request, guard)
        records = [r for r in records if r['projection_id'] in permitted]
        await guard.finish()
        try:
            result, used = await self.walk(records, request)
        except GraphError as exc:
            if exc.code not in {'graph_unavailable', 'invalid_graph_response'}:
                raise
            await guard.finish()
            return GraphResult(degraded=['graph_unavailable'])
        selected = [r for r in records if r['projection_id'] in used]
        # Every proof of a path must survive together. Never return a dangling relationship
        # or retry with text already observed under an older identity/membership epoch.
        permitted = await self.clients.pages(token, selected, request, guard)
        if permitted != used:
            await guard.finish()
            return GraphResult(degraded=['graph_projection_pending'])
        refs = list({digest(ref): ref for r in selected for ref in r['graph'].evidence}.values())
        if len(refs) > 1000:
            await guard.finish()
            return GraphResult(degraded=['graph_evidence_budget_reached'])
        proofs = await self.clients.evidence(token, refs, guard)
        if proofs != {digest(r) for r in refs}:
            await guard.finish()
            return GraphResult(degraded=['graph_projection_pending'])
        await guard.finish()
        return result

    async def walk(self, records, request):
        by_projection = {r['projection_id']: r for r in records}
        projection_ids = sorted(by_projection)
        if not projection_ids:
            return GraphResult(), set()
        nodes, edges, paths, used, degraded = {}, {}, [], set(), set()
        paths_to = {}

        def checked(row, key):
            try:
                record = by_projection[row['projection_id']]
                value = row[key]
                candidates = record['graph'].edges if key == 'edge' else record['graph'].nodes
                original = next(v for v in candidates if v.id == value['id'])
                if original.model_dump(mode='json') != value:
                    raise ValueError
                return record, original
            except (KeyError, TypeError, ValueError, StopIteration, ValidationError):
                raise unavailable('invalid_graph_response', 'Graph projection identity is inconsistent') from None

        def add_node(record, node):
            if node.id not in nodes and len(nodes) >= request.max_nodes:
                degraded.add('graph_node_budget_reached')
                return False
            fs = sorted(set(nodes[node.id].fragment_ids if node.id in nodes else []) | set(node.fragment_ids))
            if len(fs) > 100:
                degraded.add('graph_evidence_budget_reached')
            nodes[node.id] = GraphNode(id=node.id, fragment_ids=fs[:100])
            used.add(record['projection_id'])
            degraded.update(record['graph'].degraded)
            return True

        seeds = await self.backend.seeds(projection_ids, request.seed_fragment_ids, 1001)
        if len(seeds) > 1000:
            degraded.add('graph_evidence_budget_reached')
        for row in seeds[:1000]:
            record, node = checked(row, 'node')
            if not set(node.fragment_ids).intersection(request.seed_fragment_ids):
                raise unavailable('invalid_graph_response', 'Graph seed mapping is inconsistent')
            if add_node(record, node) and node.id not in paths_to:
                path = GraphPath(node_ids=[node.id], edge_ids=[], fragment_ids=nodes[node.id].fragment_ids)
                paths_to[node.id] = path
        frontier = list(paths_to)
        for _ in range(request.hops):
            if not frontier:
                break
            found = await self.backend.adjacent(projection_ids, frontier, request.relation_types, request.direction, 1001)
            if len(found) > 1000:
                degraded.add('graph_edge_budget_reached')
            next_frontier = []
            for row in found[:1000]:
                record, edge = checked(row, 'edge')
                _, source = checked(row, 'source')
                _, target = checked(row, 'target')
                if edge.type not in request.relation_types or (source.id, target.id) != (edge.source, edge.target):
                    raise unavailable('invalid_graph_response', 'Graph adjacency response is inconsistent')
                directions = []
                if request.direction in {'outgoing', 'both'} and edge.source in frontier:
                    directions.append((source, target))
                if request.direction in {'incoming', 'both'} and edge.target in frontier:
                    directions.append((target, source))
                if not directions:
                    raise unavailable('invalid_graph_response', 'Graph adjacency response is inconsistent')
                if edge.id in edges:
                    continue
                if len(edges) >= request.max_edges:
                    degraded.add('graph_edge_budget_reached')
                    continue
                missing = {source.id, target.id} - set(nodes)
                if len(nodes) + len(missing) > request.max_nodes:
                    degraded.add('graph_node_budget_reached')
                    continue
                for start, end in directions:
                    previous = paths_to[start.id]
                    ids = sorted(set(previous.fragment_ids + edge.fragment_ids + start.fragment_ids + end.fragment_ids))
                    if len(ids) > 100 or len(paths) >= 200:
                        degraded.add('graph_evidence_budget_reached')
                        continue
                    path = GraphPath(node_ids=previous.node_ids + [end.id], edge_ids=previous.edge_ids + [edge.id], fragment_ids=ids)
                    if len(path.edge_ids) > request.hops:
                        continue
                    add_node(record, source)
                    add_node(record, target)
                    edges[edge.id] = GraphEdge(**edge.model_dump(include={'id', 'source', 'target', 'type', 'kind', 'fragment_ids'}))
                    paths.append(path)
                    if end.id not in paths_to:
                        paths_to[end.id] = path
                        next_frontier.append(end.id)
            frontier = next_frontier
        # Seed-only paths preserve explicit original support when no relation is known.
        if not paths:
            paths = list(paths_to.values())[:200]
        return GraphResult(nodes=list(nodes.values()), edges=list(edges.values()), paths=paths, degraded=sorted(degraded)), used