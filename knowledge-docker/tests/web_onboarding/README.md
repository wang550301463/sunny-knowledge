# Real-browser first-use guide acceptance

This bounded case exercises deployed Chromium, same-origin Web/gateway,
Keycloak Authorization Code + PKCE, IAM/Auth, Markdown preview/Temporal ingest,
canonical review/Wiki/lifecycle/original source and Agent/LLM. Only Chat provider
output is deterministic protocol simulation. Browser API interception is not used.

## Preconditions and isolated resources

- Deploy the independently reviewed onboarding Web image and current domain
  services. Existing `test-env.json` must contain the real isolated localhost
  origin, bootstrap test login and explicitly enrolled `ingest_principal_id`.
  The browser reads this ignored file; it never emits credentials, token storage,
  callback codes or authorization headers.
- PostgreSQL/S3/Temporal, IAM/Auth, ingest API/worker, knowledge, LLM and Agent must
  be healthy. The operator schedules a quiet authorization/publishing window;
  unrelated source publications can invalidate aggregate authorization epochs.
- This overlay creates **onboarding-model-provider**, a separate private
  instance of the already tested `agent_provider/server.py`. Its state/gates are
  not shared with Graph or other Agent acceptance tests.
- The operator must temporarily enable `LLM_ALLOW_HTTP_PROVIDERS=true` and start
  that provider. This test does **not** change `.env`, Compose runtime settings,
  model defaults, retrieval bindings, indexes or workers. The overlay deliberately
  does not override LLM environment. Restore the prior setting after the window.
- A random test space receives read/write grants only for the logged-in user and
  the enrolled ingest worker. No private pilot source/page is accessed or changed;
  authorized catalogue listing is needed by the actual UI. No service account
  receives all-space privileges. Do not run concurrently with Graph's ACL/fault gate.

## Covered flow

1. Prove real PKCE S256 and code exchange without recording the protocol secrets.
   Use authenticated HTTP setup for only the random space/grants and an isolated
   Chat model. Run the actual model probe; discover it in the guide with a visible
   simulation label. Select the explicitly readable space through the guide.
2. Follow the guide's existing source route; create Markdown through the real
   form, preview immutable bytes through the UI, verify sync is disabled before
   preview, and submit sync through the UI. Wait for the real task. Return to the
   guide, select the source/Wiki and verify pending review cannot count as published.
3. Filter the actual review list to the test space, inspect its real preview and
   approve through the UI. Return to the guide and verify exact publication proof
   and server lifecycle eligibility. The old task remains `review_needed`; only
   its selected approved Wiki permits continuing. Open that pinned Wiki and its
   protected source through the actual guide link.
4. Setup a fixed own-space **get-only** Agent and owned session using actual APIs.
   Send a question through Chat. A revision-keyed gate on the separate protocol
   provider keeps generation open until a real verified block and SSE arrive.
   Release it, verify completion and read the run-bound citation. Return to the
   guide and select the actual session/run using native controls. Reload and
   follow the result link; neither local flags nor answer bodies establish success.
5. Revoke only the test source's actual read ACL. Verify the guide withdraws
   source/Wiki/run completion and all derived metadata, and actual source/Wiki
   reads deny while the run is hidden. Clear the guide's local selection, verify
   it does not retry the denied source, and recover the readable space catalogue.

This get-only path intentionally does not invoke hybrid search or require an
Embedding/Reranker binding. The guide must continue to display retrieval readiness
as **not independently verified** after the cited answer. Real Qwen relevance and
full source-to-hybrid-search quality are separate acceptance work.

This case uses an explicitly privileged test administrator for setup and review.
Ordinary-user discovery, lack of model-admin permissions, no-model states and
actor/session changes have contract/DOM tests; they are not represented as real
non-admin browser acceptance by this case.

## Operator-run commands

Only run after the root/operator allocates the browser window and enables the
bounded HTTP provider setting. The browser regression image is already pinned to
Playwright 1.63.0. No deployment or browser process is started by preparation.

```sh
knowledge-docker/scripts/compose.sh -f compose.web-onboarding-regression.yaml --profile test up -d onboarding-model-provider
knowledge-docker/scripts/compose.sh -f compose.web-onboarding-regression.yaml --profile test run --rm --no-deps web-onboarding-regression
knowledge-docker/scripts/compose.sh -f compose.web-onboarding-regression.yaml --profile test stop onboarding-model-provider
```

If the active stack includes the observability overlay, include that same overlay
when operating it; this file only introduces the two new test services. Never
recreate unrelated services merely to run this case.

## Cleanup and evidence

Every created asset is retained in an in-memory owned-resource journal. In
`finally`, the test releases only its revision gate, clears its session,
unpublishes its Agent, retires its model, and removes its source read and space
read/write grants. It retains tagged immutable snapshots/revisions/audits rather
than deleting unrelated data. Every cleanup action is recorded as confirmed or
unconfirmed; any unconfirmed action fails the test. API setup/cleanup reads have
15-second transport deadlines and unknown mutation results are not retried.

`/artifacts/web-onboarding` receives redacted assertion/asset reports, the
Playwright report and explicit stable screenshots of the owned Wiki source,
cited-answer guide card and recovered selected-space card. Automatic screenshots,
trace and video recording are disabled, particularly around authentication.

Preparation-only checks: JavaScript syntax and Compose configuration parsing.
No Chromium, deployed provider, real source workflow or full cleanup is claimed
passed until the actual scheduled case runs and its artifacts are inspected.
