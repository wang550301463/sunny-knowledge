"""Official Bailian HTTP contracts, simulated locally without external credentials."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from knowledge_platform.llm.adapters import AdapterRegistry
from knowledge_platform.llm.schemas import (
    ChatRequest,
    EmbeddingRequest,
    LLMError,
    ModelConfig,
    RerankRequest,
)

from .test_protocol import Chunks, sse


def rerank_config(provider="dashscope_rerank", **changes):
    return ModelConfig.model_validate(
        {
            "name": "Bailian rerank",
            "provider": provider,
            "provider_model": "qwen3.7-text-rerank"
            if provider == "dashscope_rerank"
            else "qwen3-rerank",
            "base_url": "https://dashscope.aliyuncs.com/api/v1"
            if provider == "dashscope_rerank"
            else "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-api/v1",
            "capability": "rerank",
            **changes,
        }
    )


def rerank_response(provider, results=None):
    value = {"results": results or [{"index": 1, "relevance_score": 0.9}]}
    return {
        **({"output": value} if provider == "dashscope_rerank" else value),
        "usage": {"prompt_tokens": 10, "total_tokens": 10},
        "request_id" if provider == "dashscope_rerank" else "id": "req_bailian_1",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["dashscope_rerank", "dashscope_rerank_compatible"])
async def test_rerank_explicit_native_and_compatible_contracts(provider):
    config = rerank_config(provider)
    response = rerank_response(provider)
    results = response.get("output", response)["results"]
    results[0]["document"] = {"text": "private provider echo"}
    captured = []

    async def handle(request):
        captured.append(request)
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = AdapterRegistry(client).get(provider)
        assert adapter.capabilities == frozenset({"rerank"})
        body = RerankRequest(configuration_id=uuid4(), query="q", documents=["a", "b"], top_n=1)
        result = await adapter.infer(config, "synthetic-key", body)
    assert result == {
        "results": [{"index": 1, "score": 0.9}],
        "usage": {"prompt_tokens": 10, "total_tokens": 10},
        "request_id": "req_bailian_1",
    }
    request = captured[0]
    assert request.headers["authorization"] == "Bearer synthetic-key"
    expected = {"model": config.provider_model}
    if provider == "dashscope_rerank":
        assert str(request.url) == (
            "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
        )
        expected.update(input={"query": "q", "documents": ["a", "b"]}, parameters={"top_n": 1})
    else:
        assert str(request.url) == (
            "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-api/v1/reranks"
        )
        expected.update(query="q", documents=["a", "b"], top_n=1)
    assert json.loads(request.content) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["dashscope_rerank", "dashscope_rerank_compatible"])
async def test_dashscope_rerank_rejects_malformed_containers_indices_scores_and_usage(provider):
    bad_results = [
        None,
        {},
        [],
        [{"index": 0, "relevance_score": 0.5}] * 2,
        [{"index": 0, "relevance_score": 0.5}, {"index": 2, "relevance_score": 0.6}],
        [{"index": True, "relevance_score": 0.5}, {"index": 1, "relevance_score": 0.6}],
    ]
    bad_results += [
        [{"index": 0, "relevance_score": value}, {"index": 1, "relevance_score": 0.6}]
        for value in [-0.1, 1.1, True, "0.5", float("inf"), float("nan")]
    ]
    response = rerank_response(provider)

    async def handle(request):
        return httpx.Response(200, content=json.dumps(response).encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = AdapterRegistry(client).get(provider)
        body = RerankRequest(configuration_id=uuid4(), query="q", documents=["a", "b"])
        for results in bad_results:
            response.get("output", response)["results"] = results
            with pytest.raises(LLMError, match="invalid_provider_response"):
                await adapter.infer(rerank_config(provider), "synthetic-key", body)
        response = rerank_response(provider, [{"index": 0, "relevance_score": 0.5}])
        response["usage"] = {"total_tokens": True}
        with pytest.raises(LLMError, match="invalid_provider_response"):
            await adapter.infer(rerank_config(provider), None, body.model_copy(update={"top_n": 1}))
        if provider == "dashscope_rerank":
            for output in [None, [], False, "bad"]:
                response = {"output": output}
                with pytest.raises(LLMError, match="invalid_provider_response"):
                    await adapter.infer(rerank_config(provider), None, body)


@pytest.mark.asyncio
async def test_native_rerank_200_error_envelope_is_sanitized_without_retry():
    calls = []

    async def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                **rerank_response("dashscope_rerank"),
                "code": "InvalidApiKey",
                "message": "private prompt and synthetic-key",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = AdapterRegistry(client).get("dashscope_rerank")
        with pytest.raises(LLMError, match="provider_rejected") as error:
            await adapter.infer(
                rerank_config(),
                "synthetic-key",
                RerankRequest(configuration_id=uuid4(), query="q", documents=["a", "b"], top_n=1),
            )
    assert len(calls) == 1
    assert error.value.provider_request_id == "req_bailian_1"
    assert error.value.message == "Provider rejected the request"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["dashscope_rerank", "dashscope_rerank_compatible"])
async def test_dashscope_rerank_retries_only_explicit_transient_status_and_closes_on_timeout(provider):
    calls, status = [], 429
    stream = Chunks([], hang=True)

    async def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(status, text="private upstream error", headers={"retry-after": "0"})
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = AdapterRegistry(client).get(provider)
        body = RerankRequest(configuration_id=uuid4(), query="q", documents=["a"])
        with pytest.raises(LLMError, match="provider_timeout"):
            await adapter.infer(rerank_config(provider, timeout_seconds=0.02), None, body)
        assert len(calls) == 2 and stream.closed
        calls.clear()
        status = 401
        with pytest.raises(LLMError, match="provider_rejected"):
            await adapter.infer(rerank_config(provider), None, body)
        assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_qwen_chat_explicit_nonthinking_payload_and_no_reasoning_output(streaming):
    config = ModelConfig(
        name="Qwen chat",
        provider="openai",
        provider_model="qwen3.7-plus-2026-05-26",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        capability="chat",
        chat_enable_thinking=False,
    )

    async def handle(request):
        assert str(request.url) == (
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        )
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3.7-plus-2026-05-26"
        assert payload["enable_thinking"] is False
        assert payload["max_completion_tokens"] == 4096
        assert "extra_body" not in payload and "max_tokens" not in payload
        choice = {"index": 0, "finish_reason": "stop"}
        message = {"role": "assistant", "content": "answer", "reasoning_content": "hidden"}
        if streaming:
            assert payload["stream_options"] == {"include_usage": True}
            return httpx.Response(
                200,
                stream=Chunks(
                    [
                        sse({"choices": [{**choice, "delta": message}]}),
                        sse({"choices": [], "usage": {"total_tokens": 8}}),
                        b"data: [DONE]\n\n",
                    ]
                ),
            )
        return httpx.Response(200, json={"choices": [{**choice, "message": message}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = AdapterRegistry(client).get("openai")
        body = ChatRequest(
            configuration_id=uuid4(), messages=[{"role": "user", "content": "q"}], stream=streaming
        )
        if streaming:
            result = [event async for event in adapter.stream(config, None, body)]
            assert result[-1]["type"] == "completed" and result[-1]["usage"] == {"total_tokens": 8}
        else:
            result = await adapter.infer(config, None, body)
            assert result["content"] == "answer"
        assert "hidden" not in json.dumps(result) and "reasoning_content" not in json.dumps(result)


def test_thinking_option_rejected_for_other_capabilities():
    with pytest.raises(ValidationError):
        rerank_config(chat_enable_thinking=False)


@pytest.mark.asyncio
async def test_bailian_embedding_v4_dimensions_and_batch_limit():
    config = ModelConfig(
        name="Bailian embedding",
        provider="openai",
        provider_model="text-embedding-v4",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        capability="embedding",
        dimensions=1024,
        request_dimensions=True,
        max_batch_size=10,
    )
    calls = []

    async def handle(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert str(request.url) == "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
        assert payload == {
            "model": "text-embedding-v4",
            "input": ["q"] * 10,
            "dimensions": 1024,
            "encoding_format": "float",
        }
        return httpx.Response(
            200,
            json={"data": [{"index": i, "embedding": [0.0] * 1024} for i in range(10)]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = AdapterRegistry(client).get("openai")
        result = await adapter.infer(
            config, None, EmbeddingRequest(configuration_id=uuid4(), input=["q"] * 10)
        )
        assert result["dimensions"] == 1024 and len(result["embeddings"]) == 10
        with pytest.raises(LLMError, match="model_limit_exceeded"):
            await adapter.infer(
                config, None, EmbeddingRequest(configuration_id=uuid4(), input=["q"] * 11)
            )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_native_rerank_public_probe_and_internal_usage_are_durable_and_safe(api):
    async def handle(request):
        body = json.loads(request.content)
        assert "/services/rerank/text-rerank/text-rerank" in str(request.url)
        assert request.headers["authorization"] == "Bearer synthetic-bailian-key"
        return httpx.Response(
            200,
            json=rerank_response(
                "dashscope_rerank",
                [{"index": i, "relevance_score": 0.8} for i in range(body["parameters"]["top_n"])],
            ),
        )

    api.state.handler = handle
    response = await api.client.post(
        "/api/v1/models",
        headers=api.headers(),
        json={**rerank_config().model_dump(), "credential": "synthetic-bailian-key"},
    )
    assert response.status_code == 201, response.text
    model = response.json()
    assert model["has_credential"] and model["test_state"] == "untested"
    probe = await api.client.post(
        f"/api/v1/models/{model['id']}/test",
        headers=api.headers(),
        json={"configuration_id": model["configuration_id"]},
    )
    assert probe.status_code == 200 and probe.json()["test_state"] == "passed", probe.text
    inference = await api.client.post(
        "/internal/v1/rerank",
        headers=api.headers("retrieval", False),
        json={"configuration_id": model["configuration_id"], "query": "private query", "documents": ["a"]},
    )
    assert inference.status_code == 200, inference.text
    assert inference.json()["request_id"] == "req_bailian_1"
    records = await api.client.get(f"/api/v1/models/{model['id']}/usage", headers=api.headers())
    assert records.status_code == 200 and "req_bailian_1" in records.text
    assert "synthetic-bailian-key" not in model.__str__() + records.text
    assert "private query" not in records.text


@pytest.mark.asyncio
async def test_chat_thinking_config_versions_remain_pinned(api):
    config = ModelConfig(
        name="Bailian chat",
        provider="openai",
        provider_model="qwen3.7-plus-2026-05-26",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        capability="chat",
        chat_enable_thinking=False,
    )
    created = await api.client.post("/api/v1/models", headers=api.headers(), json=config.model_dump())
    assert created.status_code == 201, created.text
    model = created.json()
    updated = await api.client.put(
        f"/api/v1/models/{model['id']}",
        headers=api.headers(),
        json={
            "base_configuration_id": model["configuration_id"],
            "config": config.model_copy(update={"chat_enable_thinking": None}).model_dump(),
        },
    )
    assert updated.status_code == 200, updated.text
    for version, expected in [(model, False), (updated.json(), None)]:
        response = await api.client.post(
            "/internal/v1/chat",
            headers=api.headers("agent", False),
            json={"configuration_id": version["configuration_id"], "messages": [{"role": "user", "content": "q"}]},
        )
        assert response.status_code == 200, response.text
        payload = json.loads(api.state.calls[-1].content)
        assert payload.get("enable_thinking") is expected
        if expected is None:
            assert "enable_thinking" not in payload
