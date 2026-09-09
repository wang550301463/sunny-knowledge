"""Explicit isolated preparation and native Git ingestion, never direct ES writes."""

import argparse
import hashlib
import json
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import private_json, validate_answer


def environment(root):
    return dict(line.split("=", 1) for line in (root / ".env").read_text().splitlines() if line and not line.startswith("#"))


def check_compose(root, project, model):
    root = Path(root).resolve()
    if not re.fullmatch(r"sunny-perf-[a-z0-9]{8,32}", project) or model.get("name") != project:
        raise ValueError("A separate performance project is required")
    for section in ("networks", "volumes"):
        for value in model.get(section, {}).values():
            if value.get("external") or not value.get("name", "").startswith(project + "_"):
                raise ValueError("Network or volume is not owned by the performance project")
    images = {}
    for name, value in model["services"].items():
        image = value.get("image", "")
        if not re.fullmatch(r"(?:sha256:|[^\s]+@sha256:)[0-9a-f]{64}", image) or value.get("build"):
            raise ValueError("Only already resolved immutable images are permitted")
        if value.get("network_mode") or value.get("container_name") or value.get("privileged"):
            raise ValueError("Unsafe cross-project container settings")
        if any(network not in model.get("networks", {}) for network in value.get("networks", {})):
            raise ValueError("Service uses unknown network")
        for mount in value.get("volumes", []):
            if mount["type"] == "bind" and not Path(mount["source"]).resolve().is_relative_to(root):
                raise ValueError("Bind source escapes the isolated deployment directory")
            if mount["type"] == "volume" and mount["source"] not in model.get("volumes", {}):
                raise ValueError("Service uses unknown volume")
        for port in value.get("ports", []):
            if port.get("host_ip") != "127.0.0.1":
                raise ValueError("Public or implicit host port binding is forbidden")
        images[name] = image
    if not images:
        raise ValueError("No fixed images supplied")
    return images


def checked_root(root):
    root = Path(root).resolve()
    config = json.loads((root / ".local/test-env.json").read_text())
    record = json.loads((root / ".local/performance.json").read_text())
    values = environment(root)
    project = values.get("COMPOSE_PROJECT_NAME", "")
    if not re.fullmatch(r"sunny-perf-[a-z0-9]{8,32}", project) or record.get("project") != project:
        raise ValueError("Refusing a main or unrelated deployment")
    if record.get("public_url") != config["public_url"] or values.get("PUBLIC_WEB_URL") != config["public_url"]:
        raise ValueError("Deployment origin changed")
    if not re.fullmatch(r"knowledge-performance-[a-z0-9]{8,32}", record.get("es_index", "")):
        raise ValueError("Fresh performance index required")
    return root, config, record


