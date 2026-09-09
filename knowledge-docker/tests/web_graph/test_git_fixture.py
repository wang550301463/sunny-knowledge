"""Local fixture preparation checks; these are not browser acceptance."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class WebGraphFixtureTest(unittest.TestCase):
    def test_real_git_fixture_has_two_proven_dependencies_and_three_languages(self):
        spec = importlib.util.spec_from_file_location("web_graph_git_fixture", HERE / "git_server.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repository"
            manifest, backend = module.prepare(root)
            self.assertEqual(manifest["kind"], "web_graph_three_language_git_fixture")
            self.assertEqual(manifest["dependencies"], ["org.slf4j:slf4j-api", "com.google.guava:guava"])
            self.assertEqual(len(manifest["commits"]["v1"]), 40)
            self.assertNotEqual(manifest["commits"]["v1"], manifest["commits"]["v2"])
            self.assertIn("java/src/main/java/example/Payment.java", manifest["files"]["v1"])
            self.assertIn("ts/src/payment.ts", manifest["files"]["v1"])
            self.assertIn("go/payment.go", manifest["files"]["v1"])
            pom = backend.FILES["java/pom.xml"]
            self.assertEqual(pom.count("<dependency>"), 2)
            self.assertIn("<artifactId>guava</artifactId>", pom)
            self.assertIn("<version>33.4.0-jre</version>", pom)
            self.assertFalse((root / "work" / "node_modules").exists())
            self.assertIn('"postinstall"', backend.FILES["ts/package.json"])


if __name__ == "__main__":
    unittest.main()