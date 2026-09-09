# Actual Wiki lifecycle browser acceptance

This test uses Chromium and the deployed Web/gateway, actual Keycloak PKCE,
IAM/Auth, Markdown ingestion through Temporal, canonical PostgreSQL publication
and exact source snapshots. It uses no model provider or intercepted browser
request. The fixture is a fresh space/source; cleanup revokes only its own read
and write grants and retains immutable audit/history. Credentials are read from
the existing private test configuration and never recorded in artifacts.

Run after the reviewed Web and Knowledge images are deployed and bulk source
publication is idle:

```sh
knowledge-docker/scripts/compose.sh --profile app --profile test \
  -f compose.web-lifecycle-regression.yaml run --rm --no-deps web-lifecycle-regression
```

Assertions cover actual display counting, unchanged original-source age,
refresh/reload and explicit POST replay, a new full-document pinned-version
navigation, exact source access, live source revocation, historical/current
authorization and denied receipt replay. Only selected source-free operational
reports and isolated-fixture screenshots are recorded. Traces, video, token
storage and automatic failure screenshots are disabled.

Status: **not yet passed end to end**. An initial observer bug was corrected to
compare decoded namespaced page IDs. The next actual browser run reproduced a
product bug: full-document navigations shared the router's `default` entry key
and incorrectly reused a previous visit. The retained RED is
`artifacts/web-lifecycle-browser-navigation-red.log`. The fix and conservative
legacy receipt migration passed 55 targeted tests and 9 independent native
Chromium probes, then were deployed.

The subsequent application run was interrupted by a real `503` while the large
private pilot was publishing. A separate 12-read live probe observed 11 successful
reads and one `503 authorization_changed`: the full authorization epoch changed
during that read. No read result is returned from an incoherent permission
snapshot. This is preserved in
`artifacts/lifecycle-during-ingest-authorization.json`; it is not counted as a
successful browser gate. Complete the final gate when bulk publication is idle.
