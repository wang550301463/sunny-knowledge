# Retrieval API v1

API: `uvicorn knowledge_platform.retrieval.app:app --host 0.0.0.0 --port 8080 --no-access-log`.
Projection consumer: `python -m knowledge_platform.retrieval.worker`. They share only the retrieval-owned PostgreSQL database and retrieval workload identity. No service reads another service's tables. Deployment files belong in `knowledge-docker/`.

All non-health routes require an Ed25519 `X-Service-Token` targeted at retrieval plus the unchanged delegated OAuth bearer. `/api/v1/{search,traverse,timeline}` allows gateway, agent, mcp, graphiti. `/internal/v1/{search,traverse,timeline}` allows agent, mcp, graphiti. A public JSON principal is rejected. User, department, group, service-account state and OAuth scope are resolved through auth on every operation. Management privileges do not grant content read access. No raw query, document, credential, bearer, provider error body or dynamic URL is logged. Common private metrics/tracing is installed; public `/metrics` is not provided.

## Search

`POST /api/v1/search` (same body internally):

```json
{
  "query": "Which modules depend on Payment?",
  "space_ids": ["engineering"],
  "relation": {"types": ["depends_on", "uses"], "direction": "incoming", "hops": 1},
  "limit": 12,
  "as_of": "2026-09-01T00:00:00Z",
  "known_at": null,
  "include_historical": false
}
```

Explicit space scope is required (1–100 unique IDs); every scope must be readable. Query is 1–8192 nonblank characters. `relation` is optional; the Agent selects relationship analysis explicitly rather than relying on a hidden keyword heuristic. Direction is outgoing/incoming/both, hops 1–2, relation types come from the nine approved types. `limit` is 1–12 primary fragments. Optional `embedding_configuration_id` and `rerank_configuration_id` pin explicit model configurations; omitted values use operator-configured IDs. An embedding override must match the configured index model exactly. Models are never chosen by name guessing or silently replaced. Missing model configuration returns 503 `model_not_configured`; an incompatible embedding model returns 409 `index_configuration_conflict`.

Processing:

1. Resolve current membership/epoch; require read on every requested space.
2. Load projection policy references for the scope. IAM `POST /internal/v1/policies/batch` supplies up to 1,000 current policies in one repeatable-read snapshot. All batches and the principal must share one epoch. The bounded cache is keyed by epoch and resource identity; changing membership or grants invalidates use of the old epoch immediately.
3. Whitelist only complete provenance policy fingerprints whose policies still match IAM and whose every read clause intersects current subjects. ES requires those fingerprints **and** full ACL domains **and** nested conjunction of subject clauses, space, state, time, current/history mode and embedding model before both recall paths. Policy fingerprints include resource identities and versions: a still-readable sibling in the same ACL domain cannot reopen a document whose own policy tightened.
4. BM25 and dense-vector kNN each recall 50 using identical hard filters. `knn.filter` is the prefilter; no post-filter or commercial RRF/document security is used. Python equal-weight RRF with `k=60` supplies up to 30 authorized seeds.
5. Optional graph request uses at most 2 hops, 100 nodes, 200 edges. Returned IDs are resolved using the same ES filters and live canonical authorization. Graph contributes weight 0.5 to the **original** BM25/vector rankings; fusion retains at most 60.
6. All candidates are authorized through canonical `pages/authorize` immediately before explicit-model reranking. Reauthorize after the model returns and again before returning content. Select at most 12 primary fragments; complete applicable graph paths with supplementary relationship fragments from the same already-authorized/reranked budget.
7. Resolve original references through canonical `evidence/authorize` in batches of 100, preserving exact excerpts. Deduplicate original references independently of chunk count, discard unsupported fragments/paths, and recheck the current epoch before response emission.

Response:

```json
{
  "items": [{
    "id": "fragment hash", "page_id": "page:payments", "revision_id": "immutable Wiki revision",
    "version": 3, "space_id": "engineering", "title": "Payment", "text": "...",
    "kind": "fact", "entity_ids": ["claim:call", "page:payments"], "evidence": [],
    "citation_ids": ["reference hash"], "primary": true, "rrf_score": 0.03,
    "rerank_score": 0.9, "url": "/pages/page%3Apayments?revision=...",
    "state": "valid", "valid_from": null, "valid_until": null,
    "known_at": "2026-09-01T00:00:00Z", "is_current": true
  }],
  "evidence": [{"id": "reference hash", "evidence": {}, "space_id": "engineering", "excerpt": "exact original", "sha256": "..."}],
  "graph": {"nodes": [], "edges": [], "paths": [], "degraded": []},
  "degraded": [], "gaps": [], "auth_epoch": 19,
  "as_of": "2026-09-01T00:00:00Z", "known_at": null,
  "time_basis": "source_revision_and_knowledge_time",
  "deployment_state": "unknown_without_deployment_evidence"
}
```

