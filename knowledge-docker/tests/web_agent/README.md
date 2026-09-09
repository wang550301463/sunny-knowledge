# Actual Chromium Agent and channel-result acceptance

These tests use the deployed gateway, Keycloak Authorization Code + S256 PKCE,
React WebUI, Agent/worker, LLM, IAM/auth, canonical knowledge, ingestion, PostgreSQL,
Temporal and S3. They do not intercept or substitute browser API responses.
The existing `agent-model-provider` is an explicit deterministic OpenAI protocol
fixture. Results establish UI/protocol/authorization behavior, not Qwen quality or
real WeCom Bot connectivity.

Run from the V2 worktree after the current application and provider are deployed:

```sh
docker compose --env-file knowledge-docker/.env \
  -f knowledge-docker/compose.yaml \
  -f knowledge-docker/compose.agent-regression.yaml \
  -f knowledge-docker/compose.web-agent-regression.yaml \
  run --rm --no-deps web-agent-regression
```

Build `web-regression` first if its pinned Playwright 1.63.0 image is absent. The
overlay reuses that dependency image and mounts only these test sources. The TCP
bridge from the existing browser fixture keeps the actual public localhost origin,
Host, Keycloak issuer and callback unchanged while reaching the Compose gateway.

The main case creates an isolated space, grants only its current user and ingestion
worker, previews/synchronizes/approves an actual Markdown source, and creates/tests
a fixture model using real HTTP APIs. The browser then creates and CAS-publishes an
Agent with the exact model configuration and scope. A fixture streaming gate proves
the first fact appears while the model is still paused before completion; browser
reload must resume the actual SSE cursor. It reads pinned citations, downloads
server-authorized Markdown, submits feedback, opens the actual channel editor with
no BotSecret, and revokes only its source to check DOM/citation/export hiding.

The ordinary Web citation drawer uses the protected source-snapshot route. This
case separately verifies the real exact-run citation API; it does not call the
ordinary drawer a channel citation test.

## Separate live channel-result handoff

The root `tests/channel_http` integration owns the actual local WeCom protocol
simulation, two-way identity binding, group grant and Agent run. Enable its bounded
browser gate to write `.local/web-channel-browser/fixture.json`:

```json
{
  "fixture_id": "unique UUID",
  "created_at": 0,
  "available": true,
  "run_id": "actual authorized channel run ID",
  "marker": "nonsensitive fixture source marker",
  "channel_id": "owned channel ID",
  "source_resource_id": "owned source resource ID",
  "space_id": "owned fixture space ID"
}
```

`created_at` is Unix seconds. No token, BotSecret, password or binding proof belongs
in this file. A dedicated read/write mount exposes only this gate directory. Start
`web-channel-result-regression` with the same Compose files while the root gate is
active. It selects only the channel test; the ordinary run has no skipped channel
case. The test rejects stale metadata, opens the real channel run and its deep
citation link, checks all observed application requests remain read-only and never
touch ordinary session/Agent/space/source routes, downloads the protected result,
then writes the exact `fixture_id` to `release`. Root performs actual revocation and
cleanup; the browser checks the open answer and source disappear. A `finally` block
releases root even on failure. Root's timeout is a failure, never success.

## Data handling and retained evidence

Credentials are read only from ignored `.local/test-env.json` by the existing
browser login helper. Traces, videos and automatic screenshots are disabled.
Explicit screenshots occur only after login and contain fixture answers/config
without entered credentials. Browser binding proof is never part of these cases.
OAuth URLs/bodies/tokens are not retained; login evidence consists of two booleans.

Ignored `artifacts/web-agent/` contains JSON test reports, safe behavior summaries,
explicit Chromium PNGs and exported fixture Markdown. On completion/failure, exact
main-test cleanup releases its own provider gate, clears its own session,
unpublishes its own Agent, retires its own model and removes its own space grants.
Tagged immutable source/revision/audit records remain for diagnosis; no wildcard
deletion or pre-existing scope/model edits occur. `assets-*.json` records each
cleanup result and exact IDs. A cleanup failure fails the test and needs review of
that precise retained asset; it never triggers broad deletion.

Verification status is recorded separately in `VERIFICATION.md` only after actual
Chromium execution. Syntax/Compose checks alone are not browser acceptance.
