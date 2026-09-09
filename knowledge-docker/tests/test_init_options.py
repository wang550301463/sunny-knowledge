"""Independent deployment origins must stay coherent without rotating existing secrets."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/init.py"


class DeploymentOptionsTest(unittest.TestCase):
    def run_init(self, root, *arguments):
        return subprocess.run(
            ["python3", str(SCRIPT), "--directory", str(root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_custom_project_ports_and_oidc_origin_agree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.run_init(root, "--project-name", "sunny-recovery-fixture", "--gateway-port", "28181", "--postgres-port", "25439")
            self.assertEqual(result.returncode, 0, result.stderr)
            values = dict(line.split("=", 1) for line in (root / ".env").read_text().splitlines())
            self.assertEqual(values["COMPOSE_PROJECT_NAME"], "sunny-recovery-fixture")
            self.assertEqual(values["PUBLIC_WEB_URL"], "http://localhost:28181")
            self.assertEqual(values["POSTGRES_PORT"], "25439")
            private = json.loads((root / ".local/test-env.json").read_text())
            self.assertEqual(private["public_url"], values["PUBLIC_WEB_URL"])
            self.assertTrue(all("@127.0.0.1:25439/" in value for value in private["databases"].values()))
            realm = json.loads((root / ".local/knowledge-realm.json").read_text())
            web = next(client for client in realm["clients"] if client["clientId"] == "knowledge-web")
            self.assertEqual(web["webOrigins"], [values["PUBLIC_WEB_URL"]])
            self.assertEqual(web["redirectUris"], [values["PUBLIC_WEB_URL"] + "/auth/callback"])
            self.assertNotIn(values["BOOTSTRAP_PASSWORD"], result.stdout + result.stderr)

    def test_https_public_reverse_proxy_origin_is_distinct_from_local_bind_port(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.run_init(root, "--public-url", "https://knowledge.example.test/", "--gateway-port", "28181")
            self.assertEqual(result.returncode, 0, result.stderr)
            private = json.loads((root / ".local/test-env.json").read_text())
            self.assertEqual(private["public_url"], "https://knowledge.example.test")
            self.assertIn("GATEWAY_PORT=28181\n", (root / ".env").read_text())

    def test_existing_configuration_rejects_retargeting_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            options = ("--project-name", "sunny-recovery-fixture", "--gateway-port", "28181")
            self.assertEqual(self.run_init(root, *options).returncode, 0)
            before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            for arguments in [(), options, ("--project-name", "other"), ("--public-url", "https://other.example.test")]:
                result = self.run_init(root, *arguments)
                self.assertEqual(result.returncode == 0, arguments in [(), options])
                after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
                self.assertEqual(after, before)

    def test_invalid_options_leave_directory_empty(self):
        for arguments in [
            ("--gateway-port", "0"), ("--postgres-port", "65536"),
            ("--gateway-port", "15439"), ("--project-name", "../foreign"),
            ("--public-url", "https://user:non-secret@example.test"),
            ("--public-url", "https://example.test/path"),
            ("--public-url", "https://example.test/?token=non-secret"),
            ("--public-url", "https://example.test/#fragment"),
            ("--public-url", "file:///tmp/invalid"),
        ]:
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.assertNotEqual(self.run_init(root, *arguments).returncode, 0)
                self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
