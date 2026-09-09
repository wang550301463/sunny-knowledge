import json

import httpx
import pytest
from sqlalchemy import select

from knowledge_platform.agent.models import Event, Run

from .model_protocol import answer_lines, is_final, model_response
from .test_boundaries import call
from .test_runtime import setup_run


async def test_completed_only_provider_cannot_fabricate_incremental_answer_events(api):
    async def model(request, body):
        content = answer_lines({"facts": [], "inferences": [], "gaps": ["NEVER_STREAMED"]}) if is_final(body) else '{"ready":true}'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text="event: completed\ndata: " + json.dumps({"type": "completed", "configuration_id": "model1", "content": content, "tool_calls": [], "usage": {}, "finish_reason": "stop"}) + "\n\n")

    api.state.chat_handler = model
    _, _, result, _ = await setup_run(api)
    await api.app.state.worker.run_once()
    value = (await api.client.get("/api/v1/runs/" + result.json()["id"])).json()
    assert value["status"] == "failed" and value["answer"] is None
    assert value["error_code"] == "invalid_final_model_stream"
    async with api.database.session() as session:
        events = list(await session.scalars(select(Event).where(Event.run_id == value["id"])))
        assert not any(e.type == "answer_block" for e in events)
        assert "NEVER_STREAMED" not in json.dumps([e.data for e in events])


async def test_planning_answer_or_hidden_narrative_never_becomes_public_content(api):
    async def model(request, body):
        assert not is_final(body)
        return model_response(body, content='{"facts":[],"inferences":[],"gaps":["PLANNING_SECRET"]}')

    api.state.chat_handler = model
    _, _, result, _ = await setup_run(api)
    await api.app.state.worker.run_once()
    value = (await api.client.get("/api/v1/runs/" + result.json()["id"])).json()
    assert value["status"] == "failed" and value["error_code"] == "invalid_model_decision"
    async with api.database.session() as session:
        row = await session.get(Run, value["id"])
        events = list(await session.scalars(select(Event).where(Event.run_id == value["id"])))
        assert "PLANNING_SECRET" not in json.dumps([row.answer, row.checkpoint, [e.data for e in events]])
    assert len(api.state.chat_calls) == 1


@pytest.mark.parametrize("budget,expected_rounds,expected_tools,code", [
    ({"model_rounds": 1}, 1, 0, "budget_reached"),
    ({"model_rounds": 2}, 2, 1, "budget_reached"),
    ({"model_rounds": 8}, 8, 7, "budget_reached"),
    ({"model_rounds": 8, "tool_calls": 2}, 3, 2, "tool_budget_reached"),
])
async def test_final_call_is_reserved_within_total_decision_and_tool_budgets(api, budget, expected_rounds, expected_tools, code):
    decisions, finals = 0, 0

    async def model(request, body):
        nonlocal decisions, finals
        if is_final(body):
            finals += 1
            assert body["tools"] == []
            return model_response(body, answer={"facts": [], "inferences": [], "gaps": ["Evidence remains incomplete."]})
        decisions += 1
        return model_response(body, calls=[call("decision" + str(decisions), "search", {"space_ids": ["engineering"], "query": "q"})], content="DO_NOT_PERSIST_PLANNING")

    api.state.chat_handler = model
    _, _, result, _ = await setup_run(api, config={"space_ids": ["engineering"], "model_configuration_id": "model1", "budget": budget})
    await api.app.state.worker.run_once()
    value = (await api.client.get("/api/v1/runs/" + result.json()["id"])).json()
    assert value["status"] == "partial" and value["error_code"] == code
    assert value["rounds"] == expected_rounds and value["tool_calls"] == expected_tools
    assert len(api.state.chat_calls) == expected_rounds and finals == 1
    assert not value["answer_complete"]
    assert all("DO_NOT_PERSIST_PLANNING" not in json.dumps(body["messages"]) for body in api.state.chat_calls)
    export = await api.client.get("/api/v1/runs/" + value["id"] + "/export")
    assert export.status_code == 200 and "回答未完成" in export.text


async def test_too_many_planned_tools_are_not_run_and_final_still_uses_reserved_round(api):
    async def model(request, body):
        if is_final(body):
            return model_response(body, answer={"facts": [], "inferences": [], "gaps": ["Tool budget reached."]})
        return model_response(body, calls=[call(str(i), "search", {"space_ids": ["engineering"], "query": "q"}) for i in range(3)])

    api.state.chat_handler = model
    _, _, result, _ = await setup_run(api, config={"space_ids": ["engineering"], "model_configuration_id": "model1", "budget": {"tool_calls": 2}})
    await api.app.state.worker.run_once()
    value = (await api.client.get("/api/v1/runs/" + result.json()["id"])).json()
    assert value["rounds"] == 2 and value["tool_calls"] == 0
    assert value["status"] == "partial" and value["error_code"] == "tool_budget_reached"
    assert not any(name == "retrieval" for name, *_ in api.state.calls)