"""Bounded, signed delegated calls; no token, content, or exception-body logging."""

import json
import time

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_platform.common.observability import trace_headers


class BoundaryError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=512)
    subjects: list[str]
    permissions: list[str] = Field(default_factory=list)
    auth_epoch: int = Field(ge=0, strict=True)


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal: Principal
    scopes: list[str]
    audiences: list[str]
    issuer: str
    client_id: str
    expires_at: int = Field(strict=True)


class DomainClient:
    def __init__(self, settings, security, client=None):
        self.settings, self.security = settings, security
        self.owned = client is None
        self.client = client or httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=False)

    async def close(self):
        if self.owned:
            await self.client.aclose()

    async def request(self, target, method, path, token, body=None):
        try:
            async with self.client.stream(method, self.settings.url_for(target) + path, json=body,
                                          headers={**trace_headers(), "X-Service-Token": self.security.issue(target), "Authorization": "Bearer " + token},
                                          follow_redirects=False, timeout=self.settings.request_timeout) as response:
                if response.status_code not in {200, 201, 202}:
                    status = response.status_code
                    raise BoundaryError(status if status in {401, 403, 404, 409, 422} else 503,
                                        {401: "invalid_token", 403: "forbidden", 404: "not_found", 409: "version_conflict", 422: "invalid_arguments"}.get(status, "dependency_unavailable"))
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.settings.mcp_max_response_bytes:
                        raise BoundaryError(502, "result_too_large")
                    chunks.append(chunk)
                result = json.loads(b"".join(chunks))
                if not isinstance(result, dict):
                    raise ValueError
                return result
        except (httpx.HTTPError, TimeoutError):
            raise BoundaryError(503, "dependency_unavailable") from None
        except (ValueError, TypeError, RecursionError):
            raise BoundaryError(503, "invalid_dependency_response") from None

    async def resolve(self, token, required_scope="knowledge:read"):
        result = await self.request("auth", "POST", "/internal/v1/resolve", token, {})
        try:
            identity = Identity.model_validate(result)
        except ValidationError:
            raise BoundaryError(503, "invalid_authorization_response") from None
        if (identity.issuer != self.settings.mcp_issuer_url
            or identity.client_id not in self.settings.mcp_allowed_client_ids
            or not {self.settings.mcp_resource_url, self.settings.mcp_api_audience} <= set(identity.audiences)
            or identity.expires_at <= time.time()):
            raise BoundaryError(401, "invalid_token")
        if not {"knowledge:read", required_scope} <= set(identity.scopes):
            raise BoundaryError(403, "insufficient_scope")
        return identity
