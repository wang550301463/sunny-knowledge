"""Read-only measured search traffic; setup is a separate explicit seed command."""

import argparse
import concurrent.futures
import json
import math
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
from prometheus_client.parser import text_string_to_metric_families

sys.path.insert(0, str(Path(__file__).resolve().parent))


def histogram(text, service, component, operation):
    wanted = {"service": service, "component": component, "operation": operation, "outcome": "success"}
    prefix = "knowledge_domain_duration_seconds_"
    found = {}
    buckets = {}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if not sample.name.startswith(prefix) or not all(sample.labels.get(k) == v for k, v in wanted.items()):
                continue
            kind = sample.name.removeprefix(prefix)
            if kind == "bucket":
                if set(sample.labels) != {*wanted, "le"} or sample.labels["le"] in buckets:
                    raise ValueError("Ambiguous histogram series")
                buckets[sample.labels["le"]] = sample.value
            elif kind in {"count", "sum"}:
                if set(sample.labels) != set(wanted) or kind in found:
                    raise ValueError("Ambiguous histogram series")
                found[kind] = sample.value
    if set(found) != {"count", "sum"} or not buckets:
        raise ValueError("Required successful operation histogram is absent")
    return {**found, "buckets": buckets}


def validate_answer(value, query):
    try:
        evidence = {item["id"]: item for item in value["evidence"]}
        for item in value["items"]:
            if item.get("primary") is not True or query["query"] not in item["text"]:
                continue
            if any(query.get(k) and item.get(k) != query[k] for k in ("page_id", "revision_id")):
                continue
            for cid in item["citation_ids"]:
                source = evidence[cid]
                ref = source["evidence"]
                if (ref["source_id"] == query["source_id"] and ref["source_revision"] == query["commit"]
                    and ref["path"] == query["path"] and query["query"] in source["excerpt"]
                    and type(ref["start_line"]) is int and type(ref["end_line"]) is int
                    and 1 <= ref["start_line"] <= ref["end_line"]):
                    return source
    except (KeyError, TypeError, AttributeError):
        pass
    raise ValueError("Search lacks the expected source-backed primary result")


