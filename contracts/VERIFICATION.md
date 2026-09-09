# Contract generation verification

All commands below inspect source or run isolated test schemas/fixtures. No
application was deployed or restarted, no credentials were changed, and no private
repository was read for this increment.

The first export tests failed with the missing exporter, the seven native response
boundary cases failed with missing DTO modules, and the first client tests failed
with missing generator/client artifacts. Their red logs remain in
`knowledge-docker/artifacts/contracts-{export,responses,clients}-red.log`.

Final contract-specific evidence:

- `contracts-python-final.log`: **58 passed in 9.44 s** across contract export,
  actual ASGI serializer boundaries, generated client behavior, and all common
  authorization/security tests. The only warning is the locked Graphiti SDK's
  existing Pydantic deprecation warning.
- `contracts-verify-final.log`: complete `contract_verify.py` passed: **23 generated
  files** pass byte-for-byte drift checking; **31 contract + auth tests passed in
  6.38 s**; Go core client/native alias and platform transport packages passed race
  checks; isolated TypeScript compilation and actual Node request assertions passed.
  This local unified run used installed Go 1.26.1; the independent Docker run below
  validates the repository's pinned Go 1.24.7 toolchain.
- `contracts-go-docker.log`: pinned Go 1.24.7 Docker race checks passed for
  `internal/generatedcontracts/...` and `internal/platform`. Tests cover literal
  path/query encoding, frozen JSON, identity header rejection, original signed
  workload/user bearer, native `omitempty`/null/empty serialization and HTTP 403.
- `contracts-web-migration.log`: **9 lifecycle API tests passed** after migrating
  the metadata GET. `contracts-web-typecheck.log` passed the Web type check. Existing
  ApiClient, abort signal and strict metadata parser remain in use. This does not
  replace the earlier whole-Web or real-browser acceptance evidence.
- `contracts-ruff-final.log`: owned generator/runtime/DTO/test Ruff checks and
  format checks passed.
- `contracts-native-postgres.log`: the initial native-response compatibility run
  passed **19 real PostgreSQL/HTTP tests in 5.32 s**, covering Knowledge page/review
  and Agent management/run routes.

The broader Agent/Knowledge run `contracts-native-postgres-final.log` then recorded
**163 passed and one real deadline race failure**. It was not a response validation
failure and was not discarded as slow CI: a deadline heartbeat could cancel the
model task while it was saving its final state. That separate Agent repair and
its deterministic PG/TCP red/green evidence are documented in Agent/VERIFICATION.md.

The initial inventory intentionally retains **76 untyped backend response
operations**; their IDs are in `generated/coverage.json`. Generated DTO/operation
existence is not evidence that every consumer uses it. Production migration in
this increment is limited to Python identity/authorization, Go identity resolution,
and the Web lifecycle metadata GET. Remote GitHub Actions has been configured but
has not run in this local task. No model quality, Bot acceptance or retrieval
Recall claim is made by these contract tests.