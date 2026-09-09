"""Export declaration-backed contracts without app lifespan, network, or credentials."""

from __future__ import annotations

import ast
import copy
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

PYTHON_SERVICES = ("knowledge", "ingest", "retrieval", "llm", "graphiti", "agent", "mcp")
METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def operation_id(service, method, path):
    path = re.sub(r"\{([^}]+)\}", r"by_\1", path)
    return re.sub(r"[^a-zA-Z0-9]+", "_", f"{service}_{method}_{path}").strip("_").lower()


@contextmanager
def isolated_environment():
    # Import-time configuration is not allowed to inherit deployment secrets or telemetry.
    keep = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "TMPDIR", "SYSTEMROOT"}}
    with patch.dict(os.environ, keep, clear=True):
        yield


def constant_schema(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean", "const": value}
    if isinstance(value, str):
        return {"type": "string", "const": value}
    if isinstance(value, int):
        return {"type": "integer", "const": value}
    if isinstance(value, dict):
        return {"type": "object", "properties": {k: constant_schema(v) for k, v in value.items()}, "required": sorted(value), "additionalProperties": False}
    raise ValueError("Not a constant JSON response")


def literal_response(endpoint):
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(endpoint)))
        returns = [node for node in ast.walk(tree) if isinstance(node, ast.Return)]
        if len(returns) != 1:
            return None
        return constant_schema(ast.literal_eval(returns[0].value))
    except (OSError, ValueError, TypeError, SyntaxError):
        return None


def has_bearer(dependant):
    call = getattr(dependant, "call", None)
    if getattr(call, "__name__", "") == "bearer_token":
        return True
    return any(has_bearer(child) for child in dependant.dependencies)


def decorate_operation(operation, service, method, path, *, bearer=False, public=False):
    operation["operationId"] = operation_id(service, method, path)
    operation["x-service"] = service
    operation["x-transport"] = "http-json"
    operation["x-response-typing"] = "untyped"
    if public:
        operation["security"] = []
    else:
        required = {"ServiceIdentity": []}
        if bearer:
            required["UserBearer"] = []
        operation["security"] = [required]
    return operation


def security_schemes():
    return {
        "ServiceIdentity": {"type": "apiKey", "in": "header", "name": "X-Service-Token", "description": "Fresh per-caller Ed25519 workload JWT, target audience. Supplied only by the existing trusted transport."},
        "UserBearer": {"type": "http", "scheme": "bearer", "description": "Forward the original user/delegated bearer unchanged; the existing auth boundary performs current authorization. Generated operations never retain it."},
    }


def export_python(root: Path):
    sys.path.insert(0, str(root / "services/python/src"))
    from fastapi.routing import APIRoute
    from knowledge_platform.common import observability

    result = {}
    with isolated_environment(), patch.object(observability, "install_telemetry", lambda *_: None):
        for name in PYTHON_SERVICES:
            module = importlib.import_module(f"knowledge_platform.{name}.app")
            # Do not enter lifespan: no credentials, database, brokers, or model calls.
            app = module.create_app()
            doc = copy.deepcopy(app.openapi())
            doc["info"]["version"] = "1.0.0"
            doc["x-runtime-service-version"] = app.version
            doc.setdefault("components", {})["securitySchemes"] = security_schemes()
            doc["x-contract-source"] = "runtime FastAPI routes and native Pydantic schemas"
            doc["x-mounted-protocols"] = []
            for route in app.routes:
                if not isinstance(route, APIRoute):
                    if route.path == "/mcp":
                        doc["x-mounted-protocols"].append({"path": route.path, "methods": sorted(route.methods), "protocol": "mcp-streamable-http", "schema_source": "MCP SDK and knowledge_platform.mcp.schemas; JSON-RPC methods are not REST operations"})
                    continue
                for method in sorted(route.methods):
                    key = method.lower()
                    if key not in doc.get("paths", {}).get(route.path, {}):
                        continue
                    operation = doc["paths"][route.path][key]
                    decorate_operation(operation, name, key, route.path, bearer=has_bearer(route.dependant), public=route.path in {"/healthz", "/readyz"} or route.path.startswith("/.well-known/"))
                    operation["x-handler"] = route.endpoint.__qualname__
                    operation["x-source"] = f"services/python/src/knowledge_platform/{name}/app.py"
                    success = str(route.status_code or 200)
                    response = operation["responses"].setdefault(success, {"description": "Successful response"})
                    if name == "agent" and route.path.endswith("/events"):
                        response["content"] = {"text/event-stream": {"schema": {"type": "string"}}}
                        operation["x-transport"] = "sse"
                        operation["x-response-typing"] = "stream"
                    elif name == "agent" and route.path.endswith("/export"):
                        response["content"] = {"text/markdown": {"schema": {"type": "string"}}}
                        operation["x-transport"] = "text"
                        operation["x-response-typing"] = "text"
                    else:
                        content = response.setdefault("content", {}).setdefault("application/json", {})
                        schema = content.get("schema") or literal_response(route.endpoint)
                        if schema:
                            content["schema"] = schema
                            operation["x-response-typing"] = "typed"
                        else:
                            content["schema"] = {}
                            operation["x-untyped-reason"] = "Handler has no native response schema; no fabricated model or Any-returning client is generated."
            result[name] = doc
    return result


def export_go(root: Path):
    environment = os.environ.copy()
    environment["GOTOOLCHAIN"] = "local"
    data = subprocess.check_output(["go", "run", "./cmd/contract-export", "-root", str(root)], cwd=root / "services/go", env=environment)
    return json.loads(data)


if __name__ == "__main__":
    repository = Path(__file__).resolve().parents[2]
    print(json.dumps({**export_python(repository), **export_go(repository)}, ensure_ascii=False, sort_keys=True, indent=2))
