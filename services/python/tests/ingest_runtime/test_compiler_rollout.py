"""New compiler tasks fence old prepared publication at an unchanged source version."""

from urllib.parse import quote

from sqlalchemy import select

from knowledge_platform.ingest.connectors import RawFile
from knowledge_platform.ingest.models import Source, Step, Task
from knowledge_platform.ingest.service import IngestService

from .test_fencing import knowledge_http
from .test_pipeline import finish, setup_pipeline
from .test_postgres import Machine


async def prepared_old_task(store, monkeypatch):
    import knowledge_platform.ingest.pipeline as pipeline_module
    import knowledge_platform.ingest.service as service_module

    monkeypatch.setattr(service_module, "COMPILER_VERSION", "static-wiki-v2")
    monkeypatch.setattr(pipeline_module, "COMPILER_VERSION", "static-wiki-v2")
    source, old, pipeline, _ = await setup_pipeline(
        store,
        [RawFile("main.go", b'package fixture\nvar Payload = "' + b"x" * 3000 + b'"\n', "code")],
    )
    return source, old, pipeline


async def test_compiler_upgrade_fences_old_unbased_publication_and_is_idempotent(
    store, security, monkeypatch
):
    import knowledge_platform.ingest.pipeline as pipeline_module
    import knowledge_platform.ingest.service as service_module

    source, old, pipeline = await prepared_old_task(store, monkeypatch)
    db, auth, box = store
    async with knowledge_http(store, security) as internal:
        pipeline.internal = internal
        for _ in range(20):
            await pipeline.advance(old["id"])
            async with db.session() as session, session.begin():
                task = await session.get(Task, old["id"])
                if task.stage == "publish":
                    step = await session.scalar(select(Step).where(Step.task_id == task.id))
                    assert not step.request.get("_base_captured")
                    # Freeze a valid old-format claim, as the v2 compiler did. The
                    # independent review additionally runs the actual archived v2 compiler.
                    content = {**step.request["content"]}
                    claims = [dict(claim) for claim in content["claims"]]
                    claims[1]["text"] = 'Declares variable fixture.Payload: var Payload = "' + "x" * 3000 + '"'
                    step.request = {**step.request, "content": {**content, "claims": claims}}
                    page_id = step.page_id
                    generation = task.checkpoint["generation"]
                    break
        else:
            raise AssertionError("Old task did not reach the publication boundary")
        monkeypatch.setattr(service_module, "COMPILER_VERSION", "static-wiki-v3")
        monkeypatch.setattr(pipeline_module, "COMPILER_VERSION", "static-wiki-v3")
        async with db.session() as session, session.begin():
            service = IngestService(session, auth, Machine(), internal, box)
            new = await service.sync("alice", source["id"], 1)
            duplicate = await service.sync("alice", source["id"], 1)
            assert new["id"] == duplicate["id"]
        await finish(pipeline, new["id"])
        endpoint = "/api/v1/pages/" + quote(page_id, safe="")
        latest = await internal.request("GET", endpoint, "worker")
        assert latest["revision"]["content"]["claims"][1]["text"] == "Declares variable fixture.Payload."
        await finish(pipeline, old["id"])
        assert (await internal.request("GET", endpoint, "worker"))["current_revision"] == latest["current_revision"]
        async with db.session() as session:
            current = await session.get(Source, source["id"])
            before, after = await session.get(Task, old["id"]), await session.get(Task, new["id"])
            assert current.version == 1
            assert current.generation == after.checkpoint["generation"] > generation
            assert before.status == "superseded"
