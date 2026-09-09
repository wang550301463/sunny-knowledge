import { ConfigProvider } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { TaskConflict } from "../components/TaskConflict";

const { api } = vi.hoisted(() => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../auth", () => ({ useAuth: () => ({ api }) }));
const content = { title: "Frozen title", markdown: "Frozen proposed body", entity_type: "File", claims: [], evidence: [], state: "valid" };
const conflict = {
  task_id: "task", step_number: 2, page_id: "page", source_version: 7, operation: "publish",
  original_base_revision: "original-base", review_base_revision: "first-review-base", current_revision: "current-base",
  request: { base_revision: "original-base", content }, comparison: null, proposal_id: null,
};
beforeEach(() => {
  vi.resetAllMocks();
  api.get.mockImplementation((path: string) => Promise.resolve(path.startsWith("/tasks/") ? conflict : {
    id: "page", current_revision: "current-base", revision: { content: { ...content, markdown: "Current reviewed body" } },
  }));
});
function show() {
  render(<ConfigProvider theme={{ token: { motion: false } }}><BrowserRouter><TaskConflict taskId="task" step={2} /></BrowserRouter></ConfigProvider>);
}
it("reads the live artifact and current page before showing an exact diff, then explicitly proposes against the inspected base", async () => {
  api.post.mockResolvedValue({ id: "proposal-real" });
  show();
  expect(await screen.findByText("冻结提议正文")).toBeInTheDocument();
  expect(api.get).toHaveBeenCalledWith("/tasks/task/conflicts/2", expect.any(AbortSignal));
  expect(api.get).toHaveBeenCalledWith("/pages/page", expect.any(AbortSignal));
  expect(screen.getByText("original-base", { selector: ".ant-descriptions-item-content" })).toBeInTheDocument();
  expect(screen.getByText("first-review-base", { selector: ".ant-descriptions-item-content" })).toBeInTheDocument();
  expect(screen.getByText("current-base", { selector: ".ant-descriptions-item-content" })).toBeInTheDocument();
  expect(api.post).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("冲突提案理由"), { target: { value: "Reviewed the current difference" } });
  fireEvent.click(screen.getByRole("button", { name: "基于已比较版本提交提案" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith("/tasks/task/conflicts/2/propose", { base_revision: "current-base", reason: "Reviewed the current difference" }));
  expect(await screen.findByText(/proposal-real/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "基于已比较版本提交提案" })).toBeNull();
});

it.each([403, 503])("hides the frozen body and diff after a %s proposal denial", async (status) => {
  api.post.mockRejectedValue(new ApiError(status, status === 403 ? "forbidden" : "authorization_changed"));
  show();
  await screen.findByText("冻结提议正文");
  fireEvent.change(screen.getByLabelText("冲突提案理由"), { target: { value: "Review" } });
  fireEvent.click(screen.getByRole("button", { name: "基于已比较版本提交提案" }));
  await waitFor(() => expect(screen.queryByText("冻结提议正文")).toBeNull());
  expect(screen.queryByText(/Frozen proposed body/)).toBeNull();
  expect(screen.queryByLabelText("版本差异")).toBeNull();
  expect(api.post).toHaveBeenCalledTimes(1);
});

it("requires reloading and comparing after another CAS conflict, without retrying the write", async () => {
  api.post.mockRejectedValue(new ApiError(409, "version_conflict"));
  show();
  await screen.findByText("冻结提议正文");
  fireEvent.change(screen.getByLabelText("冲突提案理由"), { target: { value: "Review" } });
  fireEvent.click(screen.getByRole("button", { name: "基于已比较版本提交提案" }));
  expect(await screen.findByText(/版本已变化或记录冲突/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "基于已比较版本提交提案" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "重新读取并比较" }));
  await screen.findByText("冻结提议正文");
  expect(api.post).toHaveBeenCalledTimes(1);
});

it("keeps a structural conflict's frozen proof visible without inventing PageContent or enabling proposal", async () => {
  api.get.mockResolvedValue({ ...conflict, current_revision: null, request: { base_revision: "original-base", proof: { deleted_paths: ["removed.md"] } } });
  show();
  expect(await screen.findByText(/removed.md/)).toBeInTheDocument();
  expect(screen.queryByText("冻结提议正文")).toBeNull();
  expect(screen.queryByRole("button", { name: "基于已比较版本提交提案" })).toBeNull();
  expect(api.post).not.toHaveBeenCalled();
});

it("never exposes frozen content when the current page read is denied", async () => {
  api.get.mockImplementation((path: string) => path.startsWith("/tasks/") ? Promise.resolve(conflict) : Promise.reject(new ApiError(403, "forbidden")));
  show();
  expect(await screen.findByText(/权限不足/)).toBeInTheDocument();
  expect(screen.queryByText(/Frozen proposed body/)).toBeNull();
});
