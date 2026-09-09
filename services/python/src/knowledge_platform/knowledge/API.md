# Canonical knowledge HTTP contract

Run `uvicorn knowledge_platform.knowledge.app:app --host 0.0.0.0 --port 8080`.
Configuration and workload identity use `knowledge_platform.common`. The service owns only its PostgreSQL database. `models.initialize` creates its tables and database triggers preventing revision, source snapshot, audit and outbox mutation. An HTTP operation owns one database transaction and commits before its response is emitted.

All non-health routes require an Ed25519 `X-Service-Token` for audience `knowledge`. User-facing and delegated routes also require the unchanged end-user `Authorization: Bearer ...`; auth resolves the principal itself. Public requests cannot provide a trusted JSON principal. `/healthz` and `/readyz` are unauthenticated health probes. Interactive docs are disabled; `app.openapi()` provides the OpenAPI schema for client generation.

## Authorization and provenance

Every page read checks the page's live space AND resource permission, plus every original source resource on which the revision depends. Current page, exact historical version, history, review list, review action, citations and source snapshot reads all enforce this. Denied entries do not contribute to list counts or cursors. Authorization outages fail with 503, never an empty success.

`Revision.access_dependencies` and `Proposal.access_dependencies` are immutable-source snapshot IDs used only to preserve access restrictions, separate from displayed factual evidence. Each new version inherits the prior version's access dependencies and adds its new evidence. Removing a displayed citation or rolling back therefore cannot declassify derived text. A fully rewritten page may retain an earlier restriction; this API deliberately has no declassification bypass.

IAM resource registration is knowledge-only `POST /internal/v1/resources {space_id,resource_id}`. Knowledge first checks the delegated actor's space read/write permission, registers an inherited resource, then checks resource read/write. A failed knowledge transaction can leave an unused inherited IAM resource; it cannot grant broader access or publish content.

Workload capabilities:

- Public reads: gateway, agent, mcp, ingest, retrieval, graphiti, always delegated.
- Public proposal/create: gateway, agent, mcp, ingest, always delegated and write-authorized.
- Human approve/reject/rollback: gateway only, delegated and review-authorized.
- Snapshot registration, deterministic publishing, proven invalidation: ingest only, delegated.
- Projection materialization and outbox lease/ack: retrieval or graphiti only, no user identity needed. These capabilities must never proxy user reads.

## Common data

Keys are UUID strings or stable URL-path-safe namespaced IDs such as `page:payments:main-go`. All validity timestamps require an explicit UTC offset. JSON unknown fields are rejected.

`PageContent`:

```json
{
  "title": "Payment module",
  "markdown": "# Payment module\n...",
  "entity_type": "Module",
  "claims": [{
    "id": "claim:pay",
    "text": "Pay is defined in main.go.",
    "kind": "fact",
    "entity_type": "File",
    "evidence": [],
    "state": "valid",
    "valid_from": null,
    "valid_until": null
  }],
  "evidence": [],
  "state": "valid",
  "valid_from": null,
  "valid_until": null
}
```

`claims[].kind` is `fact|inference|gap`. Facts require at least one original `EvidenceRef` (the empty fact evidence in the illustration must be replaced). Claim IDs are unique within a page. States are `valid|stale|retracted`. Entity types are the ten approved schema entities. Markdown may contain an explicitly reviewed narrative without formal claims; automatic business conclusions/inferences are refused.

`EvidenceRef` pins an original canonical source snapshot:

```json
{
  "resource_id": "source:payments",
  "revision_id": "<knowledge snapshot UUID returned at registration>",
  "source_id": "git:payments",
  "source_revision": "<immutable Git commit or source version>",
  "path": "main.go",
  "start_line": 1,
  "end_line": 12,
  "valid_from": null,
  "valid_until": null,
  "kind": "code"
}
```

`revision_id` here identifies the canonical immutable source snapshot, not the containing Wiki revision. Containing page/revision IDs are returned separately. `kind` is `code|markdown|ticket|policy`; digests/Wiki summaries cannot become independent source evidence. Lines are one-based and inclusive, exact original text is preserved, and path must be relative without traversal. Every reference is checked against the registered snapshot's resource/source/version/path/kind and line bounds.

