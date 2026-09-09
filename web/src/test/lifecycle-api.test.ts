import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "../api";
import { lifecycleMetrics } from "./lifecycle-fixtures";
import { readLifecycle, withLifecycleDeadline } from "../lifecycle-api";

afterEach(() => vi.useRealTimers());
describe("strict lifecycle metadata", () => {
  it("requests the exact displayed revision and accepts independent dimensions", async () => {
    const get = vi.fn().mockResolvedValue(lifecycleMetrics());
    const signal = new AbortController().signal;
    const value = await readLifecycle({ get } as unknown as ApiClient, "page:p", "r1", signal);
    expect(get).toHaveBeenCalledWith("/pages/page%3Ap/lifecycle?revision_id=r1", signal);
    expect(value.support.registered_source_count).toBe(2);
    expect(value.freshness.score).toBe(0.5);
  });
  it.each([
    (x: ReturnType<typeof lifecycleMetrics>) => ({ ...x, page_id: "another" }),
    (x: ReturnType<typeof lifecycleMetrics>) => ({ ...x, revision_id: "another" }),
    (x: ReturnType<typeof lifecycleMetrics>) => ({ ...x, support: { ...x.support, fact_claims_with_original_evidence: 9 } }),
    (x: ReturnType<typeof lifecycleMetrics>) => ({ ...x, access: { ...x.access, scope: "enterprise" } }),
    (x: ReturnType<typeof lifecycleMetrics>) => ({ ...x, freshness: { ...x.freshness, score: 2 } }),
    (x: ReturnType<typeof lifecycleMetrics>) => ({ ...x, access: { ...x.access, mine_visits_7d: -1 } }),
    (x: ReturnType<typeof lifecycleMetrics>) => ({ ...x, validity: { ...x.validity, eligible: "true" } }),
  ])("rejects mismatched or misleading server metadata", async (change) => {
    const get = vi.fn().mockResolvedValue(change(lifecycleMetrics()));
    await expect(readLifecycle({ get } as unknown as ApiClient, "page:p", "r1", new AbortController().signal)).rejects.toBeInstanceOf(ApiError);
  });
  it("enforces a total deadline even if a loader ignores cancellation", async () => {
    vi.useFakeTimers();
    let signal!: AbortSignal;
    const pending = withLifecycleDeadline((value) => { signal = value; return new Promise(() => {}); }, new AbortController().signal);
    const rejected = expect(pending).rejects.toMatchObject({ code: "lifecycle_timeout" });
    await vi.advanceTimersByTimeAsync(10000);
    await rejected;
    expect(signal.aborted).toBe(true);
  });
});