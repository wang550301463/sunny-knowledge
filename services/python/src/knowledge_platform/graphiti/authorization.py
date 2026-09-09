"""Canonical Wiki-input ACL metadata shared by the rebuildable projections."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .evidence import Key


class InputRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    page_id: Key
    revision_id: Key
    space_id: Key


_inputs = TypeAdapter(Annotated[list[InputRevision], Field(max_length=2000)])


def input_revision_resources(projection: dict) -> set[tuple[str, str]]:
    """Check the complete closure's identity shape, returning mandatory page policies.

    Canonical owns immutable revision existence, lineage traversal and live page
    ownership. Consumers additionally reject ambiguous identities or a truncated
    policy set. These constraints never count as new original-source evidence.
    """
    values = _inputs.validate_python(projection.get("input_revisions", []), strict=True)
    pairs, revisions = set(), {}
    pages = {projection["page_id"]: projection["space_id"]}
    for value in values:
        pair = (value.page_id, value.revision_id)
        identity = (value.space_id, value.page_id)
        if (
            pair in pairs
            or pages.get(value.page_id, value.space_id) != value.space_id
            or revisions.get(value.revision_id, identity) != identity
        ):
            raise ValueError("Ambiguous canonical Wiki lineage")
        pairs.add(pair)
        pages[value.page_id] = value.space_id
        revisions[value.revision_id] = identity
    return {(v.space_id, v.page_id) for v in values}
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
"""Current identity + complete policy fingerprint checks before any ES recall."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from pydantic import ValidationError

from knowledge_platform.common.auth import policies_visible

from .projection import clauses_for, policy_fingerprint
from .schemas import Policy, unavailable


@dataclass
class AuthorizationGuard:
    authorizer: object
    token: str
    principal: object

    @classmethod
    async def begin(cls, authorizer, token, space_ids):
        principal = await authorizer.resolve(token)
        guard = cls(authorizer, token, principal)
        for space in space_ids:
            decision = await authorizer.require(token, "read", space)
            guard.epoch(decision.auth_epoch)
        return guard

    def epoch(self, epoch):
        if epoch != self.principal.auth_epoch:
            raise unavailable("authorization_changed", "Authorization changed during the operation")

    async def finish(self):
        current = await self.authorizer.resolve(self.token)
        self.epoch(current.auth_epoch)
        if (
            current.id != self.principal.id
            or set(current.subjects) != set(self.principal.subjects)
            or getattr(current, "channel_context", None)
            != getattr(self.principal, "channel_context", None)
        ):
            raise unavailable("authorization_changed", "Authorization changed during the operation")


class PolicyCache:
    def __init__(self, ttl=5, max_entries=200000):
        self.ttl, self.max_entries = ttl, max_entries
        self.values = {}
        self.lock = asyncio.Lock()

    async def allowed(self, guard, records):
        expected = {}
        for record in records:
            for value in record["policies"]:
                policy = Policy.model_validate(value)
                expected[(policy.space_id, policy.resource_id)] = None
        epoch = guard.principal.auth_epoch
        async with self.lock:
            timestamp = time.monotonic()
            self.values = {
                key: value
                for key, value in self.values.items()
                if key[0] == epoch and value[0] > timestamp
            }
            missing = [key for key in sorted(expected) if (epoch, *key) not in self.values]
            for offset in range(0, len(missing), 1000):
                batch = missing[offset : offset + 1000]
                response = await guard.authorizer.request(
                    "POST",
                    "/internal/v1/policies/batch",
                    "iam",
                    json={"resources": [{"space_id": s, "resource_id": r} for s, r in batch]},
                )
                try:
                    payload = response.json()
                    guard.epoch(payload["auth_epoch"])
                    items = payload["items"]
                    if len(items) != len(batch) or {
                        (p["space_id"], p["resource_id"]) for p in items
                    } != set(batch):
                        raise ValueError
                    for item in items:
                        key = (item["space_id"], item["resource_id"])
                        if item["found"] is True:
                            policy = Policy.model_validate(item["policy"])
                            guard.epoch(policy.auth_epoch)
                            if (policy.space_id, policy.resource_id) != key:
                                raise ValueError
                        elif item["found"] is False:
                            policy = None
                        else:
                            raise ValueError
                        self.values[(epoch, *key)] = (timestamp + self.ttl, policy)
                except (KeyError, TypeError, ValueError, ValidationError):
                    raise unavailable(
                        "invalid_policy_response", "Current IAM policy snapshot unavailable"
                    ) from None
            # Read one coherent cache snapshot; cache eviction occurs only after this request's copy.
            current = {key: self.values[(epoch, *key)][1] for key in expected}
            if len(self.values) > self.max_entries:
                self.values.clear()
        allowed = []
        for record in records:
            policies = [current[(p["space_id"], p["resource_id"])] for p in record["policies"]]
            if any(p is None for p in policies):
                continue
            fingerprint = policy_fingerprint(policies)
            if fingerprint != policy_fingerprint(record["policies"]):
                continue
            if policies_visible(guard.principal, policies, clauses_for(policies)):
                allowed.append(fingerprint)
        await guard.finish()
        return sorted(set(allowed))
