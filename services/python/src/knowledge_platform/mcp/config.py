from urllib.parse import urlsplit

from pydantic import Field, model_validator

from knowledge_platform.common.config import Settings


class MCPSettings(Settings):
    service_name: str = "mcp"
    mcp_resource_url: str = "http://localhost:18180/mcp"
    mcp_issuer_url: str = "http://localhost:18180/idp/realms/knowledge"
    mcp_api_audience: str = "knowledge-api"
    mcp_allowed_client_ids: list[str] = Field(default_factory=lambda: ["knowledge-mcp"])
    mcp_allowed_origins: list[str] = Field(default_factory=list)
    mcp_internal_hosts: list[str] = Field(default_factory=lambda: ["mcp:8080"])
    mcp_max_sessions: int = Field(default=1000, ge=1, le=10000)
    mcp_session_idle_seconds: float = Field(default=900, ge=10, le=3600)
    mcp_max_response_bytes: int = Field(default=2_097_152, ge=4096, le=8_388_608)
    mcp_call_timeout_seconds: float = Field(default=190, ge=1, le=195)

    @model_validator(mode="after")
    def configured_boundaries(self):
        for value in (self.mcp_resource_url, self.mcp_issuer_url, *self.mcp_allowed_origins):
            url = urlsplit(value)
            if (not url.hostname or url.username or url.password or url.query or url.fragment
                or url.scheme not in {"https", "http"}
                or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})):
                raise ValueError("Public MCP URLs require HTTPS except loopback development")
        if urlsplit(self.mcp_resource_url).path != "/mcp":
            raise ValueError("MCP resource must be the exact /mcp endpoint")
        if not self.mcp_allowed_client_ids or any(not x or len(x) > 256 for x in self.mcp_allowed_client_ids):
            raise ValueError("Explicit preregistered clients required")
        if any("*" in x or "/" in x for x in self.mcp_internal_hosts):
            raise ValueError("Internal Host allowlist must contain exact hosts")
        return self

    @property
    def metadata_url(self):
        url = urlsplit(self.mcp_resource_url)
        return f"{url.scheme}://{url.netloc}/.well-known/oauth-protected-resource/mcp"

    @property
    def origins(self):
        url = urlsplit(self.mcp_resource_url)
        return [f"{url.scheme}://{url.netloc}", *self.mcp_allowed_origins]
