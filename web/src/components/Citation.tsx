import { Drawer, Typography, Tag, Space, Button, Alert } from 'antd';
import { LinkOutlined } from '@ant-design/icons';
import { useAuth } from '../auth';
import { useResource } from '../hooks';
import { pathId, query } from '../api';
import type { EvidenceRef, SourceSnapshot } from '../types';
import { ExactSource, sourceRows } from './Content';
import { ResourceView } from './Common';
export function citationHref(ref:Pick<EvidenceRef,'revision_id'|'start_line'|'end_line'>){return `/citations/${pathId(ref.revision_id)}${query({start:ref.start_line,end:ref.end_line})}`;}
export function CitationBody({snapshotId,evidence,startLine,endLine}:{snapshotId:string;evidence?:EvidenceRef;startLine?:number;endLine?:number}){
  const {api}=useAuth();const resource=useResource(`source:${snapshotId}`,signal=>api.get<SourceSnapshot>(`/source-snapshots/${pathId(snapshotId)}`,signal));
  return <ResourceView resource={resource}>{snapshot=>{
    const start=evidence?.start_line??startLine??1,end=evidence?.end_line??endLine??sourceRows(snapshot.text).length;
    const mismatch=evidence&&(evidence.resource_id!==snapshot.resource_id||evidence.source_id!==snapshot.source_id||evidence.source_revision!==snapshot.source_revision||evidence.path!==snapshot.path||evidence.kind!==snapshot.kind);
    if(mismatch||!Number.isInteger(start)||!Number.isInteger(end)||start<1||end<start||end>sourceRows(snapshot.text).length)return <Alert type="error" showIcon title="引用与原始快照不匹配，已停止展示。"/>;
    return <><div className="citation-meta"><Tag>{snapshot.kind}</Tag><Typography.Title level={4}>{snapshot.path}</Typography.Title><Typography.Paragraph type="secondary">来源 {snapshot.source_id}<br/>版本 <code>{snapshot.source_revision}</code><br/>行 {start}–{end}</Typography.Paragraph><Typography.Text type="secondary">快照 SHA-256</Typography.Text><div className="hash">{snapshot.sha256}</div></div><ExactSource text={snapshot.text} startLine={start} endLine={end}/><div className="citation-footer"><Button icon={<LinkOutlined/>} href={citationHref({revision_id:snapshotId,start_line:start,end_line:end})} target="_blank" rel="noopener noreferrer">打开受登录保护的引用</Button><Typography.Paragraph type="secondary">每次打开均重新检查当前权限。</Typography.Paragraph></div></>;
  }}</ResourceView>;
}
export function CitationDrawer({evidence,onClose}:{evidence:EvidenceRef|null;onClose:()=>void}){return <Drawer title="原文引用" placement="right" width={660} open={!!evidence} onClose={onClose} destroyOnHidden>{evidence&&<CitationBody snapshotId={evidence.revision_id} evidence={evidence}/>}</Drawer>;}
export function EvidenceList({evidence,onSelect}:{evidence:EvidenceRef[];onSelect:(ref:EvidenceRef)=>void}){return <Space wrap>{evidence.map((ref,index)=><Button key={`${ref.revision_id}:${ref.start_line}:${index}`} size="small" onClick={()=>onSelect(ref)}>{index+1}. {ref.path} : {ref.start_line}–{ref.end_line}</Button>)}</Space>;}