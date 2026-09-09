"""Web-only usage boundary from Auth's verified metadata, never local JWT claims."""

import time

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_platform.common.auth import Principal

from .schemas import KnowledgeError


class WebIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    principal: Principal
    scopes: list[str]
    audiences: list[str]
    issuer: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    expires_at: int
    delegated: bool = False


async def require_web_identity(authorizer, token, principal, clients, audiences):
    response = await authorizer.request("POST", "/internal/v1/resolve", "auth", token, {})
    try:
        identity = WebIdentity.model_validate(response.json())
    except (ValueError, TypeError, ValidationError):
        raise KnowledgeError(503, "authorization_unavailable", "Verified Web identity unavailable") from None
    expected = Principal.model_validate(vars(principal) if not isinstance(principal, Principal) else principal)
    if identity.principal != expected:
        raise KnowledgeError(503, "authorization_changed", "Verified identity changed during operation")
    if (
        identity.delegated or identity.principal.channel_context is not None
        or identity.client_id not in clients
        or "knowledge-api" not in identity.audiences
        or not set(identity.audiences) <= set(audiences)
        or "knowledge:read" not in identity.scopes
        or identity.expires_at <= time.time()
    ):
        raise KnowledgeError(403, "forbidden", "Personal usage requires an authorized Web client")