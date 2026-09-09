# MCP verification

Implementation was driven by failing tests before the service existed and
additional red/green boundary probes (projection metadata/epoch, error semantics,
strict original-source line types). Tests live in `services/python/tests/mcp`.

Run from the worktree:

```
services/python/.venv/bin/python -m pytest -q services/python/tests/mcp
services/python/.venv/bin/ruff check services/python/src/knowledge_platform/mcp services/python/tests/mcp
```

The suite starts real loopback Uvicorn servers and uses actual official MCP2.2
clients, both legacy handshake and modern automatic protocol selection. It covers
six tool routes, strict JSON arguments, namespace/position/time evidence binding,
OAuth metadata/challenges, dual audiences/issuer/expiry/preregistered client,
feedback scope, signed gateway/mcp workload boundaries, user isolation/token
refresh/revocation, authorization outage and epoch change, Origin/versions/Accept,
request/response budgets, protocol versus tool errors, and both legacy cancellation
notifications and modern response-stream cancellation reaching in-flight work.
No database is needed because MCP has no domain-owned state.

Domain and auth responses in this suite use explicit HTTP protocol simulators,
with real workload JWT signing/verification. These tests do **not** claim deployed
Keycloak/Elasticsearch/Graphiti/Agent/model integration. Parent-owned Docker
regression and its separately recorded real PKCE flow verify those boundaries.
Real model or WeCom acceptance is never inferred from MCP protocol tests.

No MCP event replay cache is configured or advertised. Legacy sessions are bounded
process memory metadata and require affinity; reconnect/restart initializes a new
session and reads the domain again. Agent event-cursor recovery belongs to Agent's
authorized API. Explicit Last-Event-ID rejection is tested; there is no claim of
resumable cached MCP answers or an enterprise multi-replica/load/restore acceptance.
