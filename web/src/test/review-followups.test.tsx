import { ConfigProvider } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { TaskConflict } from "../components/TaskConflict";
import { SourcesPanel } from "../pages/Sources";

const { api } = vi.hoisted(() => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../auth", () => ({ useAuth: () => ({ api }) }));
const content = { title: "Frozen title", markdown: "FROZEN-PRIVATE-MARKER", entity_type: "File", claims: [], evidence: [], state: "valid" };
const conflict = { task_id: "task", step_number: 2, page_id: "page", source_version: 7, operation: "publish", original_base_revision: "original-base", review_base_revision: "first-review-base", current_revision: "current-base", request: { base_revision: "original-base", content }, comparison: null, proposal_id: null };
const task = { id: "task", source_id: "source", source_version: 7, status: "review_needed", stage: "review", operation: "sync", result: { items: [{ conflict_available: true, step_number: 2 }] } };
const source = { id: "source", name: "My Source", space_id: "space", resource_id: "source:source", kind: "git", version: 7, config: { url: "https://example.test/repo.git", ref: "refs/heads/main" }, has_credential: false, state: "active", created_by: "u", created_at: "2026-09-08T00:00:00Z", updated_at: "2026-09-08T00:00:00Z", preview: null, latest_task_id: "task" };
beforeEach(() => vi.resetAllMocks());

it("clears every nested source/task/conflict detail during source pagination and a resulting permission denial", async () => {
  api.get.mockImplementation((path: string) => Promise.resolve(path.startsWith("/sources?") ? { items: [source], next_cursor: "next" } : path.startsWith("/tasks?") ? { items: [task] } : path === "/tasks/task" ? task : path.includes("/conflicts/") ? conflict : { current_revision: "current-base", revision: { content } }));
  render(<ConfigProvider theme={{ token: { motion: false } }}><BrowserRouter><SourcesPanel spaceId="space" /></BrowserRouter></ConfigProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "预览与同步" }));
  fireEvent.click(await screen.findByRole("button", { name: "查看详情" }));
  fireEvent.click(await screen.findByRole("button", { name: "查看冲突与比较 · 步骤 2" }));
  await screen.findByText("冻结提议正文");
  let deny: (error: unknown) => void = () => {};
  api.get.mockImplementation(() => new Promise((_, reject) => { deny = reject; }));
  const next = screen.getAllByRole("button", { name: "下一页" }).find((button) => !(button as HTMLButtonElement).disabled)!;
  fireEvent.click(next);
  expect(document.body.textContent).not.toContain("FROZEN-PRIVATE-MARKER");
  expect(screen.queryByLabelText("版本差异")).toBeNull();
  deny(new ApiError(403, "forbidden"));
  await screen.findByText(/权限不足/);
  expect(document.body.textContent).not.toContain("FROZEN-PRIVATE-MARKER");
});

it("keeps the existing proposal visible and permits another explicit frozen proposal after comparing a new base", async () => {
  api.get.mockImplementation((path: string) => Promise.resolve(path.startsWith("/tasks/") ? { ...conflict, current_revision: "newer-base", proposal_id: "old-stale-proposal" } : { current_revision: "newer-base", revision: { content } }));
  api.post.mockResolvedValue({ id: "new-review-proposal" });
  render(<ConfigProvider theme={{ token: { motion: false } }}><BrowserRouter><TaskConflict taskId="task" step={2} /></BrowserRouter></ConfigProvider>);
  await screen.findByText(/已有审核提案：old-stale-proposal/);
  fireEvent.click(screen.getByRole("button", { name: "重新读取并比较" }));
  await screen.findByText(/已有审核提案：old-stale-proposal/);
  expect(api.post).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("冲突提案理由"), { target: { value: "Compare again after the earlier proposal was rejected" } });
  fireEvent.click(screen.getByRole("button", { name: "基于已比较版本再次提交提案" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith("/tasks/task/conflicts/2/propose", { base_revision: "newer-base", reason: "Compare again after the earlier proposal was rejected" }));
  expect(await screen.findByText(/new-review-proposal/)).toBeInTheDocument();
  expect(screen.queryByLabelText("冲突提案理由")).toBeNull();
});
