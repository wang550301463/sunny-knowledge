"""Real Keycloak PKCE -> signed auth -> live IAM; no token is decoded unverified."""

import json
import sys
import time
import unittest
from pathlib import Path

import httpx
import jwt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from oidc_login import http_client, login


class MCPOAuthRegression(unittest.TestCase):
    def test_mcp_resource_token_and_optional_feedback_are_distinct_from_web(self):
        config = json.loads((ROOT / ".local/test-env.json").read_text())
        resource = config["public_url"].rstrip("/") + "/mcp"
        private_key = Path("/run/test/mcp/private.pem").read_text()

        def resolve(token):
            now = int(time.time())
            workload = jwt.encode(
                {"iss": "knowledge-services", "sub": "mcp", "aud": "auth", "iat": now, "exp": now + 60},
                private_key, algorithm="EdDSA", headers={"kid": "mcp"},
            )
            with httpx.Client(timeout=15) as client:
                response = client.post("http://auth:8080/internal/v1/resolve", json={}, headers={
                    "Authorization": "Bearer " + token,
                    "X-Service-Token": workload,
                    "X-Principal-Audience": "forged-resource",
                })
            self.assertEqual(response.status_code, 200, "Real token failed signed auth/IAM resolution")
            return response.json()

        for feedback in (False, True):
            tokens = login(
                config["public_url"], config["admin_username"], config["admin_password"],
                client_id="knowledge-mcp", redirect_uri="http://127.0.0.1:18791/callback",
                scope="openid knowledge:read" + (" knowledge:feedback" if feedback else ""),
                resource=resource,
            )
            resolved = resolve(tokens["access_token"])
            self.assertEqual(set(resolved["audiences"]), {resource, "knowledge-api"})
            self.assertEqual(resolved["issuer"], config["public_url"] + "/idp/realms/knowledge")
            self.assertEqual(resolved["client_id"], "knowledge-mcp")
            self.assertGreater(resolved["expires_at"], time.time())
            self.assertIn("knowledge:read", resolved["scopes"])
            self.assertNotIn("knowledge:write", resolved["scopes"])
            self.assertEqual("knowledge:feedback" in resolved["scopes"], feedback)
            self.assertTrue(resolved["principal"]["auth_epoch"] >= 1)
            with http_client(config["public_url"], timeout=15) as client:
                response = client.get(config["public_url"] + "/api/v1/me", headers={"Authorization": "Bearer " + tokens["access_token"]})
                self.assertEqual(response.status_code, 200, "Dedicated multi-audience token must remain usable by the platform domain APIs")
        web = login(config["public_url"], config["admin_username"], config["admin_password"])
        resolved = resolve(web["access_token"])
        self.assertNotIn(resource, resolved["audiences"], "Web clients must not inherit MCP authorization")
        self.assertEqual(resolved["client_id"], "knowledge-web")


if __name__ == "__main__":
    unittest.main()
