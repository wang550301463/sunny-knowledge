"""Owned PostgreSQL workflows preserve full and redacted native HTTP branches."""

from copy import deepcopy
from functools import wraps

import httpx
import pytest
from fastapi.encoders import jsonable_encoder

from knowledge_platform.agent.service import AgentService

from .test_channel_runtime import channel, create_channel_run  # noqa: F401
from .test_runtime import setup_run


@pytest.fixture
def returned(monkeypatch):
    values = {}
    for name in ("summary_view", "history", "list_sessions", "get_citation"):
        original = getattr(AgentService, name)

        def wrap(name, original):
            @wraps(original)
            async def call(*args, **kwargs):
                result = await original(*args, **kwargs)
                values[name] = jsonable_encoder(deepcopy(result))
                return result
            return call

        monkeypatch.setattr(AgentService, name, wrap(name, original))
    return values


async def test_summary_and_history_match_native_visible_and_revoked_output(api, returned):
    _, session, result, _ = await setup_run(api)
    await api.app.state.worker.run_once()
    path = "/api/v1/sessions/" + session["id"]
    response = await api.client.get("/api/v1/sessions")
    assert response.status_code == 200, response.text
    assert response.json() == returned["list_sessions"]
    summary = await api.client.post(path + "/summaries", json={})
    assert summary.status_code == 201, summary.text
    assert summary.json() == returned["summary_view"]
    assert summary.json()["run_ids"] == [result.json()["id"]]
    for revoked in (False, True):
        api.state.source_allowed = not revoked
        api.state.epoch += 1
        summaries = await api.client.get(path + "/summaries")
        assert summaries.status_code == 200, summaries.text
        assert summaries.json() == {"items": [returned["summary_view"]]}
        item = summaries.json()["items"][0]
        assert item["content_hidden"] is revoked
        if revoked:
            assert set(item) == {"id", "version", "created_at", "independent_evidence_count", "content_hidden"}
        history = await api.client.get(path + "/history")
        assert history.status_code == 200, history.text
        assert history.json() == returned["history"]
        if revoked:
            assert not {"question", "answer", "citations", "actual_scope"} & history.json()["items"][0].keys()
    clear = await api.client.delete(path)
    assert clear.status_code == 200 and clear.json() == {"id": session["id"], "cleared": True}


async def test_citation_exact_selection_remains_native_and_live_authorized(api, returned):
    _, _, result, _ = await setup_run(api)
    await api.app.state.worker.run_once()
    run = (await api.client.get("/api/v1/runs/" + result.json()["id"])).json()
    evidence = run["citations"][0]["evidence"]
    source = {**evidence, "id": evidence["revision_id"], "space_id": "engineering", "text": "Pay calls Ledger", "sha256": "a" * 64, "object_key": "INTERNAL-OBJECT-KEY", "registered_by": "ingest"}

    async def upstream(request, body):
        if request.url.path.startswith("/api/v1/source-snapshots/"):
            return httpx.Response(200, json=source)

    api.state.handler = upstream
    response = await api.client.get(f"/api/v1/runs/{run['id']}/citations/{run['citations'][0]['id']}")
    assert response.status_code == 200, response.text
    assert response.json() == returned["get_citation"]
    assert set(response.json()) == {"id", "resource_id", "space_id", "source_id", "source_revision", "path", "kind", "text", "sha256"}
    api.state.source_allowed = False
    response = await api.client.get(f"/api/v1/runs/{run['id']}/citations/{run['citations'][0]['id']}")
    assert response.status_code == 403 and "INTERNAL-OBJECT-KEY" not in response.text


async def test_channel_control_absent_run_is_not_filled_and_cancel_run_is_native(api, channel):
    response = await api.client.post("/internal/v1/channel/conversation/cancel", headers=channel.headers(), json={})
    assert response.status_code == 200 and response.json() == {"cancelled": False}
    run = await create_channel_run(api, channel)
    response = await api.client.post(f"/internal/v1/channel/runs/{run['id']}/cancel", headers=channel.headers(), json={})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    response = await api.client.post("/internal/v1/channel/conversation/clear", headers=channel.headers(), json={})
    assert response.status_code == 200 and response.json() == {"cleared": True}