"""Dimensioned deterministic HTTP protocol fixture. No semantic-model quality claim."""

import argparse
import hashlib
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def server(*, dimensions=1024, host="0.0.0.0", port=8080):
    if type(dimensions) is not int or not 8 <= dimensions <= 4096:
        raise ValueError("Invalid fixture dimensions")
    lock = threading.Lock()
    permits = threading.BoundedSemaphore(20)
    state = {"embedding_requests": 0, "rerank_requests": 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, code, value):
            payload = json.dumps(value, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def do_GET(self):
            if self.path not in {"/healthz", "/stats"}:
                self.reply(404, {"error": "unknown_fixture_route"})
                return
            with lock:
                self.reply(200, {"kind": "deterministic_performance_protocol", "dimensions": dimensions, **state})
        def do_POST(self):
            if self.headers.get("Authorization") or self.headers.get("Transfer-Encoding"):
                self.reply(400, {"error": "fixture_accepts_no_credentials"})
                return
            if not permits.acquire(blocking=False):
                self.reply(429, {"error": "fixture_capacity"})
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or not 0 < int(lengths[0]) <= 1_000_000:
                    raise ValueError
                self.connection.settimeout(10)
                data = self.rfile.read(int(lengths[0]))
                if len(data) != int(lengths[0]): raise ValueError
                body = json.loads(data)
                if self.path == "/v1/embeddings":
                    if body["model"] != "protocol-performance-embedding" or body["dimensions"] != dimensions:
                        raise ValueError
                    inputs = body["input"]
                    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 10 or any(not isinstance(v, str) for v in inputs) or sum(map(len, inputs)) > 8192:
                        raise ValueError
                    output = []
                    for number, value in enumerate(inputs):
                        raw = [v - 127.5 for v in hashlib.shake_256(value.encode()).digest(dimensions)]
                        norm = math.sqrt(sum(v*v for v in raw))
                        output.append({"object": "embedding", "index": number, "embedding": [v / norm for v in raw]})
                    with lock: state["embedding_requests"] += 1
                    self.reply(200, {"object": "list", "model": body["model"], "data": output, "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)}})
                elif self.path == "/v1/reranks":
                    if body["model"] != "protocol-performance-rerank": raise ValueError
                    documents, query = body["documents"], body["query"]
                    if not isinstance(documents, list) or not 1 <= len(documents) <= 64 or not isinstance(query, str) or any(not isinstance(v, str) for v in documents) or sum(map(len, documents)) + len(query) > 65536:
                        raise ValueError
                    top = body.get("top_n", len(documents))
                    if type(top) is not int or not 1 <= top <= len(documents): raise ValueError
                    output = [{"index": i, "relevance_score": 0.9 if query in value else 0.1} for i, value in enumerate(documents)]
                    output.sort(key=lambda row: (-row["relevance_score"], row["index"]))
                    with lock: state["rerank_requests"] += 1
                    self.reply(200, {"results": output[:top], "usage": {"total_tokens": len(documents)}})
                else:
                    self.reply(404, {"error": "unknown_fixture_route"})
            except (ValueError, TypeError, KeyError, OSError):
                self.reply(422, {"error": "invalid_fixture_protocol"})
            finally:
                permits.release()
    instance = ThreadingHTTPServer((host, port), Handler)
    instance.socket.listen(64)
    return instance


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dimensions", type=int, default=1024)
    args = parser.parse_args()
    server(dimensions=args.dimensions).serve_forever()
