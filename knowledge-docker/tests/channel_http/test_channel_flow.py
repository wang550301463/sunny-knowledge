"""Deployed WeCom protocol simulator -> channel/auth/Agent/LLM/canonical regression.

No real Bot credentials or provider account; this cannot certify real WeCom/Qwen.
"""

import json
import re
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from oidc_login import http_client, login

SIMULATOR = "http://wecom-provider:8765"
MODEL = "http://agent-model-provider:8080"


class ChannelHTTPRegression(unittest.TestCase):
    def wait(self, read, predicate, seconds=30):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = read()
            if predicate(value):
                return value
            time.sleep(0.15)
        self.fail("Timed out waiting for the documented protocol state")

    def test_binding_real_stream_group_isolation_reconnect_and_revocation(self):
        local = json.loads((ROOT / ".local/test-env.json").read_text())
        token = login(local["public_url"], local["admin_username"], local["admin_password"])
        with http_client(
            local["public_url"], base_url=local["public_url"], timeout=30,
            headers={"Authorization": "Bearer " + token["access_token"]},
        ) as client, httpx.Client(timeout=10, trust_env=False) as fixture:
            def call(method, path, body=None, want=200):
                response = client.request(method, "/api/v1" + path, **({"json": body} if body is not None else {}))
                self.assertEqual(response.status_code, want, f"{method} {path}: HTTP {response.status_code}")
                return response.json()

            def sim(method, path="", body=None, want=200):
                response = fixture.request(method, SIMULATOR + "/fixtures/bots/" + bot + path, **({"json": body} if body is not None else {}))
                self.assertEqual(response.status_code, want, "WeCom fixture operation failed")
                return response.json()

            suffix = uuid4().hex
            bot, external = "fixture-" + suffix, "external-" + suffix
            me = call("GET", "/me")
            actor, worker = "user:" + me["id"], "service:" + local["ingest_principal_id"]
            sources, model, channel = [], None, None
            group_id = "group-" + suffix

            def source_fixture(label):
                space = call("POST", "/spaces", {"name": "channel-" + label + "-" + suffix}, 201)["id"]
                for action in ("read", "write"):
                    call("PUT", "/grants", {"space_id": space, "action": action, "subjects": [actor, worker]})
                proof = label + "-evidence-" + suffix
                raw = "# " + proof + "\r\n原始来源必须经过实时权限核验。\r\n"
                source = call("POST", "/sources", {"name": proof, "kind": "markdown", "space_id": space, "config": {"path": "docs/" + label + ".md", "content": raw}}, 201)
                call("POST", "/sources/" + source["id"] + "/preview", {"base_version": 1})
                task = call("POST", "/sources/" + source["id"] + "/sync", {"base_version": 1}, 202)
                task = self.wait(lambda: call("GET", "/tasks/" + task["id"]), lambda row: row["status"] not in {"queued", "running"}, 120)
                self.assertEqual(task["status"], "review_needed")
                item = task["result"]["items"][0]
                revision = call("POST", "/reviews/" + item["proposal_id"] + "/approve", {"reason": "WeCom protocol regression source approval"})
                record = {"space": space, "source": source, "revision": revision["id"], "page": item["page_id"], "raw": raw, "proof": proof}
                sources.append(record)
                return record

            def question(source):
                return "FIXTURE_GET " + json.dumps({"space_id": source["space"], "page_id": source["page"], "revision_id": source["revision"]})

            def send(text, *, group=False, identity=None):
                identity = identity or uuid4().hex
                body = {"message_id": "message-" + identity, "request_id": "request-" + identity, "user_id": external, "chat_type": "group" if group else "single", "chat_id": group_id if group else "", "text": text}
                sim("POST", "/messages", body, 202)
                return body

            def replies(message):
                return [r for r in sim("GET")["replies"] if r["request_id"] == message["request_id"]]

            def complete(message):
                return self.wait(lambda: replies(message), lambda rows: bool(rows) and rows[-1]["finished"], 45)

            def run_for(message):
                rows = self.wait(
                    lambda: call("GET", "/channels/" + channel["id"] + "/diagnostics")["items"],
                    lambda rows: any(r["message_id"] == message["message_id"] and r["run_id"] for r in rows),
                )
                return next(r["run_id"] for r in rows if r["message_id"] == message["message_id"])

            def hidden(run_id, proof):
                for tail in ("", "/events", "/export"):
                    response = client.get("/api/v1/runs/" + run_id + tail)
                    self.assertIn(response.status_code, {200, 403, 404})
                    self.assertNotIn(proof, response.text)
                    if response.status_code == 200 and not tail:
                        self.assertTrue(response.json()["content_hidden"])

            try:
                public, private = source_fixture("group-public"), source_fixture("private")
                spaces = [public["space"], private["space"]]
                model = call("POST", "/models", {"name": "WeCom protocol " + suffix, "provider": "openai", "provider_model": "protocol-fixture-chat", "capability": "chat", "base_url": MODEL + "/v1", "max_input_chars": 100000, "max_output_tokens": 2048, "timeout_seconds": 20, "max_retries": 0}, 201)
                tested = call("POST", "/models/" + model["id"] + "/test", {"configuration_id": model["configuration_id"], "test_tools": True, "test_stream": True})
                self.assertEqual(tested["test_state"], "passed")
                agent = call("POST", "/agents", {"name": "WeCom Agent " + suffix, "owner_space_id": public["space"], "config": {"mode": "knowledge_qa", "space_ids": spaces, "tools": ["get"], "model_configuration_id": model["configuration_id"]}}, 201)
                call("POST", "/agents/" + agent["id"] + "/publish", {"base_configuration_id": agent["configuration_id"], "shared": True})
                channel = call("POST", "/channels", {"name": "WeCom protocol " + suffix, "bot_id": bot, "bot_secret": "wecom-protocol-only", "agent_id": agent["id"], "space_ids": spaces, "enabled": False, "base_version": 0}, 201)
                self.assertNotIn("bot_secret", channel)
                self.assertTrue(channel["secret_configured"])
                call("POST", "/channels/" + channel["id"] + "/test", {"base_version": channel["version"]}, 202)
                channel = self.wait(lambda: call("GET", "/channels/" + channel["id"]), lambda c: c["status"] == "tested" and c["tested_version"] == c["version"])
                channel = call("PUT", "/channels/" + channel["id"], {"name": channel["name"], "bot_id": bot, "bot_secret": "", "agent_id": agent["id"], "space_ids": spaces, "enabled": True, "base_version": channel["version"]})
                self.wait(lambda: sim("GET"), lambda s: s["connected"])

                # Two-way proof: unauthenticated private message cannot start an Agent.
                unbound = send(question(private))
                reply = complete(unbound)[-1]["content"]
                match = re.search(r"\[绑定账号\]\(([^)]+)\)", reply)
                self.assertIsNotNone(match, "Unbound contact did not receive a private login challenge")
                fragment = parse_qs(urlsplit(match.group(1)).fragment)
                self.assertNotIn(private["proof"], reply)
                claim = call("POST", "/channel-bindings/claim", {"challenge_id": fragment["challenge"][0], "web_token": fragment["token"][0]})
                bound = send(claim["confirmation_command"])
                self.assertIn("账号绑定完成", complete(bound)[-1]["content"])
                self.assertTrue(any(b["channel_id"] == channel["id"] and b["active"] for b in call("GET", "/channel-bindings")["items"]))

                # The real provider is held after its first complete fact. A non-final
                # WeCom update must be visible before the provider can emit its ending.
                fixture.post(MODEL + "/fixtures/gates", json={"revision_id": private["revision"]}).raise_for_status()
                message = send(question(private))
                try:
                    early = self.wait(lambda: replies(message), lambda rows: any(private["proof"] in r["content"] and not r["finished"] for r in rows))
                    progress = fixture.get(MODEL + "/fixtures/gates/" + private["revision"]).json()
                    self.assertEqual(progress, {"started": True, "completed": False})
                    self.assertFalse(early[-1]["finished"])
                finally:
                    fixture.post(MODEL + "/fixtures/releases", json={"revision_id": private["revision"]}).raise_for_status()
                frames = complete(message)
                private_run = run_for(message)
                result = call("GET", "/runs/" + private_run)
                self.assertEqual(result["entrypoint"], "channel")
                self.assertTrue(result["answer_complete"])
                self.assertIn(private["proof"], frames[-1]["content"])
                self.assertIn(local["public_url"] + "/runs/" + private_run, frames[-1]["content"])
                citation = result["citations"][0]
                exact = call("GET", "/runs/" + private_run + "/citations/" + citation["id"])
                self.assertEqual(exact["text"], private["raw"])
                self.assertNotIn(result["session_id"], {s["id"] for s in call("GET", "/sessions")["items"]})

                # Durable dedup across an actual WebSocket reconnect.
                before = sim("GET")
                sim("POST", "/disconnect", {})
                self.wait(lambda: sim("GET"), lambda s: s["connected"] and s["connection_count"] > before["connection_count"])
                sim("POST", "/messages", message, 202)
                time.sleep(1)
                self.assertEqual(len(replies(message)), len(frames))
                self.assertEqual(run_for(message), private_run)
                # Historical reading survives replacement of the original worker lease.
                self.assertIn(private["proof"], json.dumps(call("GET", "/runs/" + private_run)["answer"]))

                group = call("PUT", "/channels/" + channel["id"] + "/groups/" + group_id, {"base_version": 0, "audience_id": "audience-" + suffix, "space_ids": [public["space"]], "enabled": True, "acknowledged_public_to_group": True})
                self.assertTrue(group["enabled"])
                self.assertEqual(group["sync_state"], "synced")
                group_message = send(question(public), group=True)
                group_frames = complete(group_message)
                group_run = run_for(group_message)
                group_result = call("GET", "/runs/" + group_run)
                self.assertEqual(group_result["actual_scope"], [public["space"]])
                self.assertNotEqual(group_result["session_id"], result["session_id"])
                self.assertIn(public["proof"], group_frames[-1]["content"])
                self.assertNotIn(private["proof"], json.dumps(group_frames))
                forbidden_message = send(question(private), group=True)
                forbidden_frames = complete(forbidden_message)
                self.assertNotIn(private["proof"], json.dumps(forbidden_frames))

                # A doc-level actor-only permission must hide the group result, even
                # though this same Web user still reads its original public source.
                group_citation = group_result["citations"][0]["id"]
                call("PUT", "/grants", {"space_id": public["space"], "resource_id": public["source"]["resource_id"], "action": "read", "subjects": [actor, worker]})
                hidden(group_run, public["proof"])
                response = client.get("/api/v1/runs/" + group_run + "/citations/" + group_citation)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn(public["proof"], response.text)
                self.assertIn(private["proof"], json.dumps(call("GET", "/runs/" + private_run)["answer"]))

                cleared = send("/清空")
                self.assertIn("当前会话已清空", complete(cleared)[-1]["content"])
                hidden(private_run, private["proof"])
                fresh_message = send(question(private))
                self.assertIn(private["proof"], complete(fresh_message)[-1]["content"])
                fresh_run = run_for(fresh_message)
                self.assertNotEqual(call("GET", "/runs/" + fresh_run)["session_id"], result["session_id"])
                call("DELETE", "/channels/" + channel["id"] + "/bindings/" + external)
                hidden(fresh_run, private["proof"])
                after_unbind = send(question(private))
                self.assertIn("绑定账号", complete(after_unbind)[-1]["content"])
                self.assertNotIn(private["proof"], json.dumps(replies(after_unbind)))
            finally:
                # Disable only this test's exact Bot/config; preserve unrelated channels.
                if channel:
                    current = call("GET", "/channels/" + channel["id"])
                    self.assertEqual(current["bot_id"], bot)
                    for group in call("GET", "/channels/" + channel["id"] + "/groups")["items"]:
                        if group["chat_id"] == group_id:
                            call("PUT", "/channels/" + channel["id"] + "/groups/" + group_id, {"base_version": group["version"], "audience_id": group["audience_id"], "space_ids": group["space_ids"], "enabled": False, "acknowledged_public_to_group": False})
                    if current["enabled"]:
                        call("PUT", "/channels/" + channel["id"], {"name": current["name"], "bot_id": bot, "bot_secret": "", "agent_id": current["agent_id"], "space_ids": current["space_ids"], "enabled": False, "base_version": current["version"]})
                if model:
                    current = call("GET", "/models/" + model["id"])
                    self.assertEqual(current["configuration_id"], model["configuration_id"])
                    self.assertEqual(current["base_url"], MODEL + "/v1")
                    self.assertEqual(current["provider_model"], "protocol-fixture-chat")
                    response = client.delete("/api/v1/models/" + model["id"], params={"base_configuration_id": model["configuration_id"]})
                    self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
