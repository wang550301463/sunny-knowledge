# Real browser → Git / Graphiti / Neo4j acceptance

This suite drives deployed Chromium, Keycloak PKCE, Web, gateway, IAM/auth,
Git ingest, canonical Wiki publication, Elasticsearch hybrid search and actual
Graphiti/Neo4j adjacency. Only embedding and reranking use the explicitly labelled
protocol simulator. No Playwright request interception, canned graph JSON or
browser-generated answer replaces the application APIs.

It is **prepared but not yet run**. The unit preflight results below do not prove
browser acceptance, real Qwen quality or production performance.

## Runtime dependencies and isolation

The operator coordinates this run after private-repository ingestion is idle:

1. Deploy the reviewed Graph Web implementation and current gateway/domain images.
2. Provision the ordinary ingest/retrieval/graphiti worker identities if needed;
   provisioning grants no knowledge access.
3. Record the existing runtime configuration and running states. Start the
   existing `git-model-provider` and enable HTTP protocol providers for the test
   window. Run `scripts/setup_git_graph_regression.py` to create fresh fixture
   model configurations, separate projection schemas, ES index and Graphiti
   namespace. The script writes only ignored `.local/git-graph-regression.*`.
4. Select `compose.graph-regression.yaml` with that generated environment for
   retrieval, retrieval-worker, graphiti and graphiti-worker. Do not mix the
   retrieval-regression overlay or continue private-repository ingestion during
   this temporary runtime switch.
5. Start `web-graph-git-fixture`. It is a **separate** process/container reusing the
   existing pinned read-only Git image. Its temporary Maven manifest declares
   both `org.slf4j:slf4j-api` and `com.google.guava:guava`; the Java, TypeScript and
   Go source files remain genuine fixture files. The npm postinstall script is
   retained as a non-execution sentinel. No repository build/install runs.
6. Run the browser and its bounded fault controller as described below. Resume
   the worker immediately after exact restoration. At the end restore the
   original runtime states/configurations and retire only the newly recorded
   protocol model IDs, using the same procedure as the Git HTTP acceptance.

`test-env.json` is mounted read-only. Browser setup uses its currently logged-in
actor's unchanged Bearer through gateway APIs. Only the new randomly named test
space/source receives user and worker grants. Cleanup removes all read/write
space grants; immutable snapshots, revisions and audits remain for diagnosis.
No unrelated source, account, model, schema, index or graph partition is deleted.

## Commands (operator-controlled; from repository root)

The image `sunny-knowledge/web-regression:v2-local` provides pinned Playwright
1.63.0 and Chromium. No new package dependency is required. `git-fixture` and the
Python regression image must already have been built according to the existing
Git acceptance instructions.

```sh
knowledge-docker/scripts/compose.sh --env-file .local/git-graph-regression.env -f compose.graph-regression.yaml -f compose.web-graph-regression.yaml up -d --no-deps web-graph-git-fixture
```

Prepare a clean **dedicated** `.local/web-graph-browser` directory. Do not reuse a
previous request, operator acknowledgement or release file. Run these two commands
in separate processes after the dependencies above are ready:

```sh
knowledge-docker/scripts/compose.sh --env-file .local/git-graph-regression.env -f compose.graph-regression.yaml -f compose.web-graph-regression.yaml run --rm --no-deps web-graph-fault-control
knowledge-docker/scripts/compose.sh --env-file .local/git-graph-regression.env -f compose.graph-regression.yaml -f compose.web-graph-regression.yaml run --rm --no-deps web-graph-regression
```

The browser first ingests the real Git fixture, verifies canonical structured
relations and waits at most 420 seconds for the real projections. This wait covers
the existing 300-second outbox lease following failed delivery; it is not an
interactive-latency assertion. The browser test has a 900-second total budget.

At the physical fault step it writes only non-secret fixture IDs to
`.local/web-graph-browser/request.json`. The controller then writes
`state=awaiting_operator_worker_pause`. **Only then**, the operator stops the
fixture-configured `graphiti-worker` and writes `operator-ready.json` containing
`{"fixture_id":"the exact current request fixture_id"}`. The acknowledgement
must follow the successful worker stop. Other Graph API, Neo4j and retrieval
services stay available. The controller waits 120 seconds for this same-run
acknowledgement; the browser waits 180 seconds for the applied fault.

