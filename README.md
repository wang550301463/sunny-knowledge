# Go identity services

Go 1.22; binaries `go run ./cmd/gateway`, `./cmd/auth`, `./cmd/iam`, all listen on `:8080`. Deployment belongs to `../../knowledge-docker/`. Credentials and login remain in Keycloak; IAM stores only verified subject IDs, profiles and current authorization data.

## Runtime configuration

All binaries require `SERVICE_PRIVATE_KEY_FILE` (Ed25519 PKCS8 PEM) and `SERVICE_PUBLIC_KEYS_FILE` (JSON map from service ID to Ed25519 PKIX PEM). Each service mounts only its own private key. Workload JWTs use EdDSA, `kid=sub=caller`, issuer `knowledge-services`, target audience, required issued-at and expiry, maximum lifetime 60 seconds. Shared HS256 secret authentication is intentionally unsupported.

IAM: `DATABASE_URL`, `BOOTSTRAP_ADMIN_SUBJECT`, `AUTH_URL` (default `http://auth:8080`). Bootstrap applies only when that verified Keycloak subject is first provisioned. No request header, token group, realm role or profile field confers administration. Existing inactive users are never re-enabled by login. Store migration runs transactionally under a PostgreSQL advisory lock at startup.

Auth: `KEYCLOAK_ISSUER` (full public realm URL), `KEYCLOAK_INTERNAL_URL` (full internal realm URL), `KEYCLOAK_AUDIENCE`, `IAM_URL` (default `http://iam:8080`). OIDC discovery issuer must equal configured public issuer. JWKS requests are restricted to that issuer path and mapped to the configured internal path. Tokens must be RS256 with known `kid`, audience, issuer, subject and expiry. User scopes are returned by resolve; authorization requires `knowledge:read` for read and `knowledge:write` for write/grant/review. IAM public GET operations require read scope; mutations require write scope. Token membership/role claims are ignored. Current IAM data is read for every operation; caches are disabled.

Gateway: `AUTH_URL`, `KEYCLOAK_PROXY_URL` (origin, default `http://keycloak:8080`, preserves `/idp`), `WEB_URL` (default `http://web:8080`), and optional `<SERVICE>_URL`. Routes `/api/v1/{me,spaces,groups,departments,users,grants,audit}` to IAM; `{pages,reviews,revisions}` to knowledge; `{sources,tasks}` to ingest; `{search,traverse,timeline}` to retrieval; `models` to llm; `{agents,sessions,runs,feedback}` to agent; `{channels,bindings}` to channel; `/mcp` to MCP; `/idp` to Keycloak; remaining frontend paths to web. `/internal`, `/metrics`, `/debug`, `/.git`, `/actuator` are denied at the public gateway. Unknown API routes return 404. `/.well-known/` is forwarded to MCP for OAuth metadata. Public identity and forwarded headers are discarded; gateway authenticates through auth, forwards the unchanged user Bearer token, and mints a new workload token for the destination. Gateway uses an 8 MiB body limit, 5s connect timeout, 30s response-header timeout and 210s request deadline. SSE is flushed immediately; disconnect cancels upstream. Internal JSON body limit is 1 MiB, batch limit 1000. Request IDs propagate across internal calls.

`GET /healthz` is process liveness. Authorization endpoints fail closed when Keycloak/IAM is unavailable. No public API exposes key material or credentials.

## IAM public API

All routes require gateway workload authentication plus a valid user Bearer token. JSON errors use `{error:{code,message,request_id}}`. Responses omit inaccessible content. IDs are strings. Organization management requires current `platform_admin`; that permission **does not grant content access**. Creation of a space grants the actor explicit read/write/grant/review subjects. Group membership is flat and only existing users can be members; no nested-group cycles are possible.

