"""Graph HTTP contract; no user-supplied identity, Cypher, labels or summaries."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Key = Annotated[str, Field(min_length=1, max_length=512)]
RelationType = Literal[
    "depends_on",
    "uses",
    "owns",
    "cites",
    "governed_by",
    "caused",
    "fixed",
    "supersedes",
    "contradicts",
]
EntityType = Literal[
    "Service",
    "Module",
    "File",
    "Person",
    "Dependency",
    "Decision",
    "Policy",
    "Incident",
    "Change",
    "Procedure",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def unavailable(code="graph_unavailable", message="Graph dependency unavailable"):
    return GraphError(503, code, message)


def aware(value):
    if value is not None and value.utcoffset() is None:
        raise ValueError("Timestamp requires UTC offset")
    return value


class Policy(StrictModel):
    space_id: Key
    resource_id: Key
    space_read_subjects: list[str]
    resource_read_subjects: list[str] | None
    acl_version: int = Field(ge=0)
    auth_epoch: int = Field(ge=0)
    acl_domain: str = ""


class TraverseRequest(StrictModel):
    space_ids: list[Key] = Field(min_length=1, max_length=100)
    seed_fragment_ids: list[Key] = Field(min_length=1, max_length=30)
    relation_types: list[RelationType] = Field(
        default_factory=lambda: ["depends_on", "uses"], min_length=1, max_length=9
    )
    direction: Literal["outgoing", "incoming", "both"] = "outgoing"
    hops: int = Field(default=1, ge=1, le=2)
    max_nodes: int = Field(default=100, ge=1, le=100)
    max_edges: int = Field(default=200, ge=1, le=200)
    as_of: datetime | None = None
    known_at: datetime | None = None
    include_historical: bool = False

    _aware = field_validator("as_of", "known_at")(aware)

    @field_validator("space_ids", "seed_fragment_ids", "relation_types")
    @classmethod
    def unique(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Duplicate values")
        return values


class GraphNode(StrictModel):
    id: Key
    fragment_ids: list[Key] = Field(min_length=1, max_length=100)


class GraphEdge(StrictModel):
    id: Key
    source: Key
    target: Key
    type: RelationType
    kind: Literal["fact", "inference"]
    fragment_ids: list[Key] = Field(min_length=1, max_length=100)


class GraphPath(StrictModel):
    node_ids: list[Key] = Field(min_length=1, max_length=3)
    edge_ids: list[Key] = Field(max_length=2)
    fragment_ids: list[Key] = Field(min_length=1, max_length=100)


class GraphResult(StrictModel):
    nodes: list[GraphNode] = Field(default_factory=list, max_length=100)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=200)
    paths: list[GraphPath] = Field(default_factory=list, max_length=200)
    degraded: list[str] = Field(default_factory=list, max_length=10)


class ProjectedNode(GraphNode):
    name: str
    type: EntityType | Literal["unknown"]
    summary: str
    claim_ids: list[str]
    evidence: list[dict]
    placeholder: bool = False


class ProjectedEdge(GraphEdge):
    claim_id: Key
    text: str
    evidence: list[dict]


class Projection(StrictModel):
    page_id: Key
    revision_id: Key
    version: int = Field(ge=1)
    current_revision: Key
    current_version: int = Field(ge=1)
    space_id: Key
    group_id: str
    acl_domain: str
    policy_fingerprint: str
    acl_epoch: int = Field(ge=0)
    policies: list[dict]
    nodes: list[ProjectedNode] = Field(default_factory=list)
    edges: list[ProjectedEdge] = Field(default_factory=list)
    state: Literal["valid", "stale", "retracted"]
    valid_from: datetime | None
    valid_until: datetime | None
    known_at: datetime
    is_current: bool
    degraded: list[str]
    evidence: list[dict] = Field(default_factory=list)
    content_hash: str
"""Deterministic structured claims; never infer formal relations from natural language."""

import hashlib
import json
from datetime import datetime
from pathlib import PurePosixPath

from pydantic import ValidationError

from .schemas import Policy, ProjectedEdge, ProjectedNode, Projection, unavailable


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def clauses_for(policies):
    clauses = []
    for policy in policies:
        clauses.append(tuple(sorted(set(policy.space_read_subjects))))
        if policy.resource_read_subjects is not None:
            clauses.append(tuple(sorted(set(policy.resource_read_subjects))))
    return [list(c) for c in sorted(set(clauses))]


def policy_fingerprint(policies):
    values = [
        Policy.model_validate(p).model_dump() if isinstance(p, dict) else p.model_dump()
        for p in policies
    ]
    return digest(
        sorted(
            [{k: v for k, v in p.items() if k not in {"auth_epoch", "acl_domain"}} for p in values],
            key=lambda p: (p["space_id"], p["resource_id"]),
        )
    )


def checked_policies(projection):
    try:
        policies = [Policy.model_validate(value) for value in projection["policies"]]
        expected = {(projection["space_id"], projection["page_id"])} | {
            (s["space_id"], s["resource_id"]) for s in projection["source_snapshots"]
        }
        if (
            len({p.auth_epoch for p in policies}) != 1
            or {(p.space_id, p.resource_id) for p in policies} != expected
            or len(policies) != len(expected)
        ):
            raise ValueError
        clauses = clauses_for(policies)
        if projection["read_clauses"] != clauses or projection["acl_domain"] != digest(
            {"space_id": projection["space_id"], "read_clauses": clauses}
        ):
            raise ValueError
        return policies
    except (KeyError, TypeError, ValueError, ValidationError):
        raise unavailable(
            "invalid_projection", "Canonical graph projection has incomplete provenance or ACL"
        ) from None


def instant(value):
    if value is None:
        return None
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("Naive validity time")
    return result


def compile_graph(value):
    policies = checked_policies(value)
    try:
        content, page_id, revision_id = value["content"], value["page_id"], value["revision_id"]
        claims = content["claims"]
        if (
            type(value["version"]) is not int
            or type(value["current_version"]) is not int
            or not 1 <= value["version"] <= value["current_version"]
            or type(value["is_current"]) is not bool
            or value["is_current"] != (revision_id == value["current_revision"])
            or (value["is_current"] and value["version"] != value["current_version"])
        ):
            raise ValueError("Inconsistent canonical current revision")
        if any(c["state"] not in {"valid", "stale", "retracted"} for c in [content, *claims]):
            raise ValueError("Invalid canonical state")
        if len({c["id"] for c in claims}) != len(claims):
            raise ValueError("Duplicate claims")
        refs = list(
            {
                digest(r): r
                for r in content["evidence"] + [r for c in claims for r in c["evidence"]]
            }.values()
        )
        snapshots = {s["id"]: s for s in value["source_snapshots"]}
        for ref in refs:
            snapshot = snapshots[ref["revision_id"]]
            if ref["kind"] not in {"code", "markdown", "ticket", "policy", "source_manifest"}:
                raise ValueError("Invalid evidence kind")
            if (
                ref.get("valid_from")
                and ref.get("valid_until")
                and instant(ref["valid_from"]) >= instant(ref["valid_until"])
            ):
                raise ValueError("Invalid evidence interval")
            if any(
                ref[k] != snapshot[k]
                for k in ("source_id", "source_revision", "resource_id", "path", "kind")
            ):
                raise ValueError("Inconsistent evidence")
            if (
                type(ref["start_line"]) is not int
                or type(ref["end_line"]) is not int
                or not 1 <= ref["start_line"] <= ref["end_line"]
            ):
                raise ValueError("Invalid evidence location")
            if (
                "\\" in ref["path"]
                or "\x00" in ref["path"]
                or ref["path"].startswith("/")
                or ".." in PurePosixPath(ref["path"]).parts
            ):
                raise ValueError("Invalid source path")
        times = [content, *claims, *refs]
        starts = [instant(t["valid_from"]) for t in times if t.get("valid_from")]
        ends = [instant(t["valid_until"]) for t in times if t.get("valid_until")]
        start, end = max(starts, default=None), min(ends, default=None)
        states = {content["state"], *[c["state"] for c in claims]}
        state = "retracted" if "retracted" in states else "stale" if "stale" in states else "valid"
        if start and end and start >= end:
            state = "stale"
        nodes, edges, degraded = {}, [], set()
        markdown_ids = [
            digest([page_id, revision_id, "markdown", n])
            for n, _ in enumerate(range(0, len(content["markdown"]), 2000))
        ]

        def bound(ids):
            ids = sorted(set(ids))
            if len(ids) > 100:
                degraded.add("graph_evidence_budget_reached")
            return ids[:100]

        for claim in claims:
            if claim.get("relation") or not claim["evidence"] or claim["kind"] == "gap":
                continue
            entity = claim.get("entity")
            if entity and (entity["id"] != claim["id"] or entity["type"] != claim["entity_type"]):
                raise ValueError("Invalid entity identity")
            ids = [
                digest([page_id, revision_id, "claim", claim["id"], n])
                for n, _ in enumerate(range(0, len(claim["text"]), 2000))
            ]
            if not ids:
                raise ValueError("Empty claim")
            nodes[claim["id"]] = ProjectedNode(
                id=claim["id"],
                name=entity["name"] if entity else claim["id"],
                type=entity["type"] if entity else claim["entity_type"],
                summary=claim["text"],
                claim_ids=[claim["id"]],
                evidence=claim["evidence"],
                fragment_ids=bound(ids + markdown_ids),
            )
        # A narrative page is an evidenced navigation node; no fabricated containment edges.
        if refs and markdown_ids:
            nodes.setdefault(
                page_id,
                ProjectedNode(
                    id=page_id,
                    name=content["title"],
                    type=content["entity_type"],
                    summary="",
                    claim_ids=[],
                    evidence=refs,
                    fragment_ids=bound(markdown_ids),
                ),
            )
        for claim in claims:
            relation = claim.get("relation")
            if not relation:
                continue
            if (
                claim.get("entity")
                or not claim["evidence"]
                or claim["kind"] not in {"fact", "inference"}
            ):
                raise ValueError("Relation requires evidence")
            ids = [
                digest([page_id, revision_id, "claim", claim["id"], n])
                for n, _ in enumerate(range(0, len(claim["text"]), 2000))
            ]
            edge = ProjectedEdge(
                id=digest([page_id, revision_id, "edge", claim["id"]]),
                source=relation["source_id"],
                target=relation["target_id"],
                type=relation["type"],
                kind=claim["kind"],
                fragment_ids=bound(ids),
                claim_id=claim["id"],
                text=claim["text"],
                evidence=claim["evidence"],
            )
            edges.append(edge)
            for endpoint in (edge.source, edge.target):
                if endpoint not in nodes:
                    nodes[endpoint] = ProjectedNode(
                        id=endpoint,
                        name=endpoint,
                        type="unknown",
                        summary="",
                        claim_ids=[claim["id"]],
                        evidence=claim["evidence"],
                        placeholder=True,
                        fragment_ids=bound(ids + markdown_ids),
                    )
                    degraded.add("graph_projection_pending")
                else:
                    node = nodes[endpoint]
                    node.fragment_ids = bound(node.fragment_ids + ids)
                    node.claim_ids = sorted(set(node.claim_ids + [claim["id"]]))
                    node.evidence = list(
                        {digest(r): r for r in node.evidence + claim["evidence"]}.values()
                    )
        return Projection(
            page_id=page_id,
            revision_id=revision_id,
            version=value["version"],
            current_revision=value["current_revision"],
            current_version=value["current_version"],
            space_id=value["space_id"],
            group_id="knowledge_" + digest([value["space_id"], value["acl_domain"]]),
            acl_domain=value["acl_domain"],
            policy_fingerprint=policy_fingerprint(policies),
            acl_epoch=policies[0].auth_epoch,
            policies=[p.model_dump() for p in policies],
            nodes=list(nodes.values()),
            edges=edges,
            state=state,
            valid_from=start,
            valid_until=end,
            known_at=instant(value["created_at"]),
            is_current=value["is_current"],
            degraded=sorted(degraded),
            evidence=refs,
            content_hash=digest(content),
        )
    except (KeyError, TypeError, ValueError, ValidationError):
        raise unavailable("invalid_projection", "Canonical graph projection is invalid") from None
