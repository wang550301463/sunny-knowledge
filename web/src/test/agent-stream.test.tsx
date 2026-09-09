import { act,renderHook,waitFor } from "@testing-library/react";
import { afterEach,beforeEach,describe,expect,it,vi } from "vitest";
import { ApiError } from "../api";
import { useAgentRun } from "../agent-hooks";
import type { RunEvent } from "../events";
const {api,resume}=vi.hoisted(()=>({api:{get:vi.fn()},resume:vi.fn()}));
vi.mock("../auth",()=>({useAuth:()=>({api})}));
vi.mock("../events",()=>({resumeRun:resume}));
const run={id:"r",session_id:"s",status:"running",event_seq:1,created_at:"2026-09-08T00:00:00Z",finished_at:null,content_hidden:false,agent_id:"a",configuration_id:"v",actual_scope:["space"],question:"PRIVATE",answer:null,citations:[],usage:{},error_code:null,budget:{model_rounds:8,tool_calls:20,parallel_reads:2,seconds:180},rounds:0,tool_calls:0};
beforeEach(()=>{vi.clearAllMocks();api.get.mockResolvedValue(run);resume.mockImplementation(()=>new Promise(()=>{}))});
afterEach(()=>vi.useRealTimers());
describe("run stream and live authorization",()=>{
 it("resumes with the last observed sequence after disconnection",async()=>{
  vi.useFakeTimers();let emit:(e:RunEvent)=>void=()=>{};let disconnect:(reason:unknown)=>void=()=>{};
  resume.mockImplementationOnce((_api,_id,_cursor,onEvent)=>{emit=onEvent;return new Promise((_,reject)=>{disconnect=reject})});
  const {result}=renderHook(()=>useAgentRun("r"));await act(async()=>{});
  act(()=>emit({id:"4",type:"generation",data:{seq:4,type:"generation",data:{round:1,status:"started"}}}));
  expect(result.current.progress.cursor).toBe(4);
  await act(async()=>disconnect(new ApiError(0,"network_error")));
  await act(async()=>vi.advanceTimersByTimeAsync(3100));
  expect(resume).toHaveBeenLastCalledWith(api,"r","4",expect.any(Function),expect.any(AbortSignal));
 });
 it("does not restore content from an older request after a denied refresh",async()=>{
  const {result}=renderHook(()=>useAgentRun("r"));await waitFor(()=>expect(result.current.run?.id).toBe("r"));
  let stale:(value:unknown)=>void=()=>{};api.get.mockImplementationOnce(()=>new Promise(resolve=>{stale=resolve}));
  act(()=>window.dispatchEvent(new Event("focus")));
  api.get.mockRejectedValue(new ApiError(403,"forbidden"));act(()=>window.dispatchEvent(new Event("focus")));
  await waitFor(()=>expect(result.current.run).toBeUndefined());
  await act(async()=>stale(run));expect(result.current.run).toBeUndefined();expect(result.current.error).toBeInstanceOf(ApiError);
 });
 it("uses polling when SSE fails and clears data on a polling authorization outage",async()=>{
  resume.mockRejectedValue(new ApiError(502,"invalid_content_type"));const {result}=renderHook(()=>useAgentRun("r"));await waitFor(()=>expect(result.current.transport).toBe("polling"));
  api.get.mockRejectedValue(new ApiError(503,"authorization_unavailable"));act(()=>window.dispatchEvent(new Event("focus")));
  await waitFor(()=>expect(result.current.run).toBeUndefined());expect(result.current.progress.stages).toEqual([]);
 });
});
