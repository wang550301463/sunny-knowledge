"""Real smart HTTP Git and no-execution boundaries; no provider simulation here."""
import importlib.util
import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class GitFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = None
        cls.directory = tempfile.TemporaryDirectory(prefix="git-fixture-test-")

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.shutdown()
            cls.server.server_close()
            cls.thread.join()
        cls.directory.cleanup()

    def fixture(self):
        self.assertTrue((ROOT / "server.py").is_file(), "Read-only smart HTTP fixture is missing")
        cls = type(self)
        if cls.server is None:
            spec = importlib.util.spec_from_file_location("git_fixture_server", ROOT / "server.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls.module = module
            cls.manifest = module.build_repository(Path(cls.directory.name) / "fixture")
            cls.server = module.make_server(Path(cls.directory.name) / "fixture", cls.manifest, "127.0.0.1", 0)
            cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
            cls.thread.start()
        return f"http://127.0.0.1:{cls.server.server_port}"

    def test_two_fixed_commits_are_reproducible_and_shallow_fetchable(self):
        base = self.fixture()
        manifest = json.load(urllib.request.urlopen(base + "/manifest.json"))
        other = self.module.build_repository(Path(self.directory.name) / "second")
        self.assertEqual(manifest, other)
        with tempfile.TemporaryDirectory(prefix="git-shallow-test-") as checkout:
            subprocess.run(["git", "init", "--bare", checkout], check=True, capture_output=True)
            for version in ("v1", "v2"):
                subprocess.run(["git", "-C", checkout, "fetch", "--depth=1", "--no-tags", base + "/fixture.git", manifest["refs"][version]], check=True, capture_output=True)
                commit = subprocess.check_output(["git", "-C", checkout, "rev-parse", "FETCH_HEAD"]).decode().strip()
                self.assertEqual(commit, manifest["commits"][version])
                self.assertTrue((Path(checkout) / "shallow").is_file())
            deleted = subprocess.run(["git", "-C", checkout, "cat-file", "-e", commit + ":go/legacy.go"], capture_output=True)
            self.assertNotEqual(deleted.returncode, 0)

    def test_read_only_allowlist_rejects_push_traversal_and_arbitrary_files(self):
        base = self.fixture()
        for method, path in [("GET", "/fixture.git/info/refs?service=git-receive-pack"), ("POST", "/fixture.git/git-receive-pack"), ("GET", "/other.git/info/refs?service=git-upload-pack"), ("GET", "/fixture.git/HEAD"), ("GET", "/../etc/passwd"), ("GET", "/%2e%2e/etc/passwd")]:
            with self.subTest(method=method, path=path):
                request = urllib.request.Request(base + path, data=b"" if method == "POST" else None, method=method)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request)
                self.assertIn(caught.exception.code, (400, 404, 405))

    def test_git_connector_and_static_compiler_keep_exact_three_language_locations(self):
        base = self.fixture()
        from knowledge_platform.ingest.connectors import GitConnector
        from knowledge_platform.ingest.analyzers import SourceFile, SourceSnapshot, analyze_snapshot
        from knowledge_platform.ingest.compiler import KnowledgeCompiler
        for version in ("v1", "v2"):
            raw = GitConnector().capture({"url": base + "/fixture.git", "ref": self.manifest["refs"][version]}, None, 1)
            self.assertEqual(raw.source_revision, self.manifest["commits"][version])
            analysis = analyze_snapshot(SourceSnapshot("fixture", raw.source_revision, tuple(SourceFile(f.path, f.data) for f in raw.files)))
            self.assertTrue({"java", "typescript", "go"} <= {f.language for f in analysis.files})
            self.assertTrue({"org.slf4j:slf4j-api", "lodash", "github.com/google/uuid"} <= {d.name for d in analysis.dependencies})
            snapshots = {f.path: {"id": "snapshot:"+f.path, "source_id": "fixture", "source_revision": raw.source_revision, "resource_id": "source:fixture", "path": f.path, "kind": f.kind, "text": f.data.decode()} for f in raw.files}
            pages = KnowledgeCompiler().code_pages(analysis, snapshots)
            edges = [claim for page in pages for claim in page["content"]["claims"] if claim.get("relation", {}).get("type") == "depends_on"]
            self.assertGreaterEqual(len(edges), 3)
            for page in pages:
                for claim in page["content"]["claims"]:
                    for ref in claim["evidence"]:
                        self.assertEqual(ref["source_revision"], raw.source_revision)
                        lines = snapshots[ref["path"]]["text"].splitlines()
                        self.assertTrue(1 <= ref["start_line"] <= ref["end_line"] <= len(lines))
            self.assertFalse(Path("/tmp/knowledge-git-fixture-must-not-run").exists())


if __name__ == "__main__":
    unittest.main()