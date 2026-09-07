import React, { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { toGraph } from "./graphMap.js";

const token = import.meta.env.VITE_SUNNY_TOKEN || "secret";
const headers = { "X-Token": token, "X-Role": "editor", "Content-Type": "application/json" };

export default function App() {
  const [payload, setPayload] = useState({ nodes: [], edges: [] });
  const [page, setPage] = useState(null);
  const [filter, setFilter] = useState("");
  const [minConf, setMinConf] = useState(0);
  const wrapRef = useRef(null);
  const [size, setSize] = useState({ w: 640, h: 640 });

  useEffect(() => {
    fetch("/v1/graph/neighborhood?center=pay-api", { headers })
      .then((r) => r.json())
      .then(setPayload)
      .catch(() => setPayload({ nodes: [], edges: [] }));
  }, []);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setSize({ w: Math.max(1, el.clientWidth), h: Math.max(1, el.clientHeight) });
    });
    ro.observe(el);
    setSize({ w: Math.max(1, el.clientWidth), h: Math.max(1, el.clientHeight) });
    return () => ro.disconnect();
  }, []);

  const graph = useMemo(() => {
    const g = toGraph(payload, filter || undefined);
    g.nodes = g.nodes.filter((n) => (n.confidence ?? 1) >= minConf);
    return g;
  }, [payload, filter, minConf]);

  async function openNode(id) {
    const isFile = id.includes("/") || /\.\w+$/.test(id);
    const candidates = isFile ? ["entities/files.md"] : [`entities/${id}.md`, "procedures/change.md"];
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
    <div style={{ display: "flex", height: "100vh", fontFamily: "sans-serif", background: "#111827", color: "#111" }}>
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <div style={{ padding: 8, display: "flex", gap: 8, background: "#f3f4f6" }}>
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
        <div ref={wrapRef} style={{ flex: 1, minHeight: 0 }}>
          <ForceGraph2D
            width={size.w}
            height={size.h}
            backgroundColor="#111827"
            graphData={graph}
            linkLineDash={(l) => (l.invalid ? [4, 3] : [])}
            linkColor={(l) => (l.invalid ? "#9ca3af" : "#93c5fd")}
            nodeCanvasObject={(node, ctx, scale) => {
              ctx.globalAlpha = node.dim ? 0.35 : 1;
              ctx.fillStyle = "#60a5fa";
              ctx.beginPath();
              ctx.arc(node.x, node.y, 5, 0, 2 * Math.PI);
              ctx.fill();
              ctx.globalAlpha = 1;
              ctx.fillStyle = "#f9fafb";
              ctx.font = `${12 / scale}px sans-serif`;
              ctx.fillText(node.id, node.x + 6, node.y);
            }}
            onNodeClick={(n) => openNode(n.id)}
          />
        </div>
      </div>
      <div style={{ width: 420, padding: 16, overflow: "auto", background: "#fff", color: "#111" }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 12 }}>
          {(payload.nodes || []).map((n) => (
            <button key={n.id} type="button" onClick={() => openNode(n.id)}>
              {n.id}
            </button>
          ))}
        </div>
        {page ? (
          <>
            <h2>{page.meta?.title || page.Meta?.Title}</h2>
            <pre style={{ whiteSpace: "pre-wrap" }}>{page.body || page.Body}</pre>
            {page.highlight ? <p>高亮：{page.highlight}</p> : null}
          </>
        ) : (
          <p>点左侧节点或右侧按钮打开 wiki 页</p>
        )}
      </div>
    </div>
  );
}
