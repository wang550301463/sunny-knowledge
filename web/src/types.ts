export interface ListResult<T> { items: T[]; next_cursor?: string | null }
export interface Principal { id: string; subjects: string[]; permissions: string[]; auth_epoch: number }
export interface Space { id: string; name: string }
export interface Account { id: string; name: string; email: string; active: boolean; permissions: string[]; account_kind: 'user'|'service'; client_id?: string; version: number }
export interface OrgUnit { id: string; name: string; kind: string }
export type Action = 'read'|'write'|'grant'|'review';
export interface Grant { space_id: string; resource_id?: string; action: Action; subjects: string[] | null; version: number }
export type EntityType = 'Service'|'Module'|'File'|'Person'|'Dependency'|'Decision'|'Policy'|'Incident'|'Change'|'Procedure';
export const entityTypes: EntityType[] = ['Service','Module','File','Person','Dependency','Decision','Policy','Incident','Change','Procedure'];
export interface EvidenceRef { resource_id: string; revision_id: string; source_id: string; source_revision: string; path: string; start_line: number; end_line: number; valid_from?: string|null; valid_until?: string|null; kind: 'code'|'markdown'|'ticket'|'policy' }
export interface Claim { id: string; text: string; kind:'fact'|'inference'|'gap'; evidence: EvidenceRef[]; state:'valid'|'stale'|'retracted'; entity_type?:EntityType|null }
export interface PageContent { title: string; markdown: string; entity_type: EntityType|null; claims: Claim[]; evidence: EvidenceRef[]; state:'valid'|'stale'|'retracted'; valid_from?:string|null; valid_until?:string|null }
export interface Revision { id: string; page_id:string; number:number; base_revision: string|null; content:PageContent; created_by:string; created_at:string; publication_kind:string }
export interface WikiPage { id:string; space_id:string; current_revision:string|null; revision_number:number; revision:Revision|null; created_by:string; created_at:string }
export interface Proposal { id:string; page_id:string; base_revision:string|null; content:PageContent; kind:string; reason:string; status:'pending'|'approved'|'rejected'; proposed_by:string; created_at:string; review_reason?:string|null }
export interface SourceSnapshot { id:string; source_id:string; source_revision:string; resource_id:string; space_id:string; path:string; kind:EvidenceRef['kind']; text:string; sha256:string }
export type Capability = 'chat'|'embedding'|'rerank';
export interface ModelConfig { name:string; provider:string; provider_model:string; base_url:string; capability:Capability; dimensions:number|null; request_dimensions:boolean; max_input_chars:number; max_batch_size:number; max_output_tokens:number; max_response_bytes:number; timeout_seconds:number; max_retries:number; concurrency_per_replica:number; max_queue_per_replica:number; chat_token_parameter:'max_completion_tokens'|'max_tokens' }
export interface ModelRecord extends ModelConfig { id:string; configuration_id:string; version:number; state:'active'|'disabled'|'retired'; has_credential:boolean; test_state:'untested'|'passed'|'failed'; capabilities:Record<string,boolean>; test_error_code?:string|null; tested_at?:string|null }
export interface AuditEntry { id:string; actor:string; action:string; target:string; detail:unknown; auth_epoch:number; occurred_at:string }