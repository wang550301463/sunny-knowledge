"""Fixture provisioning must keep dedicated catalogs and credentials isolated."""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


class GitGraphSetupTests(unittest.TestCase):
    def test_secret_environment_is_written_with_private_permissions(self):
        module = importlib.import_module("setup_git_graph_regression")
        self.assertTrue(callable(getattr(module, "private_text", None)), "Private atomic environment writer missing")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "fixture.env"
            old_umask = os.umask(0)
            try:
                module.private_text(target, "SECRET=fixture-only\n")
            finally:
                os.umask(old_umask)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(target.read_text(), "SECRET=fixture-only\n")
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_environment_targets_separate_owned_schemas_and_quotes_credentials(self):
        module = importlib.import_module("setup_git_graph_regression")
        values = {"DB_PASSWORD_RETRIEVAL": "fixture:$@/", "DB_PASSWORD_GRAPHITI": "fixture:?&#"}
        record = {"graph_namespace": "graph-fixture-unique", "es_index": "es-fixture-unique", "schemas": {"retrieval": "test_retrieval_unique", "graphiti": "test_graph_unique"}, "models": {"embedding": {"configuration_id": "new-embedding"}, "rerank": {"configuration_id": "new-rerank"}}}
        env = module.environment(values, record)
        for service, key in (("retrieval", "GIT_GRAPH_RETRIEVAL_DATABASE_URL"), ("graphiti", "GRAPH_REGRESSION_DATABASE_URL")):
            parsed = urlsplit(env[key])
            self.assertEqual(parsed.username, service)
            self.assertEqual(unquote(parsed.password), values["DB_PASSWORD_" + service.upper()])
            self.assertEqual(parse_qs(parsed.query)["options"], ["-csearch_path=" + record["schemas"][service]])
            self.assertEqual(parsed.hostname, "postgres")
        self.assertFalse(any(key.startswith("REGRESSION_") for key in env))


if __name__ == "__main__":
    unittest.main()