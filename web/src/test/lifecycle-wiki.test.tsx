import { ConfigProvider } from "antd";
import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { ApiError } from "../api";
import { WikiPageDetail } from "../pages/Wiki";
import { lifecycleMetrics, lifecyclePage, lifecycleRevision } from "./lifecycle-fixtures";

const { api } = vi.hoisted(() => ({ api: { get: vi.fn(), request: vi.fn(), post: vi.fn() } }));
vi.mock("../auth", () => ({ useAuth: () => ({ api,
  user: { profile: { sub: "alice", iss: "https://idp", sid: "login" } } }) }));
function visibility(value: string) {
  Object.defineProperty(document, "visibilityState", { value, configurable: true });
  fireEvent(document, new Event("visibilitychange"));
}
function show(pinned = false, strict = false) {
  const ui = <ConfigProvider theme={{ token: { motion: false } }}>
    <MemoryRouter initialEntries={[{ pathname: "/pages/page%3Ap", search: pinned ? "?revision=r1" : "", key: "same-display" }]}>
      <Routes><Route path="/pages/:pageId" element={<WikiPageDetail />} /></Routes>
    </MemoryRouter>
  </ConfigProvider>;
  return render(strict ? <StrictMode>{ui}</StrictMode> : ui);
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("crypto", webcrypto);
  visibility("visible");
  api.get.mockImplementation(async (path: string) => {
    if (path === "/pages/page%3Ap") return lifecyclePage;
    if (path === "/pages/page%3Ap/revisions/r1") return lifecycleRevision;
    if (path === "/pages/page%3Ap/lifecycle?revision_id=r1") return lifecycleMetrics();
    throw new Error("Unexpected route: " + path);
  });
  api.request.mockImplementation(async (_path: string, init: RequestInit) => ({ id: "event", revision_id: JSON.parse(String(init.body)).revision_id, recorded_at: "2026-09-08T12:00:00Z" }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); visibility("visible"); });

describe("Wiki lifecycle display and usage", () => {
  it.each([false, true])("shows independent dimensions and records only the displayed revision, pinned=%s", async (pin) => {
    api.request.mockImplementation(async (_path: string, init: RequestInit) => {
      expect(screen.getByText("VISIBLE-WIKI-BODY")).toBeInTheDocument();
      expect(JSON.parse(String(init.body)).revision_id).toBe("r1");
      return { id: "event", revision_id: "r1", recorded_at: "2026-09-08T12:00:00Z" };
    });
    show(pin);
    expect(await screen.findByText("VISIBLE-WIKI-BODY")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "知识生命周期" })).toHaveTextContent("不同登记来源数");
    expect(screen.getByText("2 / 3（67%）")).toBeInTheDocument();
    expect(screen.getByText("我的近 7 天访问次数")).toBeInTheDocument();
    expect(screen.getByText("我的近 30 天访问次数")).toBeInTheDocument();
    expect(screen.getByText("新鲜度")).toBeInTheDocument();
    await waitFor(() => expect(api.request).toHaveBeenCalledTimes(1));
    expect(api.request.mock.calls[0][0]).toBe("/pages/page%3Ap/access-events");
    if (pin) expect(api.get.mock.calls.every(([path]) => path !== "/pages/page%3Ap")).toBe(true);
  });
  it("does not double count StrictMode, remount or read-only refresh", async () => {
    const mounted = show(false, true);
    await screen.findByText("VISIBLE-WIKI-BODY");
    await waitFor(() => expect(api.request).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "刷新指标" }));
    await screen.findByText("VISIBLE-WIKI-BODY");
    mounted.unmount();
    show();
    await screen.findByText("VISIBLE-WIKI-BODY");
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(api.request).toHaveBeenCalledTimes(1);
    expect(api.get.mock.calls.filter(([path]) => path.includes("/lifecycle?")).length).toBeGreaterThan(1);
  });
  it("never fetches or records an initially hidden tab, then authorizes on restore", async () => {
    visibility("hidden"); show();
    expect(api.get).not.toHaveBeenCalled();
    expect(api.request).not.toHaveBeenCalled();
    visibility("visible");
    await screen.findByText("VISIBLE-WIKI-BODY");
    await waitFor(() => expect(api.request).toHaveBeenCalledTimes(1));
  });
  it("does not show or record content while its lifecycle authorization is pending", async () => {
    api.get.mockImplementation((path: string) => path.includes("/lifecycle?") ? new Promise(() => {}) : Promise.resolve(lifecyclePage));
    show();
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("VISIBLE-WIKI-BODY")).toBeNull();
    expect(api.request).not.toHaveBeenCalled();
  });
  it.each([403, 503])("clears body and metadata on failed periodic authorization (%s)", async (status) => {
    vi.useFakeTimers(); show();
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(screen.getByText("VISIBLE-WIKI-BODY")).toBeInTheDocument();
    api.get.mockRejectedValue(new ApiError(status, "denied"));
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.queryByText("VISIBLE-WIKI-BODY")).toBeNull();
    expect(screen.queryByRole("region", { name: "知识生命周期" })).toBeNull();
  });
  it("clears immediately on focus and ignores a late old read", async () => {
    show(); await screen.findByText("VISIBLE-WIKI-BODY");
    let late!: (value: unknown) => void;
    api.get.mockImplementationOnce(() => new Promise((resolve) => { late = resolve; }));
    fireEvent(window, new Event("focus"));
    expect(screen.queryByText("VISIBLE-WIKI-BODY")).toBeNull();
    api.get.mockRejectedValue(new ApiError(403, "revoked"));
    fireEvent(window, new Event("focus"));
    await screen.findByText(/权限不足/);
    await act(async () => { late(lifecyclePage); });
    expect(screen.queryByText("VISIBLE-WIKI-BODY")).toBeNull();
  });
  it("keeps a slow periodic read alive and removes all content at its total deadline", async () => {
    vi.useFakeTimers(); show();
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    const before = api.get.mock.calls.length;
    let pendingSignal!: AbortSignal;
    api.get.mockImplementation((_path: string, signal: AbortSignal) => { pendingSignal = signal; return new Promise(() => {}); });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(api.get.mock.calls.length).toBe(before + 1);
    expect(pendingSignal.aborted).toBe(false);
    expect(screen.getByText("VISIBLE-WIKI-BODY")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(pendingSignal.aborted).toBe(true);
    expect(screen.queryByText("VISIBLE-WIKI-BODY")).toBeNull();
  });
});