"""HTTP resource metadata only; MCP JSON-RPC results remain SDK protocol objects."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue


class ProtectedResourceMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue]
    resource: str
    authorization_servers: list[str]
    scopes_supported: list[str]
    bearer_methods_supported: list[Literal["header"]]
    resource_name: str