# Graphiti API v1

HTTP: `uvicorn knowledge_platform.graphiti.app:app --host 0.0.0.0 --port 8080 --no-access-log`.
Consumer: `python -m knowledge_platform.graphiti.worker`; rebuild: append `--rebuild`.
Each process uses only the graphiti PostgreSQL role, its own Ed25519 workload identity, and the configured Neo4j database. It never reads another service's database. All deployment files and test runners are in `knowledge-docker/`.

## Traversal contract

`POST /internal/v1/traverse` accepts retrieval, agent or mcp workload identities; `POST /api/v1/graph/traverse` also accepts gateway. Both require the unchanged delegated OAuth bearer. Identity is resolved live by auth; callers cannot supply a JSON principal, Cypher, arbitrary node labels or a preauthorized graph partition. Scope read permissions are required even for an empty result. Management permissions do not imply content access. Gateway may continue exposing retrieval's public `/api/v1/traverse`, which adds exact original excerpts and citations around this internal graph result.

```json
{
  "space_ids": ["engineering"],
  "seed_fragment_ids": ["immutable-fragment-sha256"],
  "relation_types": ["depends_on", "uses"],
  "direction": "outgoing",
  "hops": 1,
  "max_nodes": 100,
  "max_edges": 200,
  "as_of": null,
  "known_at": null,
  "include_historical": false
}
```

Space IDs: 1–100 unique. Seed IDs: 1–30 unique. Relation types: 1–9 unique, restricted to `depends_on, uses, owns, cites, governed_by, caused, fixed, supersedes, contradicts`. Direction is outgoing/incoming/both. Hops are 1–2. Node budget 1–100, edge budget 1–200. Times require offsets. `as_of` defaults once per request to now; valid start inclusive/end exclusive. `known_at` limits canonical system observation time. Current-only is the default; historical mode queries individually pinned historical revisions without claiming that different commits were simultaneously deployed. No production-deployment inference is made.

```json
{
  "nodes": [{"id":"source-namespaced-entity", "fragment_ids":["fragment-sha256"]}],
  "edges": [{"id":"immutable-edge-sha256", "source":"source-namespaced-entity", "target":"dependency-entity", "type":"depends_on", "kind":"fact", "fragment_ids":["relationship-fragment-sha256"]}],
  "paths": [{"node_ids":["source-namespaced-entity","dependency-entity"], "edge_ids":["immutable-edge-sha256"], "fragment_ids":["support-fragment-sha256"]}],
  "degraded": []
}
```

Every returned element has nonempty original fragment mappings (maximum 100 each). Paths start at a seed-mapped node, follow actual directed relationships, have at most two edges/three nodes, and retain all selected path support. At most 200 paths are returned. A maximum of 1,001 physical rows is read for seed/each adjacency stage; the 1,001st row signals incomplete bounded evidence/adjacency. Nodes and relationships are selected deterministically, with explicit `graph_node_budget_reached`, `graph_edge_budget_reached`, or `graph_evidence_budget_reached` when a budget prevents completeness. No disconnected or unsupported relationship is returned.

`graph_projection_pending` means an endpoint has no loaded explicit canonical entity declaration, a known seed projection is missing in Neo4j, or authorization/current revision changed enough to invalidate an assembled result. Missing endpoint declarations remain evidence-backed placeholders with an ID and `unknown` type inside the projection; their relation evidence proves the mention, not an inferred entity description. An available declaration in a different visible partition can support traversal by its explicit stable ID. `graph_unavailable` is an explicit empty degraded result for graph transport/protocol failure. Auth/IAM/canonical failure is a 401/403/503 failure, never a permissive graph fallback. Error messages contain only safe codes and no source text, token, Cypher parameter or dependency response body.

Request bytes are capped at 256 KiB, including chunked bodies without Content-Length. `/healthz` is process liveness. `/readyz` probes graphiti PG and Neo4j. Application startup tolerates unavailable Neo4j so callers can receive explicit degraded graph results; worker initialization remains retryable. Telemetry uses common bounded route/method/status/duration instrumentation; there is no public metrics endpoint or raw SDK debug tracing.

## Authorization and graph storage

