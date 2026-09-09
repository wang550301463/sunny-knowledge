"""Reject projection internals and broken evidence links at the public adapter."""

from pydantic import ValidationError

from knowledge_platform.common.evidence import EvidenceRef

from .clients import BoundaryError

RESULT_FIELDS = {"items", "evidence", "graph", "degraded", "gaps", "auth_epoch", "as_of", "known_at", "time_basis", "deployment_state"}
ITEM_FIELDS = {"id", "page_id", "revision_id", "version", "space_id", "title", "text", "kind", "entity_ids", "evidence", "citation_ids", "primary", "rrf_score", "rerank_score", "url", "state", "valid_from", "valid_until", "known_at", "is_current"}


def require(condition):
    if not condition:
        raise BoundaryError(503, "invalid_dependency_response")


def retrieval_result(name, result, epoch):
    fields = {"items", "truncated", "auth_epoch", "time_basis", "deployment_state"} if name == "timeline" else RESULT_FIELDS
    require(set(result) == fields)
    require(type(result["auth_epoch"]) is int and result["auth_epoch"] == epoch)
    require(isinstance(result["items"], list) and len(result["items"]) <= (100 if name == "timeline" else 1000))
    if name == "timeline":
        require(type(result["truncated"]) is bool)
        for item in result["items"]:
            require(isinstance(item, dict) and set(item) == {"page_id", "revision_id", "version", "known_at", "state", "publication_kind", "source_revisions"})
        return
    require(isinstance(result["evidence"], list) and len(result["evidence"]) <= 10000)
    references = {}
    try:
        for value in result["evidence"]:
            require(isinstance(value, dict) and set(value) == {"id", "evidence", "space_id", "excerpt", "sha256"})
            require(isinstance(value["id"], str) and value["id"] not in references)
            references[value["id"]] = EvidenceRef.model_validate(value["evidence"])
        item_ids = set()
        for item in result["items"]:
            require(isinstance(item, dict) and set(item) == ITEM_FIELDS)
            require(isinstance(item["id"], str) and item["id"] not in item_ids)
            item_ids.add(item["id"])
            refs = [EvidenceRef.model_validate(value) for value in item["evidence"]]
            citation_ids = item["citation_ids"]
            require(isinstance(citation_ids, list) and len(citation_ids) == len(refs))
            require(all(references.get(key) == ref for key, ref in zip(citation_ids, refs, strict=True)))
        graph = result["graph"]
        require(isinstance(graph, dict) and set(graph) == {"nodes", "edges", "paths", "degraded"})
        for kind, limit, fields in (("nodes", 100, {"id", "fragment_ids"}), ("edges", 200, {"id", "source", "target", "type", "kind", "fragment_ids"}), ("paths", 200, {"node_ids", "edge_ids", "fragment_ids"})):
            require(isinstance(graph[kind], list) and len(graph[kind]) <= limit)
            for value in graph[kind]:
                require(isinstance(value, dict) and set(value) == fields)
                require(isinstance(value["fragment_ids"], list) and 0 < len(value["fragment_ids"]) <= 100)
                require(set(value["fragment_ids"]) <= item_ids)
    except (TypeError, KeyError, ValidationError):
        raise BoundaryError(503, "invalid_dependency_evidence") from None
