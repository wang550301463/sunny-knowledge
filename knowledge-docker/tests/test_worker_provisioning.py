"""Worker bootstrap uses distinct credentials and never grants knowledge implicitly."""

import contextlib
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


class WorkerProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module("configure_worker")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".local").mkdir()
        (self.root / ".local/test-env.json").write_text("{}")
        values = {
            "PUBLIC_WEB_URL": "http://localhost:18180",
            "KEYCLOAK_ADMIN_PASSWORD": "private-admin-key",
            "BOOTSTRAP_USERNAME": "admin",
            "BOOTSTRAP_PASSWORD": "private-user-key",
        }
        for worker in ("ingest", "retrieval", "graphiti"):
            values[f"{worker.upper()}_OIDC_CLIENT_ID"] = f"knowledge-{worker}-worker"
            values[f"{worker.upper()}_OIDC_CLIENT_SECRET"] = f"private-{worker}-key"
        (self.root / ".env").write_text("\n".join(f"{k}={v}" for k, v in values.items()))
        self.clients, self.accounts, self.writes = {}, [], []

    def handle(self, request):
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content.startswith(b"{") else None
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            self.writes.append((method, path, body))
        if path.endswith("/token"):
            if "/master/" in path:
                return httpx.Response(200, json={"access_token": "admin-token"})
            from urllib.parse import parse_qs

            values = parse_qs(request.content.decode())
            client = self.clients[values["client_id"][0]]
            self.assertEqual(values["client_secret"], [client["secret"]])
            return httpx.Response(200, json={"access_token": "machine:" + client["id"]})
        if path.endswith("/clients"):
            if method == "GET":
                client = self.clients.get(request.url.params["clientId"])
                return httpx.Response(200, json=[client] if client else [])
            client = {**body, "id": body["clientId"] + "-uuid"}
            self.clients[body["clientId"]] = client
            return httpx.Response(201)
        if "/clients/" in path:
            client = next(c for c in self.clients.values() if c["id"] == path.split("/")[-2])
            if path.endswith("/client-secret"):
                return httpx.Response(200, json={"value": client["secret"]})
            if path.endswith("/service-account-user"):
                return httpx.Response(200, json={"id": client["id"] + "-subject"})
        if path == "/api/v1/service-accounts":
            if method == "GET":
                return httpx.Response(200, json={"items": self.accounts})
            self.accounts.append({**body, "active": True})
            return httpx.Response(201, json=body)
        if path == "/api/v1/me":
            account_id = request.headers["Authorization"].removeprefix("Bearer machine:") + "-subject"
            self.assertTrue(any(a["id"] == account_id for a in self.accounts))
            return httpx.Response(200, json={"id": account_id, "subjects": ["service:" + account_id], "permissions": []})
        self.fail(f"Unexpected provisioning operation {method} {path}")

    def configure(self, service):
        with (
            patch.object(self.module, "http_client", lambda *a, **kw: httpx.Client(transport=httpx.MockTransport(self.handle))),
            patch.object(self.module, "login", return_value={"access_token": "human-token"}),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.module.configure(service, root=self.root)
        self.assertNotIn("private-", output.getvalue())

    def test_workers_have_distinct_machine_accounts_and_read_scopes_only_for_projections(self):
        for service in ("ingest", "retrieval", "graphiti"):
            self.configure(service)
            client = self.clients[f"knowledge-{service}-worker"]
            scopes = {"basic", "knowledge:read"}
            if service == "ingest":
                scopes.add("knowledge:write")
            self.assertEqual(set(client["defaultClientScopes"]), scopes)
            self.assertFalse(client["publicClient"])
            self.assertFalse(client["standardFlowEnabled"])
            self.assertFalse(client["directAccessGrantsEnabled"])
            target = self.root / f".local/{service}-worker.json"
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("private-", target.read_text())
        self.assertEqual(len({c["secret"] for c in self.clients.values()}), 3)
        self.assertEqual(len({a["id"] for a in self.accounts}), 3)
        config = json.loads((self.root / ".local/test-env.json").read_text())
        self.assertEqual(len({config[f"{s}_principal_id"] for s in ("ingest", "retrieval", "graphiti")}), 3)
        self.assertFalse(any("grant" in path for _, path, _ in self.writes))
        self.writes.clear()
        self.configure("retrieval")
        self.assertFalse(any(body for _, _, body in self.writes), "Rerun must not mutate identity bindings")

    def test_disabled_keycloak_client_is_not_silently_reenabled_or_verified(self):
        self.configure("retrieval")
        self.clients["knowledge-retrieval-worker"]["enabled"] = False
        self.writes.clear()
        with self.assertRaisesRegex(RuntimeError, "authentication settings"):
            self.configure("retrieval")
        self.assertFalse(any(body for _, _, body in self.writes))

    def test_mismatched_secret_is_not_overwritten(self):
        self.configure("retrieval")
        self.clients["knowledge-retrieval-worker"]["secret"] = "operator-replaced-key"
        self.writes.clear()
        with self.assertRaisesRegex(RuntimeError, "credential differs"):
            self.configure("retrieval")
        self.assertFalse(any(body for _, _, body in self.writes))

    def test_disabled_iam_account_is_not_reactivated(self):
        self.configure("retrieval")
        self.accounts[0]["active"] = False
        self.writes.clear()
        with self.assertRaisesRegex(RuntimeError, "different or disabled"):
            self.configure("retrieval")
        self.assertFalse(any(body for _, _, body in self.writes))


if __name__ == "__main__":
    unittest.main()
