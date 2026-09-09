# Agent API v1

API process: `uvicorn knowledge_platform.agent.app:app --host 0.0.0.0 --port 8080 --no-access-log`.
Independent worker: `python -m knowledge_platform.agent.worker`.
They share only the Agent-owned PostgreSQL database, encryption key and Agent workload identity. The API does not start an in-process background worker. No Agent code reads another service's tables or publishes canonical knowledge directly.

## Authentication and current channel boundary

Every non-health request needs an Ed25519 `X-Service-Token` targeted at `agent` and the unchanged delegated OAuth bearer. Public `/api/v1` accepts gateway or mcp. `/internal/v1` accepts mcp only. The gateway adds its workload identity; browsers send only their OAuth bearer to the gateway. JSON principals, roles, user IDs and external identity assertions are not accepted.

User identity, account state, groups, departments and scopes are resolved through current auth on every operation. `platform_admin` does not grant knowledge read. Ordinary operations need `knowledge:read`; Agent create/update/publish need `knowledge:write` as well as the resource actions below. Feedback requires the **independent `knowledge:feedback` scope**, not merely `knowledge:write`.

**Channel is deliberately not enabled yet.** A channel workload currently receives 403 even with an OAuth bearer. The planned auth-broker delegation must bind every request to a currently active external binding, channel and conversation/audience, with separate session identity. Accepting a JSON principal or sharing Web/private/group sessions would bypass that requirement. This is an explicitly pending integration, not claimed completed channel support.

## Agent configuration

- `GET /api/v1/agents/presets` returns `{items:[{id,name,instruction,tools,model_configuration_id:null}]}` for `knowledge_qa`, `dependency_impact`, `incident_history`, `maintenance`. Presets do not select a model or create global shared knowledge grants.
- `GET /api/v1/agents/models?cursor=&limit=50` exposes safe active Chat choices from the LLM workload discovery contract: model ID/name, immutable configuration ID, provider model name, capability, state and test capabilities. No provider URL, credential or credential-presence flag is returned. This selection does not require LLM administration privilege.
- `POST /api/v1/agents` creates a private definition and immutable configuration, HTTP 201.
- `GET /api/v1/agents?cursor=&limit=50` returns `{items,next_cursor}`. Cursors contain only a visible Agent ID, with no unfiltered count. Ordinary users see their private definitions and readable published definitions; grant administrators can inspect private drafts in spaces they also read.
- `GET /api/v1/agents/{id}?configuration_id=` returns the selected immutable version. Owners and owner-space grant administrators can inspect drafts and old versions. Ordinary users see only the currently published version. Returned knowledge/tool scope arrays omit spaces the caller cannot currently read.
- `PUT /api/v1/agents/{id}` requires `{base_configuration_id,name,description?:string,config}` and creates a new immutable version under a row lock. Only the creating editor, with current owner-space read/write, edits the definition. Stale base is 409. A draft update does not silently move the published version.
- `GET /api/v1/agents/{id}/versions?cursor=&limit=50` returns authorized immutable versions descending; cursor is the last version number. Owners and readable owner-space grant administrators can inspect versions.
- `POST /api/v1/agents/{id}/publish {base_configuration_id,shared:true|false}` requires current owner-space **read + grant**. It atomically compares the current version, changes the published pointer and audits. Grant administrators can inspect the exact draft before publishing it. Sharing grants no knowledge access.
- `GET /api/v1/agents/{id}/audit?limit=50` returns safe configuration/publication actions to the owner or a currently authorized owner-space grant administrator. No prompts or credentials occur in audit records.

Create example (replace IDs with actual deployment IDs):

```json
{
  "name": "研发知识问答",
  "description": "依据原始源码与知识修订回答",
  "owner_space_id": "engineering",
  "config": {
    "mode": "knowledge_qa",
    "model_configuration_id": "EXPLICIT_CHAT_CONFIGURATION_ID",
    "prompt": "回答时指出证据缺口。",
    "space_ids": ["engineering"],
    "tools": ["search", "get"],
    "tool_space_ids": {"search": ["engineering"]},
    "budget": {"model_rounds": 8, "tool_calls": 20, "parallel_reads": 2, "seconds": 180},
    "max_output_tokens": 2048
  }
}
```