`kind` is fact/inference/gap/narrative. Narrative chunks are reviewed Wiki text, not independent original evidence. `evidence` uses the canonical `EvidenceRef` contract (source snapshot UUID in `revision_id`, immutable Git/source revision and LF-based original location); the containing Wiki revision is separately pinned on each item. ACL subject lists, internal policy fingerprints and embeddings are not returned. Scores express rank only. Absence of original evidence is explicitly `original_evidence_missing`; an empty authorized result is `no_authorized_evidence`. Graph service/protocol failure returns safe `graph_unavailable` while preserving ES results; graph authentication/authorization failure aborts the request. ES partial results, shard failures and timeouts produce 503, never an apparently complete empty success.

`as_of` is the business-valid instant, default now; start inclusive, end exclusive. `known_at` restricts what the platform had learned by that timestamp. `include_historical=true` allows individually pinned immutable versions already projected through the outbox, adds `history_contains_projected_revisions_only`, and does not assert that different commits coexisted or were deployed. Exact revision history is available through timeline/canonical knowledge APIs. An Agent must keep source revisions distinct and report conflicting evidence rather than infer a production version.

## Traverse

`POST /api/v1/traverse` / `/internal/v1/traverse`:

```json
{"space_ids":["engineering"],"seed_fragment_ids":["fragment hash"],"relation":{"types":["depends_on"],"direction":"outgoing","hops":2},"include_historical":false}
```

Shares `as_of`, `known_at`, explicit scope and all authorization filters. Up to 30 seeds. At most 1,000 unique mapped fragments may be fetched to support the bounded graph; larger protocol responses fail safely. No language model is called by this endpoint. Response is the same evidence/graph shape, with seed fragments marked primary and supplementary relationship evidence included.

Graph adapter contract, **to be implemented by graphiti**:

`POST graphiti /internal/v1/traverse`, delegated bearer plus retrieval workload:

```json
{"space_ids":["engineering"],"seed_fragment_ids":["..."],"relation_types":["depends_on"],"direction":"outgoing","hops":1,"max_nodes":100,"max_edges":200,"as_of":null,"known_at":null,"include_historical":false}
```

Strict response fields:

- `nodes`: `{id,fragment_ids:[nonempty]}`; at most 100.
- `edges`: `{id,source,target,type,kind:"fact"|"inference",fragment_ids:[nonempty]}`; at most 200.
- `paths`: `{node_ids:[1..3],edge_ids:[0..2],fragment_ids:[nonempty]}`; at most 200.
- `degraded`: string list (empty on complete traversal).

Each list of fragment IDs is bounded to 100 per element. IDs are mapped to original evidence by the retrieval catalog. Fragment IDs are SHA-256 of canonical UTF-8 JSON `[page_id,revision_id,"markdown",ordinal]` or `[page_id,revision_id,"claim",claim_id,ordinal]`; deterministic chunk length defaults to 2,000 characters. Claims and page IDs are `entity_ids`. Graphiti must maintain these mappings, perform actual adjacency traversal, enforce full partition/time/live authorization, and return only evidence-backed relations. Retrieval additionally validates unique IDs, relation types, referenced endpoints, direction, reachability from seed-mapped nodes, maximum depth, and connected path order. A path begins at a node mapped to at least one requested seed. Unsupported/unauthorized paths are pruned as a whole.

## Timeline

`POST /api/v1/timeline` / `/internal/v1/timeline` with `{space_ids,page_ids:[1..20],limit:1..100,known_at?,as_of?}` reads real canonical revision history (up to 100 per page), applies current complete provenance authorization, and returns safe revision events: `{page_id,revision_id,version,known_at,state,publication_kind,source_revisions:[{source_id,source_revision}]}`. It includes stale/retracted historical records for inspection, without treating their claims as current model facts. Final authorization uses the canonical `authorized` inspection flag, not the retrieval `allowed` flag. `truncated` explicitly marks the bounded history window; no fabricated exhaustive timeline or production deployment is claimed.

