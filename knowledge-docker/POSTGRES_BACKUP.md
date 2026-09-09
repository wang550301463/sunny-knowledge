# PostgreSQL archive backup and new-database restore

`scripts/postgres_store.py` provides `backup`, `verify` and `restore` for the
PostgreSQL portion of recovery. It uses custom-format archives, with one independent
snapshot per database. It does not pause services, back up S3, copy deployment
secrets, perform ES/Graphiti rebuilds, change application DSNs or certify a
platform-wide recovery. The accompanying regression creates a separate Docker
project, with no published ports and no access to live databases.

The tool requires Python 3.11+ and the official PostgreSQL **17.6** `pg_dump`,
`pg_restore` and `psql` clients. It checks all three versions. Supply local binaries,
or `--docker-container` to execute those binaries inside an explicitly selected
`postgres:17.6-alpine` container. Archives pass over stdin/stdout; the application
containers do not receive a Docker socket. Server versions supported by this
pinned client are PostgreSQL 17.0–17.6. A future version upgrade requires updating
and rerunning this acceptance gate; the tool rejects a newer server today.

## Consistency and shutdown boundary

Every manifest is labeled `per_database_snapshot`. Each `pg_dump` is consistent
within that database, but dumps run sequentially and do not share a cross-database
snapshot. Even a successful backup of every database is not, by itself, a coherent
platform checkpoint. [PostgreSQL pg_dump documentation](https://www.postgresql.org/docs/17/app-pgdump.html)

To declare a coordinated platform backup, the operator must first stop admission,
stop **all** API and background writers (including channel/Agent, ingest, projection,
maintenance and outbox workers), freeze Temporal servers/workers and Keycloak,
and ensure external database writers and S3 uploads have stopped. Record the exact
writer inventory, stopped state, backup start/end and corresponding raw-store set
in the external recovery record. Keep writers stopped through both PG and S3
backups. This tool does not stop anything, infer quiescence from traffic or upgrade
its consistency label on the strength of a command-line assertion. The regression
leaves all existing application services running and makes no platform consistency
claim.

## Preserve roles, secrets and backup trust

Restore the original private disaster-recovery configuration **before** connecting
applications. Preserve the service database passwords and Keycloak configuration,
`CREDENTIAL_ENCRYPTION_KEY`, `AGENT_ENCRYPTION_KEY`, `CHANNEL_ENCRYPTION_KEY`,
workload/delegation signing and verification keys, OAuth client secrets, and S3
credentials. Keep their protected backup separate from these database archives.
Do not rerun initialization to generate replacement keys: the old ciphertext will
not decrypt with a new key. The test restores ciphertext created by the real
`SecretBox`, verifies it with the retained key, and proves a new key cannot decrypt
it. This verifies cryptographic preservation, not recovery of real provider secrets.

Cluster globals, roles, role passwords/memberships and server configuration are
**not** exported. Provision the intended service LOGIN roles from the preserved
configuration in the isolated recovery cluster first. Do not start application
processes during restore. Restore requires an explicitly configured administrator
with superuser privileges; the temporary zero connection limit gates ordinary
service logins while the administrator restores as the service owner.

All directories/files belong to the invoking OS user and must be private (directory
0700, files 0600). New output gets these permissions, symlinks and non-regular files
are rejected, and existing backup directories/receipts are never overwritten. Keep
parent directories controlled by the same operator. Store archives on encrypted,
access-controlled storage: they contain knowledge, account data and encrypted
credentials, and may also contain other sensitive service metadata. A manifest
contains database/role names and checksums but no connection password or encryption
key. It is still sensitive operational metadata.

A manifest and its archives must come from a trusted backup authority and stay
unchanged during verification/restore. SHA-256 and TOC hashes detect corruption
relative to that trusted manifest. They do not authenticate a malicious replacement
of **both** archive and manifest. PostgreSQL restore executes archive SQL; do not
restore untrusted dumps with the recovery administrator. This tool is not a SQL
sandbox. [PostgreSQL restore security and transaction behavior](https://www.postgresql.org/docs/17/app-pgrestore.html)

## Database and ownership inventory

The current initializer creates 13 databases. For `iam`, `auth`, `channel`,
`knowledge`, `ingest`, `retrieval`, `llm`, `graphiti`, `agent`, `mcp`, `keycloak`
and `temporal`, the source name is `knowledge_<service>` and its owner/normal
CONNECT role is `<service>`. `gateway` is stateless and has no database.

**`knowledge_temporal_visibility` is owned by `temporal`, not
`temporal_visibility`.** Its recovery owner and CONNECT role must be `temporal`.
An unused `temporal_visibility` role may exist in an initialized deployment; its
existence does not change ownership. The backup validates each supplied owner
against the real source database. Inventory any separately approved CONNECT roles
explicitly; the tool does not infer grants or recover role memberships.

Custom-format dumps include database contents and schema, not original ownership
or access grants. `pg_dump --no-owner` is ignored for a custom archive; the restore
flags are authoritative: `pg_restore --no-owner --no-acl --role <target-owner>`.
The new database and every restored object belong to the configured service role.
The tool revokes **all database privileges from PUBLIC**, grants CONNECT only to the
explicit `connect_roles` list (which must include the owner), and opens ordinary
connections only after every archive has restored. Additional CONNECT roles do not
receive table/schema privileges automatically. Default isolation is one owner per
service database; shared data access must be deliberately provisioned separately.

## Private configuration and commands

Create a private operator directory on protected storage. Source and target JSON
files have exactly `connection` and `databases`. Copy existing passwords locally;
do not send them through chat, shell command arguments or checked-in files. Example
shape for a **partial, two-database backup**:

```json
{
  "connection": {
    "host": "127.0.0.1",
    "port": 5432,
    "user": "postgres",
    "password": "COPY_EXISTING_PASSWORD_PRIVATELY",
    "sslmode": "disable",
    "maintenance_database": "postgres"
  },
  "databases": [
    {
      "service": "knowledge",
      "database": "knowledge_knowledge",
      "owner": "knowledge",
      "connect_roles": ["knowledge"]
    },
    {
      "service": "temporal_visibility",
      "database": "knowledge_temporal_visibility",
      "owner": "temporal",
      "connect_roles": ["temporal"]
    }
  ]
}
```

A full platform inventory must contain all 13 database entries above; the tool
faithfully backs up the declared set and does not label a partial set as full.
Names are lowercase ASCII SQL identifiers, at most 63 bytes; no quoted unusual
identifiers, duplicate service names/databases or duplicate CONNECT roles are
accepted. At most 64 databases and 64 CONNECT roles per database are supported.

For Docker execution, `host` is resolved **inside the selected tool container**.
The example's `127.0.0.1` and `sslmode=disable` are appropriate only when the tools
run inside that isolated PostgreSQL container or another trusted local network
namespace. For a remote database, configure transport protection and the relevant
container-local TLS trust files. The CLI clears inherited `PG*` settings and passes
only its declared connection fields, preventing an unrelated `PGSERVICE` or
`PGOPTIONS` from silently changing the destination.

From the worktree, with absolute private paths and explicitly selected container
names stored in these operator variables:

```sh
python3 knowledge-docker/scripts/postgres_store.py backup \
  --config "$PG_SOURCE_CONFIG" --directory "$PG_BACKUP_DIRECTORY" \
  --docker-container "$PG_SOURCE_TOOL_CONTAINER"
python3 knowledge-docker/scripts/postgres_store.py verify \
  --config "$PG_SOURCE_CONFIG" --directory "$PG_BACKUP_DIRECTORY" \
  --docker-container "$PG_SOURCE_TOOL_CONTAINER"
```

Do not pre-create the final backup directory; it is created exclusively. The parent
must exist. The tool streams each `.dump` file, fsyncs it, records byte length,
SHA-256 and `pg_restore --list` SHA-256, then atomically publishes `manifest.json`.
A failed dump leaves a private incomplete directory with no completed manifest.
Choose a new backup path to retry; this tool does not erase partial artifacts.
Manifest size is bounded at 1 MiB, and an archive TOC at 64 MiB. Archive data itself
is streamed rather than loaded into memory. Tool errors contain fixed codes and
counts, not PostgreSQL stderr, SQL statements, data or passwords.

`verify` validates every declared archive, exact directory inventory, manifest
schema, bytes and TOC using the selected client tools; it does **not** connect to a
database. `--config` is still required for a uniform CLI, and must remain private.
Verification detects changes observed during its checks, not arbitrary later
modification. Preserve a read-only/otherwise frozen backup set during operations.

## Restore exclusively into new databases

Prepare a second private JSON config pointing at the **isolated recovery cluster**.
Keep the same service set and choose a new target name for every entry, for example
`restore_20260908_knowledge` and `restore_20260908_temporal_visibility`. Explicitly
set the target owner and CONNECT roles from the restored role inventory. The tool
rejects source database names, maintenance/system names and every existing target,
including an existing empty database. There is no `--clean`, DROP, overwrite,
merge or resume flag.

```sh
python3 knowledge-docker/scripts/postgres_store.py restore \
  --config "$PG_RECOVERY_CONFIG" --directory "$PG_BACKUP_DIRECTORY" \
  --receipt "$PG_RECOVERY_RECEIPT" \
  --docker-container "$PG_RECOVERY_TOOL_CONTAINER"
```

The receipt must be a new file in an existing private directory **outside** the
backup directory. Before the first CREATE, restore validates all archives, complete
service mapping, every target's absence, and every required LOGIN role. It then:

1. Creates a new database from `template0`, with its explicit owner and connection
   limit zero, and revokes PUBLIC database privileges.
2. Runs `pg_restore --single-transaction --exit-on-error --no-owner --no-acl --role`.
   A SQL error rolls back the entire database's restored schema/data. The newly
   created database itself remains empty and gated; no DROP is attempted.
3. After all databases restore, grants the explicit CONNECT roles and changes all
   connection limits to normal in one maintenance-database transaction. It writes
   the private completion receipt. It never changes any application DSN.

This is **not** a distributed transaction across databases. If database two fails,
database one may remain fully restored, database two remains empty after its SQL
rollback, and no completion receipt is written. All newly created databases remain
at connection limit zero if failure occurs before the final admission transaction.
If receipt storage fails after that final transaction, the databases can be fully
restored and open but there is still no receipt: do not cut over applications until
an operator has reconciled the recovery record. Timeout/interrupted Docker exec
may leave a remote pg_restore still running; keep the recovery cluster isolated,
inspect it with an administrator, and wait for/stop that owned process before
assessing the result. Do not interpret a generic tool timeout as proof of rollback.

A failed attempt must not be blindly retried against its existing databases. Select
another new set of names, or have the operator inspect and separately dispose of
only the failed recovery project. The tool never deletes partial databases. No
application cutover should happen without a complete receipt, row/schema checks,
original-key decryption checks, corresponding raw-store restoration, and successful
projection rebuild/replay in the separate recovery environment. This document does
not claim those remaining cross-system acceptance steps have run.

## Real Docker regression

The independent file `compose.postgres-restore-regression.yaml` is **standalone**.
Never merge it with the application `compose.yaml` and never use a live project name.
It pins `postgres:17.6-alpine`, uses a private volume/internal network, and publishes
no host ports. The seed container uses the existing locked
`sunny-knowledge/regression:v2-local` image and mounts current Python source read-only
so the real canonical initializer and immutability triggers are tested.

Run from the V2 worktree after building that existing regression image:

```sh
python3 -m unittest discover -s knowledge-docker/tests/postgres_restore \
  -p 'test_*.py' -v
```

The test runner generates a random `sk-pg-restore-*` project and test-only credentials,
starts only its own PostgreSQL and one-shot seed containers, then removes that exact
project with its volume. It verifies no project container, network or volume remains.
No test credential is a user/provider credential. No live service is stopped,
reconfigured, read, restored or dropped.

The eight real-PG tests cover complete row preservation for the real canonical
schema (including private input revisions, source references, audit and outbox),
restored immutability triggers, ciphertext/key continuity, per-service owners and
PUBLIC/CONNECT isolation, CLI private-file enforcement, duplicate/invalid manifests,
missing/corrupted/symlink archives, forbidden identifiers, incomplete mappings,
existing empty targets, missing roles and a real permission-denied pg_dump.
A valid archive containing a post-data index that raises a private exception on the
new target proves SQL rollback leaves zero application tables/functions, preserves
an earlier completed database, leaves connection limits closed, creates no receipt
and does not echo the private exception. This fixture is deliberately trusted SQL
for transaction verification, not a model or external-service integration test.