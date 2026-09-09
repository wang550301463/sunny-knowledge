# Graphiti implementation verification

Recorded 2026-09-08 in `codex/platform-v2`. This verifies the Graphiti domain implementation and its real middleware boundaries, not completion of the whole V2 platform.

## Executed evidence

Final Docker run against the existing isolated `sunny-knowledge-v2` PostgreSQL and Neo4j services:

```sh
knowledge-docker/scripts/compose.sh run --rm --no-deps \
  -v "$PWD/services/python/src:/workspace/services/python/src:ro" \
  -v "$PWD/services/python/tests:/workspace/services/python/tests:ro" \
  regression pytest -q services/python/tests/graphiti services/python/tests/common
```

**73 passed, 0 skipped, 4.00 seconds**: 53 Graphiti tests plus 20 common tests. One upstream Graphiti `SearchInterface` Pydantic class-config deprecation warning remains; it is not a failed test or a swallowed application warning. The regression image uses locked graphiti-core 0.30.1, neo4j driver 6.3.0, actual Compose Neo4j 5.26.12 and PostgreSQL 17.6. `TEST_GRAPHITI_DATABASE_URL`, `TEST_GRAPHITI_NEO4J_URI` and private `TEST_GRAPHITI_NEO4J_PASSWORD` are passed only through local Compose configuration.

- 6 actual Neo4j tests: SDK episode/entity/edge/mention CRUD; real outgoing/incoming/type-limited adjacency; late older physical generation after newer write; full episode rollback on mid-write failure; combined graphiti PG + Neo4j loss/rebuild; same-generation identity separation after restored sequence; real two-hop cross-ACL-partition traversal followed by immediate policy tightening; explicit rebuild of corrupted payload with unchanged object counts. Some tests cover several related behaviors.
- 8 actual graphiti-owned PG tests: cancelled/single rebuild lock; resumable/restartable checkpoints; uncertain graph result leaves no committed delivery and uses a higher retry generation; page publication concurrency and out-of-order current-version protection; stale ACL snapshots and mutated immutable content; failure backoff isolation; unchanged reconciliation reuse/missing graph repair; metadata-only scope plus GIN seed lookup and exact candidate hydration.
- Remaining domain tests exercise deterministic evidence/fragment mappings and all nine relation types; no name-based merging; malformed ACL/current/state/source/time inputs; fact versus inference distinction; direction/depth/node/edge/evidence budgets; live IAM fingerprint and epoch behavior; canonical page/evidence revocation before return; safe graph failure/forged payload/missing projections; machine OAuth no redirect or credential echo; own-consumer lease and post-commit ACK; worker authorization before/after graph write; HTTP workload/bearer/unknown-principal rejection and chunked request-byte limits.

The real middleware fixtures use unique PostgreSQL schemas and unique graph namespaces. Cleanup removes only those fixtures. They do not delete shared Neo4j indexes, other applications' data, production namespaces, Docker volumes, or user repositories. Auth/canonical behavior inside the combined graph tests is a controlled protocol fixture; full deployed Keycloak → IAM/auth → knowledge → graphiti-worker → graphiti → retrieval HTTP acceptance is a separate integration task.

Lint/format checks also passed:

```sh
services/python/.venv/bin/ruff check services/python/src/knowledge_platform/graphiti services/python/tests/graphiti
services/python/.venv/bin/ruff format --check services/python/src/knowledge_platform/graphiti services/python/tests/graphiti
```

Both clean; 26 Python files already formatted. New behavior tests were first run before their implementation to establish failures for missing compiler/traversal/backend modules; actual middleware then exposed and fixed two SDK/Neo4j boundary issues: SDK index/constraint overlap and unconditional null-vector setters. The adapter uses genuine Graphiti query builders and transactions without fabricating model vectors.

## Integration and operational limits

- No real Chat/Embedding/Reranker provider call occurred in this graph suite. Structured canonical facts and reviewed relation claims require no model inference. Graphiti does not instantiate a default external-model client or expose arbitrary LLM extraction/name merging; the LLM service remains the exclusive model gateway.
- Full deployed HTTP publication/outbox/worker/retrieval integration, 100,000-fragment/10-query performance acceptance, service-process crash/lease recovery, and complete canonical PG + S3 backup/restore → graph rebuild remain platform integration checks owned by the parent task. The scope metadata registry is bounded but current canonical authorization still batches every eligible scoped revision before traversal; its end-to-end latency must be measured on the acceptance dataset rather than inferred from these small fixtures.
- Ordinary reconciliation checks object presence/counts and policy identity; an explicit `--rebuild` deliberately rewrites all enumerated revisions to repair inconsistent payloads. Orphan generations are inaccessible but retained. Full namespace cutovers must quiesce old writers and avoid running conflicting namespaces against one catalog; retained-orphan capacity is an operations concern, not evidence that a deleted graph was successfully restored.
- Exact official protocol, SDK adaptation, service configuration, ACL semantics, history behavior and rebuild commands are documented in `API.md`. Independent specification/security/quality review is still required before integrating this implementation batch.