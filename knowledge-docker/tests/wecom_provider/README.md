# Bounded WeCom WebSocket protocol fixture

This is an explicit protocol simulator, not a connection to WeCom or a production
fallback. It accepts only ASCII BotIDs beginning with `fixture-` and the fixed
public test secret `wecom-protocol-only`. Subscription credentials are never
retained in bot state, returned by control APIs or logged. Use locally configured
test bots with these values; do not supply real bot credentials.

The existing regression image supplies locked FastAPI, uvicorn and websockets
versions. `server.py` listens on port **8765**. HTTP control is available only on
the Compose backend network; the WebSocket `/` route accepts only a loopback peer.
`compose.channel-regression.yaml` makes `channel-worker` share the provider's
network namespace, resets its standalone network list, and enables the worker's
explicit loopback exception for `ws://127.0.0.1:8765`. No host port is published.
The regular channel API, Agent, LLM and ordinary model configuration are untouched
by this overlay. Stack `compose.agent-regression.yaml` separately when the
synthetic Chat model is needed. The coordinator owns application deployment.

## Native protocol

- `aibot_subscribe` with `headers.req_id` and `body.{bot_id,secret}` receives an ACK
  with that request ID and `errcode:0` only for dedicated fixture credentials.
  Invalid credentials receive a constant nonzero ACK and a closed connection.
- `ping` receives an ordinary success ACK. The subscribe deadline is 3 seconds;
  after subscription, idle clients close after 90 seconds without a frame.
- Injected messages use native `aibot_msg_callback` bodies, including exact BotID,
  sender, message ID, chat type, ChatID and text.
- `aibot_respond_msg` requires a registered request ID and a strict stream body
  `{msgtype:"stream",stream:{id,content,finish}}`. Cumulative content and the final
  flag are preserved exactly. Reply frames are **not deduplicated**: an incorrect
  client resend remains observable in the reply history.
- A new subscription for an already connected fixture BotID sends the official
  `disconnected_event` to the old socket and replaces it. Explicit control
  disconnection only closes the socket with 1012, allowing ordinary retry.
- Every server write (ACK, callback, displacement and close) is serialized per
  socket. Writes have a 2-second network timeout. No callback/credential logging
  or HTTP access log is enabled.

## Internal control API

All responses use `Cache-Control: no-store`. No request body or error echoes
subscription credentials. Unknown routes and malformed/duplicate JSON fields are
rejected. These controls intentionally require only access to the isolated test
network and cannot reach any real bot.

`GET /healthz` returns `{status:"protocol_fixture",real_wecom:false}`.

`GET /fixtures/bots/{bot}` returns:

```json
{
  "bot_id": "fixture-example",
  "connected": true,
  "connection_count": 2,
  "injected_message_count": 1,
  "drop_next_response_ack": false,
  "dropped_response_ack_count": 0,
  "replies": [
    {
      "request_id": "request-1",
      "sequence": 1,
      "request_sequence": 1,
      "stream_id": "stream-1",
      "finished": true,
      "content": "Test answer",
      "ack_dropped": false
    }
  ]
}
```

`sequence` is the bot's global received-reply order; `request_sequence` is the
order within the request ID. Unknown valid fixture BotIDs return an empty,
disconnected snapshot without consuming a bot slot. Successful subscription and
reply history survive disconnection/reconnection, but are process-local and reset
when the provider restarts.

`POST /fixtures/bots/{bot}/messages` takes exactly:

```json
{
  "message_id": "message-1",
  "request_id": "request-1",
  "user_id": "test-wecom-user",
  "chat_type": "group",
  "chat_id": "test-group",
  "text": "Test question"
}
```

For private messages use `chat_type:"single",chat_id:""`. Group ChatID is required.
Success is **202** `{status:"injected",sequence}` after the callback write. The
same request and content can be deliberately redelivered; a reused request ID
with different content returns 409. A disconnected bot returns 409. An uncertain
callback write returns 503 rather than claiming successful injection. The
successful injection counter counts completed socket writes; sequence numbers
may have gaps after uncertain writes.

`POST /fixtures/bots/{bot}/disconnect` with `{}` closes the current connection and
returns `{status:"disconnected"}`. History remains intact.

`POST /fixtures/bots/{bot}/ack-policy` with
`{drop_next_response_ack:true}` suppresses exactly the next response-frame ACK.
The frame remains in `replies` with `ack_dropped:true`. Subscription and heartbeat
ACKs do not consume the flag. A subsequent response is acknowledged normally; this
allows tests to detect unsafe blind retransmission. Setting false cancels an
unused policy. The bot must have subscribed at least once.

## Capacity and validation

The defaults allow 32 bots, 64 admitted sockets, 256 distinct request IDs and 256
reply frames per bot, 8 MiB of total stored UTF-8 reply content, and 10,000
injection attempts / subscriptions per bot. Uvicorn caps concurrent connections
and tasks at 128. A frame or control body is at most 128 KiB, an injected question
32,768 UTF-8 bytes, and an individual response 20,480 UTF-8 bytes. IDs are bounded
to 256 ASCII characters. Reply finish must be a JSON boolean.

Capacity is explicit: injection exhaustion returns 429; bot subscription capacity
returns a nonzero ACK then closes; reply exhaustion closes with 1013. Existing
history is never silently evicted. Once a run exhausts the fixture, restart this
provider for a new isolated test session. The integration runner should choose
fresh `fixture-…` BotIDs and unique request IDs for independent questions.

## Validation

Tests start uvicorn on an ephemeral loopback TCP port **inside the existing
regression Docker image** and use real HTTP and WebSocket clients. They do not
start, reconfigure or restart application services.

```sh
knowledge-docker/scripts/compose.sh run --rm --no-deps \
  -v "$PWD/knowledge-docker/tests/wecom_provider:/fixture:ro" \
  regression sh -c 'ruff check /fixture && ruff format --check /fixture && python -m pytest /fixture/test_server.py -q -p no:cacheprovider'
```

2026-09-08: **8 tests passed, 4.29 seconds**, with no skips. They cover native
subscribe/ping/callbacks, Unicode cumulative/final replies, rejected real-looking
credentials with no echo, replayed callbacks, disconnect/reconnect, one-shot ACK
loss with visible repeated sends, strict control JSON, concurrent writes,
subscription displacement, per-bot/global-byte capacity and invalid finish types.
The initial preimplementation run failed because the fixture server did not yet
exist; final Docker log: `/tmp/knowledge-wecom-provider-final.log`.

Compose `config --format json` with both app/test profiles was checked without
printing secrets: worker network sharing/reset, fixed loopback URL, no host ports,
retained PostgreSQL dependency, provider health dependency and regression mounts
all matched the contract. This validates protocol simulation only; it does not
claim real WeCom, OAuth UI, model quality or full Agent integration acceptance.