def load(base_url, actors, queries, *, requests_per_actor=30, timeout=60, client_factory=None):
    if len(actors) != 10 or len({a["id"] for a in actors}) != 10 or len({a["token"] for a in actors}) != 10:
        raise ValueError("Exactly ten independent authenticated actors are required")
    if not queries or type(requests_per_actor) is not int or not 1 <= requests_per_actor <= 10000:
        raise ValueError("Invalid bounded load request")
    barrier, lock = threading.Barrier(10, timeout=30), threading.Lock()
    concurrency = {"active": 0, "peak": 0}
    def worker(number, actor):
        rows = []
        with (client_factory() if client_factory else httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False)) as client:
            client.headers["Authorization"] = "Bearer " + actor["token"]
            barrier.wait()
            for offset in range(requests_per_actor):
                query_number = (number * requests_per_actor + offset) % len(queries)
                query = queries[query_number]
                row = {"actor_number": number, "query_number": query_number, "ok": False,
                       "status": None, "error": None, "retry_after": None}
                with lock:
                    concurrency["active"] += 1
                    concurrency["peak"] = max(concurrency["peak"], concurrency["active"])
                started = time.monotonic()
                try:
                    response = client.post(base_url.rstrip("/") + "/api/v1/search", json={"query": query["query"], "space_ids": [query["space_id"]]})
                    row["status"] = response.status_code
                    after = response.headers.get("Retry-After", "")
                    if after.isascii() and after.isdecimal() and len(after) <= 8:
                        row["retry_after"] = int(after)
                    if response.status_code == 200:
                        validate_answer(response.json(), query)
                        row["ok"] = True
                    else:
                        row["error"] = "http_failure"
                except httpx.TimeoutException:
                    row["error"] = "timeout"
                except httpx.HTTPError:
                    row["error"] = "transport_failure"
                except (ValueError, TypeError):
                    row["error"] = "invalid_evidence_response"
                finally:
                    row["seconds"] = time.monotonic() - started
                    with lock:
                        concurrency["active"] -= 1
                rows.append(row)
        return rows
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(worker, number, actor) for number, actor in enumerate(actors)]
        rows = [row for future in futures for row in future.result()]
    return {"requests": rows, "peak_concurrency": concurrency["peak"],
            "successful_requests": sum(row["ok"] for row in rows),
            "failed_requests": sum(not row["ok"] for row in rows),
            "client_seconds": [row["seconds"] for row in rows if row["ok"]]}


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".performance-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def external_delta(before, after):
    """Preserve each provider histogram delta; never subtract means from percentiles."""
    if before["buckets"].keys() != after["buckets"].keys() or after["count"] < before["count"] or after["sum"] < before["sum"]:
        raise ValueError("External model histogram reset")
    buckets = {k: after["buckets"][k] - v for k, v in before["buckets"].items()}
    if any(v < 0 for v in buckets.values()):
        raise ValueError("External model histogram reset")
    return {"count": after["count"] - before["count"], "sum_seconds": after["sum"] - before["sum"], "buckets": buckets}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--requests-per-actor", type=int, default=30)
    parser.add_argument("--warmup-per-actor", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--exclusive-window", action="store_true", required=True)
    args = parser.parse_args()
    from seed import checked_root, index_state, verify_original
    from measurement import MeasurementError, summarize
    root, config, record = checked_root(args.root)
    if record["state"] != "seeded":
        raise ValueError("A completed seed receipt is required")
    sys.path.insert(0, str(root / "scripts"))
    from oidc_login import http_client, login
    actors = []
    for actor in record["actors"][:10]:
        token = login(config["public_url"], actor["username"], actor["password"], scope="openid profile email knowledge:read")["access_token"]
        actors.append({"id": actor["id"], "token": token})
    factory = lambda: http_client(config["public_url"], timeout=args.timeout, follow_redirects=False)
    with factory() as api:
        api.headers["Authorization"] = "Bearer " + actors[0]["token"]
        # GET original sources before the measured window; all 300 target statements.
        for query in record["queries"]:
            verify_original(api, config["public_url"], query)
    with httpx.Client(timeout=10, trust_env=False) as internal:
        fragments = index_state(internal, record, require_complete=True)
        warmup = load(config["public_url"], actors, record["queries"], requests_per_actor=args.warmup_per_actor, timeout=args.timeout, client_factory=factory)
        def metrics():
            local = internal.get(record["retrieval_metrics_url"])
            provider = internal.get(record["llm_metrics_url"])
            if local.status_code != 200 or provider.status_code != 200:
                raise ValueError("Private telemetry unavailable")
            return histogram(local.text, "retrieval", "retrieval", "search_local"), {
                op: histogram(provider.text, "llm", "model", op) for op in ("embedding", "rerank")}
        before, external_before = metrics()
        measured = load(config["public_url"], actors, record["queries"], requests_per_actor=args.requests_per_actor, timeout=args.timeout, client_factory=factory)
        after, external_after = metrics()
        report = {"accepted": False, "model_kind": "deterministic_protocol_simulation", "fixture_digest": record["fixture_digest"],
                  "source_count": len(record["sources"]), "configured_accounts": len(record["actors"]), "measured_accounts": 10,
                  "warmup": warmup, "measured": measured, "local_before": before, "local_after": after,
                  "external_models": {}, "measurement_error": None}
        try:
            report["summary"] = summarize(before, after, successful_requests=measured["successful_requests"],
                failed_requests=measured["failed_requests"], peak_concurrency=measured["peak_concurrency"],
                fragment_count=fragments, client_seconds=measured["client_seconds"])
            report["external_models"] = {op: external_delta(external_before[op], external_after[op]) for op in external_before}
            report["accepted"] = (report["summary"]["accepted"] and warmup["failed_requests"] == 0
                and len(record["sources"]) == 100 and len(record["actors"]) == 100)
        except (MeasurementError, ValueError):
            report["measurement_error"] = "incoherent_or_incomplete_histogram_window"
        private_json(root / ".local/performance-report.json", report)
        print(json.dumps({"accepted": report["accepted"], "requests": len(measured["requests"]), "failures": measured["failed_requests"], "fragment_count": fragments}))
        return 0 if report["accepted"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, httpx.HTTPError, RuntimeError):
        raise SystemExit("Performance run failed safely; inspect private readiness and evidence receipts") from None
