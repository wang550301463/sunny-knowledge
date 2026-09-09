# Git / ES / Graphiti acceptance — 2026-09-08

Verified in the isolated `sunny-knowledge-v2` Docker project on branch
`codex/platform-v2`. No source repository build, dependency installation, checkout
hook or npm postinstall script was executed.

- Fixture/provisioning Docker behavior suite: **5 tests passed, 2.378 seconds**.
- Full gateway/Keycloak/Git/Temporal/S3/canonical/ES/Graphiti HTTP suite:
  **1 test passed, 340.634 seconds**.
- Ruff check passed; all 6 Python files passed format verification.
- Final fixture image built successfully from the pinned Python/Git versions.

The full HTTP suite validated both fixed commits, Java/TypeScript/Go declarations,
three manifest dependency relations per revision (**6 physical dependency edges**
across two revisions), exact original commit/hash/line citations, explicit README
review, duplicate sync idempotency, changed Maven dependency version, deleted Go
page invalidation, immutable history and source-level revocation across search,
graph traversal, timeline, current pages, old revisions and original snapshots.
The first commit contained 8 code/manifest files plus one Markdown document; the
second commit removed `go/legacy.go` and changed code and the Maven declaration.

An initial attempt failed at **187.574 seconds** because its 180-second projection
window ended before failed outbox deliveries' fixed **300-second leases** expired.
Missing projections were subsequently observed to recover through ordinary outbox
redelivery. The final run used a bounded 420-second projection window and passed
without changing leases, bypassing authorization or manually writing graph data.
Concurrent IAM epoch changes during publication are a plausible trigger for those
failed initial attempts; the worker's generic safe warning does not identify each
individual failure cause. This exposes an operational retry-latency consideration,
not evidence that interactive retrieval meets any performance target.

The test used deterministic **embedding/reranking protocol simulators**. It did not
use real Qwen/DashScope credentials and does not establish model quality, Recall@10,
load capacity, production deployment state or real enterprise repository coverage.
Graphiti was a real SDK-backed Neo4j service and worker; no graph response was mocked.

## Runtime restoration

After acceptance, runtime state, sorted environment fingerprints and image IDs
were compared with the pre-test snapshot. All four matched exactly:

- `retrieval`: running; healthcheck healthy.
- `graphiti`: running; healthcheck healthy.
- `graphiti-worker`: running.
- `retrieval-worker`: stopped, with its original protocol-test configuration and
  original image. Its older image was restored explicitly without starting it.

The original observability overrides were restored. LLM and
`agent-model-provider` were neither stopped nor reconfigured. Only the two model IDs
created by `setup_git_graph_regression.py` were retired, with their states verified
through the public model API. The two newly introduced Git/protocol fixture
containers were stopped. Their isolated schema/index/namespace data and test
space/source remain available for diagnosis; no broad deletion was performed.
Run setup again to create fresh protocol model configurations before rerunning.

Local diagnostic artifacts (not committed):

- `/tmp/knowledge-git-graph-http.log`: final complete HTTP test output.
- `/tmp/knowledge-git-graph-before.json`: pre-test running states/environment hashes.
- `/tmp/knowledge-git-graph-restore.json`: final restoration checks.
- `knowledge-docker/.local/git-graph-regression.json`: exact isolated resources and
  the two retired model IDs, with `state=retired`.