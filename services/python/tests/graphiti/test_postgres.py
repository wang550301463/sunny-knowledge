"""Real PostgreSQL projection fencing; set GRAPHITI_TEST_DATABASE_URL."""

import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .fixtures import projection


@pytest_asyncio.fixture
async def catalog():
    url = os.environ.get("GRAPHITI_TEST_DATABASE_URL") or os.environ.get(
        "TEST_GRAPHITI_DATABASE_URL"
    )
    if not url:
        pytest.skip("GRAPHITI_TEST_DATABASE_URL required for real PostgreSQL")
    from knowledge_platform.common.db import Database
    from knowledge_platform.graphiti.models import initialize
    from knowledge_platform.graphiti.store import Catalog

    schema = "test_graphiti_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_async_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    await initialize(engine)
    yield Catalog(Database(engine))
    await engine.dispose()
    async with admin.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    await admin.dispose()


@pytest.mark.asyncio
async def test_rebuild_checkpoint_resumes_incomplete_run_and_restart_completed_run(catalog):
    from knowledge_platform.graphiti.schemas import GraphError

    async with catalog.rebuild_lock("index-model"):
        assert await catalog.rebuild_state("index-model") == {"cursor": None, "processed": 0}
        await catalog.rebuild_checkpoint("index-model", "cursor-1", 100, False)
        assert await catalog.rebuild_state("index-model") == {
            "cursor": "cursor-1",
            "processed": 100,
        }
        with pytest.raises(GraphError) as error:
            async with catalog.rebuild_lock("index-model"):
                pytest.fail("concurrent rebuild acquired the same lock")
        assert error.value.code == "rebuild_running"
        await catalog.rebuild_checkpoint("index-model", None, 10, True)
    async with catalog.rebuild_lock("index-model"):
        assert await catalog.rebuild_state("index-model") == {"cursor": None, "processed": 0}


@pytest.mark.asyncio
async def test_cancelled_rebuild_releases_advisory_session_lock(catalog):
    entered = asyncio.Event()

    async def hold():
        async with catalog.rebuild_lock("index-model"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(hold())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with catalog.rebuild_lock("index-model"):
        pass


@pytest.mark.asyncio
async def test_failed_graph_transaction_receipt_not_committed_and_generation_never_reused(catalog):
    from knowledge_platform.graphiti.projection import compile_graph

    seen = []

    async def fail(graph, generation):
        seen.append(generation)
        raise RuntimeError("uncertain commit")

    graph = compile_graph(projection())
    with pytest.raises(RuntimeError):
        await catalog.project(graph, fail, event_id="event-a")
    assert await catalog.scope(["engineering"], 100) == []

    async def write(graph, generation):
        seen.append(generation)
        return "projection-" + str(generation)

    await catalog.project(graph, write, event_id="event-a")
    assert seen[1] > seen[0]
    await catalog.project(graph, write, event_id="event-a")
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_concurrent_publications_serialize_and_old_event_never_restores_current(catalog):
    from knowledge_platform.graphiti.projection import compile_graph

    active = peak = 0

    async def write(graph, generation):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0.03)
        active -= 1
        return "projection-" + str(generation)

    old = compile_graph(projection())
    new = compile_graph(
        projection(
            revision_id="revision-b", version=2, current_revision="revision-b", current_version=2
        )
    )
    await asyncio.gather(catalog.project(new, write), catalog.project(old, write))
    assert peak == 1
    rows = await catalog.scope(["engineering"], 100)
    assert {r["graph"].revision_id: r["graph"].is_current for r in rows} == {
        "revision-a": False,
        "revision-b": True,
    }


