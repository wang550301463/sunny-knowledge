"""Native TCP OAuth discovery remains distinct from mounted MCP JSON-RPC."""

from knowledge_platform.mcp.responses import ProtectedResourceMetadata


async def test_both_native_metadata_routes_keep_exact_body_and_no_store(runtime):
    client, domains, _, resource = runtime
    expected = {
        "resource": resource,
        "authorization_servers": ["http://localhost:18180/idp/realms/knowledge"],
        "scopes_supported": ["knowledge:read"],
        "bearer_methods_supported": ["header"],
        "resource_name": "sunny-knowledge",
    }
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        response = await client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == expected
        assert ProtectedResourceMetadata.model_validate(response.json()).model_dump(exclude_unset=True) == expected
    assert not domains.calls