Each projection validates the canonical policy set against the page plus every source snapshot/resource dependency. Its Graphiti `group_id` is a stable hash of **space + full effective ACL domain**. The complete resource/version policy fingerprint is separate from the shared ACL-domain hash; a readable sibling cannot authorize a stale tightened document in the same domain.

A request loads only bounded graphiti-owned PostgreSQL metadata (up to `GRAPHITI_MAX_POLICY_RECORDS`); node text, summaries, edges and source evidence are hydrated only for actual graph candidates. IAM `policies/batch` obtains current policies at one epoch. Every conjunction clause must intersect the live user's subjects; missing/mismatched policies exclude the entire projection before Neo4j receives its ID. All candidate revisions are checked with canonical `pages/authorize`, including current version, provenance, state and valid time, before adjacency. Neo4j queries only the whitelisted committed projection IDs. Cross-partition traversal joins only explicit canonical entity IDs appearing in those already authorized projections; it adds no fabricated relationship between matching names.

Selected paths are rechecked with current canonical page and original-evidence authorization before output, then the identity/epoch is resolved again. If a selected path's supporting revision is no longer eligible, this implementation returns an empty `graph_projection_pending` result instead of an incomplete path. A coherent-epoch failure aborts. The retrieval service independently resolves returned fragment IDs through its own ACL-prefiltered ES and canonical evidence checks before any model or final text.

Node summaries are exact published claim text, stored on per-revision physical nodes in that full ACL partition; they are never merged across documents, revisions or scopes. The traversal API returns evidence IDs rather than raw graph summaries, so all displayed/generated content continues through canonical/retrieval access checks. There are no public arbitrary graph-read, count, summary, merge or write endpoints.

## Canonical structure and real SDK behavior

Canonical `Claim.entity` is optional `{id,name,type}` and must match the claim's stable ID and entity type. Without this explicit field, an evidence-backed claim can still form a node keyed by `claim.id`; no name-based entity resolution is performed. Only `Claim.relation={source_id,target_id,type}` with nonempty original support becomes a graph relation. Natural-language dependency mentions without this structure do not become formal edges. `kind=inference` is retained distinctly from `fact`; gap claims cannot create edges. Compiler-generated deterministic facts and reviewed inference publications follow the canonical publication rules; Graphiti cannot publish or promote a conclusion itself.

Fragments match retrieval's exact UTF-8 canonical JSON SHA-256 algorithm, with 2,000-character chunks: `[page_id,revision_id,"claim",claim_id,ordinal]` and `[page_id,revision_id,"markdown",ordinal]`. Original source snapshot, source version and line references are retained in every projected claim. The full page/claim/evidence business-valid interval and state intersection is identical to retrieval's hard constraints.

This implementation uses **graphiti-core 0.30.1** `Neo4jDriver`, `EpisodicNode`, `EntityNode`, `EntityEdge`, `EpisodicEdge`, and its parameterized CRUD query builders. An episode and all entity/relationship/mention records commit inside one actual SDK Neo4j transaction. Queries use real `MATCH (s:Entity)-[e:RELATES_TO]->(t:Entity)` adjacency for each bounded BFS level. No graph vector search substitutes for traversal. Static facts require no generated model text and make no Chat/Embedding calls. Graphiti's `add_episode` extraction/name-deduplication and community summarization are intentionally not public capabilities; model-derived formal changes continue through Agent proposals and canonical review. No provider credentials are accepted or used by Graphiti.

The pinned SDK's ordinary entity/edge save unconditionally invokes Neo4j vector setters even when embeddings are `None`; Neo4j rejects that. `sdk.py` extends the SDK operations only for absent vectors, using its official `has_aoss=True` query-builder switch to omit those setters. Present vectors use the normal SDK method. No placeholder/fake vector is stored and no external vector store is contacted. Application-specific labels provide UUID constraints without deleting/conflicting with SDK-owned range indexes. This behavior is verified against the actual Compose Neo4j, not a graph mock.

