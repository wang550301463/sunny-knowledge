"""Real gateway images and Valkey, confined to two private test gateway instances."""

import unittest

import httpx


class GatewayAdmissionRegression(unittest.TestCase):
    def test_real_peer_buckets_keep_oauth_and_reject_spoofed_identity(self):
        base = "http://gateway-admission-regression:8080"
        with httpx.Client(timeout=8, trust_env=False) as client:
            self.assertEqual(client.get(base + "/readyz").status_code, 200)
            for path, initial in (
                ("/api/v1/pages", 401),
                ("/mcp", 401),
                ("/idp/realms/knowledge/.well-known/openid-configuration", 200),
            ):
                for number, expected in enumerate((initial, initial, 429)):
                    response = client.get(base + path, headers={
                        "Forwarded": "for=192.0.2." + str(number),
                        "X-Forwarded-For": "192.0.2." + str(number),
                        "X-Principal-ID": "spoof-" + str(number),
                        "Accept": "application/json, text/event-stream",
                    })
                    self.assertEqual(response.status_code, expected, path)
                    if path == "/mcp" and expected == 401:
                        self.assertIn("resource_metadata=", response.headers.get("www-authenticate", ""))
                    if expected == 429:
                        self.assertEqual(response.json()["error"]["code"], "rate_limited")
                        self.assertGreaterEqual(int(response.headers["retry-after"]), 1)
                        self.assertEqual(response.headers["cache-control"], "no-store")
            # Exhausting dynamic quotas does not prevent basic operation/health checks.
            self.assertEqual(client.get(base + "/healthz").status_code, 200)
            self.assertEqual(client.get(base + "/").status_code, 200)

    def test_real_process_with_failed_valkey_stays_closed_for_dynamic_reads(self):
        base = "http://gateway-unavailable-regression:8080"
        with httpx.Client(timeout=8, trust_env=False) as client:
            for path in ("/api/v1/pages", "/mcp", "/idp/realms/knowledge", "/readyz"):
                response = client.get(base + path)
                self.assertEqual(response.status_code, 503, path)
                self.assertEqual(response.json()["error"]["code"], "rate_limit_unavailable")
            self.assertEqual(client.get(base + "/healthz").status_code, 200)
            self.assertEqual(client.get(base + "/").status_code, 200)


if __name__ == "__main__":
    unittest.main()