- `GET /api/v1/me`: `{id,subjects,permissions,auth_epoch}`.
- `GET /api/v1/spaces`: `{items:[{id,name}]}` filtered by current read ACL, including for administrators.
- `POST /api/v1/spaces`: `{name}` → 201 `{id,name}`. Platform admin only.
- `GET /api/v1/spaces/{id}`: `{id,name}` when read-authorized.
- `GET /api/v1/groups` or `/departments`: `{items:[{id,name,kind}]}`. Platform admin only.
- `POST /api/v1/groups` or `/departments`: `{name}` → 201 `{id,name,kind}`. Platform admin only.
- `GET /api/v1/groups/{id}/members` (or departments): `{items:[User]}`. Platform admin only.
- `PUT` / `DELETE /api/v1/groups/{id}/members/{user_id}` (or departments): add/remove existing user; no body; 204. Platform admin only.
- `GET /api/v1/users`: `{items:[{id,name,email,active,permissions}]}`. Platform admin only. Users are first provisioned by verified Keycloak login; password creation/reset occurs in Keycloak.
- `PUT /api/v1/users/{id}`: `{name,active}` → 204. Platform admin only. `active` is required; disabled accounts fail authorization immediately.
- `GET /api/v1/grants?space_id=...&resource_id=...`: `{items:[{space_id,resource_id,action,subjects,version}]}`. Current grant permission required.
- `PUT /api/v1/grants`: `{space_id,resource_id?,action,subjects}` → effective read policy. Current space **and** resource grant permission required. `subjects` is required; `null` on a registered resource inherits the space; `[]` denies; space subjects cannot be null. Allowed subject forms are existing `user:<id>`, `group:<id>`, `department:<id>`. Input subjects are sorted/deduplicated and validated against IAM. Resource grants cannot broaden space access.
- `GET /api/v1/audit`: `{items:[{id,actor,action,target,detail,auth_epoch,occurred_at}]}`, newest 200. Platform admin only.

Mutation, epoch increment and audit record commit in one transaction. Failed authorization rolls back all three. Registration retries do not alter epoch/audit. Read decisions and current memberships use a consistent repeatable-read transaction. Batch auth detects epoch changes and asks callers to retry, never combines inconsistent authorization snapshots.

## Internal API

See `../../contracts/README.md` for shared contracts. All internal calls require audience-bound workload JWTs.

- Auth `/internal/v1/resolve`, `/authorize`, `/authorize-batch`: principal is derived from Bearer only; supplied JSON principal fields are rejected.
- IAM `POST /internal/v1/principals/ensure`: auth only, `{id,name,email}` → current principal. IAM never trusts memberships or roles in this payload.
- IAM `GET /internal/v1/principals/{sub}`: auth only → current principal.
- IAM `POST /internal/v1/check`: auth only, `{principal_id,action,space_id,resource_id?}` → `{allowed,auth_epoch,acl_domain,acl_version}`.
- IAM `POST /internal/v1/resources`: knowledge only, `{space_id,resource_id}` → 201 input. Knowledge checks the user's space write permission before registration. Registration is idempotent; unknown resources fail closed in checks and policy reads.
- IAM `GET /internal/v1/policies/{space_id}/{resource_id}` (or omit resource segment for space): auth/knowledge/ingest/retrieval/graphiti only → `{space_id,resource_id,space_read_subjects,resource_read_subjects,acl_version,acl_domain,auth_epoch}`.

An ACL domain is lowercase hex SHA256 of compact UTF-8 JSON with lexically sorted keys: `{"resource_read_subjects":null|[...],"space_id":"...","space_read_subjects":[...]}`. Arrays are sorted unique subject IDs. Resource ID is deliberately absent, so equal effective ACLs in one space share a domain. `null` and `[]` remain distinct. Decisions intersect space and optional resource ACL for the selected action. Policy domain always describes read visibility.

## Verification

`go test ./...` exercises Ed25519 identity, wrong-key/caller/audience/lifetime rejection, real RSA signatures with HTTP discovery/JWKS, malformed/expired/wrong issuer/wrong audience tokens, public principal injection, route isolation, SSE and cancellation, and ACL canonicalization. `go vet ./...` checks the Go code.

Set `IAM_TEST_DATABASE_URL` to a real PostgreSQL DSN and run `go test -race ./... -count=1`. Integration tests create isolated random schemas and drop only their own schemas; they do not truncate live app tables. They verify live membership revocation, inactive accounts, grant intersection, absent-vs-empty ACL, unknown-resource denial, audit/idempotency, management scope enforcement, spoofed identity headers and the full gateway→IAM→auth→IAM trust chain. Their OIDC server uses actual RSA/JWKS but is a protocol fixture; it is not evidence of live Keycloak integration. Actual Keycloak/PKCE Docker smoke belongs to the parent `knowledge-docker` regression suite.