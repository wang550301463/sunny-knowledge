The executable wires `HTTPAgentClient` to the auth context broker and dedicated
Agent channel facade. A missing injected adapter in an embedded worker remains
`UnavailableAgent`; it never creates credentials or substitutes administrator access.
`HTTPAgentClient` exchanges only `{context_id}` for a bounded read-only `skc_`
delegation, then creates a run using only `{question}`. It validates channel entry,
frozen Agent/configuration, exact run/session identity and scope intersection. SSE
events supply cursors and refresh signals; their payload is never rendered. Each
update comes from a freshly authorized cumulative RunView, with separate facts,
inferences and evidence gaps. Only platform run/citation links are generated.

Every update carries a live GET check inside the worker's bounded send fence. Old
claims and citation identities must remain present; cancellation/failure/partial
termination rejects queued running frames. Successful completion may retain a
verified prefix until the following final frame. EOF reconnects are read-only and
resume from the last processed event, at most three times. Uncertain create/send
outcomes never trigger another POST or a blind reply resend. Conversation commands
require explicit typed cancellation/clear acknowledgements.

`GET /internal/v1/runs/{run_id}/context` is auth-only and binds historical reading
to one persisted run/message/context. It preserves original expiry metadata while
checking current channel, account binding, group and clear generation. Unlike the
execution introspection route, it does not require the old lease or unexpired
message. It cannot be used to continue a run. Ambiguous mapping fails closed.
cumulative. `Update.BeforeSend` revalidates current source access after the
cancellable serial ACK gate and lease wait, inside the actual fenced socket write.
The worker checks the live local context through the same lease transaction.
Callbacks are trusted synchronous read operations: they must honor their context,
must not retain/use it concurrently, and must not invoke a channel mutation that
waits for the lease held by the send. This local transaction context is bound to
one exact Store instance and is never serialized to another service or database.
while a previous write is in progress. The lease wait, local/remote authorization
guard, socket write and fence commit share `TransportOptions.WriteTimeout`
(default 5 seconds), further shortened by caller cancellation/deadline. The guard
uses that context for every remote HTTP request; timeout fails closed before any
bytes are sent. Cancellation during an attempted socket write closes the socket
and retains conservative unknown-delivery semantics. Renewal failure cancels Agent work and
# Channel v1: WeCom connection and identity boundary

This Go service owns its PostgreSQL database, encrypted credentials, configuration
versions, registered chat metadata, two-way binding challenges, message receipts,
connection leases and delivery records. `cmd/channel` runs the HTTP API;
`cmd/channel --worker` runs independent long connections. No source content read
endpoint, raw message injection endpoint, attachment processing, proactive sending
or raw credential response is provided.

## Configuration

- `DATABASE_URL`: channel database/role only.
- `CHANNEL_ENCRYPTION_KEY`: base64 encoded 32-byte AES-256-GCM key, injected locally.
  Back up this key with the owned DB using the deployment's protected backup policy.
  Ciphertext is bound to its channel ID (question ciphertext to channel + message).
- `SERVICE_PRIVATE_KEY_FILE`, `SERVICE_PUBLIC_KEYS_FILE`: workload identity files.
- `AUTH_URL`, `IAM_URL`, `AGENT_URL`: default internal service URLs on port 8080.
- `CHANNEL_WS_URL`: deployment-owned endpoint; defaults to
  `wss://openws.work.weixin.qq.com`. API callers cannot select connection URLs.
- `CHANNEL_ALLOW_LOOPBACK_WS`: default false. The true setting only allows `ws://`
  for a literal loopback IP or localhost in protocol tests. TLS verification remains
  enabled for `wss://`, with TLS >= 1.2 and HTTP/1.1 ALPN. Ambient proxies are disabled.
- `CHANNEL_WEB_URL`: user-accessible WebUI base URL; binding link fragments contain
  the first challenge token. Full result links remain login/ACL protected.


## Authenticated configuration API

`/api/v1/*` requires signed **gateway** workload and an unchanged Web bearer.
The live auth resolver must return the required `knowledge:read` / `knowledge:write`
scope. Channel configuration and group management require `platform_admin`.
This is a configuration permission, not content access. Agent selection separately
calls the published-Agent verifier with the Web bearer; IAM separately checks
read + grant before registering any group audience.

- `GET /api/v1/channels`: `{items: Config[]}`, max 100. `GET /.../channels/{id}`
  returns one Config. No BotSecret or ciphertext field is serialized.
- `POST /api/v1/channels`: `{name,bot_id,bot_secret,agent_id,space_ids,enabled:false,
  base_version:0}`. Returns 201 with version 1. Bot ID is unique and immutable.
- `PUT /api/v1/channels/{id}`: same fields, exact `base_version`. Empty BotSecret
  retains the encrypted secret. Changed secret requires a new successful connection
  test before enabling. Every accepted save advances version and invalidates the
  current connection lease/contexts. CAS mismatch returns 409.
