"""Operator-gated, exact fixture-edge Neo4j fault; restore the original relationship in finally.

This tool never stops a service, changes runtime configuration, or broad-deletes a
namespace. The operator must stop the fixture graph worker after browser seeding
and acknowledge the same request ID through operator-ready.json.
"""

import json
import os
import re
import time
from pathlib import Path

from neo4j import GraphDatabase

ROOT = Path("/run/web-graph-fixture")
CONFIG = Path("/run/knowledge/git-graph-regression.json")


def read(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write(name, value):
    pending = ROOT / (name + ".tmp")
    pending.write_text(json.dumps(value) + "\n")
    pending.replace(ROOT / name)


def valid_request(value, namespace, now):
    if not isinstance(value, dict) or value.get("namespace") != namespace:
        return False
    if not re.fullmatch(r"git-graph-regression-[a-f0-9]{32}", namespace):
        return False
    if not re.fullmatch(r"[a-f0-9]{32}", str(value.get("fixture_id", ""))):
        return False
    created = value.get("created_at")
    if type(created) not in (int, float) or not 0 <= now - created <= 180:
        return False
    return all(isinstance(value.get(key), str) and 0 < len(value[key]) <= 512 for key in ("space_id", "page_id", "revision_id", "edge_id"))


def wait_request(namespace):
    deadline = time.monotonic() + 780
    while time.monotonic() < deadline:
        value = read(ROOT / "request.json")
        if valid_request(value, namespace, time.time()):
            return value
        time.sleep(0.25)
    raise RuntimeError("browser_request_timeout")


def wait_id(name, fixture_id, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if (read(ROOT / name) or {}).get("fixture_id") == fixture_id:
            return
        time.sleep(0.25)
    raise RuntimeError("operator_or_browser_gate_timeout")


def remove(tx, request):
    rows = list(tx.run(
        """MATCH (source:Entity)-[edge:RELATES_TO]->(target:Entity)
        WHERE edge.knowledge_namespace=$namespace
          AND source.knowledge_namespace=$namespace AND target.knowledge_namespace=$namespace
          AND edge.knowledge_page_id=$page_id AND edge.knowledge_revision_id=$revision_id
          AND edge.knowledge_edge_id=$edge_id
        RETURN source.uuid AS source, target.uuid AS target, properties(edge) AS properties""",
        **{key: request[key] for key in ("namespace", "page_id", "revision_id", "edge_id")},
    ))
    if not 1 <= len(rows) <= 16:
        raise RuntimeError("exact_fixture_edge_cardinality_mismatch")
    captured = [dict(row) for row in rows]
    if any(not row["source"] or not row["target"] or not row["properties"].get("uuid") for row in captured):
        raise RuntimeError("exact_fixture_edge_identity_missing")
    # Delete only the physical UUIDs captured under all four exact constraints.
    result = tx.run(
        """MATCH ()-[edge:RELATES_TO]->()
        WHERE edge.knowledge_namespace=$namespace AND edge.knowledge_page_id=$page_id
          AND edge.knowledge_revision_id=$revision_id AND edge.knowledge_edge_id=$edge_id
          AND edge.uuid IN $ids
        DELETE edge RETURN count(edge) AS removed""",
        **{key: request[key] for key in ("namespace", "page_id", "revision_id", "edge_id")},
        ids=[row["properties"]["uuid"] for row in captured],
    ).single()
    if result["removed"] != len(captured):
        raise RuntimeError("exact_fixture_edge_delete_conflict")
    return captured


def restore(tx, request, rows):
    for row in rows:
        result = tx.run(
            """MATCH (source:Entity {uuid:$source}), (target:Entity {uuid:$target})
            WHERE source.knowledge_namespace=$namespace AND target.knowledge_namespace=$namespace
              AND source.knowledge_page_id=$page_id AND target.knowledge_page_id=$page_id
              AND source.knowledge_revision_id=$revision_id AND target.knowledge_revision_id=$revision_id
            MERGE (source)-[edge:RELATES_TO {uuid:$uuid}]->(target)
            SET edge = $properties
            RETURN count(edge) AS restored""",
            **{key: request[key] for key in ("namespace", "page_id", "revision_id")},
            source=row["source"], target=row["target"], uuid=row["properties"]["uuid"], properties=row["properties"],
        ).single()
        if result["restored"] != 1:
            raise RuntimeError("exact_fixture_edge_restore_failed")


def main():
    settings = json.loads(CONFIG.read_text())
    if settings.get("state") != "ready" or settings.get("model_kind") != "deterministic_protocol_simulation":
        raise RuntimeError("isolated_projection_runtime_not_ready")
    namespace = settings["graph_namespace"]
    request = wait_request(namespace)
    identity = {"fixture_id": request["fixture_id"]}
    write("state.json", {**identity, "state": "awaiting_operator_worker_pause"})
    # An explicit same-run operator acknowledgement is required before mutation.
    wait_id("operator-ready.json", request["fixture_id"], 120)
    rows = []
    with GraphDatabase.driver(
        os.environ["TEST_GRAPHITI_NEO4J_URI"],
        auth=("neo4j", os.environ["TEST_GRAPHITI_NEO4J_PASSWORD"]),
        connection_timeout=10, connection_acquisition_timeout=10,
    ) as driver, driver.session(database="neo4j") as session:
        try:
            rows = session.execute_write(remove, request)
            write("state.json", {**identity, "state": "fault_applied", "physical_relationships": len(rows)})
            wait_id("release.json", request["fixture_id"], 90)
        finally:
            if rows:
                session.execute_write(restore, request, rows)
                write("state.json", {**identity, "state": "restored", "physical_relationships": len(rows)})
    print("Exact isolated Neo4j relationship removed and restored; no credentials or source payload logged.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Driver exceptions may embed connection information. Preserve a generic
        # failure and require the operator to reconcile the named fixture scope.
        raise SystemExit("Graph fault controller failed; inspect the isolated gate state and reconcile its exact test projection before restarting the worker.") from None