#!/usr/bin/env python3
"""Prepare explicitly simulated model configs and a retrieval-owned test schema."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql

from configure_worker import require
from oidc_login import http_client, login

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retire-models", action="store_true")
    args = parser.parse_args()
    config = json.loads((ROOT / ".local/test-env.json").read_text())
    record_path = ROOT / ".local/retrieval-regression.json"
    tokens = login(config["public_url"], config["admin_username"], config["admin_password"])
    with http_client(config["public_url"], base_url=config["public_url"], timeout=30, headers={"Authorization": "Bearer " + tokens["access_token"]}) as client:
        if args.retire_models:
            record = json.loads(record_path.read_text())
            for model in record["models"].values():
                current = require(client.get("/api/v1/models/" + model["id"]))
                if current["state"] != "retired":
                    require(client.delete("/api/v1/models/" + model["id"], params={"base_configuration_id": current["configuration_id"]}))
            print("Protocol-fixture model configurations retired; no credentials printed.")
            return
        # Each run preserves isolated catalog/index evidence for diagnosis; no application
        # schema, ES index or pre-existing model configuration is replaced.
        suffix = uuid4().hex
        schema = "test_retrieval_protocol_" + suffix
        record = {"schema": schema, "es_index": "knowledge-retrieval-protocol-" + suffix, "models": {}, "model_kind": "deterministic_protocol_simulation"}

        def save():
            record_path.write_text(json.dumps(record, indent=2) + "\n")
            record_path.chmod(0o600)

        save()
        with psycopg.connect(config["databases"]["retrieval"]) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        for capability in ("embedding", "rerank"):
            body = {
                "name": "Protocol regression " + capability + " " + suffix,
                "provider": "openai" if capability == "embedding" else "dashscope_rerank_compatible",
                "provider_model": "protocol-fixture-" + capability,
                "base_url": "http://model-provider:8080/v1",
                "capability": capability,
                "max_input_chars": 8192 if capability == "embedding" else 65536,
                "max_batch_size": 10 if capability == "embedding" else 64,
                "timeout_seconds": 5,
                "max_retries": 0,
            }
            if capability == "embedding":
                body.update(dimensions=8, request_dimensions=True)
            model = require(client.post("/api/v1/models", json=body), (201,))
            record["models"][capability] = {"id": model["id"], "configuration_id": model["configuration_id"]}
            save()
            result = require(client.post("/api/v1/models/" + model["id"] + "/test", json={"configuration_id": model["configuration_id"]}))
            if result["test_state"] != "passed":
                raise RuntimeError("Protocol fixture capability test failed: " + str(result.get("error_code")))
        values = dict(line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines() if line and not line.startswith("#"))
        database = "postgresql+psycopg://retrieval:" + values["DB_PASSWORD_RETRIEVAL"] + "@postgres:5432/knowledge_retrieval?" + urlencode({"options": "-csearch_path=" + schema})
        env = {
            "RETRIEVAL_REGRESSION_DATABASE_URL": database,
            "REGRESSION_ES_INDEX": record["es_index"],
            "REGRESSION_EMBEDDING_CONFIGURATION_ID": record["models"]["embedding"]["configuration_id"],
            "REGRESSION_RERANK_CONFIGURATION_ID": record["models"]["rerank"]["configuration_id"],
        }
        env_path = ROOT / ".local/retrieval-regression.env"
        env_path.write_text("\n".join(f"{key}={value}" for key, value in env.items()) + "\n")
        env_path.chmod(0o600)
    print("Isolated protocol regression configured. No real models called; credentials were not printed.")


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, psycopg.Error, KeyError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error) if isinstance(error, RuntimeError) else "Protocol regression setup failed; inspect private local configuration and service readiness") from None