"""Current identity + complete policy fingerprint checks before any graph adjacency query."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from pydantic import ValidationError

from knowledge_platform.common.auth import policies_visible

from .projection import clauses_for, policy_fingerprint
from .schemas import Policy, unavailable


@dataclass
class AuthorizationGuard:
    authorizer: object
    token: str
    principal: object

    @classmethod
    async def begin(cls, authorizer, token, space_ids):
        principal = await authorizer.resolve(token)
        guard = cls(authorizer, token, principal)
        for space in space_ids:
            decision = await authorizer.require(token, "read", space)
            guard.epoch(decision.auth_epoch)
        return guard

    def epoch(self, epoch):
        if epoch != self.principal.auth_epoch:
            raise unavailable("authorization_changed", "Authorization changed during the operation")

    async def finish(self):
        current = await self.authorizer.resolve(self.token)
        self.epoch(current.auth_epoch)
        if (
            current.id != self.principal.id
            or set(current.subjects) != set(self.principal.subjects)
            or getattr(current, "channel_context", None)
            != getattr(self.principal, "channel_context", None)
        ):
            raise unavailable("authorization_changed", "Authorization changed during the operation")


class PolicyCache:
    def __init__(self, ttl=5, max_entries=200000):
        self.ttl, self.max_entries = ttl, max_entries
        self.values = {}
        self.lock = asyncio.Lock()

    async def allowed(self, guard, records):
        expected = {}
        for record in records:
            for value in record["policies"]:
                policy = Policy.model_validate(value)
                expected[(policy.space_id, policy.resource_id)] = None
        epoch = guard.principal.auth_epoch
        async with self.lock:
            timestamp = time.monotonic()
            self.values = {
                key: value
                for key, value in self.values.items()
                if key[0] == epoch and value[0] > timestamp
            }
            missing = [key for key in sorted(expected) if (epoch, *key) not in self.values]
            for offset in range(0, len(missing), 1000):
                batch = missing[offset : offset + 1000]
                response = await guard.authorizer.request(
                    "POST",
                    "/internal/v1/policies/batch",
                    "iam",
                    json={"resources": [{"space_id": s, "resource_id": r} for s, r in batch]},
                )
                try:
                    payload = response.json()
                    guard.epoch(payload["auth_epoch"])
                    items = payload["items"]
                    if len(items) != len(batch) or {
                        (p["space_id"], p["resource_id"]) for p in items
                    } != set(batch):
                        raise ValueError
                    for item in items:
                        key = (item["space_id"], item["resource_id"])
                        if item["found"] is True:
                            policy = Policy.model_validate(item["policy"])
                            guard.epoch(policy.auth_epoch)
                            if (policy.space_id, policy.resource_id) != key:
                                raise ValueError
                        elif item["found"] is False:
                            policy = None
                        else:
                            raise ValueError
                        self.values[(epoch, *key)] = (timestamp + self.ttl, policy)
                except (KeyError, TypeError, ValueError, ValidationError):
                    raise unavailable(
                        "invalid_policy_response", "Current IAM policy snapshot unavailable"
                    ) from None
            # Read one coherent cache snapshot; cache eviction occurs only after this request's copy.
            current = {key: self.values[(epoch, *key)][1] for key in expected}
            if len(self.values) > self.max_entries:
                self.values.clear()
        allowed = []
        for record in records:
            policies = [current[(p["space_id"], p["resource_id"])] for p in record["policies"]]
            if any(p is None for p in policies):
                continue
            fingerprint = policy_fingerprint(policies)
            if fingerprint != policy_fingerprint(record["policies"]):
                continue
            if policies_visible(guard.principal, policies, clauses_for(policies)):
                allowed.append(fingerprint)
        await guard.finish()
        return sorted(set(allowed))
