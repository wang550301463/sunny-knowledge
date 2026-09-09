"""Real Git → ingest → canonical → ES/Graphiti HTTP acceptance.

Embedding/reranking use the explicitly labelled protocol fixture. Missing graph
workers or incomplete physical adjacency fail this test; no graph is simulated.
"""
import hashlib
import json
import os
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from oidc_login import http_client, login


class GitGraphHTTPRegression(unittest.TestCase):
    def test_real_git_two_commits_three_languages_dependency_paths_and_revocation(self):
        self.config = json.loads((ROOT / ".local/test-env.json").read_text())
        record = json.loads((ROOT / ".local/git-graph-regression.json").read_text())
        self.assertEqual(record["state"], "ready")
        self.assertEqual(record["model_kind"], "deterministic_protocol_simulation")
        self.fixture_url = os.environ.get("GIT_GRAPH_FIXTURE_URL", "http://git-fixture:8080")
        manifest_response = httpx.get(self.fixture_url + "/manifest.json", timeout=10)
        self.assertEqual(manifest_response.status_code, 200)
        self.manifest = manifest_response.json()
        self.assertEqual(self.manifest["kind"], "fixed_three_language_git_fixture")
        self.assertNotEqual(self.manifest["commits"]["v1"], self.manifest["commits"]["v2"])
        self.snapshots = {}
        with http_client(self.config["public_url"], base_url=self.config["public_url"], timeout=30) as self.client:
            self.refresh()
            me = self.call("GET", "/me")
            space = self.call("POST", "/spaces", {"name": "git-graph-" + uuid4().hex}, 201)["id"]
            actor = "user:" + me["id"]
            workers = {service: "service:" + self.config[service + "_principal_id"] for service in ("ingest", "retrieval", "graphiti")}
            self.call("PUT", "/grants", {"space_id": space, "action": "read", "subjects": [actor, *workers.values()]})
            self.call("PUT", "/grants", {"space_id": space, "action": "write", "subjects": [actor, workers["ingest"]]})
            source = self.call("POST", "/sources", {
                "name": "Fixed Java TypeScript Go dependency fixture", "kind": "git", "space_id": space,
                "config": {"url": self.fixture_url + self.manifest["repository_path"], "ref": self.manifest["refs"]["v1"]},
            }, 201)
            source_path = "/sources/" + source["id"]
            first = self.sync(source_path, "v1", 1)
            first_pages = self.published_pages(first, "v1")
            self.assertTrue({"java/src/main/java/example/Payment.java", "ts/src/payment.ts", "go/payment.go"} <= set(first_pages))
            first_graph = self.verify_dependency_paths(space, first_pages, "v1")
            self.verify_search(space, first_pages["java/pom.xml"], "org.slf4j:slf4j-api", "v1")
            legacy = first_pages["go/legacy.go"]
            old_revision = legacy["revision"]["id"]
            source = self.call("PATCH", source_path, {"base_version": 1, "name": "Fixed Java TypeScript Go dependency fixture", "config": {"url": self.fixture_url + self.manifest["repository_path"], "ref": self.manifest["refs"]["v2"]}})
            self.assertEqual(source["version"], 2)
            second = self.sync(source_path, "v2", 2)
            second_pages = self.published_pages(second, "v2")
            deleted = second_pages["go/legacy.go"]
            self.assertEqual(deleted["revision"]["content"]["state"], "stale")
            self.assertEqual(deleted["revision"]["publication_kind"], "structured_invalidation")
            self.assertNotEqual(deleted["revision"]["id"], old_revision)
            retained = self.call("GET", "/pages/" + quote(legacy["id"], safe="") + "/revisions/" + old_revision)
            self.assertEqual(retained["content"]["state"], "valid")
            second_graph = self.verify_dependency_paths(space, second_pages, "v2")
            search = self.verify_search(space, second_pages["java/pom.xml"], "org.slf4j:slf4j-api", "v2")
            # Canonical current-only checks reject an old fragment immediately, even
            # if an asynchronous ES/Neo4j worker still retains its physical record.
            retired = self.call("POST", "/traverse", {"space_ids": [space], "seed_fragment_ids": [self.fragment(legacy, legacy["revision"]["content"]["claims"][0]["id"])], "relation": {"types": ["depends_on"], "direction": "outgoing", "hops": 1}})
            self.assertEqual(retired["items"], [])
            self.assertEqual(retired["graph"]["edges"], [])
            timeline = self.call("POST", "/timeline", {"space_ids": [space], "page_ids": [second_pages["java/pom.xml"]["id"]], "limit": 10})
            serialized = json.dumps(timeline)
            self.assertIn(self.manifest["commits"]["v1"], serialized)
            self.assertIn(self.manifest["commits"]["v2"], serialized)
            # Source-level tightening constrains every derived page, exact original,
            # old revision, graph partition and retrieval result at the same epoch.
            self.call("PUT", "/grants", {"space_id": space, "resource_id": source["resource_id"], "action": "read", "subjects": list(workers.values())})
            denied_search = self.call("POST", "/search", {"query": "org.slf4j:slf4j-api", "space_ids": [space]})
            denied_graph = self.call("POST", "/traverse", {"space_ids": [space], "seed_fragment_ids": second_graph["seeds"], "relation": {"types": ["depends_on"], "direction": "outgoing", "hops": 1}})
            for result in (denied_search, denied_graph):
                self.assertEqual(result["items"], [])
                self.assertEqual(result["evidence"], [])
                self.assertEqual(result["graph"]["nodes"], [])
                self.assertEqual(result["graph"]["edges"], [])
            for path in (source_path, "/pages/" + quote(legacy["id"], safe=""), "/pages/" + quote(legacy["id"], safe="") + "/revisions/" + old_revision, "/source-snapshots/" + search["evidence"][0]["evidence"]["revision_id"]):
                self.call("GET", path, want=403)
            denied_timeline = self.call("POST", "/timeline", {"space_ids": [space], "page_ids": [legacy["id"]], "limit": 10})
            self.assertNotIn(old_revision, json.dumps(denied_timeline))
            print("Git/graph HTTP acceptance: two real commits, three languages, " + str(len(first_graph["edges"]) + len(second_graph["edges"])) + " physical dependency edges; revocation passed. Models: protocol simulation only.")

    def refresh(self):
        token = login(self.config["public_url"], self.config["admin_username"], self.config["admin_password"])
        self.client.headers["Authorization"] = "Bearer " + token["access_token"]
        self.refreshed = time.monotonic()

    def response(self, method, path, body=None):
        if time.monotonic() - self.refreshed > 120:
            self.refresh()
        return self.client.request(method, "/api/v1" + path, **({"json": body} if body is not None else {}))

    def call(self, method, path, body=None, want=200):
        response = self.response(method, path, body)
        self.assertEqual(response.status_code, want, f"{method} {path}: HTTP {response.status_code}, expected {want}")
        return response.json()

    def sync(self, source_path, version, number):
        preview = self.call("POST", source_path + "/preview", {"base_version": number})
        self.assertEqual(preview["source_revision"], self.manifest["commits"][version])
        self.assertEqual(preview["file_count"], len(self.manifest["files"][version]))
        self.assertEqual({f["path"]: f["sha256"] for f in preview["files"]}, {path: value["sha256"] for path, value in self.manifest["files"][version].items()})
        task = self.call("POST", source_path + "/sync", {"base_version": number}, 202)
        duplicate = self.call("POST", source_path + "/sync", {"base_version": number}, 202)
        self.assertEqual(task["id"], duplicate["id"])
        deadline = time.monotonic() + 180
        while task["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.5)
            task = self.call("GET", "/tasks/" + task["id"])
        self.assertEqual(task["status"], "review_needed", "Git narrative review/static compilation failed: " + task["status"] + "/" + str(task.get("error_code")))
        return task

    def published_pages(self, task, version):
        pages = {}
        for item in task["result"]["items"]:
            if item["path"] == "README.md":
                self.assertEqual(item["status"], "review_needed")
                proposal = self.call("GET", "/reviews/" + item["proposal_id"])
                self.assertEqual(proposal["status"], "pending")
                self.call("POST", "/reviews/" + item["proposal_id"] + "/approve", {"reason": "Accept exact original fixture narrative; no generated conclusion"})
            else:
                self.assertEqual(item["status"], "published", "Deterministic Git fact unexpectedly requires manual recovery: " + item["path"])
            page = self.call("GET", "/pages/" + quote(item["page_id"], safe=""))
            self.assertIsNotNone(page["revision"])
            pages[item["path"]] = page
            if item["path"] in self.manifest["files"][version]:
                self.assertEqual(page["revision"]["content"]["state"], "valid")
                content = page["revision"]["content"]
                for claim in content["claims"]:
                    for ref in claim["evidence"]:
                        self.check_ref(ref, version)
                for ref in content["evidence"]:
                    self.check_ref(ref, version)
        for path, language in (("java/src/main/java/example/Payment.java", "java"), ("ts/src/payment.ts", "typescript"), ("go/payment.go", "go")):
            self.assertIn("Language: " + language, pages[path]["revision"]["content"]["markdown"])
            self.assertTrue(any(claim["text"].startswith("Declares ") for claim in pages[path]["revision"]["content"]["claims"]))
        return pages

    def check_ref(self, ref, version):
        self.assertEqual(ref["source_revision"], self.manifest["commits"][version])
        self.assertIn(ref["path"], self.manifest["files"][version])
        if ref["revision_id"] not in self.snapshots:
            self.snapshots[ref["revision_id"]] = self.call("GET", "/source-snapshots/" + ref["revision_id"])
        snapshot = self.snapshots[ref["revision_id"]]
        self.assertEqual(snapshot["source_revision"], ref["source_revision"])
        self.assertEqual(snapshot["path"], ref["path"])
        self.assertEqual(hashlib.sha256(snapshot["text"].encode()).hexdigest(), self.manifest["files"][version][ref["path"]]["sha256"])
        lines = snapshot["text"].split("\n")
        if lines[-1] == "":
            lines.pop()
        self.assertTrue(1 <= ref["start_line"] <= ref["end_line"] <= len(lines))
        return "\n".join(lines[ref["start_line"] - 1:ref["end_line"]])

    @staticmethod
    def fragment(page, claim_id):
        # Public immutable fragment identity contract, independent of service imports.
        payload = [page["id"], page["revision"]["id"], "claim", claim_id, 0]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def verify_dependency_paths(self, space, pages, version):
        seeds, expected = [], set()
        for path, dependency, required_version in (("java/pom.xml", "org.slf4j:slf4j-api", "2.0.16" if version == "v1" else "2.0.17"), ("ts/package.json", "lodash", "4.17.21"), ("go/go.mod", "github.com/google/uuid", "v1.6.0")):
            page = pages[path]
            claims = page["revision"]["content"]["claims"]
            module = next(claim for claim in claims if claim.get("entity_type") == "Module")
            target = next(claim for claim in claims if claim.get("entity", {}).get("name") == dependency)
            self.assertIn(required_version, target["text"])
            self.assertTrue(any(c.get("relation") == {"source_id": module["id"], "target_id": target["id"], "type": "depends_on"} for c in claims))
            seeds.append(self.fragment(page, module["id"]))
            expected.add((module["id"], target["id"], "depends_on"))
        request = {"space_ids": [space], "seed_fragment_ids": seeds, "relation": {"types": ["depends_on"], "direction": "outgoing", "hops": 1}}
        deadline, result = time.monotonic() + 180, None
        while time.monotonic() < deadline:
            response = self.response("POST", "/traverse", request)
            self.assertIn(response.status_code, (200, 503), "Graph projection poll has unexpected authorization/protocol failure")
            if response.status_code == 200:
                candidate = response.json()
                actual = {(edge["source"], edge["target"], edge["type"]) for edge in candidate["graph"]["edges"]}
                if expected <= actual and not candidate["degraded"] and not candidate["graph"]["degraded"]:
                    result = candidate
                    break
            time.sleep(1)
        self.assertIsNotNone(result, "Real Graphiti/ES workers did not materialize all three evidence-backed dependency paths")
        self.assertTrue(result["graph"]["paths"])
        self.assertTrue(all(edge["kind"] == "fact" and edge["fragment_ids"] for edge in result["graph"]["edges"]))
        for citation in result["evidence"]:
            self.assertEqual(citation["excerpt"], self.check_ref(citation["evidence"], version))
        return {"seeds": seeds, "edges": result["graph"]["edges"]}

    def verify_search(self, space, page, query, version):
        deadline, result = time.monotonic() + 120, None
        while time.monotonic() < deadline:
            response = self.response("POST", "/search", {"query": query, "space_ids": [space], "limit": 12})
            self.assertIn(response.status_code, (200, 503))
            if response.status_code == 200 and any(item["revision_id"] == page["revision"]["id"] for item in response.json()["items"]):
                result = response.json()
                break
            time.sleep(1)
        self.assertIsNotNone(result, "Real ES hybrid retrieval did not return the current compiled manifest")
        self.assertTrue(result["evidence"])
        for citation in result["evidence"]:
            self.assertEqual(citation["excerpt"], self.check_ref(citation["evidence"], version))
        self.assertEqual(result["deployment_state"], "unknown_without_deployment_evidence")
        return result


if __name__ == "__main__":
    unittest.main()