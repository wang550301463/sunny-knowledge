# Response coverage and pending client adoption

All 174 currently implemented backend HTTP operations are declared: 164 JSON,
6 bodyless successes, 3 Agent SSE streams and 1 Markdown export. The LLM chat JSON
operation also has an explicit SSE branch and discriminated event schema. Gateway's
100 entries mirror its actual routing and health surface. Mounted MCP JSON-RPC tools
remain a protocol extension, not fabricated REST routes. Machine-readable totals
and operation IDs are in `generated/coverage.json`; its untyped list is empty.

Runtime adoption completed earlier is limited to Python HTTPAuthorizer
resolve/authorize, Go platform.Client.Resolve, and the Web lifecycle metadata read.
The response declaration increments do not claim additional migration. Useful next
boundaries, each requiring its own transport/authorization regression, are:

- `retrieval/clients.py` DomainClient calls for page/evidence authorization,
  embeddings, reranking and graph traversal. Reuse generated operations while
  retaining safe_json size limits, epoch/evidence validation and cancellation.
- `graphiti/clients.py` DomainClient page/evidence checks and Knowledge outbox and
  projection reads. Keep current lease, ACL closure and time validation; generated
  field types cannot replace these checks.
- `ingest/clients.py` InternalClient request/advance_fence and canonical publication.
  Keep the source-generation headers, missing-source behavior, CAS/error mapping,
  idempotency and existing signing transport. The plain JSON convenience executor
  deliberately rejects prepared header-bearing requests.
- `agent/clients.py` Clients.call for knowledge, retrieval and model configuration.
  Preserve bounded streaming body reads, live authorization, deadline and upstream
  close behavior. Clients.chat needs a separate SSE migration retaining incremental
  UTF-8/frame parsing, final NDJSON validation and cancellation.
- `mcp/clients.py` DomainClient requests and `mcp/tools.py` internal operations.
  Preserve strict OAuth client/audience checks, response disclosure allowlists and
  protocol error handling. MCP sessions and methods continue to use the SDK.
- Go `channel/agent_client.go` broker/run operations and other IAM/Auth calls using
  platform.Client. Keep context-aware send fencing, fresh GET checks, read-only
  delegation, unknown-write handling and SSE cursor recovery.
- Web Agent, Wiki, ingest, model and channel API modules. Preserve their runtime
  DTO validators, abort/deadline behavior, focus-time withdrawal and hidden views;
  generated static types alone are not a security boundary.

No token persistence, generic client base-URL selection or automatic write retry is
introduced by generation. The next migration should be reviewed independently from
these native response declarations.
