import { ConfigProvider } from "antd";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { GraphExplorer } from "../pages/GraphExplorer";
import { graphResult, graphSnapshot, seedResult } from "./graph-fixtures";
const { api } = vi.hoisted(() => ({ api: { request: vi.fn(), get: vi.fn() } }));
vi.mock("../auth", () => ({ useAuth: () => ({ api }) }));
beforeEach(() => {
  vi.clearAllMocks();
  api.request.mockImplementation(async (path: string) => path === "/search" ? seedResult() : graphResult());
  api.get.mockResolvedValue(graphSnapshot);
});
function mount(spaceId = "space") { return render(<ConfigProvider theme={{ token: { motion: false } }}><MemoryRouter><GraphExplorer spaceId={spaceId}/></MemoryRouter></ConfigProvider>); }
async function findSeeds() {
  fireEvent.change(screen.getByRole("textbox", { name: "查找起点" }), { target: { value: "service dependency" } });
  fireEvent.click(screen.getByRole("button", { name: "查找证据片段" }));
  await screen.findByRole("checkbox", { name: "授权 Wiki A" });
}
async function traverse() {
  await findSeeds();
  fireEvent.click(screen.getByRole("checkbox", { name: "授权 Wiki A" }));
  fireEvent.click(screen.getByRole("button", { name: "展开关系" }));
  await screen.findByRole("button", { name: "查看关系 edge-ab" });
}
async function openEvidence() {
  await traverse();
  fireEvent.click(screen.getByRole("button", { name: "查看关系 edge-ab" }));
  return screen.findByRole("dialog", { name: "图谱证据" });
}
describe("authorized graph explorer", () => {
  it("searches real seeds then freezes relation/time controls separately from displayed results", async () => {
    mount();
    fireEvent.change(screen.getByLabelText("业务有效时间"), { target: { value: "2026-09-01T10:00" } });
    fireEvent.change(screen.getByLabelText("系统获知截止时间"), { target: { value: "2026-09-02T11:00" } });
    fireEvent.change(screen.getByLabelText("遍历方向"), { target: { value: "incoming" } });
    fireEvent.change(screen.getByLabelText("遍历跳数"), { target: { value: "2" } });
    await traverse();
    const search = JSON.parse(api.request.mock.calls[0][1].body), call = JSON.parse(api.request.mock.calls[1][1].body);
    expect(search.relation).toBeUndefined();
    expect(search.space_ids).toEqual(["space"]);
    expect(call).toMatchObject({ seed_fragment_ids: ["fragment-a"], relation: { direction: "incoming", hops: 2 }, as_of: new Date("2026-09-01T10:00").toISOString(), known_at: new Date("2026-09-02T11:00").toISOString() });
    expect(screen.getByLabelText("当前执行条件")).toHaveTextContent("入向 · 2 跳");
    fireEvent.change(screen.getByLabelText("遍历跳数"), { target: { value: "1" } });
    expect(screen.getByText("条件已修改，重新查询后应用。")).toBeInTheDocument();
    expect(screen.getByLabelText("当前执行条件")).toHaveTextContent("入向 · 2 跳");
    expect(screen.queryByRole("checkbox", { name: "授权 Wiki A" })).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "授权关系图" })).toBeInTheDocument();
    expect(screen.queryByText("Service")).not.toBeInTheDocument();
    expect(screen.getAllByText("stable-a").length).toBeGreaterThan(0);
  });
  it("shows projection degradation as a capability gap rather than no dependencies", async () => {
    mount();
    const degraded = seedResult();
    degraded.degraded = ["graph_projection_pending"] as never;
    api.request.mockImplementation(async (path: string) => path === "/search" ? seedResult() : degraded);
    await findSeeds();
    fireEvent.click(screen.getByRole("checkbox", { name: "授权 Wiki A" }));
    fireEvent.click(screen.getByRole("button", { name: "展开关系" }));
    await screen.findByText(/图投影尚未就绪/);
    expect(screen.getByText(/不能据此判断不存在依赖/)).toBeInTheDocument();
    expect(screen.getByText(/没有部署证据/)).toBeInTheDocument();
  });
  it("shows actionable model setup failure without fabricated fallback", async () => {
    api.request.mockRejectedValue(new ApiError(503, "model_not_configured")); mount();
    fireEvent.change(screen.getByRole("textbox", { name: "查找起点" }), { target: { value: "q" } });
    fireEvent.click(screen.getByRole("button", { name: "查找证据片段" }));
    expect(await screen.findByText(/配置检索所需的 Embedding/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "模型设置" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "授权关系图" })).not.toBeInTheDocument();
  });
  it("opens exact cross-space source only after both live checks and hides it immediately on focus", async () => {
    mount(); const drawer = await openEvidence();
    let finish!: (value: unknown) => void;
    api.request.mockResolvedValueOnce(graphResult()).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    fireEvent.click(within(drawer).getByRole("button", { name: "查看原文 src/main.go" }));
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/source-snapshots/snapshot", expect.any(AbortSignal)));
    expect(screen.queryByRole("region", { name: "原始引用逐行内容" })).not.toBeInTheDocument();
    await act(async () => { finish(graphResult()); });
    const source = await screen.findByRole("region", { name: "原始引用逐行内容" });
    expect(source).toHaveTextContent("EXACT-GRAPH-SOURCE");
    api.request.mockImplementation(() => new Promise(() => {}));
    fireEvent.focus(window);
    expect(screen.queryByRole("dialog", { name: "图谱证据" })).not.toBeInTheDocument();
    expect(screen.queryByText("DECLARATION-2")).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "授权关系图" })).not.toBeInTheDocument();
  });
  it("never displays a source returned after focus invalidated its graph", async () => {
    mount(); const drawer = await openEvidence();
    let late!: (value: unknown) => void;
    api.get.mockImplementation(() => new Promise(resolve => { late = resolve; }));
    fireEvent.click(within(drawer).getByRole("button", { name: "查看原文 src/main.go" }));
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    api.request.mockRejectedValue(new ApiError(403, "denied"));
    fireEvent.focus(window);
    await act(async () => { late(graphSnapshot); });
    expect(screen.queryByRole("region", { name: "原始引用逐行内容" })).not.toBeInTheDocument();
    expect(screen.queryByText("DECLARATION-2")).not.toBeInTheDocument();
  });
  it("clears graph and evidence when final source authorization removes the relation", async () => {
    mount(); const drawer = await openEvidence();
    api.request.mockResolvedValueOnce(graphResult()).mockResolvedValueOnce(seedResult());
    fireEvent.click(within(drawer).getByRole("button", { name: "查看原文 src/main.go" }));
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "图谱证据" })).not.toBeInTheDocument());
    expect(screen.queryByRole("region", { name: "原始引用逐行内容" })).not.toBeInTheDocument();
  });
});