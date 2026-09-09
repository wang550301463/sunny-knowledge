# Controlled platform recovery acceptance

This harness prepares and checks one **independent acceptance deployment**. It does not stop/start existing containers, deploy applications, delete a database/index/namespace/bucket, change existing model bindings, or touch the main deployment.

Source: `sunny-acceptance-5add1c1af0`, copied root `knowledge-docker/.local/acceptance-5add1c1af0/knowledge-docker`, public origin `http://localhost:28181`, PostgreSQL `25440`. The main `18180` deployment and its private pilot are outside this harness.

The generated target is `sunny-recovery-<12 hex>`, with a new directory/network/volumes and PostgreSQL `25441` by default. The **same origin** `28181` is deliberately preserved: the operator must stop source admission/Keycloak/writers before target applications start, and release target `28181` before restoring the source's original running state. No OIDC issuer/callback/key migration or fresh realm import occurs.

## What is proved

- `postgres_store.py` verifies and restores all thirteen archives to new `recovery_<service>` databases, with the original service roles/passwords. `temporal_visibility` stays owned by `temporal`. Every restored database is connected using its actual service role, and every user table's row count is compared before any target application starts. Archive/TOC verification remains the existing backup tool's responsibility.
- `snapshot_store.py` backs up/verifies/restores the exact immutable raw object set and bytes. Every canonical snapshot object key must exist in this set. The target uses a new bucket; a pre-existing target bucket is refused by this harness.
- Original workload/delegation private keys, Keycloak material, S3 credentials, and encryption keys are retained in private files. A nonempty random **protocol-only** model credential is encrypted through the real model API before backup; after restoration the original ciphertext hash, decryption, wrong-key rejection, and real LLM capability-test request are verified. This is not Qwen quality or real Bot evidence.
- The restored retrieval/graphiti databases are retained as archive evidence. Active runtime points at two additional **fresh** `projection_retrieval`/`projection_graphiti` databases. Before applications start, both must have zero user tables, ES must have no index with the new name, and the new Graphiti namespace must have zero physical nodes/edges. All physical storage container IDs are pinned across this check and rebuild. An old running journal cannot be reused to certify a replacement backend.
- Two isolated Java/TypeScript/Go Git sources are ingested. Only their own exact README proposals are approved. Real ES/Graphiti dependency paths and both actual outbox ACKs are checked before backup. Workers then lose source-read access to the negative fixture, while the human actor keeps canonical read access. No worker receives global read access.
- `--rebuild` exits `2` for mixed ACL and persists `not_visible` outcomes in its own DB. The harness checks **every positive fixture revision is projected and every negative revision has a durable deny**, and compares the original canonical ACK timestamps. It never ACKs events manually. A total count alone cannot pass coverage.
- On the target, real PKCE returns the same actor, Wiki revisions and exact sources match the baseline, three real dependency paths and hybrid search work, and the negative source has no projection. A later real source ACL tightening withdraws page/old revision/source/search/graph content.

A passing positive fixture proves that authorized scope was recovered. The global inventory is explicitly **partial** when other revisions are not visible. The final receipt sets `full_platform_restore_certified: false`; it does not relabel omissions as full recovery.

## Operator sequence

Run these host commands from the repository worktree. Do not print `.env`, private configuration, source responses, bind proofs, or backup contents. All generated sensitive files are `0600` under private directories. Use a protected/encrypted backup location.

1. Wait for the root operator to release the independent source API window. Prepare a fresh target (no Docker/API side effects):

   ```sh
   python3 knowledge-docker/tests/platform_recovery/prepare.py \
     --destination knowledge-docker/.local/platform-recovery-UNIQUE \
     --recovery-id REPLACE_WITH_32_LOWERCASE_HEX \
     --postgres-port 25441
   ```

   `--source-root` is fixed to the authorized acceptance source; it cannot point at the main project. Preparation validates all private inputs and target port availability before writing. The port is rechecked by Docker at actual bind time; preparation does not reserve it. Never rerun `init.py` in this target, since that would replace original keys.

   Set an ordinary task variable to the resulting absolute private plan path:

   ```sh
   recovery_plan=/absolute/new/target/knowledge-docker/.local/recovery/plan.json
   python3 knowledge-docker/tests/platform_recovery/control.py capture --plan "$recovery_plan"
   python3 knowledge-docker/tests/platform_recovery/control.py fixture-before --plan "$recovery_plan"
   ```

   `capture` records exact container IDs, immutable image IDs and previous running state, without capturing environment variables. It creates `compose.recovery-images.json`. `fixture-before` runs a one-off test container on the **source's existing backend network**; only its private journal directory is writable. It does not start dependencies. The deterministic Git/model fixture providers and ingest/retrieval/graphiti workers must already be running and separately enrolled. A failed baseline retains its exact asset journal for cleanup and cannot be silently replayed.

