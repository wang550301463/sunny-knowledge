import asyncio
import importlib.util
import json
from uuid import uuid4

import httpx
import pytest


def test_llm_service_exists():
    assert importlib.util.find_spec('knowledge_platform.llm.app') is not None


def config(capability='embedding', **kwargs):
    from knowledge_platform.llm.schemas import ModelConfig
    return ModelConfig(name='configured', provider='openai' if capability != 'rerank' else 'cohere',
                       provider_model='explicit-provider-model', base_url='https://provider.test/v1',
                       capability=capability, dimensions=2 if capability == 'embedding' else None,
                       **kwargs)


@pytest.mark.asyncio
async def test_embedding_order_dimensions_finite_and_request_contract():
    from knowledge_platform.llm.adapters import AdapterRegistry
    from knowledge_platform.llm.schemas import EmbeddingRequest, LLMError
    captured = []
    payload = {'data': [{'index': 1, 'embedding': [3, 4]}, {'index': 0, 'embedding': [1, 2]}],
               'usage': {'prompt_tokens': 3, 'total_tokens': 3}}
    async def handle(request):
        captured.append(request)
        return httpx.Response(200, json=payload, headers={'x-request-id': 'req_123'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = AdapterRegistry(client).get('openai')
        body = EmbeddingRequest(configuration_id=uuid4(), input=['first', 'second'])
        result = await adapter.infer(config(), 'secret', body)
        assert result['embeddings'] == [[1, 2], [3, 4]]
        assert result['request_id'] == 'req_123'
        assert json.loads(captured[0].content) == {'model': 'explicit-provider-model', 'input': ['first', 'second'], 'encoding_format': 'float'}
        assert captured[0].headers['authorization'] == 'Bearer secret'
        for invalid in ([{'index': 0, 'embedding': [1, 2]}],
                        [{'index': 0, 'embedding': [1, 2]}, {'index': 0, 'embedding': [1, 2]}],
                        [{'index': 0, 'embedding': [1]}, {'index': 1, 'embedding': [2]}],
                        [{'index': 0, 'embedding': [True, 2]}, {'index': 1, 'embedding': [1, 2]}]):
            payload['data'] = invalid
            with pytest.raises(LLMError, match='invalid_provider_response'):
                await adapter.infer(config(), 'secret', body)


@pytest.mark.asyncio
async def test_rerank_unique_inrange_scores_and_safe_errors():
    from knowledge_platform.llm.adapters import AdapterRegistry
    from knowledge_platform.llm.schemas import LLMError, RerankRequest
    payload = {'results': [{'index': 1, 'relevance_score': .9}], 'meta': {'billed_units': {'search_units': 1}}}
    async def handle(request):
        assert request.url.path == '/v1/rerank'
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = AdapterRegistry(client).get('cohere')
        body = RerankRequest(configuration_id=uuid4(), query='q', documents=['a', 'b'], top_n=1)
        result = await adapter.infer(config('rerank'), 'secret', body)
        assert result['results'] == [{'index': 1, 'score': .9}]
        assert result['usage'] == {'search_units': 1}
        for invalid in ([{'index': 2, 'relevance_score': .9}], [{'index': True, 'relevance_score': .9}],
                        [{'index': 0, 'relevance_score': '0.9'}], []):
            payload['results'] = invalid
            with pytest.raises(LLMError, match='invalid_provider_response'):
                await adapter.infer(config('rerank'), 'secret', body)


@pytest.mark.asyncio
async def test_retry_only_429_and_5xx_never_reflects_provider_body():
    from knowledge_platform.llm.adapters import AdapterRegistry
    from knowledge_platform.llm.schemas import EmbeddingRequest, LLMError
    count = 0
    status = 429
    async def handle(request):
        nonlocal count
        count += 1
        if count <= 2:
            return httpx.Response(status, text='private-prompt-and-api-key', headers={'retry-after': '0'})
        return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1, 2]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = AdapterRegistry(client).get('openai')
        body = EmbeddingRequest(configuration_id=uuid4(), input=['secret prompt'])
        await adapter.infer(config(), 'api-key', body)
        assert count == 3
        count, status = 0, 401
        with pytest.raises(LLMError) as error:
            await adapter.infer(config(), 'api-key', body)
        assert count == 1
        assert 'private' not in str(error.value)
        assert error.value.code == 'provider_rejected'


@pytest.mark.asyncio
async def test_chat_tool_calls_no_hidden_reasoning_and_malformed_rejected():
    from knowledge_platform.llm.adapters import AdapterRegistry
    from knowledge_platform.llm.schemas import ChatRequest, LLMError
    payload = {'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'answer', 'reasoning_content': 'private chain'}}]}
    async def handle(request):
        body = json.loads(request.content)
        assert body['max_completion_tokens'] == 20
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = AdapterRegistry(client).get('openai')
        body = ChatRequest(configuration_id=uuid4(), messages=[{'role': 'user', 'content': 'q'}], max_output_tokens=20)
        result = await adapter.infer(config('chat'), 'secret', body)
        assert result['content'] == 'answer'
        assert 'private chain' not in json.dumps(result)
        payload['choices'][0]['message']['tool_calls'] = [{'id': 'call_1', 'type': 'function', 'function': {'name': 'search', 'arguments': '{broken'}}]
        with pytest.raises(LLMError, match='invalid_provider_response'):
            await adapter.infer(config('chat'), 'secret', body)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, fail=False, hang=False):
        self.chunks, self.fail, self.hang, self.closed = chunks, fail, hang, False
    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.hang:
            await asyncio.Event().wait()
        if self.fail:
            raise httpx.ReadError('prompt api-key')
    async def aclose(self):
        self.closed = True


def sse(value):
    return ('data: ' + json.dumps(value) + '\n\n').encode()


@pytest.mark.asyncio
async def test_stream_normalizes_and_rejects_partial_retry_and_closes():
    from knowledge_platform.llm.adapters import AdapterRegistry
    from knowledge_platform.llm.schemas import ChatRequest, LLMError
    stream = Chunks([sse({'choices': [{'index': 0, 'delta': {'content': 'answer', 'reasoning_content': 'hidden'}, 'finish_reason': None}]}),
                     sse({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}), b'data: [DONE]\n\n'])
    requests = []
    async def handle(request):
        requests.append(request)
        return httpx.Response(200, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = AdapterRegistry(client).get('openai')
        body = ChatRequest(configuration_id=uuid4(), messages=[{'role': 'user', 'content': 'q'}], stream=True)
        events = [event async for event in adapter.stream(config('chat'), 'secret', body)]
        assert [event['type'] for event in events] == ['content_delta', 'completed']
        assert 'hidden' not in json.dumps(events)
        assert stream.closed
        stream = Chunks([sse({'choices': [{'index': 0, 'delta': {'content': 'part'}, 'finish_reason': None}]})], fail=True)
        with pytest.raises(LLMError):
            _ = [event async for event in adapter.stream(config('chat'), 'secret', body)]
        assert len(requests) == 2
        assert stream.closed


@pytest.mark.asyncio
async def test_total_deadline_covers_stream_and_closes_response():
    from knowledge_platform.llm.adapters import AdapterRegistry
    from knowledge_platform.llm.schemas import ChatRequest, LLMError
    stream = Chunks([], hang=True)
    async def handle(request):
        return httpx.Response(200, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        body = ChatRequest(configuration_id=uuid4(), messages=[{'role': 'user', 'content': 'q'}], stream=True)
        with pytest.raises(LLMError) as error:
            _ = [event async for event in AdapterRegistry(client).get('openai').stream(config('chat', timeout_seconds=.05), 'secret', body)]
        assert error.value.code == 'provider_timeout'
        assert stream.closed


@pytest.mark.asyncio
async def test_limiter_queue_bound_and_cancellation_releases_permit():
    from knowledge_platform.llm.runtime import ModelLimiter
    from knowledge_platform.llm.schemas import LLMError
    limiter = ModelLimiter()
    started, release = asyncio.Event(), asyncio.Event()
    async def occupy():
        async with limiter.slot('model', 1, 1):
            started.set()
            await release.wait()
    task = asyncio.create_task(occupy())
    await started.wait()
    waiting = asyncio.create_task(occupy())
    await asyncio.sleep(.01)
    with pytest.raises(LLMError) as error:
        async with limiter.slot('model', 1, 1):
            pass
    assert error.value.code == 'model_busy'
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with limiter.slot('model', 1, 0):
        pass