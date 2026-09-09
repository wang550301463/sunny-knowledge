"""Deployed business telemetry gate, using only an isolated empty public fixture.

Run after the actual onboarding and Git projection fixture traffic. Missing
traffic is a failure, not a skip or an invented zero. No private corpus is read.
"""

import json
import math
import os
import sys
import time
import unittest
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from prometheus_client.parser import text_string_to_metric_families

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from oidc_login import http_client, login
from test_pipeline import service_spans


def samples(text):
    return [sample for family in text_string_to_metric_families(text) for sample in family.samples]


def value(records, name, **labels):
    found = [sample.value for sample in records if sample.name == name
             and all(sample.labels.get(key) == item for key, item in labels.items())]
    if len(found) != 1:
        raise AssertionError("Expected one fixed-label sample: " + name)
    return found[0]


class BusinessPipelineRegression(unittest.TestCase):
    def test_live_queues_match_canonical_database_and_consumers_remain_unknown(self):
        # This read-only acceptance probe uses Knowledge's own configured role.
        # Production collectors do not receive any other service's credentials.
        dsn = os.environ["TEST_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(dsn, autocommit=True) as database, httpx.Client(timeout=5) as client:
            def expected():
                with database.transaction():
                    database.execute("SET TRANSACTION READ ONLY")
                    return dict(database.execute("""
                        SELECT consumer, count(*) FROM
                          (VALUES ('retrieval'), ('graphiti')) AS consumers(consumer)
                          CROSS JOIN knowledge_outbox AS event
                        WHERE NOT EXISTS (
                          SELECT 1 FROM knowledge_deliveries AS delivery
                          WHERE delivery.event_id=event.id
                            AND delivery.consumer=consumers.consumer
                            AND delivery.acked_at IS NOT NULL
                        ) GROUP BY consumer
                    """).fetchall())

            deadline = time.monotonic() + 50
            while time.monotonic() < deadline:
                before = expected()
                result = client.get("http://knowledge:9090/metrics")
                self.assertEqual(result.status_code, 200)
                records = samples(result.text)
                after = expected()
                coherent = before == after and all(
                    value(records, "knowledge_queue_known", queue="outbox_" + consumer) == 1
                    and value(records, "knowledge_queue_size", queue="outbox_" + consumer) == before.get(consumer, 0)
                    for consumer in ("retrieval", "graphiti")
                )
                if coherent:
                    break
                time.sleep(1)
            else:
                self.fail("Canonical committed backlog did not reach its own collector")
            # The isolated corpus contains explicitly revoked fixtures. Their
            # pending deliveries must remain visible in the authoritative total.
            self.assertGreater(sum(before.values()), 0, "Missing retained-denial fixture traffic")
            for consumer in ("retrieval", "graphiti"):
                result = client.get(f"http://{consumer}-worker:9090/metrics")
                self.assertEqual(result.status_code, 200)
                records = samples(result.text)
                self.assertEqual(value(records, "knowledge_queue_known", queue="outbox_total"), 0)
                self.assertTrue(math.isnan(value(records, "knowledge_queue_size", queue="outbox_total")))
                for queue in ("reconcile_due", "failed_revisions"):
                    self.assertEqual(value(records, "knowledge_queue_known", queue=queue), 1)
            records = samples(client.get("http://ingest-worker:9090/metrics").text)
            for queue in ("tasks_queued", "tasks_running", "tasks_failed", "tasks_review_needed"):
                self.assertEqual(value(records, "knowledge_queue_known", queue=queue), 1)

    def test_real_search_business_trace_and_private_prometheus_targets(self):
        config = json.loads((Path(__file__).resolve().parents[2] / ".local/test-env.json").read_text())
        trace_id, marker = uuid4().hex, "public-telemetry-fixture-" + uuid4().hex
        token = login(config["public_url"], config["admin_username"], config["admin_password"])["access_token"]
        space = None
        with http_client(config["public_url"], timeout=20) as api, httpx.Client(timeout=5) as internal:
            api.headers["Authorization"] = "Bearer " + token
            def request(method, path, body=None, expected=200, headers=None):
                result = api.request(method, config["public_url"] + "/api/v1" + path,
                                     json=body, headers=headers)
                self.assertEqual(result.status_code, expected, method + " " + path + " failed")
                return result.json()
            try:
                actor = request("GET", "/me")
                space = request("POST", "/spaces", {"name": marker}, expected=201)
                request("PUT", "/grants", {"space_id": space["id"], "action": "read", "subjects": ["user:" + actor["id"]]})
                result = request("POST", "/search", {"query": marker, "space_ids": [space["id"]]}, headers={
                    "traceparent": f"00-{trace_id}-1234567890123456-01",
                    "baggage": "private=" + marker, "tracestate": "private=" + marker,
                })
                self.assertEqual(result["items"], [])
                self.assertIn("no_authorized_evidence", result["gaps"])
                deadline = time.monotonic() + 30
                trace = None
                while time.monotonic() < deadline:
                    response = internal.get("http://tempo:3200/api/traces/" + trace_id)
                    if response.status_code == 200:
                        current = response.json()
                        records = service_spans(current)
                        pairs = {(item["service"], item["name"]) for item in records}
                        if {("retrieval", "retrieval.search"), ("retrieval", "retrieval.es_hybrid"),
                            ("llm", "model.embedding"), ("llm", "model.usage_commit")} <= pairs:
                            trace = current
                            break
                    time.sleep(0.5)
                self.assertIsNotNone(trace, "Real business spans did not reach Tempo")
                serialized = json.dumps(trace)
                self.assertFalse(any(secret in serialized for secret in (token, marker, space["id"])),
                                 "Private input appeared in a business trace")
                expected_targets = {name + ":9090" for name in (
                    "gateway", "iam", "auth", "channel", "channel-worker", "knowledge",
                    "ingest", "retrieval", "llm", "graphiti", "agent", "mcp", "ingest-worker",
                    "retrieval-worker", "graphiti-worker",
                )}
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    response = internal.get("http://prometheus:9090/api/v1/query", params={"query": 'up{job="knowledge-services"}'})
                    self.assertEqual(response.status_code, 200)
                    found = response.json()["data"]["result"]
                    if {item["metric"]["instance"] for item in found if item["value"][1] == "1"} == expected_targets:
                        break
                    time.sleep(0.5)
                else:
                    self.fail("Prometheus did not observe all fifteen independent targets")
                for instance in expected_targets:
                    response = internal.get("http://" + instance + "/metrics")
                    self.assertEqual(response.status_code, 200)
                    self.assertFalse(any(secret in response.text for secret in (token, marker, space["id"])))
                    for sample in samples(response.text):
                        if sample.name.startswith("knowledge_domain_"):
                            self.assertLessEqual(set(sample.labels), {"service", "component", "operation", "outcome", "le"})
                required = [("llm", "model", "chat_stream", "success"),
                            ("llm", "model", "embedding", "success"),
                            ("retrieval", "retrieval", "search_local", "success"),
                            ("ingest", "ingest", "advance", "review_needed")]
                for service, component, operation, outcome in required:
                    response = internal.get("http://prometheus:9090/api/v1/query", params={"query":
                        'sum(knowledge_domain_operations_total{service="%s",component="%s",operation="%s",outcome="%s"})' % (service, component, operation, outcome)})
                    data = response.json()["data"]["result"]
                    self.assertTrue(data and float(data[0]["value"][1]) > 0,
                                    "Missing committed fixture operation: " + service + "/" + operation)
            finally:
                if space is not None:
                    request("PUT", "/grants", {"space_id": space["id"], "action": "read", "subjects": []})


if __name__ == "__main__":
    unittest.main()