Lists use `{items:[...],next_cursor?:string}`; no unfiltered totals. Errors use `{error:{code,message}}` with no input or database credential echo. Conflict is 409, invalid evidence/request is 422, denied is 403, unavailable authorization/database is 503.

## Public pages and review

- `GET /api/v1/pages?space_id=&cursor=&limit=50` returns readable published page objects with nested `revision`. Unpublished drafts are not listed as published pages.
- `POST /api/v1/pages {id?,space_id,content,reason?}` returns 201 `{page,proposal}`. The page has `current_revision:null` and `revision_number:0`; no revision/outbox exists until review. A duplicate ID is 409.
- `GET /api/v1/pages/{page_id}` returns page metadata and `revision` (null for draft).
- `POST /api/v1/pages/{page_id}/proposals {base_revision,content,kind?,reason}` returns 201 proposal. `base_revision` is required, nullable only before first publication. Kinds: `formal_change|entity_merge|schema_change|digest`. Digests must retain original source support.
- `GET /api/v1/pages/{page_id}/revisions?cursor=&limit=50` returns authorized immutable revisions newest first. Cursor is the previous result's revision number.
- `GET /api/v1/pages/{page_id}/revisions/{revision_id}` returns an authorized exact immutable historical revision, including stale historical records for inspection.
- `GET /api/v1/reviews?status=pending&space_id=&cursor=&limit=50` returns proposals for which the caller can read all relevant content and review the page. Status is `pending|approved|rejected`.
- `POST /api/v1/reviews/{proposal_id}/approve {reason}` returns the new revision. It locks and compares the current base; a competing publication returns 409 and leaves the proposal pending. Resolved reviews cannot be processed again.
- `POST /api/v1/reviews/{proposal_id}/reject {reason}` returns the rejected proposal, with no published revision or outbox event.
- `POST /api/v1/pages/{page_id}/rollback {base_revision,revision_id,reason}` publishes a new revision restoring the requested payload. It never moves the pointer backwards or mutates history. Review permission and all old/new source permissions are required.

Revision responses include `id,page_id,number,base_revision,content,access_dependencies,created_by,publication_kind,proof,created_at`. Publication updates the revision, current pointer, audit and both consumer deliveries in one PostgreSQL transaction. IDs and version numbers are stable, and no last-writer-wins operation exists.

## Immutable sources and deterministic publication

`POST /internal/v1/sources/snapshots` (ingest + delegated bearer):

```json
{
  "source_id":"git:payments",
  "source_revision":"<commit>",
  "resource_id":"source:payments",
  "space_id":"finance",
  "path":"main.go",
  "kind":"code",
  "text":"package payments\nfunc Pay() {}\n",
  "sha256":"<SHA-256 of the exact UTF-8 text>",
  "object_key":"sources/payments/<commit>/main.go"
}
```

Returns 201 snapshot with `id`. Identical source ID/version/path registrations are idempotent. Changing text or metadata at the same immutable key is 409; checksum mismatch is 422. Ingest is responsible for creating the immutable raw S3 object before registration. Knowledge checks the text checksum and trusts the authenticated ingest workload for the storage manifest; it does not assert that it has independently fetched or verified S3. `text` is the original text snapshot used for exact citations, bounded to 4 MB per file; raw snapshots remain owned by ingest/S3.

`GET /api/v1/source-snapshots/{snapshot_id}` returns the readable exact snapshot text and metadata, excluding the storage object key. `GET /internal/v1/sources/{source_id}/revisions/{source_revision}?path=` returns `{items:[...]}` with each snapshot independently user-authorized. Projection consumers do not receive a workload-only raw source read capability.

`POST /internal/v1/pages/publish` (ingest + delegated bearer):

```json
{
  "page_id":"page:payments:main-go",
  "space_id":"finance",
  "base_revision":null,
  "content":{},
  "proof":{
    "compiler_version":"tree-sitter-v1",
    "schema_version":"v2",
    "snapshot_ids":["<all and only snapshot IDs referenced by content>"],
    "idempotency_key":"<source+revision+compiler+schema+page key>"
  }
}
```

