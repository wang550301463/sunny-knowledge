"""Real gateway, identity, ingest worker, S3, Temporal and canonical Wiki flow."""

import importlib.util
import json
import time
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("oidc_login", ROOT / "scripts/oidc_login.py")
oidc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oidc)


class IngestHTTPRegression(unittest.TestCase):
    def test_markdown_snapshot_worker_review_exact_citation_and_live_revocation(self):
        config = json.loads((ROOT / ".local/test-env.json").read_text())
        token = oidc.login(config["public_url"], config["admin_username"], config["admin_password"])
        with oidc.http_client(config["public_url"], base_url=config["public_url"], timeout=30,
                             headers={"Authorization": "Bearer " + token["access_token"]}) as client:
            def call(method, path, body=None, want=200):
                response = client.request(method, "/api/v1" + path, **({"json": body} if body is not None else {}))
                self.assertEqual(response.status_code, want, f"{method} {path} status mismatch: {response.status_code}")
                return response.json()

            me = call("GET", "/me")
            space = call("POST", "/spaces", {"name": "ingest-http-" + uuid4().hex}, 201)["id"]
            worker_subject = "service:" + config["ingest_principal_id"]
            for action in ("read", "write"):
                call("PUT", "/grants", {"space_id": space, "action": action,
                     "subjects": [me["subjects"][0], worker_subject]})
            raw = "# 发布流程\r\n先检查依赖证据，再提交审核。\r\n"
            created = call("POST", "/sources", {"name": "release.md", "kind": "markdown", "space_id": space,
                           "config": {"path": "docs/release.md", "content": raw}}, 201)
            self.assertNotIn("content", created["config"])
            source_path = "/sources/" + created["id"]
            preview = call("POST", source_path + "/preview", {"base_version": 1})
            self.assertEqual(preview["file_count"], 1)
            again = call("POST", source_path + "/preview", {"base_version": 1})
            self.assertEqual(again["source_revision"], preview["source_revision"])
            task = call("POST", source_path + "/sync", {"base_version": 1}, 202)
            duplicate = call("POST", source_path + "/sync", {"base_version": 1}, 202)
            self.assertEqual(task["id"], duplicate["id"])
            deadline = time.monotonic() + 100
            while task["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.5)
                task = call("GET", "/tasks/" + task["id"])
            self.assertEqual(task["status"], "review_needed", f"Worker outcome {task['status']} / {task.get('error_code')}")
            result = task["result"]["items"][0]
            proposal = call("GET", "/reviews/" + result["proposal_id"])
            self.assertEqual(proposal["status"], "pending")
            revision = call("POST", "/reviews/" + result["proposal_id"] + "/approve", {"reason": "真实容器审核原文"})
            wiki = call("GET", "/pages/" + result["page_id"])
            self.assertEqual(wiki["current_revision"], revision["id"])
            evidence = revision["content"]["evidence"][0]
            snapshot = call("GET", "/source-snapshots/" + evidence["revision_id"])
            self.assertEqual(snapshot["text"], raw)
            self.assertEqual(evidence["path"], "docs/release.md")
            self.assertNotIn("object_key", snapshot)
            call("PUT", "/grants", {"space_id": space, "resource_id": created["resource_id"],
                                    "action": "read", "subjects": []})
            # Management remains possible but never bypasses revoked content/provenance access.
            for path in [source_path, "/pages/" + result["page_id"], "/source-snapshots/" + evidence["revision_id"],
                         "/reviews/" + result["proposal_id"], "/tasks/" + task["id"]]:
                call("GET", path, want=403)


if __name__ == "__main__":
    unittest.main()
