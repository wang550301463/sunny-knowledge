# Channel core verification

2026-09-08, branch `codex/platform-v2`. This is the bounded channel transport/config/
binding implementation; it does not certify real WeCom, full Agent broker, UI or
whole-platform acceptance.

Protocol sources were read, not executed: WecomTeam/aibot-node-sdk commit
`80615b987ef69c6028ad764924609247c0725955` (src/ws.ts, src/types/message.ts,
src/types/api.ts, src/client.ts), and official document path 101463. The official
site's full SSR HTML was obtained with TLS-verified curl because web text extraction
failed. Gorilla websocket official latest release was verified as v1.5.3; the
coordinator pinned it in go.mod/go.sum.

Reproducible real middleware command, from worktree root:

```sh
knowledge-docker/scripts/compose.sh run --rm --no-deps \
  -v "$PWD/services/go:/workspace/services/go" \
  go-regression go test -race ./internal/channel ./cmd/channel -v
```

The Compose runner injects `TEST_CHANNEL_DATABASE_URL` using the independent
channel DB role. Every PG test creates a random schema and cleans only that schema.
Local httptest servers perform real WebSocket upgrade/framing; no real BotID,
BotSecret or external bot endpoint is used. No dependencies of ingested repositories
were built/installed and no non-test records were removed.

Latest complete Docker run at 19:24 CST: **18 tests pass, 0 skipped, race clean**
(3.444s package time). Tests cover authenticated handshake; cumulative UTF-8 stream
and final bit; ACK timeout and no resend; server displacement; before-send guard;
PG config CAS; durable dedup; binding double proof, replay, wrong external user,
expiration, rebind and revocation; group registration and version invalidation;
unknown delivery recovery; fenced-write lock and old-owner rejection; end-to-end
worker duplicate/unbound help; reconnect dedup; and lease loss cancelling Agent
work/closing old socket. Host `go vet ./internal/channel ./cmd/channel` also passed.

Follow-up source changes add bounded nested JSON validation, explicit TLS ALPN and
consistent PG lock ordering; the final fresh full run is recorded by the coordinator
before integrating. Individual transport tests use the same actual websocket
connection implementation as the worker, not a mocked socket API.

Not yet validated here: actual WeCom account private/group @ behavior and response
rendering; client reachability of configured Web URL; real channel-to-auth-to-Agent
broker and IAM audience lifecycle; UI bind/channel flows; secrets rotation under an
existing long-running bot. Worker intentionally defaults to UnavailableAgent until
the authenticated context broker is connected. Simulator evidence cannot replace
that required separate acceptance.