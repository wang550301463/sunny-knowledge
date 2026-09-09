"""Dedicated MCP clients cannot inherit Web/worker audiences or write privileges."""

import copy
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


class MCPProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module("configure_mcp_client")
        self.expected = self.module.client_definition(
            "http://localhost:18180", "knowledge-mcp",
            ["http://127.0.0.1:18791/callback", "http://localhost:18791/callback"],
        )

    def test_explicit_pkce_client_has_only_read_and_optional_feedback(self):
        client = self.expected
        self.assertEqual(client["defaultClientScopes"], ["basic", "knowledge:read"])
        self.assertEqual(client["optionalClientScopes"], ["knowledge:feedback"])
        self.assertTrue(client["publicClient"])
        self.assertFalse(client["serviceAccountsEnabled"])
        self.assertFalse(client["directAccessGrantsEnabled"])
        self.assertFalse(client["implicitFlowEnabled"])
        self.assertEqual(client["attributes"]["pkce.code.challenge.method"], "S256")
        self.assertEqual(
            {m["config"]["included.custom.audience"] for m in client["protocolMappers"]},
            {"knowledge-api", "http://localhost:18180/mcp"},
        )
        self.module.validate_existing(client | {"id": "keycloak-uuid"}, client)

    def test_existing_unsafe_client_is_refused_without_mutation(self):
        for field, value in [
            ("enabled", False), ("publicClient", False),
            ("directAccessGrantsEnabled", True), ("serviceAccountsEnabled", True),
            ("implicitFlowEnabled", True),
            ("defaultClientScopes", ["basic", "knowledge:read", "knowledge:write"]),
            ("optionalClientScopes", ["knowledge:feedback", "knowledge:write"]),
            ("redirectUris", ["http://localhost:*"]),
            ("protocolMappers", []),
            ("attributes", {"pkce.code.challenge.method": "plain"}),
        ]:
            with self.subTest(field=field):
                existing = copy.deepcopy(self.expected)
                existing[field] = value
                before = copy.deepcopy(existing)
                with self.assertRaises(RuntimeError):
                    self.module.validate_existing(existing, self.expected)
                self.assertEqual(existing, before)

    def test_existing_extra_mapper_cannot_widen_audience(self):
        existing = copy.deepcopy(self.expected)
        existing["protocolMappers"].append({
            "name": "other-resource", "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper",
            "config": {"included.custom.audience": "another-service"},
        })
        with self.assertRaises(RuntimeError):
            self.module.validate_existing(existing, self.expected)

    def test_client_registration_only_accepts_secure_or_loopback_urls(self):
        for public, redirect in [
            ("http://example.org", "https://client.example/callback"),
            ("https://knowledge.example", "http://client.example/callback"),
            ("https://knowledge.example", "https://client.example/*"),
            ("https://knowledge.example", "https://secret@client.example/callback"),
            ("https://knowledge.example", "https://client.example/callback#fragment"),
        ]:
            with self.subTest(public=public, redirect=redirect):
                with self.assertRaises(ValueError):
                    self.module.client_definition(public, "knowledge-mcp", [redirect])


if __name__ == "__main__":
    unittest.main()
