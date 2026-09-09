"""Transport-neutral operation preparation; authorization stays in the existing transport.

Types describe JSON wire shapes. Domain validators and live authorization remain
server responsibilities. This module does not deserialize an untyped success into
an invented typed object, cache responses, retain credentials, or choose origins.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote, urlencode

import httpx


type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True)
class Operation:
    id: str
    service: str
    method: str
    template: str
    path_parameters: tuple[str, ...]
    query_parameters: tuple[str, ...]
    header_parameters: tuple[str, ...]
    body_required: bool
    accepts_body: bool
    response_typing: str
    transport: str
    request_schema: str | None
    response_schema: str | None


@dataclass(frozen=True)
class Prepared:
    service: str
    method: str
    path: str
    # Exact JSON bytes are frozen so later caller mutations cannot change a request.
    body: bytes | None
    headers: tuple[tuple[str, str], ...]


def prepare(
    operation: Operation, *, path: dict[str, str] | None = None,
    query: dict[str, str | int | bool | list[str]] | None = None,
    body: JsonValue = None, headers: dict[str, str] | None = None,
) -> Prepared:
    path, query, headers = path or {}, query or {}, headers or {}
    if set(path) != set(operation.path_parameters):
        raise ValueError('Exact declared path parameters required')
    if set(query) - set(operation.query_parameters):
        raise ValueError('Unknown query parameter')
    permitted = {name.lower() for name in operation.header_parameters}
    if any(name.lower() not in permitted or name.lower() in {'authorization', 'x-service-token', 'cookie', 'host'} for name in headers):
        raise ValueError('Identity or undeclared headers are transport-owned')
    target = operation.template
    for name, value in path.items():
        if not isinstance(value, str) or not value or value in {'.', '..'}:
            raise ValueError('Nonempty literal path segment required')
        target = target.replace('{' + name + '}', quote(value, safe=''))
    if not target.startswith('/') or target.startswith('//') or '{' in target:
        raise ValueError('Invalid service-relative path')
    pairs = []
    for name in sorted(query):
        values = query[name] if isinstance(query[name], list) else [query[name]]
        for value in values:
            pairs.append((name, str(value).lower() if isinstance(value, bool) else str(value)))
    if pairs:
        target += '?' + urlencode(pairs)
    if (body is None and operation.body_required) or (body is not None and not operation.accepts_body):
        raise ValueError('Request body does not match declared operation')
    encoded = None if body is None else json.dumps(body, allow_nan=False, ensure_ascii=False, separators=(',', ':')).encode()
    if any('\r' in name + value or '\n' in name + value for name, value in headers.items()):
        raise ValueError('Invalid header value')
    return Prepared(operation.service, operation.method, target, encoded, tuple(sorted(headers.items())))


class JSONTransport(Protocol):
    async def request(self, method: str, path: str, target: str, token: str | None = None, json=None) -> httpx.Response: ...


async def execute_json(transport: JSONTransport, operation: Operation, prepared: Prepared, *, token: str | None = None) -> httpx.Response:
    """Use HTTPAuthorizer or another existing signing transport, without keeping identity.

    SSE remains the existing transport's streaming API. Extra transport headers
    (e.g. Last-Event-ID) must be handled there rather than silently discarded here.
    The returned HTTP response is intentionally raw; generated types do not replace
    runtime schema, error, evidence or authorization validation.
    """
    if operation.transport != 'http-json' or prepared.headers:
        raise ValueError('Use the existing streaming/header-aware transport for this operation')
    if prepared.service != operation.service or prepared.method != operation.method:
        raise ValueError('Prepared operation mismatch')
    return await transport.request(prepared.method, prepared.path, prepared.service, token=token, json=None if prepared.body is None else json.loads(prepared.body))