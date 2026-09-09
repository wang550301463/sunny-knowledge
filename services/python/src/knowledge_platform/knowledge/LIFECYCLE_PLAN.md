# Canonical lifecycle implementation plan

Approved by the implementation coordinator. Execute inline in the existing V2 worktree; no new agents, commits, Web changes, Go changes or live deployment edits.

**Goal:** expose independently computed source support, hard validity, fixed-source freshness and personal usage; materialize daily freshness snapshots idempotently.

**Architecture:** canonical-owned pure calculation plus immutable PostgreSQL access events/snapshots and monotonic heads. Public reads reauthorize the exact Wiki revision and its complete source/input closure. A dedicated read-only service account drives a bounded Temporal workflow over authenticated Knowledge HTTP.

**Stack:** existing SQLAlchemy/PostgreSQL, FastAPI/Pydantic, HTTPX, Temporal SDK; no new dependencies.

## Approved contracts

- `GET /api/v1/pages/{page_id}/lifecycle?revision_id=&as_of=` computes current authorized metrics. Historical revision selection is explicit; dynamic reads never write a snapshot/head. Default time is server UTC; timestamps must be timezone-aware. Policy `freshness-v1` is immutable: Policy/Decision/Procedure 180 days, Service/Module/Person/Dependency 90, File/Incident/Change 21, absent type 90.
- Source support counts distinct globally registered `source_id` values from explicit original support. Multiple snippets, versions or spaces with the same source ID count once. Source manifests are excluded. A Wiki or summary is never an independent source. Registered sources are not a claim of statistical independence or semantic entailment.
- Source age starts at the earliest immutable registration timestamp of explicit original support. Unsupported content reports unknown freshness. `2 ** (-max(0, as_of - observed_at) / half_life)` is only freshness, never confidence. Reads, edits and summaries cannot reset its baseline.
- `POST /api/v1/pages/{page_id}/access-events {revision_id,idempotency_key}` accepts a normal Web user via gateway. It records server time, authenticated actor and a hash of the complete current provenance policy domain. Same actor/key/body is idempotent; changed body is 409. Workloads, MCP and channel delegations cannot write personal usage. The operation requires current full read authorization, not a fact-edit scope.
- Access results are explicitly personal: exact actor/revision/current ACL domain, recent 7/30-day counts and last access. No cross-user, channel, domain or enterprise aggregation. GET and internal authorization/model traffic are not access events. The Web coordinator will wire explicit Wiki display to this endpoint.
- Daily materialization: `POST /internal/v1/lifecycle/reconcile {space_id,scheduled_at,cursor?,limit,idempotency_key}` permits only Knowledge workload plus the configured lifecycle service-account principal. A daily timestamp is UTC midnight and cannot be in the future. Batch limit <=25; hidden pages never appear in output or cursors. The first transaction freezes selected revision IDs in the existing idempotency operation; replays reauthorize those exact inputs.
- Immutable snapshots are unique on revision/policy/day. A head changes only while the page's current revision matches, and never moves backward in revision/day. Personal usage is never materialized into shared snapshots. Canonical content/current revision/audit/outbox publication semantics remain unchanged.
- A dedicated worker uses deterministic policy/space/day workflow IDs, fresh client-credentials tokens per activity and a separate Temporal task queue. Only explicit configured spaces are enrolled. Credentials and raw data never enter workflow history. Coordinator owns service-account registration/grants and Compose wiring after contract freeze.

## Execution checklist

- [ ] Pure metric behavior first: source ID dedup across versions/spaces, manifest exclusion, fixed observed time, class half-lives, no-support unknown, validity intervals, no probability/confidence field. Run new tests and retain expected missing-feature failures before adding `lifecycle.py`/strict schemas.
- [ ] Real PG events and HTTP boundaries: POST replay/conflict, actor/domain isolation, inherited private Wiki/source denial, same-epoch failure, delegated/workload rejection. Add append-only table constraints and service methods only after the failing tests.
- [ ] Real PG snapshots: repeated batch yields identical IDs without extra audit; older day cannot replace head, concurrent newer revision cannot be overwritten, stale policy mismatch fails, future scheduling fails, replay after revocation fails closed. Add immutable snapshots, policy and head guards; preserve existing schema migrations.
- [ ] Temporal/client behavior: deterministic workflow IDs, pagination, safe retry errors, service-account pinning, no token in histories, cancellation and HTTP deadline cleanup. Add owned worker/config modules and bounded HTTP fixtures; no live changes.
- [ ] Run knowledge/common regression against actual isolated PG, then Ruff. Record actual counts and scope in `VERIFICATION.md`, document DTOs/environment/worker command in `API.md`, freeze for independent review.

## Verification commands

Use the existing regression image and knowledge-owned `TEST_DATABASE_URL`; tests create random schemas and never access another service database. Narrow execution is `pytest tests/knowledge/test_lifecycle*.py -q`; final regression adds all `tests/knowledge` and `tests/common`. Worker HTTP tests use protocol fixtures and are not real production scheduling acceptance.
