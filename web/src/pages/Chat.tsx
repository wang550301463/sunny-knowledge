import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Alert, Button, Empty, Input, List, Popconfirm, Segmented, Select, Skeleton, Space, Tag, Typography } from "antd";
import { DeleteOutlined, MessageOutlined, PlusOutlined, SendOutlined } from "@ant-design/icons";
import { useAuth } from "../auth";
import { pathId, query } from "../api";
import { modeLabels, readableRun, runStatusLabel, terminal } from "../agent-api";
import { useAgentList } from "../agent-hooks";
import type { AgentDefinition, AgentRun, AgentSession, RunCreate } from "../agent-types";
import type { Space as KnowledgeSpace } from "../types";
import { ErrorNotice } from "../components/Common";
import { RunPanel } from "../components/AgentRun";

export function ChatPage() {
 const {api}=useAuth();const [params,setParams]=useSearchParams();const {runId:linkedRun}=useParams();
 const sessionId=params.get("session")??"",runId=params.get("run")??linkedRun??"",agentId=params.get("agent")??"",configId=params.get("configuration");
 const sessions=useAgentList<AgentSession>("/sessions",true),agents=useAgentList<AgentDefinition>("/agents"),spaces=useAgentList<KnowledgeSpace>("/spaces"),history=useAgentList<AgentRun>(sessionId?`/sessions/${pathId(sessionId)}/history`:"",true);
 const [question,setQuestion]=useState(""),[scope,setScope]=useState<string[]|null>(null),[mode,setMode]=useState("quick"),[busy,setBusy]=useState(false),[error,setError]=useState<unknown>(),[pinned,setPinned]=useState<{key:string;agent?:AgentDefinition;error?:unknown}>({key:""});
 const alive=useRef(true),routeRef=useRef("");routeRef.current=`${sessionId}:${runId}`;
 const attempt=useRef<{fingerprint:string;body:RunCreate}|null>(null);
 const directKey=agentId&&configId?`${agentId}:${configId}`:"";
 useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
 useEffect(()=>{if(!directKey)return;const controller=new AbortController();let live=true;setPinned({key:directKey});void api.get<AgentDefinition>(`/agents/${pathId(agentId)}${query({configuration_id:configId})}`,controller.signal).then(agent=>{if(live)setPinned({key:directKey,agent})},error=>{if(live)setPinned({key:directKey,error})});return()=>{live=false;controller.abort()}},[api,directKey,agentId,configId]);
 useEffect(()=>{setScope(null);setQuestion("");setError(undefined);attempt.current=null},[sessionId,agentId,configId]);
 const visibleAgents=agents.items.filter(a=>mode==="quick"?a.config.mode==="knowledge_qa":a.config.mode!=="knowledge_qa");
 const selected=directKey?(pinned.key===directKey?pinned.agent:undefined):(agents.items.find(a=>a.id===agentId)??visibleAgents[0]);
 const allowedSpaces=spaces.items.filter(s=>selected?.config.space_ids.includes(s.id));
 const actualScope=(scope??allowedSpaces.map(s=>s.id)).filter(id=>allowedSpaces.some(s=>s.id===id));
 const runs=history.items.map(readableRun).sort((a,b)=>a.created_at.localeCompare(b.created_at));
 const active=runs.some(r=>!terminal(r));
 const changeRun=(run:AgentRun)=>{setParams({session:run.session_id,run:run.id,...(selected?{agent:selected.id}:{})});void history.refresh();void sessions.refresh()};
 const submit=async()=>{
  if(!selected||!question.trim()||!actualScope.length||busy||!selected.config.model_configuration_id)return;
  const route=routeRef.current;setBusy(true);setError(undefined);
  try{
   let sid=sessionId;
   if(!sid){const session=await api.post<AgentSession>("/sessions",{title:"新会话"});sid=session.id;if(!alive.current||routeRef.current!==route)return;setParams({session:sid,agent:selected.id});void sessions.refresh()}
   const data={agent_id:selected.id,configuration_id:selected.configuration_id,session_id:sid,question:question.trim(),space_ids:actualScope};
   const fingerprint=JSON.stringify(data);
   if(attempt.current?.fingerprint!==fingerprint)attempt.current={fingerprint,body:{...data,idempotency_key:crypto.randomUUID()}};
   const run=readableRun(await api.post<AgentRun>("/runs",attempt.current.body));
   if(!alive.current)return;
   // A user changing sessions while the request runs must never receive another session's content.
   if(routeRef.current!==route&&routeRef.current!==`${sid}:`)return;
   attempt.current=null;setQuestion("");changeRun(run);
  }catch(err){if(alive.current)setError(err)}finally{if(alive.current)setBusy(false)}
 };
 const newSession=async()=>{setBusy(true);setError(undefined);try{const session=await api.post<AgentSession>("/sessions",{title:"新会话"});if(alive.current){setParams({session:session.id});void sessions.refresh()}}catch(err){if(alive.current)setError(err)}finally{if(alive.current)setBusy(false)}};
 const pickSession=(id:string)=>{setParams({session:id});history.clear()};
 const sessionOptions=sessions.items.map(s=>({value:s.id,label:s.title}));
 return <div className="chat-layout agent-chat">
  <aside className="chat-history"><Button block aria-label="新建会话" icon={<PlusOutlined/>} onClick={()=>void newSession()} disabled={busy}>新建会话</Button><Typography.Title level={5}>会话</Typography.Title><ErrorNotice error={sessions.error}/>{sessions.loading?<Skeleton active/>:<List dataSource={sessions.items} renderItem={session=><List.Item><Button type={session.id===sessionId?"primary":"text"} className="session-link" icon={<MessageOutlined/>} onClick={()=>pickSession(session.id)}>{session.title}</Button></List.Item>}/>} {sessions.next&&<Button onClick={()=>void sessions.more()}>加载更多会话</Button>}</aside>
  <main className="chat-main">
   <div className="chat-toolbar agent-chat-toolbar"><Space wrap><Typography.Title level={4}>知识问答</Typography.Title><Tag>回答有据可循</Tag></Space><Space wrap><Select className="mobile-session-select" aria-label="选择会话" placeholder="选择会话" value={sessionId||undefined} options={sessionOptions} onChange={pickSession}/><Button className="mobile-session-select" aria-label="新会话" onClick={()=>void newSession()} icon={<PlusOutlined/>}/>{sessionId&&<Popconfirm title="清空此会话？" description="将停止未完成运行并清除平台会话历史。" onConfirm={async()=>{setError(undefined);try{await api.delete(`/sessions/${pathId(sessionId)}`);history.clear();setParams({});void sessions.refresh()}catch(err){setError(err)}}><Button aria-label="清空会话" icon={<DeleteOutlined/>}>清空会话</Button></Popconfirm>}</Space></div>
   <div className="chat-controls"><Segmented aria-label="问答模式" value={selected?(selected.config.mode==="knowledge_qa"?"quick":"analysis"):mode} options={[{label:"快速问答",value:"quick"},{label:"研发分析",value:"analysis"}]} onChange={value=>{setMode(String(value));setScope(null);const next=new URLSearchParams(params);next.delete("agent");next.delete("configuration");setParams(next)}}/><Select aria-label="智能体" placeholder="选择智能体" value={selected?.id} options={(directKey&&selected?[selected]:visibleAgents).map(a=>({value:a.id,label:a.name}))} onChange={id=>{setScope(null);const next=new URLSearchParams(params);next.set("agent",id);next.delete("configuration");setParams(next)}}/><Select aria-label="知识范围" mode="multiple" placeholder="选择本轮知识范围" value={actualScope} options={allowedSpaces.map(s=>({value:s.id,label:s.name}))} onChange={setScope}/>{agents.next&&<Button onClick={()=>void agents.more()}>更多智能体</Button>}{spaces.next&&<Button onClick={()=>void spaces.more()}>更多空间</Button>}</div>
   <div className="chat-transcript" aria-label="问答内容" tabIndex={0}>
    <ErrorNotice error={error}/><ErrorNotice error={agents.error}/><ErrorNotice error={spaces.error}/><ErrorNotice error={pinned.key===directKey?pinned.error:undefined}/><ErrorNotice error={history.error} retry={()=>void history.refresh()}/>
    {!agents.loading&&!agents.error&&!selected&&<Empty description="先创建智能体，选择模型并设置知识范围。"><Link to="/agents">创建智能体</Link></Empty>}
    {selected&&!selected.config.model_configuration_id&&<Alert showIcon type="warning" message="这个智能体尚未选择模型" description={<Link to={`/agents${query({agent:selected.id})}`}>配置智能体模型</Link>}/>}
    {selected&&actualScope.length===0&&!spaces.loading&&<Alert showIcon type="warning" message="当前没有可用的知识范围" description={<Link to="/spaces">检查知识空间与授权</Link>}/>}
    {runs.length>0&&<div className="conversation-turns"><Typography.Text type="secondary">本会话的提问</Typography.Text>{runs.map(run=><Button type={run.id===runId?"primary":"default"} key={run.id} onClick={()=>setParams({session:run.session_id,run:run.id})}>{run.content_hidden?"已隐藏的历史回答":run.question.slice(0,100)} · {runStatusLabel(run.status)}</Button>)}{history.next&&<Button onClick={()=>void history.more()}>加载更早回答</Button>}</div>}
    {runId&&!history.error?<RunPanel key={runId} runId={runId} onRunChanged={changeRun} onAuthorizationFailure={history.clear}/>:!runId&&selected&&<div className="chat-welcome"><MessageOutlined/><Typography.Title level={2}>从一个研发问题开始</Typography.Title><Typography.Paragraph type="secondary">在获授权的范围内查找代码、依赖、变更和故障证据。<br/>回答会区分事实、推断和仍缺失的信息。</Typography.Paragraph><Tag>{modeLabels[selected.config.mode]}</Tag></div>}
   </div>
   <div className="chat-composer"><Input.TextArea aria-label="问题" value={question} onChange={e=>setQuestion(e.target.value)} maxLength={8192} autoSize={{minRows:3,maxRows:7}} placeholder="描述你想定位的问题，或需要追溯的变更…" onKeyDown={e=>{if(e.key==="Enter"&&(e.ctrlKey||e.metaKey)){e.preventDefault();void submit()}}}/><div className="composer-foot"><Typography.Text type="secondary">Ctrl / ⌘ + Enter 发送 · 原文仍需当前权限</Typography.Text><Button aria-label="发送问题" type="primary" icon={<SendOutlined/>} loading={busy} disabled={!selected?.config.model_configuration_id||!question.trim()||!actualScope.length||active||!!agents.error||!!spaces.error} onClick={()=>void submit()}>发送</Button></div></div>
  </main>
 </div>;
}
