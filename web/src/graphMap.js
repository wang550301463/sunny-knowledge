export function toGraph(payload, sourceFilter) {
  const nodes = (payload.nodes || []).map((n) => ({
    id: n.id,
    group: n.kind || "Entity",
    dim: (n.confidence ?? 1) < 0.4,
    confidence: n.confidence ?? 1,
  }));
  const links = (payload.edges || [])
    .filter((e) => !sourceFilter || e.sourceKind === sourceFilter || e.origin === sourceFilter)
    .map((e) => ({
      source: e.source,
      target: e.target,
      invalid: Boolean(e.invalidAt),
      kind: e.kind,
    }));
  return { nodes, links };
}
