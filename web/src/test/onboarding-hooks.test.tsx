import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../api";
import { useOnboarding } from "../onboarding-hooks";
import { onboardingAPI, onboardingSelection as selection } from "./onboarding-fixtures";
beforeEach(() => { vi.useFakeTimers(); vi.spyOn(document, "hasFocus").mockReturnValue(true); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });
async function flush() { await act(async () => { await vi.advanceTimersByTimeAsync(0); }); }
describe("onboarding live metadata boundary", () => {
  it("withdraws on blur, reads nothing while unfocused, and requires fresh focus authorization", async () => {
    const source = onboardingAPI();
    const { result } = renderHook(() => useOnboarding(source.api, selection));
    await flush();
    expect(result.current.data?.space?.name).toBe("PRIVATE-SPACE");
    act(() => window.dispatchEvent(new Event("blur")));
    expect(result.current.data).toBeUndefined();
    const count = source.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(source.calls.length).toBe(count);
    act(() => window.dispatchEvent(new Event("focus")));
    expect(result.current.data).toBeUndefined();
    await flush();
    expect(result.current.data?.space?.name).toBe("PRIVATE-SPACE");
  });
  it("coalesces a normal slow poll, then fences the whole read at ten seconds despite an uncancellable response", async () => {
    const source = onboardingAPI();
    let held = false, done!: (value: unknown) => void, signal: AbortSignal | undefined;
    const api = { get: (path: string, incoming: AbortSignal) => {
      if (held && path === "/me") { signal = incoming; return new Promise(resolve => { done = resolve; }); }
      return source.api.get(path, incoming);
    } } as unknown as ApiClient;
    const { result } = renderHook(() => useOnboarding(api, selection));
    await flush(); held = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(11_000); });
    expect(result.current.data?.space?.name).toBe("PRIVATE-SPACE");
    expect(signal?.aborted).toBe(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(4_001); });
    expect(result.current.data).toBeUndefined();
    expect(result.current.error).toMatchObject({ code: "onboarding_read_timeout" });
    expect(signal?.aborted).toBe(true);
    await act(async () => { done({ id: "other", permissions: [], auth_epoch: 7 }); });
    expect(result.current.data).toBeUndefined();
  });
  it("does not display an old space response after changing the selected route", async () => {
    let complete!: (value: unknown) => void;
    const source = onboardingAPI();
    const api = { get: (path: string, signal: AbortSignal) => path === "/spaces/s" ? new Promise(resolve => { complete = resolve; }) : source.api.get(path, signal) } as unknown as ApiClient;
    const { result, rerender } = renderHook(({ ids }) => useOnboarding(api, ids), { initialProps: { ids: selection } });
    await flush();
    rerender({ ids: { ...selection, space: "", source: "", page: "" } });
    await flush();
    await act(async () => { complete({ id: "s", name: "LATE-PRIVATE-SPACE" }); });
    expect(result.current.data?.space).toBeUndefined();
  });
});