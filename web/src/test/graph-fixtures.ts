export const graphRef = {
  resource_id: "source-resource", revision_id: "snapshot", source_id: "repo",
  source_revision: "commit-fixed", path: "src/main.go", start_line: 1, end_line: 1,
  kind: "code" as const, valid_from: null, valid_until: null,
};
export const graphSnapshot = {
  id: "snapshot", resource_id: "source-resource", source_id: "repo", source_revision: "commit-fixed",
  path: "src/main.go", kind: "code", space_id: "source-space", text: "EXACT-GRAPH-SOURCE\n", sha256: "a".repeat(64),
};
export function graphResult() {
  return {
    items: ["fragment-a", "fragment-b", "fragment-edge"].map((id, index) => ({
      id, page_id: "page-" + index, revision_id: "wiki-" + index, version: 1, space_id: "space",
      title: ["授权 Wiki A", "授权 Wiki B", "依赖证据 Wiki"][index], text: "DECLARATION-" + index,
      kind: "fact", entity_ids: ["entity-" + index], evidence: [graphRef], citation_ids: ["citation"],
      primary: index === 0, rrf_score: null, rerank_score: null,
      url: "/pages/page-" + index + "?revision=wiki-" + index, state: "valid", valid_from: null, valid_until: null,
      known_at: "2026-09-08T00:00:00Z", is_current: true,
    })),
    evidence: [{ id: "citation", evidence: graphRef, space_id: "source-space", excerpt: "EXACT-GRAPH-SOURCE", sha256: "a".repeat(64) }],
    graph: {
      nodes: [{ id: "stable-a", fragment_ids: ["fragment-a"] }, { id: "stable-b", fragment_ids: ["fragment-b"] }],
      edges: [{ id: "edge-ab", source: "stable-a", target: "stable-b", type: "depends_on", kind: "fact", fragment_ids: ["fragment-edge"] }],
      paths: [{ node_ids: ["stable-a", "stable-b"], edge_ids: ["edge-ab"], fragment_ids: ["fragment-a", "fragment-b", "fragment-edge"] }], degraded: [],
    },
    degraded: [], gaps: [], auth_epoch: 3, as_of: "2026-09-08T01:00:00Z", known_at: null,
    time_basis: "source_revision_and_knowledge_time", deployment_state: "unknown_without_deployment_evidence",
  };
}
export function seedResult() {
  const result = graphResult();
  result.items = [result.items[0]];
  result.graph = { nodes: [], edges: [], paths: [], degraded: [] };
  return result;
}