2. The **root operator** records/stops all source containers that were running, except `postgres` and `seaweedfs`, and independently stops any external writers. Do not use a generic `compose down`, and do not restart services that were not previously running. Then attest and check:

   ```sh
   python3 knowledge-docker/tests/platform_recovery/control.py quiesced \
     --plan "$recovery_plan" --external-writers-stopped
   python3 knowledge-docker/tests/platform_recovery/control.py backup --plan "$recovery_plan"
   ```

   Quiescence is checked again before/after backup and in target restore/fresh/rebuild phases. PG/S3 source container IDs must match the captured inventory. Separate PG dumps have cross-service meaning only within this operator-controlled no-write window. Backup/restore tools are called directly; their verification/transaction protections are not duplicated or bypassed.

3. The operator starts **only** target `postgres seaweedfs elasticsearch neo4j valkey`, using this exact Compose order, existing immutable images and `--no-build --pull never`:

   ```text
   --project-directory <target>/knowledge-docker
   --env-file <target>/knowledge-docker/.env
   -p sunny-recovery-<12 hex>
   -f <target>/knowledge-docker/compose.yaml
   -f <target>/knowledge-docker/compose.graph-regression.yaml
   -f <target>/knowledge-docker/compose.recovery-images.json
   -f <target>/knowledge-docker/compose.recovery.json
   ```

   `compose.recovery.json` contains credentials and is private. The final override maps **all API and worker databases**, including `knowledge-lifecycle-worker`; Keycloak uses `recovery_keycloak` without realm import, and Temporal uses its two restored databases. Init SQL creates original roles and only the two fresh projection databases. The thirteen restored databases are created/admitted by `postgres_store.py`, not by PostgreSQL init SQL.

   Once those five targets are ready:

   ```sh
   python3 knowledge-docker/tests/platform_recovery/control.py restore --plan "$recovery_plan"
   python3 knowledge-docker/tests/platform_recovery/control.py fresh --plan "$recovery_plan"
   ```

4. The operator starts target `keycloak temporal`, all eleven APIs, `web git-fixture git-model-provider`. Keep **all background workers stopped**, including the lifecycle worker. Source admission remains stopped throughout. The restored model UUIDs point to the target protocol provider with identical hostnames; runtime index/namespace and catalog DBs are fresh.

   ```sh
   python3 knowledge-docker/tests/platform_recovery/control.py rebuild --plan "$recovery_plan"
   python3 knowledge-docker/tests/platform_recovery/control.py fixture-after --plan "$recovery_plan"
   python3 knowledge-docker/tests/platform_recovery/control.py verified --plan "$recovery_plan"
   python3 knowledge-docker/tests/platform_recovery/control.py fixture-revoke --plan "$recovery_plan"
   python3 knowledge-docker/tests/platform_recovery/control.py fixture-cleanup-target --plan "$recovery_plan"
   ```

   `rebuild` itself runs two one-off workers in order, accepts only a confirmed exit `0`/`2` with coherent JSON, and persists the separate global inventory status. Each worker has a one-hour total command deadline. It does not turn them into background consumers. Ordinary API calls have 30-second bounds; Git tasks have a 180-second bound, projection readiness 420 seconds, and source ACK readiness 120 seconds. Timeouts fail the phase.

5. The operator stops the target admission/application containers, verifies `28181` is released, and restores **only the originally running source containers by recorded ID**. Once source APIs are healthy, clean the exact source copies:

   ```sh
   python3 knowledge-docker/tests/platform_recovery/control.py fixture-cleanup-source --plan "$recovery_plan"
   ```

   Cleanup only empties grants for the two recorded spaces/source resources and retires the owned canary model. It retains immutable knowledge/audit history. No database, bucket, index, namespace, directory or non-fixture grant is deleted. Source and target cleanup receipts are independent.

## Failure and interruption

Each mutating phase writes an exclusive `*.started.json`, then `*.complete.json` only after all checks. An exception writes `failed_or_uncertain`; a killed process leaves `started` without a completion. Both states prevent automatic replay and prevent dependent phases from running. The root operator must inspect retained storage and private receipts; never remove a receipt to convert an unknown restore into a fresh attempt. Start with a new target for an uncertain restore. Source running-state restoration remains an operator obligation even if the harness fails.

API-created exact assets are journaled after each acknowledged creation. A timeout before a creation response is an uncertain orphan and is not retried. `api-progress.json` contains only side/method/route family/status; it omits identifiers, bodies, headers and credentials. The tools deliberately suppress provider/database exception text. Detailed dump/backup receipts remain private. Only safe fixed state/count fields reach stdout.

## Local validation and remaining gate

```sh
python3 -m unittest discover -s knowledge-docker/tests/platform_recovery -p 'test_*.py' -v
services/python/.venv/bin/ruff check knowledge-docker/tests/platform_recovery
python3 -m py_compile knowledge-docker/tests/platform_recovery/*.py
```

Local guard tests cover thirteen mappings/owner roles, same-origin key preservation, main-origin rejection, private symlinks, busy/existing targets, source identity/quiescence, interruption receipts, old catalog/index/graph rejection, coherent partial reports and exact positive/negative revision coverage. They do not establish a performed platform restore. Actual PG17/S3/ES/Neo4j/Keycloak/API execution and the source-running-state restoration need the root operator's scheduled window and their resulting receipts.