@pytest.mark.asyncio
async def test_stale_acl_snapshot_and_mutated_immutable_revision_rejected(catalog):
    from knowledge_platform.graphiti.projection import clauses_for, compile_graph, digest
    from knowledge_platform.graphiti.schemas import GraphError, Policy

    writes = []

    async def write(graph, generation):
        writes.append(graph)
        return "projection-" + str(generation)

    value = projection()
    for p in value["policies"]:
        p["auth_epoch"] += 1
    value["policies"][0].update(resource_read_subjects=["user:bob"], acl_version=12)
    value["read_clauses"] = clauses_for([Policy.model_validate(p) for p in value["policies"]])
    value["acl_domain"] = digest(
        {"space_id": value["space_id"], "read_clauses": value["read_clauses"]}
    )
    await catalog.project(compile_graph(value), write)
    await catalog.project(compile_graph(projection()), write)
    assert len(writes) == 1
    value["content"]["markdown"] = "silently mutated"
    with pytest.raises(GraphError, match="Immutable"):
        await catalog.project(compile_graph(value), write)


@pytest.mark.asyncio
async def test_reconcile_failure_backoff_does_not_starve_other_revisions(catalog):
    from datetime import UTC, datetime, timedelta

    from knowledge_platform.graphiti.projection import compile_graph

    async def write(graph, generation):
        return "projection-" + str(generation)

    await catalog.project(compile_graph(projection()), write)
    await catalog.project(
        compile_graph(
            projection(
                revision_id="revision-b",
                version=2,
                current_revision="revision-b",
                current_version=2,
            )
        ),
        write,
    )
    await catalog.defer("revision-a", "graph_unavailable")
    rows = await catalog.due(datetime.now(UTC) + timedelta(milliseconds=1))
    assert [r["revision_id"] for r in rows] == ["revision-b"]


@pytest.mark.asyncio
async def test_unchanged_reconcile_reuses_graph_but_restores_missing_projection(catalog):
    from knowledge_platform.graphiti.projection import compile_graph

    writes = []

    async def write(graph, generation):
        writes.append(generation)
        return "projection-" + str(generation)

    async def present(projection_id, graph):
        return True

    graph = compile_graph(projection())
    await catalog.project(graph, write, exists=present)
    await catalog.project(graph, write, exists=present)
    assert len(writes) == 1

    async def missing(projection_id, graph):
        return False

    await catalog.project(graph, write, exists=missing)
    assert len(writes) == 2 and writes[1] > writes[0]


@pytest.mark.asyncio
async def test_scope_loads_only_metadata_and_selected_seeds_hydrate_exact_revision(catalog):
    from knowledge_platform.graphiti.projection import compile_graph

    from .test_projection import structured

    graph = compile_graph(structured())

    async def write(graph, generation):
        return "projection-current"

    await catalog.project(graph, write)
    records = await catalog.scope(["engineering"], 100)
    assert records[0]["graph"].nodes == [] and records[0]["graph"].evidence == []
    assert records[0]["hydrated"] is False
    assert set(await catalog.graphs(["projection-current"])) == {"projection-current"}
    assert await catalog.seed_projections(["projection-current"], graph.nodes[0].fragment_ids) == {
        "projection-current"
    }
    assert await catalog.seed_projections(["different"], graph.nodes[0].fragment_ids) == set()
import os
from uuid import uuid4

import pytest
import pytest_asyncio

from .test_projection import structured


@pytest_asyncio.fixture
async def graph_backend():
    from knowledge_platform.graphiti.backend import Neo4jGraph
    from knowledge_platform.graphiti.config import GraphitiSettings

    uri = os.getenv("TEST_GRAPHITI_NEO4J_URI")
    if not uri:
        pytest.skip("Real Neo4j not configured")
    settings = GraphitiSettings(
        graphiti_neo4j_uri=uri,
        graphiti_neo4j_password=os.environ["TEST_GRAPHITI_NEO4J_PASSWORD"],
        graphiti_namespace="test-" + uuid4().hex,
    )
    backend = Neo4jGraph(settings)
    await backend.initialize()
    try:
        yield backend
    finally:
        async with backend.driver.transaction() as tx:
            await tx.run(
                "MATCH (n) WHERE n.knowledge_namespace = $namespace DETACH DELETE n",
                namespace=settings.graphiti_namespace,
            )
        await backend.close()


