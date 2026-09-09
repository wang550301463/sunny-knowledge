# 100k fragment / ten concurrent actor retrieval gate

This harness prepares real public Git repositories, registers sources through the
gateway, uses the ordinary ingest/Temporal/S3/canonical publication pipeline, and
waits for real Elasticsearch projections. It never writes ES documents directly.
It is not a model-quality evaluation: embeddings are deterministic SHAKE-256
vectors and reranking recognizes exact fixture symbols. The default vector size
is **1024**, and both the model configuration and retrieval index use that size.

`fixture.py` defaults to 100 repositories. Each has Java, TypeScript and Go code
with 340 declarations per language, plus actual pom.xml, package.json,
package-lock.json and go.mod declarations. Files remain below the compiler's
900-symbol bound. There are no executable installation/build scripts. Commits,
source hashes and statements are deterministic; their count is preparation data,
not proof of 100k indexed fragments. The final gate counts real current valid ES
fragments and checks the exact page/revision inventory against published Wiki.

## Isolation prerequisites

Use a **new** ignored deployment directory and project named
`sunny-perf-<8..32 lowercase letters/digits>`. Do not reuse the main private corpus
or the recovery project's catalogs, model configuration, network, volumes, keys,
or journals. No command in this harness starts, stops, recreates or removes
containers. The operator provisions only the new project, then invokes the steps
below. A pending management operation is not automatically retried.

Copy only public Compose YAML, scripts, the performance tests, and observability
configuration into the new `knowledge-docker` directory. Exclude every `.local`,
`.env`, artifacts directory, bytecode cache and private source checkout. Resolve
every Compose service image to its already-built `sha256:<id>` (or digest), remove
all `build` entries, and ensure each API/worker pair uses the same reviewed image.
Use the matching reviewed regression image. The fixture overlay takes
`PERFORMANCE_REGRESSION_IMAGE` as that exact image ID; it neither builds nor pulls.

Initialize **inside the new directory**, choosing unused host ports:

```sh
python scripts/init.py --project-name sunny-perf-<random> \
  --gateway-port <unused-gateway-port> --postgres-port <unused-pg-port> \
  --public-url http://localhost:<unused-gateway-port>
```

All rendered networks and volumes must be owned by that project; host mounts
must stay inside the new deployment directory. Host-published ports must bind
127.0.0.1. `prepare` checks those properties, the distinct project/index naming,
immutable images, and the absence of existing readable sources/pages/index data.
The normal API ACL is still enforced: this check does not claim a superuser
inventory of data hidden from the setup actor. Fresh isolated databases and
volumes are an operator prerequisite, not something ACL-filtered lists prove.

Keep the new deployment's `.local/test-env.json` and `.env` private. Set the
following **only in the new project's environment**:

```sh
PERFORMANCE_REGRESSION_IMAGE=sha256:<reviewed-regression-image-id>
PERFORMANCE_DIMENSIONS=1024
```

Use the following Compose file combination in that directory:

```sh
docker compose --env-file .env -f compose.yaml \
  -f compose.observability.yaml -f tests/performance/compose.performance.yaml \
  --profile app --profile test <command>
```

The examples below call this combination `perf_compose`. Define it locally as a
shell function containing the command above. No global alias or project override
is installed by the harness. Start the new infrastructure and APIs using this
combination, plus `performance-git` and `performance-model`. The providers have no
host ports. Graph projection is outside this retrieval benchmark: leave this new
project's graphiti-worker stopped or idle, and do not grant it the corpus. Do not
stop any existing project's worker. Retrieval/ingest workers start only in the
new project and receive only its generated identities.

Prepare S3/Temporal and worker identities using the existing reviewed scripts:

```sh
perf_compose run --rm --no-deps regression python knowledge-docker/scripts/prepare_ingest.py
python scripts/configure_worker.py ingest
python scripts/configure_worker.py retrieval
```

`configure_worker.py` accepts the service as its existing CLI requires; inspect
`--help` if using another released script version. It creates no content grants.
All commands run with the new directory as the working directory. Persist the
rendered configuration privately (`umask 077`); it contains credentials:

```sh
umask 077
perf_compose config --format json > .local/performance-compose.json
perf_compose run --rm --no-deps regression \
  python knowledge-docker/tests/performance/seed.py \
  --root /workspace/knowledge-docker prepare \
  --compose-json /workspace/knowledge-docker/.local/performance-compose.json \
  --compose-root "$PWD" --es-index knowledge-performance-<same-random> --dimensions 1024
```

The rendered bind paths are host paths, so `--compose-root "$PWD"` tells the
container checker their expected host root. It must be the actual new deployment
directory, not the main project's directory. `prepare` records the declared image
IDs; independently compare the new running containers' `.Image` values with
those IDs before acceptance. The record does not claim to inspect Docker runtime
identity from inside the regression container.

Preparation creates and capability-tests two protocol model configurations and
**100 ordinary Keycloak + IAM accounts**, each with its own random password.
They have no administrative permission. The first ten will independently log in
with Authorization Code + PKCE and read scope for the load. All 100 get corpus
read permission; this proves configured account population, not 100 concurrent
sessions. Credentials remain only in the private 0600 preparation receipt.

Preparation writes `.local/performance.env`. Add it as a second `--env-file`
when creating/recreating **only the new project's** retrieval API and worker:

