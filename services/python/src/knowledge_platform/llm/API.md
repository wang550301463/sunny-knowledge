# LLM API v1

Service module `knowledge_platform.llm.app:app`, port 8080. Database belongs only to llm. All routes except health/ready require a signed `X-Service-Token` targeted at llm. Public model routes accept only gateway. Every public request also forwards end-user Bearer unchanged to live auth `/internal/v1/resolve`; the response must contain current `platform_admin` permission and `knowledge:read` for GET or `knowledge:write` for mutations. Admin privilege grants model management only, never content access.

## Model administration

- `GET /api/v1/models?capability=chat|embedding|rerank&cursor=<model UUID>&limit=50` returns `{items,next_cursor}`; limit 1–100.
- `POST /api/v1/models` creates an active model and immutable configuration, HTTP 201.
- `GET /api/v1/models/{model_id}` returns current metadata.
- `PUT /api/v1/models/{model_id}` body `{base_configuration_id,config:<complete ModelConfig>,credential?:string}` performs CAS. Omit credential to preserve the old secret; a nonempty value rotates it. Explicit null on update is invalid. CAS failure is 409 `configuration_conflict`.
- `PATCH /api/v1/models/{model_id}/state` body `{base_configuration_id,state:"active"|"disabled"|"retired"}` creates another immutable version and audits the state change. Retired is terminal.
- `DELETE /api/v1/models/{model_id}?base_configuration_id=<UUID>` retires without destroying history.
- `GET /api/v1/models/{model_id}/versions?cursor=<version number>&limit=50` returns safe version history, descending, `{items,next_cursor}`.
- `GET /api/v1/models/{model_id}/audit?limit=50` and `/usage?limit=50` return recent safe administration records and provider invocation outcomes. No prompts, responses, credentials or provider error bodies.
- `POST /api/v1/models/{model_id}/test` body `{configuration_id,test_tools:false,test_stream:false}` sends fixed, bounded diagnostic text to the configured provider. Must target current configuration. Response `{id,configuration_id,test_state:"passed"|"failed",capabilities:{chat?:bool,embedding?:bool,rerank?:bool,tools?:bool,stream?:bool},dimensions:int|null,error_code:string|null,tested_at}`. Unknown/unrequested subcapabilities are absent, not claimed. A concurrent configuration edit makes the test result 409 rather than applying it to a new version. A provider rejection returns HTTP 200 with test_state failed; test endpoint execution success is distinct from provider success.

`POST` fields (ModelConfig plus optional `credential`; never echo credential):

```json
{
  "name": "Enterprise embedding",
  "provider": "openai",
  "provider_model": "YOUR_EXPLICIT_MODEL_ID",
  "base_url": "https://YOUR_PROVIDER/v1",
  "capability": "embedding",
  "dimensions": 1024,
  "request_dimensions": false,
  "max_input_chars": 131072,
  "max_batch_size": 64,
  "max_output_tokens": 4096,
  "max_response_bytes": 8000000,
  "timeout_seconds": 60,
  "max_retries": 2,
  "concurrency_per_replica": 4,
  "max_queue_per_replica": 16,
  "chat_token_parameter": "max_completion_tokens",
  "credential": "PRIVATE_INPUT_ONLY"
}
```

Provider names: `openai` for chat/embedding, `cohere` for rerank. Base URL includes API version prefix: OpenAI `https://api.openai.com/v1`; Cohere `https://api.cohere.com/v2`. Configurable compatible hosts use the same documented contract. No inferred model choice or endpoint/version fallback. HTTPS required by default; credential-in-URL, query and fragment rejected. Operators may explicitly enable `LLM_ALLOW_HTTP_PROVIDERS=true` for trusted local model endpoints. A provider credential can be omitted for an explicitly configured unauthenticated endpoint. Registry extension happens in trusted Python startup, never by user-supplied import paths.

Embedding dimensions must be explicitly configured; the probe verifies actual output matches. `request_dimensions=false` validates natural provider dimensions without sending the optional dimensions parameter; set true only for models documented to support it. Other capabilities must use dimensions null. `chat_token_parameter` selects exactly one provider-supported parameter; no retry with alternate parameters. Character limits are character limits, not estimated token counts; provider context limits still apply. Names 200 chars; batch at most 256; individual embedding/rerank text at most 65536 chars; input total at most configured cap, hard ceiling 1M chars; chat at most 100 messages/20 tools; output at most 32768 tokens; deadline 0.01–180 seconds; retries 0–3; configured cap at most 100 concurrent/200 queued per model per replica.

Model response contains ModelConfig fields plus `{id,configuration_id,version,state,has_credential,created_at,created_by,test_state:"untested"|"passed"|"failed",capabilities,tested_at,test_error_code}`. Every create, config edit, credential rotation, and state change advances immutable configuration_id. Older versions remain available for pinned runs while the model is active. New versions reset test state to untested. Old credentials are encrypted at rest and never returned from any version endpoint. Model current state is checked at invocation and again after queueing.

## Internal client contract

`GET /internal/v1/models?capability=...` discovers active metadata, same pagination and safe model shape. Callers are restricted by requested capability:

- chat: agent, graphiti
- embedding: retrieval, ingest, graphiti
- rerank: retrieval

Gateway and MCP cannot invoke/discover inference. Caller services own source authorization and must reauthorize content before sending it; the LLM service accepts only authenticated workload identities, not caller-supplied public principals. Configuration selection is explicit, no default model or fallback.

