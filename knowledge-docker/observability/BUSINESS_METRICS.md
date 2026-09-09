# Business metrics

This increment adds process-local operation metrics and authoritative **own-database**
queue snapshots. The existing observability overlay enables the three Python worker
listeners on backend-only port 9090. No public API or gateway `/metrics` route is added.
Prometheus targets each worker separately. A stopped target remains `up=0`; it is not an
empty queue. Deployment and a live Prometheus/Tempo acceptance gate are separate from
the source-level Docker regression below.

## Fixed labels and scope

`knowledge_domain_operations_total` and
`knowledge_domain_operation_duration_seconds{service,component,operation,outcome}` use
allowlists in `common/domain_metrics.py`; unexpected labels become `other`. No principal,
space, source, task, invocation, model configuration or provider identifier is a label.
No prompts, source text, code, response text, credentials, URLs, exception messages or
hidden reasoning are inspected or exported. The matching OTel spans have fixed operation
names, no payload attributes and no exception events. Existing HTTP spans continue to
use route templates. Model and projection identifiers remain in their authorized domain
records, not metrics.

Counters count **attempts**, including retries, not unique tasks or billing events. An
operation succeeds only after its awaited result and transaction boundary complete.
Metrics are not transactional billing records: a process can exit after a database
commit and before incrementing a counter. A caller retry can count an additional attempt.

- `model`: `chat`, `chat_stream`, `embedding`, `rerank` include metadata validation,
  per-replica queue wait, provider retries, response handling and durable usage recording.
  `usage_commit` measures the separate usage transaction, including rollback failure.
  Stream completion is counted after the provider's completed event and usage commit;
  early iterator closure / cancellation is `cancelled`. Classified upstream or local
  timeouts are `timeout`; public validation/admission denial is `rejected`; other failures
  are `error`. This does not claim provider quality or real credential integration.
- `ingest`: `queued`, `reconcile`, `register`, `plan`, `publish` are actual durable activity
  attempts. `advanced` means that step committed; `succeeded`, `review_needed`, `failed`
  and `superseded` reflect the committed task result. Repeated terminal activity calls
  are `skipped`. `retry_exhausted` measures the final failure checkpoint; a rollback
  reports an error and cannot increase the committed `failed` counter. `dispatch`
  covers Temporal start plus the local dispatch checkpoint; no eligible task is `idle`.
- `projection`: `lease`, `project`, `ack`, `delivery`, `rebuild`, `reconcile`. `projected`
  means the local projection catalog transaction committed after the external write;
  it does not prove delivery ACK. `already_projected` and `older_policy_skipped` remain
  explicit. `delivery=success` requires project completion plus `acked:true` from
  Knowledge. An accepted external write followed by timeout is not counted as delivery
  success. An HTTP ACK response without that boolean is `unconfirmed`, preserving the
  existing retry behavior. Failed reconciliation that durably saves its retry backoff
  is `reconcile=deferred`.
- `retrieval`: overall `search`, `search_local`, `scope`, `authorization`, `es_hybrid`,
  `es_ids`, `fusion`, `graph`, `assemble`, `embedding`, `rerank`. `search_local` is elapsed
  request time minus sequential Embedding and Reranker HTTP waits (including their
  retries/configuration done inside those calls). It still includes local processing,
  authorization, metadata requests, ES and graph HTTP waits. These measurements are
  not additive because some scopes overlap and BM25/vector recall runs concurrently.
  The local p95 SLO query must select successful `search_local`, not total HTTP duration;
  admission/error counts must still be reported alongside performance results.
- `graph`: `traverse` covers authorization, adjacency traversal and final proof checks;
  `walk` covers bounded traversal. A safe degraded result is `degraded` on `traverse`.

## Queue truth, age and staleness

`knowledge_queue_known{service,queue}` is 1 only after a successful valid read and for at
most 45 seconds after that read. The sampler runs every 15 seconds with a 5 second
read deadline. Failed, timed-out or malformed batches mark every requested queue unknown.
`knowledge_queue_size` is NaN when unknown or stale, **never an invented zero**.
`knowledge_queue_observed_age_seconds` continues increasing since the last successful
snapshot, even when unavailable; it is NaN before the first success. Thus an alive HTTP
listener with a stalled sampler still loses `known=1`. Scraping performs no database I/O.

