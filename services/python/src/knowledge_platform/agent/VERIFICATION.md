# Agent backend verification

Verified 2026-09-08 in the isolated `codex/platform-v2` worktree. Source ownership for this bounded implementation is only `knowledge_platform/agent/` and `tests/agent/`; no other service's database tables are used.

## Executed evidence

- Host CPython 3.12 / locked runtime, **52 Agent tests passed in 13.94 seconds** against the actual Docker-hosted Agent PostgreSQL database, each test in its own random schema.
- **Docker regression: 72 passed in 12.86 seconds**, comprising the same 52 Agent tests plus 20 common tests. Log: `knowledge-docker/artifacts/agent-docker-regression.log` (local ignored artifact).
- `ruff check` and `ruff format` passed for Agent source/tests after explicit public EvidenceRef re-export was preserved; the shared original-evidence wire contract is reused.

Docker command, executed from the worktree (replace the host prefix only when using another checkout):

```sh
knowledge-docker/scripts/compose.sh -f compose.observability.yaml run --rm --no-deps \
  -v /Users/wangyunfeng/AiProjects/sunny-knowledge/.worktrees/platform-v2/services/python/src:/workspace/services/python/src:ro \
  -v /Users/wangyunfeng/AiProjects/sunny-knowledge/.worktrees/platform-v2/services/python/tests:/workspace/services/python/tests:ro \
  regression pytest services/python/tests/agent services/python/tests/common -q
```

The runner uses `TEST_AGENT_DATABASE_URL` for the Agent-owned database; it never prints the DSN or reads another domain database. Bind mounts ensure the tested source matches the worktree rather than an old application image. No existing containers/volumes were removed. An unrelated local model-provider Compose container was left intact.

## Covered boundaries

- Four explicit presets; bounded strict configuration and tool argument DTOs; no default model; safe Chat selection for an editor without exposing provider URLs or credential metadata.
- Agent configuration CAS under concurrent updates, immutable configuration/event database triggers, frozen run configuration, separate published pointer, owner-space read/write/grant actions, review administrator access without implicit content read.
- Principal-owned sessions/runs, persistent unique idempotency, immutable ordered events, cursor replay, independent feedback scope, cancellation and retry as a new run.
- Initial and tool scope intersection; currently unavailable write tools omitted from model capability lists; arguments rechecked after provider output; invented tools/graph seeds/citations refused.
- Actual PostgreSQL worker claims with fencing tokens; simultaneous claims cannot execute a run twice; stale worker cannot commit; encrypted delegation bound to a run and erased at terminal states; wrong encryption key and expired tasks fail closed.
- Process-loss recovery from committed validated tool intent without duplicating a model decision/counter; an in-flight unknown model call becomes explicit partial output without blind provider retry.
- Complete consulted provenance persists even when no citation is used in the final answer. Revocation redacts the entire run/history/export/digest and excludes it from future model context; auth outages and epoch changes fail whole operations.
- Strict original EvidenceRef reuse, live canonical Wiki revision and inherited-source authorization, exact original excerpts, separately labeled stale historical inspection, proposal-only canonical writes with base/CAS/idempotency/source support.
- Immutable extractive digests preserve run IDs/original support and count as zero independent evidence; repeated identical digest requests reuse the same immutable version.
- At most two simultaneous reads; failure cancels and awaits siblings. No orphaned HTTP read continues after the run fails.
- **Actual TCP** slow LLM SSE tests: explicit cancel closes the upstream socket and clears delegation; the absolute deadline closes a stalled model stream; worker shutdown closes the transport and restart reports its uncertain outcome without an extra provider request.
- **Actual TCP** SSE consumer disconnect leaves the durable run queued. A blocked send has a bounded lifetime and closes its iterator. A resumed slow event reader reauthorizes each frame, so revocation between frames cannot leak a prefetched terminal answer.
- Chunked oversized public JSON is rejected before parsing with safe 413 output. Validation failures do not echo private input. Workload allowlists reject channel/ingest and reject gateway access to MCP-only internal aliases.

These tests use signed service identities and simulated auth/retrieval/knowledge/LLM HTTP contracts in addition to real PostgreSQL and real TCP sockets. They verify this service's behavior and resource lifetime, **not** real Qwen quality or complete platform integration.

## Required remaining work and review status

Independent specification/code review has been requested from the parent task and is pending. The implementation is not claimed fully reviewed before that result.

Two approved Agent/entrypoint requirements are explicitly unfinished in this boundary version:

1. **Actual incremental answer generation:** current LLM SSE is consumed internally, execution/tool stages are streamed, and the answer is emitted after whole structured-output/citation validation. This is not answer-block/token streaming. The next isolated increment must separate tool planning from final-answer-only generation, validate complete answer blocks plus original citation mappings and current ACL before durable `answer_block` emission, and complete whole-answer validation. It must never expose unparsed JSON, planning content or provider reasoning.
2. **Channel broker/session isolation:** channel requests currently fail 403. The parent task is coordinating auth-owned binding/channel/group audience delegation and external conversation identity. Those constraints must be incorporated into Agent session identity and checked in every read/run before enabling channel calls. A JSON principal or cross-entrypoint session reuse is not acceptable.

Separate full-platform acceptance still includes deployed Gateway/Keycloak/IAM/Knowledge/Retrieval/Graphiti/LLM/Agent/MCP integration, real Qwen credentials, Web Playwright flows, real WeCom bot validation, 60 human-labeled quality questions, scale/performance/fault/restore scenarios. Passing these 72 backend/common tests does not replace those gates or declare sunny-knowledge V2 complete.