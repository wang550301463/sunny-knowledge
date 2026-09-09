# Ingest HTTP API v1

Runtime: `uvicorn knowledge_platform.ingest.app:app --host 0.0.0.0 --port 8080`.
Worker: `python -m knowledge_platform.ingest.worker`. PostgreSQL is ingest-owned; S3
stores immutable original bytes and manifests; Temporal stores task UUIDs only.
Every non-health endpoint requires gateway's Ed25519 X-Service-Token and a live
Bearer token. All source/task reads enforce current space and source-resource ACL.
The worker has a separate Keycloak service account and explicit space/resource
read/write grants. No user bearer or source credential enters Temporal/history/logs.

## Requests

- `POST /api/v1/sources` → 201 Source: `{name,space_id,kind:"git"|"markdown"|"ticket",config,credential?}`.
- `GET /api/v1/sources?space_id=&cursor=&limit=50` → `{items:[Source],next_cursor:null|string}` (authorized items only).
- `GET /api/v1/sources/{id}` → Source.
- `PATCH /api/v1/sources/{id}` → Source: `{base_version:int,name,config,credential?}`.
  Creates a new immutable version even if config is unchanged; use this to refresh a remote Git source.
  An omitted credential preserves the existing encrypted credential; `credential:null` clears it.
- `POST /api/v1/sources/{id}/preview` → Preview: `{base_version:int}`.
  Fetches bounded remote content or uploaded body, persists exact immutable raw bytes,
  pins commit/manifest to that version, and returns file metadata/diagnostics. Repeated
  preview of the same version returns that pinned snapshot. No knowledge is published.
- `POST /api/v1/sources/{id}/sync` → 202 Task: `{base_version:int}`. Requires successful preview.
  Duplicate source+version+compiler+schema sync returns the original durable task.
- `DELETE /api/v1/sources/{id}` → 202 Task: `{base_version:int}`. Tombstones source,
  fences earlier jobs and schedules removal review proposals; preserves raw/audit/history.
- `GET /api/v1/tasks?space_id=&source_id=&cursor=&limit=50` → `{items:[Task],next_cursor:null|string}`.
- `GET /api/v1/tasks/{id}` → Task.

Git config: `{url:"https://host/org/repo.git",ref:"refs/heads/main"}`. Only HTTP(S)/SSH
URLs are accepted; URL userinfo, queries, fragments, command options and local/file
URLs are rejected. Credentials (write-only): `{username?,password?,ssh_private_key?,ssh_known_hosts?}`;
SSH requires configured username/private key/known hosts. Git uses a fresh bare repository,
no checkout/build/install/hooks/filter/smudge or inherited Git config. Submodules and
symlinks are recorded as skipped and never traversed. Public metadata excludes credentials.
Markdown config: `{path:"docs/guide.md",content:"# Guide\n..."}`.
Ticket config: `{path:"tickets/INC-1.json",content:"{...}"}`; content is an exact JSON
object encoded as a string, not reserialized. Generic tickets and Markdown create
unpublished drafts/review proposals. Exact structured `knowledge_validity_update`
tickets use knowledge's guarded validity endpoint. No generated business conclusions.
Uploads and every retained file are bounded to 4,000,000 bytes. Snapshot total bytes,
files, Git transfer disk, command timeout and preview concurrency are bounded.

## Responses

Source: `{id,name,space_id,resource_id:"source:<UUID>",kind,version,config,has_credential,
state:"active"|"deleted",created_by,created_at,updated_at,preview:null|{source_revision,file_count,total_bytes},latest_task_id:null|string}`.
`config` excludes uploaded content and all credentials; upload metadata includes `path`.
Preview: `{source_id,version,source_revision,files:[{path,size,sha256,kind:"code"|"markdown"|"ticket"|"raw",diagnostics:[string]}],diagnostics:[string],file_count,total_bytes}`.
Task: `{id,source_id,source_version,space_id,operation:"sync"|"delete",status:"queued"|"running"|"succeeded"|"review_needed"|"superseded"|"failed",stage,error_code:null|string,result:{},created_at,updated_at}`.
`result` includes published page/revision identifiers, proposals and bounded diagnostics.
Task status and retry checkpoints survive API and worker restarts. Task list never includes
credentials, upload content, raw keys, bearer tokens or unfiltered counts.
Errors: `{error:{code,message}}`; 401 unauthenticated, 403 forbidden or
worker_authorization_required, 404 not_found, 409 version_conflict/preview_required/
source_deleted/snapshot_conflict, 422 invalid_request/invalid_source/source_limit_exceeded,
503 dependency_unavailable/authorization_changed/database_unavailable.

## Configuration

Common SERVICE_NAME=ingest/DATABASE_URL/workload keys/auth and knowledge URLs apply.
`INGEST_ENCRYPTION_KEY` is base64 32-byte AES-GCM key. `INGEST_OIDC_TOKEN_URL`,
`INGEST_OIDC_CLIENT_ID`, `INGEST_OIDC_CLIENT_SECRET` configure client_credentials.
`INGEST_S3_ENDPOINT_URL`, `INGEST_S3_BUCKET`, `INGEST_S3_ACCESS_KEY_ID`,
`INGEST_S3_SECRET_ACCESS_KEY`, `INGEST_S3_REGION` configure raw storage.
`INGEST_TEMPORAL_ADDRESS` (default temporal:7233), `INGEST_TEMPORAL_NAMESPACE`
(default default), `INGEST_TEMPORAL_TASK_QUEUE` (default knowledge-ingest).
Worker fetches fresh machine tokens per activity/operation and reauthorizes canonical
reads/writes. Revocation/disabled IAM service principal fails closed. Source creator
is retained in source audit; canonical writes and execution audit name service principal.
S3 keys are SHA-256 addressed, writes are conditional put-if-absent and collision retries
verify actual stored bytes. Manifests include exact file hashes and Git commit.