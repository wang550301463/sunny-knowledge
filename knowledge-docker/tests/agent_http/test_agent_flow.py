"""Real deployed Agent/worker/LLM/canonical chain; model output is protocol simulation."""

import json
import sys
import time
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from oidc_login import http_client, login


class AgentHTTPRegression(unittest.TestCase):
    def test_tool_answer_resume_feedback_retry_and_live_revocation(self):
        config = json.loads((ROOT / ".local/test-env.json").read_text())
        tokens = login(config["public_url"], config["admin_username"], config["admin_password"])
        with http_client(config["public_url"], base_url=config["public_url"], timeout=30,
                         headers={"Authorization": "Bearer " + tokens["access_token"]}) as client:
            def call(method, path, body=None, want=200):
                response = client.request(method, "/api/v1" + path,
                                          **({"json": body} if body is not None else {}))
                self.assertEqual(response.status_code, want,
                                 f"{method} {path}: HTTP {response.status_code}")
                return response.json()

            me = call("GET", "/me")
            suffix = uuid4().hex
            space = call("POST", "/spaces", {"name": "agent-protocol-" + suffix}, 201)["id"]
            user, worker = "user:" + me["id"], "service:" + config["ingest_principal_id"]
            for action in ("read", "write"):
                call("PUT", "/grants", {"space_id": space, "action": action, "subjects": [user, worker]})
            proof = "agent-evidence-" + suffix
            raw = "# " + proof + "\r\n发布必须保留不可变修订与原始引用。\r\n"
            source = call("POST", "/sources", {
                "name": proof, "kind": "markdown", "space_id": space,
                "config": {"path": "docs/agent.md", "content": raw},
            }, 201)
            call("POST", "/sources/" + source["id"] + "/preview", {"base_version": 1})
            task = call("POST", "/sources/" + source["id"] + "/sync", {"base_version": 1}, 202)
            deadline = time.monotonic() + 120
            while task["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.3)
                task = call("GET", "/tasks/" + task["id"])
            self.assertEqual(task["status"], "review_needed")
            item = task["result"]["items"][0]
            revision = call("POST", "/reviews/" + item["proposal_id"] + "/approve",
                            {"reason": "Approve deterministic Agent protocol fixture source"})
            model = call("POST", "/models", {
                "name": "Agent protocol fixture " + suffix, "provider": "openai",
                "provider_model": "protocol-fixture-chat", "capability": "chat",
                "base_url": "http://agent-model-provider:8080/v1",
                "max_input_chars": 100000, "max_output_tokens": 2048,
                "timeout_seconds": 15, "max_retries": 0,
            }, 201)
            try:
                tested = call("POST", "/models/" + model["id"] + "/test", {
                    "configuration_id": model["configuration_id"], "test_tools": True, "test_stream": True,
                })
                self.assertEqual(tested["test_state"], "passed")
                self.assertTrue(tested["capabilities"]["tools"])
                self.assertTrue(tested["capabilities"]["stream"])
                agent = call("POST", "/agents", {
                    "name": "Protocol Agent " + suffix, "owner_space_id": space,
                    "config": {"mode": "knowledge_qa", "space_ids": [space],
                               "tools": ["get"], "model_configuration_id": model["configuration_id"]},
                }, 201)
                call("POST", "/agents/" + agent["id"] + "/publish",
                     {"base_configuration_id": agent["configuration_id"], "shared": True})
                session = call("POST", "/sessions", {"title": "Agent protocol session"}, 201)
                question = "FIXTURE_GET " + json.dumps({"space_id": space, "page_id": item["page_id"],
                                                       "revision_id": revision["id"]})
                body = {"agent_id": agent["id"], "session_id": session["id"], "question": question,
                        "space_ids": [space], "idempotency_key": "agent-http-" + suffix}
                run = call("POST", "/runs", body, 201)
                run_id = run["id"]
                self.assertEqual(call("POST", "/runs", body, 201)["id"], run_id)
                events = client.get("/api/v1/runs/" + run_id + "/events")
                self.assertEqual(events.status_code, 200)
                frames = [json.loads(line[6:]) for line in events.text.splitlines() if line.startswith("data: ")]
                self.assertTrue(frames)
                self.assertEqual([f["seq"] for f in frames], list(range(1, len(frames) + 1)))
                run = call("GET", "/runs/" + run_id)
                self.assertEqual(run["status"], "completed", run.get("error_code"))
                self.assertEqual(run["configuration_id"], agent["configuration_id"])
                self.assertIn(proof, json.dumps(run["answer"]))
                self.assertEqual(run["citations"][0]["revision_id"], revision["id"])
                self.assertEqual(run["citations"][0]["excerpt"], raw.removesuffix("\n"))
                self.assertGreater(run["usage"]["total_tokens"], 0)
                resumed = client.get("/api/v1/runs/" + run_id + "/events",
                                     headers={"Last-Event-ID": str(frames[-2]["seq"])})
                self.assertEqual(resumed.status_code, 200)
                self.assertEqual(sum(line.startswith("id: ") for line in resumed.text.splitlines()), 1)
                call("POST", "/feedback", {"run_id": run_id, "rating": "helpful",
                                           "comment": "Protocol validation only; no model quality label"}, 201)
                exported = client.get("/api/v1/runs/" + run_id + "/export")
                self.assertEqual(exported.status_code, 200)
                self.assertIn(proof, exported.text)
                call("POST", "/sessions/" + session["id"] + "/summaries", {}, 201)
                retried = call("POST", "/runs/" + run_id + "/retry", {"idempotency_key": "retry-" + suffix}, 201)
                self.assertNotEqual(retried["id"], run_id)
                cancelled = call("POST", "/runs/" + retried["id"] + "/cancel", {})
                self.assertEqual(cancelled["status"], "cancelled")
                call("PUT", "/grants", {"space_id": space, "resource_id": source["resource_id"],
                                         "action": "read", "subjects": [worker]})
                hidden = call("GET", "/runs/" + run_id)
                self.assertTrue(hidden["content_hidden"])
                for path in ("/runs/" + run_id + "/export", "/runs/" + run_id + "/events",
                             "/sessions/" + session["id"] + "/history",
                             "/sessions/" + session["id"] + "/summaries"):
                    response = client.get("/api/v1" + path)
                    self.assertNotIn(proof, response.text)
                    self.assertNotIn(question, response.text)
            finally:
                current = call("GET", "/models/" + model["id"])
                self.assertEqual(current["provider_model"], "protocol-fixture-chat")
                self.assertEqual(current["configuration_id"], model["configuration_id"])
                response = client.delete("/api/v1/models/" + model["id"],
                                         params={"base_configuration_id": current["configuration_id"]})
                self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
