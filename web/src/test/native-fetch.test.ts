import { afterEach, expect, it, vi } from "vitest";
import { ApiClient } from "../api";

afterEach(() => vi.unstubAllGlobals());

it("calls the default browser fetch with its native global receiver", async () => {
  const nativeFetch = vi.fn(function (this: unknown) {
    if (this !== globalThis) throw new TypeError("Illegal invocation");
    return Promise.resolve(new Response('{"id":"me"}', { status: 200 }));
  });
  vi.stubGlobal("fetch", nativeFetch);
  const api = new ApiClient(async () => "private-token");
  await expect(api.get("/me")).resolves.toEqual({ id: "me" });
  expect(nativeFetch).toHaveBeenCalledOnce();
  expect(nativeFetch).toHaveBeenCalledWith("/api/v1/me", expect.objectContaining({ cache: "no-store" }));
});

it("keeps injected fetch implementations and the authenticated request contract", async () => {
  const injected = vi.fn().mockResolvedValue(new Response('{"id":"custom"}', { status: 200 }));
  const api = new ApiClient(async () => "private-token", injected);
  await expect(api.get("/me")).resolves.toEqual({ id: "custom" });
  expect(injected).toHaveBeenCalledOnce();
  const headers = injected.mock.calls[0][1].headers as Headers;
  expect(headers.get("Authorization")).toBe("Bearer private-token");
});
