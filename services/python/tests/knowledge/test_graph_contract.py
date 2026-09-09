"""Structured graph statements retain canonical identity, evidence and review rules."""

import pytest
from pydantic import ValidationError

from .test_schema import evidence


def relation_claim(**updates):
    from knowledge_platform.knowledge.schemas import Claim

    return Claim.model_validate({
        "id": "repo:payments:relation:redis",
        "text": "Payments declares a Redis dependency; deployment is unknown.",
        "kind": "fact",
        "evidence": [evidence().model_dump(mode="json")],
        "relation": {"source_id": "repo:payments:module", "target_id": "repo:payments:dependency:redis", "type": "depends_on"},
    } | updates)


def test_graph_relationship_is_explicit_and_preserves_source_namespaced_ids():
    claim = relation_claim()
    assert claim.relation.type == "depends_on"
    assert claim.relation.source_id == "repo:payments:module"
    assert claim.relation.target_id == "repo:payments:dependency:redis"
    assert claim.entity is None
    inferred = relation_claim(kind="inference")
    assert inferred.kind == "inference"
    for changes in (
        {"relation": {"source_id": "a", "target_id": "b", "type": "deploys"}},
        {"relation": {"source_id": "", "target_id": "b", "type": "uses"}},
        {"kind": "gap"},
        {"kind": "inference", "evidence": []},
    ):
        with pytest.raises(ValidationError):
            relation_claim(**changes)


def test_entity_identity_cannot_disagree_with_its_canonical_claim():
    from knowledge_platform.knowledge.schemas import Claim

    value = {
        "id": "repo:payments:module",
        "text": "Module payments",
        "entity_type": "Module",
        "entity": {"id": "repo:payments:module", "name": "payments", "type": "Module"},
        "evidence": [evidence().model_dump(mode="json")],
    }
    assert Claim.model_validate(value).entity.name == "payments"
    for entity in (
        {**value["entity"], "id": "another:module"},
        {**value["entity"], "type": "Person"},
    ):
        with pytest.raises(ValidationError):
            Claim.model_validate(value | {"entity": entity})
    with pytest.raises(ValidationError):
        Claim.model_validate(value | {"relation": {"source_id": "a", "target_id": "b", "type": "uses"}})


@pytest.mark.asyncio
async def test_structured_edge_survives_publication_projection_and_inference_needs_review(store):
    from knowledge_platform.knowledge.schemas import DeterministicPublish, KnowledgeError
    from .test_postgres import content, source

    _, snapshot = await source(store)
    body = content(snapshot)
    claim = relation_claim(evidence=[body.evidence[0].model_dump(mode="json")])
    body.claims.append(claim)
    request = DeterministicPublish(
        page_id="page:payments:relations",
        space_id="finance",
        base_revision=None,
        content=body,
        proof={"compiler_version": "static-wiki-v2", "schema_version": "v2", "snapshot_ids": [snapshot["id"]], "idempotency_key": "graph-structure"},
    )
    factory, _, _ = store
    async with factory() as knowledge:
        revision = await knowledge.deterministic_publish("alice", "ingest", request)
    async with factory() as knowledge:
        projection = await knowledge.projection("retrieval", revision["page_id"], revision["id"])
    saved = next(c for c in projection["content"]["claims"] if c["id"] == claim.id)
    assert saved["relation"] == claim.relation.model_dump()
    assert saved["evidence"] == claim.model_dump(mode="json")["evidence"]
    assert projection["source_snapshots"][0]["id"] == snapshot["id"]
    request.content.claims[-1].kind = "inference"
    request.base_revision = revision["id"]
    request.proof.idempotency_key = "graph-inference"
    async with factory() as knowledge:
        with pytest.raises(KnowledgeError, match="Inferences") as denied:
            await knowledge.deterministic_publish("alice", "ingest", request)
        assert denied.value.code == "review_required"
