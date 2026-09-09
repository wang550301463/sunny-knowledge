# Retrieval verification record

Branch: `codex/platform-v2`, uncommitted implementation in the V2 worktree. This records a bounded retrieval implementation, not complete platform acceptance.

Latest run on 2026-09-08:

```sh
knowledge-docker/scripts/compose.sh -f compose.observability.yaml run --rm --no-deps \
  -e RETRIEVAL_TEST_ES_URL=http://elasticsearch:9200 \
  -v ../services/python/src:/workspace/services/python/src:ro \
  -v ../services/python/tests:/workspace/services/python/tests:ro \
  regression pytest -q services/python/tests/retrieval services/python/tests/common
```

Result: **69 passed in 4.70 seconds**. The image supplies the locked Python environment; source/test mounts used the actual current worktree. All six PostgreSQL tests used the isolated retrieval database and per-test random schemas; both real ES tests used random `knowledge-retrieval-test-*` indices removed at teardown. No application index was deleted. Other tests use deterministic service-boundary doubles and do not claim real model, IAM, or Graphiti integration.

Covered behavior:

- Four RRF tests: equal keyword/vector weighting, graph 0.5, original ranking preservation, deduplication, deterministic ties and explicit budgets.
- Deterministic Wiki/claim fragments, source commit/snapshot/line pins, complete provenance policy conjunction and fingerprint, rejection of forged/incomplete projection metadata, exact character slicing, and full page/claim/source validity intersection.
- Identical BM25 and kNN hard filters, nested conjunction, space/status/time/current/model filters, no commercial fusion or post-filter substitute; real Elasticsearch confirms permitted/inherited resources succeed and tightening/time-invalid/stale resources do not recall.
- Sibling resources sharing an ACL domain cannot reopen an old tightened-policy fingerprint. Fresh principal epoch invalidates cache use. Mixed IAM epochs fail closed.
- Live canonical authorization before/after rerank and before output, resource revocation during the model call aborts the response, upstream auth failure prevents source content reaching rerank, exact original citation excerpts, graph failure versus authentication denial, safe graph partial-result warnings, path evidence supplements, and model configuration mismatch/missing behavior.
- Provider boundary validation: finite nonzero embedding vectors, exact dimensions/counts, pinned model item/character budgets, explicitly reported rerank text clipping, redirect refusal for machine credentials, real-client graph hop-boundary validation.
- Six real PostgreSQL tests: reverse event order retains distinct history without reverting current; uncertain ES failure rolls back receipt and consumes a non-reusable generation; old ACL epoch cannot overwrite newer; concurrent same-page writes serialize; rebuild checkpoints resume correctly and exclude concurrent rebuild; cancellation releases rebuild lock.
- Real ES external generations reject late lower-version writes; index metadata rejects mixed embedding configurations.
- Worker uses its separately configured machine identity, reuses existing vectors during ACL refresh, sends no freshly invalid content to a provider, indexes historical expired revisions at their declared valid time, respects Bailian batch/character limits with authorization before each actual provider request, isolates reconciliation failures and resumes explicit rebuild enumeration.
- HTTP workload/bearer boundaries reject forged identity, missing delegation, unsupported caller and unknown fields; validation errors do not echo private input.
- The same run includes 20 common security/auth/secrets/telemetry tests.

Static verification also passed:

```sh
services/python/.venv/bin/ruff check services/python/src/knowledge_platform/retrieval services/python/tests/retrieval
services/python/.venv/bin/ruff format --check services/python/src/knowledge_platform/retrieval services/python/tests/retrieval
```

Still required for full platform acceptance:

- Independent specification and quality review, with findings resolved.
- Compose retrieval API/worker service wiring, separate Keycloak/IAM machine registration/grants, configured real Bailian embedding/rerank IDs, and a real source → canonical outbox → ES → authorized HTTP search run.
- The newly agreed canonical revision enumeration endpoint must be exercised end-to-end for database/S3 restore and clean-index rebuild. Unit/PG checkpoint tests are not restore acceptance.
- Real Graphiti implementation and adjacency integration, including permission-domain invalidation, inferred edges, source/deployment-time distinction, and Agent use of paths.
- 60 human-labeled questions, Recall@10 target, citation-support/time evaluation, 100,000-fragment/10-concurrent local retrieval p95 measurement, and full outage/backlog/restart/restore tests. No quality or performance target is claimed from this small suite.
- WebUI historical revision links must honor returned pinned `?revision=`; Agent/MCP/channel complete permission-consistent flows remain parent work.