Official background references: [Graphiti CRUD](https://help.getzep.com/graphiti/working-with-data/crud-operations), [Graphiti namespaces](https://help.getzep.com/graphiti/core-concepts/graph-namespacing), [SDK Neo4j operation source](https://github.com/getzep/graphiti/blob/main/graphiti_core/driver/neo4j/operations/entity_node_ops.py). Exact API compatibility is checked against the installed, locked 0.30.1 package source; upstream main/docs can evolve independently.

## Durability, recovery and configuration

Graphiti consumes only its own canonical outbox lease, one event/300-second lease at a time. Canonical projection reads are workload-only internal materialization; a separate configured graph machine OAuth account must have live explicit read grants on the page and all provenance before any graph write. Its bearer is fetched in memory and is never stored in PostgreSQL/outbox/checkpoints. Failed writes leave the canonical lease retryable. A receipt and catalog generation commit in graphiti PG only after Neo4j confirms the complete write and the machine still passes authorization. ACK uses the exact current canonical lease token only after this PG commit; redelivery of a committed receipt is idempotent.

PG serializes page projection work with transaction advisory locks. A nonrollback sequence assigns a new physical generation on each actual write. Each physical graph ID additionally includes namespace, canonical page/revision, immutable content hash and full policy fingerprint. Older uncertain writes and graph commits whose PG transaction failed remain unreachable orphans; they cannot overwrite a later committed projection or share a mutable summary with it. Current page versions move monotonically, including out-of-order historical delivery. Immutable content identity changes and regressing ACL snapshots are rejected/skipped. Restoring an old sequence cannot alias differing policy/content hashes.

Periodic reconciliation obtains fresh canonical materialization and policy metadata. Unchanged graph projections are reused only after their episode/entity/edge presence is checked, avoiding a new full graph every minute. Missing graph objects, changed ACL domains or policies produce a new generation; the old one is immediately excluded by live fingerprint authorization before rebuild. Per-revision failure codes and bounded backoff let reconciliation continue beyond a failed revision. There is no automatic orphan deletion: no maintenance process guesses whether a delayed transaction could still commit. Capacity/retention cleanup can use a deliberate namespace cutover after in-flight workers are quiesced.

`--rebuild` enumerates canonical `/internal/v1/projections/revisions` independent of already-ACKed outbox deliveries. A durable PG checkpoint advances only after the entire returned batch commits; interrupted runs replay that batch safely. A session advisory lock permits one rebuild for a namespace/database. A completed explicit rebuild starts again from the beginning on the next invocation. Restore canonical PG/S3, start a fresh graph namespace/database when performing a full cutover, run rebuild, and continue the normal outbox consumer to catch concurrent publications. Actual test coverage includes deleting only randomly namespaced test graph nodes and restoring them from owned catalog/canonical projection input; a full deployed multi-service PG/S3 restore remains separate integration acceptance.

Configuration inherits common `DATABASE_URL`, service private/public key files, AUTH/IAM/KNOWLEDGE URLs, `REQUEST_TIMEOUT`, private `METRICS_PORT` and OTLP endpoint:

- `GRAPHITI_NEO4J_URI=bolt://neo4j:7687`, `GRAPHITI_NEO4J_USER=neo4j`, private `GRAPHITI_NEO4J_PASSWORD`, `GRAPHITI_NEO4J_DATABASE=neo4j`.
- `GRAPHITI_NAMESPACE=knowledge-v2`: owned graph instance/cutover namespace; API only reads this namespace.
- `GRAPHITI_OIDC_TOKEN_URL`, `GRAPHITI_OIDC_CLIENT_ID`, private `GRAPHITI_OIDC_CLIENT_SECRET`: graph-specific client credentials, scope `knowledge:read`.
- `GRAPHITI_POLL_SECONDS=2` (0.1–60), `GRAPHITI_RECONCILE_SECONDS=60` (1–3600), `GRAPHITI_WORKER_TIMEOUT_SECONDS=240` (10–270, below lease).
- `GRAPHITI_QUERY_TIMEOUT_SECONDS=5` (0.1–60): each Neo4j query, separately bounded from authorization IO.
- `GRAPHITI_MAX_POLICY_RECORDS=10000` (1–50000): hard cap for requested revision/policy registry scope; excess returns 503 with no partial authorized count.

The normal graph backend never instantiates the SDK's default OpenAI client or external telemetry client. Raw SDK and Neo4j driver logging is disabled to avoid driver exception/parameter exposure; safe service warnings and common telemetry remain available. Model setup and Qwen credentials belong exclusively to the LLM service; this deterministic graph stage can be exercised independently of those credentials.