- `POST /api/v1/channels/{id}/test`: `{base_version}`. Disabled configuration only;
  202 `{status:"test_pending"}`. The worker obtains the same exclusive lease,
  performs the real protocol handshake and records tested_version. It does not
  process callbacks during the test. Enable only after `tested_version==version`.
- `GET /api/v1/channels/{id}/groups`: `{items: Group[]}`, max 100.
- `PUT /api/v1/channels/{id}/groups/{chat_id}`:
  `{base_version,audience_id,space_ids,enabled,acknowledged_public_to_group}`.
  Version 0 creates an immutable UUID registration `Group.id`. The group key is
  independent of mutable display names and is never reused for another chat.
  Enabling requires explicit acknowledgement plus successful IAM registration and
  verification for this exact channel/group/space set. Disabling is local and
  immediately invalidates prior contexts. Group scope must be a channel subset.

`Config` includes `id,name,bot_id,agent_id,agent_configuration_id,space_ids,version,
enabled,status,tested_version,secret_configured`. The Agent configuration ID comes
from the verifier, never from input JSON. Initial statuses are `disabled`,
`test_pending`, `connected`; runtime statuses include `connecting`, `reconnecting`,
`auth_failed`, `displaced`, `connection_failed`. A displaced bot is quarantined until
an explicit configuration version change. Persistent failures are bounded to 10
attempts with 1/2/4...30 second backoff.

## Two-way identity binding

1. `/绑定` in a private authenticated WeCom callback creates a random challenge and
   256-bit token, expiring after 5 minutes. A new challenge revokes the prior one
   for the same channel/external user. Only hashes are stored.
2. After Web login, `POST /api/v1/channel-bindings/claim` with
   `{challenge_id,web_token}` consumes the first proof once. The actor is taken from
   auth, not request JSON. Response `{confirmation_command:"/确认 <id> <proof>"}`
   contains a fresh second token; its hash is bound to that actor and external user.
3. Sending that command in the **same external user's private chat** consumes the
   second proof once and changes the binding. Cross-user, expired and replayed
   proofs fail. Rebinding increments version and invalidates old contexts/memory.
   Rebinding the same platform actor to another external account revokes the old one.
4. `DELETE /api/v1/channels/{id}/bindings/{external_user_id}` is self-service with
   the bound actor's actual bearer. Another actor, including a configuration admin,
   cannot unlink it through this self-service route.

Unbound contacts receive only help/binding instructions. Groups cannot start or
confirm binding challenges. A bound group contact also needs an enabled registered
group before any Agent context can be created.

## Auth broker introspection (no caller-asserted principal)

`GET /internal/v1/contexts/{opaque UUID}` accepts only a signed **auth** workload.
The worker alone creates contexts from durable accepted messages plus current
binding, channel, group and conversation records. Return fields:

```json
{
  "id": "opaque UUID", "user_id": "verified platform subject",
  "external_user_id": "WeCom subject", "channel_id": "channel UUID",
  "channel_version": 2, "binding_version": 1,
  "conversation_key": "sha256", "generation": 1,
  "capabilities": ["knowledge:read"], "message_id": "WeCom message ID",
  "agent_id": "Agent UUID", "agent_configuration_id": "published revision UUID",
  "space_ids": ["space UUID"], "chat_type": "single", "chat_id": "",
  "audience_id": "", "group_key": "", "group_version": 0,
  "expires_at": "2026-09-08T12:00:00Z"
}
```

Contexts last at most 180 seconds and are bound to the connection owner/fence.
Introspection rechecks expiry, active lease, channel enabled/version, binding
active/user/version, conversation generation, and registered group enabled/version.
Any mismatch returns 403; owned DB failure returns 503. It does not represent an
IAM permission decision. Auth must additionally resolve the live platform user and
verify group audience registration and every source's user **AND** group ACL.
Group subjects must never be appended to the user subject list as an OR.

Conversation identity hashes channel, external user, private/group type, chat ID,
binding version, channel version, group version and clear generation. This prevents
private/group/cross-group/rebinding history crossover.

## Domain dependencies

`HTTPVerifier.Agent` uses `GET agent /internal/v1/agents/{id}/published` with channel
workload + original Web bearer. Requires shared published revision and selected
space subset of its currently readable configuration. It freezes configuration_id.

`HTTPVerifier.Audience` uses `POST iam /internal/v1/channel-audiences`, with channel
workload + Web bearer, body `{id,channel_id,group_key,space_ids,
acknowledged_public_to_group:true}`, then `POST /internal/v1/channel-audiences/verify`
with channel workload only, body `{audience_id,channel_id,group_key,space_ids}`.
Verification must return `{allowed:true,version,auth_epoch}`. IAM update/revoke
lifecycle and Agent execution broker are integrated by the platform coordinator.

