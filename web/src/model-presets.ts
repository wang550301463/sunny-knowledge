import type { Capability, ModelConfig } from "./types";

// Explicit operator choices, mirrored from llm/examples/bailian-models.json.
// These examples are not a successful capability test or an enabled default.
export const bailianPresets: Record<Capability, Partial<ModelConfig>> = {
  chat: {
    name: "Bailian Qwen3.7 Plus 2026-05-26",
    provider: "openai",
    provider_model: "qwen3.7-plus-2026-05-26",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    capability: "chat",
    chat_token_parameter: "max_completion_tokens",
    chat_enable_thinking: false,
    max_input_chars: 65536,
    max_output_tokens: 4096,
    timeout_seconds: 60,
    max_retries: 1,
    concurrency_per_replica: 2,
    max_queue_per_replica: 8,
  },
  embedding: {
    name: "Bailian text-embedding-v4 1024",
    provider: "openai",
    provider_model: "text-embedding-v4",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    capability: "embedding",
    dimensions: 1024,
    request_dimensions: true,
    max_batch_size: 10,
    max_input_chars: 8192,
    timeout_seconds: 30,
    max_retries: 1,
    concurrency_per_replica: 2,
    max_queue_per_replica: 8,
  },
  rerank: {
    name: "Bailian Qwen3.7 text rerank",
    provider: "dashscope_rerank",
    provider_model: "qwen3.7-text-rerank",
    base_url: "https://dashscope.aliyuncs.com/api/v1",
    capability: "rerank",
    max_batch_size: 64,
    max_input_chars: 65536,
    timeout_seconds: 30,
    max_retries: 1,
    concurrency_per_replica: 2,
    max_queue_per_replica: 8,
  },
};