`model_configuration_id` may be null while configuring a draft, but a run then fails with 409 `model_configuration_required`. No model-name fallback. Mode selects task guidance and default tools when `tools` is omitted; explicit empty tools is allowed for an evidence-gap-only response. Tool scopes must narrow configured spaces and refer only to enabled tools. At most 100 unique spaces, six tool names, an 8,000-character configured prompt, and 8,192 output tokens. Budgets can be reduced but cannot exceed 8 model decisions, 20 tool calls, 2 simultaneous read calls and 180 seconds. A provider/model's smaller token or context limits still apply.

Creating/editing also requires current read on every configured knowledge space. Owner-space administration and knowledge scope are independent. Grant administrators do not gain content from publishing or from inspecting configuration metadata.

## Sessions, runs, retry and cancellation

- `POST /api/v1/sessions {title?:string}` → 201 `{id,title,created_at}`; title defaults to `新会话`. Titles are supplied by the user, never generated from private answer content. Sessions belong only to the currently resolved principal.
- `GET /api/v1/sessions?cursor=&limit=50` returns owned, uncleared sessions. Current list/history keysets use opaque IDs; consumers can order returned records by `created_at` for display.
- `DELETE /api/v1/sessions/{id}` marks the private conversation cleared, atomically cancels its active run and erases its stored delegation. Cleared history is no longer accessible. This is logical clearing, not a claim that backups or immutable records have been physically erased.
- `POST /api/v1/runs` → 201 a `RunView`:

```json
{
  "agent_id": "AGENT_ID",
  "session_id": "SESSION_ID",
  "configuration_id": null,
  "question": "Payment 的依赖变更会影响哪些模块？",
  "space_ids": ["engineering"],
  "idempotency_key": "UNIQUE_LOGICAL_REQUEST_KEY",
  "as_of": null,
  "known_at": null
}
```

Question is 1–8,192 nonblank characters. Keys are bounded URL-safe identifiers; idempotency keys are principal-scoped. Repeating the exact logical request returns the original run after fresh authorization; reusing a key with changed input is 409. Only one queued/running run may exist per session; PostgreSQL enforces this independently of API races.

The configuration snapshot is frozen in the run. Owners/grant administrators can explicitly test an accessible immutable draft/version; ordinary callers use the currently published version. The actual initial scope is **current user read ∩ Agent scope ∩ requested scope**. Empty intersection is 403. Every tool narrows this again by its configured tool scope and current action/scope permissions. The model receives only the actual current tool scopes, not inaccessible configured spaces. It cannot widen scope by changing tool arguments. Scope changes during a composite authorization operation fail closed; subsequent decisions re-resolve current membership. A revoked scope/dependency stops use of existing context rather than silently retaining it.

- `GET /api/v1/runs/{id}` returns a fresh `RunView`.
- `POST /api/v1/runs/{id}/cancel {}` atomically changes an active run to cancelled, revokes its lease and erases the encrypted bearer. Heartbeat notices cancellation and cancels/awaits any model/tool HTTP tasks. Repeated cancel is safe.
- `POST /api/v1/runs/{id}/retry {idempotency_key}` creates a **new** run, retaining the original question, requested scope and configuration ID. The caller must still be entitled to select that configuration and all original dependencies; a no-longer-published inaccessible version cannot be used as a bypass. The prior run is not modified or resumed by this endpoint.
- `GET /api/v1/sessions/{id}/history?cursor=&limit=50` reauthorizes every returned run. It does not trust stored answer text or browser caches.
- `GET /api/v1/runs/{id}/export` produces authorized Markdown with login-protected citation URLs. No public raw source links are minted.

A readable `RunView` includes:

```json
{
  "id": "RUN_ID", "session_id": "SESSION_ID", "status": "completed",
  "event_seq": 8, "created_at": "2026-09-08T00:00:00+00:00", "finished_at": null,
  "content_hidden": false, "agent_id": "AGENT_ID", "configuration_id": "AGENT_CONFIGURATION_ID",
  "actual_scope": ["engineering"], "question": "...",
  "answer": {
    "facts": [{"text": "...", "citation_ids": ["ORIGINAL_REFERENCE_HASH"]}],
    "inferences": [], "gaps": []
  },
  "citations": [{
    "id": "ORIGINAL_REFERENCE_HASH", "evidence": {}, "excerpt": "exact original text",
    "page_id": "page:payment", "revision_id": "PINNED_WIKI_REVISION", "space_id": "engineering",
    "url": "/citations/SOURCE_SNAPSHOT_ID?start=1&end=10"
  }],
  "usage": {"total_tokens": 123}, "error_code": null,
  "budget": {"model_rounds":8,"tool_calls":20,"parallel_reads":2,"seconds":180},
  "rounds": 2, "tool_calls": 1
}
```

Statuses are `queued|running|completed|partial|failed|cancelled`. `answer` is null before a validated outcome. References use the shared `common.evidence.EvidenceRef` contract; `evidence.revision_id` is the immutable original source snapshot, while the citation's outer `revision_id` pins its containing Wiki revision. Citation hashes are deterministic hashes of normalized complete original references, not model-generated IDs. Unknown model citations fail the run. Semantic support still requires evaluation; passing ID validation is not a claim that an arbitrary sentence is entailed.

If **any** original dependency is no longer authorized, the entire derived run is redacted: only `{id,session_id,status,event_seq,created_at,finished_at,content_hidden:true}` remains. No question, answer, summary, tool output, citation IDs or excerpt is returned. Auth outages return 503 rather than a partially successful content response. Each history/model-context authorization checks both exact canonical Wiki revisions (including inherited access dependencies) and displayed original sources. Dropping a citation does not declassify an answer: dependencies include **all consulted model/tool/history inputs**, not just its final citations.

## Durable events and current answer-streaming limitation

`GET /api/v1/runs/{id}/events?cursor=0` also accepts `Last-Event-ID: N` (takes precedence). Event sequence starts at 1 and is committed transactionally with run transitions. Reconnect uses the sequence cursor. There are no volatile replay buffers or hidden model-reasoning events.

```text
id: 4
event: generation
data: {"seq":4,"type":"generation","data":{"round":1,"status":"started"}}

id: 8
event: completed
data: {"seq":8,"type":"completed","data":{"status":"completed","error_code":null,"run":{}}}
```

Events include `queued`, `started` (with resumed flag), `context_ready`, `generation`, `tool`, `completed`. A terminal event's run is **materialized with fresh authorization**, not trusted from the stored event. Authorization is checked before each yielded frame, including after a slow reader resumes. Cursor-ahead is 409, malformed cursor 422. An authorization/dependency failure after headers yields a safe terminal error when possible; never a stale answer. A slow send is bounded; disconnect closes only the SSE iterator, not its persistent run. Explicit cancel owns stopping a run. A connection closes after its configured lifetime; reconnect with the last received sequence.

**This reviewed boundary version streams execution stages and consumes real LLM SSE, but publishes its final answer only after complete structured-output/citation validation. It does not yet incrementally publish answer text or answer blocks during model generation.** The approved Web/WeCom answer-streaming requirement therefore remains pending. The next separate increment must split tool decisions from a dedicated final-answer generation and emit only complete, validated, freshly authorized `answer_block` records. Unfinished JSON, provider reasoning and tool-planning content must not be forwarded as a workaround. Current clients must not claim token-level or block-level answer streaming.

## Internal tools and model boundaries

Tools are fixed in trusted source code, never dynamically imported from user configuration:

- `search {query,space_ids,relation?,limit?:1..12}` calls retrieval hybrid search with the run's pinned time constraints. All returned original references are independently authorized and exact excerpts rebuilt through knowledge; upstream citation IDs are not trusted.
- `get {space_id,page_id,revision_id?}` checks the named resource in its explicit tool scope before fetching a Wiki revision. An explicit old revision is labeled historical with its state, and inspected with current canonical provenance authorization. A stale historical text is not presented as a current fact.
- `traverse {space_ids,seed_fragment_ids,relation}` accepts only previously observed run fragment IDs. `relation` includes approved types, direction and 1–2 hops. Retrieval owns real bounded adjacency and graph/canonical validation. Graph outages remain explicit retrieval degradation, never evidence of absence.
- `timeline {space_ids,page_ids,limit?}` returns authorized revision metadata, marked historical inspection. It asks for exact `get` evidence before forming citable narrative claims, and does not infer production deployment from repository history.
- `propose_revision {space_id,page_id,base_revision,title,markdown,reason,citation_ids,kind?:formal_change|digest}` requires current target read/write, all current and new original dependencies, a still-current explicit base, and observed citation IDs. It sends only an idempotent canonical proposal; it cannot call approve/publish/validity/raw graph mutation. Generated narrative and exact source support are reviewed; it does not relabel model guesses as compiler facts. The whole target's inherited provenance is retained even if a proposal omits displayed sources.
- `feedback {rating,comment}` records a user-requested rating about this run under independent feedback scope. It is absent from presets by default and must be explicitly enabled. Public feedback is described below.

The model sees only enabled tools with currently available scope/action permissions. Arguments are parsed as duplicate-key-rejecting JSON objects and validated against strict local DTOs after provider output. Unknown fields/tools, out-of-scope IDs, invented graph seeds/citations, duplicate call IDs, and over-budget calls are refused before execution. Only validated tool calls and tool results are checkpointed; narrative content accompanying a tool decision is discarded. Final facts/inferences/gaps are strict JSON. Original source text is explicitly untrusted data; instructions inside it do not become application permissions or tool definitions. No execution, web search or external MCP client tool is available.

Read tools run in at most two concurrent HTTP tasks. A failing task cancels and awaits its siblings before ending the run; writes are serialized. Every actual model call is preceded by fresh full provenance authorization and followed by another check before committing an answer. Models are pinned by immutable LLM configuration ID, with current active state and provider context/output limits checked. The LLM request is workload-only; the Agent does not leak the user bearer into the model gateway or provider. The model gateway retains ownership of provider credentials and usage records.

## Feedback and original-evidence digests

`POST /api/v1/feedback {run_id,rating:"helpful"|"unhelpful"|"incorrect",comment?:string,idempotency_key}` → 201 `{id,run_id}`. Requires the run's owner, current complete content access and `knowledge:feedback`. Replays are principal-scoped and live-authorized; changed input is 409. It does not mutate canonical facts or graph weights.

`POST /api/v1/sessions/{id}/summaries {}` creates an **extractive, independently versioned digest of up to 12 authorized completed/partial answers**, preserving their original citations and run IDs. Identical source content is idempotent; new content produces a new immutable version. `GET` at the same path lists the most recent 50 digests after current full dependency authorization. Any missing dependency redacts the whole digest. `independent_evidence_count` is always 0. There is no confidence reinforcement, hotness-based truth boost, summary-as-source citation, or unreviewed canonical publication. The current runtime reuses up to six authorized prior answers directly as explicitly historical context rather than silently trusting a digest cache.

## MCP reuse

Exact aliases available to the **mcp workload only**, with the unchanged caller bearer:

- `POST /internal/v1/sessions`
- `POST /internal/v1/runs`
- `GET /internal/v1/runs/{id}`
- `GET /internal/v1/runs/{id}/events`
- `POST /internal/v1/runs/{id}/cancel`
- `POST /internal/v1/feedback`

They have the same DTOs, idempotency, owner restrictions, scopes, limits, revocation behavior and error contract as the public operations. MCP should explicitly create a session or supply an existing session ID; silently creating a new session on each ask retry produces avoidable orphan conversations. Cancelling an HTTP read does not cancel a persistent run. MCP must use the cancel operation for that intent and must not reuse a cached answer after revocation.

## Recovery, deadlines, confidentiality and operation

