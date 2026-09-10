"""Explicit provider contracts over HTTPX; no model-name guessing or fallback."""

from __future__ import annotations

import asyncio
import json
import math
import re
from contextlib import asynccontextmanager

import httpx
from anyio import CancelScope
from pydantic import ValidationError

from .schemas import ChatRequest, EmbeddingRequest, LLMError, ToolCall, validate_limits


def invalid():
    return LLMError(502, "invalid_provider_response", "Provider response failed validation")


def strict_json(value):
    try:
        return json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise invalid() from None


def usage(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise invalid()
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "search_units"):
        if key in value:
            if type(value[key]) is not int or not 0 <= value[key] <= 10**12:
                raise invalid()
            result[key] = value[key]
    return result


def request_id(value):
    # Provider-controlled arbitrary text must never reach audit/log/output.
    return (
        value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,200}", value) else None
    )


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_chat(message, finish):
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise invalid()
    content = message.get("content")
    if content is not None and (not isinstance(content, str) or len(content) > 1_000_000):
        raise invalid()
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list) or len(calls) > 20:
        raise invalid()
    try:
        calls = [ToolCall.model_validate(call).model_dump() for call in calls]
    except (ValidationError, ValueError):
        raise invalid() from None
    if len({call["id"] for call in calls}) != len(calls):
        raise invalid()
    if finish not in {"stop", "length", "tool_calls", "content_filter"}:
        raise invalid()
    if finish == "tool_calls" and not calls:
        raise invalid()
    if content is None and not calls and finish != "content_filter":
        raise invalid()
    return {"content": content, "tool_calls": calls, "finish_reason": finish}


class HTTPAdapter:
    capabilities: frozenset[str] = frozenset()

    def __init__(self, client):
        self.client = client

    @asynccontextmanager
    async def response(self, config, secret, path, payload):
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = "Bearer " + secret
        for attempt in range(config.max_retries + 1):
            retry_delay = None
            outgoing = self.client.build_request(
                "POST",
                config.base_url + path,
                headers=headers,
                json=payload,
                timeout=config.timeout_seconds,
            )
            response = await self.client.send(outgoing, stream=True, follow_redirects=False)
            try:
                if 200 <= response.status_code < 300:
                    yield response
                    return
                if (
                    response.status_code in {429, 500, 502, 503, 504}
                    and attempt < config.max_retries
                ):
                    value = response.headers.get("retry-after", "")
                    retry_delay = (
                        min(float(value), 2.0)
                        if re.fullmatch(r"\d+(\.\d+)?", value)
                        else min(0.1 * 2**attempt, 2)
                    )
                else:
                    raise LLMError(
                        502,
                        "provider_rejected",
                        "Provider rejected the request",
                        request_id(response.headers.get("x-request-id")),
                    )
            finally:
                # A disconnect repeatedly cancels ASGI awaits. Complete transport cleanup
                # under its own shield, bounded independently from the inference deadline.
                with CancelScope(shield=True):
                    async with asyncio.timeout(5):
                        await response.aclose()
            # An explicit failed response is safe to retry before any output is consumed.
            await asyncio.sleep(retry_delay)

    async def infer(self, config, secret, body):
        validate_limits(config, body)
        rid = None
        try:
            async with asyncio.timeout(config.timeout_seconds):
                path, payload = self.payload(config, body)
                async with self.response(config, secret, path, payload) as response:
                    rid = request_id(response.headers.get("x-request-id"))
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > config.max_response_bytes:
                            raise invalid()
                    value = strict_json(raw)
                    if not isinstance(value, dict):
                        raise invalid()
                    result = self.normalize(config, body, value)
                    result["request_id"] = request_id(
                        response.headers.get("x-request-id") or value.get("id")
                    )
                    return result
        except LLMError as error:
            error.provider_request_id = error.provider_request_id or rid
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise LLMError(
                504, "provider_timeout", "Provider request deadline exceeded", rid
            ) from None
        except httpx.HTTPError:
            # Network errors have an unknown processing outcome; do not resend.
            raise LLMError(502, "provider_unavailable", "Provider connection failed", rid) from None
        except (KeyError, TypeError, ValueError, OverflowError):
            raise invalid() from None