## Projection worker and rebuild

The retrieval worker acquires knowledge outbox leases for its own consumer only (one event, 300 seconds), materializes the immutable projection through a workload-only contract, and performs new embeddings using a **separate configured machine account with explicit live read grants**. It never borrows ingest credentials or persists an end-user bearer. Before every embedding batch and after inference, canonical page/provenance authorization is checked at one coherent epoch. Historical or future revisions use an instant within their explicit business-valid interval for indexing; this is independent of user query time. Invalidated revisions remain unsearchable and are projected without sending fresh invalid content to a model. ACL reconciliation reuses existing vectors without resending text to a provider.

Retrieval-owned PostgreSQL stores a rebuildable page/revision projection catalog, complete policy references, vectors, and durable event receipts. Page processing is serialized under a transaction advisory lock. Canonical current versions advance monotonically; every immutable revision retains separate fragment IDs. A PostgreSQL sequence allocates an external ES generation that does **not** roll back on failure; late uncertain writes cannot overwrite a later generation. ES uses `external_gte` only for identical-generation idempotent retries; lower-version conflicts fail the operation. The revision metadata and delivery receipt commit only after all ES writes are acknowledged; knowledge ACK then uses the current lease token. Lost/expired ACKs are redelivered without repeating committed projection work. ACL refresh cannot replace a newer epoch with an older policy snapshot.

Periodic reconciliation re-materializes catalog revisions and updates ACL/current metadata, preserving embedding vectors. Live policy-fingerprint filtering blocks tightened stale projections immediately, including before reconciliation. Source/projection outages remain retryable. ES is a projection: restoring PostgreSQL/S3 and replaying canonical outbox plus reconciliation rebuilds it; use a fresh ES index for a restored database/sequence so an older generation cannot conflict with an unreconciled newer index. The canonical outbox consumer replay API for a completely lost retrieval catalog remains a platform restore integration requirement; this worker can rebuild an empty ES index from an intact retrieval catalog by reconciliation, and does not claim a missing canonical replay endpoint exists.

## Configuration

Uses common database URL, per-service private/public key files, auth/IAM/knowledge/LLM/graphiti URLs, request timeout, private metrics port and OTLP endpoint, plus:

- `RETRIEVAL_ES_URL` default `http://elasticsearch:9200`; `RETRIEVAL_ES_INDEX` default `knowledge-fragments-v2`; optional private `RETRIEVAL_ES_API_KEY`.
- `RETRIEVAL_EMBEDDING_CONFIGURATION_ID`, `RETRIEVAL_EMBEDDING_DIMENSIONS` (1–4096), `RETRIEVAL_RERANK_CONFIGURATION_ID`: explicit operator selections.
- `RETRIEVAL_OIDC_TOKEN_URL`, `RETRIEVAL_OIDC_CLIENT_ID`, private `RETRIEVAL_OIDC_CLIENT_SECRET`: retrieval machine account. Only `knowledge:read` is requested; IAM must register the identity and grant every page/source space it indexes.
- `RETRIEVAL_POLL_SECONDS` default 2; `RETRIEVAL_RECONCILE_SECONDS` default 60; `RETRIEVAL_WORKER_TIMEOUT_SECONDS` default 240 (maximum 270, below the 300-second outbox lease).
- `RETRIEVAL_MAX_POLICY_DOMAINS` default 10,000, maximum 50,000; exceeding the bounded registry scope returns 503. `RETRIEVAL_POLICY_CACHE_SECONDS` default 5 (0–30), always fenced by current epoch.
- `RETRIEVAL_GRAPH_TIMEOUT_SECONDS` default 5 (0.1–30).

Each ES index pins one immutable embedding configuration and dimension in mapping metadata. Updating embedding model, dimension or chunk schema requires a separate rebuild/cutover; silently mixing vectors is refused. Index initialization uses a strict mapping, nested ACL clauses, explicit HNSW cosine dense vectors, one shard/no replica for local development. Production shard/replica sizing and benchmark acceptance remain deployment decisions.

Primary protocol references: [Elastic kNN prefilters](https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-knn-query), [Elastic external versioning](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/docs-index_.html). These justify the actual APIs used; Python RRF requires no Elasticsearch commercial fusion feature.