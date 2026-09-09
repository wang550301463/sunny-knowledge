import { describe, expect, it } from "vitest";
import { applyRunEvent, readableRun, runStatusLabel } from "../agent-api";
import type { AgentRun } from "../agent-types";
const run: AgentRun = {id:"r",session_id:"s",status:"running",event_seq:2,created_at:"2026-09-08T00:00:00Z",finished_at:null,content_hidden:false,agent_id:"a",configuration_id:"v",actual_scope:["space"],question:"问题",answer:null,citations:[],usage:{},error_code:null,budget:{model_rounds:8,tool_calls:20,parallel_reads:2,seconds:180},rounds:0,tool_calls:0};
describe("authorized Agent UI event boundary",()=>{
 it("removes every content field when a run is redacted",()=>{const hidden=readableRun({...run,content_hidden:false,answer:{facts:[],inferences:[],gaps:["PRIVATE"]}});expect(hidden.content_hidden).toBe(true);expect(JSON.stringify(hidden)).not.toContain("PRIVATE");});
 it("ignores duplicate and older event cursors without revealing model reasoning",()=>{const before={cursor:2,stages:[]};expect(applyRunEvent(before,{id:"1",type:"generation",data:{seq:1,type:"generation",data:{reasoning:"SECRET"}}})).toEqual(before);const next=applyRunEvent(before,{id:"3",type:"tool",data:{seq:3,type:"tool",data:{tool:"search",status:"started",reasoning:"SECRET"}}});expect(next.cursor).toBe(3);expect(JSON.stringify(next)).not.toContain("SECRET");});
 it("rejects malformed sequence and maps actual statuses",()=>{expect(()=>applyRunEvent({cursor:0,stages:[]},{id:"4",type:"tool",data:{seq:5,type:"tool",data:{}}})).toThrow();expect(runStatusLabel("partial")).toBe("部分完成");});
});