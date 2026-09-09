# Git → Wiki → ES / Graphiti Docker acceptance

This suite uses real Git smart HTTP, Tree-sitter Java/TypeScript/Go analysis,
Keycloak PKCE, gateway/IAM/auth, ingest PostgreSQL/Temporal/S3, canonical
publication/review, Elasticsearch, and actual Graphiti SDK Neo4j adjacency.
Embedding and reranking use a deterministic **protocol simulator**. Passing this
suite provides neither real Qwen integration nor retrieval-quality/Recall@10 evidence.

The read-only `git-fixture` service has no host port or volume. It builds two
reproducible commits in temporary storage, serves only one repository, rejects
receive-pack and arbitrary paths, and runs as an unprivileged user. The fixture
contains Maven, npm/lockfile and Go manifests, declarations in all three languages,
a reviewed Markdown procedure, and a second commit with changed code/Maven version
and a deleted Go file. The npm postinstall marker must never execute.

## Start (from repository root)

Ensure the normal platform, ingest worker, and worker identities have been
provisioned. `configure_worker.py` is idempotent and grants no knowledge access:

```sh
services/python/.venv/bin/python knowledge-docker/scripts/configure_worker.py ingest
services/python/.venv/bin/python knowledge-docker/scripts/configure_worker.py retrieval
services/python/.venv/bin/python knowledge-docker/scripts/configure_worker.py graphiti
knowledge-docker/scripts/compose.sh -f compose.graph-regression.yaml build git-fixture
knowledge-docker/scripts/compose.sh -f compose.graph-regression.yaml up -d --no-deps git-fixture git-model-provider llm
services/python/.venv/bin/python knowledge-docker/scripts/setup_git_graph_regression.py
knowledge-docker/scripts/compose.sh --env-file .local/git-graph-regression.env -f compose.graph-regression.yaml up -d --no-deps retrieval retrieval-worker graphiti graphiti-worker
knowledge-docker/scripts/compose.sh --env-file .local/git-graph-regression.env -f compose.graph-regression.yaml run --rm --no-deps regression python -m unittest discover -s knowledge-docker/tests/git_graph_http -v
```

Do not combine this override with `compose.retrieval-regression.yaml`. Setup creates
new models, a random schema in each projection service's own database, an isolated
ES index and a random Graphiti namespace. It changes only dedicated
`.local/git-graph-regression.{json,env}` files (0600), not the original `.env`, model
configurations, worker identities or source data. The override changes runtime
projection configuration only when explicitly selected. Each run creates its own
space/source and grants its user and the three preconfigured workers access there.
No other space receives grants. The suite intentionally leaves these namespaced
artifacts available for diagnosis.

The six generated environment variables are:

- `GIT_GRAPH_RETRIEVAL_DATABASE_URL`
- `GIT_GRAPH_ES_INDEX`
- `GIT_GRAPH_EMBEDDING_CONFIGURATION_ID`
- `GIT_GRAPH_RERANK_CONFIGURATION_ID`
- `GRAPH_REGRESSION_DATABASE_URL`
- `GRAPH_REGRESSION_NAMESPACE`

Use the normal base configuration to restore the application's previous runtime
configuration after acceptance. Retire only the fixture model IDs and remove only
this run's explicitly recorded test schema/index/namespace when performing deliberate
cleanup. No broad database/index/graph deletion is built into the tests.

## What the HTTP test proves

1. Preview pins each Git tag to its immutable commit and exact file hashes;
   duplicate synchronization returns the same durable task.
2. Static code/manifests publish deterministic facts; README remains a proposal and
   is explicitly approved through the public review API.
3. Java, TypeScript and Go declarations carry fixed original commit and line
   references. Maven, npm and Go manifest dependencies carry structured relations.
4. ES projections appear; traversing three documented module fragment IDs returns
   actual supported `depends_on` edges and paths, with exact original excerpts.
   Empty/degraded/missing graph results fail after a bounded wait.
5. A second source version changes declarations and the Maven dependency version;
   its deleted Go page becomes a new audited stale revision while the old immutable
   revision remains inspectable. Current traversal rejects its prior fragment.
6. Source ACL tightening immediately removes derived search/graph/history results
   and denies current pages, old revisions and original snapshots.

Graph availability is mandatory. The test contains no graph mock and never labels
pending projections or `graph_unavailable` as a successful graph assertion.

## Preparation verification recorded on 2026-09-08

The fixture image built successfully with `python:3.12.12-slim-bookworm` and pinned
Git `1:2.39.5-0+deb12u3`. Both deployed-container tags were successfully fetched by the
actual `GitConnector` using depth=1:

- `fixture-v1`: `d33a5bae9468bf289dbcee7073dace3fdd062ba2`
- `fixture-v2`: `10acb792d421d10b82331f5e6552689c508860ee`

The fixture/provisioning behavior suite is under `../git_fixture/`. The full HTTP
chain requires the explicit projection deployment above; this preparation record
is not a claim that the full chain or a real model provider has passed.