The controller validates the namespace against the ignored provisioning record,
then matches one exact canonical edge using namespace, page ID, revision ID and
edge ID, with a maximum of 16 physical generations. In one Neo4j transaction it
copies only those relationships to a dedicated `WEB_GRAPH_EDGE_BACKUP` type and
removes the `RELATES_TO` originals. Application traversal never consumes that
backup relationship type. The copy survives a lost reply or controller crash;
repeated attempts for the same fixture are idempotent.

After the browser observes `graph_projection_pending`, it writes its matching
release ID. The controller restores the exact original relationship properties
and deletes the temporary backup in one transaction, including from its `finally`
path. It reports `state=restored`. The operator can now start the same
fixture-configured worker. The remaining browser steps revoke source access and
then space access through the real authorization API.

If the controller or machine is interrupted, keep the request file and selected
namespace unchanged, leave the worker paused, and restore only the recorded backup:

```sh
knowledge-docker/scripts/compose.sh --env-file .local/git-graph-regression.env -f compose.graph-regression.yaml -f compose.web-graph-regression.yaml run --rm --no-deps web-graph-fault-control python /tests/web_graph/fault_control.py --restore-only
```

A failed controller command is not a successful restoration. Check the named gate
state and reconcile that exact test projection before restarting the worker or
restoring ordinary runtime configuration. The fault controller never receives a
Docker socket and cannot stop services itself. Its Neo4j password comes only from
the existing private Compose environment; it is never available to Chromium.

## Browser assertions and evidence

- Real login observes PKCE S256 and authorization-code exchange without recording
  passwords, tokens, callback codes or HTTP headers.
- An explicit hybrid search recalls the actual Guava dependency fragment. The UI
  selects that fragment ID and performs a real incoming one-hop traversal.
- Draft direction/hop edits leave the current executed view unchanged. Applying
  both/two hops returns a real `dependency → module → sibling dependency` path;
  stored relationship arrows remain `module → dependency`.
- The source drawer reads the real snapshot and displays exact original Maven XML
  after pre/post traversal authorization, checking commit and SHA-256 metadata.
- Business `as_of` and knowledge `known_at` are actual request/response fields.
  Historical scope explicitly displays the projected-history limitation and
  absence of deployment evidence. This notice does not substitute for the
  separate real physical missing-edge degradation assertion.
- Removing the exact Neo4j relationship shows a projection-pending capability gap
  and cannot be interpreted as proof of no dependency. Restoration returns it.
- Real source ACL tightening removes the graph, relationship controls and original
  text while the still-authorized space header remains. Removing space read next
  withdraws that header and disables graph reads. Direct protected source and
  space reads return 403. No browser route is mocked to simulate revocation.
- Passive checks, directions, source reads and history changes do not issue
  additional browser `/search` calls; only the explicit initial search does.

Artifacts are under `knowledge-docker/artifacts/web-graph/`: final screenshots of
one-hop/two-hop, exact source, time/history, missing-edge degradation, source
revocation and space revocation, plus redacted asset/cleanup and assertion reports.
Tracing, video and automatic failure screenshots are off to avoid incidental
credential capture. Final selected screenshots disable animations and the normal
graph view also checks absence of page horizontal overflow.

## Preparation verification

- Real temporary Git fixture preflight was RED before implementation, then green.
- Three local preparation tests passed in 0.322 seconds: real Git contents and
  three languages, strict namespace/freshness/scope validation, and malformed gate
  rejection. Logs: `artifacts/web-graph-browser-preparation-{red,green}.log`.
- JavaScript `node --check`, Python Ruff checks/format, and Compose
  `config --quiet` passed. These checks started no container and changed no live
  runtime or data.
- Real browser execution and real Neo4j fault/restoration remain pending the
  operator's coordinated integration window. No skipped test is counted as pass.