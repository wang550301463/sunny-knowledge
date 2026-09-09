import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "./auth";
import { pathId } from "./api";
import { applyRunEvent, readableRun, terminal } from "./agent-api";
import { resumeRun } from "./events";
import type { AgentRun, RunProgress } from "./agent-types";

interface RunState { key:string; run?:AgentRun; error?:unknown; loading:boolean; progress:RunProgress; transport:"connecting"|"events"|"polling" }
/** No answer or event payload is persisted in browser storage. Every refresh reauthorizes. */
export function useAgentRun(runId:string) {
 const {api}=useAuth();
 const [state,setState]=useState<RunState>({key:runId,loading:true,progress:{cursor:0,stages:[]},transport:"connecting"});
 const loadRef=useRef<()=>Promise<AgentRun|undefined>>(async()=>undefined);
 const invalidateRef=useRef<(error:unknown)=>void>(()=>{});
 useEffect(()=>{
  let live=true,serial=0,current:AgentRun|undefined;
  let progress:RunProgress={cursor:0,stages:[]};
  let readController:AbortController|undefined,streamController:AbortController|undefined;
  let transport:RunState["transport"]="connecting";
  let streamActive=false,reconnectAfter=0;
  setState({key:runId,loading:true,progress,transport});
  const invalidate=(error:unknown)=>{serial++;readController?.abort();streamController?.abort();current=undefined;progress={cursor:0,stages:[]};if(live)setState({key:runId,error,loading:false,progress,transport:"polling"})};
  const stream=()=>{
   if(!live||streamActive||!current||current.content_hidden||terminal(current)||Date.now()<reconnectAfter)return;
   streamActive=true;streamController=new AbortController();const signal=streamController.signal;
   void resumeRun(api,runId,String(progress.cursor),event=>{
    if(!live||signal.aborted)return;
    progress=applyRunEvent(progress,event);transport="events";
    setState(value=>({...value,progress,transport}));
    // The server exposes validated content through the live-authorized run view.
    if(event.type==="completed"||event.type==="answer_block")void load();
   },signal).catch(error=>{if(live&&!signal.aborted){invalidate(error);void load()}}).finally(()=>{
    streamActive=false;reconnectAfter=Date.now()+3000;
    if(live&&!signal.aborted){transport="polling";setState(value=>({...value,transport}));void load()}
   });
  };
  const load=async():Promise<AgentRun|undefined>=>{
   const request=++serial;readController?.abort();readController=new AbortController();
   try{
    const next=readableRun(await api.get<AgentRun>(`/runs/${pathId(runId)}`,readController.signal));
    if(!live||request!==serial)return;
    current=next;
    if(next.content_hidden){progress={cursor:0,stages:[]};streamController?.abort()}
    setState({key:runId,run:next,loading:false,progress,transport});stream();return next;
   }catch(error){if(live&&request===serial)invalidate(error)}
  };
  loadRef.current=load;invalidateRef.current=invalidate;void load();
  const interval=window.setInterval(()=>void load(),3000);
  const focus=()=>void load();const visibility=()=>{if(document.visibilityState==="visible")void load()};
  window.addEventListener("focus",focus);document.addEventListener("visibilitychange",visibility);
  return()=>{live=false;serial++;readController?.abort();streamController?.abort();clearInterval(interval);window.removeEventListener("focus",focus);document.removeEventListener("visibilitychange",visibility)};
 },[api,runId]);
 const refresh=useCallback(()=>loadRef.current(),[]);
 const invalidate=useCallback((error:unknown)=>invalidateRef.current(error),[]);
 return {...(state.key===runId?state:{key:runId,loading:true,progress:{cursor:0,stages:[]},transport:"connecting" as const}),refresh,invalidate};
}
