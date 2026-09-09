import type { EvidenceRef, SourceSnapshot } from "./types";
export const graphRelationTypes = ["depends_on", "uses", "owns", "cites", "governed_by", "caused", "fixed", "supersedes", "contradicts"] as const;
export type GraphRelationType = (typeof graphRelationTypes)[number];
export interface GraphParameters {
  space_ids: string[];
  relation: { types: GraphRelationType[]; direction: "outgoing" | "incoming" | "both"; hops: 1 | 2 };
  as_of: string | null;
  known_at: string | null;
  include_historical: boolean;
}
export interface GraphFragment {
  id: string; page_id: string; revision_id: string; version: number; space_id: string;
  title: string; text: string; kind: "fact" | "inference" | "gap" | "narrative";
  entity_ids: string[]; evidence: EvidenceRef[]; citation_ids: string[]; primary: boolean;
  state: "valid" | "stale" | "retracted"; valid_from: string | null; valid_until: string | null;
  known_at: string; is_current: boolean;
}
export interface GraphEvidence { id: string; evidence: EvidenceRef; space_id: string; excerpt: string; sha256: string }
export interface GraphNode { id: string; fragment_ids: string[] }
export interface GraphEdge extends GraphNode { source: string; target: string; type: GraphRelationType; kind: "fact" | "inference" }
export interface GraphPath { node_ids: string[]; edge_ids: string[]; fragment_ids: string[] }
export interface GraphResult {
  items: GraphFragment[]; evidence: GraphEvidence[];
  graph: { nodes: GraphNode[]; edges: GraphEdge[]; paths: GraphPath[]; degraded: string[] };
  degraded: string[]; gaps: string[]; auth_epoch: number; as_of: string | null; known_at: string | null;
  time_basis: "source_revision_and_knowledge_time"; deployment_state: "unknown_without_deployment_evidence";
}
export type GraphSelection = { kind: "node" | "edge"; id: string };
export interface GraphExecution { mode: "search" | "graph"; params: GraphParameters; seed_fragment_ids: string[]; query: string }
export type GraphSource = SourceSnapshot;