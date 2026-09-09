"""Native response declarations preserve the existing JSON boundary exactly."""

from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder


async def serialized(model, payload):
    app = FastAPI()

    @app.get("/value", response_model=model, response_model_exclude_unset=True)
    def value():
        return payload

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/value")
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["Page", "Revision", "Proposal", "SourceSnapshot"])
async def test_sql_wire_schema_is_derived_from_all_actual_serialized_columns(kind):
    from knowledge_platform.knowledge import models, responses
    from knowledge_platform.knowledge.service import serialize

    row_type = getattr(models, kind)
    output_type = getattr(responses, {"Page": "PageRecord", "Revision": "KnowledgeRevision", "Proposal": "RevisionProposal", "SourceSnapshot": "SourceSnapshotView"}[kind])
    values = {}
    for column in row_type.__table__.columns:
        if column.nullable:
            values[column.name] = None
        elif column.name == "content":
            values[column.name] = {"title": "old shape", "markdown": "body", "claims": [], "evidence": []}
        elif column.name in {"access_dependencies", "input_revisions"}:
            values[column.name] = []
        elif column.type.python_type is datetime:
            values[column.name] = datetime(2026, 9, 8, 1, 2, 3, tzinfo=UTC)
        elif column.type.python_type is int:
            values[column.name] = 1
        else:
            values[column.name] = "value"
    raw = serialize(row_type(**values))
    assert set(output_type.model_fields) == set(raw)
    # Extra metadata and legacy nested missing defaults must not disappear or be filled.
    raw["future_metadata"] = {"enabled": False, "items": []}
    assert await serialized(output_type, raw) == jsonable_encoder(raw)


@pytest.mark.asyncio
async def test_hidden_run_response_does_not_add_content_or_null_placeholders():
    from knowledge_platform.agent.responses import AgentRunView

    raw = {"id": "r", "session_id": "s", "status": "completed", "event_seq": 8,
           "created_at": "2026-09-08T01:02:03+00:00", "finished_at": None, "content_hidden": True}
    assert await serialized(AgentRunView, raw) == raw


@pytest.mark.asyncio
async def test_config_response_retains_live_filtered_empty_scope_without_reapplying_create_rules():
    from knowledge_platform.agent.responses import AgentDefinition
    from knowledge_platform.agent.schemas import AgentConfig

    config = AgentConfig(space_ids=["restricted"]).model_dump(mode="json")
    config["space_ids"] = []
    raw = {"id": "a", "configuration_id": "c", "version": 1, "name": "name", "description": "",
           "owner_space_id": "manage", "created_by": "u", "shared": True,
           "published_configuration_id": "c", "config": config, "created_at": "2026-09-08T01:02:03+00:00"}
    assert await serialized(AgentDefinition, raw) == raw


@pytest.mark.asyncio
async def test_answer_block_event_has_typed_body_and_preserves_its_exact_fields():
    from knowledge_platform.agent.responses import RunEvent

    event = {"seq": 4, "type": "answer_block", "data": {"index": 0, "kind": "fact", "text": "Evidence", "citation_ids": ["c1"]}}
    assert await serialized(RunEvent, event) == event
    with pytest.raises(ValueError):
        RunEvent.model_validate({**event, "data": {"index": 0, "kind": "fact"}})
