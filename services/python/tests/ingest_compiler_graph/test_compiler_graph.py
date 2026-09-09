"""Compile declared graph facts without promoting directory or naming heuristics."""

from dataclasses import replace

import pytest

from knowledge_platform.ingest.analyzers import SourceFile, SourceSnapshot, analyze_snapshot
from knowledge_platform.ingest.compiler import KnowledgeCompiler
from knowledge_platform.knowledge.schemas import PageContent


@pytest.fixture
def project():
    files = (
        SourceFile("package.json", '{"name":"payments","dependencies":{"redis":"5.0.0"}}\n'),
        SourceFile("src/main.ts", 'import Redis from "redis";\nexport function pay() { return 1; }\n'),
    )
    return SourceSnapshot("git:payments", "a" * 40, files)


def compile_project(project):
    snapshots = {
        file.path: {
            "id": "snapshot:" + file.path,
            "resource_id": "source:" + project.repo_id,
            "source_id": project.repo_id,
            "source_revision": project.source_revision,
            "path": file.path,
            "kind": "code",
            "text": file.content,
        }
        for file in project.files
    }
    result = analyze_snapshot(project)
    return result, KnowledgeCompiler().code_pages(result, snapshots)


def test_declarations_have_stable_entities_and_manifest_edges_with_exact_evidence(project):
    result, pages = compile_project(project)
    claims = [claim for page in pages for claim in page["content"]["claims"]]
    entities = {claim["id"]: claim["entity"] for claim in claims if claim.get("entity")}
    edges = [claim for claim in claims if claim.get("relation")]
    assert entities and edges
    assert any(entity["name"] == "payments" and entity["type"] == "Module" for entity in entities.values())
    edge = next(claim for claim in edges if claim["relation"]["type"] == "depends_on")
    expected = next(relation for relation in result.relations if relation.kind == "depends_on")
    assert edge["relation"] == {"source_id": expected.source_id, "target_id": expected.target_id, "type": "depends_on"}
    assert edge["relation"]["source_id"] in entities
    assert edge["relation"]["target_id"] in entities
    assert entities[edge["relation"]["target_id"]]["name"] == "redis"
    assert edge["evidence"][0]["path"] == "package.json"
    assert edge["evidence"][0]["start_line"] == expected.location.start_line
    assert edge["evidence"][0]["end_line"] == expected.location.end_line
    assert edge["evidence"][0]["source_revision"] == "a" * 40
    for page in pages:
        PageContent.model_validate(page["content"])
    # Commit and unrelated line movement do not invent a new logical relationship.
    _, next_pages = compile_project(replace(project, source_revision="b" * 40))
    next_ids = {claim["id"] for page in next_pages for claim in page["content"]["claims"] if claim.get("relation")}
    assert {edge["id"]} <= next_ids


def test_inferred_directory_membership_stays_out_of_automatic_formal_graph(project):
    result, pages = compile_project(project)
    assert any(relation.evidence == "inferred" for relation in result.relations)
    claims = [claim for page in pages for claim in page["content"]["claims"]]
    assert all(claim["kind"] == "fact" for claim in claims)
    assert not any(claim.get("relation", {}).get("type") == "uses" for claim in claims)
    _, other = compile_project(replace(project, repo_id="git:other"))
    other_ids = {claim["id"] for page in other for claim in page["content"]["claims"]}
    assert not {claim["id"] for claim in claims}.intersection(other_ids)
