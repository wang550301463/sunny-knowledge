import { ConfigProvider } from "antd";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SpaceDetail } from "../pages/Spaces";
import { graphResult, seedResult } from "./graph-fixtures";
const { api } = vi.hoisted(() => ({ api: { request: vi.fn(), get: vi.fn() } }));
vi.mock("../auth", () => ({ useAuth: () => ({ api }) }));
beforeEach(() => {
  vi.resetAllMocks();
  api.get.mockImplementation(async (path: string) => {
    if (path === "/spaces/space") return { id: "space", name: "PRIVATE-SPACE-TITLE" };
    if (path.startsWith("/pages?")) return { items: [] };
    throw new Error("Unexpected " + path);
  });
  api.request.mockImplementation(async (path: string) => path === "/search" ? seedResult() : graphResult());
});
afterEach(() => vi.useRealTimers());
function mount(tab="graph") { return render(<ConfigProvider theme={{ token: { motion: false } }}><MemoryRouter initialEntries={["/spaces/space?tab="+tab]}><Routes><Route path="/spaces/:spaceId" element={<SpaceDetail/>}/></Routes></MemoryRouter></ConfigProvider>); }
async function openGraph() {
  await screen.findByRole("heading", { name: "PRIVATE-SPACE-TITLE" });
  fireEvent.change(screen.getByRole("textbox", { name: "查找起点" }), { target: { value: "private user query" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "查找证据片段" })); });
  fireEvent.click(await screen.findByRole("checkbox", { name: "授权 Wiki A" }));
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "展开关系" })); });
  await screen.findByRole("img", { name: "授权关系图" });
}
describe("graph tab outer space authorization", () => {
  it("withdraws the space name and graph while focus is pending, then resumes using seeds and keeps the draft", async () => {
    mount(); await openGraph();
    let finish!: (value: unknown) => void;
    api.get.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    fireEvent.focus(window);
    expect(screen.queryByText("PRIVATE-SPACE-TITLE")).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "授权关系图" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "查找起点" })).toHaveValue("private user query");
    expect(screen.getByRole("button", { name: "查找证据片段" })).toBeDisabled();
    await act(async () => { finish({ id: "space", name: "PRIVATE-SPACE-TITLE" }); });
    expect(await screen.findByRole("img", { name: "授权关系图" })).toBeInTheDocument();
    expect(api.request.mock.calls.filter(([path]) => path === "/search")).toHaveLength(1);
  });
  it("uses a finite metadata deadline and cannot resurrect a late space response", async () => {
    mount(); await openGraph(); vi.useFakeTimers();
    let late!: (value: unknown) => void;
    api.get.mockImplementation(() => new Promise(resolve => { late = resolve; }));
    // Focus starts the bounded read immediately; timers do not repeatedly cancel it.
    await act(async () => { window.dispatchEvent(new Event("focus")); await vi.advanceTimersByTimeAsync(31000); });
    await act(async () => { late({ id: "space", name: "LATE-PRIVATE-TITLE" }); });
    expect(screen.queryByText("LATE-PRIVATE-TITLE")).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "授权关系图" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查找证据片段" })).toBeDisabled();
  });
  it("preserves the existing non-graph tab resource behavior", async () => {
    mount("wiki");
    await screen.findByRole("heading", { name: "PRIVATE-SPACE-TITLE" });
    await waitFor(() => expect(api.get.mock.calls.some(([path]) => path.startsWith("/pages?"))).toBe(true));
    api.get.mockClear();
    fireEvent.focus(window);
    expect(api.get).not.toHaveBeenCalled();
    expect(api.request).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "PRIVATE-SPACE-TITLE" })).toBeInTheDocument();
  });
});