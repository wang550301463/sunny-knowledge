# Gateway admission control and Go operational telemetry

This implementation covers gateway/auth/IAM/channel HTTP, channel message roots,
service HTTP clients (including Keycloak discovery/JWKS), and gateway proxy/SSE
requests. It does not change authorization or grant management privileges.

## Shared gateway admission

The runnable gateway requires `VALKEY_URL`. Invalid/missing configuration fails
startup with a constant error. Valkey can be unavailable at startup: the gateway
still serves static files and `/healthz`, while dynamic admission fails with 503
and `/readyz` reports 503. Ready checks Valkey directly; health is process liveness.
No local permissive fallback or cached authorization is used.

Before any authentication request, the gateway checks the actual TCP peer IP,
normalizing IPv4-mapped IPv6 and ignoring caller-supplied Forwarded/XFF/identity
headers. Separate API, MCP and IDP peer buckets protect these entrypoints; MCP's
OAuth metadata shares its MCP peer bucket. REST additionally consumes a principal
bucket after the existing live auth Resolve returns a nonempty authoritative ID.
Token or peer rotation does not reset that principal quota. MCP continues to own
its dedicated audience/client/scope OAuth checks and challenge response; gateway
admission for MCP is deliberately per peer, with no invented user identity.

Defaults, independently configurable on every gateway replica:

- `GATEWAY_PEER_LIMIT=600`: API peer and MCP peer requests per window.
- `GATEWAY_IDP_LIMIT=120`: IDP peer requests per window.
- `GATEWAY_PRINCIPAL_LIMIT=300`: authenticated REST requests per principal/window.
- `GATEWAY_LIMIT_WINDOW=1m`: fixed window duration for all three limits.

A window starts with its first admitted request; an atomic Valkey Lua operation
checks/increments and installs its TTL. Rejections do not extend that TTL.
Fixed windows allow a boundary burst approaching twice the quota across adjacent
windows; these defaults are not a benchmark result. Replicas must use identical
policy settings. Future load reports must include 429/503 rejections, rather than
silently exclude them from latency or success rates. Default limits should be
tuned using that evidence.

Keys use a fixed service namespace plus SHA-256 of the bucket identity: raw IPs,
principal IDs and Bearer tokens are absent from stored keys. Only bounded counters
are stored; keys expire automatically. There are no identity metric labels.
Valkey commands have a 250 ms runtime deadline, bounded connection/write timeouts
and disabled command retries. A lost response may consume an allowance; it is
never interpreted as permission to proceed or blindly retried. 429 responses
include a rounded-up, positive `Retry-After` in seconds and `Cache-Control:no-store`.
Dynamic requests fail with `rate_limit_unavailable`/503 on admission failure, before
business dispatch. Streaming/MCP requests consume allowance when opening the
request, not per frame; an already admitted stream is not cut off by later quota
exhaustion. Health/readiness and frontend assets do not consume dynamic quotas.

`gateway.NewHandler` accepts an injected Limiter for tests/embedding. Nil skips
admission in that embedded constructor; the runnable `cmd/gateway` always constructs
and injects Valkey plus validated limits, and never deploys that test shortcut.

## Safe Go traces and metrics

`platform.Serve` initializes each API process from `SERVICE_NAME`,
`METRICS_PORT` (0/unset disables the listener) and
`OTEL_EXPORTER_OTLP_ENDPOINT` (unset disables exporting). The channel worker
initializes separately with `service.name=channel-worker`, and begins one root
`channel.message` span per handled authenticated callback. No message/user/bot/run
ID or content is attached. Existing leases, authorization, send guards, error
handling and Agent dispatch semantics are unchanged.

Custom server/client/proxy instrumentation avoids automatic URL/header capture.
Allowed attributes are fixed method, coarse route template, response status and
an allowlisted peer service. Routes retain a fixed domain such as
`/api/v1/pages/{path...}`; unknown routes use `unmatched`. They never contain a
resource ID or query. No body, credentials, request ID, baggage, arbitrary
tracestate, user-supplied identity or exception text is collected. W3C traceparent
is parsed/injected by the OTel propagator; server and client spans form the actual
HTTP parent chain. Invalid trace headers create a fresh trace without failing the
business request. Tracestate and baggage are not forwarded by the traced clients.

Server duration includes full response completion. Client spans end only on body