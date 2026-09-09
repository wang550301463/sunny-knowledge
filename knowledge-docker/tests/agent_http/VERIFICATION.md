# Deployed Agent and MCP protocol-chain verification

2026-09-08, `codex/platform-v2`, isolated Docker project `sunny-knowledge-v2`.

The full HTTP test passed in **8.721 seconds**, after an initial red run against the
previous Agent image failed because no answer block arrived before model completion.
The Agent API and separate worker were then rebuilt and started healthy. Local
logs are `artifacts/agent-answer-stream-deployed-{red,green}.log` and the matching
build/start logs. The preceding non-streaming official-MCP chain passed in 8.533s.

The test uses real Keycloak PKCE, gateway, IAM, ingest/Temporal/S3, canonical review,
Agent API/worker, LLM service and official MCP SDK. It creates a Markdown source,
approves its revision, creates/tests an explicit Chat model and publishes an Agent.
The Agent's real `get` tool reads that exact immutable revision and original text.

The model fixture pauses after emitting the first complete NDJSON fact. The test
reads an `answer_block` through gateway SSE, verifies the provider has not completed,
and checks GET exposes the committed prefix with `status=running` and
`answer_complete=false`. Only then does it release the remaining provider output.
Final answer, exact CRLF source excerpt/revision, usage, event sequence and cursor
replay are checked. Feedback and Markdown export are authorized API operations;
retry creates a distinct run, and cancellation is persisted.

The official MCP client separately creates a session, starts/idempotently repeats
an Agent run, reads the complete answer and submits feedback with its own OAuth
scope. Source read permission is revoked while that MCP session is still open.
Subsequent MCP run reads hide content; Web history, summaries, events and export
also cannot reveal the question or original evidence.

`compose.agent-regression.yaml` enables an internal-only deterministic Chat
protocol fixture and allows HTTP model endpoints for this explicit test. No model
or Bot credential is used. This is not a quality evaluation, real Qwen acceptance,
MCP search/graph acceptance or a browser/WeCom test. Each run retires only its exact
model/configuration through the public API. Test knowledge remains for diagnosis.

After starting the app and fixture with the overlay, run:

```sh
knowledge-docker/scripts/compose.sh -f compose.observability.yaml \
  -f compose.agent-regression.yaml run --rm --no-deps regression \
  python -m unittest discover -s knowledge-docker/tests/agent_http -v
```

Restore the ordinary LLM service configuration and stop the protocol fixture after
all tests sharing that fixture have finished. Do not stop unrelated Compose assets.
