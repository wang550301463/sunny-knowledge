"""MCP owns no content store: strict tools and authoritative OAuth metadata."""

import importlib.util


def test_mcp_service_exists():
    assert importlib.util.find_spec("knowledge_platform.mcp") is not None, "MCP service is absent"
