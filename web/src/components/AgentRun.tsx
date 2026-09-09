import { useEffect, useRef, useState } from "react";
import { Alert, Button, Collapse, Drawer, Input, Modal, Select, Skeleton, Space, Tag, Typography } from "antd";
import { DownloadOutlined, LinkOutlined, ReloadOutlined, StopOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { isAuthorizationFailure, pathId, query } from "../api";
import { downloadRun, readableRun, runStatusLabel, terminal, toolLabels } from "../agent-api";
import { useAgentRun } from "../agent-hooks";
import type { AgentCitation, AgentRun, ReadableRun } from "../agent-types";
import { SafeMarkdown } from "./Content";
import { CitationBody } from "./Citation";
import { ErrorNotice, formatDate } from "./Common";

export function AnswerView({run,onCitation}:{run:ReadableRun;onCitation:(citation:AgentCitation)=>void}) {
 const citations=new Map(run.citations.map(c=>[c.id,c]));
 if(!run.answer)return <Alert type={run.status==="failed"?"error":"info"} showIcon message={terminal(run)?"本次运行没有形成可展示的回答。":"正在检索和整理证据，回答将在校验后显示。"} />;
 return <div className="agent-answer">
  {(["facts","inferences"] as const).map(kind=>run.answer![kind].length>0&&<section key={kind} aria-label={kind==="facts"?"事实":"推断"}><Typography.Title level={5}>{kind==="facts"?"事实":"推断"}</Typography.Title>{run.answer![kind].map((claim,i)=><div className="answer-claim" key={i}><SafeMarkdown text={claim.text}/><Space wrap>{claim.citation_ids.map(id=>{const c=citations.get(id);return c?<Button className="citation-chip" size="small" key={id} onClick={()=>onCitation(c)} icon={<LinkOutlined/>}>{c.evidence.path} : {c.evidence.start_line}–{c.evidence.end_line}</Button>:<Tag key={id} color="warning">引用暂不可用</Tag>})}</Space></div>)}</section>)}
  {run.answer.gaps.length>0&&<section aria-label="证据缺口"><Typography.Title level={5}>证据缺口</Typography.Title>{run.answer.gaps.map((gap,i)=><Alert key={i} type="warning" showIcon message={<SafeMarkdown text={gap}/>} />)}</section>}
 </div>;
}

export function RunPanel({runId,onRunChanged,onAuthorizationFailure}:{runId:string;onRunChanged:(run:AgentRun)=>void;onAuthorizationFailure?:()=>void}) {
 const {api}=useAuth();const state=useAgentRun(runId);
 const [citation,setCitation]=useState<AgentCitation|null>(null),[busy,setBusy]=useState(false),[actionError,setActionError]=useState<unknown>();
 const [feedback,setFeedback]=useState(false),[rating,setRating]=useState("helpful"),[comment,setComment]=useState(""),[feedbackSent,setFeedbackSent]=useState(false);
 const mounted=useRef(true);const keyRef=useRef(runId);keyRef.current=runId;
 const authCallback=useRef(onAuthorizationFailure);authCallback.current=onAuthorizationFailure;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{setCitation(null);setFeedback(false);setActionError(undefined);setFeedbackSent(false)},[runId]);
 const denied=!!state.error||state.run?.content_hidden===true;
 useEffect(()=>{if(denied){setCitation(null);setFeedback(false);setComment("");authCallback.current?.()}},[denied]);
 const action=async(operation:()=>Promise<void>)=>{const key=runId;setBusy(true);setActionError(undefined);try{await operation()}catch(error){if(mounted.current&&keyRef.current===key){setActionError(error);if(isAuthorizationFailure(error))state.invalidate(error)}}finally{if(mounted.current&&keyRef.current===key)setBusy(false)}};
 const selectCitation=(selected:AgentCitation)=>void action(async()=>{const fresh=await state.refresh();if(fresh&&!fresh.content_hidden&&keyRef.current===runId){const current=fresh.citations.find(c=>c.id===selected.id);if(current)setCitation(current)}});
 const visible=state.run&&!state.run.content_hidden?state.run:undefined;
 return <article className="agent-run" aria-label="运行回答">
  {state.loading?<Skeleton active/>:state.error?<ErrorNotice error={state.error} retry={()=>void state.refresh()}/>:state.run?.content_hidden?<Alert type="warning" showIcon message="当前授权已变化，已停止展示此回答及其上下文。"/>:visible&&<>
   <div className="run-question"><Typography.Text type="secondary">你的问题</Typography.Text><div>{visible.question}</div></div>
   <div className="run-heading"><Space wrap><Tag color={visible.status==="failed"?"red":visible.status==="partial"?"gold":"green"}>{runStatusLabel(visible.status)}</Tag><Typography.Text type="secondary">{formatDate(visible.created_at)}</Typography.Text>{!terminal(visible)&&<Typography.Text type="secondary">{state.transport==="events"?"进度实时连接":"自动刷新进度"}</Typography.Text>}</Space></div>
   <Collapse className="run-steps" size="small" items={[{key:"steps",label:`执行步骤 · ${visible.rounds} 轮 · ${visible.tool_calls} 次工具调用`,children:<><ol>{state.progress.stages.map(stage=><li key={stage.seq}>{stage.label}{stage.tool&&` · ${toolLabels[stage.tool as keyof typeof toolLabels]}`}{stage.round&&` · 第 ${stage.round} 轮`}</li>)}</ol>{state.progress.stages.length===0&&<Typography.Text type="secondary">暂无可观察的运行事件。刷新保留运行，事件按游标恢复。</Typography.Text>}<div className="muted">预算：{visible.budget.model_rounds} 轮 / {visible.budget.tool_calls} 次工具 / {visible.budget.seconds} 秒</div></>}]}/>
   <AnswerView run={visible} onCitation={selectCitation}/>
   {visible.error_code&&<Alert type="warning" message={`运行未完整完成：${visible.error_code}`}/>}
   <Space wrap className="run-actions">
    {!terminal(visible)?<Button icon={<StopOutlined/>} disabled={busy} onClick={()=>void action(async()=>{await api.post(`/runs/${pathId(runId)}/cancel`,{});await state.refresh()})}>停止</Button>:<Button icon={<ReloadOutlined/>} disabled={busy} onClick={()=>void action(async()=>{const result=readableRun(await api.post<AgentRun>(`/runs/${pathId(runId)}/retry`,{idempotency_key:crypto.randomUUID()}));if(mounted.current&&keyRef.current===runId)onRunChanged(result)})}>重新运行</Button>}
    {visible.answer&&<><Button icon={<DownloadOutlined/>} disabled={busy} onClick={()=>void action(()=>downloadRun(api,runId))}>Markdown 导出</Button><Button disabled={busy||feedbackSent} onClick={()=>setFeedback(true)}>{feedbackSent?"反馈已记录":"反馈"}</Button></>}
    <Typography.Text type="secondary">{visible.usage.total_tokens!==undefined?`${visible.usage.total_tokens} tokens`:"用量以服务端记录为准"}</Typography.Text>
   </Space>
  </>}
  <ErrorNotice error={actionError}/>
  <Drawer title="回答证据" open={!!citation&&!denied} width={660} onClose={()=>setCitation(null)} destroyOnHidden>{citation&&!denied&&<><Typography.Paragraph>Wiki 修订 <code>{citation.revision_id}</code></Typography.Paragraph><Link to={`/spaces/${pathId(citation.space_id)}/pages/${pathId(citation.page_id)}${query({revision:citation.revision_id})}`}>查看该知识修订</Link><CitationBody snapshotId={citation.evidence.revision_id} evidence={citation.evidence}/></>}</Drawer>
  <Modal title="反馈本次回答" open={feedback&&!denied} onCancel={()=>setFeedback(false)} confirmLoading={busy} okText="提交反馈" onOk={()=>void action(async()=>{await api.post("/feedback",{run_id:runId,rating,comment:comment.trim()||null,idempotency_key:crypto.randomUUID()});if(mounted.current&&keyRef.current===runId){setFeedback(false);setComment("");setFeedbackSent(true)}})}><Select aria-label="反馈类型" value={rating} onChange={setRating} options={[{value:"helpful",label:"有帮助"},{value:"unhelpful",label:"帮助有限"},{value:"incorrect",label:"内容有误"}]}/><Input.TextArea aria-label="反馈说明" value={comment} onChange={e=>setComment(e.target.value)} maxLength={2000} rows={4} placeholder="可补充问题或证据线索"/><ErrorNotice error={actionError}/></Modal>
 </article>;
}
