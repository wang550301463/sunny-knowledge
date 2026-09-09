import { ApiError, pathId, query } from "./api";
import { sourceRows } from "./components/Content";
import { graphRelationTypes } from "./graph-types";
import type { GraphEvidence, GraphFragment, GraphResult, GraphSelection, GraphSource } from "./graph-types";
import type { EvidenceRef } from "./types";
const fail = (): never => { throw new ApiError(502, "invalid_graph_response"); };
const object = (v: unknown): Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : fail();
const text = (v: unknown, max = 65536): string => typeof v === "string" && v.length <= max ? v : fail();
const id = (v: unknown): string => text(v, 512) || fail();
const array = (v: unknown, max: number): unknown[] => Array.isArray(v) && v.length <= max ? v : fail();
const ids = (v: unknown, max: number, nonempty = false): string[] => { const a = array(v, max).map(id); return (!nonempty || a.length) && new Set(a).size === a.length ? a : fail(); };
const bool = (v: unknown): boolean => typeof v === "boolean" ? v : fail();
const integer = (v: unknown, min = 0): number => Number.isSafeInteger(v) && Number(v) >= min ? Number(v) : fail();
const choice = <T extends string>(v: unknown, choices: readonly T[]): T => choices.includes(v as T) ? v as T : fail();
const time = (v: unknown): string | null => v === null || v === undefined ? null : typeof v === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v)) ? v : fail();
function reference(value: unknown): EvidenceRef {
  const r = object(value), path = text(r.path, 2048), start = integer(r.start_line, 1), end = integer(r.end_line, 1);
  if (!path || /[\\\x00]/.test(path) || path.startsWith("/") || path.split("/").includes("..") || end < start) fail();
  return { resource_id: id(r.resource_id), revision_id: id(r.revision_id), source_id: id(r.source_id), source_revision: id(r.source_revision), path,
    start_line: start, end_line: end, kind: choice(r.kind, ["code", "markdown", "ticket", "policy", "source_manifest"]), valid_from: time(r.valid_from), valid_until: time(r.valid_until) };
}
export const graphRefKey = (r: EvidenceRef) => JSON.stringify([r.resource_id, r.revision_id, r.source_id, r.source_revision, r.path, r.start_line, r.end_line, r.kind, r.valid_from ?? null, r.valid_until ?? null]);
/** Treat the HTTP result as a bounded evidence graph; never render arbitrary extra payload fields. */
export function decodeGraphResult(value: unknown): GraphResult {
  const r = object(value), g = object(r.graph);
  const evidence = array(r.evidence, 10000).map((v): GraphEvidence => { const e = object(v); return { id: id(e.id), evidence: reference(e.evidence), space_id: id(e.space_id), excerpt: text(e.excerpt), sha256: text(e.sha256, 128) }; });
  const evidenceMap = new Map(evidence.map(e => [e.id, e]));
  if (evidenceMap.size !== evidence.length) fail();
  const items = array(r.items, 1000).map((v): GraphFragment => {
    const i = object(v), refs = array(i.evidence, 10000).map(reference), citations = ids(i.citation_ids, 10000);
    if (!citations.length || citations.some(c => !evidenceMap.has(c) || !refs.some(ref => graphRefKey(ref) === graphRefKey(evidenceMap.get(c)!.evidence)))) fail();
    return { id: id(i.id), page_id: id(i.page_id), revision_id: id(i.revision_id), version: integer(i.version, 1), space_id: id(i.space_id), title: text(i.title), text: text(i.text),
      kind: choice(i.kind, ["fact", "inference", "gap", "narrative"]), entity_ids: ids(i.entity_ids, 10000), evidence: refs, citation_ids: citations, primary: bool(i.primary),
      state: choice(i.state, ["valid", "stale", "retracted"]), valid_from: time(i.valid_from), valid_until: time(i.valid_until), known_at: time(i.known_at) ?? fail(), is_current: bool(i.is_current) };
  });
  const itemIds = new Set(items.map(i => i.id));
  if (itemIds.size !== items.length) fail();
  const fragments = (v: unknown) => { const a = ids(v, 100, true); if (a.some(i => !itemIds.has(i))) fail(); return a; };
  const nodes = array(g.nodes, 100).map(v => { const n = object(v); return { id: id(n.id), fragment_ids: fragments(n.fragment_ids) }; });
  const nodeIds = new Set(nodes.map(n => n.id));
  if (nodeIds.size !== nodes.length) fail();
  const edges = array(g.edges, 200).map(v => { const e = object(v), source = id(e.source), target = id(e.target); if (!nodeIds.has(source) || !nodeIds.has(target)) fail(); return { id: id(e.id), source, target, type: choice(e.type, graphRelationTypes), kind: choice(e.kind, ["fact", "inference"]), fragment_ids: fragments(e.fragment_ids) }; });
  const edgeMap = new Map(edges.map(e => [e.id, e]));
  if (edgeMap.size !== edges.length) fail();
  const paths = array(g.paths, 200).map(v => { const p = object(v), ns = ids(p.node_ids, 3, true), es = ids(p.edge_ids, 2); if (es.length !== ns.length - 1 || ns.some(n => !nodeIds.has(n)) || es.some((e, ix) => { const edge = edgeMap.get(e); return !edge || ![edge.source, edge.target].includes(ns[ix]) || ![edge.source, edge.target].includes(ns[ix + 1]); })) fail(); return { node_ids: ns, edge_ids: es, fragment_ids: fragments(p.fragment_ids) }; });
  return { items, evidence, graph: { nodes, edges, paths, degraded: ids(g.degraded, 100) }, degraded: ids(r.degraded, 100), gaps: ids(r.gaps, 100), auth_epoch: integer(r.auth_epoch), as_of: time(r.as_of), known_at: time(r.known_at), time_basis: choice(r.time_basis, ["source_revision_and_knowledge_time"]), deployment_state: choice(r.deployment_state, ["unknown_without_deployment_evidence"]) };
}
export function graphSelectionFragments(data: GraphResult, selection: GraphSelection) {
  const element = selection.kind === "node" ? data.graph.nodes.find(n => n.id === selection.id) : data.graph.edges.find(e => e.id === selection.id);
  return element ? data.items.filter(i => element.fragment_ids.includes(i.id)) : [];
}
export function graphSelectionEvidence(data: GraphResult, selection: GraphSelection) {
  const citationIds = new Set(graphSelectionFragments(data, selection).flatMap(f => f.citation_ids));
  return data.evidence.filter(e => citationIds.has(e.id));
}
export function matchesGraphSource(value: unknown, citation: GraphEvidence): value is GraphSource {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const s = value as Partial<GraphSource>, r = citation.evidence;
  return s.id === r.revision_id && s.resource_id === r.resource_id && s.source_id === r.source_id && s.source_revision === r.source_revision && s.path === r.path && s.kind === r.kind && s.space_id === citation.space_id && s.sha256 === citation.sha256 && typeof s.text === "string" && r.end_line <= sourceRows(s.text).length;
}
export const graphWikiHref = (item: GraphFragment) => `/pages/${pathId(item.page_id)}${query({ revision: item.revision_id })}`;
export const sameGraphEvidence = (a: GraphEvidence, b: GraphEvidence) => a.id === b.id && a.space_id === b.space_id && a.sha256 === b.sha256 && a.excerpt === b.excerpt && graphRefKey(a.evidence) === graphRefKey(b.evidence);