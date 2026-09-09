import { ConfigProvider } from "antd";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OnboardingPage } from "../pages/Onboarding";
import { ApiError } from "../api";
import { onboardingAPI, onboardingResponses, onboardingSelection } from "./onboarding-fixtures";
const { api } = vi.hoisted(() => ({ api: { get: vi.fn() } }));
vi.mock("../auth", () => ({ useAuth: () => ({ api }) }));
const principal = { id: "person", subjects: [], permissions: [], auth_epoch: 7 };
beforeEach(() => {
  vi.resetAllMocks();
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  api.get.mockImplementation(onboardingAPI().api.get);
});
afterEach(() => vi.restoreAllMocks());
function Path() { return <output aria-label="当前位置">{useLocation().search}</output>; }
function mount(search = new URLSearchParams(onboardingSelection).toString()) {
  return render(<ConfigProvider theme={{ token: { motion: false } }}><MemoryRouter initialEntries={["/onboarding?" + search]}><Routes><Route path="/onboarding" element={<><OnboardingPage principal={principal} /><Path /></>} /></Routes></MemoryRouter></ConfigProvider>);
}
describe("first-use evidence guide", () => {
  it("offers real routes and evidence checks without displaying raw bodies or pretending retrieval was probed", async () => {
    mount();
    await screen.findByRole("heading", { name: "首次使用" });
    await screen.findByText("PRIVATE-SPACE");
    expect(screen.getByText("检索能力尚未独立验证")).toBeInTheDocument();
    expect(screen.getByText("已核验当前 Wiki 的引用回答")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "添加来源、预览与同步" })).toHaveAttribute("href", "/spaces/s?tab=sources");
    expect(screen.getByRole("link", { name: "浏览已发布 Wiki" })).toHaveAttribute("href", "/spaces/s/pages/page?revision=revision");
    expect(screen.getByRole("link", { name: "查看所选回答" })).toHaveAttribute("href", "/runs/run");
    expect(screen.queryByText("PRIVATE-BODY")).not.toBeInTheDocument();
    expect(screen.queryByText("PRIVATE-ANSWER")).not.toBeInTheDocument();
    expect(api.get.mock.calls.some(([path]) => path.startsWith("/models"))).toBe(false);
  });
  it("withdraws private metadata on blur and keeps only opaque selection IDs for an explicit return", async () => {
    mount();
    await screen.findByText("PRIVATE-SPACE");
    fireEvent.blur(window);
    expect(screen.queryByText("PRIVATE-SPACE")).not.toBeInTheDocument();
    expect(screen.queryByText("PRIVATE-SOURCE")).not.toBeInTheDocument();
    expect(screen.queryByText("PRIVATE-WIKI")).not.toBeInTheDocument();
    expect(screen.getByText("已暂停显示，返回此窗口后重新核验授权。")).toBeInTheDocument();
    const stored = sessionStorage.getItem("sunny:onboarding:person");
    expect(JSON.parse(stored!)).toEqual(onboardingSelection);
    expect(stored).not.toContain("PRIVATE");
    api.get.mockRejectedValue(new ApiError(403, "denied"));
    fireEvent.focus(window);
    await screen.findByText(/权限不足/);
    expect(screen.queryByText("PRIVATE-SPACE")).not.toBeInTheDocument();
  });
  it("restores IDs from this actor's session on return, then reauthorizes instead of trusting a stored success", async () => {
    sessionStorage.setItem("sunny:onboarding:person", JSON.stringify({ ...onboardingSelection, completed: true, secret: "DO-NOT-RESTORE" }));
    mount("");
    await screen.findByText("PRIVATE-SPACE");
    await waitFor(() => expect(screen.getByLabelText("当前位置")).toHaveTextContent("source=source"));
    expect(api.get).toHaveBeenCalledWith("/runs/run", expect.any(AbortSignal));
    expect(JSON.stringify(sessionStorage)).not.toContain("DO-NOT-RESTORE");
  });
  it("shows real review and worker-authorization blockers instead of a completion action", async () => {
    const values = onboardingResponses();
    Object.assign(values["/tasks/task"] as object, { status: "failed", error_code: "worker_authorization_required" });
    api.get.mockImplementation(onboardingAPI(values).api.get);
    mount();
    await screen.findByText("接入 Worker 缺少所选空间或来源的授权");
    expect(screen.queryByRole("button", { name: /完成|跳过/ })).not.toBeInTheDocument();
    expect(screen.getByText(/不会自动给服务账号扩大权限/)).toBeInTheDocument();
  });
  it("clears source, page and run selection when a different space is selected", async () => {
    const values = onboardingResponses();
    (values["/spaces"] as { items: unknown[] }).items.push({ id: "other", name: "Other space" });
    values["/spaces/other"] = { id: "other", name: "Other space" };
    api.get.mockImplementation(onboardingAPI(values).api.get);
    mount();
    await screen.findByText("PRIVATE-SPACE");
    fireEvent.change(screen.getByLabelText("本次知识空间"), { target: { value: "other" } });
    await act(async () => {});
    expect(screen.getByLabelText("当前位置")).not.toHaveTextContent("source=source");
    expect(screen.getByLabelText("当前位置")).not.toHaveTextContent("run=run");
    expect(screen.queryByText("PRIVATE-WIKI")).not.toBeInTheDocument();
  });
});