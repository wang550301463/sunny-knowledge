"""Bounded HTTP clients. No arbitrary URLs, JSON identities or opaque dependency errors."""
import codecs

import httpx

from knowledge_platform.common.observability import trace_headers

from .schemas import fail, load_json


class Clients:
    def __init__(self, settings, authorizer):
        self.settings, self.auth = settings, authorizer
        self.client = authorizer.client

    def headers(self, target, token):
        result = {**trace_headers(), 'X-Service-Token':self.auth.security.issue(target)}
        if token is not None:
            result['Authorization'] = 'Bearer ' + token
        return result

    async def call(self, target, method, path, token=None, body=None):
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Configured relative service path required')
        try:
            async with self.client.stream(method, self.settings.url_for(target)+path, headers=self.headers(target, token), json=body, follow_redirects=False, timeout=self.settings.request_timeout) as response:
                if response.status_code not in range(200, 300):
                    status = response.status_code if response.status_code in {401,403,404,409,422} else 503
                    raise fail('dependency_request_failed', 'Required internal operation failed', status)
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.settings.agent_max_response_bytes:
                        raise fail('dependency_response_too_large')
                    chunks.append(chunk)
                try:
                    value = load_json(b''.join(chunks).decode())
                    if not isinstance(value, dict):
                        raise ValueError
                    return value
                except (ValueError, UnicodeError):
                    raise fail('invalid_dependency_response') from None
        except httpx.HTTPError:
            raise fail('dependency_unavailable') from None

    async def chat(self, configuration_id, messages, tools, max_output_tokens):
        payload = {'configuration_id':configuration_id, 'messages':messages, 'tools':tools, 'max_output_tokens':max_output_tokens, 'temperature':0.1, 'stream':True}
        completed = None
        try:
            async with self.client.stream('POST', self.settings.llm_url.rstrip('/')+'/internal/v1/chat', headers=self.headers('llm', None), json=payload, timeout=None, follow_redirects=False) as response:
                if response.status_code != 200 or response.headers.get('content-type','').split(';')[0] != 'text/event-stream':
                    raise fail('model_unavailable')
                decoder = codecs.getincrementaldecoder('utf-8')('strict')
                buffer, size = '', 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.settings.agent_max_response_bytes:
                        raise fail('model_response_too_large')
                    buffer += decoder.decode(chunk)
                    while '\n\n' in buffer:
                        frame, buffer = buffer.split('\n\n', 1)
                        event, data = None, []
                        for line in frame.split('\n'):
                            if line.startswith('event: '):
                                event = line[7:]
                            elif line.startswith('data: '):
                                data.append(line[6:])
                        if event is None and not data:
                            continue
                        value = load_json('\n'.join(data))
                        if not isinstance(value, dict) or value.get('type') != event or completed is not None:
                            raise fail('invalid_model_stream')
                        if event == 'error':
                            raise fail('model_failed', 'Model invocation failed')
                        if event == 'completed':
                            completed = value
                        elif event not in {'content_delta','tool_call_delta'}:
                            raise fail('invalid_model_stream')
                buffer += decoder.decode(b'', final=True)
                if buffer.strip() or completed is None or completed.get('configuration_id') != configuration_id:
                    raise fail('incomplete_model_stream')
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise fail('model_transport_failed') from None
        calls = completed.get('tool_calls')
        content = completed.get('content')
        usage = completed.get('usage') or {}
        if not isinstance(calls, list) or len(calls) > 20 or (content is not None and not isinstance(content,str)) or not isinstance(usage,dict):
            raise fail('invalid_model_response')
        if completed.get('finish_reason') not in {'stop','length','tool_calls','content_filter'}:
            raise fail('invalid_model_response')
        safe_usage = {}
        for key in ('prompt_tokens','completion_tokens','total_tokens'):
            value = usage.get(key)
            if value is not None:
                if type(value) is not int or not 0 <= value <= 1_000_000_000:
                    raise fail('invalid_model_response')
                safe_usage[key] = value
        return {'content': content, 'tool_calls':calls, 'usage':safe_usage, 'finish_reason':completed['finish_reason']}