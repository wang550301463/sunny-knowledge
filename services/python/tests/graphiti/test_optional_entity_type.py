"""Canonical nullable entity types remain unknown navigation types in Graphiti."""

import hashlib

import pytest
from knowledge.test_http import client_for
from knowledge.test_postgres import store  # noqa: F401

from knowledge_platform.graphiti.projection import compile_graph, digest
from knowledge_platform.graphiti.schemas import GraphError
from knowledge_platform.ingest.compiler import Compiler

from .fixtures import projection


@pytest.mark.asyncio
@pytest.mark.parametrize("untyped_claim", [False, True])
async def test_reviewed_markdown_projection_preserves_nullable_canonical_types(
    store, untyped_claim
):
    text = "# Reviewed README\nThis repository documents payment processing.\n"
    async with client_for(store) as (client, headers):
        response = await client.post(
            "/internal/v1/sources/snapshots",
            headers=headers("ingest"),
            json={
                "source_id": "git:readme",
                "source_revision": "a" * 40,
                "resource_id": "source:readme",
                "space_id": "finance",
                "path": "README.md",
                "kind": "markdown",
                "text": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "object_key": "raw/readme/commit-a/README.md",
            },
        )
        assert response.status_code == 201, response.text
        snapshot = response.json()
        compiled = Compiler().narrative_page("git:readme", snapshot)
        assert compiled["content"]["entity_type"] is None
        if untyped_claim:
            compiled["content"]["claims"] = [
                {
                    "id": "claim:readme",
                    "text": "This repository documents payment processing.",
                    "kind": "fact",
                    "evidence": compiled["content"]["evidence"],
                }
            ]
        created = await client.post(
            "/api/v1/pages",
            headers=headers(),
            json={"id": compiled["page_id"], "space_id": "finance", "content": compiled["content"]},
        )
        assert created.status_code == 201, created.text
        proposal = created.json()["proposal"]
        approved = await client.post(
            f"/api/v1/reviews/{proposal['id']}/approve",
            headers=headers(actor="reviewer"),
            json={"reason": "Reviewed the exact Markdown source"},
        )
        assert approved.status_code == 200, approved.text
        revision = approved.json()
        response = await client.get(
            f"/internal/v1/projections/pages/{compiled['page_id']}/revisions/{revision['id']}",
            headers=headers("graphiti", actor=None),
        )
        assert response.status_code == 200, response.text
        canonical = response.json()
        assert canonical["content"]["entity_type"] is None
        if untyped_claim:
            assert canonical["content"]["claims"][0]["entity_type"] is None

    graph = compile_graph(canonical)
    nodes = {node.id: node for node in graph.nodes}
    narrative = nodes[compiled["page_id"]]
    assert narrative.type == "unknown"
    assert narrative.name == "README.md"
    assert not narrative.placeholder
    assert narrative.evidence == canonical["content"]["evidence"]
    assert narrative.fragment_ids == [digest([graph.page_id, graph.revision_id, "markdown", 0])]
    assert graph.edges == []  # A missing type cannot imply any formal relation.
    assert graph.degraded == []  # Evidenced unknown type is valid, not an absent projection.
    if untyped_claim:
        claim = nodes["claim:readme"]
        assert claim.type == "unknown"
        assert claim.name == claim.id
        assert claim.evidence == canonical["content"]["claims"][0]["evidence"]
        assert digest([graph.page_id, graph.revision_id, "claim", claim.id, 0]) in claim.fragment_ids


@pytest.mark.parametrize("target", ["page", "claim"])
@pytest.mark.parametrize("invalid", ["", "InventedEntity", 0, False])
def test_missing_type_fallback_does_not_accept_invalid_entity_values(target, invalid):
    value = projection()
    content = value["content"]
    (content if target == "page" else content["claims"][0])["entity_type"] = invalid
    with pytest.raises(GraphError) as error:
        compile_graph(value)
    assert error.value.code == "invalid_projection"
