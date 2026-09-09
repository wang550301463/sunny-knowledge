import { ConfigProvider } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { WikiPageDetail } from "../pages/Wiki";
const { api } = vi.hoisted(() => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../auth", () => ({ useAuth: () => ({ api }) }));
const revision = {
  id: "old-revision", page_id: "page:p", number: 1, base_revision: null, created_by: "u", created_at: "2026-09-08T00:00:00Z", publication_kind: "reviewed",
  content: { title: "Historical title", markdown: "EXACT-OLD-MARKER", entity_type: "Module", claims: [], evidence: [], state: "stale" },
};
const current = { id: "page:p", space_id: "s", current_revision: "new-revision", revision_number: 2, revision: { ...revision, id: "new-revision", number: 2, content: { ...revision.content, markdown: "CURRENT-PRIVATE-MARKER" } } };
beforeEach(() => vi.resetAllMocks());
function show(search = "?revision=old-revision") {
  render(<ConfigProvider theme={{ token: { motion: false } }}><MemoryRouter initialEntries={[`/pages/page%3Ap${search}`]}>
    <Link to="/pages/page%3Ap?revision=denied-revision">另一固定版本</Link>
    <Routes><Route path="/pages/:pageId" element={<WikiPageDetail />} /></Routes>
  </MemoryRouter></ConfigProvider>);
}
it.each([false, true])("reads only the exact pinned revision even if the current revision is denied=%s", async (denied) => {
  api.get.mockImplementation((path: string) => path === "/pages/page%3Ap/revisions/old-revision" ? Promise.resolve(revision) : denied ? Promise.reject(new ApiError(403, "forbidden")) : Promise.resolve(current));
  show();
  expect(await screen.findByText("EXACT-OLD-MARKER")).toBeInTheDocument();
  expect(screen.getByText("已过期")).toBeInTheDocument();
  expect(api.get).toHaveBeenCalledWith("/pages/page%3Ap/revisions/old-revision", expect.any(AbortSignal));
  expect(api.get).toHaveBeenCalledTimes(1);
  expect(document.body.textContent).not.toContain("CURRENT-PRIVATE-MARKER");
  expect(screen.queryByRole("button", { name: "提出修改" })).toBeNull();
});
it("does not substitute current content when the pinned revision is denied", async () => {
  api.get.mockImplementation((path: string) => path.includes("/revisions/") ? Promise.reject(new ApiError(403, "forbidden")) : Promise.resolve(current));
  show();
  expect(await screen.findByText(/权限不足/)).toBeInTheDocument();
  expect(document.body.textContent).not.toContain("CURRENT-PRIVATE-MARKER");
  expect(api.get).toHaveBeenCalledTimes(1);
});
it("clears the previous pin immediately while another pinned revision is loading or denied", async () => {
  let deny: (error: unknown) => void = () => {};
  api.get.mockImplementation((path: string) => path.endsWith("old-revision") ? Promise.resolve(revision) : new Promise((_, reject) => { deny = reject; }));
  show();
  await screen.findByText("EXACT-OLD-MARKER");
  fireEvent.click(screen.getByText("另一固定版本"));
  expect(document.body.textContent).not.toContain("EXACT-OLD-MARKER");
  await waitFor(() => expect(api.get).toHaveBeenCalledWith("/pages/page%3Ap/revisions/denied-revision", expect.any(AbortSignal)));
  deny(new ApiError(403, "forbidden"));
  expect(await screen.findByText(/权限不足/)).toBeInTheDocument();
  expect(document.body.textContent).not.toContain("EXACT-OLD-MARKER");
});
it.each(["?revision=", "?revision=one&revision=two"])("rejects an ambiguous or empty pin without loading current content: %s", async (search) => {
  api.get.mockResolvedValue(current);
  show(search);
  expect(await screen.findByText(/提交内容不符合接口要求/)).toBeInTheDocument();
  expect(api.get).not.toHaveBeenCalled();
});
