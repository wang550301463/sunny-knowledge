import { useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Empty, Input, Space, Spin, Tag, Typography } from "antd";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { ApiError, errorMessage } from "../api";
import { useGraphExplorer } from "../graph-hooks";
import { graphSelectionFragments, graphWikiHref } from "../graph-api";
import { graphRelationTypes } from "../graph-types";
import type { GraphParameters, GraphResult, GraphSelection } from "../graph-types";
import { GraphCanvas, graphNodeTitle } from "../components/GraphCanvas";
import { GraphEvidenceDrawer } from "../components/GraphEvidence";
import "../graph.css";
const directions = { outgoing: "出向", incoming: "入向", both: "双向" };
const degradedText: Record<string, string> = {
  graph_unavailable: "图服务不可用，当前只能显示仍获授权的证据片段。",
  graph_projection_pending: "图投影尚未就绪，部分关系或证据映射正在等待重建。",
  graph_node_budget_reached: "已达到 100 个节点预算，图可能不完整。",
  graph_edge_budget_reached: "已达到 200 条边预算，图可能不完整。",
  graph_evidence_budget_reached: "关系证据达到预算，部分路径未展示。",
  graph_time_filter_incomplete: "部分关系的时间过滤不完整，结果已降级。",
  graph_budget_exceeded: "遍历达到预算，结果已降级。",
  history_contains_projected_revisions_only: "历史查询仅包含已完成投影的修订，并非完整历史档案。",
  no_authorized_evidence: "没有可展示的授权证据。",
  original_evidence_missing: "部分原始证据缺失，相关结论未展示。",
};
function GraphNotices({ data }: { data: GraphResult }) {
  const reasons = [...new Set([...data.degraded, ...data.graph.degraded, ...data.gaps])];
  return <div className="graph-notices">
    {reasons.map(reason => <Alert key={reason} showIcon type="warning" message={Object.hasOwn(degradedText, reason) ? degradedText[reason] : "部分检索或图谱能力不可用，结果可能不完整。"}/>)}
    <Typography.Paragraph type="secondary">结果以知识修订和来源时间为准。没有部署证据，不能据此推断生产版本。{reasons.length > 0 && "当前图不完整，不能据此判断不存在依赖。"}</Typography.Paragraph>
  </div>;
}
export function GraphExplorer({ spaceId }: { spaceId: string }) { return <Explorer key={spaceId} spaceId={spaceId}/>; }
function Explorer({ spaceId }: { spaceId: string }) {
  const { api } = useAuth(), resource = useGraphExplorer(api);
  const [query, setQuery] = useState(""), [business, setBusiness] = useState(""), [known, setKnown] = useState("");
  const [relation, setRelation] = useState<GraphParameters["relation"]>({ types: ["depends_on", "uses"], direction: "outgoing", hops: 1 });
  const [history, setHistory] = useState(false), [seeds, setSeeds] = useState<string[]>([]), [formError, setFormError] = useState<string>();
  const [selection, setSelection] = useState<{ target: GraphSelection; fence: number }>();
  const action = useRef(0);
  const iso = (value: string) => value ? Number.isFinite(Date.parse(value)) ? new Date(value).toISOString() : "invalid" : null;
  const params: GraphParameters = { space_ids: [spaceId], relation, as_of: iso(business), known_at: iso(known), include_historical: history };
  const changed = resource.execution && JSON.stringify(params) !== JSON.stringify(resource.execution.params);
  const data = resource.data, validSeeds = seeds.filter(id => data?.items.some(i => i.id === id));
  const selected = data && selection?.fence === resource.fence && graphSelectionFragments(data, selection.target).length ? selection.target : undefined;
  function valid() {
    if (!relation.types.length || params.as_of === "invalid" || params.known_at === "invalid") { setFormError("请选择至少一种关系，并填写有效时间。"); return false; }
    setFormError(undefined); return true;
  }
  async function select(target: GraphSelection) {
    const ticket = ++action.current, fence = resource.fence; setSelection(undefined);
    const fresh = await resource.refresh();
    if (ticket === action.current && fresh && graphSelectionFragments(fresh, target).length) setSelection({ target, fence });
  }
  const noModel = resource.error instanceof ApiError && resource.error.code === "model_not_configured";
  return <section className="graph-explorer" aria-label="知识图谱浏览器">
    <Typography.Title level={4}>从证据出发，追踪关系</Typography.Title>
    <Typography.Paragraph type="secondary">查找当前空间的知识片段作为起点，按关系和时间展开真实邻接关系。节点展示稳定标识及关联 Wiki，不推测实体名称或类型。</Typography.Paragraph>
    <Card size="small" title="查询条件">
      <form onSubmit={e => { e.preventDefault(); if (valid() && query.trim()) { action.current++; setSelection(undefined); setSeeds([]); void resource.search(query.trim(), params); } }}>
        <label className="graph-field">查找起点<Input aria-label="查找起点" value={query} onChange={e => setQuery(e.target.value)} maxLength={8192} placeholder="例如：订单服务依赖，故障修复"/></label>
        <div className="graph-controls">
          <label className="graph-field">遍历方向<select aria-label="遍历方向" value={relation.direction} onChange={e => setRelation(r => ({ ...r, direction: e.target.value as typeof r.direction }))}>{Object.entries(directions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="graph-field">遍历跳数<select aria-label="遍历跳数" value={relation.hops} onChange={e => setRelation(r => ({ ...r, hops: Number(e.target.value) as 1 | 2 }))}><option value={1}>1 跳</option><option value={2}>2 跳</option></select></label>
          <label className="graph-field">业务有效时间<Input type="datetime-local" aria-label="业务有效时间" value={business} onChange={e => setBusiness(e.target.value)}/></label>
          <label className="graph-field">系统获知截止时间<Input type="datetime-local" aria-label="系统获知截止时间" value={known} onChange={e => setKnown(e.target.value)}/></label>
        </div>
        <fieldset className="graph-relations"><legend>关系类型</legend><Checkbox.Group options={graphRelationTypes.map(value => ({ label: value, value }))} value={relation.types} onChange={values => setRelation(r => ({ ...r, types: values as typeof r.types }))}/></fieldset>
        <Space wrap><Checkbox checked={history} onChange={e => setHistory(e.target.checked)}>包含已投影历史修订</Checkbox><Typography.Text type="secondary">时间按本机时区输入；留空使用当前时间。</Typography.Text></Space>
        {formError && <Alert type="error" message={formError}/>}
        <div className="graph-actions"><Button type="primary" htmlType="submit" disabled={!query.trim() || resource.loading}>查找证据片段</Button>{resource.execution && <Button disabled={resource.loading || !resource.execution.seed_fragment_ids.length} onClick={() => void resource.refresh(true)}>复核当前结果</Button>}{resource.execution?.mode === "graph" && <Button disabled={resource.loading || !resource.execution.seed_fragment_ids.length} onClick={() => { if (valid()) void resource.traverse(resource.execution!.seed_fragment_ids, params); }}>按新条件展开</Button>}</div>
      </form>
    </Card>
    {resource.execution && <div aria-label="当前执行条件" className="graph-execution"><strong>当前执行：</strong>{directions[resource.execution.params.relation.direction]} · {resource.execution.params.relation.hops} 跳 · {resource.execution.params.relation.types.join("、")}<br/>业务有效：{data?.as_of ?? resource.execution.params.as_of ?? "请求时当前时间"}；获知截止：{resource.execution.params.known_at ?? "请求时当前时间"}；{resource.execution.params.include_historical ? "包含已投影历史" : "当前修订"}{changed && <div role="status">条件已修改，重新查询后应用。</div>}</div>}
    {resource.loading && <div role="status" className="graph-loading"><Spin size="small"/> 正在读取并复核授权…</div>}
    {resource.error !== undefined && <Alert type="error" showIcon message={noModel ? "请先配置检索所需的 Embedding 和 Reranker，并完成索引投影。" : errorMessage(resource.error)} description={noModel ? <Link to="/settings?tab=models">模型设置</Link> : "已撤回旧结果。修复问题后可以重新查询。"}/>}
    {data && <><GraphNotices data={data}/>{resource.execution?.mode === "search" ? <>
      <Typography.Title level={5}>选择证据起点（{data.items.length}）</Typography.Title>
      {!data.items.length && <Empty description="没有可展示的证据片段。可以调整查询，或检查来源同步和授权。"/>}
      <div className="graph-seeds">{data.items.map(item => <Card size="small" key={item.id}><Checkbox aria-label={item.title} checked={validSeeds.includes(item.id)} onChange={e => setSeeds(current => e.target.checked ? [...current.filter(id => id !== item.id), item.id].slice(0, 30) : current.filter(id => id !== item.id))}>{item.title}</Checkbox><Typography.Paragraph ellipsis={{ rows: 3 }} className="graph-snippet">{item.text}</Typography.Paragraph><Space><Tag>{item.kind}</Tag><Tag>{item.is_current ? "当前" : "历史"}</Tag><Link to={graphWikiHref(item)}>Wiki v{item.version}</Link></Space></Card>)}</div>
      <Button type="primary" disabled={!validSeeds.length || resource.loading} onClick={() => { if (valid()) { setSelection(undefined); void resource.traverse(validSeeds, params); } }}>展开关系</Button>
    </> : <>
      <Typography.Title level={5}>授权关系 · {data.graph.nodes.length} 节点 / {data.graph.edges.length} 关系</Typography.Title>
      <Typography.Paragraph type="secondary">实线为来源证明的事实，虚线为推断。最多 100 个节点、200 条边；箭头始终表示源节点 → 目标节点。</Typography.Paragraph>
      {!!data.graph.nodes.length ? <GraphCanvas data={data} params={resource.execution!.params} onSelect={target => void select(target)}/> : <Empty description="当前条件下没有可展示的关系图；不能据此判断不存在依赖。"/>}
      <div className="graph-lists"><section><Typography.Title level={5}>节点</Typography.Title><ul>{data.graph.nodes.map(node => <li key={node.id}><Button type="link" aria-label={`查看节点 ${node.id}`} onClick={() => void select({ kind: "node", id: node.id })}><code>{node.id}</code></Button><div>关联 Wiki：{graphNodeTitle(data, node.id) ?? "未提供标题"}</div></li>)}</ul></section><section><Typography.Title level={5}>关系</Typography.Title><ul>{data.graph.edges.map(edge => <li key={edge.id}><Button type="link" aria-label={`查看关系 ${edge.id}`} onClick={() => void select({ kind: "edge", id: edge.id })}>{edge.type} · {edge.kind === "inference" ? "推断" : "事实"}</Button><div className="graph-id">{edge.source} → {edge.target}</div><small className="graph-id">{edge.id}</small></li>)}</ul></section></div>
      {!!data.graph.paths.length && <details><summary>返回的证据路径（{data.graph.paths.length}）</summary><ol className="graph-paths">{data.graph.paths.map((path, index) => <li key={index}><span>遍历顺序：{path.node_ids.join(" → ")}</span><br/><small>关系标识：{path.edge_ids.join("、") || "起点"}</small></li>)}</ol></details>}
      {!data.graph.nodes.length && data.items.length > 0 && <section><Typography.Title level={5}>仍可读取的起点证据</Typography.Title>{data.items.filter(i => i.primary).map(item => <p key={item.id}><Link to={graphWikiHref(item)}>{item.title} · v{item.version}</Link></p>)}</section>}
    </>}</>}
    {data && selected && <GraphEvidenceDrawer key={`${resource.fence}:${selected.kind}:${selected.id}`} api={api} data={data} selection={selected} refresh={resource.refresh} invalidate={resource.invalidate} onClose={() => { action.current++; setSelection(undefined); }}/ >}
  </section>;
}