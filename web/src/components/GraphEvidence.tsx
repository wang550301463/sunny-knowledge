import { useEffect, useRef, useState } from "react";
import { Alert, Button, Drawer, Space, Tag, Typography } from "antd";
import { Link } from "react-router-dom";
import { ApiError, pathId } from "../api";
import type { ApiClient } from "../api";
import { graphSelectionEvidence, graphSelectionFragments, graphWikiHref, matchesGraphSource, sameGraphEvidence } from "../graph-api";
import type { GraphEvidence as Citation, GraphResult, GraphSelection, GraphSource } from "../graph-types";
import { ExactSource, SafeMarkdown } from "./Content";
export function GraphEvidenceDrawer({ api, data, selection, refresh, invalidate, onClose }: {
  api: ApiClient; data: GraphResult; selection: GraphSelection;
  refresh: (clear?: boolean) => Promise<GraphResult | undefined>;
  invalidate: (error: unknown) => void; onClose: () => void;
}) {
  const [source, setSource] = useState<{ citation: Citation; snapshot: GraphSource }>();
  const [busy, setBusy] = useState<string>();
  const live = useRef(true), serial = useRef(0), abort = useRef<AbortController | undefined>(undefined);
  useEffect(() => { live.current = true; return () => { live.current = false; serial.current++; abort.current?.abort(); }; }, []);
  const citations = graphSelectionEvidence(data, selection);
  const visible = source && citations.some(e => sameGraphEvidence(e, source.citation)) ? source : undefined;
  async function readSource(citation: Citation) {
    const generation = ++serial.current; abort.current?.abort(); const controller = new AbortController(); abort.current = controller;
    const current = () => live.current && generation === serial.current && !controller.signal.aborted;
    setSource(undefined); setBusy(citation.id);
    const stillSupported = (fresh?: GraphResult) => fresh && graphSelectionEvidence(fresh, selection).some(e => sameGraphEvidence(e, citation));
    try {
      const before = await refresh();
      if (!current()) return;
      if (!stillSupported(before)) throw new ApiError(403, "graph_evidence_changed");
      const snapshot = await api.get<unknown>(`/source-snapshots/${pathId(citation.evidence.revision_id)}`, controller.signal);
      if (!current()) return;
      if (!matchesGraphSource(snapshot, citation)) throw new ApiError(502, "graph_source_mismatch");
      const after = await refresh();
      if (!current()) return;
      if (!stillSupported(after)) throw new ApiError(403, "graph_evidence_changed");
      setSource({ citation, snapshot });
    } catch (error) { if (current()) invalidate(error); }
    finally { if (current()) setBusy(undefined); }
  }
  return <Drawer title="图谱证据" aria-label="图谱证据" open onClose={onClose} width={660} destroyOnHidden>
    <Typography.Text type="secondary">{selection.kind === "node" ? "节点" : "关系"}稳定标识</Typography.Text>
    <pre className="graph-id">{selection.id}</pre>
    {graphSelectionFragments(data, selection).map(item => <section key={item.id} className="graph-evidence-item">
      <Space wrap><Tag>{item.kind === "inference" ? "推断" : item.kind === "fact" ? "事实" : item.kind === "gap" ? "证据缺口" : "Wiki 正文"}</Tag><Tag>{item.is_current ? "当前修订" : "历史修订"}</Tag><Tag>{item.state}</Tag></Space>
      <Typography.Title level={5}>关联 Wiki：{item.title}</Typography.Title>
      <Link to={graphWikiHref(item)} target="_blank" rel="noopener noreferrer">打开固定 Wiki 修订 · v{item.version}</Link>
      <SafeMarkdown text={item.text}/>
      <small>业务有效：{item.valid_from ?? "未指定起点"} — {item.valid_until ?? "未指定终点"}；获知：{item.known_at}</small>
      <details><summary>片段与知识修订标识</summary><pre className="graph-id">{item.id}{"\n"}{item.revision_id}</pre></details>
    </section>)}
    <Typography.Title level={5}>原始来源</Typography.Title>
    {citations.map(citation => <section className="graph-evidence-item" key={citation.id}>
      <Typography.Text strong>{citation.evidence.path}:{citation.evidence.start_line}–{citation.evidence.end_line}</Typography.Text>
      <pre className="graph-id">来源修订：{citation.evidence.source_revision}</pre>
      <Button loading={busy === citation.id} disabled={busy !== undefined} aria-label={`查看原文 ${citation.evidence.path}`} onClick={() => void readSource(citation)}>查看原文</Button>
      {visible?.citation.id === citation.id && <><Alert type="info" message="已复核图谱映射及原始来源授权；展示固定来源修订。"/><ExactSource text={visible.snapshot.text} startLine={citation.evidence.start_line} endLine={citation.evidence.end_line}/></>}
    </section>)}
  </Drawer>;
}