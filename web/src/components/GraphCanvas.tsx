import { useId } from "react";
import type { GraphParameters, GraphResult, GraphSelection } from "../graph-types";
export function graphNodeTitle(data: GraphResult, nodeId: string) {
  const node = data.graph.nodes.find(n => n.id === nodeId);
  return data.items.find(i => node?.fragment_ids.includes(i.id))?.title;
}
/** Deterministic bounded layout. Arrows always use stored source→target, even for incoming traversal. */
export function GraphCanvas({ data, params, onSelect }: { data: GraphResult; params: GraphParameters; onSelect: (selection: GraphSelection) => void }) {
  const marker = useId().replace(/:/g, "");
  const nodes = [...data.graph.nodes].sort((a, b) => a.id.localeCompare(b.id)), distances = new Map<string, number>();
  const primary = new Set(data.items.filter(i => i.primary).map(i => i.id));
  nodes.filter(n => n.fragment_ids.some(id => primary.has(id))).forEach(n => distances.set(n.id, 0));
  if (!distances.size && nodes[0]) distances.set(nodes[0].id, 0);
  for (let hop = 0; hop < params.relation.hops; hop++) for (const edge of data.graph.edges) {
    const pairs = params.relation.direction === "incoming" ? [[edge.target, edge.source]] : params.relation.direction === "outgoing" ? [[edge.source, edge.target]] : [[edge.source, edge.target], [edge.target, edge.source]];
    for (const [source, target] of pairs) if (distances.get(source) === hop && !distances.has(target)) distances.set(target, hop + 1);
  }
  const buckets = [0, 1, 2, 3].map(hop => nodes.filter(n => (distances.get(n.id) ?? 3) === hop));
  const active = buckets.filter(b => b.length), width = Math.max(820, active.length * 280), height = Math.max(300, Math.max(...active.map(b => b.length), 0) * 100 + 60);
  const positions = new Map(active.flatMap((bucket, column) => bucket.map((n, row) => [n.id, { x: 35 + column * 280, y: 35 + row * 100 }] as const)));
  return <div className="graph-canvas-scroll" tabIndex={0} aria-label="关系图滚动区域"><svg role="img" aria-label="授权关系图" width={width} height={height}>
    <defs><marker id={marker} markerWidth="9" markerHeight="9" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#677ba4"/></marker></defs>
    {data.graph.edges.map((edge, index) => {
      const a = positions.get(edge.source)!, b = positions.get(edge.target)!;
      const right = b.x >= a.x, x1 = a.x + (right ? 220 : 0), x2 = b.x + (right ? 0 : 220), y1 = a.y + 31, y2 = b.y + 31;
      const curve = a.x === b.x ? `M ${x1} ${y1} C ${x1 + 40 + index % 3 * 12} ${y1 - 42}, ${x2 + 40 + index % 3 * 12} ${y2 + 42}, ${x2} ${y2}` : `M ${x1} ${y1} C ${(x1 + x2) / 2} ${y1}, ${(x1 + x2) / 2} ${y2}, ${x2} ${y2}`;
      return <g key={edge.id}><path d={curve} fill="none" stroke="#677ba4" strokeWidth={1.6} strokeDasharray={edge.kind === "inference" ? "5 5" : undefined} markerEnd={`url(#${marker})`}><title>{edge.source} → {edge.target} · {edge.type} · {edge.kind}</title></path></g>;
    })}
    {nodes.map(node => { const p = positions.get(node.id)!, title = graphNodeTitle(data, node.id); return <g key={node.id} transform={`translate(${p.x},${p.y})`} role="button" tabIndex={0} aria-label={`图中节点 ${node.id}`} onClick={() => onSelect({ kind: "node", id: node.id })} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect({ kind: "node", id: node.id }); } }}>
      <title>{node.id}{title ? ` · 关联 Wiki：${title}` : ""}</title><rect width={220} height={64} rx={10} fill="#f4f7ff" stroke="#93a7cb"/><text x={12} y={24} className="graph-svg-id">{node.id.length > 25 ? node.id.slice(0, 22) + "…" : node.id}</text><text x={12} y={47} className="graph-svg-title">关联 Wiki：{title && title.length > 12 ? title.slice(0, 12) + "…" : title ?? "未提供标题"}</text>
    </g>; })}
  </svg></div>;
}