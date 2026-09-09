"""Independent review counterexamples, retained against real isolated Agent PostgreSQL."""
import json
from copy import deepcopy
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from knowledge_platform.agent.models import Run, Summary, now

from .test_boundaries import call, model_response
from .test_runtime import setup_run


async def test_legacy_summary_rechecks_run_scope_even_without_any_citation(api):
    _, session, result, _ = await setup_run(api)

    async def model(request, body):
        return model_response(body, answer={"facts": [], "inferences": [], "gaps": ["PRIVATE_QUESTION_CONTEXT"]})

    api.state.chat_handler = model
    await api.app.state.worker.run_once()
    path = "/api/v1/sessions/" + session["id"] + "/summaries"
    created = await api.client.post(path, json={})
    assert created.status_code == 201
    async with api.database.session() as db:
        row = await db.get(Summary, created.json()["id"])
        assert row.dependencies == []  # Existing schema must remain protected without new defaults.
        assert row.run_ids == [result.json()["id"]]
    api.state.denied.add(("read", "engineering", None))
    api.state.epoch += 1
    assert (await api.client.get("/api/v1/runs/" + result.json()["id"])).json()["content_hidden"]
    response = await api.client.get(path)
    assert response.json()["items"][0]["content_hidden"] is True
    assert "PRIVATE_QUESTION_CONTEXT" not in response.text


async def test_summary_source_run_lookup_failure_is_fail_closed(api):
    _, session, _, _ = await setup_run(api)
    await api.app.state.worker.run_once()
    path = "/api/v1/sessions/" + session["id"] + "/summaries"
    assert (await api.client.post(path, json={})).status_code == 201
    async with api.database.session() as db, db.begin():
        row = await db.scalar(select(Summary).where(Summary.session_id == session["id"]))
        db.add(Summary(session_id=row.session_id, version=2, run_ids=["missing-run"], content=deepcopy(row.content), dependencies=[], source_hash="legacy-corruption"))
    response = await api.client.get(path)
    assert response.status_code == 503
    assert "Pay calls Ledger" not in response.text


@pytest.mark.parametrize("revision_time", ["2026-09-01T00:00:00Z", None, "2020-01-01", "not-a-time"])
async def test_get_cannot_send_post_cutoff_or_unproved_revision_to_model(api, revision_time):
    _, _, old, body = await setup_run(api)
    await api.client.post("/api/v1/runs/" + old.json()["id"] + "/cancel", json={})
    body.update({"known_at": "2020-01-01T00:00:00Z", "idempotency_key": "historical"})
    created = await api.client.post("/api/v1/runs", json=body)
    assert created.status_code == 201

    async def upstream(request, payload):
        if request.url.path.endswith("/revisions/future-revision"):
            revision = {"id": "future-revision", "page_id": "page:pay", "number": 1,
                "content": {"title": "Future", "markdown": "KNOWN_ONLY_IN_2026", "entity_type": "Module", "state": "valid", "claims": [], "evidence": [api.reference]}}
            if revision_time is not None:
                revision["created_at"] = revision_time
            return httpx.Response(200, json=revision)

    async def model(request, payload):
        if not any(m["role"] == "tool" for m in payload["messages"]):
            return model_response(payload, calls=[call("future", "get", {"space_id": "engineering", "page_id": "page:pay", "revision_id": "future-revision"})])
        return model_response(payload, answer={"facts": [], "inferences": [], "gaps": ["Unacceptable future context"]})

    api.state.handler, api.state.chat_handler = upstream, model
    await api.app.state.worker.run_once()
    assert "KNOWN_ONLY_IN_2026" not in json.dumps(api.state.chat_calls)
    value = (await api.client.get("/api/v1/runs/" + created.json()["id"])).json()
    assert value["status"] == "failed"
    assert value["error_code"] in {"knowledge_after_cutoff", "knowledge_time_unavailable"}


async def test_later_gap_only_history_cannot_enter_an_earlier_known_at_run(api):
    _, _, first, body = await setup_run(api)

    async def first_model(request, payload):
        return model_response(payload, answer={"facts": [], "inferences": [], "gaps": ["FUTURE_GAP_ONLY_CONTEXT"]})

    api.state.chat_handler = first_model
    await api.app.state.worker.run_once()
    response = await api.client.post("/api/v1/runs", json={**body, "known_at": "2020-01-01T00:00:00Z", "idempotency_key": "historical"})
    assert response.status_code == 201

    async def second_model(request, payload):
        return model_response(payload, answer={"facts": [], "inferences": [], "gaps": ["No historical evidence."]})

    api.state.chat_handler = second_model
    await api.app.state.worker.run_once()
    assert "FUTURE_GAP_ONLY_CONTEXT" not in json.dumps(api.state.chat_calls[-1]["messages"])