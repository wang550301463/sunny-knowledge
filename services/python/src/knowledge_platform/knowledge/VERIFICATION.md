# Verification evidence (2026-09-08)

Scope: canonical knowledge service and its own tests only. This is not a claim that the full approved V2 platform or external integrations are complete.

## Reproduce

From the repository root, after the shared Python environment and private Docker initialization exist:

```sh
services/python/.venv/bin/python services/python/tests/knowledge/run_postgres.py
services/python/.venv/bin/ruff check services/python/src/knowledge_platform/knowledge services/python/tests/knowledge
services/python/.venv/bin/ruff format --check services/python/src/knowledge_platform/knowledge services/python/tests/knowledge
```

The runner honors `TEST_DATABASE_URL`; otherwise it reads only `databases.knowledge` from ignored `knowledge-docker/.local/test-env.json` and never prints credentials. Every database test creates and removes its own random PostgreSQL schema. Tests never use SQLite or another service's tables. Without `TEST_DATABASE_URL`, invoking pytest directly skips the real database tests rather than pretending to verify them.

Latest run: **33 passed**, real PostgreSQL 17 container, 5.28 seconds; Ruff check passed. A subsequent final verification command is recorded in the agent response. Breakdown: five schema behavior tests, 21 PostgreSQL domain/transaction tests, seven HTTP boundary tests backed by PostgreSQL.

## Observed red/green work

- Fixed source revision/path/line/validity and fact-support tests: five initial failures because the schema implementation was absent, then five passed.
- Canonical publication behavior: nine real PostgreSQL failures because the service was absent; after implementation, proposal publication, CAS, rollback, immutable records, evidence access, auto-publication proof, lease/ack and ACL conjunction passed.
- Removing private citations: observed `DID NOT RAISE` on access after citation removal. Implemented immutable inherited access dependencies; the original test then passed.
- Structured invalidation and delegated source/page batches: four failures for missing behavior; implemented and passed.
- HTTP boundary: six failures before the FastAPI application existed; implemented and passed using real workload signatures.
- Agent review bypass: observed 200 where 403 was required for an agent carrying a reviewer's user token; implemented explicit route capabilities and passed.
- Human-reviewed page after invalidation: observed auto-publication succeed when review was still required; implemented publication lineage checks and passed.
- Expired claim under an open page interval: observed retrieval eligibility `true`; implemented claim/evidence validity checks and passed.

Supplemental verification covers real concurrent approvals (one succeeds, one conflicts), rollback after publication before commit (all canonical/audit/outbox records rolled back), expired lease retries fenced against old worker ACK, final rejection with no publication, and pagination that never exposes denied page IDs in results/cursors.

## What these tests establish

- PostgreSQL is actually exercised, including concurrent transactions, unique/version constraints, append-only triggers, atomic rollback and delivery locks.
- HTTP request middleware actually verifies ephemeral Ed25519 per-service identities. Public principal headers do not override the delegated authenticated actor. Gateway/agent cannot invoke projection capabilities; agent cannot approve; only ingest can register or auto-publish.
- Revoked supporting-source permission blocks page/history/review/citations even when the page's own ACL remains broad. Projection clauses preserve the conjunction including inherited provenance.
- Auth failures return 503 and no content; allowed exact evidence returns the original line slice.

## Explicit limits

The domain authorizer is injected in these tests; actual Go auth/IAM/Keycloak network integration belongs to the parent integration suite. Raw S3 objects are produced and verified by ingest; this service test suite checks exact text checksums and immutable manifests, not live S3 object existence. Projection consumers must implement and test version-guarded writes, user-facing live reauthorization, and ACK only after successful persistence. There is no declassification endpoint. Automatic fact compilation trusts the authenticated deterministic ingest compiler plus registered source proof; arbitrary public callers have no automatic publication capability.