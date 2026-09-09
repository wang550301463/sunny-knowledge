"""Actual service/ES/PG authorization flow with explicitly simulated model HTTP."""

import json
from pathlib import Path
import sys
import time
import unittest
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from oidc_login import http_client, login  # noqa: E402


class RetrievalHTTPRegression(unittest.TestCase):
    def test_publish_project_search_cite_and_revoke_before_rerank(self):
        config = json.loads((ROOT / ".local/test-env.json").read_text())
        model_config = json.loads((ROOT / ".local/retrieval-regression.json").read_text())
        self.assertEqual(model_config["model_kind"], "deterministic_protocol_simulation")
        tokens = login(config["public_url"], config["admin_username"], config["admin_password"])
        with http_client(config["public_url"], base_url=config["public_url"], timeout=30, headers={"Authorization": "Bearer " + tokens["access_token"]}) as client:
            def call(method, path, body=None, want=200):
                response = client.request(method, "/api/v1" + path, **({"json": body} if body is not None else {}))
                self.assertEqual(response.status_code, want, f"{method} {path}: unexpected HTTP {response.status_code}")
                return response.json()

            me = call("GET", "/me")
            space = call("POST", "/spaces", {"name": "retrieval-protocol-" + uuid4().hex}, 201)["id"]
            own_subject = "user:" + me["id"]
            ingest_subject = "service:" + config["ingest_principal_id"]
            projection_subject = "service:" + config["retrieval_principal_id"]
            call("PUT", "/grants", {"space_id": space, "action": "read", "subjects": [own_subject, ingest_subject, projection_subject]})
            call("PUT", "/grants", {"space_id": space, "action": "write", "subjects": [own_subject, ingest_subject]})
            unique = "projection-proof-" + uuid4().hex
            raw = "# " + unique + "\r\n所有发布必须记录版本与引用。\r\n"
            source = call("POST", "/sources", {"name": unique, "kind": "markdown", "space_id": space, "config": {"path": "docs/trace.md", "content": raw}}, 201)
            call("POST", "/sources/" + source["id"] + "/preview", {"base_version": 1})
            task = call("POST", "/sources/" + source["id"] + "/sync", {"base_version": 1}, 202)
            deadline = time.monotonic() + 120
            while task["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.5)
                task = call("GET", "/tasks/" + task["id"])
            self.assertEqual(task["status"], "review_needed", "Ingest did not produce the real narrative review proposal")
            proposal = task["result"]["items"][0]
            revision = call("POST", "/reviews/" + proposal["proposal_id"] + "/approve", {"reason": "Docker protocol regression approves the original fixture"})
            request = {"query": unique, "space_ids": [space], "limit": 12}
            deadline = time.monotonic() + 180
            result = None
            while time.monotonic() < deadline:
                response = client.post("/api/v1/search", json=request)
                if response.status_code == 200 and response.json()["items"]:
                    result = response.json()
                    break
                self.assertIn(response.status_code, (200, 503), "Projection poll failed outside documented retryable states")
                time.sleep(1)
            self.assertIsNotNone(result, "Canonical outbox did not project through the real worker/LLM HTTP/ES chain")
            self.assertTrue(any(item["revision_id"] == revision["id"] for item in result["items"]))
            self.assertTrue(any(item["text"] == raw for item in result["items"]))
            self.assertTrue(any(item["excerpt"] == raw for item in result["evidence"]))
            self.assertTrue(all(item["space_id"] == space for item in result["items"]))
            for item in result["items"]:
                self.assertNotIn("embedding", item)
                self.assertNotIn("policy_fingerprint", item)
                self.assertIn("revision=" + item["revision_id"], item["url"])
            snapshot_id = result["evidence"][0]["evidence"]["revision_id"]
            self.assertEqual(call("GET", "/source-snapshots/" + snapshot_id)["text"], raw)
            timeline = call("POST", "/timeline", {"space_ids": [space], "page_ids": [proposal["page_id"]], "limit": 10})
            self.assertIn(revision["id"], json.dumps(timeline))
            graph = call("POST", "/search", request | {"relation": {"types": ["depends_on"], "direction": "outgoing", "hops": 1}})
            self.assertIn("graph_unavailable", graph["degraded"])
            self.assertTrue(graph["items"])
            stats = httpx.get("http://model-provider:8080/stats", timeout=5).json()
            self.assertGreater(stats["embedding_requests"], 0)
            self.assertGreater(stats["rerank_requests"], 0)
            call("PUT", "/grants", {"space_id": space, "resource_id": source["resource_id"], "action": "read", "subjects": [projection_subject]})
            denied = call("POST", "/search", request)
            self.assertEqual(denied["items"], [])
            self.assertEqual(denied["evidence"], [])
            self.assertNotIn(unique, json.dumps(denied))
            # Revocation reaches the ES prefilter even while the permitted worker
            # can retain/index the document. Its text never reaches reranking again.
            self.assertEqual(httpx.get("http://model-provider:8080/stats", timeout=5).json()["rerank_requests"], stats["rerank_requests"])
            for path in ("/pages/" + proposal["page_id"], "/source-snapshots/" + snapshot_id):
                call("GET", path, want=403)


if __name__ == "__main__":
    unittest.main()
