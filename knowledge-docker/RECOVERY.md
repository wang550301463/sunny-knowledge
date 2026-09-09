# Platform recovery and acceptance boundary

PostgreSQL is the authority for knowledge, revision lineage, authorization and
service metadata. S3 contains the original immutable object set. Elasticsearch
and Graphiti/Neo4j are derived projections. The separate database and raw-store
tools have real integration tests; a complete coordinated restore followed by
ES/graph rebuild is **not yet verified**. Do not interpret their separate green
tests as a full platform recovery result.

## Preserve the complete recovery set

- All 13 service databases listed in [POSTGRES_BACKUP.md](POSTGRES_BACKUP.md),
  including Keycloak, Temporal and Temporal visibility. Services retain their
  distinct database owners and credentials.
- The complete ingest raw bucket, with a verified content-addressed manifest,
  following [RAW_BACKUP.md](RAW_BACKUP.md).
- The original private environment, workload signing identities/public registry,
  encryption keys, OIDC client secrets, and approved public origin configuration.
  Store these separately on protected storage; no script prints them. A newly
  generated encryption key cannot decrypt existing model/Bot/Git credentials.
- Exact application/infrastructure image IDs, pinned source revision, service
  database mapping, bucket selection and the operator's quiescence record.

The recovery set contains private knowledge and credentials. Its storage access
is separate from platform ACL checks. Do not put it in Git or a public artifact
directory. A manifest verifies bytes and completeness for its declared scope;
it does not make an arbitrary SQL archive trustworthy.

## Establish a coordinated backup point

1. Stop accepting new user runs, mutations, MCP writes and channel messages.
   Record active runs and channel sends whose outcome is uncertain. Do not
   acknowledge or resend a Bot response merely because recovery is starting.
2. Drain or stop the named application writers and workers, including channel,
   ingest, Agent, lifecycle and projection processes. Use the exact deployment's
   service list and state; retain the distinction between an already stopped
   worker and one stopped for backup. Wait for termination rather than assuming
   an interrupted command cancelled a remote database transaction.
3. Stop the remaining service processes, Keycloak and Temporal after their
   clients have stopped. Keep PostgreSQL and the raw S3 service running for the
   backup tools. Verify that no independent operator or job can write during
   this window. Separately dumping databases while writers remain active does
   not establish a consistent cross-service backup.
4. Back up every declared database with `postgres_store.py` and verify all
   archives. Back up and verify the raw bucket with `snapshot_store.py`. Record
   their manifest hashes and the image/configuration inventory in the same
   private recovery record. Do not label a partial service inventory as full.
5. Resume only the processes that were running before this window, following
   their dependency order. Reconcile any send whose result was uncertain before
   deciding whether further channel action is appropriate.

Quiescence is currently an explicit operator procedure. Neither backup tool
stops services or enforces a distributed write barrier on its own.

## Restore into a separate deployment

1. Create an isolated Compose project, network, ports and fresh volumes. Preserve
   the original project. Prepare the original database roles and private keys;
   do not run credential regeneration against the restored configuration.
2. Restore the declared databases into **new names** using the reviewed
   PostgreSQL tool. Every archive must validate before creation. It keeps
   ordinary connections closed until the complete declared database set has
   restored. A failure leaves explicit partial state for reconciliation; it
   neither drops that state nor reports a completed group restore.
3. Restore the raw objects into a pre-created empty recovery bucket. An exact
   partial restore may be resumed; conflicting objects are never overwritten.
   Verify the final complete byte set and inventory while writers remain stopped.
4. Check original credential decryption and service ownership. Map each service
   to its own restored database and the recovery bucket. Start identity and
   read services against those targets, keeping ingress and background writers
   closed. Configure the recovery origin and issuer together; an origin change
   is not permission to accept tokens issued for the original audience.
5. Use fresh retrieval/graph catalog databases, an empty ES index and a new
   Graphiti namespace. Give projection workers their existing service identities
   and only the restored grants. Invoke each worker's explicit `--rebuild`
   enumeration against the **restored** canonical service, then continue its
   normal durable outbox processing. Restore acknowledgment rows do not imply
   that an empty replacement index already contains the acknowledged revisions.
6. Verify exact source bytes and revisions, current pointers, original/input-Wiki
   lineage, live ACL filtering, graph adjacency/time behavior and pending outbox
   reconciliation. Obsolete events must not replace newer projections. A graph
   capability gap must remain visible until the matching projection is present.
7. Open the recovery deployment only after the acceptance checks below pass.
   Re-enroll explicitly configured lifecycle spaces for the current UTC day;
   startup does not invent snapshots for historical days missed during downtime.
   Reconcile channel message/run state and uncertain sends before enabling bots.

## Required end-to-end evidence

The remaining recovery acceptance must use an isolated, known fixture and record:

- A pre-backup reviewed Wiki revision, original S3 bytes, precise source
  references, source/document restrictions, and an already acknowledged
  retrieval/graph projection.
- Actual database dump/restore and raw backup/restore with the original
  encryption keys; byte hashes and canonical immutable records match afterward.
- An initially empty replacement index/catalog/graph. Explicit canonical
  enumeration reconstructs the current fragments and real adjacency despite
  pre-backup outbox acknowledgments. Replaying or duplicating old events does
  not lower the current revision.
- The same authorized search/source/path result after rebuilding, with a
  separate denied actor/source case. Live revocation still withdraws results.
- A failure or interrupted restore/rebuild whose uncertain state is reported,
  followed by an explicit safe recovery. No unverified restoration or fixture
  cleanup is counted as success.

Protocol model fixtures may verify this reconstruction mechanism. They cannot
establish real Qwen answer quality, the 100,000-fragment latency target or real
WeCom acceptance. Each of those has a separate acceptance record.
