import { useRef } from "react";
import { Button, Typography } from "antd";
import { useAuth } from "../auth";
import { ApiError, pathId } from "../api";
import { useResource } from "../hooks";
import type { SourceSnapshot } from "../types";
import type { AgentCitation } from "../agent-types";
import { ExactSource, sourceRows } from "./Content";
import { ResourceView } from "./Common";
import { citationHref } from "./Citation";

/** A source authorization error also invalidates the surrounding generated answer. */
export function AgentEvidence({citation,onFailure}:{citation:AgentCitation;onFailure:(error:unknown)=>void}) {
 const {api}=useAuth();const failure=useRef(onFailure);failure.current=onFailure;
 const ref=citation.evidence;
 const resource=useResource(`agent-evidence:${citation.id}:${ref.revision_id}`,async signal=>{
  try{
   const snapshot=await api.get<SourceSnapshot>(`/source-snapshots/${pathId(ref.revision_id)}`,signal);
   if(ref.resource_id!==snapshot.resource_id||ref.source_id!==snapshot.source_id||ref.source_revision!==snapshot.source_revision||ref.path!==snapshot.path||ref.kind!==snapshot.kind||!Number.isInteger(ref.start_line)||!Number.isInteger(ref.end_line)||ref.start_line<1||ref.end_line<ref.start_line||ref.end_line>sourceRows(snapshot.text).length)throw new ApiError(502,"citation_snapshot_mismatch");
   return snapshot;
  }catch(error){if(!signal.aborted)failure.current(error);throw error}
 });
 return <ResourceView resource={resource}>{snapshot=><><Typography.Title level={4}>{snapshot.path}</Typography.Title><Typography.Paragraph type="secondary">来源版本 <code>{snapshot.source_revision}</code><br/>行 {ref.start_line}–{ref.end_line}<br/>有效时间：{ref.valid_from??"未限定"} 至 {ref.valid_until??"未限定"}</Typography.Paragraph><ExactSource text={snapshot.text} startLine={ref.start_line} endLine={ref.end_line}/><div className="citation-footer"><Button href={citationHref(ref)} target="_blank" rel="noopener noreferrer">打开受登录保护的引用</Button><Typography.Paragraph type="secondary">每次打开均重新检查当前权限。</Typography.Paragraph></div></>}</ResourceView>;
}