class OpenAIAdapter(HTTPAdapter):
    capabilities = frozenset({"chat", "embedding"})

    def payload(self, config, body):
        if isinstance(body, EmbeddingRequest):
            payload = {
                "model": config.provider_model,
                "input": body.input,
                "encoding_format": "float",
            }
            if config.request_dimensions:
                payload["dimensions"] = config.dimensions
            return "/embeddings", payload
        if not isinstance(body, ChatRequest):
            raise LLMError(422, "unsupported_capability", "Adapter does not support this operation")
        payload = {
            "model": config.provider_model,
            "messages": [m.model_dump(exclude_none=True) for m in body.messages],
            config.chat_token_parameter: body.max_output_tokens or config.max_output_tokens,
            "stream": body.stream,
        }
        if body.tools:
            payload["tools"] = [t.model_dump(exclude_none=True) for t in body.tools]
        if body.temperature is not None:
            payload["temperature"] = body.temperature
        if body.response_format is not None:
            payload["response_format"] = body.response_format
        if body.stream:
            payload["stream_options"] = {"include_usage": True}
        return "/chat/completions", payload

    def normalize(self, config, body, value):
        if isinstance(body, EmbeddingRequest):
            data = value.get("data")
            if not isinstance(data, list) or len(data) != len(body.input):
                raise invalid()
            vectors = [None] * len(data)
            for item in data:
                if not isinstance(item, dict):
                    raise invalid()
                idx, vector = item.get("index"), item.get("embedding")
                if (
                    type(idx) is not int
                    or not 0 <= idx < len(data)
                    or vectors[idx] is not None
                    or not isinstance(vector, list)
                    or len(vector) != config.dimensions
                    or not all(numeric(v) for v in vector)
                ):
                    raise invalid()
                vectors[idx] = vector
            return {
                "embeddings": vectors,
                "dimensions": config.dimensions,
                "usage": usage(value.get("usage")),
            }
        choices = value.get("choices")
        if (
            not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
            or type(choices[0].get("index")) is not int
            or choices[0]["index"] != 0
        ):
            raise invalid()
        return {
            **validate_chat(choices[0].get("message"), choices[0].get("finish_reason")),
            "usage": usage(value.get("usage")),
        }

    async def stream(self, config, secret, body):
        validate_limits(config, body)
        rid = None
        try:
            async with asyncio.timeout(config.timeout_seconds):
                path, payload = self.payload(config, body)
                async with self.response(config, secret, path, payload) as response:
                    rid = request_id(response.headers.get("x-request-id"))
                    total, buffer, content, calls, finish, counts, done = (
                        0,
                        b"",
                        "",
                        {},
                        None,
                        {},
                        False,
                    )
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > config.max_response_bytes:
                            raise invalid()
                        buffer += chunk
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            line = line.rstrip(b"\r")
                            if not line or line.startswith(b":"):
                                continue
                            if not line.startswith(b"data:"):
                                raise invalid()
                            data = line[5:].strip()
                            if data == b"[DONE]":
                                done = True
                                break
                            value = strict_json(data)
                            if not isinstance(value, dict):
                                raise invalid()
                            rid = rid or request_id(value.get("id"))
                            if value.get("usage") is not None:
                                counts = usage(value["usage"])
                            choices = value.get("choices")
                            if not isinstance(choices, list) or len(choices) > 1:
                                raise invalid()
                            if not choices:
                                if value.get("usage") is None:
                                    raise invalid()
                                continue
                            choice = choices[0]
                            if (
                                not isinstance(choice, dict)
                                or type(choice.get("index")) is not int
                                or choice["index"] != 0
                                or finish is not None
                            ):
                                raise invalid()
                            delta = choice.get("delta")
                            if (
                                not isinstance(delta, dict)
                                or delta.get("role", "assistant") != "assistant"
                            ):
                                raise invalid()
                            if delta.get("content") is not None:
                                text = delta["content"]
                                if not isinstance(text, str):
                                    raise invalid()
                                content += text
                                if text:
                                    yield {"type": "content_delta", "delta": text}
                            additions = delta.get("tool_calls") or []
                            if not isinstance(additions, list) or len(additions) > 20:
                                raise invalid()
                            for addition in additions:
                                if not isinstance(addition, dict):
                                    raise invalid()
                                idx = addition.get("index")
                                if type(idx) is not int or not 0 <= idx < 20:
                                    raise invalid()
                                call = calls.setdefault(
                                    idx,
                                    {
                                        "id": "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    },
                                )
                                if addition.get("type", "function") != "function":
                                    raise invalid()
                                function = addition.get("function") or {}
                                if not isinstance(function, dict):
                                    raise invalid()
                                for owner, key, fragment in (
                                    (call, "id", addition.get("id")),
                                    (call["function"], "name", function.get("name")),
                                    (call["function"], "arguments", function.get("arguments")),
                                ):
                                    if fragment is not None:
                                        if not isinstance(fragment, str):
                                            raise invalid()
                                        owner[key] += fragment
                                if (
                                    len(call["function"]["arguments"]) > 65536
                                    or len(call["id"]) > 200
                                    or len(call["function"]["name"]) > 64
                                ):
                                    raise invalid()
                                # Only complete, validated tool calls are exposed; argument fragments remain private until JSON is well-formed.
                            if choice.get("finish_reason") is not None:
                                finish = choice["finish_reason"]
                        if done:
                            break
                    if not done or finish is None:
                        raise invalid()
                    result = validate_chat(
                        {
                            "role": "assistant",
                            "content": content or None,
                            "tool_calls": [calls[i] for i in sorted(calls)],
                        },
                        finish,
                    )
                    for idx, call in enumerate(result["tool_calls"]):
                        yield {"type": "tool_call_delta", "index": idx, "tool_call": call}
                    yield {"type": "completed", **result, "usage": counts, "request_id": rid}
        except LLMError as error:
            error.provider_request_id = error.provider_request_id or rid
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise LLMError(
                504, "provider_timeout", "Provider request deadline exceeded", rid
            ) from None
        except httpx.HTTPError:
            raise LLMError(
                502, "provider_unavailable", "Provider stream interrupted", rid
            ) from None
        except (KeyError, TypeError, ValueError, OverflowError):
            raise invalid() from None


class CohereAdapter(HTTPAdapter):
    capabilities = frozenset({"rerank"})

    def payload(self, config, body):
        return "/rerank", {
            "model": config.provider_model,
            "query": body.query,
            "documents": body.documents,
            "top_n": body.top_n or len(body.documents),
        }

    def normalize(self, config, body, value):
        results = value.get("results")
        if not isinstance(results, list) or len(results) != (body.top_n or len(body.documents)):
            raise invalid()
        normalized, indices = [], set()
        for item in results:
            if not isinstance(item, dict):
                raise invalid()
            idx, score = item.get("index"), item.get("relevance_score")
            if (
                type(idx) is not int
                or not 0 <= idx < len(body.documents)
                or idx in indices
                or not numeric(score)
            ):
                raise invalid()
            indices.add(idx)
            normalized.append({"index": idx, "score": score})
        meta = value.get("meta", {})
        if not isinstance(meta, dict):
            raise invalid()
        return {"results": normalized, "usage": usage(meta.get("billed_units"))}


class DashScopeRerankAdapter(HTTPAdapter):
    """Alibaba Bailian native rerank protocol (see BAILIAN.md).

    Appends /services/rerank/text-rerank/text-rerank, sends
    {model, input:{query,documents}, parameters:{top_n}}, reads
    output.results plus top-level usage and request_id.
    """
    capabilities = frozenset({"rerank"})

    def payload(self, config, body):
        return "/services/rerank/text-rerank/text-rerank", {
            "model": config.provider_model,
            "input": {
                "query": body.query,
                "documents": body.documents,
            },
            "parameters": {
                "top_n": body.top_n or len(body.documents),
            },
        }

    def normalize(self, config, body, value):
        output = value.get("output")
        if not isinstance(output, dict):
            raise invalid()
        results = output.get("results")
        if not isinstance(results, list) or len(results) != (body.top_n or len(body.documents)):
            raise invalid()
        normalized, indices = [], set()
        for item in results:
            if not isinstance(item, dict):
                raise invalid()
            idx, score = item.get("index"), item.get("relevance_score")
            if (
                type(idx) is not int
                or not 0 <= idx < len(body.documents)
                or idx in indices
                or not numeric(score)
            ):
                raise invalid()
            indices.add(idx)
            normalized.append({"index": idx, "score": score})
        return {"results": normalized, "usage": usage(value.get("usage"))}


class DashScopeRerankCompatibleAdapter(HTTPAdapter):
    """Alibaba Bailian workspace compatible-mode rerank (see BAILIAN.md).

    Appends /reranks (plural), sends flat {model,query,documents,top_n},
    reads top-level results, usage and id.
    """
    capabilities = frozenset({"rerank"})

    def payload(self, config, body):
        return "/reranks", {
            "model": config.provider_model,
            "query": body.query,
            "documents": body.documents,
            "top_n": body.top_n or len(body.documents),
        }

    def normalize(self, config, body, value):
        results = value.get("results")
        if not isinstance(results, list) or len(results) != (body.top_n or len(body.documents)):
            raise invalid()
        normalized, indices = [], set()
        for item in results:
            if not isinstance(item, dict):
                raise invalid()
            idx, score = item.get("index"), item.get("relevance_score")
            if (
                type(idx) is not int
                or not 0 <= idx < len(body.documents)
                or idx in indices
                or not numeric(score)
            ):
                raise invalid()
            indices.add(idx)
            normalized.append({"index": idx, "score": score})
        return {"results": normalized, "usage": usage(value.get("usage"))}


class AdapterRegistry:
    def __init__(self, client):
        self.adapters = {
            "openai": OpenAIAdapter(client),
            "cohere": CohereAdapter(client),
            "dashscope_rerank": DashScopeRerankAdapter(client),
            "dashscope_rerank_compatible": DashScopeRerankCompatibleAdapter(client),
        }

    def register(self, name, adapter):
        if name in self.adapters or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise ValueError("Adapter name is invalid or already registered")
        self.adapters[name] = adapter

    def get(self, name):
        try:
            return self.adapters[name]
        except KeyError:
            raise LLMError(422, "unknown_provider", "Provider adapter is not installed") from None