@pytest.mark.asyncio
async def test_real_sdk_episode_entity_edge_and_adjacency_without_model(graph_backend):
    from knowledge_platform.graphiti.projection import compile_graph

    graph = compile_graph(structured())
    pid = graph_backend.projection_id(graph, 1)
    await graph_backend.write(graph, 1)
    seed = next(n for n in graph.nodes if n.id == "git:pay:module")
    rows = await graph_backend.seeds([pid], seed.fragment_ids, 100)
    assert any(r["node"]["id"] == seed.id for r in rows)
    outgoing = await graph_backend.adjacent([pid], [seed.id], ["depends_on"], "outgoing", 20)
    assert len(outgoing) == 1
    assert outgoing[0]["edge"]["target"] == "go:pq"
    assert not await graph_backend.adjacent([pid], [seed.id], ["depends_on"], "incoming", 20)
    assert not await graph_backend.adjacent([pid], [seed.id], ["uses"], "both", 20)
    from graphiti_core.nodes import EpisodicNode

    episode = await EpisodicNode.get_by_uuid(graph_backend.driver, pid)
    assert episode.group_id == graph.group_id
    assert episode.entity_edges
    assert graph.revision_id in episode.content


@pytest.mark.asyncio
async def test_old_late_generation_cannot_poison_selected_new_graph(graph_backend):
    from knowledge_platform.graphiti.projection import compile_graph

    old = compile_graph(structured())
    value = structured()
    value["content"]["claims"][1]["relation"]["target_id"] = "new:target"
    newer = compile_graph(value)
    await graph_backend.write(newer, 2)
    await graph_backend.write(old, 1)  # Simulate older transaction committing after timeout.
    pid = graph_backend.projection_id(newer, 2)
    rows = await graph_backend.adjacent([pid], ["git:pay:module"], ["depends_on"], "outgoing", 20)
    assert {r["edge"]["target"] for r in rows} == {"new:target"}
    assert not await graph_backend.adjacent(
        ["uncommitted-projection"], ["git:pay:module"], ["depends_on"], "outgoing", 20
    )


@pytest.mark.asyncio
async def test_sdk_projection_transaction_rolls_back_whole_episode_on_failure(
    graph_backend, monkeypatch
):
    from knowledge_platform.graphiti.projection import compile_graph
    from knowledge_platform.graphiti.schemas import GraphError

    graph = compile_graph(structured())

    async def broken(*args, **kwargs):
        raise RuntimeError("unsafe secret provider text")

    monkeypatch.setattr(graph_backend.driver.entity_edge_ops, "save", broken)
    with pytest.raises(GraphError) as exc:
        await graph_backend.write(graph, 1)
    assert "secret" not in str(exc.value)
    assert not await graph_backend.seeds(
        [graph_backend.projection_id(graph, 1)], graph.nodes[0].fragment_ids, 100
    )


@pytest.mark.asyncio
async def test_combined_catalog_actual_graph_loss_rebuild_and_same_generation_after_restore(
    catalog, graph_backend
):
    from knowledge_platform.graphiti.projection import compile_graph

    graph = compile_graph(structured())
    await catalog.project(graph, graph_backend.write, exists=graph_backend.exists)
    first = (await catalog.scope(["engineering"], 100))[0]
    assert await graph_backend.exists(first["projection_id"], graph)
    async with graph_backend.driver.transaction() as tx:
        await tx.run(
            "MATCH (n) WHERE n.knowledge_namespace = $ns DETACH DELETE n",
            ns=graph_backend.settings.graphiti_namespace,
        )
    assert not await graph_backend.exists(first["projection_id"], graph)
    await catalog.project(graph, graph_backend.write, exists=graph_backend.exists)
    restored = (await catalog.scope(["engineering"], 100))[0]
    assert restored["projection_id"] != first["projection_id"]
    assert await graph_backend.exists(restored["projection_id"], graph)
    # Even a restored PG sequence cannot alias a different policy or content snapshot.
    changed = graph.model_copy(update={"policy_fingerprint": "f" * 64})
    assert graph_backend.projection_id(graph, 1) != graph_backend.projection_id(changed, 1)


