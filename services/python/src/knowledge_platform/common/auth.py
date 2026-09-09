from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .config import Settings
from .observability import trace_headers
from .security import ServiceSecurity

ChannelKey = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:-]+$")]


class ChannelConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    context_id: ChannelKey
    channel_id: ChannelKey
    conversation_key: ChannelKey
    agent_id: ChannelKey
    agent_configuration_id: ChannelKey
    space_ids: list[ChannelKey] = Field(min_length=1, max_length=100)
    chat_type: Literal["single", "group"]
    audience_id: str = Field(max_length=256, pattern=r"^[A-Za-z0-9_.:-]*$")
    group_key: str = Field(max_length=256, pattern=r"^[A-Za-z0-9_.:-]*$")
    message_id: ChannelKey
    read_run_id: ChannelKey | None = None

    @model_validator(mode="after")
    def audience_boundary(self):
        if len(set(self.space_ids)) != len(self.space_ids):
            raise ValueError("Duplicate channel scope")
        if self.chat_type == "group":
            if not self.audience_id or not self.group_key:
                raise ValueError("Group audience must be explicit")
        elif self.audience_id or self.group_key:
            raise ValueError("Private conversation cannot carry a group audience")
        return self


class Principal(BaseModel):
    id: str
    subjects: list[str]
    permissions: list[str] = Field(default_factory=list)
    auth_epoch: int
    channel_context: ChannelConstraint | None = None


def channel_audience(principal) -> str | None:
    context = getattr(principal, "channel_context", None)
    return context.audience_id if context and context.chat_type == "group" else None


def policies_visible(principal, policies, clauses) -> bool:
    """Actor and channel audience must each satisfy every provenance ACL clause."""
    subjects = set(principal.subjects)
    if not all(subjects.intersection(clause) for clause in clauses):
        return False
    context = getattr(principal, "channel_context", None)
    if context is None:
        return True
    if any(policy.space_id not in context.space_ids for policy in policies):
        return False
    audience = channel_audience(principal)
    return audience is None or all(f"audience:{audience}" in clause for clause in clauses)


class Decision(BaseModel):
    allowed: bool
    auth_epoch: int
    acl_domain: str = ""
    acl_version: int = 0


class HTTPAuthorizer:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.security = ServiceSecurity.from_settings(settings)
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=settings.request_timeout, follow_redirects=False
        )

    async def close(self):
        if self._owned_client:
            await self.client.aclose()

    async def request(
        self, method: str, path: str, target: str, token: str | None = None, json=None
    ) -> httpx.Response:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Internal API paths must be relative to a configured service")
        headers = {**trace_headers(), "X-Service-Token": self.security.issue(target)}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = await self.client.request(
                method,
                self.settings.url_for(target) + path,
                headers=headers,
                json=json,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise HTTPException(503, "Required internal service unavailable") from None
        if response.status_code in {401, 403}:
            raise HTTPException(response.status_code, "Authentication or authorization denied")
        if not 200 <= response.status_code < 300:
            raise HTTPException(503, "Required internal service unavailable")
        return response

    @staticmethod
    def _json(response):
        try:
            return response.json()
        except ValueError:
            raise HTTPException(503, "Invalid authorization response") from None

    async def resolve(self, token: str) -> Principal:
        response = await self.request("POST", "/internal/v1/resolve", "auth", token, {})
        try:
            resolved = self._json(response)
            principal = Principal.model_validate(resolved["principal"])
            delegated = resolved.get("delegated", False)
            if type(delegated) is not bool or delegated != (principal.channel_context is not None):
                raise ValueError("Incomplete channel delegation")
            return principal
        except (KeyError, TypeError, ValueError, ValidationError):
            raise HTTPException(503, "Invalid authorization response") from None

    async def authorize(
        self, token: str, action: str, space_id: str, resource_id: str | None = None
    ) -> Decision:
        body = {"action": action, "space_id": space_id}
        if resource_id is not None:
            body["resource_id"] = resource_id
        response = await self.request("POST", "/internal/v1/authorize", "auth", token, body)
        try:
            return Decision.model_validate(self._json(response))
        except ValidationError:
            raise HTTPException(503, "Invalid authorization response") from None

    async def require(
        self, token: str, action: str, space_id: str, resource_id: str | None = None
    ) -> Decision:
        decision = await self.authorize(token, action, space_id, resource_id)
        if not decision.allowed:
            raise HTTPException(403, "Resource access denied")
        return decision

    async def policy(self, space_id: str, resource_id: str) -> dict:
        path = f"/internal/v1/policies/{quote(space_id, safe='')}/{quote(resource_id, safe='')}"
        response = await self.request("GET", path, "iam")
        value = self._json(response)
        if not isinstance(value, dict):
            raise HTTPException(503, "Invalid policy response")
        return value
"""Real PostgreSQL and signed HTTP lifecycle authorization/idempotency boundaries."""

