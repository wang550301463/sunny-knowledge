import { describe, expect, it } from "vitest";
import { ApiClient, ApiError } from "../api";
import { loadOnboarding, onboardingChecks, readSelection } from "../onboarding-api";
import { onboardingAPI, onboardingResponses, onboardingSelection as selection } from "./onboarding-fixtures";
describe("onboarding real evidence contracts", () => {
  it("uses safe Chat discovery for ordinary users and retains no secret, source, or answer content", async () => {
    const { api, calls } = onboardingAPI();
    const data = await loadOnboarding(api, selection, new AbortController().signal);
    expect(calls.some(path => path.startsWith("/models"))).toBe(false);
    expect(onboardingChecks(data)).toMatchObject({ chat: true, space: true, preview: true, sync: true, wiki: true, answer: true });
    expect(data.retrievalVerification).toBe("unknown");
    for (const privateValue of ["MUST-NEVER-KEEP", "PRIVATE-BODY", "PRIVATE-ANSWER", "PRIVATE-QUESTION", "PRIVATE-EXCERPT"])
      expect(JSON.stringify(data)).not.toContain(privateValue);
  });
  it("separates simulated model probes from actual retrieval readiness", async () => {
    const { api } = onboardingAPI(onboardingResponses(true));
    const data = await loadOnboarding(api, selection, new AbortController().signal);
    expect(data.adminModels?.every(model => model.simulated)).toBe(true);
    expect(data.retrievalVerification).toBe("unknown");
  });
  it("does not count an old source task or pending review as successful publication", async () => {
    const values = onboardingResponses();
    Object.assign(values["/tasks/task"] as object, { source_version: 1, status: "review_needed" });
    const data = await loadOnboarding(onboardingAPI(values).api, selection, new AbortController().signal);
    expect(onboardingChecks(data).sync).toBe(false);
    expect(data.task?.currentVersion).toBe(false);
  });
  it("requires current Wiki provenance and a referenced answer clause, not just a loose citation", async () => {
    const values = onboardingResponses();
    const run = values["/runs/run"] as { answer: { facts: unknown[] } };
    run.answer.facts = [];
    const data = await loadOnboarding(onboardingAPI(values).api, selection, new AbortController().signal);
    expect(onboardingChecks(data).answer).toBe(false);
    (values["/pages/page"] as { revision: { content: { evidence: unknown[] } } }).revision.content.evidence = [];
    const changed = await loadOnboarding(onboardingAPI(values).api, selection, new AbortController().signal);
    expect(onboardingChecks(changed).wiki).toBe(false);
  });
  it.each(["partial", "channel", "hidden"])("does not complete with a %s result", async kind => {
    const values = onboardingResponses();
    const run = values["/runs/run"] as Record<string, unknown>;
    if (kind === "partial") Object.assign(run, { status: "partial", answer_complete: false });
    if (kind === "channel") run.entrypoint = "channel";
    if (kind === "hidden") Object.assign(run, { content_hidden: true });
    const data = await loadOnboarding(onboardingAPI(values).api, selection, new AbortController().signal);
    expect(onboardingChecks(data).answer).toBe(false);
    expect(JSON.stringify(data)).not.toContain("PRIVATE-ANSWER");
  });
  it("rejects a changed final authorization epoch rather than combining stale successful reads", async () => {
    const original = onboardingAPI().api;
    let me = 0;
    const api = { get: async (path: string, signal: AbortSignal) => {
      const value = await original.get<Record<string, unknown>>(path, signal);
      return path === "/me" && ++me > 1 ? { ...value, auth_epoch: 8 } : value;
    } } as unknown as ApiClient;
    await expect(loadOnboarding(api, selection, new AbortController().signal)).rejects.toEqual(new ApiError(409, "authorization_changed"));
  });
  it("persists only bounded opaque selection IDs and ignores local success flags", () => {
    expect(readSelection({ ...selection, completed: true, spaceName: "PRIVATE", run: "x".repeat(513) })).toEqual({ ...selection, run: "" });
  });
});