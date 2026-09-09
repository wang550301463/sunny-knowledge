import { ConfigProvider } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { ModelForm } from "../pages/Models";
import type { ModelRecord } from "../types";

const { api } = vi.hoisted(() => ({
  api: { post: vi.fn(), put: vi.fn() },
}));
vi.mock("../auth", () => ({ useAuth: () => ({ api }) }));

beforeEach(() => {
  vi.clearAllMocks();
  api.post.mockResolvedValue({ id: "created" });
  api.put.mockResolvedValue({ id: "updated" });
});

function showForm(model?: ModelRecord) {
  render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <ModelForm model={model} onClose={vi.fn()} onSaved={vi.fn()} />
    </ConfigProvider>,
  );
}

describe("Bailian model configuration", () => {
  it("uses an optional chat preset and clears the write-only key even if saving fails", async () => {
    api.post.mockRejectedValue(new ApiError(403, "forbidden"));
    showForm();
    expect(screen.getByLabelText("供应商模型 ID")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "百炼对话预设" }));
    expect(screen.getByLabelText("供应商模型 ID")).toHaveValue("qwen3.7-plus-2026-05-26");
    fireEvent.change(screen.getByLabelText("API 密钥（私有输入）"), {
      target: { value: "test-private-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存模型" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/models", expect.objectContaining({
      provider: "openai",
      provider_model: "qwen3.7-plus-2026-05-26",
      base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      capability: "chat",
      chat_token_parameter: "max_completion_tokens",
      chat_enable_thinking: false,
      max_input_chars: 65536,
      max_retries: 1,
      concurrency_per_replica: 2,
      credential: "test-private-key",
    })));
    expect(screen.getByLabelText("API 密钥（私有输入）")).toHaveValue("");
    expect(await screen.findByText(/权限不足/)).toBeInTheDocument();
  });

  it("resets chat-only options and the unsaved credential when applying the embedding preset", async () => {
    showForm();
    fireEvent.click(screen.getByRole("button", { name: "百炼对话预设" }));
    fireEvent.change(screen.getByLabelText("API 密钥（私有输入）"), { target: { value: "old-key" } });
    fireEvent.click(screen.getByRole("button", { name: "百炼向量预设" }));
    expect(screen.getByLabelText("API 密钥（私有输入）")).toHaveValue("");
    expect(screen.getByLabelText("向量维度")).toHaveValue("1024");
    fireEvent.click(screen.getByRole("button", { name: "保存模型" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/models", expect.objectContaining({
      provider: "openai", provider_model: "text-embedding-v4", capability: "embedding",
      dimensions: 1024, request_dimensions: true, max_batch_size: 10,
      max_input_chars: 8192, timeout_seconds: 30, chat_enable_thinking: null,
    })));
    expect(api.post.mock.calls[0][1]).not.toHaveProperty("credential");
  });

  it("uses the native rerank protocol and keeps its base URL separate from the chat compatibility API", async () => {
    showForm();
    fireEvent.click(screen.getByRole("button", { name: "百炼重排预设" }));
    fireEvent.click(screen.getByRole("button", { name: "保存模型" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/models", expect.objectContaining({
      provider: "dashscope_rerank", provider_model: "qwen3.7-text-rerank",
      base_url: "https://dashscope.aliyuncs.com/api/v1", capability: "rerank",
      dimensions: null, request_dimensions: false, chat_enable_thinking: null,
      max_batch_size: 64, max_input_chars: 65536, timeout_seconds: 30,
    })));
  });

  it("lets admins explicitly choose the other rerank protocol and enter their workspace URL", async () => {
    showForm();
    const user = userEvent.setup();
    fireEvent.click(screen.getByRole("button", { name: "百炼重排预设" }));
    await user.click(screen.getByLabelText("适配协议"));
    await user.click(screen.getByText("百炼兼容重排（/reranks）"));
    fireEvent.change(screen.getByLabelText("供应商模型 ID"), { target: { value: "qwen3-rerank" } });
    fireEvent.change(screen.getByLabelText("API 地址（含版本前缀）"), {
      target: { value: "https://my-workspace.cn-beijing.maas.aliyuncs.com/compatible-api/v1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存模型" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/models", expect.objectContaining({
      provider: "dashscope_rerank_compatible", provider_model: "qwen3-rerank",
      base_url: "https://my-workspace.cn-beijing.maas.aliyuncs.com/compatible-api/v1",
    })));
  });

  it.each([
    ["开启思考", true],
    ["默认（不发送参数）", null],
  ])("sends the explicit thinking choice %s", async (label, value) => {
    showForm();
    const user = userEvent.setup();
    fireEvent.click(screen.getByRole("button", { name: "百炼对话预设" }));
    await user.click(screen.getByText("高级参数与调用限制"));
    await user.click(screen.getByLabelText("思考模式（仅对话）"));
    await user.click(screen.getByText(label as string));
    fireEvent.click(screen.getByRole("button", { name: "保存模型" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/models", expect.objectContaining({ chat_enable_thinking: value })));
  });
});
