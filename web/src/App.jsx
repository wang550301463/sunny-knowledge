import React, { useEffect, useMemo, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { toGraph } from "./graphMap.js";

const token = import.meta.env.VITE_SUNNY_TOKEN || "secret";
const headers = { "X-Token": token, "X-Role": "editor", "Content-Type": "application/json" };

export default function App() {
  const [payload, setPayload] = useState({ nodes: [], edges: [] });
  const [page, setPage] = useState(null);
  const [filter, setFilter] = useState("");
  const [minConf, setMinConf] = useState(0);

  useEffect(() => {
    fetch("/v1/graph/neighborhood?center=pay-api", { headers })
      .then((r) => r.json())
      .then(setPayload)
      .catch(() => setPayload({ nodes: [], edges: [] }));
  }, []);

  const graph = useMemo(() => {
    const g = toGraph(payload, filter || undefined);
    g.nodes = g.nodes.filter((n) => (n.confidence ?? 1) >= minConf);
    return g;
  }, [payload, filter, minConf]);

  async function openNode(id) {
    const candidates = id.includes("/") ? [`entities/files.md`] : [`entities/${id}.md`, `procedures/change.md`];
    for (const path of candidates) {
      const res = await fetch("/v1/wiki/" + path, { headers: { "X-Token": token, "X-Role": "reader" } });
      if (res.ok) {
        const p = await res.json();
        setPage({ ...p, highlight: id });
        return;
      }
    }
  }

  return (
    <div style={{ display: "flex", height: "100vh", fontFamily: "sans-serif" }}>
      <div style={{ flex: 1, borderRight: "1px solid #ddd" }}>
        <div style={{ padding: 8, display: "flex", gap: 8 }}>
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">全部来源</option>
            <option value="graph">图</option>
            <option value="code">代码</option>
            <option value="policy">制度</option>
            <option value="ticket">工单</option>
          </select>
          <label>
            置信度 ≥ {minConf.toFixed(2)}
            <input type="range" min="0" max="1" step="0.05" value={minConf} onChange={(e) => setMinConf(Number(e.target.value))} />
          </label>
        </div>
        <ForceGraph2D
          graphData={graph}
          linkLineDash={(l) => (l.invalid ? [4, 3] : [])}
          linkColor={(l) => (l.invalid ? "#999" : "#333")}
          nodeCanvasObject={(node, ctx, scale) => {
            ctx.globalAlpha = node.dim ? 0.35 : 1;
            ctx.fillStyle = "#2563eb";
            ctx.beginPath();
            ctx.arc(node.x, node.y, 5, 0, 2 * Math.PI);
            ctx.fill();
            ctx.globalAlpha = 1;
            ctx.fillStyle = "#111";
            ctx.font = `${12 / scale}px sans-serif`;
            ctx.fillText(node.id, node.x + 6, node.y);
          }}
          onNodeClick={(n) => openNode(n.id)}
        />
      </div>
      <div style={{ width: 420, padding: 16, overflow: "auto" }}>
        {page ? (
          <>
            <h2>{page.meta?.title || page.Meta?.Title}</h2>
            <pre style={{ whiteSpace: "pre-wrap" }}>{page.body || page.Body}</pre>
            {page.highlight ? <p>高亮：{page.highlight}</p> : null}
          </>
        ) : (
          <p>点左侧节点打开 wiki 页</p>
        )}
      </div>
    </div>
  );
}