`POST /internal/v1/chat`:

```json
{
  "configuration_id": "IMMUTABLE_CONFIGURATION_UUID",
  "messages": [{"role":"user","content":"Question"}],
  "tools": [],
  "max_output_tokens": 1024,
  "temperature": 0.2,
  "stream": false
}
```

Message roles system/developer/user/assistant/tool. Text-only content. Assistant tool calls use `{id,type:"function",function:{name,arguments:<JSON object string>}}`. Tool messages require `tool_call_id`; only assistant messages may include tool_calls. Tool definitions use OpenAI `{type:"function",function:{name,description?,parameters:<JSON object schema>,strict?}}`. Optional `response_format` accepts `{type:"json_object"}` or `{type:"json_schema",json_schema:...}` for Graphiti structured responses. Extra request/message fields are rejected, including hidden reasoning.

Nonstream response `{configuration_id,invocation_id,content:string|null,tool_calls:[],finish_reason:"stop"|"length"|"tool_calls"|"content_filter",usage:{prompt_tokens?,completion_tokens?,total_tokens?},request_id:string|null}`. Tool arguments must be JSON objects, call IDs unique and names bounded. The service validates provider structure; callers must still validate tool arguments against their tool schemas and enforce allowed tool names.

For `stream:true`, HTTP `text/event-stream`; each event has `event: <type>` and JSON `data`:

- `content_delta`: `{type,configuration_id,invocation_id,delta:string}`.
- `tool_call_delta`: `{type,configuration_id,invocation_id,index,tool_call}`. Emits only completed validated tool calls; consumers must not execute unvalidated argument fragments.
- `completed`: nonstream response plus `type:"completed"`; includes full final content, calls, usage, provider request ID and finish reason. Completion is persisted before delivery.
- `error`: `{type:"error",error:{code,message}}` safe terminal error after headers accepted. Earlier config failures use ordinary non-200 JSON errors. Incomplete streams never emit completed. Consumer disconnect/cancel closes the upstream stream and releases the permit.

No reasoning fields or events are forwarded. The service cannot identify reasoning embedded by a provider directly in ordinary content; only the documented content channel is returned. No retry after stream consumption starts, including a read failure before the first text delta.

`POST /internal/v1/embeddings` body `{configuration_id,input:[string]}` → `{configuration_id,invocation_id,embeddings:[[number]],dimensions,usage,request_id}`. Results are reordered by provider index and validated for exact count, unique indices, explicit dimension and finite numeric values (no bool/string coercion).

`POST /internal/v1/rerank` body `{configuration_id,query,documents:[string],top_n?:int}` → `{configuration_id,invocation_id,results:[{index,score}],usage:{search_units?},request_id}`. Exact requested count, unique in-range indices, finite numeric scores; no provider document echo. Caller should associate returned indices with its already-authorized candidate list.

## Failure, durability, operations

Errors use `{error:{code,message}}`; validation details never echo raw input. Responses carry service-generated `X-Request-ID`. JSON `request_id` is a bounded provider request ID, and `invocation_id` locates the durable record. Opaque provider error payloads are never returned or logged. Credentials are AES-GCM via common SecretBox, bound to immutable configuration UUID; SQL parameters hidden. Set `CREDENTIAL_ENCRYPTION_KEY` to private base64 32 bytes; startup fails closed without a valid key. Back up this key separately with the database; replacing it alone makes existing credentials unusable. Model credential rotation creates another encrypted version; encryption-key rotation requires a deliberate migration preserving immutable history.

Each invocation is durably started before provider IO and has an append-only usage outcome containing configuration ID, caller, outcome, usage counts, provider request ID and duration. Interrupted processes leave a started invocation without an outcome; `/usage` reports unknown rather than fabricating failure/success. A metadata database failure prevents successful result delivery. Provider prompts, responses and keys are not stored. Test probes generate real provider requests and may incur provider usage.

**Limits are explicitly per process/replica, per model**, shared across immutable configurations within that process. N replicas or Uvicorn workers have up to N times configured concurrency and queue capacity. There is no cluster-wide capacity claim. Size deployment replica count accordingly; an external provider quota remains authoritative. New requests use current model concurrency settings while inference parameters/credentials remain pinned. Queue plus provider consumption share the configured deadline. Retry only explicit 429/500/502/503/504 responses, at most configured retries, bounded exponential backoff or capped numeric Retry-After, all inside the deadline. Network/timeout errors have unknown processing outcome and are never automatically resent. No redirects; outgoing client ignores ambient HTTP proxy environment.

ASGI request body hard limit defaults to 2 MB (`LLM_MAX_REQUEST_BYTES`). Provider responses bounded by config max_response_bytes, including full stream. `/healthz` is process liveness; `/readyz` checks service PG. Normal deployment uses only the llm database role and service identity from common settings.

## Protocol sources reviewed

Reviewed official references on 2026-09-08: [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create), [streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events), [embeddings](https://developers.openai.com/api/reference/resources/embeddings/methods/create), [Cohere v2 rerank](https://docs.cohere.com/reference/rerank). HTTPX 0.28.1 provides stream context cleanup, typed timeouts, disabled redirects and bounded connection pools; no provider SDK or extra dependency used. Local protocol simulations are not external model acceptance; real credential tests remain a separate required deployment acceptance step.