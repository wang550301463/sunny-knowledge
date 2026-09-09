# MCP service v1

Run `uvicorn knowledge_platform.mcp.app:app --host 0.0.0.0 --port 8080 --no-access-log`.
The service owns no knowledge tables, model credentials, OAuth refresh tokens, or
answer cache. Domain services remain the only authorization and mutation owners.
All deployment artifacts belong in `knowledge-docker/`.

## Transport and OAuth

The public endpoint is **`/mcp`**, behind gateway. The locked official **mcp 2.2.0**
SDK owns JSON-RPC, version negotiation, Content-Type/Accept validation, Streamable
HTTP and cancellation. It supports the 2025-11-25 initialize handshake and the
2026-07-28 per-request protocol/discovery; both are exercised by the official
Python client over real TCP. No stdio, legacy HTTP+SSE endpoint, remote tool
installation, arbitrary HTTP proxy, graph writes or shell tools are exposed.

The gateway must strip external identity/workload headers, sign its own
`X-Service-Token` for mcp and pass the original Bearer unchanged. `/mcp`
authentication is delegated to this service so that OAuth HTTP status and
`WWW-Authenticate` challenges reach the client intact. MCP rejects every workload
other than gateway; direct service-to-service tool use is not a bypass.

`GET /.well-known/oauth-protected-resource/mcp` and its root alias return the exact
resource URI, configured Keycloak issuer, header bearer method and minimal
`knowledge:read` scope. They contain only public configuration. An absent/invalid
Bearer receives HTTP401 with an absolute resource-metadata URL. Missing scope is
HTTP403 `insufficient_scope`; feedback challenges for `knowledge:read knowledge:feedback`.
Authentication service outages are HTTP503, never a negative cached permission.

Every authenticated HTTP request calls signed auth `/internal/v1/resolve`.
Its trusted response must include `principal`, `scopes`, `audiences`, `issuer`,
`client_id` and `expires_at`. MCP requires **both the exact MCP_RESOURCE_URL and
knowledge-api audiences**, the exact issuer, a currently valid expiry and a client
in the preregistered allowlist. Auth independently validates Keycloak signature,
issuer, API audience, token lifetime, current IAM principal and service-account
binding. MCP never trusts unverified JWT JSON or external identity headers.

Only preregistered Keycloak clients are supported. Register public native clients
with authorization-code flow, PKCE S256, explicit redirect URIs and no password
grant. The client must include `resource=MCP_RESOURCE_URL` in authorization and
token requests. The dedicated client receives the MCP+API audiences; ordinary Web
and worker tokens must not receive the MCP audience. Request feedback separately
when needed; it does not require or imply `knowledge:write`.

Origin, when present, must match an explicitly configured origin. SDK DNS-rebinding
checks allow only the public resource host and configured private gateway upstream
hosts. Duplicate security headers and bodies over 256 KiB are rejected. HTTP is
permitted only for public loopback development URLs; production public URLs use TLS.

## Sessions, cancellation and reconnect

The SDK's legacy session IDs are random and bound to **issuer + OAuth client +
subject**. Another principal's ID returns404. Token refresh for that same identity
is permitted, but each new request and every tool call rechecks current token,
scope and authorization. Sessions expire after 900 idle seconds by default and
are limited to 1,000 per process. Deploy stateful compatibility traffic with sticky
routing; a restart/other replica returns404 and the client initializes again.
The modern protocol is request-scoped and requires no session affinity.

Successful tool results use request-scoped SSE and structured JSON plus its text
representation. Legacy `notifications/cancelled` and modern HTTP response-stream
closure cancel in-flight read work through the SDK. Agent runs are durable domain
objects: stopping a transport request does **not** implicitly cancel a stored run;
call `ask(operation="cancel",run_id=...)` explicitly.

There is deliberately **no MCP content event store or answer replay**. No event IDs
or resumability promise are issued. `Last-Event-ID` receives400
`event_replay_unavailable`; reconnect by reinitializing if necessary and executing
a fresh read or `ask(operation="get")`. Agent's own API provides cursor-based
authorized event recovery. This avoids storing/replaying an answer that can become
unauthorized after it was generated. Headers use no-store. SDK argument/result
logging and its default per-message tracing middleware are disabled; common
bounded route/status telemetry remains available.

## Six tools

`tools/list` advertises complete JSON Schemas. Unknown fields, noninteger/boolean
numeric arguments, non-offset timestamps, unsupported operations and oversized
lists are rejected before domain execution. Tool errors use `isError:true` with
safe codes; unknown tools use JSON-RPC -32602, unknown methods use SDK -32601,
unexpected server failures use sanitized -32603. No dependency error body is
returned.

### search

`{query,space_ids,relation?,limit?:12,as_of?,known_at?,include_historical?:false}`.
Query is nonblank, at most 8,192 characters; 1–100 unique spaces; limit1–12.
Relation is `{types?:[depends_on,uses],direction?:outgoing,hops?:1}` with nine
approved types, incoming/outgoing/both and at most2 hops.
Calls retrieval `POST /internal/v1/search`. Models use the operator's pinned
retrieval configuration; MCP cannot invent an embedding configuration or broaden
scope. Returns the exact authorized evidence/graph response.

### get

- `{operation?:"page",page_id,revision_id?}` calls canonical current page or exact
  historical revision read. It returns that complete authorized canonical object.
