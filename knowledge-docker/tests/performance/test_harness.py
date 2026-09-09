"""Small real Git/parser/TCP boundaries; never imports the 100k corpus into a live stack."""

import importlib.util
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent


def module(case, name):
    path = ROOT / (name + ".py")
    case.assertTrue(path.is_file(), "Performance " + name + " implementation missing")
    spec = importlib.util.spec_from_file_location("perf_" + name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def answer(query):
    ref = {"source_id": "source", "source_revision": "a" * 40, "revision_id": "snapshot",
           "resource_id": "resource", "path": "go/main.go", "start_line": 2, "end_line": 2, "kind": "code"}
    return {"items": [{"page_id": "page", "revision_id": "revision", "primary": True,
                       "text": "Declares " + query, "citation_ids": ["citation"]}],
            "evidence": [{"id": "citation", "evidence": ref, "excerpt": "func " + query + "() {}"}]}


class FixtureTests(unittest.TestCase):
    def test_real_git_shallow_capture_compiles_three_languages_with_exact_evidence(self):
        fixture = module(self, "fixture")
        from knowledge_platform.ingest.connectors import GitConnector
        from knowledge_platform.ingest.analyzers import SourceFile, SourceSnapshot, analyze_snapshot
        from knowledge_platform.ingest.compiler import KnowledgeCompiler
        from knowledge_platform.knowledge.schemas import PageContent

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = fixture.build(root / "one", repositories=2, symbols=4)
            self.assertEqual(manifest, fixture.build(root / "two", repositories=2, symbols=4))
            server = fixture.server(root / "one", manifest, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                for repo in manifest["repositories"]:
                    raw = GitConnector().capture({"url": base + repo["path"], "ref": repo["ref"]}, None, 1)
                    self.assertEqual(raw.revision, repo["commit"])
                    self.assertEqual({f.path: f.digest for f in raw.files}, repo["files"])
                    analysis = analyze_snapshot(SourceSnapshot(repo["name"], raw.revision, tuple(SourceFile(f.path, f.data) for f in raw.files)))
                    self.assertTrue({"go", "java", "typescript"} <= {f.language for f in analysis.files})
                    self.assertGreaterEqual(len(analysis.dependencies), 3)
                    snapshots = {f.path: {"id": "snapshot:" + f.path, "source_id": repo["name"], "source_revision": raw.revision,
                        "resource_id": "resource", "path": f.path, "kind": f.kind, "text": f.data.decode()} for f in raw.files}
                    pages = KnowledgeCompiler().code_pages(analysis, snapshots)
                    for page in pages:
                        content = PageContent.model_validate(page["content"])
                        self.assertTrue(content.supports())
                        for ref in content.supports():
                            self.assertEqual(ref.source_revision, repo["commit"])
                            self.assertLessEqual(ref.end_line, len(snapshots[ref.path]["text"].splitlines()))
                    for query in repo["queries"]:
                        self.assertTrue(any(query["query"] in c["text"] for p in pages for c in p["content"]["claims"]))
                for path in ("/../private", "/%2e%2e/private", "/other.git/info/refs?service=git-upload-pack", "/repo-000.git/info/refs?service=git-receive-pack", "/repo-000.git/HEAD"):
                    self.assertIn(httpx.get(base + path).status_code, (400, 404))
                self.assertEqual(httpx.get(base + "/manifest.json", headers={"Authorization": "private"}).status_code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_bounds_and_existing_fixture_directory_are_rejected(self):
        fixture = module(self, "fixture")
        with tempfile.TemporaryDirectory() as temporary:
            for repositories, symbols in ((0, 340), (101, 340), (100, 0), (100, 899), (True, 4)):
                with self.assertRaises(ValueError):
                    fixture.build(Path(temporary) / "new", repositories=repositories, symbols=symbols)
            with self.assertRaises((ValueError, FileExistsError)):
                fixture.build(Path(temporary), repositories=1, symbols=2)


class RunnerTests(unittest.TestCase):
    def test_ten_distinct_actors_real_tcp_concurrency_and_all_failures_recorded(self):
        run = module(self, "run")
        lock, state = threading.Lock(), {"active": 0, "peak": 0, "actors": set(), "requests": 0}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with lock:
                    state["active"] += 1
                    state["peak"] = max(state["peak"], state["active"])
                    state["actors"].add(self.headers["Authorization"])
                    state["requests"] += 1
                    number = state["requests"]
                time.sleep(0.06)
                value = answer(body["query"])
                code = 429 if number == 1 else 200
                data = json.dumps(value if code == 200 else {"secret": "MUST_NOT_REPORT"}).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Retry-After", "60")
                self.end_headers()
                self.wfile.write(data)
                with lock:
                    state["active"] -= 1
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            actors = [{"id": str(i), "token": "private-token-" + str(i)} for i in range(10)]
            result = run.load(f"http://127.0.0.1:{server.server_port}", actors,
                [{"query": "PerfOperation", "space_id": "space", "source_id": "source", "commit": "a" * 40, "path": "go/main.go"}], requests_per_actor=2)
            self.assertEqual(result["peak_concurrency"], 10)
            self.assertEqual(state["peak"], 10)
            self.assertEqual(len(state["actors"]), 10)
            self.assertEqual(result["successful_requests"], 19)
            self.assertEqual(result["failed_requests"], 1)
            self.assertEqual(len(result["requests"]), 20)
            failed = [r for r in result["requests"] if not r["ok"]]
            self.assertEqual(failed[0]["status"], 429)
            self.assertEqual(failed[0]["retry_after"], 60)
            self.assertNotIn("private-token", json.dumps(result))
            self.assertNotIn("MUST_NOT_REPORT", json.dumps(result))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_empty_or_wrong_source_or_unsupported_answers_are_not_successes(self):
        run = module(self, "run")
        query = {"query": "PerfOperation", "source_id": "source", "commit": "a" * 40, "path": "go/main.go"}
        run.validate_answer(answer(query["query"]), query)
        for mutation in ("empty", "commit", "citation", "excerpt"):
            value = answer(query["query"])
            if mutation == "empty": value["items"] = []
            elif mutation == "commit": value["evidence"][0]["evidence"]["source_revision"] = "wrong"
            elif mutation == "citation": value["items"][0]["citation_ids"] = ["other"]
            else: value["evidence"][0]["excerpt"] = "unrelated"
            with self.assertRaises(ValueError): run.validate_answer(value, query)

    def test_histogram_filters_exact_fixed_series_and_rejects_duplicates_or_missing(self):
        run = module(self, "run")
        labels = 'service="retrieval",component="retrieval",operation="search_local",outcome="success"'
        value = '\n'.join([f'knowledge_domain_duration_seconds_bucket{{{labels},le="2.0"}} 3', f'knowledge_domain_duration_seconds_bucket{{{labels},le="+Inf"}} 4', f'knowledge_domain_duration_seconds_count{{{labels}}} 4', f'knowledge_domain_duration_seconds_sum{{{labels}}} 2.3'])
        expected = {"count": 4, "sum": 2.3, "buckets": {"2.0": 3, "+Inf": 4}}
        self.assertEqual(run.histogram(value, "retrieval", "retrieval", "search_local"), expected)
        for bad in ("", value + '\n' + value):
            with self.assertRaises(ValueError): run.histogram(bad, "retrieval", "retrieval", "search_local")


if __name__ == "__main__":
    unittest.main()
