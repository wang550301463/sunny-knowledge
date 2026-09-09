"""Domain response contracts are derived from actual serializers and preserve JSON."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from test_responses import serialized


@pytest.mark.parametrize("domain,expected", [("ingest", 11), ("retrieval", 6), ("llm", 15), ("graphiti", 2)])
def test_all_existing_domain_data_routes_have_native_response_declarations(domain, expected):
    from importlib import import_module
    from fastapi.routing import APIRoute
    from contract_export import isolated_environment

    with isolated_environment():
        app = import_module(f"knowledge_platform.{domain}.app").create_app()
    routes = [r for r in app.routes if isinstance(r, APIRoute) and r.path not in {"/healthz", "/readyz"}]
    assert len(routes) == expected
    assert all(r.response_model is not None for r in routes)


@pytest.mark.asyncio
async def test_ingest_real_source_preview_and_task_serializers_keep_redaction_and_missing_values():
    from knowledge_platform.ingest import models, responses
    from knowledge_platform.ingest.service import IngestService

    now = datetime(2026, 9, 9, tzinfo=UTC)
    manifest = {"source_revision": "commit", "file_count": 1, "total_bytes": 9, "diagnostics": [],
                "files": [{"path": "guide.md", "size": 9, "sha256": "a" * 64, "kind": "markdown", "diagnostics": [], "object_key": "must-not-escape"}]}
    preview = models.Preview(source_id="s", version=1, manifest=manifest)
    version = models.SourceVersion(config={"path": "guide.md", "content": "must-not-escape"}, credential_ciphertext=None)
    class Session:
        async def get(self, cls, key):
            return preview if cls is models.Preview else version
    service = IngestService(Session(), None, None, None, None)
    source = models.Source(id="s", name="Guide", kind="markdown", space_id="space", resource_id="resource", version=1, generation=2,
                           state="active", created_by="alice", created_at=now, updated_at=now, latest_task_id=None)
    raw = await service.source_wire(source)
    raw["future_metadata"] = {"items": [], "disabled": False}
    assert await serialized(responses.SourceView, raw) == raw
    assert "must-not-escape" not in str(raw)
    raw = service.preview_wire(preview)
    assert await serialized(responses.PreviewView, raw) == raw
    assert "object_key" not in str(raw)
    task = models.Task(id="t", source_id="s", source_version=1, space_id="space", operation="sync", status="pending", stage="snapshot", error_code=None, result={}, checkpoint={}, created_at=now, updated_at=now)
    raw = service.task_wire(task)
    assert await serialized(responses.TaskView, raw) == raw


@pytest.mark.asyncio
async def test_llm_real_public_serializer_preserves_config_defaults_and_credential_redaction():
    from knowledge_platform.llm import models, responses
    from knowledge_platform.llm.schemas import ModelConfig
    from knowledge_platform.llm.service import ModelStore

    store = ModelStore(None, None, None, None)
    async def no_test(*args):
        return None
    store._test = no_test
    config = ModelConfig(name="Qwen", provider="openai", provider_model="qwen-plus", base_url="https://provider.example/v1", capability="chat").model_dump()
    config.pop("chat_enable_thinking")  # Existing immutable versions can predate this optional field.
    row = models.Configuration(id="c", version=1, config=config, credential_ciphertext="must-not-escape", created_at=datetime(2026, 9, 9, tzinfo=UTC), created_by="alice")
    raw = await store.public(None, SimpleNamespace(id="m", state="active"), row)
    raw["future_metadata"] = {"items": [], "value": None}
    assert await serialized(responses.ModelView, raw) == raw
    assert "must-not-escape" not in str(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["chat", "embedding", "rerank"])
async def test_llm_inference_wire_keeps_native_adapter_results(capability):
    from knowledge_platform.llm import responses
    from knowledge_platform.llm.adapters import OpenAIAdapter, normalize_rerank_results
    from knowledge_platform.llm.schemas import ChatRequest, EmbeddingRequest, ModelConfig, RerankRequest

    cid = "00000000-0000-0000-0000-000000000001"
    config = ModelConfig(name="model", provider="openai", provider_model="qwen", base_url="https://provider.example", capability=capability, dimensions=2 if capability == "embedding" else None)
    if capability == "chat":
        body = ChatRequest(configuration_id=cid, messages=[{"role": "user", "content": "query"}])
        value = {"choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "tc", "type": "function", "function": {"name": "search", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 3}}
        raw = OpenAIAdapter(None).normalize(config, body, value)
        output = responses.ChatResult
    elif capability == "embedding":
        body = EmbeddingRequest(configuration_id=cid, input=["query"])
        raw = OpenAIAdapter(None).normalize(config, body, {"data": [{"index": 0, "embedding": [1, 0.5]}]})
        output = responses.EmbeddingResult
    else:
        body = RerankRequest(configuration_id=cid, query="query", documents=["doc"], top_n=1)
        raw = {"results": normalize_rerank_results(body, [{"index": 0, "relevance_score": 0.5}]), "usage": {}}
        output = responses.RerankResult
    raw = {"configuration_id": cid, "invocation_id": "invoke", **raw, "request_id": None}
    assert await serialized(output, raw) == raw


@pytest.mark.asyncio
async def test_graph_response_reuses_native_model_and_retains_default_empty_branches():
    from knowledge_platform.graphiti.responses import TraverseResult
    from knowledge_platform.graphiti.schemas import GraphResult

    assert TraverseResult is GraphResult
    raw = GraphResult(degraded=["graph_unavailable"]).model_dump(mode="json")
    assert await serialized(TraverseResult, raw) == raw