`content` must be a valid `PageContent`. Source support must all be registered `code`; claim types are facts only and entities are Service/Module/File/Dependency. LLM content, inferences, and business conclusions go through proposals. Exact repeated key+request returns the original revision after live authorization; changed request with reused key is 409. Existing human-reviewed conclusions cannot be automatically overwritten, including after structured invalidation. Automatic publishing trusts the explicitly authorized ingest compiler identity and immutable proof; public users cannot select this route by setting a JSON mode flag.

`POST /internal/v1/pages/{page_id}/validity` (ingest + delegated bearer): `{base_revision,proof:EvidenceRef,claim_ids,state:"stale"|"retracted",reason}`. The proof must cover the entire registered ticket JSON, whose exact object is:

```json
{
  "type":"knowledge_validity_update",
  "page_id":"page:payments",
  "base_revision":"<current revision UUID>",
  "claim_ids":["claim:pay"],
  "state":"stale",
  "reason":"Structured ticket update"
}
```

Claim IDs are unique and sorted in the source object. The operation only changes validity states, retains claim text, adds original update evidence and audits the new revision. Natural-language tickets/LLM guesses are refused. Restoring validity requires a reviewed proposal.

## Delegated retrieval authorization

`POST /internal/v1/evidence/authorize {evidence:[EvidenceRef,...]}` returns `{decisions:[{evidence,allowed,space_id?,excerpt?,sha256?}]}`. `excerpt` is the exact authorized line slice. Invalid/revoked references return `allowed:false` without text. An authorization outage fails the request, rather than issuing negative or cached decisions.

`POST /internal/v1/pages/authorize {pages:[{page_id,revision_id}],include_historical?:false,as_of?:timestamp}` returns `{decisions:[...]}`. `allowed` means eligible for retrieval/model use: live page+all provenance authorization, current version unless explicit historical mode, and valid page/claim/evidence state/time. Readable decisions also include `authorized:true,is_current,state,valid_from,valid_until,current_revision,version,space_id`; revoked decisions contain only the input IDs and `allowed:false`. Exact history display should call the public revision API, which permits authorized inspection of stale versions. Retrieval must check `allowed` immediately before rerank/model/output, not merely the informational `authorized` flag.

## Projection and outbox protocol

`POST /internal/v1/outbox/lease {consumer:"retrieval"|"graphiti",limit?:20,lease_seconds?:30}` returns `{items:[{id,page_id,revision_id,version,event_type,payload,created_at,lease_token,lease_until,attempts}]}`. Consumer must equal authenticated workload. Each consumer owns a separate delivery for every event; locks use `FOR UPDATE SKIP LOCKED`. Expired deliveries are retried with a new fencing token. No bearer token is persisted in events or deliveries.

`GET /internal/v1/projections/pages/{page_id}/revisions/{revision_id}` returns immutable `content`, original support snapshot metadata, `page_id,space_id,revision_id,version,current_revision,current_version,is_current`, and current IAM policy fields:

- `read_clauses`: canonical conjunction of OR-subject clauses. Every page/source space ACL contributes a clause, every non-null tightening ACL contributes another. An empty clause denies all. Membership must satisfy **every** clause. These clauses include inherited source access dependencies.
- `policies`: the individual live policy snapshots and versions for revalidation.
- `acl_domain`: SHA-256 of canonical sorted JSON `{space_id,read_clauses}`. Graph partitions must use this full effective domain, never the page's ACL alone.

Consumers must ignore obsolete events (`is_current:false`) and guard their projection writes by canonical monotonic `version`; an older worker must never overwrite a newer projection. Tightening requires live delegated reauthorization before any user-facing use, even if an old projection remains indexed. This endpoint exists only for rebuilding projections and is unavailable to gateway/agent/MCP callers.

`POST /internal/v1/outbox/{event_id}/ack {lease_token}` succeeds only for the authenticated consumer holding an unexpired lease. Wrong/expired token is 409. Repeated successful ACK using the same token is idempotent. Consumers ACK only after a committed, version-guarded projection write (or deliberate obsolete-event skip). Failed processing leaves the delivery retryable after expiry.