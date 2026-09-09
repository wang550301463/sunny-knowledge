"""Graph HTTP contract; no user-supplied identity, Cypher, labels or summaries."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Key = Annotated[str, Field(min_length=1, max_length=512)]
RelationType = Literal['depends_on', 'uses', 'owns', 'cites', 'governed_by', 'caused', 'fixed', 'supersedes', 'contradicts']
EntityType = Literal['Service', 'Module', 'File', 'Person', 'Dependency', 'Decision', 'Policy', 'Incident', 'Change', 'Procedure']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class GraphError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def unavailable(code='graph_unavailable', message='Graph dependency unavailable'):
    return GraphError(503, code, message)


def aware(value):
    if value is not None and value.utcoffset() is None:
        raise ValueError('Timestamp requires UTC offset')
    return value


class Policy(StrictModel):
    space_id: Key
    resource_id: Key
    space_read_subjects: list[str]
    resource_read_subjects: list[str] | None
    acl_version: int = Field(ge=0)
    auth_epoch: int = Field(ge=0)
    acl_domain: str = ''


class TraverseRequest(StrictModel):
    space_ids: list[Key] = Field(min_length=1, max_length=100)
    seed_fragment_ids: list[Key] = Field(min_length=1, max_length=30)
    relation_types: list[RelationType] = Field(default_factory=lambda: ['depends_on', 'uses'], min_length=1, max_length=9)
    direction: Literal['outgoing', 'incoming', 'both'] = 'outgoing'
    hops: int = Field(default=1, ge=1, le=2)
    max_nodes: int = Field(default=100, ge=1, le=100)
    max_edges: int = Field(default=200, ge=1, le=200)
    as_of: datetime | None = None
    known_at: datetime | None = None
    include_historical: bool = False

    _aware = field_validator('as_of', 'known_at')(aware)

    @field_validator('space_ids', 'seed_fragment_ids', 'relation_types')
    @classmethod
    def unique(cls, values):
        if len(set(values)) != len(values):
            raise ValueError('Duplicate values')
        return values


class GraphNode(StrictModel):
    id: Key
    fragment_ids: list[Key] = Field(min_length=1, max_length=100)


class GraphEdge(StrictModel):
    id: Key
    source: Key
    target: Key
    type: RelationType
    kind: Literal['fact', 'inference']
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
    type: EntityType | Literal['unknown']
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
    nodes: list[ProjectedNode]
    edges: list[ProjectedEdge]
    state: Literal['valid', 'stale', 'retracted']
    valid_from: datetime | None
    valid_until: datetime | None
    known_at: datetime
    is_current: bool
    degraded: list[str]
    evidence: list[dict]
    content_hash: str