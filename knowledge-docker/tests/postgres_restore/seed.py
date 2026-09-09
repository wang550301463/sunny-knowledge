"""Actual canonical schema and immutable data, only on the isolated fixture PG."""
import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.engine import URL

from knowledge_platform.knowledge.models import (
    Audit, Outbox, Page, Revision, SourceSnapshot, initialize,
)


async def main():
    engine = create_async_engine(URL.create(
        "postgresql+psycopg", username="postgres",
        password=os.environ["PG_RESTORE_TEST_PASSWORD"], host="postgres",
        database="source_knowledge",
    ))
    await initialize(engine)
    async with engine.begin() as conn:
        await conn.execute(SourceSnapshot.__table__.insert().values(
            id="snapshot", source_id="git", source_revision="a" * 40,
            resource_id="source", space_id="private", path="src/Main.java", kind="code",
            text="private immutable source\n", sha256="b" * 64,
            object_key="objects/sha256/bb/" + "b" * 64, registered_by="ingest",
        ))
        for page, space in (("input", "private"), ("derived", "public")):
            await conn.execute(Page.__table__.insert().values(
                id=page, space_id=space, current_revision=page + "-r1",
                revision_number=1, created_by="actor",
            ))
            await conn.execute(Revision.__table__.insert().values(
                id=page + "-r1", page_id=page, number=1,
                content={"markdown": "# 不可变\n" + page, "citations": []},
                access_dependencies=["snapshot"],
                input_revisions=([{"page_id": "input", "revision_id": "input-r1"}]
                                 if page == "derived" else []),
                created_by="actor", publication_kind="reviewed", proof={"review": "approved"},
            ))
            await conn.execute(Audit.__table__.insert().values(
                id=page + "-audit", action="page.publish", actor_id="actor",
                resource_id=page, details={"revision_id": page + "-r1"},
            ))
            await conn.execute(Outbox.__table__.insert().values(
                id=page + "-event", page_id=page, revision_id=page + "-r1", version=1,
                payload={"page_id": page, "input_revisions": [{"page_id": "input", "revision_id": "input-r1"}]},
            ))
    await engine.dispose()
    for database in ("source_iam", "source_temporal_visibility"):
        engine = create_async_engine(URL.create(
            "postgresql+psycopg", username="postgres",
            password=os.environ["PG_RESTORE_TEST_PASSWORD"], host="postgres", database=database,
        ))
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE fixture_state (id int PRIMARY KEY, value text NOT NULL)"))
            await conn.execute(text("INSERT INTO fixture_state VALUES (1, 'encrypted:unchanged-fixture-bytes')"))
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())