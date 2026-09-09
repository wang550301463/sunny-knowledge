import { ApiClient } from "../api";
export const onboardingSelection = { space: "s", source: "source", page: "page", session: "session", run: "run" };
export function onboardingResponses(admin = false): Record<string, unknown> {
  const ref = { resource_id: "source:source", revision_id: "snapshot", source_id: "source", source_revision: "commit", path: "README.md", start_line: 1, end_line: 1, kind: "markdown" };
  const content = { title: "PRIVATE-WIKI", markdown: "PRIVATE-BODY", state: "valid", claims: [], evidence: [ref] };
  return {
    "/me": { id: "person", subjects: ["user:person"], permissions: admin ? ["platform_admin"] : [], auth_epoch: 7 },
    "/agents/models": { items: [{ id: "m", configuration_id: "mc", name: "可用Chat", provider_model: "qwen-plus", capability: "chat", state: "active", test_state: "passed", capabilities: { chat: true, tools: true, stream: true } }], next_cursor: null },
    "/models": { items: ["chat", "embedding", "rerank"].map(capability => ({ id: capability, configuration_id: "c-" + capability, name: capability, provider_model: "protocol-fixture-" + capability, capability, state: "active", test_state: "passed", capabilities: { [capability]: true, tools: true, stream: true }, credential: "MUST-NEVER-KEEP" })), next_cursor: null },
    "/spaces": { items: [{ id: "s", name: "PRIVATE-SPACE" }], next_cursor: null },
    "/spaces/s": { id: "s", name: "PRIVATE-SPACE" },
    "/sources": { items: [{ id: "source", name: "PRIVATE-SOURCE", space_id: "s", state: "active" }], next_cursor: null },
    "/sources/source": { id: "source", name: "PRIVATE-SOURCE", space_id: "s", resource_id: "source:source", state: "active", kind: "markdown", version: 2, preview: { source_revision: "commit", file_count: 1, total_bytes: 1 }, latest_task_id: "task", config: { content: "MUST-NEVER-KEEP" }, credential: "MUST-NEVER-KEEP" },
    "/tasks/task": { id: "task", source_id: "source", source_version: 2, space_id: "s", operation: "sync", status: "succeeded", stage: "completed", error_code: null, result: { items: [{ page_id: "page", path: "README.md", status: "published" }] } },
    "/pages/page": { id: "page", space_id: "s", current_revision: "revision", revision_number: 1, revision: { id: "revision", page_id: "page", content } },
    "/sessions": { items: [{ id: "session", title: "PRIVATE-SESSION", created_at: "2026-09-08T00:00:00Z" }], next_cursor: null },
    "/sessions/session/history": { items: [{ id: "run", session_id: "session", status: "completed", content_hidden: false, created_at: "2026-09-08T00:00:00Z" }], next_cursor: null },
    "/runs/run": { id: "run", session_id: "session", status: "completed", event_seq: 1, content_hidden: false, entrypoint: "web", answer_complete: true, actual_scope: ["s"], question: "PRIVATE-QUESTION", budget: {}, usage: {}, answer: { facts: [{ text: "PRIVATE-ANSWER", citation_ids: ["citation"] }], inferences: [], gaps: [] }, citations: [{ id: "citation", page_id: "page", revision_id: "revision", space_id: "s", evidence: ref, excerpt: "PRIVATE-EXCERPT" }] },
  };
}
export function onboardingAPI(values = onboardingResponses()) {
  const calls: string[] = [];
  return { calls, api: { get: async (path: string) => { calls.push(path); const key = path.split("?")[0]; if (!(key in values)) throw new Error("Unexpected " + path); return structuredClone(values[key]); } } as unknown as ApiClient };
}