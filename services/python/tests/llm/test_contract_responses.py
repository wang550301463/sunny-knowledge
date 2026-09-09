"""New DTOs exercise native PG model, audit, version, capability and provider boundaries."""

import json

import pytest

from knowledge_platform.llm import responses

from .test_http import create, model_body


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["chat", "embedding", "rerank"])
async def test_native_pg_and_provider_serializers_match_typed_http_metadata_and_inference(api, capability):
    model = await create(api, capability)
    mid, cid = model["id"], model["configuration_id"]
    assert model == await api.app.state.models.get(mid)
    headers = api.headers()
    for path, expected in [
        ("/api/v1/models/" + mid, model),
        ("/api/v1/models", await api.app.state.models.list()),
        (f"/api/v1/models/{mid}/versions", await api.app.state.models.versions(mid)),
    ]:
        response = await api.client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.json() == expected
    tested = await api.client.post(f"/api/v1/models/{mid}/test", headers=headers, json={"configuration_id": cid})
    assert tested.status_code == 200
    assert tested.json()["test_state"] == "passed"
    assert responses.CapabilityTestResult.model_validate(tested.json()).model_dump(mode="json", exclude_unset=True) == tested.json()
    caller = "agent" if capability == "chat" else "retrieval"
    path, body = {
        "chat": ("chat", {"messages": [{"role": "user", "content": "question"}]}),
        "embedding": ("embeddings", {"input": ["question"]}),
        "rerank": ("rerank", {"query": "question", "documents": ["document"], "top_n": 1}),
    }[capability]
    inference = await api.client.post("/internal/v1/" + path, headers=api.headers(caller, actor=False), json={"configuration_id": cid, **body})
    assert inference.status_code == 200
    result = inference.json()
    assert result["configuration_id"] == cid and result["invocation_id"]
    assert "SECRET" not in json.dumps(result)
    if capability == "rerank":
        assert result["results"] == [{"index": 0, "score": 0.8}]
    metadata = await api.client.get("/internal/v1/models/configurations/" + cid, headers=api.headers(caller, actor=False))
    assert metadata.status_code == 200
    assert metadata.json() == await api.app.state.models.configuration_metadata(cid, {capability})
    usage = await api.client.get(f"/api/v1/models/{mid}/usage", headers=headers)
    assert usage.status_code == 200 and usage.json()["items"]
    assert any(item["invocation_id"] == result["invocation_id"] for item in usage.json()["items"])
    audit = await api.client.get(f"/api/v1/models/{mid}/audit", headers=headers)
    assert audit.status_code == 200
    assert "SECRET" not in json.dumps(audit.json())
    updated = await api.client.put(f"/api/v1/models/{mid}", headers=headers, json={"base_configuration_id": cid, "config": {k: v for k, v in model_body(capability).items() if k != "credential"}})
    assert updated.status_code == 200
    changed = updated.json()
    disabled = await api.client.patch(f"/api/v1/models/{mid}/state", headers=headers, json={"base_configuration_id": changed["configuration_id"], "state": "disabled"})
    assert disabled.status_code == 200
    retired = await api.client.delete(f"/api/v1/models/{mid}", headers=headers, params={"base_configuration_id": disabled.json()["configuration_id"]})
    assert retired.status_code == 200
    assert retired.json() == await api.app.state.models.get(mid)
    api.state.permissions = []
    assert (await api.client.get(f"/api/v1/models/{mid}", headers=headers)).status_code == 403