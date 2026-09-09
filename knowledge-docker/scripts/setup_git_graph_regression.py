#!/usr/bin/env python3
"""Provision isolated catalog schemas and explicitly simulated model configurations."""
import json
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from configure_worker import require
from oidc_login import http_client, login

ROOT = Path(__file__).resolve().parents[1]


def private_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def environment(values, record):
    result = {
        "GRAPH_REGRESSION_NAMESPACE": record["graph_namespace"],
        "GIT_GRAPH_ES_INDEX": record["es_index"],
        "GIT_GRAPH_EMBEDDING_CONFIGURATION_ID": record["models"]["embedding"]["configuration_id"],
        "GIT_GRAPH_RERANK_CONFIGURATION_ID": record["models"]["rerank"]["configuration_id"],
    }
    for service, variable in (("retrieval", "GIT_GRAPH_RETRIEVAL_DATABASE_URL"), ("graphiti", "GRAPH_REGRESSION_DATABASE_URL")):
        result[variable] = (
            "postgresql+psycopg://" + service + ":" + quote(values["DB_PASSWORD_" + service.upper()], safe="")
            + "@postgres:5432/knowledge_" + service + "?"
            + urlencode({"options": "-csearch_path=" + record["schemas"][service]})
        )
    return result


def main():
    config = json.loads((ROOT / ".local/test-env.json").read_text())
    # Requires prior explicit worker provisioning. Never register or grant a worker here.
    for service in ("ingest", "retrieval", "graphiti"):
        if not config.get(service + "_principal_id"):
            raise RuntimeError("Run configure_worker.py for ingest, retrieval and graphiti first")
    values = dict(line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines() if line and not line.startswith("#"))
    suffix = uuid4().hex
    record = {
        "schemas": {service: "test_git_graph_" + suffix for service in ("retrieval", "graphiti")},
        "es_index": "knowledge-git-graph-" + suffix,
        "graph_namespace": "git-graph-regression-" + suffix,
        "models": {},
        "model_kind": "deterministic_protocol_simulation",
        "fixture_kind": "fixed_three_language_git_fixture",
        "state": "preparing",
    }
    target = ROOT / ".local/git-graph-regression.json"
    private_json(target, record)
    for service, schema in record["schemas"].items():
        with psycopg.connect(config["databases"][service]) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    tokens = login(config["public_url"], config["admin_username"], config["admin_password"])
    with http_client(config["public_url"], base_url=config["public_url"], timeout=30, headers={"Authorization": "Bearer " + tokens["access_token"]}) as client:
        for capability in ("embedding", "rerank"):
            body = {
                "name": "Git graph protocol " + capability + " " + suffix,
                "provider": "openai" if capability == "embedding" else "dashscope_rerank_compatible",
                "provider_model": "protocol-fixture-" + capability,
                "base_url": "http://git-model-provider:8080/v1",
                "capability": capability,
                "max_input_chars": 8192 if capability == "embedding" else 65536,
                "max_batch_size": 10 if capability == "embedding" else 64,
                "timeout_seconds": 5, "max_retries": 0,
            }
            if capability == "embedding":
                body.update(dimensions=8, request_dimensions=True)
            model = require(client.post("/api/v1/models", json=body), (201,))
            record["models"][capability] = {"id": model["id"], "configuration_id": model["configuration_id"]}
            private_json(target, record)
            tested = require(client.post("/api/v1/models/" + model["id"] + "/test", json={"configuration_id": model["configuration_id"]}))
            if tested["test_state"] != "passed":
                raise RuntimeError("Git graph protocol model capability test failed")
    result = environment(values, record)
    path = ROOT / ".local/git-graph-regression.env"
    path.write_text("\n".join(key + "=" + value for key, value in result.items()) + "\n")
    path.chmod(0o600)
    record["state"] = "ready"
    private_json(target, record)
    print("Isolated Git/graph regression configured; models are protocol simulations; no credentials printed.")


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, psycopg.Error, KeyError, ValueError, OSError, RuntimeError) as error:
        raise SystemExit(str(error) if isinstance(error, RuntimeError) else "Git graph setup failed; check private configuration and service readiness") from None