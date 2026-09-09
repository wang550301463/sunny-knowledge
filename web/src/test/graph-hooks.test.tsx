import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../api";
import { useGraphExplorer } from "../graph-hooks";
import { graphResult, seedResult } from "./graph-fixtures";
const params = { space_ids: ["space"], relation: { types: ["depends_on" as const], direction: "outgoing" as const, hops: 1 as const }, include_historical: false, as_of: null, known_at: null };
afterEach(() => vi.useRealTimers());
function client() {
  const request = vi.fn().mockResolvedValue(graphResult());
  const api = { request } as unknown as ApiClient;
  return { request, api };
}
describe("graph reauthorization lifecycle", () => {
  it("uses no-model traverse for five-second seed checks and does not cancel slow reads", async () => {
    vi.useFakeTimers();
    const { api, request } = client();
    request.mockResolvedValueOnce(seedResult());
    const { result } = renderHook(() => useGraphExplorer(api));
    await act(async () => { await result.current.search("query", params); });
    let complete!: (value: unknown) => void;
    request.mockImplementationOnce(() => new Promise((resolve) => { complete = resolve; }));
    await act(async () => { await vi.advanceTimersByTimeAsync(16000); });
    expect(request).toHaveBeenCalledTimes(2);
    expect(request.mock.calls[1][0]).toBe("/traverse");
    expect(request.mock.calls[1][1].signal.aborted).toBe(false);
    expect(JSON.parse(request.mock.calls[1][1].body)).toMatchObject({ seed_fragment_ids: ["fragment-a"] });
    expect(result.current.data?.items).toHaveLength(1);
    await act(async () => { complete(graphResult()); });
    expect(result.current.data?.items).toHaveLength(1);
  });
  it.each(["focus", "visibilitychange"])("immediately removes protected results on %s and fences an uncancellable old response", async (event) => {
    vi.useFakeTimers();
    const { api, request } = client();
    const { result } = renderHook(() => useGraphExplorer(api));
    await act(async () => { await result.current.traverse(["fragment-a"], params); });
    let oldComplete!: (value: unknown) => void;
    request.mockImplementationOnce(() => new Promise((resolve) => { oldComplete = resolve; }));
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    request.mockRejectedValueOnce(new Error("authorization unavailable"));
    await act(async () => { (event === "focus" ? window : document).dispatchEvent(new Event(event)); });
    expect(result.current.data).toBeUndefined();
    await act(async () => { oldComplete(graphResult()); });
    expect(result.current.data).toBeUndefined();
    expect(result.current.error).toBeDefined();
  });
  it("freezes execution parameters and supports explicit retry without rerunning search billing", async () => {
    const { api, request } = client();
    const { result } = renderHook(() => useGraphExplorer(api));
    const input = structuredClone(params);
    await act(async () => { await result.current.search("query", input); });
    input.relation.hops = 2 as never;
    await act(async () => { await result.current.refresh(true); });
    expect(request.mock.calls[1][0]).toBe("/traverse");
    expect(JSON.parse(request.mock.calls[1][1].body).relation.hops).toBe(1);
  });
});