`AgentClient` is injected: `Execute(ctx,RunContext,question,idempotencyKey,emit)` returns
run ID; `Cancel(ctx,RunContext)` and `Clear(ctx,RunContext)` operate on that exact
conversation. The idempotency key is SHA-256(channel ID, durable message ID).
An implementation must exchange the opaque context ID through auth, freeze the
specified Agent version and preserve live authorization. `Update.Content` is

## Transport and reliability

Primary protocol: [official WeCom long connection documentation](https://developer.work.weixin.qq.com/document/path/101463)
and [official SDK, pinned source revision](https://github.com/WecomTeam/aibot-node-sdk/tree/80615b987ef69c6028ad764924609247c0725955).
The official protocol states that a group @ or a private message triggers
`aibot_msg_callback`. There is no independent mention flag in the SDK body. We
accept only this command from the authenticated subscribed socket with the exact
BotID; no public message callback API exists. Other events, attachments and arbitrary
frame commands do not become questions. The official group example includes the
bot mention in `text.content`; the question retains the original text.

`aibot_subscribe` carries bot_id/secret; `ping` every 30 seconds; ACK requires exact
req_id and explicit zero errcode. `aibot_respond_msg` preserves callback req_id,
uses one stream.id and cumulative content, ending with finish=true. Each reply
waits for ACK before any next reply. ACK timeout/ambiguous network failure closes
the connection: a late ACK cannot confirm a later frame with the same req_id.

Each BotID has one channel and a fenced PG lease. Before accepting callbacks or
writing socket frames the owner/fence/DB clock expiry are checked. A row lock is
held across the bounded socket write, so a successor cannot acquire ownership
closes the socket. Reconnection creates a new fence and invalidates old contexts.

Fenced binding/message/context/delivery operations reuse the transaction holding
the lease; they do not borrow a second pooled connection. This avoids a deadlock
where lease waiters exhaust the pool while its owner waits for another connection
to perform authorization. Clear, unbind, configuration and group changes use the
same lease order. A single-connection store supports the local lifecycle.
metadata, never derived content. Queue cancellation, a closed connection before
writing, revoked/expired guards and all other proven zero-attempt outcomes are
`UnsentError` and persist as `not_sent`; they are not network uncertainty. Missing ACK becomes `delivery_unknown` and is not

Messages are durably unique by (channel,message_id) before Agent dispatch. Replies
create a pending delivery record **before** writing; store only hash/sequence/final
resent. On crash recovery, pending deliveries become unknown and old received
messages become interrupted. This bounded core deliberately does not resume an
old callback's ambiguous send; the user sends a new question or inspects the run.
Read/write failure is safe and visible as a non-success status; no synthetic ACK.

Content is capped at 20,480 UTF-8 bytes on rune boundaries. Long content is an
explicit excerpt with a protected full-result link, not a fabricated summary.
`/帮助`, `/停止`, `/清空` operate in the current isolated conversation. No proactive
notification, attachment fetch, source export or unprotected citation is introduced.

Remote authorization still occurs while the lease is held, which is necessary
for local revocation ordering. Dependency overload or a remote service waiting
for a lease-changing operation can make authorization fail within the send bound;
no stale cached decision is substituted. The remote callback chain must remain
read-only, propagate cancellation and avoid lease mutations. This is a bounded
failure behavior, not a guarantee of availability during dependency saturation.
cumulative. `Update.BeforeSend` revalidates current source access after the
cancellable serial ACK gate and lease wait, inside the actual fenced socket write.
The worker checks the live local context through the same lease transaction.
Callbacks are trusted synchronous read operations: they must honor their context,
must not retain/use it concurrently, and must not invoke a channel mutation that
waits for the lease held by the send. This local transaction context is bound to
one exact Store instance and is never serialized to another service or database.
while a previous write is in progress. The lease wait, local/remote authorization
guard, socket write and fence commit share `TransportOptions.WriteTimeout`
(default 5 seconds), further shortened by caller cancellation/deadline. The guard
uses that context for every remote HTTP request; timeout fails closed before any
bytes are sent. Cancellation during an attempted socket write closes the socket
and retains conservative unknown-delivery semantics. Renewal failure cancels Agent work and

Fenced binding/message/context/delivery operations reuse the transaction holding
the lease; they do not borrow a second pooled connection. This avoids a deadlock
where lease waiters exhaust the pool while its owner waits for another connection
to perform authorization. Clear, unbind, configuration and group changes use the
same lease order. A single-connection store supports the local lifecycle.
metadata, never derived content. Queue cancellation, a closed connection before
writing, revoked/expired guards and all other proven zero-attempt outcomes are
`UnsentError` and persist as `not_sent`; they are not network uncertainty. Missing ACK becomes `delivery_unknown` and is not

Remote authorization still occurs while the lease is held, which is necessary
for local revocation ordering. Dependency overload or a remote service waiting
for a lease-changing operation can make authorization fail within the send bound;
no stale cached decision is substituted. The remote callback chain must remain
read-only, propagate cancellation and avoid lease mutations. This is a bounded
failure behavior, not a guarantee of availability during dependency saturation.