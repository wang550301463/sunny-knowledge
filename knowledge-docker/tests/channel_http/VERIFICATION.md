# Deployed WeCom protocol chain

2026-09-08, isolated `sunny-knowledge-v2` Docker project, branch `codex/platform-v2`.
The complete HTTP/WebSocket chain passed in **46.522 seconds**. The test uses
actual Keycloak PKCE, gateway, channel API and separate connection worker, auth,
IAM, Agent API and worker, LLM, ingest/Temporal/S3 and canonical knowledge services.
Only the Bot endpoint and Chat provider are explicit protocol simulators.

The first deployment exposed a mismatch between the initializer's URL-safe Base64
and channel's former standard-only decoder. The decoder now accepts either strict
encoding of the same 32-byte key; no local key was changed. The first full protocol
run then passed binding/streaming but returned 503 on the protected Web result:
the running knowledge image lacked the new `read_run_id` constraint, while Agent
already supported it. Its fail-closed behavior revealed the missing deployment.
Rebuilding/restarting knowledge resolved that version mismatch; the next complete
chain passed. This is why shared authorization contract changes must be included
in every consuming service image, not only the caller.

Verified behavior:

- Two real Markdown sources in different spaces, immutable S3 snapshots, review
  and publication; explicit tested Chat configuration and published Agent.
- Connection testing before enable; configured Bot credentials never appear in
  config responses. The fixture only accepts its fixed synthetic Bot secret.
- Unbound private contact receives a login link; actual Web login claims its
  challenge, and the same external private chat supplies the second proof.
- The model fixture pauses after its first complete fact. An actual non-final
  WeCom response contains that fact while the provider is still blocked. Releasing
  it produces the cumulative final answer and protected Web result/citation links.
- The Web result reads the exact original source through the run-bound endpoint.
  Channel sessions are absent from the ordinary Web session listing.
- An actual network disconnect reconnects with a new lease. Repeated message IDs
  produce no new reply/run; the completed result remains readable after replacement
  of its original connection lease.
- A registered group receives only its selected public space, with a different
  session from the private conversation. Attempting the private source in that
  group exposes no private source text.
- Tightening the public source to actor-only ACL hides the group answer, export,
  events and run-bound citation. Clearing a private conversation hides its old
  result and subsequent questions use a new session. Unbinding hides subsequent
  historical results and returns the external contact to the binding flow.
- Cleanup disables only the test's exact Bot/group and retires the exact immutable
  model configuration after verifying its provider/endpoint. Immutable test sources
  and audit records remain for diagnosis; unrelated assets are unchanged.

Local logs: `artifacts/channel-integration-{build,start}.log`,
`channel-deployment-key-{red,green,build}.log`,
`channel-http-old-canonical-red.log`, `channel-canonical-contract-{build,start}.log`,
and `channel-http-protocol.log`. The fixture's own eight actual TCP/WebSocket tests
also pass; they are documented in `tests/wecom_provider/README.md`.

An independent read-through subsequently identified a missing comparison assertion:
the group ACL test must also read the **same** original public source with the
ordinary Web token. The test now includes that comparison, confirms no AgentRun
was assigned to the unbound message, and rechecks duplicate reply/run identity at
the end. These additions await the next complete run; the 46.522s result above
describes the earlier assertions precisely.

Start the ordinary services and the existing model fixture. Then start the WeCom
fixture and its worker using the documented test overlay/profile commands. Run:

```sh
knowledge-docker/scripts/compose.sh -f compose.observability.yaml \
  -f compose.agent-regression.yaml -f compose.channel-regression.yaml \
  --profile test run --rm --no-deps regression \
  python -m unittest discover -s knowledge-docker/tests/channel_http -v
```

`CHANNEL_BROWSER_GATE=true` is an additional opt-in browser handoff. Mount only
`.local/web-channel-browser` writable at the matching regression path. The test
writes synthetic result IDs/marker and a unique `fixture_id` to `fixture.json`,
then waits at most 120 seconds for `release` containing that exact ID. The browser
case can inspect an authorized group answer before the HTTP test continues its
actual source revocation. It never exchanges or persists tokens or binding proofs
through those files, and marks the fixture unavailable on leaving the gate.

This report does **not** certify real WeCom private/group-@ delivery, real Bot
rendering and network reachability, Qwen integration or model quality, browser
interaction, long-running recovery/load targets, or the full V2 scope. Real model
and test Bot credentials still belong in local deployment configuration. Restore
the normal channel worker network/WS endpoint and LLM provider configuration, and
stop only the owned protocol fixtures when all dependent browser tests finish.
