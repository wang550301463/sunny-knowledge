"""Explicit simulator for the two distinct model request protocols (no quality claims)."""
import json

import httpx


def is_final(body):
    return any(
        message["role"] == "developer" and "FINAL_ANSWER_NDJSON_V1" in (message.get("content") or "")
        for message in body["messages"]
    )


def answer_lines(answer):
    blocks = []
    for section, kind in [("facts", "fact"), ("inferences", "inference"), ("gaps", "gap")]:
        for claim in answer[section]:
            blocks.append({"kind": kind, **({"text": claim, "citation_ids": []} if kind == "gap" else claim)})
    return "".join(json.dumps(block, ensure_ascii=False) + "\n" for block in blocks) + '{"done":true}\n'


def model_response(body, *, calls=None, answer=None, content=None, usage=3):
    if answer is not None:
        content = answer_lines(answer) if is_final(body) else '{"ready":true}'
    value = {
        "type": "completed", "configuration_id": body["configuration_id"],
        "invocation_id": "test-invocation", "content": content,
        "tool_calls": calls or [], "finish_reason": "tool_calls" if calls else "stop",
        "usage": {"total_tokens": usage},
    }
    # Final fixtures deliberately emit actual deltas before the terminal event. Planning
    # fixtures remain completed-only, ensuring their narrative can never become a block.
    frames = []
    if is_final(body) and content is not None:
        for part in content.splitlines(keepends=True):
            frames.append("event: content_delta\ndata: " + json.dumps({"type": "content_delta", "delta": part}) + "\n\n")
    frames.append("event: completed\ndata: " + json.dumps(value) + "\n\n")
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text="".join(frames))