@pytest.mark.asyncio
async def test_real_adjacency_cross_partition_pruned_on_current_policy_tightening(
    catalog, graph_backend
):
    from types import SimpleNamespace

    from knowledge_platform.graphiti.projection import clauses_for, digest
    from knowledge_platform.graphiti.schemas import Policy
    from knowledge_platform.graphiti.service import GraphService

    from .test_authorization import Auth
    from .test_traversal import Clients, records, request

    rows = records()
    # Give the second projection a distinct ACL domain while preserving Alice access.
    graph = rows[1]["graph"]
    graph.policies[0]["resource_read_subjects"] = ["user:alice"]
    graph.acl_domain = digest(
        {
            "space_id": graph.space_id,
            "read_clauses": clauses_for([Policy.model_validate(p) for p in graph.policies]),
        }
    )
    from knowledge_platform.graphiti.projection import policy_fingerprint

    graph.policy_fingerprint = policy_fingerprint(graph.policies)
    graph.group_id = "knowledge_" + digest([graph.space_id, graph.acl_domain])
    for row in rows:
        await catalog.project(row["graph"], graph_backend.write)
    auth = Auth()
    auth.current = [*rows[0]["graph"].policies, graph.policies[0]]
    service = GraphService(
        SimpleNamespace(graphiti_max_policy_records=100), auth, catalog, graph_backend, Clients()
    )
    result = await service.traverse("token", request())
    assert any(p.node_ids == ["git:pay:module", "go:pq", "service:disk"] for p in result.paths)
    # Tightening before the asynchronous rebuild immediately makes the old graph unqueryable.
    auth.current[-1].update(resource_read_subjects=["user:bob"], acl_version=30)
    result = await service.traverse("token", request())
    assert {e.type for e in result.edges} == {"depends_on"}
    assert all(n.id != "service:disk" for n in result.nodes)


@pytest.mark.asyncio
async def test_explicit_rebuild_repairs_changed_payload_with_unchanged_object_counts(
    catalog, graph_backend
):
    from knowledge_platform.graphiti.projection import compile_graph
    from knowledge_platform.graphiti.worker import ProjectionWorker

    from .test_authorization import Auth
    from .test_worker import Clients, Tokens

    graph = compile_graph(structured())
    await catalog.project(graph, graph_backend.write)
    old = (await catalog.scope(["engineering"], 100))[0]
    async with graph_backend.driver.transaction() as tx:
        await tx.run(
            'MATCH (n:Entity) WHERE n.knowledge_projection=$id SET n.knowledge_payload="{}"',
            id=old["projection_id"],
        )
    assert await graph_backend.exists(
        old["projection_id"], graph
    )  # Ordinary count checks cannot certify payload integrity.
    clients = Clients()

    async def call(*args, **kwargs):
        return {
            "items": [
                {
                    "page_id": graph.page_id,
                    "revision_id": graph.revision_id,
                    "version": graph.version,
                }
            ],
            "next_cursor": None,
        }

    clients.call = call
    worker = ProjectionWorker(
        graph_backend.settings, Auth(), catalog, graph_backend, clients, Tokens()
    )
    await worker.rebuild()
    new = (await catalog.scope(["engineering"], 100))[0]
    assert new["projection_id"] != old["projection_id"]
    rows = await graph_backend.adjacent(
        [new["projection_id"]], ["git:pay:module"], ["depends_on"], "outgoing", 20
    )
    assert rows[0]["source"]["id"] == "git:pay:module"