`knowledge_queue_oldest_age_seconds` is the age of an authoritative oldest timestamp.
An observed empty queue has age 0. An observed nonempty count with no usable timestamp
has NaN age independently of its known count. The Grafana dashboard displays missing
values as unknown and uses `max by(service,queue)` for duplicate replica snapshots,
not a sum that would multiply a shared database's count.

- Ingest worker: `tasks_queued`, `tasks_running`, `tasks_failed`, `tasks_review_needed`
  count the own `ingest_tasks` rows in those statuses. Age is since **task creation**, not
  time in the current stage or first failure. These include durable tasks even if
  Temporal is unavailable; they are not Temporal server queue-depth estimates.
- Retrieval/Graphiti worker: `reconcile_due` counts own catalog rows whose next regular
  reconciliation is due; age is seconds overdue. `failed_revisions` counts own catalog
  rows with recorded failed attempts. Its first-failure age is unavailable and remains
  NaN for nonempty results: the catalog's `checked_at` may be a future backoff deadline.
- Both consumers explicitly export `outbox_total` with `known=0`. An empty lease response
  cannot prove that no pending or leased-out events remain. A projection catalog is not
  the authority for the canonical outbox and no cross-database reads are made.

`knowledge_projection_delivery_age_seconds{service}` measures only observed
`Outbox.created_at → confirmed ACK` deliveries. Missing, invalid or future creation
instants are counted as unknown in
`knowledge_projection_delivery_age_observations_total{service,state}` and are not
converted to age zero. This latency histogram is **not** the age or size of the backlog.

Useful alert expressions include `knowledge_queue_known == 0`,
`knowledge_queue_observed_age_seconds > 45`, and `up{job="knowledge-services"} == 0`.
The consumer `outbox_total` is intentionally unknown until the Knowledge-owned sampler
below is implemented; do not page on this documented placeholder as a new fault.

## Knowledge-owned outbox sampler integration contract (pending)

Knowledge must query only its own `Outbox` / `Delivery` tables. The public helper is:

```python
metrics = app.state.telemetry.domain
sampler = SnapshotSampler(metrics, load, ("outbox_retrieval", "outbox_graphiti"))
# load is async, owns a read transaction, returns exactly:
# {"outbox_retrieval": QueueSnapshot(count, oldest_created_at_or_none),
#  "outbox_graphiti": QueueSnapshot(count, oldest_created_at_or_none)}
# Start asyncio.create_task(sampler.run()) in the Knowledge lifespan;
# cancel and await it before closing the database.
```

The loader must count all committed, unacknowledged deliveries for each consumer,
including actively leased rows and retries. Join each delivery to the original outbox
creation timestamp for the oldest age. If a delivery row is not materialized until lease,
include every committed eligible event with no delivery or no ACK; do not count only
existing lease rows. Read count and oldest in one consistent aggregate snapshot. Do not
expose IDs or `last_error` text. A failed database read should raise to the sampler so
both queues become unknown. No schema changes or arbitrary model labels are needed.
The root owner will add this sampler separately; this increment does not claim total
canonical backlog coverage or change the knowledge maintenance worker.

## Verification and limitations

The source regression uses isolated random PostgreSQL schemas and synthetic protocol
inputs. It verifies actual model usage commit/rollback, streaming completion and real TCP
cancellation, committed task states, projection write failure, accepted-write/ACK timeout
and invalid ACK, idempotent retry, read-committed queue snapshots, sampler DB failure,
unknown/stale behavior, safe spans, and local retrieval time excluding both model waits.
Full affected common/LLM/retrieval/Graphiti and ingest pipeline/fence/rollout/Temporal
unit suites: **297 passed, 0 skipped** in 30.68 seconds. The only warning is the existing
Graphiti SDK Pydantic class-config deprecation. Logs and final source hashes are handed
to the root owner for independent review before deployment.

Still outside this increment: canonical backlog sampler above; LLM token billing
aggregation (durable per-invocation usage remains authoritative); worker initialization
failure-specific counters; Temporal server backlog estimates; real workload SLO/quality
and production alert thresholds. No application/worker was restarted for these tests.