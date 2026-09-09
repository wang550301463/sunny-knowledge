"""Official MCP client through deployed gateway and canonical source permissions."""

import asyncio
import json
import os
import sys
import time
import unittest
from pathlib import Path
from uuid import uuid4

import httpx
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from oidc_login import http_client, login


class MCPKnowledgeRegression(unittest.TestCase):
    def test_official_client_reads_pinned_knowledge_then_source_revocation_blocks_reuse(self):
        config = json.loads((ROOT / ".local/test-env.json").read_text())
        base = config["public_url"]
        admin = login(base, config["admin_username"], config["admin_password"])
        mcp_token = login(base, config["admin_username"], config["admin_password"],
                          client_id="knowledge-mcp", redirect_uri="http://127.0.0.1:18791/callback",
                          scope="openid knowledge:read", resource=base + "/mcp")
        with http_client(base, base_url=base, timeout=30, headers={"Authorization": "Bearer " + admin["access_token"]}) as client:
            def call(method, path, body=None, want=200):
                response = client.request(method, "/api/v1" + path, **({"json": body} if body is not None else {}))
                self.assertEqual(response.status_code, want, f"Unexpected HTTP {response.status_code} on {method} {path}")
                return response.json()

            actor = call("GET", "/me")
            space = call("POST", "/spaces", {"name": "mcp-proof-" + uuid4().hex}, 201)["id"]
            subjects = ["user:" + actor["id"], "service:" + config["ingest_principal_id"]]
            for action in ("read", "write"):
                call("PUT", "/grants", {"space_id": space, "action": action, "subjects": subjects})
            raw = "# MCP original evidence\r\n" + uuid4().hex + " 必须使用已发布版本。\r\n"
            source = call("POST", "/sources", {"space_id": space, "name": "MCP source proof", "kind": "markdown", "config": {"path": "docs/mcp.md", "content": raw}}, 201)
            source_path = "/sources/" + source["id"]
            call("POST", source_path + "/preview", {"base_version": 1})
            task = call("POST", source_path + "/sync", {"base_version": 1}, 202)
            deadline = time.monotonic() + 120
            while task["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.25)
                task = call("GET", "/tasks/" + task["id"])
            self.assertEqual(task["status"], "review_needed")
            proposal = task["result"]["items"][0]
            revision = call("POST", "/reviews/" + proposal["proposal_id"] + "/approve", {"reason": "Authorize original MCP regression evidence"})
            evidence = revision["content"]["evidence"][0]

            async def verify():
                # Host stays the public host while TCP uses only the isolated Compose gateway.
                endpoint = os.environ.get("REGRESSION_GATEWAY_URL", base).rstrip("/") + "/mcp"
                async with (
                    httpx2.AsyncClient(headers={"Authorization": "Bearer " + mcp_token["access_token"], "Host": httpx.URL(base).netloc.decode()}) as http,
                    streamable_http_client(endpoint, http_client=http) as streams,
                    ClientSession(*streams) as session,
                ):
                    await session.initialize()
                    listed = await session.list_tools()
                    self.assertEqual({tool.name for tool in listed.tools}, {"search", "get", "traverse", "timeline", "ask", "feedback"})
                    page = await session.call_tool("get", {"page_id": proposal["page_id"], "revision_id": revision["id"]})
                    self.assertFalse(page.is_error, "Canonical exact revision must be readable via the official MCP client")
                    self.assertEqual(page.structured_content["id"], revision["id"])
                    original = await session.call_tool("get", {"operation": "evidence", "evidence": evidence})
                    self.assertFalse(original.is_error)
                    self.assertEqual(original.structured_content["excerpt"], raw.removesuffix("\n"))
                    call("PUT", "/grants", {"space_id": space, "resource_id": source["resource_id"], "action": "read", "subjects": []})
                    for arguments in ({"page_id": proposal["page_id"], "revision_id": revision["id"]}, {"operation": "evidence", "evidence": evidence}):
                        denied = await session.call_tool("get", arguments)
                        self.assertTrue(denied.is_error, "An existing MCP session must not retain revoked content")
                        self.assertNotIn(raw.strip(), denied.model_dump_json())
            asyncio.run(verify())


if __name__ == "__main__":
    unittest.main()
