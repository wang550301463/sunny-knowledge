import type { EvidenceRef } from "./types";
export type AgentMode="knowledge_qa"|"dependency_impact"|"incident_history"|"maintenance";
export type AgentTool="search"|"get"|"traverse"|"timeline"|"feedback"|"propose_revision";
export interface AgentBudget{model_rounds:number;tool_calls:number;parallel_reads:number;seconds:number}
export interface AgentConfig{mode:AgentMode;model_configuration_id:string|null;prompt:string;space_ids:string[];tools:AgentTool[];tool_space_ids:Partial<Record<AgentTool,string[]>>;budget:AgentBudget;max_output_tokens:number}
export interface AgentDefinition{id:string;configuration_id:string;version:number;name:string;description:string;owner_space_id:string;created_by:string;shared:boolean;published_configuration_id:string|null;config:AgentConfig;created_at:string}
export interface AgentPreset{id:AgentMode;name:string;instruction:string;tools:AgentTool[];model_configuration_id:null}
export interface AgentModel{id:string;name:string;configuration_id:string;provider_model:string;capability:"chat";state:"active";test_state:string;capabilities:Record<string,unknown>}
export interface AgentSession{id:string;title:string;created_at:string}
export interface AnswerClaim{text:string;citation_ids:string[]}
export interface AgentAnswer{facts:AnswerClaim[];inferences:AnswerClaim[];gaps:string[]}
export interface AgentCitation{id:string;evidence:EvidenceRef;excerpt:string;page_id:string;revision_id:string;space_id:string;url:string}
export type RunStatus="queued"|"running"|"completed"|"partial"|"failed"|"cancelled";
export interface RunBase{id:string;session_id:string;status:RunStatus;event_seq:number;created_at:string;finished_at:string|null}
export interface ReadableRun extends RunBase{content_hidden:false;entrypoint?:string;answer_complete?:boolean;agent_id:string;configuration_id:string;actual_scope:string[];question:string;answer:AgentAnswer|null;citations:AgentCitation[];usage:Record<string,number>;error_code:string|null;budget:AgentBudget;rounds:number;tool_calls:number}
export interface HiddenRun extends RunBase{content_hidden:true}
export type AgentRun=ReadableRun|HiddenRun;
export interface AgentCreate{name:string;description:string;owner_space_id:string;config:AgentConfig}
export interface RunCreate{agent_id:string;configuration_id:string|null;session_id:string;question:string;space_ids:string[];idempotency_key:string}
export interface Stage{seq:number;type:string;label:string;status?:string;tool?:string;round?:number}
export interface RunProgress{cursor:number;stages:Stage[]}