The worker claims an active run with `FOR UPDATE SKIP LOCKED`, increments durable event order, and installs a random lease fencing token. Heartbeat/save verify the current token and unexpired lease under a row lock. Concurrent claims cannot execute a run twice; an old worker cannot overwrite a new worker's result. API cancellation revokes the token before upstream cancellation is observed.

A run stores its delegated bearer **encrypted under the separate `AGENT_ENCRYPTION_KEY`, AES-GCM-bound to that run ID**, only for its bounded execution lifetime. Auth first verifies the exact token. Its unverified JWT `exp` is subsequently used only to shorten the 180-second maximum, never to authorize an identity. The absolute deadline includes queue wait and every model/tool round. No refresh token is stored, no machine-user substitution is allowed, and missing/expired/undecryptable delegation fails closed. Terminal completion/cancel/failure erases the encrypted token in the same transaction. A dead worker's encrypted pending delegation is erased when workers recover/claim expired tasks; operators must retain worker service monitoring rather than assume a powered-off process can perform physical deletion. Encryption keys must be backed up separately with the Agent DB; replacing the key alone intentionally makes pending runs fail closed.

Restart at a durable validated tool checkpoint resumes those calls with the same IDs and counters. Read calls may safely repeat. Proposal and feedback calls use stable per-run/per-call idempotency keys; no direct publication is possible. A provider model request recorded as in-flight has an unknown processing/cost outcome after process loss: recovery returns `partial / model_interrupted` with already-authorized original quotations and asks for a new explicit retry. It **does not blindly repeat uncertain inference** or claim a lost response was successful. Likewise a failed proposal response is not advertised as a completed publication. Existing canonical pending proposals remain visible in their review workflow.

Budget/deadline exhaustion returns only already-authorized original excerpts and a precise incomplete-analysis gap where authorization still permits; an authorization failure yields no partial private content. Provider transport cancellation, worker shutdown and explicit run cancellation close the actual upstream stream. Necessary authorization/audit cleanup has separate bounded 5-second phases; it may extend wall-clock cleanup beyond the inference deadline. Intermediate messages are cleared on terminal transition; immutable events/audit contain only safe state metadata. No bearer, prompts, provider error body, source excerpt or hidden reasoning is logged. PostgreSQL parameters are hidden. API errors are `{error:{code,message}}` without raw input echo.

Settings supplement common service URLs, database URL, Agent service keys, request timeout and opt-in private telemetry:

- `AGENT_ENCRYPTION_KEY`: required private base64 32-byte key; no default or shared LLM/ingest key.
- `AGENT_WORKER_CONCURRENCY=10`: 1–20 independent run slots per worker process. Per-run budgets remain 8/20/2/180 maximum regardless of replicas.
- `AGENT_POLL_SECONDS=1`, `AGENT_EVENT_POLL_SECONDS=0.5`.
- `AGENT_LEASE_SECONDS=15`, `AGENT_HEARTBEAT_SECONDS=2`; heartbeat must be less than half the lease duration.
- `AGENT_MAX_REQUEST_BYTES=2000000`: enforced over chunked bodies before JSON parsing, with the common request-timeout bound.
- `AGENT_MAX_RESPONSE_BYTES=4000000`: internal JSON and LLM SSE response cap.
- `AGENT_MAX_CONTEXT_CHARS=100000`: explicit aggregate context budget, additionally narrowed by pinned LLM metadata.
- `AGENT_SSE_SEND_TIMEOUT_SECONDS=2`, `AGENT_SSE_LIFETIME_SECONDS=190`.
- Common `METRICS_PORT`, `OTEL_EXPORTER_OTLP_ENDPOINT`: API safe route/status/duration telemetry, no content/body/credential capture.

`/healthz` is process liveness; `/readyz` checks the owned database. Worker deployments have no HTTP health server. All deployment files and Docker regression commands live under `knowledge-docker/`. Real Qwen credentials, deployed retrieval/Graphiti end-to-end acceptance, channel identity/audience integration and incremental answer-block generation are separate remaining acceptance work; local protocol fixtures are not live provider validation.