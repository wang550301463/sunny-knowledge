"""Real private Docker metrics/trace pipeline; never print login or token values."""

import json
from pathlib import Path
import sys
import time
import unittest
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from oidc_login import http_client, login  # noqa: E402


class ObservabilityRegression(unittest.TestCase):
    def test_actual_http_trace_reaches_tempo_and_prometheus_without_private_inputs(self):
        config = json.loads(
            (Path(__file__).resolve().parents[2] / ".local/test-env.json").read_text()
        )
        trace_id = uuid4().hex
        private_query = "private-query-" + uuid4().hex
        with http_client(config["public_url"], timeout=10) as browser, httpx.Client(timeout=5) as internal:
            token = login(browser, config)
            response = browser.get(
                config["public_url"] + "/api/v1/pages",
                params={"irrelevant": private_query},
                headers={
                    "Authorization": "Bearer " + token,
                    "traceparent": f"00-{trace_id}-1234567890123456-01",
                },
            )
            self.assertEqual(response.status_code, 200, "Authenticated knowledge request failed")
            deadline = time.monotonic() + 25
            spans = None
            while time.monotonic() < deadline:
                result = internal.get("http://tempo:3200/api/traces/" + trace_id)
                if result.status_code == 200:
                    spans = result.json()
                    break
                time.sleep(0.5)
            self.assertIsNotNone(spans, "Knowledge trace did not reach Tempo")
            serialized = json.dumps(spans)
            self.assertIn("GET /api/v1/pages", serialized)
            self.assertFalse(token in serialized, "Token appeared in trace data")
            self.assertFalse(private_query in serialized, "Query value appeared in trace data")
            metrics = internal.get("http://knowledge:9090/metrics")
            self.assertEqual(metrics.status_code, 200)
            self.assertIn('route="/api/v1/pages"', metrics.text)
            self.assertFalse(token in metrics.text or private_query in metrics.text)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                result = internal.get(
                    "http://prometheus:9090/api/v1/query",
                    params={"query": 'up{job="knowledge-services",instance="knowledge:9090"}'},
                )
                samples = result.json().get("data", {}).get("result", [])
                if samples and samples[0]["value"][1] == "1":
                    break
                time.sleep(0.5)
            else:
                self.fail("Prometheus is not scraping the live knowledge process")
            health = internal.get("http://grafana:3000/api/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["database"], "ok")
            self.assertEqual(internal.get("http://grafana:3000/api/datasources").status_code, 401)
            self.assertEqual(browser.get(config["public_url"] + "/metrics").status_code, 404)


if __name__ == "__main__":
    unittest.main()
