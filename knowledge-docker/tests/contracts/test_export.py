"""Contract export reads application declarations without starting dependencies."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def exporter():
    path = ROOT / "knowledge-docker/scripts/contract_export.py"
    spec = importlib.util.spec_from_file_location("contract_export", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_python_export_uses_real_route_parameters_and_strict_request_models():
    value = exporter().export_python(ROOT)
    operation = value["knowledge"]["paths"]["/api/v1/pages/{page_id}/proposals"]["post"]
    assert operation["operationId"] == "knowledge_post_api_v1_pages_by_page_id_proposals"
    assert operation["requestBody"]["required"] is True
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert any(p["name"] == "page_id" and p["in"] == "path" for p in operation["parameters"])
    schemas = value["knowledge"]["components"]["schemas"]
    proposal = schemas["ProposalCreate"]
    assert proposal["additionalProperties"] is False
    assert proposal["properties"]["input_revisions"]["maxItems"] == 200
    assert "base_revision" in proposal["properties"]


def test_python_inventory_includes_internal_routes_and_distinguishes_stream_protocol():
    value = exporter().export_python(ROOT)
    assert "/internal/v1/channel/runs" in value["agent"]["paths"]
    stream = value["agent"]["paths"]["/api/v1/runs/{run_id}/events"]["get"]
    assert stream["x-transport"] == "sse"
    assert "text/event-stream" in stream["responses"]["200"]["content"]
    assert value["mcp"]["x-mounted-protocols"][0]["protocol"] == "mcp-streamable-http"
    assert "tools/call" not in value["mcp"]["paths"]


def test_missing_response_types_are_explicit_and_not_false_any_models():
    value = exporter().export_python(ROOT)
    for service in value.values():
        for path in service["paths"].values():
            for method, operation in path.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                assert operation["x-response-typing"] in {"typed", "untyped", "stream", "text"}
                for response in operation["responses"].values():
                    schema = response.get("content", {}).get("application/json", {}).get("schema")
                    if schema == {}:
                        assert operation["x-response-typing"] == "untyped"


def test_export_is_deterministic_and_does_not_need_credentials(monkeypatch):
    for key in ("DATABASE_URL", "SERVICE_PRIVATE_KEY_FILE", "SERVICE_PUBLIC_KEYS_FILE"):
        monkeypatch.setenv(key, "/does/not/exist-contract-test")
    export = exporter()
    assert export.export_python(ROOT) == export.export_python(ROOT)


def test_go_export_includes_dynamic_routes_and_excludes_secret_cipher():
    value = exporter().export_go(ROOT)
    assert "/api/v1/groups/{id}/members" in value["iam"]["paths"]
    assert "/api/v1/departments/{id}/members" in value["iam"]["paths"]
    config = value["channel"]["components"]["schemas"]["channel_Config"]
    assert "agent_configuration_id" in config["properties"]
    assert "secret_cipher" not in config["properties"]
    assert "SecretCipher" not in config["properties"]
    assert "secret_configured" in config["properties"]
    constraint = value["auth"]["components"]["schemas"]["platform_ChannelConstraint"]
    assert "read_run_id" in constraint["properties"]
    assert "read_run_id" not in constraint["required"]
