# Immutable raw-store backup and restore

`scripts/snapshot_store.py` copies and verifies the content-addressed raw objects
owned by ingest. This is the S3 part of recovery; it does not establish a coherent
multi-database backup, pause writers, restore credentials, or rebuild ES/Graphiti.
Those operations require the database recovery procedure and separately verified
rebuild/replay. The current S3 behavior passed ten Docker tests against SeaweedFS,
with no optional skips (`artifacts/snapshot-store-concurrent-green.log`, 0.879s).

The tool supports the current `objects/sha256/{prefix}/{digest}` namespace only.
It rejects unknown keys, corrupt bytes, changed inventories, unsafe local paths,
duplicate manifest entries, unsupported formats and incomplete backups. A completed
manifest is published atomically after each object and its directory are synced.
No partial backup has a completed manifest. Object contents and the manifest remain
sensitive knowledge: store the private directory on an encrypted disk, transfer it
through protected storage, and restrict access independently of the application's
ACLs. The manifest contains no API keys or login tokens. This tool prints only safe
codes and object/byte counts; provider exception bodies are suppressed.

Prepare a private backup directory before mounting it. Run from the V2 worktree:

```sh
mkdir -p knowledge-docker/.local/backups
chmod 700 knowledge-docker/.local/backups
knowledge-docker/scripts/compose.sh -f compose.snapshot-tools.yaml run --rm --no-deps \
  snapshot-store backup --bucket knowledge-raw --directory /backups/raw-20260908
knowledge-docker/scripts/compose.sh -f compose.snapshot-tools.yaml run --rm --no-deps \
  snapshot-store verify --directory /backups/raw-20260908
```

Each backup uses a new directory; existing paths are never overwritten. Verification
checks the complete local byte set without contacting S3. The default guardrails are
one million objects, one GiB per object and a 512 MiB manifest; exceeding them fails
explicitly. The runtime and boto3 dependencies use the project's locked regression
image. `compose.snapshot-tools.yaml` has no Docker socket or PostgreSQL credentials.

Restore into an explicitly selected, pre-created empty bucket in the recovery
deployment. Point the selected Compose deployment at that recovery S3 instance and
mount the preserved private backup directory. Do not start ingest until recovery
has been verified. For a prepared recovery bucket:

```sh
knowledge-docker/scripts/compose.sh -f compose.snapshot-tools.yaml run --rm --no-deps \
  snapshot-store restore --bucket knowledge-raw --directory /backups/raw-20260908
```

The tool validates the entire local backup and all existing destination objects
before writing. A destination may contain only the exact objects from a partial
restore; unrelated or corrupted objects cause `target_conflict`. Every new write is
conditional on absence. An uncertain response is resolved by reading exact bytes,
never by blindly retrying or overwriting. Re-running a partial restore is safe.
After all writes, every destination object is hashed again and the surrounding
inventories must agree. Concurrent writers must remain stopped: verification
detects changes observed during its checks and cannot prevent later external writes.
No failure automatically deletes or repairs destination objects.

Independent review reproduced a same-size mutation of an already checked object
while a second object was being uploaded. The original check incorrectly reported
success. A retained failing-first regression and the final full-byte/inventory check
close that issue. Both the original independent probe and a mutation between final
read and inventory verification now reject with `target_conflict`; independent
evidence is in `/tmp/knowledge-snapshot-review/`. The full real-S3 test also verifies
empty bytes, exact UTF-8/CRLF, multi-block streaming, repeated restore and corruption
preservation. It creates and removes only its own two random test buckets:

```sh
knowledge-docker/scripts/compose.sh -f compose.snapshot-regression.yaml \
  run --rm --no-deps regression python -m unittest discover \
  -s knowledge-docker/tests -p test_snapshot_store.py -v
```