class API:
    def __init__(self, root, config):
        sys.path.insert(0, str(root / "scripts"))
        from oidc_login import http_client, login
        self.login, self.config, self.values = login, config, environment(root)
        self.client = http_client(config["public_url"], timeout=90, follow_redirects=False)
        self.last = self.refreshed = self.kc_refreshed = 0
        self.kc_token = ""
    def close(self):
        self.client.close()
    def raw(self, method, path, *, expected=200, **kwargs):
        # Preserve normal gateway limits; deliberate <=3 management requests/sec.
        time.sleep(max(0, 1 / 3 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        result = self.client.request(method, self.config["public_url"] + path, **kwargs)
        if result.status_code != expected:
            raise ValueError("Fixture management request failed, HTTP " + str(result.status_code))
        return result
    def call(self, method, path, body=None, expected=200):
        if time.monotonic() - self.refreshed > 100:
            self.token = self.login(self.config["public_url"], self.config["admin_username"], self.config["admin_password"])["access_token"]
            self.refreshed = time.monotonic()
        return self.raw(method, "/api/v1" + path, expected=expected, headers={"Authorization": "Bearer " + self.token},
                        **({"json": body} if body is not None else {})).json()
    def keycloak(self, method, path, body=None, expected=200):
        if time.monotonic() - self.kc_refreshed > 30:
            response = self.raw("POST", "/idp/realms/master/protocol/openid-connect/token", data={
                "grant_type": "password", "client_id": "admin-cli", "username": "bootstrap", "password": self.values["KEYCLOAK_ADMIN_PASSWORD"]})
            self.kc_token = response.json()["access_token"]
            self.kc_refreshed = time.monotonic()
        return self.raw(method, "/idp/admin/realms/knowledge" + path, expected=expected,
            headers={"Authorization": "Bearer " + self.kc_token}, **({"json": body} if body is not None else {}))


def validate_index(value, record):
    if value["count"] != value["filtered_count"] or set(value["pages"]) != set(record["pages"]):
        raise ValueError("Unexpected or incomplete physical index inventory")
    count = 0
    for page, revision in record["pages"].items():
        actual = value["pages"][page]
        if set(actual) != {revision} or type(actual[revision]) is not int or actual[revision] <= 0:
            raise ValueError("Old or missing indexed revision")
        count += actual[revision]
    if count != value["count"]:
        raise ValueError("Truncated physical index inventory")
    return count


def index_state(client, record, *, require_complete=False):
    base = record["es_url"] + "/" + record["es_index"]
    total = client.get(base + "/_count")
    if total.status_code == 404 and not require_complete:
        return 0
    if total.status_code != 200:
        raise ValueError("Physical ES count unavailable")
    count = total.json()["count"]
    if not require_complete:
        return count
    filters = [{"term": {"space_id": record["space_id"]}}, {"term": {"is_current": True}},
               {"term": {"state": "valid"}}, {"term": {"embedding_configuration_id": record["models"]["embedding"]["configuration_id"]}}]
    result = client.post(base + "/_search", json={"size": 0, "track_total_hits": True,
        "query": {"bool": {"filter": filters}}, "aggs": {"pages": {"terms": {"field": "page_id", "size": 10000},
            "aggs": {"revisions": {"terms": {"field": "revision_id", "size": 2}}}}}})
    if result.status_code != 200:
        raise ValueError("ES projection inventory unavailable")
    value = result.json()
    if value.get("timed_out") or value.get("_shards", {}).get("failed", 0) or value["hits"]["total"]["relation"] != "eq":
        raise ValueError("Partial ES response")
    if value["aggregations"]["pages"].get("sum_other_doc_count", 0):
        raise ValueError("Truncated page aggregation")
    return validate_index({"count": count, "filtered_count": value["hits"]["total"]["value"],
        "pages": {p["key"]: {r["key"]: r["doc_count"] for r in p["revisions"]["buckets"]} for p in value["aggregations"]["pages"]["buckets"]}}, record)


def verify_original(client, base, query):
    response = client.get(base + "/api/v1/source-snapshots/" + quote(query["snapshot_id"], safe=""))
    if response.status_code != 200:
        raise ValueError("Original source is not currently readable")
    source = response.json()
    if (source["source_id"] != query["source_id"] or source["source_revision"] != query["commit"] or source["path"] != query["path"]
        or hashlib.sha256(source["text"].encode()).hexdigest() != query["sha256"]):
        raise ValueError("Original immutable source identity mismatch")
    lines = source["text"].split("\n")
    selected = "\n".join(lines[query["start_line"] - 1:query["end_line"]])
    if not 1 <= query["start_line"] <= query["end_line"] <= len(lines) or query["query"] not in selected:
        raise ValueError("Original citation lines do not support the query")


def prepare(root, compose_json, fixture_url, provider_url, es_url, es_index):
    root = root.resolve()
    values = environment(root)
    config = json.loads((root / ".local/test-env.json").read_text())
    project = values.get("COMPOSE_PROJECT_NAME", "")
    images = check_compose(root, project, json.loads(compose_json.read_text()))
    if not re.fullmatch(r"knowledge-performance-[a-z0-9]{8,32}", es_index) or values["PUBLIC_WEB_URL"] != config["public_url"]:
        raise ValueError("Fresh performance origin/index required")
    for url in (fixture_url, provider_url, es_url):
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("Expected private fixture service origin")
    target = root / ".local/performance.json"
    if target.exists():
        raise ValueError("Preparation receipt already exists; never overwrite pending identities")
    for name in ("ingest", "retrieval"):
        if not config.get(name + "_principal_id"):
            raise ValueError("Provision worker identities without content grants first")
    record = {"project": project, "public_url": config["public_url"], "images": images, "state": "preparing",
              "es_url": es_url, "es_index": es_index, "fixture_url": fixture_url, "provider_url": provider_url,
              "retrieval_metrics_url": "http://retrieval:9090/metrics", "llm_metrics_url": "http://llm:9090/metrics",
              "models": {}, "actors": [], "sources": {}, "pages": {}, "queries": [], "pending": None,
              "gateway_limits": {k: values.get(k, default) for k, default in
                (("GATEWAY_PEER_LIMIT", "600"), ("GATEWAY_IDP_LIMIT", "120"), ("GATEWAY_PRINCIPAL_LIMIT", "300"), ("GATEWAY_LIMIT_WINDOW", "1m"))}}
    api = API(root, config)
    try:
        if api.call("GET", "/sources?limit=1")["items"] or api.call("GET", "/pages?limit=1")["items"]:
            raise ValueError("Performance deployment contains prior content")
        with httpx.Client(timeout=10, trust_env=False) as internal:
            if index_state(internal, record) != 0:
                raise ValueError("Performance physical index is not empty")
            manifest = internal.get(fixture_url + "/manifest.json")
            if manifest.status_code != 200 or manifest.json()["kind"] != "public_performance_three_language_v1":
                raise ValueError("Wrong public fixture provider")
            record["fixture_digest"] = manifest.json()["digest"]
        private_json(target, record)
        for capability in ("embedding", "rerank"):
            record["pending"] = "create_model_" + capability
            private_json(target, record)
            body = {"name": "Performance protocol " + capability, "provider": "openai" if capability == "embedding" else "dashscope_rerank_compatible",
                    "provider_model": "protocol-fixture-" + capability, "base_url": provider_url + "/v1", "capability": capability,
                    "max_input_chars": 8192 if capability == "embedding" else 65536, "max_batch_size": 10 if capability == "embedding" else 64,
                    "timeout_seconds": 10, "max_retries": 0}
            if capability == "embedding": body.update(dimensions=8, request_dimensions=True)
            model = api.call("POST", "/models", body, 201)
            record["models"][capability] = {"id": model["id"], "configuration_id": model["configuration_id"]}
            record["pending"] = None
            private_json(target, record)
            tested = api.call("POST", "/models/" + model["id"] + "/test", {"configuration_id": model["configuration_id"]})
            if tested["test_state"] != "passed": raise ValueError("Protocol model capability failed")
        for number in range(100):
            username = f"perf-{project.removeprefix('sunny-perf-')}-{number:03d}"
            password = secrets.token_urlsafe(32)
            record["pending"] = {"account_number": number, "username": username, "password": password}
            private_json(target, record)
            created = api.keycloak("POST", "/users", {"username": username, "enabled": True, "emailVerified": True,
                "email": username + "@example.invalid", "firstName": "Public", "lastName": "Performance",
                "credentials": [{"type": "password", "value": password, "temporary": False}]}, 201)
            identity = str(UUID(created.headers["Location"].rstrip("/").rsplit("/", 1)[-1]))
            actor = api.call("POST", "/users", {"id": identity, "name": username, "email": username + "@example.invalid"}, 201)
            if actor["permissions"] or actor["id"] != identity or actor["account_kind"] != "user":
                raise ValueError("Fixture account is not an ordinary independent user")
            record["actors"].append({"id": identity, "username": username, "password": password})
            record["pending"] = None
            private_json(target, record)
        record["state"] = "prepared"
        private_json(target, record)
        env = {"RETRIEVAL_ES_INDEX": es_index, "RETRIEVAL_EMBEDDING_CONFIGURATION_ID": record["models"]["embedding"]["configuration_id"],
               "RETRIEVAL_EMBEDDING_DIMENSIONS": "8", "RETRIEVAL_RERANK_CONFIGURATION_ID": record["models"]["rerank"]["configuration_id"]}
        # Contains only index/configuration IDs, still kept with private setup receipts.
        path = root / ".local/performance.env"
        path.write_text("\n".join(k + "=" + v for k, v in env.items()) + "\n")
        path.chmod(0o600)
    finally:
        api.close()


def seed(root, *, timeout=7200, minimum_fragments=100000):
    root, config, record = checked_root(root)
    if record["state"] not in {"prepared", "seeding"} or record.get("pending"):
        raise ValueError("Preparation incomplete or prior write outcome unknown; reconcile manually")
    target = root / ".local/performance.json"
    values, api = environment(root), API(root, config)
    try:
        with httpx.Client(timeout=30, trust_env=False) as internal:
            response = internal.get(record["fixture_url"] + "/manifest.json")
            if response.status_code != 200: raise ValueError("Fixture unavailable")
            manifest = response.json()
            if manifest["digest"] != record["fixture_digest"]: raise ValueError("Fixture changed")
            if "space_id" not in record:
                record["pending"] = "create_space"
                private_json(target, record)
                record["space_id"] = api.call("POST", "/spaces", {"name": "Public performance corpus"}, 201)["id"]
                record["pending"] = None
            record["state"] = "seeding"
            private_json(target, record)
            admin = api.call("GET", "/me")["id"]
            readers = ["user:" + a["id"] for a in record["actors"]] + ["user:" + admin]
            readers += ["service:" + config[s + "_principal_id"] for s in ("ingest", "retrieval")]
            api.call("PUT", "/grants", {"space_id": record["space_id"], "action": "read", "subjects": readers})
            api.call("PUT", "/grants", {"space_id": record["space_id"], "action": "write", "subjects": ["user:" + admin, "service:" + config["ingest_principal_id"]]})
            for repo in manifest["repositories"]:
                if repo["name"] in record["sources"] and record["sources"][repo["name"]].get("complete"):
                    continue
                record["pending"] = {"source": repo["name"], "operation": "create"}
                private_json(target, record)
                source = api.call("POST", "/sources", {"name": repo["name"], "kind": "git", "space_id": record["space_id"],
                    "config": {"url": record["fixture_url"] + repo["path"], "ref": repo["ref"]}}, 201)
                record["sources"][repo["name"]] = {"id": source["id"], "resource_id": source["resource_id"], "complete": False}
                record["pending"] = {"source": repo["name"], "operation": "sync"}
                private_json(target, record)
                path = "/sources/" + quote(source["id"], safe="")
                preview = api.call("POST", path + "/preview", {"base_version": 1})
                if preview["source_revision"] != repo["commit"] or {f["path"]: f["sha256"] for f in preview["files"]} != repo["files"]:
                    raise ValueError("Git snapshot does not match deterministic fixture")
                # Verify real stored bytes, addressed by public fixture hashes only.
                import boto3
                from knowledge_platform.ingest.storage import S3Store
                storage = S3Store(boto3.client("s3", endpoint_url="http://seaweedfs:8333", aws_access_key_id=values["S3_ACCESS_KEY"],
                    aws_secret_access_key=values["S3_SECRET_KEY"], region_name="us-east-1"), "knowledge-raw")
                for digest in repo["files"].values():
                    storage.get_bytes("objects/sha256/" + digest[:2] + "/" + digest, digest)
                task = api.call("POST", path + "/sync", {"base_version": 1}, 202)
                deadline = time.monotonic() + timeout
                while task["status"] in {"queued", "running"} and time.monotonic() < deadline:
                    time.sleep(1)
                    task = api.call("GET", "/tasks/" + task["id"])
                if task["status"] != "succeeded":
                    raise ValueError("Deterministic source did not complete automatic canonical publication")
                for item in task["result"]["items"]:
                    if item["status"] != "published": raise ValueError("Unexpected source review or failure")
                    page = api.call("GET", "/pages/" + quote(item["page_id"], safe=""))
                    revision = page["revision"]
                    record["pages"][page["id"]] = revision["id"]
                    for query in repo["queries"]:
                        if item["path"] != query["path"]: continue
                        claim = next(c for c in revision["content"]["claims"] if query["query"] in c["text"])
                        ref = next(r for r in claim["evidence"] if r["path"] == query["path"])
                        if ref["source_revision"] != repo["commit"] or ref["source_id"] != source["id"]:
                            raise ValueError("Wiki claim lost its source revision")
                        record["queries"].append({**query, "source_id": source["id"], "commit": repo["commit"], "space_id": record["space_id"],
                            "page_id": page["id"], "revision_id": revision["id"], "snapshot_id": ref["revision_id"], "sha256": repo["files"][query["path"]],
                            "start_line": ref["start_line"], "end_line": ref["end_line"]})
                record["sources"][repo["name"]]["complete"] = True
                record["pending"] = None
                private_json(target, record)
                print(json.dumps({"completed_sources": len([s for s in record["sources"].values() if s["complete"]]), "total_sources": len(manifest["repositories"])}), flush=True)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    count = index_state(internal, record, require_complete=True)
                    if count >= minimum_fragments: break
                except ValueError:
                    pass
                time.sleep(5)
            else:
                raise ValueError("Physical ES projection has not reached complete fixture coverage and required scale")
            record["indexed_fragments"] = count
            record["minimum_fragments"] = minimum_fragments
            record["state"] = "seeded"
            private_json(target, record)
    finally:
        api.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="operation", required=True)
    setup = sub.add_parser("prepare")
    setup.add_argument("--compose-json", type=Path, required=True)
    setup.add_argument("--fixture-url", default="http://performance-git:8080")
    setup.add_argument("--provider-url", default="http://performance-model:8080")
    setup.add_argument("--es-url", default="http://elasticsearch:9200")
    setup.add_argument("--es-index", required=True)
    ingest = sub.add_parser("seed")
    ingest.add_argument("--timeout", type=int, default=7200)
    ingest.add_argument("--minimum-fragments", type=int, default=100000)
    args = parser.parse_args()
    if args.operation == "prepare":
        prepare(args.root, args.compose_json, args.fixture_url, args.provider_url, args.es_url, args.es_index)
    else:
        seed(args.root, timeout=args.timeout, minimum_fragments=args.minimum_fragments)
    print("Performance stage complete; no credentials printed")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Performance preparation failed safely; private receipt may contain an unresolved operation. No automatic write retry or cleanup performed.") from None