- `{operation:"evidence",evidence:EvidenceRef}` reads the exact original excerpt
  through canonical `evidence/authorize`. All returned resource/source/snapshot/
  revision/path/line/time fields must equal the requested normalized EvidenceRef.

`EvidenceRef.revision_id` is a **source snapshot**, distinct from the containing
Wiki revision. No citation is converted to a public raw-file link. Large complete
pages may exceed the response budget; read a bounded original evidence slice or
use search instead of silently accepting truncated page content.

### traverse and timeline

`traverse`: `{space_ids,seed_fragment_ids,relation?,as_of?,known_at?,include_historical?}`,
1–30 unique seeds; calls retrieval `/internal/v1/traverse`, which performs actual
Graphiti adjacency and reattaches fully authorized original relationship evidence.

`timeline`: `{space_ids,page_ids,limit?:50,as_of?,known_at?,include_historical?:true}`,
1–20 unique pages, limit1–100. Historical mode cannot be disabled. Calls retrieval
`/internal/v1/timeline`, preserving state/time and its explicit truncation marker.
Neither tool infers a production deployment from a source commit.

Projection result fields, live epoch and every fragment/citation relationship are
checked before public output. Internal embeddings, ACL policy fingerprints or
unexpected result fields cause a safe failure, not a passthrough. Every domain
call forwards the unchanged Bearer with the mcp workload signature. After the
result arrives, MCP resolves the principal again and discards content if the
authorization epoch/identity changed. Domain services also perform their own full
current provenance and resource checks.

### ask

Use explicit operations so transport retries do not implicitly create sessions:

1. `{operation:"create_session",title?:"MCP conversation"}` → Agent session ID.
2. `{operation:"start",agent_id,session_id,question,space_ids,idempotency_key,configuration_id?,as_of?,known_at?}`
   → created/current AgentRun. `configuration_id`, if used, is an **Agent config**
   ID. Select/configure/publish Agents through Web/API first. Missing models are a
   domain error, never an implicit model selection.
3. `{operation:"get",run_id}` → fresh authorized run status and answer/citations.
   A queued/running status is not a finished answer. `content_hidden:true` means
   current permissions prohibit displaying its derived content.
4. `{operation:"cancel",run_id}` → explicit owned-run cancellation.

The adapter calls the corresponding Agent `/internal/v1/sessions` or `/runs`
routes; it never reads Agent tables or caches a completed answer. Preserve the
same start idempotency key when retrying an uncertain create. Session creation
itself is an explicit non-idempotent action: do not blindly repeat it after an
uncertain response. Agent owns current user∩config∩turn∩tool scope intersection,
configuration snapshots, execution budgets and owner/provenance checks.

### feedback

`{run_id,rating:helpful|unhelpful|incorrect,comment?:string<=2000,idempotency_key}`
calls Agent `/internal/v1/feedback`. Requires explicit user intent, current run
ownership/provenance and independent `knowledge:feedback`. It cannot publish a
revision, change a graph or increase factual confidence.

## Configuration

Common: `SERVICE_NAME=mcp`, private/public workload-key files, `AUTH_URL`,
`KNOWLEDGE_URL`, `RETRIEVAL_URL`, `AGENT_URL`, `REQUEST_TIMEOUT` (per dependency call),
private `METRICS_PORT` and optional OTLP endpoint. **No DATABASE_URL or service
database credential is required.**

- `MCP_RESOURCE_URL=http://localhost:18180/mcp` (exact external resource/audience).
- `MCP_ISSUER_URL=http://localhost:18180/idp/realms/knowledge` (exact public issuer).
- `MCP_API_AUDIENCE=knowledge-api`.
- `MCP_ALLOWED_CLIENT_IDS=["knowledge-mcp"]` (JSON list).
- `MCP_ALLOWED_ORIGINS=[]` (additional exact JSON-list origins; resource origin is
  automatically allowed).
- `MCP_INTERNAL_HOSTS=["mcp:8080"]` (JSON-list Host values after gateway rewrite).
- `MCP_MAX_SESSIONS=1000`, `MCP_SESSION_IDLE_SECONDS=900`.
- `MCP_MAX_RESPONSE_BYTES=2097152` (bounded dependency JSON; final SDK response has
  structured data and its text representation).
- `MCP_CALL_TIMEOUT_SECONDS=190` (maximum195; below gateway's210-second deadline).

Protocol references: [2025 Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
[2026 transport/cancellation](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports),
[OAuth resource binding](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization),
[tool results/errors](https://modelcontextprotocol.io/specification/2025-11-25/server/tools),
[official Python SDK](https://github.com/modelcontextprotocol/python-sdk).

## Official Python client example

Obtain an OAuth access token through the preregistered PKCE flow and keep it local.
This example uses the locked 2.2 SDK's `httpx2` transport. External clients provide
only their OAuth header; gateway supplies workload authentication internally.

```python
import os
import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

async def search():
    async with httpx2.AsyncClient(headers={
        "Authorization": "Bearer " + os.environ["KNOWLEDGE_ACCESS_TOKEN"]
    }) as http:
        async with Client(streamable_http_client(
            "http://localhost:18180/mcp", http_client=http
        ), cache=None) as client:
            return await client.call_tool("search", {
                "query": "Which modules declare this dependency?",
                "space_ids": [os.environ["KNOWLEDGE_SPACE_ID"]]
            })
```