```sh
docker compose --env-file .env --env-file .local/performance.env \
  -f compose.yaml -f compose.observability.yaml \
  -f tests/performance/compose.performance.yaml --profile app --profile test \
  up -d --no-deps retrieval retrieval-worker ingest-worker
```

Keep this second env file in all later commands to preserve the same config.
Do not lower application permission checks or change gateway limits to get a
passing run. Defaults are peer 600/min, IdP 120/min and principal 300/min. Actual
configured values are copied into the report; all 429 responses count as failures.

## Native ingestion and verified corpus receipt

```sh
perf_compose run --rm --no-deps regression \
  python knowledge-docker/tests/performance/seed.py --root /workspace/knowledge-docker seed
```

The seed creates one dedicated space and 100 real Git sources, authorizes only
the 100 accounts, setup actor and ingest/retrieval identities, and runs preview
and sync. Each source's fixed commit and all file hashes must match the public
fixture. Actual S3 objects are fetched by their known public content hashes and
verified. Static publications must complete successfully without unresolved
review or conflict. Wiki page IDs and current revisions are recorded from the
public API, as are the precise source snapshot, commit and lines for 300 queries.

The final ES inventory must contain exactly these pages and current revisions,
no foreign/unfiltered documents, and at least **100000 real indexed fragments**.
The default per-source/projection wait is 7200 seconds. Timeout is a failure,
never permission to insert synthetic ES records or skip missing pages. The
private receipt is `seeded` only after this check. `--minimum-fragments` and the
fixture's `PERFORMANCE_REPOSITORIES`/`PERFORMANCE_SYMBOLS` allow a small smoke run;
the final load summary still rejects anything below 100k or 100 sources/accounts.

Known-complete source steps can resume after projection waiting fails. A process
or HTTP failure while creating an account/model/source leaves a pending receipt;
do not rerun such writes blindly. Reconcile the exact private receipt against the
new project's APIs, or start another entirely fresh isolated project. The tool
does not drop or overwrite resources and does not erase a partial attempt.

## Exclusive measured window

First complete ingestion/projection and stop other clients from querying **this
benchmark stack**. No source, grant, model or configuration writes may occur in
the window. Projection embedding traffic must be idle too, since the LLM metric
labels deliberately do not contain caller or model IDs. The main stack can keep
running, but its CPU/IO contention must be noted in the eventual hardware report.

```sh
perf_compose run --rm --no-deps regression \
  python knowledge-docker/tests/performance/run.py --root /workspace/knowledge-docker \
  --exclusive-window --requests-per-actor 30 --warmup-per-actor 1 --settle-seconds 65
```

Ten independent actors log in, their actual `/me` IDs are checked, and all target
originals are reauthorized and hash/line-checked before measurement. Ten warmup
requests are recorded separately. The configurable quiet interval lets the
unchanged default rate-limit window expire; use a corresponding longer interval
if the deployment window differs. Actors obtain fresh tokens immediately before
the measured window. The default measured corpus is 300 searches, 30 per actor,
with at most ten active synchronous HTTP calls. A run long enough to outlive its
tokens fails normally; the harness does not suppress or silently retry 401s.

Every successful response must have a nonempty **primary** result for the expected
statement, the exact pinned Wiki/source identities, and a supporting citation
excerpt. Empty 200s, mismatched evidence, transport errors, timeouts and every
non-200 response fail the run. Raw response bodies, bearer tokens and credentials
are never written to the measurement report. Each attempt records safe status,
duration, failure kind and numeric Retry-After; there are no automatic query retries.

`measurement.py` compares before/after successful `search_local` histogram counts
with successful client responses and uses the exact 2-second bucket. It does not
subtract external averages from a client percentile. Histograms must have stable
boundaries and no reset; count disagreement rejects the measurement. Matching
counts cannot detect equal amounts of unrelated traffic and lost responses, so
exclusive admission remains necessary. Embedding and rerank histogram deltas are
reported separately; in a failure-free run each must match the query count.
The final ES page/revision inventory is checked again after measurement.

`performance-warmup.json` and `performance-requests.json` preserve completed
attempts even if final telemetry is unavailable. `performance-report.json`
contains the acceptance decision, all warmup/measured outcomes, local histogram
deltas, successful-client p95, model deltas, actual fragment count, dimensions,
declared image IDs and limiter configuration. Any request/warmup failure prevents
acceptance. Exit 0 means this synthetic measured sample met all implemented gates;
exit 2 means it did not. A setup/telemetry exception exits nonzero with a safe
message and leaves available receipts intact. No report constitutes real Qwen
quality validation, graph performance, 100 concurrent users, or production sizing.

## Small boundary verification

Run the folder's unittest suite in the existing locked regression image, mounting
the current public Python source and this directory read-only. It starts only
ephemeral loopback HTTP listeners and temporary Git repositories, never a 100k
import or an application restart. Tests cover real Git shallow fetch, all three
actual parsers/compiler and evidence bounds, deterministic dimensioned protocol
HTTP, real ten-actor TCP concurrency/429 accounting, production metric extraction,
the histogram gate, and isolation/index inventory rejection cases.

Full 100-source seed and 100k/ten-concurrent measurement remain separate actual
Docker acceptance steps; unit results alone do not claim that scale was executed.
