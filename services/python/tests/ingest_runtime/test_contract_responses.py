"""Native ingest/source serializers pass real PG and both domain HTTP boundaries."""

import httpx
from fastapi.encoders import jsonable_encoder

from knowledge_platform.ingest.app import create_app
from knowledge_platform.ingest.config import IngestSettings
from knowledge_platform.ingest.models import Step
from knowledge_platform.ingest.service import IngestService

from .test_fencing import knowledge_http
from .test_pipeline import finish, setup_pipeline
from .test_postgres import Machine


async def test_real_pg_source_preview_task_and_canonical_conflict_proposal_preserve_wire_shapes(store, security):
    source, task, pipeline, _ = await setup_pipeline(store)
    db, auth, box = store
    async with knowledge_http(store, security) as internal:
        pipeline.internal = internal
        await finish(pipeline, task["id"])
        async with db.session() as session, session.begin():
            step = await session.get(Step, (task["id"], 0))
            step.result = {"conflict_available": True, "error_code": "review_base_changed"}
        app = create_app(database=db, authorizer=auth, security=security["ingest"], machine=Machine(), internal=internal, secret_box=box, storage=pipeline.storage, settings=IngestSettings(), initialize_schema=False)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ingest") as client:
            headers = {"X-Service-Token": security["gateway"].issue("ingest"), "Authorization": "Bearer alice"}
            async with db.session() as session, session.begin():
                service = IngestService(session, auth, Machine(), internal, box)
                expected_source = await service.get("alice", source["id"])
                expected_task = await service.get_task("alice", task["id"])
                expected_conflict = await service.get_conflict("alice", task["id"], 0)
            for path, expected in [("/api/v1/sources/" + source["id"], expected_source), ("/api/v1/tasks/" + task["id"], expected_task), (f"/api/v1/tasks/{task['id']}/conflicts/0", expected_conflict)]:
                response = await client.get(path, headers=headers)
                assert response.status_code == 200, response.text
                assert response.json() == jsonable_encoder(expected)
            listed = await client.get("/api/v1/sources", headers=headers)
            assert listed.status_code == 200 and listed.json() == {"items": [expected_source], "next_cursor": None}
            tasks = await client.get("/api/v1/tasks", headers=headers)
            assert tasks.status_code == 200 and tasks.json() == {"items": [expected_task], "next_cursor": None}
            preview = await client.post(f"/api/v1/sources/{source['id']}/preview", headers=headers, json={"base_version": 1})
            assert preview.status_code == 200 and preview.json()["files"]
            assert "object_key" not in str(preview.json())
            proposed = await client.post(f"/api/v1/tasks/{task['id']}/conflicts/0/propose", headers=headers, json={"base_revision": expected_conflict["current_revision"], "reason": "Review frozen source evidence"})
            assert proposed.status_code == 201, proposed.text
            canonical = await internal.request("GET", "/api/v1/reviews/" + proposed.json()["id"], "alice")
            # Review GET adds the target space; proposal POST returns the canonical record.
            assert proposed.json() == {key: value for key, value in canonical.items() if key != "space_id"}
            auth.disabled.add("alice")
            assert (await client.get("/api/v1/tasks/" + task["id"], headers=headers)).status_code == 403