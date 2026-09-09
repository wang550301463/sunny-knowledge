# LLM verification — 2026-09-08

Implemented only `services/python/src/knowledge_platform/llm/` and `services/python/tests/llm/`; no shared dependencies, Compose, common utilities, contracts or other services changed by this task. No commits created.

## Executed evidence

From repository root with the pinned Python 3.12 environment:

```sh
services/python/.venv/bin/ruff check services/python/src/knowledge_platform/llm services/python/tests/llm
services/python/.venv/bin/ruff format --check services/python/src/knowledge_platform/llm services/python/tests/llm
services/python/.venv/bin/python -m pytest -q services/python/tests/llm services/python/tests/common
```

Results: lint clean; 12 Python files already formatted; **55 passed in 14.08s** (35 LLM tests plus 20 common tests). LLM tests alone previously passed **35 in 3.41s**. No skipped PostgreSQL or HTTP boundary tests in this run.

PostgreSQL tests used the real llm database DSN privately from ignored `knowledge-docker/.local/test-env.json` (`databases.llm`) or `TEST_LLM_DATABASE_URL`. Each test created a unique `test_llm_<random>` schema and dropped only that schema after disposing its connections; no cross-service SQL access and no shared tables cleared. DSNs and real credentials were not printed.

## Behavior covered

- Real Ed25519 service signatures, audience/caller allowlists; gateway cannot invoke internal chat/embedding/rerank. Every public management read/write requires live auth resolve response, platform_admin plus the appropriate knowledge scope. Missing Bearer, malformed resolve/scopes, IAM/auth failure, forged service token and content-admin assumptions all fail closed.
- Model create/update/state retirement, explicit immutable configuration UUID, old version invocation remains pinned, retired/disabled state blocks new calls (including queued calls), list/discovery/history/audit/usage routes, and safe fields.
- Real PostgreSQL concurrent CAS yields one update and one conflict. Config versions are append-only with database mutation rejection. Credential ciphertext is owner-bound AES-GCM, omitted credential preserved, explicit rotation effective, plaintext absent from public records and audit details.
- Real HTTPX adapters verify actual outgoing chat/embedding/rerank payloads against explicit provider contracts. Vector ordering/count/dimension/finiteness; rerank count/index uniqueness/range/numeric scores; malformed container/value types and chat tool-call JSON rejected without provider error reflection. Reasoning fields excluded from normalized events/results.
- 429/5xx bounded retries, no 401 or network/timeout retry, total queue/provider deadline, bounded Retry-After, no redirect credential forwarding, response byte caps, model-specific request/batch caps and raw ASGI body cap.
- Stream deltas, final completion, validated complete tool calls, final usage, interrupted stream errors, no retry after consuming a stream, connection close on provider timeout.
- Per-model per-replica bounded queue/concurrency, independent models, cancelled waiters/holders, idle limiter entry cleanup, and disable while a request is queued.
- Native asyncio cancellation over a real TCP provider connection closes the socket and leaves a durable cancelled outcome. A real Uvicorn/HTTPX downstream client disconnect test includes asynchronous upstream cleanup; both transport close and usage writes complete under ASGI/AnyIO cancellation. This test originally exposed and drove the cancellation shielding fix.
- Durable started invocation and append-only usage outcome; safe provider request IDs on successes and rejected calls; usage/duration/outcomes; bounded metadata acquisition; no provider call after metadata deadline.
- Common telemetry exports route templates instead of model IDs, with no prompt/key values.

## Acceptance boundary

Provider responses in these tests came from local HTTP protocol simulators (HTTPX MockTransport and loopback TCP), not external model credentials. **No real external Chat, Embedding or Reranker acceptance is claimed.** The real-model acceptance step remains: administrator privately supplies supported provider endpoint, explicit model ID, capability/dimensions and credential; run capability probes and representative inference/streaming requests, then record real provider evidence separately.

Concurrency is deliberately and explicitly **per replica/process**. N replicas/workers can multiply configured capacity by N. There is no PostgreSQL/shared global limiter or cluster-wide concurrency claim. Model inference deadline covers queue and provider attempts; metadata and mandatory cleanup have separately bounded phases documented in API.md. A process crash can leave a started invocation with unknown outcome; the service never invents a success/failure or blindly repeats the provider call.

Operational deployment must provide `CREDENTIAL_ENCRYPTION_KEY`, independent llm database identity, service signing identity, live auth endpoint and optional common telemetry configuration. Provider compatibility is explicit; no guessed provider endpoints, hidden model defaults, synthetic answers, credential fallbacks or SDK schema assumptions.