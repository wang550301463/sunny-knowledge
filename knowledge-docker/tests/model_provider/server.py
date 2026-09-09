"""Explicit deterministic protocol fixture. It provides no model-quality evidence."""

import hashlib
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class State:
    lock = threading.Lock()
    embedding_requests = 0
    rerank_requests = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Test prompts, authorization headers and URLs are never logged.

    def reply(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self.reply(200, {"status": "protocol_fixture"})
        elif self.path == "/stats":
            with State.lock:
                self.reply(200, {"embedding_requests": State.embedding_requests, "rerank_requests": State.rerank_requests})
        else:
            self.reply(404, {"error": "unknown_fixture_route"})

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1_000_000:
                raise ValueError
            body = json.loads(self.rfile.read(size))
            if self.path == "/v1/embeddings":
                if body["model"] != "protocol-fixture-embedding" or body.get("dimensions") != 8:
                    raise ValueError
                inputs = body["input"]
                if not isinstance(inputs, list) or not 1 <= len(inputs) <= 10:
                    raise ValueError
                if any(not isinstance(item, str) for item in inputs) or sum(map(len, inputs)) > 8192:
                    raise ValueError
                vectors = []
                for index, value in enumerate(inputs):
                    raw = [byte + 1.0 for byte in hashlib.sha256(value.encode()).digest()[:8]]
                    norm = math.sqrt(sum(v * v for v in raw))
                    vectors.append({"object": "embedding", "index": index, "embedding": [v / norm for v in raw]})
                with State.lock:
                    State.embedding_requests += 1
                self.reply(200, {"object": "list", "data": vectors, "model": body["model"], "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)}})
            elif self.path == "/v1/reranks":
                if body["model"] != "protocol-fixture-rerank":
                    raise ValueError
                documents, query = body["documents"], body["query"]
                if not isinstance(query, str) or not isinstance(documents, list) or not 1 <= len(documents) <= 64:
                    raise ValueError
                if any(not isinstance(item, str) for item in documents) or sum(map(len, documents)) + len(query) > 65536:
                    raise ValueError
                results = [{"index": index, "relevance_score": 0.9 if query in value else 0.1} for index, value in enumerate(documents)]
                results.sort(key=lambda row: (-row["relevance_score"], row["index"]))
                with State.lock:
                    State.rerank_requests += 1
                self.reply(200, {"results": results[:body.get("top_n", len(results))], "usage": {"total_tokens": len(documents)}})
            else:
                self.reply(404, {"error": "unknown_fixture_route"})
        except (KeyError, TypeError, ValueError):
            self.reply(422, {"error": "invalid_fixture_protocol"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
