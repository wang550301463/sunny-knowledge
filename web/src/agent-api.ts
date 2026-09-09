import { ApiClient,ApiError,pathId,query } from "./api";
import type { RunEvent } from "./events";
import type { ListResult } from "./types";
import type { AgentRun,RunProgress,RunStatus } from "./agent-types";
export const modeLabels={knowledge_qa:"知识问答",dependency_impact:"依赖影响分析",incident_history:"故障追溯",maintenance:"知识维护"};
export const toolLabels={search:"混合检索",get:"读取证据",traverse:"图遍历",timeline:"版本时间线",feedback:"反馈",propose_revision:"修订提案"};
export const runStatusLabel=(status:RunStatus)=>({queued:"排队中",running:"运行中",completed:"已完成",partial:"部分完成",failed:"运行失败",cancelled:"已停止"})[status];
export const terminal=(run:AgentRun)=>!["queued","running"].includes(run.status);
export function readableRun(value:AgentRun):AgentRun{if(!value||!value.id||!value.session_id||!Number.isInteger(value.event_seq)||!(value.status in {queued:1,running:1,completed:1,partial:1,failed:1,cancelled:1})||typeof value.content_hidden!=="boolean")throw new ApiError(502,"invalid_run");if(value.content_hidden)return {id:value.id,session_id:value.session_id,status:value.status,event_seq:value.event_seq,created_at:value.created_at,finished_at:value.finished_at,content_hidden:true};if(!Array.isArray(value.citations)||!Array.isArray(value.actual_scope)||typeof value.question!=="string")throw new ApiError(502,"invalid_run");return value}
export async function agentList<T>(api:ApiClient,path:string,signal?:AbortSignal,cursor?:string|number):Promise<ListResult<T>>{const result=await api.get<ListResult<T>>(path+query({limit:50,cursor}),signal);if(!Array.isArray(result.items))throw new ApiError(502,"invalid_response");return result}
export function applyRunEvent(before:RunProgress,event:RunEvent):RunProgress{
 const body=event.data as {seq?:unknown;type?:unknown;data?:Record<string,unknown>;error?:unknown};if(event.type==="error")throw new ApiError(503,"run_event_authorization_unavailable");
 const seq=Number(event.id);if(!Number.isSafeInteger(seq)||seq<1||body.seq!==seq||body.type!==event.type)throw new ApiError(502,"invalid_event");if(seq<=before.cursor)return before;
 const labels:Record<string,string>={queued:"等待运行",started:"运行已开始",context_ready:"上下文准备完成",generation:"正在生成回答",tool:"调用知识工具",completed:"运行已结束",answer_block:"已验证回答更新"};
 const data=body.data??{};const tool=typeof data.tool==="string"&&data.tool in toolLabels?data.tool:undefined;
 const stage={seq,type:event.type,label:labels[event.type]??"运行进度更新",...(tool?{tool}:{}),...(typeof data.round==="number"?{round:data.round}:{}),...(typeof data.status==="string"&&["started","completed","failed","queued","running","partial","cancelled"].includes(data.status)?{status:data.status}:{})};
 return {cursor:seq,stages:[...before.stages,stage].slice(-100)};
}
export async function downloadRun(api:ApiClient,id:string){const response=await api.response(`/runs/${pathId(id)}/export`);if(!response.headers.get("content-type")?.includes("text/markdown"))throw new ApiError(502,"invalid_export");const blob=new Blob([await response.text()],{type:"text/markdown;charset=utf-8"});const href=URL.createObjectURL(blob);try{const a=document.createElement("a");a.href=href;a.download="knowledge-answer.md";a.click()}finally{setTimeout(()=>URL.revokeObjectURL(href),0)}}