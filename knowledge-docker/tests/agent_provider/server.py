"""Deterministic OpenAI protocol fixture. Never used as a production model fallback."""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def model_message(body):
    messages = body["messages"]
    tools = {t["function"]["name"] for t in body.get("tools", [])}
    if "probe" in tools:
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": "probe-call", "type": "function", "function": {
                "name": "probe", "arguments": json.dumps({"value": "OK"}),
            },
        }]}
    question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    if question.startswith("FIXTURE_GET "):
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if not results:
            if "get" not in tools:
                raise ValueError("Test requires the real internal get tool")
            args = json.loads(question.removeprefix("FIXTURE_GET "))
            if set(args) != {"space_id", "page_id", "revision_id"}:
                raise ValueError("Invalid fixture target")
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": "get-original", "type": "function", "function": {
                    "name": "get", "arguments": json.dumps(args),
                },
            }]}
        source = results[-1]
        citation = source["citations"][0]
        answer = {"facts": [{"text": citation["excerpt"], "citation_ids": [citation["id"]]}],
                  "inferences": [], "gaps": ["协议模拟结果，不代表真实模型质量评测。"]}
        return {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False), "tool_calls": []}
    return {"role": "assistant", "content": "OK", "tool_calls": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(200 if self.path == "/healthz" else 404,
                   {"status": "deterministic_protocol_simulation"})

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/chat/completions" or not 0 < size <= 1_000_000:
                raise ValueError
            body = json.loads(self.rfile.read(size))
            if body["model"] != "protocol-fixture-chat":
                raise ValueError
            message = model_message(body)
            finish = "tool_calls" if message["tool_calls"] else "stop"
            usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
            if not body.get("stream"):
                self.reply(200, {"id": "fixture-chat", "choices": [
                    {"index": 0, "message": message, "finish_reason": finish}], "usage": usage})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def frame(value):
                self.wfile.write(("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()

            if message["tool_calls"]:
                deltas = [{"tool_calls": [{"index": index, **call} for index, call in enumerate(message["tool_calls"])]}]
            else:
                content = message["content"]
                deltas = [{"content": content[n:n+37]} for n in range(0, len(content), 37)]
            for delta in deltas:
                frame({"id": "fixture-chat", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
                time.sleep(0.005)
            frame({"id": "fixture-chat", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
            frame({"id": "fixture-chat", "choices": [], "usage": usage})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (KeyError, TypeError, ValueError):
            self.reply(422, {"error": "invalid_fixture_protocol"})
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
